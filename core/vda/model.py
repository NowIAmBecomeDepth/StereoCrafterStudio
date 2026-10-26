"""model.py -- Video Depth Anything: load a size, run a clip's frames through it.

The model code is vendored in video_depth_anything/ (nagadomi's Video-Depth-Anything_iw3 fork of
ByteDance's Video Depth Anything, Apache-2.0), untouched but for one import. It is what iw3 runs,
built here from the checkpoints in the models folder -- never fetched through torch.hub.

Two of the user's iw3 fixes (nunif iw3/video_depth_anything_model.py, MIT) ride on top as wrappers,
so the vendored files stay as upstream has them:

* the overlap feature cache: each 32-frame window starts with the previous window's keyframes,
  whose DINOv2 features were computed already -- only the new frames go through the encoder;
* sign-safe alignment: the least-squares fit that joins one window to the last can come out
  negative on dark or low-contrast footage, which turns near into far for the rest of the clip.
  A negative scale is never a real answer, so it falls back to matching mean and spread.

Online is iw3's mode: frames go in one at a time and come out a window later, so memory stays the
same however long the clip is. Offline is Video Depth Anything's own reference loop: the whole clip
in memory at once, for short clips and for comparing.

Torch is imported by the functions that need it, so the page can read SIZES without loading it.
"""

from __future__ import annotations

import logging
import os
import sys
import types
from pathlib import Path
from typing import Callable, Iterable, Optional, Tuple

from core import weights_paths

logger = logging.getLogger(__name__)

#: The models folder's subfolder (studio.model_catalog VDA_S / VDA_B / VDA_L).
FOLDER = "VideoDepthAnything"

#: The page's names -> the encoder each checkpoint is for.
SIZES = {"Small": "vits", "Base": "vitb", "Large": "vitl"}

_CONFIGS = {
    "vits": {"encoder": "vits", "features": 64, "out_channels": [48, 96, 192, 384]},
    "vitb": {"encoder": "vitb", "features": 128, "out_channels": [96, 192, 384, 768]},
    "vitl": {"encoder": "vitl", "features": 256, "out_channels": [256, 512, 1024, 1024]},
}

#: The input size VDA is trained at, and the multiple every side must be.
DEFAULT_INPUT = 518
PATCH = 14
#: Longer than this to one, the short side is shrunk instead (iw3: it runs out of memory).
MAX_ASPECT = 4


class ModelsMissing(Exception):
    """The checkpoint for the size picked isn't in the models folder."""


def checkpoint_name(size: str) -> str:
    return f"video_depth_anything_{SIZES[size]}.pth"


def checkpoint_path(size: str, weights_dir: Optional[str] = None) -> Path:
    """Where `size`'s checkpoint is: in `weights_dir` when the user pointed Studio at a folder of their own
    (iw3's, say) and it is there, else in the models folder (or where it would be downloaded to)."""
    name = checkpoint_name(size)
    if weights_dir:
        own = Path(weights_dir) / name
        if own.is_file():
            return own
    return weights_paths.find(FOLDER, marker=name) / name


#: Where a checkpoints folder sits inside an iw3 install (torch.hub's cache, iw3's pretrained_models/hub): a
#: user who picks iw3's own folder (or one above it) is taken to it.
IW3_CHECKPOINTS = (("nunif", "iw3", "pretrained_models", "hub", "checkpoints"),
                   ("iw3", "pretrained_models", "hub", "checkpoints"),
                   ("pretrained_models", "hub", "checkpoints"),
                   ("hub", "checkpoints"),
                   ("checkpoints",))


def sizes_in(folder) -> list:
    """The sizes whose checkpoints are in `folder`, in SIZES' order."""
    try:
        return [s for s in SIZES if (Path(folder) / checkpoint_name(s)).is_file()]
    except (OSError, TypeError):
        return []


def resolve_weights_dir(folder) -> Optional[Path]:
    """The folder holding Video Depth Anything checkpoints for a folder the user picked: the folder itself, or
    iw3's checkpoints folder inside it (user, 2026-10-25: "point studio to their vda weights folder since they
    likely already have them downloaded from iw3"). None when neither has any."""
    if not folder:
        return None
    base = Path(folder)
    for parts in ((),) + IW3_CHECKPOINTS:
        cand = base.joinpath(*parts)
        if sizes_in(cand):
            return cand
    return None


def snap_input(value) -> int:
    """An Input Size the model can take: a multiple of 14, rounded up (iw3: 512 -> 518), 196 at least."""
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        v = DEFAULT_INPUT
    v = max(196, v)
    return v + (-v % PATCH)


def infer_size(height: int, width: int, input_size: int) -> Tuple[int, int]:
    """The (height, width) a frame is run at: its short side at `input_size`, both sides multiples of
    14, the long one cut to 4x the short (iw3's depth_anything_model.batch_preprocess)."""
    lower = snap_input(input_size)
    scale = lower / min(width, height)
    new_h, new_w = int(height * scale), int(width * scale)
    if new_h < new_w:
        new_w = min(new_w, MAX_ASPECT * new_h)
    else:
        new_h = min(new_h, MAX_ASPECT * new_w)
    new_h -= new_h % PATCH
    new_w -= new_w % PATCH
    return max(new_h, lower), max(new_w, lower)


def output_size(height: int, width: int) -> Tuple[int, int]:
    """The depth map's (height, width) for a run at (height, width): the width a multiple of 16 and
    the height even. FFmpeg 8's sliced swscale puts gray ticks on the left edge of 10-bit output
    whose width is 2-8 past a multiple of 16 (the user's iw3, 2026-10-25), and VDA's multiples of 14
    land there (882 = 16 * 55 + 2); Splatting scales the map to its own size anyway."""
    w = max(16, int(round(width / 16.0)) * 16)
    h = max(2, int(round(height / 2.0)) * 2)
    return h, w


# ---- loading -----------------------------------------------------------------------------

def load(size: str, mode: str = "online", device=None, weights_dir: Optional[str] = None):
    """`size`'s model on `device` (CUDA when there is one), eval, with iw3's fixes on. `weights_dir`: the
    user's own checkpoints folder, looked in first."""
    import torch

    path = checkpoint_path(size, weights_dir)
    if not path.is_file():
        raise ModelsMissing(f"Video Depth Anything {size} isn't in the models folder ({path})"
                            + (f" or in {weights_dir}" if weights_dir else "")
                            + ". Get it in Settings > Studio > Models, or point VDA at the folder that has it.")
    if device is None:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    cfg = _CONFIGS[SIZES[size]]
    if mode == "offline":
        from video_depth_anything.video_depth import VideoDepthAnything
        model = VideoDepthAnything(**cfg)
    else:
        from video_depth_anything.video_depth_online_torch import VideoDepthAnythingOnline
        model = VideoDepthAnythingOnline(device=device, metric_depth=False, **cfg)
    state = torch.load(str(path), weights_only=True, map_location="cpu")
    model.load_state_dict(state, strict=True)
    del state
    model = model.to(device).eval()
    if mode != "offline":
        enable_overlap_feature_cache(model)
    enable_sign_safe_alignment(model)
    logger.info("Video Depth Anything %s loaded (%s, %s).", size, mode, device)
    return model


def release(model) -> None:
    """Let go of the model and give its memory back to the card."""
    try:
        import torch
        if model is not None:
            model.to("cpu")
        del model
        import gc
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        logger.debug("Releasing Video Depth Anything failed", exc_info=True)


# ---- iw3's fixes (nunif iw3/video_depth_anything_model.py, MIT) ---------------------------

def _overlap_cached_forward(self, x):
    # Same as VideoDepthAnythingOnline.forward, but the DINOv2 features of the
    # OVERLAP frames are reused from the previous window. Each window starts with
    # the previous window's KEYFRAMES (infer_depth copies pre_input[:, KEYFRAMES]
    # in), and the encoder sees each frame on its own, so those features are
    # the ones already computed. Only the new frames go through the encoder.
    import torch
    import torch.nn.functional as F

    B, T, C, H, W = x.shape
    patch_h, patch_w = H // 14, W // 14
    layer_idx = self.intermediate_layer_idx[self.encoder]
    infer_len, overlap, keyframes = self._overlap_consts
    full_window = B == 1 and T == infer_len
    cached = self._overlap_feats
    if (self.overlap_cache_enabled and full_window and self.pre_input is not None
            and cached is not None and cached[0] == (H, W, x.dtype)):
        new = self.pretrained.get_intermediate_layers(
            x[:, overlap:].flatten(0, 1), layer_idx, return_class_token=True)
        features = tuple((torch.cat((ct, nt)), torch.cat((cc, nc)))
                         for (ct, cc), (nt, nc) in zip(cached[1], new))
    else:
        features = self.pretrained.get_intermediate_layers(
            x.flatten(0, 1), layer_idx, return_class_token=True)
    if self.overlap_cache_enabled and full_window:
        # list indexing copies, so the full window's features are freed
        self._overlap_feats = ((H, W, x.dtype),
                               tuple((t[keyframes], c[keyframes]) for t, c in features))
    else:
        self._overlap_feats = None

    depth = self.head(features, patch_h, patch_w, T, return_hidden_state_list=False)[0]
    depth = F.interpolate(depth, size=(H, W), mode="bilinear", align_corners=True).to(depth.dtype)
    depth = F.relu(depth)
    return depth.squeeze(1).unflatten(0, (B, T))  # [B, T, H, W]


def enable_overlap_feature_cache(model):
    """Patch a loaded VideoDepthAnythingOnline to skip re-encoding overlap frames.
    Set IW3_VDA_NO_OVERLAP_CACHE=1 to turn it off (iw3's switch)."""
    mod = sys.modules.get(type(model).__module__)
    consts = tuple(getattr(mod, name, None) for name in ("INFER_LEN", "OVERLAP", "KEYFRAMES"))
    needed = ("pretrained", "head", "intermediate_layer_idx", "encoder", "pre_input")
    if (type(model).__name__ != "VideoDepthAnythingOnline" or None in consts
            or len(consts[2]) != consts[1] or not all(hasattr(model, a) for a in needed)):
        return model  # the model code changed shape; leave it alone
    model._overlap_consts = (consts[0], consts[1], list(consts[2]))
    model._overlap_feats = None
    model.overlap_cache_enabled = not os.getenv("IW3_VDA_NO_OVERLAP_CACHE")
    model.forward = types.MethodType(_overlap_cached_forward, model)
    return model


def _sign_safe_fit(fit):
    # VDA joins each 32-frame window to the last by a least-squares scale +
    # shift on two anchor frames. On dark or low-contrast footage that fit can
    # come out negative, which turns near into far; every later window is then
    # fitted to the inverted one and stays inverted (the eyes swap for long
    # stretches). A negative scale is never a real answer, so fall back to
    # matching the anchors' mean and spread, which keeps the sign.
    # (Torch tensors from the online loop, numpy arrays from the offline one.)
    def wrapped(prediction, target, mask=None, scale_only=False):
        scale, shift = fit(prediction, target, mask, scale_only)
        if float(scale) > 0:
            return scale, shift
        import torch
        p = torch.as_tensor(prediction).to(torch.float32)
        q = torch.as_tensor(target).to(device=p.device, dtype=torch.float32)
        if mask is not None:
            m = torch.as_tensor(mask).to(device=p.device, dtype=torch.bool)
            p, q = p[m], q[m]
        if scale_only:
            pm = p.mean().item()
            return (q.mean().item() / pm if pm > 1e-6 else 1.0), 0
        ps = p.std().item()
        scale = q.std().item() / ps if ps > 1e-6 else 1.0
        return scale, (q.mean() - scale * p.mean()).item()
    wrapped._iw3_sign_safe = True
    return wrapped


def enable_sign_safe_alignment(model):
    """Patch the model's module's window fit so it can't invert depth.
    Set IW3_VDA_ALLOW_NEGATIVE_SCALE=1 to turn it off (iw3's switch)."""
    mod = sys.modules.get(type(model).__module__)
    fit = getattr(mod, "compute_scale_and_shift", None)
    if fit is None or getattr(fit, "_iw3_sign_safe", False) or os.getenv("IW3_VDA_ALLOW_NEGATIVE_SCALE"):
        return
    mod.compute_scale_and_shift = _sign_safe_fit(fit)


# ---- running ---------------------------------------------------------------------------------

def preprocess(x, size: Tuple[int, int]):
    """(B, 3, H, W) uint8 RGB frames on the card -> the model's input at `size` (iw3's resize:
    bilinear with antialias, then ImageNet's mean and spread)."""
    import torch
    import torch.nn.functional as F

    x = x.to(torch.float32).div_(255.0)
    x = F.interpolate(x, size=tuple(size), mode="bilinear", align_corners=False, antialias=True)
    x.clamp_(0, 1)
    mean = torch.tensor([0.485, 0.456, 0.406], dtype=x.dtype, device=x.device).reshape(1, 3, 1, 1)
    stdv = torch.tensor([0.229, 0.224, 0.225], dtype=x.dtype, device=x.device).reshape(1, 3, 1, 1)
    return x.sub_(mean).div_(stdv)


def run_online(model, batches: Iterable, size: Tuple[int, int], emit: Callable, use_amp: bool = True,
               stopped: Optional[Callable[[], bool]] = None) -> int:
    """Every frame of `batches` ((B, 3, H, W) uint8 on the model's card) through the online model.
    `emit(depth)` gets each frame's depth in order, (h, w) float32 on the card, raw (bigger is
    nearer). Returns how many frames came out; stops early, with what came out so far, once
    `stopped()` says so."""
    import torch

    model.reset_state()
    if hasattr(model, "_overlap_feats"):
        model._overlap_feats = None
    n_in = n_out = 0

    def take(ret):
        nonlocal n_out
        for d in ret or ():
            if n_out < n_in:
                emit(d)
                n_out += 1

    with torch.inference_mode():
        for batch in batches:
            if stopped is not None and stopped():
                return n_out
            x = preprocess(batch, size)
            for i in range(x.shape[0]):
                n_in += 1
                take(model.infer(x[i], use_amp=use_amp))
        guard = 0
        while n_out < n_in:
            if stopped is not None and stopped():
                return n_out
            take(model.infer(None, use_amp=use_amp))
            guard += 1
            if guard > n_in + 8:            # each flush gives a window; never spin on a model that gives none
                raise RuntimeError(f"Video Depth Anything gave {n_out} of {n_in} frames.")
    model.reset_state()
    return n_out


def run_offline(model, frames, input_size: int, use_amp: bool = True):
    """Video Depth Anything's own loop over the whole clip: `frames` (N, H, W, 3) uint8 in memory.
    Returns (N, H, W) float32 raw depth (bigger is nearer)."""
    import torch

    device = "cuda" if next(model.parameters()).is_cuda else "cpu"
    with torch.inference_mode():
        depths, _fps = model.infer_video_depth(frames, 0, input_size=snap_input(input_size), device=device,
                                               fp32=not use_amp)
    return depths
