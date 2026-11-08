"""model.py -- DVD: load the checkpoint, run a window of frames through it, stitch the windows.

DVD (Deterministic Video Depth Estimation with Generative Priors; user, 2026-11-07: "lets put this into
studio") is Wan2.1-T2V-1.3B turned into a depth regressor: the frames go through Wan's video VAE, the DiT
runs once at a fixed timestep with no prompt, and the VAE decodes its output as the depth video. One pass,
no sampling, so the same clip gives the same map every time.

The checkpoint (FayeHongfeiZhang/DVD dvd_1.1.safetensors) holds all of it -- the DiT with its LoRA not yet
merged, and the VAE -- so nothing else is downloaded: no Wan base weights, no T5 (the prompt is always
empty; upstream feeds zeros for it, and so does this). The LoRA (rank 512, alpha 512) is merged into the
DiT's weights as they load, so peft isn't needed either.

The model code is vendored in dvd_depth/ (Apache-2.0). What upstream's pipeline does around it -- the
4n+1 frame padding, the one forward pass, the window stitching ("Global Affine Coherence": each window
fitted to the last by least-squares scale and shift over their shared frames, clipped to 0.7..1.5, then
cross-faded) -- is test_script/test_single_video.py's, done here a window at a time so a long clip needs
no more memory than one window.

Torch is imported by the functions that need it, so the page can read the constants without loading it.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from core import weights_paths

logger = logging.getLogger(__name__)

#: The models folder's subfolder (studio.model_catalog DVD), and the checkpoint in it.
FOLDER = "DVD"
CHECKPOINT = "dvd_1.1.safetensors"

#: Wan2.1-T2V-1.3B's DiT (DiffSynth's config for its state dict).
DIT_CONFIG = {
    "has_image_input": False, "patch_size": [1, 2, 2], "in_dim": 16, "dim": 1536, "ffn_dim": 8960,
    "freq_dim": 256, "text_dim": 4096, "out_dim": 16, "num_heads": 12, "num_layers": 30, "eps": 1e-6,
}
#: ckpt/model_config.yaml: denoise_step 0.5 -> the timestep the DiT runs at (1000 * 0.5).
TIMESTEP = 500.0
#: LoRA scale: alpha / rank (DiffusionTrainingModule.add_lora_to_model: alpha defaults to the rank).
LORA_SCALE = 1.0
#: The empty prompt upstream feeds: zeros, (1, 512, 4096).
CONTEXT_LEN, CONTEXT_DIM = 512, 4096

#: Every side a multiple of this (the VAE's 8 and the DiT's 2x2 patches).
MULTIPLE = 16
#: The short side the frames run at by default: DVD's training scale (480x640).
DEFAULT_INPUT = 480
#: Billy's GUI's Width (bill_gui_app.py; user, 2026-11-07: his Width + Early Downsample, added as his).
DEFAULT_WIDTH = 640
#: Upstream's window and overlap (test_single_video.py's defaults; Billy's GUI's too).
DEFAULT_WINDOW, DEFAULT_OVERLAP = 81, 9
#: Wan's VAE takes 4n+1 frames: a window of another length is padded to it with its last frame and the
#: padding dropped (run_window), as upstream does. 81 is the longest Wan was trained on.
MIN_WINDOW, MAX_WINDOW = 9, 81
#: The window-to-window scale is held to this (upstream's clip).
SCALE_CLIP = (0.7, 1.5)


class ModelsMissing(Exception):
    """The checkpoint isn't in the models folder."""


def checkpoint_path() -> Path:
    return weights_paths.find(FOLDER, marker=CHECKPOINT) / CHECKPOINT


def installed() -> bool:
    try:
        return checkpoint_path().is_file()
    except OSError:
        return False


def snap_input(value) -> int:
    """An Input Size the model can take: a multiple of 16, rounded up, 256 at least."""
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        v = DEFAULT_INPUT
    v = max(256, v)
    return v + (-v % MULTIPLE)


def snap_window(value) -> int:
    """A window length, 9..81 frames, as Billy's GUI takes (run_window pads it to 4n+1)."""
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        v = DEFAULT_WINDOW
    return min(MAX_WINDOW, max(MIN_WINDOW, v))


def snap_width(value) -> int:
    """A Width the model can take: a multiple of 16, rounded up, 256 at least."""
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        v = DEFAULT_WIDTH
    v = max(256, v)
    return v + (-v % MULTIPLE)


def infer_size(height: int, width: int, input_size: int, target_width: int = 0,
               early_downsample: bool = False) -> Tuple[int, int]:
    """The (height, width) a frame is run at, both sides multiples of 16 (rounded up), as Billy's GUI:

    * Early Downsample: his read_video_early_downsample -- the width at `target_width`, the height by the
      clip's shape (Input Size not used);
    * otherwise his resize_for_training_scale, with Input Size for his fixed 480: scaled by whichever is
      larger, the short side to `input_size` or the width to `target_width` (0: the short side alone)."""
    if early_downsample and target_width:
        w = snap_width(target_width)
        h = int(-(-height * w / max(1, width) // 1))
    else:
        scale = snap_input(input_size) / max(1, min(width, height))
        if target_width:
            scale = max(scale, snap_width(target_width) / max(1, width))
        h = int(-(-height * scale // 1))
        w = int(-(-width * scale // 1))
    h += -h % MULTIPLE
    w += -w % MULTIPLE
    return max(h, MULTIPLE), max(w, MULTIPLE)


def windows(total: int, window: int, overlap: int) -> List[Tuple[int, int]]:
    """Upstream's get_window_index: (start, end) of each window. Each starts `window - overlap` after the
    last; the last one is pulled back to end at the clip's end with a full window (its overlap is larger)."""
    window = max(1, int(window))
    overlap = min(max(0, int(overlap)), window - 1)
    if total <= window:
        return [(0, total)]
    out = [(0, window)]
    start = window - overlap
    while start < total:
        end = start + window
        if end < total:
            out.append((start, end))
            start += window - overlap
        else:
            out.append((max(0, total - window), total))
            break
    return out


# ---- loading -----------------------------------------------------------------------------------

#: Smaller cards (8 and 12 GB), or a card with less than PARK_FREE free once DVD is loaded, park the transformer
#: in pinned RAM while the VAE runs (user, 2026-11-07: "optimise for 8-12 gb"): the VAE's encode and decode don't
#: use it, and its 2.6 GB is most of what a 896x480 window's decode can't find room for on a 12 GB card.
SMALL_CARD = 14 << 30
PARK_FREE = 9 << 30


class _Parked:
    """A module's weights kept in pinned RAM: off() points them there (no copy -- the GPU copy is just
    dropped), on() copies them back (~0.2 s for DVD's 2.6 GB transformer)."""

    def __init__(self, module, device):
        self.module, self.device = module, device
        self.cpu = {}
        for key, t in self._tensors():
            c = t.detach().to("cpu")
            try:
                c = c.pin_memory()
            except RuntimeError:            # no pinned RAM to spare: plain RAM, a slower copy back
                pass
            self.cpu[key] = c
        self.on_card = True

    def _tensors(self):
        for name, p in self.module.named_parameters():
            yield ("p", name), p
        for name, b in self.module.named_buffers():
            yield ("b", name), b

    def _set(self, key, value):
        kind, name = key
        owner, _, leaf = name.rpartition(".")
        mod = self.module.get_submodule(owner) if owner else self.module
        if kind == "p":
            getattr(mod, leaf).data = value
        else:
            mod._buffers[leaf] = value

    def off(self) -> bool:
        """Off the card. Returns whether it was on."""
        was = self.on_card
        if was:
            for key, _ in list(self._tensors()):
                self._set(key, self.cpu[key])
            self.on_card = False
        return was

    def on(self) -> None:
        if not self.on_card:
            for key, _ in list(self._tensors()):
                self._set(key, self.cpu[key].to(self.device, non_blocking=True))
            self.on_card = True


class DvdModel:
    """The DiT and VAE on the card, bf16, and the empty prompt already through the DiT's text embedding."""

    def __init__(self, dit, vae, device, dtype):
        self.dit, self.vae, self.device, self.dtype = dit, vae, device, dtype
        #: core.dvd.vae_trt.VaeTensorRT when the TensorRT box is on (the runner sets it), else None
        self.trt = None
        #: _Parked on a smaller card (see SMALL_CARD): the transformer off the card while the VAE runs
        self.parker = None
        #: > 0: the VAE runs in tiles of at most this many latents (H/8 x W/8) when a frame is bigger (tile_grid)
        self.tile_area = 0
        import torch
        with torch.inference_mode():
            zeros = torch.zeros(1, CONTEXT_LEN, CONTEXT_DIM, dtype=dtype, device=device)
            self.context = dit.text_embedding(zeros)

    def dit_off(self) -> bool:
        """The transformer off the card (pinned RAM when parked, else plain); returns whether it was on."""
        if self.parker is not None:
            return self.parker.off()
        on = next(self.dit.parameters()).device.type != "cpu"
        if on:
            self.dit.to("cpu")
        return on

    def dit_on(self) -> None:
        if self.parker is not None:
            self.parker.on()
        else:
            self.dit.to(self.device)

    def parameters(self):
        return self.dit.parameters()


def _merged_dit_state(state: dict, device) -> dict:
    """The checkpoint's DiT keys as a plain WanModel's: each LoRA'd Linear's base weight + B @ A * scale
    (peft's merge, done once here), its bias as is."""
    import torch

    dit = {k[len("pipe.dit."):]: v for k, v in state.items() if k.startswith("pipe.dit.")}
    out = {}
    for key, value in dit.items():
        if ".lora_A." in key or ".lora_B." in key:
            continue
        if ".base_layer." not in key:
            out[key] = value
            continue
        name = key.replace(".base_layer.", ".")
        if key.endswith(".weight"):
            stem = key[: -len(".base_layer.weight")]
            a = dit.get(f"{stem}.lora_A.default.weight")
            b = dit.get(f"{stem}.lora_B.default.weight")
            if a is not None and b is not None:
                w = value.to(device, torch.float32)
                w.addmm_(b.to(device, torch.float32), a.to(device, torch.float32), alpha=LORA_SCALE)
                value = w.to(value.dtype).cpu()
        out[name] = value
    return out


def load(device=None) -> DvdModel:
    """DVD on `device` (CUDA when there is one), bf16, eval."""
    import torch
    from safetensors.torch import load_file

    from dvd_depth.utils import init_weights_on_device
    from dvd_depth.wan_video_dit import WanModel
    from dvd_depth.wan_video_vae import WanVideoVAE

    path = checkpoint_path()
    if not path.is_file():
        raise ModelsMissing(f"DVD isn't in the models folder ({path}). Get it on this page or in "
                            "Settings > Studio > Models.")
    if device is None:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    dtype = torch.bfloat16
    state = load_file(str(path), device="cpu")
    merge_on = device if getattr(device, "type", str(device)) == "cuda" else "cpu"
    dit_state = _merged_dit_state(state, merge_on)
    vae_state = {k[len("pipe.vae."):]: v for k, v in state.items() if k.startswith("pipe.vae.")}
    del state

    with init_weights_on_device():
        dit = WanModel(**DIT_CONFIG)
    dit.load_state_dict(dit_state, strict=True, assign=True)
    del dit_state
    dit = dit.to(device=device, dtype=dtype).eval().requires_grad_(False)

    vae = WanVideoVAE()
    vae.model.load_state_dict({k[len("model."):]: v for k, v in vae_state.items()}, strict=True)
    del vae_state
    vae = vae.to(device=device, dtype=dtype).eval().requires_grad_(False)
    logger.info("DVD loaded (%s, %s).", path.name, device)
    m = DvdModel(dit, vae, device, dtype)
    if getattr(device, "type", str(device)) == "cuda":
        # the LoRA merge ran on the card: what PyTorch kept cached from it (~0.9 GB) back to the card -- on an
        # 8 GB card it was a third of what a window had left (2026-11-07)
        torch.cuda.empty_cache()
        free, total = torch.cuda.mem_get_info()
        if total < SMALL_CARD or free < PARK_FREE:
            m.parker = _Parked(dit, device)
            logger.info("DVD: a %.0f GB card with %.1f GB free -- the transformer waits in RAM while the VAE runs "
                        "(about 0.2 s a window).", total / 2**30, free / 2**30)
    return m


def release(model) -> None:
    """Let go of the model and give its memory back to the card."""
    try:
        import gc

        import torch
        if model is not None:
            if getattr(model, "trt", None) is not None:
                model.trt.release()
                model.trt = None
            model.parker = None
            model.dit.to("cpu")
            model.vae.to("cpu")
            model.dit = model.vae = model.context = None
        del model
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        logger.debug("Releasing DVD failed", exc_info=True)


# ---- running ---------------------------------------------------------------------------------------

#: The biggest passing tensors of each transformer block -- the FFN's hidden layer (tokens x 8960: 0.66 GB at
#: 640x352, 1.3 GB at 896x480) and q / k's float32 norms and float64 RoPE copies -- go through TOKEN_SLICE tokens at
#: a time. They work token by token: bit-identical output, same speed, the transformer's own peak above its weights
#: 0.79 -> 0.44 GB at 640x352, 1.51 -> 0.84 at 896x480 (user, 2026-11-07: "cut the transformer's peak").
TOKEN_SLICE = 4096


def _by_tokens(fn, x, *per_token):
    """fn over x (1, S, C) a slice of tokens at a time, into one output; per_token tensors sliced alongside on
    their first dim."""
    import torch

    out = None
    for a in range(0, x.shape[1], TOKEN_SLICE):
        b = min(x.shape[1], a + TOKEN_SLICE)
        y = fn(x[:, a:b], *(p[a:b] for p in per_token))
        if out is None:
            out = torch.empty((x.shape[0], x.shape[1]) + tuple(y.shape[2:]), dtype=y.dtype, device=y.device)
        out[:, a:b] = y
    return out


def _self_attention(sa, q, k, v):
    """sa.attn(q, k, v) -- with Sage (the vendored flash_attention's pick here) reading (b, s, heads, d) as it lies:
    no rearranged copies of q / k / v (3 x 0.24 GB at 1344x720)."""
    import dvd_depth.wan_video_dit as D

    if D.SAGE_ATTN_AVAILABLE and not (D.FLASH_ATTN_3_AVAILABLE or D.FLASH_ATTN_2_AVAILABLE):
        b, s, c = q.shape
        n = sa.num_heads
        out = D.sageattn(q.view(b, s, n, c // n), k.view(b, s, n, c // n), v.view(b, s, n, c // n),
                         tensor_layout="NHD")
        return out.reshape(b, s, c)
    return sa.attn(q, k, v)


def _add_by_tokens(x, fn) -> None:
    """x[:, s] += fn(s) for each slice s of TOKEN_SLICE tokens (x + r, as the block's gate and residuals)."""
    for a in range(0, x.shape[1], TOKEN_SLICE):
        s = slice(a, min(x.shape[1], a + TOKEN_SLICE))
        x[:, s] += fn(s)


def _block_sliced(block, x, context, t_mod, freqs):
    """dvd_depth.wan_video_dit.DiTBlock.forward with everything that works token by token -- norms, modulation,
    projections, RoPE, cross-attention (each token asks the prompt alone), FFN, residuals -- done by token slices,
    in place on x; only self-attention sees every token at once. The same sums: bit-identical, less held."""
    from dvd_depth.wan_video_dit import modulate, rope_apply

    shift_msa, scale_msa, gate_msa, shift_mlp, scale_mlp, gate_mlp = (
        block.modulation.to(dtype=t_mod.dtype, device=t_mod.device) + t_mod).chunk(6, dim=1)
    sa, ca = block.self_attn, block.cross_attn
    pre = lambda t: modulate(block.norm1(t), shift_msa, scale_msa)          # noqa: E731
    q = _by_tokens(lambda t, f: rope_apply(sa.norm_q(sa.q(pre(t))), f, sa.num_heads), x, freqs)
    k = _by_tokens(lambda t, f: rope_apply(sa.norm_k(sa.k(pre(t))), f, sa.num_heads), x, freqs)
    v = _by_tokens(lambda t: sa.v(pre(t)), x)
    a = _self_attention(sa, q, k, v)
    q = k = v = None
    _add_by_tokens(x, lambda s: gate_msa * sa.o(a[:, s]))
    a = None
    _add_by_tokens(x, lambda s: ca(block.norm3(x[:, s]), context))
    _add_by_tokens(x, lambda s: gate_mlp * block.ffn(modulate(block.norm2(x[:, s]), shift_mlp, scale_mlp)))
    return x


def _dit_forward(m: DvdModel, latents):
    """DiffSynth's model_fn_wan_video for DVD's case: no image input, no VACE, no TeaCache."""
    import torch

    from dvd_depth.wan_video_dit import sinusoidal_embedding_1d

    dit = m.dit
    timestep = torch.tensor([TIMESTEP], dtype=m.dtype, device=m.device)
    t = dit.time_embedding(sinusoidal_embedding_1d(dit.freq_dim, timestep))
    t_mod = dit.time_projection(t).unflatten(1, (6, dit.dim))
    x, (f, h, w) = dit.patchify(latents, None)
    x = x.contiguous()                                                  # the blocks add into it in place
    freqs = torch.cat([
        dit.freqs[0][:f].view(f, 1, 1, -1).expand(f, h, w, -1),
        dit.freqs[1][:h].view(1, h, 1, -1).expand(f, h, w, -1),
        dit.freqs[2][:w].view(1, 1, w, -1).expand(f, h, w, -1),
    ], dim=-1).reshape(f * h * w, 1, -1).to(x.device)
    for block in dit.blocks:
        x = _block_sliced(block, x, m.context, t_mod, freqs)            # block(...), by token slices
    x = dit.head(x, t)
    return dit.unpatchify(x, (f, h, w))


# -- tiled VAE (user, 2026-11-07: a tester runs DVD at 1280x720 on an 8 GB card with a tiled VAE) --------------------
#: Tiles share TILE_OVERLAP latents (64 px) with their neighbours, cross-faded. Every tile of a window is the same
#: size, so TensorRT's engines (one set per size) are one set for the tile, whatever the clip's size.
TILE_OVERLAP = 8


def tile_grid(lh: int, lw: int, area: int) -> Tuple[int, int, int, int]:
    """(rows, cols, tile_h, tile_w) in latents for an lh x lw latent frame, each tile at most `area` latents: the
    grid with the least work (tiles x tile size) whose tiles share at least TILE_OVERLAP. Sizes stay even (16 px)."""
    if lh * lw <= area:
        return 1, 1, lh, lw
    best = None
    ov = TILE_OVERLAP
    for r in range(1, lh // (2 * ov) + 2):
        th = lh if r == 1 else min(lh, -(-(lh + (r - 1) * ov) // r))
        th += th % 2
        for c in range(1, lw // (2 * ov) + 2):
            tw = lw if c == 1 else min(lw, -(-(lw + (c - 1) * ov) // c))
            tw += tw % 2
            if th * tw > area:
                continue
            cost = (r * c * th * tw, r * c)
            if best is None or cost < best[0]:
                best = (cost, (r, c, th, tw))
            break                               # more columns only cost more
    if best is None:
        raise RuntimeError("DVD: no tile fits in %d latents" % area)
    return best[1]


#: What the VAE needs per latent pixel of a tile (measured 2026-11-07, 81 frames, encode or decode, whichever is
#: more: 0.55 MB the normal way; TensorRT's working memory and fp16 caches add ~0.35), the room kept back for
#: Windows and what isn't PyTorch's, and per frame pixel what a window holds besides (its frames, the depth and its
#: blend weights, the stitch's frames).
VAE_PER_LATENT = {False: 0.62 * 2**20, True: 1.0 * 2**20}
VAE_RESERVE = 1.0 * 2**30
WINDOW_PER_PIXEL = 16


def choose_tile_area(m: DvdModel, h: int, w: int, frames: int) -> int:
    """The tile area (latents) for m.tile_area at a run size: 0 (whole frames) when a whole frame fits the room
    the VAE will have, else the biggest tile that does. The transformer's weights count as room when it waits in
    RAM during the VAE (m.parker)."""
    import torch

    if getattr(m.device, "type", str(m.device)) != "cuda":
        return 0
    free, _ = torch.cuda.mem_get_info()
    p = getattr(m, "parker", None)
    if p is not None and p.on_card:
        free += sum(t.numel() * t.element_size() for _, t in p._tensors())
    room = free - VAE_RESERVE - frames * h * w * WINDOW_PER_PIXEL
    area = int(room // VAE_PER_LATENT[getattr(m, "trt", None) is not None])
    if (h // 8) * (w // 8) <= area:
        return 0
    return max(area, 4 * TILE_OVERLAP * TILE_OVERLAP)


def _starts(n: int, k: int, t: int) -> List[int]:
    """k tiles of t over n, spread evenly, ends on the edges, starts even."""
    if k == 1:
        return [0]
    return [min(n - t, int(round(i * (n - t) / (k - 1))) // 2 * 2) for i in range(k - 1)] + [n - t]


def _ramp(length: int, lo: bool, hi: bool, fade: int, device):
    """1-D blend weight for one tile: rises over `fade` at an inner edge (lo / hi: on the frame's edge, no ramp)."""
    import torch

    w = torch.ones(length, device=device)
    r = torch.arange(1, fade + 1, device=device, dtype=torch.float32) / (fade + 1)
    if not lo:
        w[:fade] = r
    if not hi:
        w[-fade:] = r.flip(0)
    return w


def tiles(lh: int, lw: int, area: int):
    """[(h0, h1, w0, w1) in latents] for tile_grid's grid."""
    r, c, th, tw = tile_grid(lh, lw, area)
    return [(h, h + th, w, w + tw) for h in _starts(lh, r, th) for w in _starts(lw, c, tw)]


def _encode_tiled(m: DvdModel, x, scale, area: int):
    """VideoVAE_.encode by tiles: (1, 16, t, H/8, W/8), cross-faded where tiles meet."""
    import torch

    trt = getattr(m, "trt", None)
    H, W = x.shape[-2:]
    lh, lw = H // 8, W // 8
    out = weight = None
    for h0, h1, w0, w1 in tiles(lh, lw, area):
        part = x[..., h0 * 8:h1 * 8, w0 * 8:w1 * 8].contiguous()
        lat = trt.encode(part, scale) if trt is not None else None
        if lat is None:
            lat = m.vae.model.encode(part, scale)
        part = None
        if out is None:
            out = torch.zeros(lat.shape[:3] + (lh, lw), device=lat.device, dtype=torch.float32)
            weight = torch.zeros((lh, lw), device=lat.device, dtype=torch.float32)
        mask = (_ramp(h1 - h0, h0 == 0, h1 >= lh, TILE_OVERLAP, lat.device).view(-1, 1)
                * _ramp(w1 - w0, w0 == 0, w1 >= lw, TILE_OVERLAP, lat.device).view(1, -1))
        out[..., h0:h1, w0:w1] += lat.float() * mask
        weight[h0:h1, w0:w1] += mask
        lat = None
        if getattr(m, "parker", None) is not None:
            torch.cuda.empty_cache()
    return out.div_(weight)


def _decode_tiled(m: DvdModel, z, scale, area: int):
    """VideoVAE_.decode by tiles, each tile straight to depth: (T, H, W) float32 0..1, cross-faded."""
    import torch

    trt = getattr(m, "trt", None)
    lh, lw = z.shape[-2:]
    depth = weight = None
    fade = TILE_OVERLAP * 8
    for h0, h1, w0, w1 in tiles(lh, lw, area):
        part = z[..., h0:h1, w0:w1].contiguous()
        video = trt.decode(part, scale, (h1 - h0) * 8, (w1 - w0) * 8) if trt is not None else None
        if video is None:
            video = m.vae.model.decode(part, scale)
        part = None
        d = video.clamp_(-1, 1)[0].float().add_(1).mul_(0.5).clamp_(0, 1).mean(dim=0)   # (T, h, w)
        video = None
        if depth is None:
            depth = torch.zeros((d.shape[0], lh * 8, lw * 8), device=d.device, dtype=torch.float32)
            weight = torch.zeros((lh * 8, lw * 8), device=d.device, dtype=torch.float32)
        mask = (_ramp(d.shape[1], h0 == 0, h1 >= lh, fade, d.device).view(-1, 1)
                * _ramp(d.shape[2], w0 == 0, w1 >= lw, fade, d.device).view(1, -1))
        depth[:, h0 * 8:h1 * 8, w0 * 8:w1 * 8] += d * mask
        weight[h0 * 8:h1 * 8, w0 * 8:w1 * 8] += mask
        d = None
        if getattr(m, "parker", None) is not None:
            torch.cuda.empty_cache()
    return depth.div_(weight)


def run_window(m: DvdModel, frames):
    """One window: `frames` (T, 3, H, W), uint8 0..255 or float 0..1 (H, W multiples of 16), in RAM or on the
    card -- the runner hands over uint8 in pinned RAM, so nothing of the caller's sits on the card through the
    transformer (2026-11-07: an 8 GB card's later windows touched full). Returns (T, H, W) float32 on the card,
    0..1, bigger is nearer. Padded to 4n+1 frames with the last one, as upstream."""
    import torch

    frames = frames.to(m.device, non_blocking=True)
    n = frames.shape[0]
    pad = (4 - n % 4 + 1) % 4
    if pad:
        frames = torch.cat([frames, frames[-1:].expand(pad, -1, -1, -1)], dim=0)
    with torch.inference_mode():
        if frames.dtype == torch.uint8:
            frames = frames.float().div_(255.0)                  # as the float path had it, bit for bit
        x = frames.to(m.dtype).mul(2).sub_(1)                    # [-1, 1]
        frames = None
        x = x.permute(1, 0, 2, 3).unsqueeze(0)                   # (1, 3, T, H, W)
        scale = [s.to(m.device, m.dtype) for s in m.vae.scale]
        h, w = x.shape[-2:]
        trt = getattr(m, "trt", None)                            # the TensorRT box (core.dvd.vae_trt)
        parked = getattr(m, "parker", None) is not None          # a smaller card: the transformer waits in RAM
        # On a smaller card each phase hands what PyTorch cached back to the card: Windows spills into system RAM
        # instead of reporting the card full, so PyTorch never frees its cache by itself -- the parked
        # transformer's 2.6 GB sat cached in pieces the VAE couldn't use, and an 8 GB card spilled (2026-11-07).
        if parked:
            m.dit_off()
            torch.cuda.empty_cache()
        area = getattr(m, "tile_area", 0)                         # > 0: tiled VAE (see tile_grid), set by the runner
        tiled = area > 0 and (h // 8) * (w // 8) > area
        if tiled:
            latents = _encode_tiled(m, x, scale, area)
        else:
            latents = trt.encode(x, scale) if trt is not None else None
            if latents is None:
                latents = m.vae.model.encode(x, scale)
        latents = latents.to(m.dtype)
        del x
        if parked:
            if trt is not None:
                trt.scratch.release()                            # TensorRT's working memory: the transformer's turn
            torch.cuda.empty_cache()
            m.dit_on()
        out = _dit_forward(m, latents)
        del latents
        if parked:
            m.dit_off()
            torch.cuda.empty_cache()
        if tiled:
            return _decode_tiled(m, out, scale, area)[:n]
        video = trt.decode(out, scale, h, w) if trt is not None else None
        if video is None:
            video = m.vae.model.decode(out, scale)
        video = video.clamp_(-1, 1)
        depth = video[0].float().add_(1).mul_(0.5).clamp_(0, 1).mean(dim=0)  # (T, H, W)
    return depth[:n]


def fit(curr, ref) -> Tuple[float, float]:
    """Upstream's compute_scale_and_shift over the overlap, its scale held to SCALE_CLIP."""
    c = curr.double().flatten()
    r = ref.double().flatten()
    a00, a01, a11 = float((c * c).sum()), float(c.sum()), float(c.numel())
    b0, b1 = float((c * r).sum()), float(r.sum())
    det = a00 * a11 - a01 * a01
    if det != 0:
        scale = (a11 * b0 - a01 * b1) / det
        shift = (-a01 * b0 + a00 * b1) / det
    else:
        scale, shift = 1.0, 0.0
    return min(max(scale, SCALE_CLIP[0]), SCALE_CLIP[1]), shift


class Stitcher:
    """Joins the windows as upstream's generate_depth_sliced does, a window at a time: each new window is
    fitted to the frames it shares with what came before and cross-faded in over them. Frames before the
    newest window's start can't change any more, and go out through `emit` (each (H, W), where the depth is: the
    card, or RAM on a smaller card -- run_clip)."""

    def __init__(self, emit: Callable):
        self.emit = emit
        self.held = None            # the latest aligned window's frames not yet emitted: (k, H, W)
        self.held_end = 0           # the clip frame after the last held one
        self.fits: List[Tuple[float, float]] = []

    def add(self, start: int, end: int, depth) -> None:
        import torch

        if self.held is None:
            self.held, self.held_end = depth, end
            return
        overlap = self.held_end - start
        if overlap <= 0:
            self._flush(len(self.held))
            self.held, self.held_end = depth, end
            return
        overlap = min(overlap, depth.shape[0], self.held.shape[0])
        ref = self.held[-overlap:]
        scale, shift = fit(depth[:overlap], ref)
        self.fits.append((scale, shift))
        aligned = depth.mul(scale).add_(shift).clamp_(min=0)
        alpha = torch.linspace(0, 1, overlap, device=depth.device, dtype=depth.dtype).view(-1, 1, 1)
        blended = ref * (1 - alpha) + aligned[:overlap] * alpha
        self._flush(len(self.held) - overlap)
        self.held = torch.cat([blended, aligned[overlap:]], dim=0)
        self.held_end = end

    def _flush(self, count: int) -> None:
        for i in range(count):
            self.emit(self.held[i])
        self.held = self.held[count:]

    def finish(self) -> None:
        if self.held is not None:
            self._flush(len(self.held))
        self.held = None


def run_clip(m: DvdModel, frames_from: Callable[[int, int], Optional[object]], total: int, window: int,
             overlap: int, emit: Callable, stopped: Optional[Callable[[], bool]] = None,
             on_window: Optional[Callable[[int, int], None]] = None) -> int:
    """Every window of a `total`-frame clip: `frames_from(start, end)` gives its frames ((T, 3, H, W) on the
    card, 0..1; None or fewer: the clip ended early). `emit(depth)` gets each frame's depth in order. Returns
    how many frames came out; stops early, with what came out so far, once `stopped()` says so."""
    out = 0

    def counted(d):
        nonlocal out
        out += 1
        emit(d)

    stitch = Stitcher(counted)
    for i, (start, end) in enumerate(windows(total, snap_window(window), overlap)):
        if stopped is not None and stopped():
            return out
        frames = frames_from(start, end)
        if frames is None or frames.shape[0] == 0:
            break
        got = frames.shape[0]
        depth = run_window(m, frames)
        if getattr(m, "parker", None) is not None:
            depth = depth.cpu()           # a smaller card: the joins in RAM, the card's room for the next window
        stitch.add(start, start + got, depth)
        depth = None
        del frames
        if on_window is not None:
            on_window(i, start + got)
        if got < end - start:
            break
    if stopped is not None and stopped():
        return out
    stitch.finish()
    for k, (s, sh) in enumerate(stitch.fits):
        logger.debug("DVD window %d: scale %.4f, shift %.4f", k + 1, s, sh)
    return out
