"""sam3_trt.py -- TensorRT for SAM 3's tracking (user, 2026-10-31): the image backbone's ViT trunk and the
tracker's memory attention as TensorRT engines, built the first time they're needed (about a minute in all,
once a GPU / TensorRT), through Studio's own pipelines/trt_unet.py. third_party/sam3 is untouched: the
engines are put in on the loaded model's instances (sam3_backend.Sam3Engine.load).

Where a tracked frame's time went (RTX 5060 Ti, 2026-10-31; click 292 ms, text 388 ms): the ViT trunk 173 ms,
the memory attention 64-78 (its cross-attention -- one head of width 256 against 20-47k memory tokens -- is
48 ms of it: SageAttention stops at width 128, and Windows' PyTorch has no flash kernel for it; TensorRT's
runs it 1.45x faster), the rest small or not model work.

* the trunk: one fixed shape (1 x 3 x 1008 x 1008). The export uses SAM 3's own real-valued RoPE
  (use_rope_real) and, for the fused MLP op (aten._addmm_activation, no ONNX form), linear + GELU (tanh:
  the closer of the two). Its PyTorch weights stay on the GPU beside the engine's (0.9 GB): SAM 3 reads its
  device from its parameters, and a call the engine doesn't take needs them.
* the memory attention: a range of object counts and memory lengths. Its RoPE tables are inputs: the
  current frame's, and the memory's -- the frame's repeated per remembered frame, and no rotation (cos 1,
  sin 0) for the object-pointer tokens SAM 3 leaves unrotated -- so the count of those, a Python number in
  SAM 3, needn't be fixed in the engine.
* the text detector (text layers; a click layer calls it once, on its first frame): its encoder past the
  fusion step (which stays in PyTorch -- SAM 3 adds the pooled text to the caller's image features in
  place), its decoder and its mask head, at one fixed shape each (one image, 200 queries, 33 prompt tokens).
  Their torch nn.MultiheadAttentions export without attention weights (_no_attn_weights): the default path
  converts wrong under bf16 once a mask is added. Car clip, text: 272 -> 232 ms a frame, IoU 0.995.

The masks differ from PyTorch's along edges only (car clip, 100 frames: IoU 0.99), as much as SAM 3's own
real-valued RoPE in PyTorch moves them: its 32 bf16 layers carry any rounding change through.
A call an engine can't take runs in PyTorch; a build or run that fails puts that part back on PyTorch
for the session, said once.
"""

from __future__ import annotations

import contextlib
import functools
import importlib.util
import logging
import math
from pathlib import Path

import torch
import torch.nn.functional as F

logger = logging.getLogger(__name__)

EXPORT_VERSION = 1
SIDE = 1008                       # SAM 3's frame size
#: the memory attention's ranges: objects in one call, memory tokens (a frame's 5184 x up to 7 remembered
#: frames + conditioning frames -- 10 on a text layer -- plus object pointers, 4 tokens each: 72 seen). TensorRT plans the engine's working memory for the largest: 8 objects x 12 frames held 3.8 GB
#: (2026-10-31). More objects run in groups of MEM_OBJECTS[2]; a longer memory runs in PyTorch.
MEM_OBJECTS = (1, 1, 2)          # objects a call: 1-2 (4 held 1.75 GB, 2 about half)
MEM_TOKENS_OPT = 7 * 5184 + 64
MEM_TOKENS_MAX = 11 * 5184 + 128   # a text layer settles at 10 frames + 72 pointer tokens

_T = None


def studio_trt():
    global _T
    if _T is None:
        path = Path(__file__).resolve().parents[2] / "pipelines" / "trt_unet.py"
        spec = importlib.util.spec_from_file_location("studio_trt_unet_sam3", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _T = mod
    return _T


def unavailable_reason() -> str:
    try:
        return studio_trt().unavailable_reason()
    except Exception as e:
        return f"Studio's TensorRT code didn't load ({type(e).__name__}: {e})"


def _rotate(x, cos, sin):
    """x [..., L, 2c] rotated by cos / sin [L, c] in fp32, back to x's type -- apply_rotary_enc's maths."""
    xr = x.float().unflatten(-1, (-1, 2))
    a, b = xr[..., 0], xr[..., 1]
    return torch.stack([a * cos - b * sin, a * sin + b * cos], dim=-1).flatten(-2).type_as(x)


# ---- the trunk ---------------------------------------------------------------------------------------------

@contextlib.contextmanager
def _trunk_export_form(trunk):
    from sam3.model import vitdet
    attns = [m for m in trunk.modules() if isinstance(m, vitdet.Attention) and m.use_rope and not m.use_ve_rope]
    saved = [(m, m.use_rope_real) for m in attns]
    for m in attns:
        m.use_rope_real = True
        m.freqs_cis_real = m.freqs_cis.real.contiguous()
        m.freqs_cis_imag = m.freqs_cis.imag.contiguous()
    real_act = vitdet.addmm_act

    def addmm_act(act, linear, mat1):
        y = F.linear(mat1.to(torch.bfloat16), linear.weight.to(torch.bfloat16), linear.bias.to(torch.bfloat16))
        return F.gelu(y, approximate="tanh")
    vitdet.addmm_act = addmm_act
    try:
        yield
    finally:
        vitdet.addmm_act = real_act
        for m, r in saved:
            m.use_rope_real = r


class _TrunkExport(torch.nn.Module):
    """module: the trunk, registered so its weights export as weights; forward: what runs it. Made .eval():
    torch.onnx.export puts the wrapper's own mode back afterwards, all the way down -- a new module is in
    training mode, and that turned the tracker's dropout on for every PyTorch call after a build
    (2026-10-31: 39% off)."""

    def __init__(self, module, forward):
        super().__init__()
        self.m = module
        self.f = forward

    def forward(self, x):
        out = self.f(x)
        return out[0] if isinstance(out, (list, tuple)) else out


# ---- the memory attention ------------------------------------------------------------------------------------

_ROPE = {}


def _rope_attention_forward(self, q, k, v, num_k_exclude_rope=0):
    """sam3.sam.transformer.RoPEAttention.forward with its rotation tables from _ROPE: "q" for the current
    frame (self-attention's keys too), "k" for the memory (pointer tokens unrotated)."""
    q = self._separate_heads(self.q_proj(q), self.num_heads)
    k = self._separate_heads(self.k_proj(k), self.num_heads)
    v = self._separate_heads(self.v_proj(v), self.num_heads)
    qc, qs = _ROPE["q"]
    kc, ks = _ROPE["k"] if _ROPE.get("cross_now") else _ROPE["q"]
    q = _rotate(q, qc, qs)
    k = _rotate(k, kc, ks)
    out = F.scaled_dot_product_attention(q, k, v)
    return self.out_proj(self._recombine_heads(out))


@contextlib.contextmanager
def _memattn_export_form(encoder):
    """Each layer's attentions on the _ROPE tables: the cross-attention ("cross_attn_image") on the memory's."""
    from sam3.sam.transformer import RoPEAttention
    saved = []
    for layer in encoder.layers:
        for name in ("self_attn", "cross_attn_image"):
            att = getattr(layer, name, None)
            if isinstance(att, RoPEAttention):
                cross = name == "cross_attn_image"

                def fwd(q, k, v, num_k_exclude_rope=0, _a=att, _cross=cross):
                    _ROPE["cross_now"] = _cross
                    return _rope_attention_forward(_a, q, k, v, num_k_exclude_rope)
                saved.append((att, att.__dict__.get("forward")))
                att.forward = fwd
    try:
        yield
    finally:
        for att, f in saved:
            if f is None:
                att.__dict__.pop("forward", None)
            else:
                att.forward = f
        _ROPE.clear()


class _MemAttnExport(torch.nn.Module):
    def __init__(self, module, forward):
        super().__init__()
        self.m = module
        self.f = forward

    def forward(self, src, prompt, src_pos, prompt_pos, q_cos, q_sin, k_cos, k_sin):
        _ROPE["q"], _ROPE["k"] = (q_cos, q_sin), (k_cos, k_sin)
        out = self.f(src=src, prompt=prompt, src_key_padding_mask=None, src_pos=src_pos, prompt_pos=prompt_pos)
        return out["memory"]


# ---- the text detector -----------------------------------------------------------------------------------------

DET_SIDE = 72                     # the detector's one feature level (1008 / 14)
DET_QUERIES = 200
#: prompt tokens: SAM 3's 32 text tokens + 1, on every text layer seen. Fixed: the export bakes the count into
#: the attention masks' reshapes (a 1-96 range built, but only 33 could run), so any other count runs in PyTorch.
DET_TOKENS = 33


@contextlib.contextmanager
def _no_attn_weights(module):
    """Every nn.MultiheadAttention in `module` without its attention weights -- SAM 3 drops them ([0]).
    Their default path (need_weights=True: baddbmm plus the mask) under bf16 autocast comes out of
    ONNX -> TensorRT wrong once a key-padding mask is added: the decoder's text attention was unrelated to
    PyTorch's (rel 1.17, 2026-10-31) and every layer after it off. Without the weights (the SDPA path) it
    converts right."""
    mods = [m for m in module.modules() if isinstance(m, torch.nn.MultiheadAttention) and "forward" not in m.__dict__]
    for m in mods:
        m.forward = functools.partial(type(m).forward, m, need_weights=False)
    try:
        yield
    finally:
        for m in mods:
            m.__dict__.pop("forward", None)


def _autocast_now():
    """The caller's CUDA autocast, for an export to record the same casts (no weight cache: a cached cast
    weight is a constant that needs grad, which the tracer refuses)."""
    if torch.is_autocast_enabled("cuda"):
        return torch.autocast("cuda", dtype=torch.get_autocast_dtype("cuda"), cache_enabled=False)
    return contextlib.nullcontext()


class _DetEncoderExport(torch.nn.Module):
    """The detector encoder past its fusion step (pooled text added to the image features, in place -- that
    stays in PyTorch, as SAM 3 does it): the base TransformerEncoder over the one 72 x 72 level."""

    def __init__(self, module):
        super().__init__()
        self.m = module

    def forward(self, src, pos, prompt, prompt_mask):
        out = super(type(self.m), self.m).forward([src], src_key_padding_masks=[None], pos=[pos],
                                                  prompt=prompt.transpose(0, 1), prompt_key_padding_mask=prompt_mask)
        return out[0], out[2]


class _DetDecoderExport(torch.nn.Module):
    def __init__(self, module, forward):
        super().__init__()
        self.m = module
        self.f = forward

    def forward(self, tgt, memory, pos, valid_ratios, memory_text, text_mask, level_start_index, spatial_shapes):
        return tuple(self.f(tgt=tgt, memory=memory, memory_key_padding_mask=None, pos=pos, reference_boxes=None,
                            level_start_index=level_start_index, spatial_shapes=spatial_shapes,
                            valid_ratios=valid_ratios, tgt_mask=None, memory_text=memory_text,
                            text_attention_mask=text_mask, apply_dac=False))


class _MaskHeadExport(torch.nn.Module):
    def __init__(self, module, forward):
        super().__init__()
        self.m = module
        self.f = forward

    def forward(self, f0, f1, f2, queries, enc, prompt, prompt_mask):
        ids = torch.zeros(1, dtype=torch.long, device=queries.device)        # one image: SAM 3 doesn't index
        out = self.f(backbone_feats=[f0, f1, f2], obj_queries=queries, image_ids=ids, encoder_hidden_states=enc,
                     prompt=prompt, prompt_mask=prompt_mask)
        return out["pred_masks"], out["semantic_seg"]


def _tokens_ok(prompt, mask):
    return (torch.is_tensor(prompt) and torch.is_tensor(mask) and prompt.is_cuda and prompt.dim() == 3
            and tuple(prompt.shape[:2]) == (DET_TOKENS, 1)
            and mask.dtype == torch.bool and tuple(mask.shape) == (1, DET_TOKENS))


# ---- on the model ---------------------------------------------------------------------------------------------

class Sam3TensorRT:
    """The trunk's and the memory attention's calls through engines; PyTorch where they can't run."""

    def __init__(self, model):
        self.T = studio_trt()
        self.scratch = self.T.Scratch()
        self.failed = set()
        self.engines = {}
        self.calls = {"trunk": [0, 0], "memory attention": [0, 0], "text encoder": [0, 0], "text decoder": [0, 0],
                      "mask head": [0, 0]}
        self.trunk = model.detector.backbone.vision_backbone.trunk
        self.trunk_forward = self.trunk.forward                  # sam3_backend's bf16 wrapper, or SAM 3's
        self.trunk.forward = self._trunk
        self.encoder = model.tracker.transformer.encoder
        self.encoder_forward = self.encoder.forward
        self.encoder.forward = self._memattn
        from sam3.sam.transformer import RoPEAttention
        att = next(a for l in self.encoder.layers for a in (getattr(l, "self_attn", None),) if isinstance(a, RoPEAttention))
        self.frame_cis = att.freqs_cis                           # [5184, c] complex64, SAM 3's own
        self._k_tables = {}
        # the text detector (text layers only: a click layer never calls it)
        det = model.detector
        self.det_encoder = det.transformer.encoder
        self.det_encoder_forward = self.det_encoder.forward
        self.det_encoder.forward = self._det_encoder
        self.det_decoder = det.transformer.decoder
        self.det_decoder_forward = self.det_decoder.forward
        self.det_decoder.forward = self._det_decoder
        self.mask_head = det.segmentation_head
        self.mask_head_forward = self.mask_head.forward
        self.mask_head.forward = self._mask_head
        self._det_consts = None

    # -- engines --

    def _name(self, part):
        T = self.T
        return f"sam3_{part}_v{EXPORT_VERSION}_{T._gpu_tag()}_trt{T._trt_version()}.engine"

    def _engine(self, part, export, profile=None):
        if part in self.engines:
            return self.engines[part]
        T = self.T
        name = self._name(part)
        if not (T.ENGINE_DIR / name).exists():
            logger.info("SAM 3: building its TensorRT %s engine (about a minute, once)...", part)
        path = T.build_engine(name, export, profile=profile)
        engine = T.Engine(path, self.scratch)
        logger.info("SAM 3: TensorRT on for the %s (%.0f MB working memory).", part, engine.scratch / 2**20)
        self.engines[part] = engine
        return engine

    def _fail(self, part, e):
        self.failed.add(part)
        self.engines.pop(part, None)
        logger.warning("SAM 3: the TensorRT %s failed (%s: %s); it runs in PyTorch from here on.", part,
                       type(e).__name__, e)

    @staticmethod
    def _run(engine, feeds: dict):
        return engine(*[feeds[n] for n in engine.inputs])

    @staticmethod
    def _run_all(engine, feeds: dict) -> dict:
        """Every output by name; inputs the export found unused aren't in the engine and are skipped."""
        return engine.run(*[feeds[n] for n in engine.inputs])

    # -- the trunk --

    def _trunk(self, x, *a, **kw):
        if "trunk" not in self.failed and not a and not kw and torch.is_tensor(x) and x.is_cuda \
                and tuple(x.shape) == (1, 3, SIDE, SIDE):
            try:
                def export(onnx_path):
                    with _trunk_export_form(self.trunk), torch.no_grad():
                        torch.onnx.export(_TrunkExport(self.trunk, self.trunk_forward).eval(), (x.float(),), str(onnx_path),
                                          input_names=["x"], output_names=["out"], opset_version=17,
                                          do_constant_folding=True, dynamo=False)
                engine = self._engine("trunk", export)
                out = self._run(engine, {"x": x.float()})
                self.calls["trunk"][0] += 1
                return [out]
            except Exception as e:
                self._fail("trunk", e)
        self.calls["trunk"][1] += 1
        return self.trunk_forward(x, *a, **kw)

    # -- the memory attention --

    def _tables(self, mem_tokens, num_ptr):
        """cos / sin for the current frame [5184, c] and the memory [mem_tokens, c] (fp32)."""
        key = (mem_tokens, num_ptr)
        if key not in self._k_tables:
            self._k_tables.clear()
            cis = self.frame_cis.to("cuda")
            qc, qs = cis.real.float().contiguous(), cis.imag.float().contiguous()
            r = (mem_tokens - num_ptr) // cis.shape[0]
            kc = torch.cat([qc.repeat(r, 1), torch.ones(num_ptr, qc.shape[1], device="cuda")])
            ks = torch.cat([qs.repeat(r, 1), torch.zeros(num_ptr, qs.shape[1], device="cuda")])
            self._k_tables[key] = (qc, qs, kc.contiguous(), ks.contiguous())
        return self._k_tables[key]

    def _memattn(self, src, prompt, src_mask=None, prompt_mask=None, src_key_padding_mask=None,
                 prompt_key_padding_mask=None, src_pos=None, prompt_pos=None, feat_sizes=None,
                 num_obj_ptr_tokens=0, **kw):
        args = dict(src=src, prompt=prompt, src_mask=src_mask, prompt_mask=prompt_mask,
                    src_key_padding_mask=src_key_padding_mask, prompt_key_padding_mask=prompt_key_padding_mask,
                    src_pos=src_pos, prompt_pos=prompt_pos, feat_sizes=feat_sizes,
                    num_obj_ptr_tokens=num_obj_ptr_tokens, **kw)
        s, sp, sk = src, src_pos, src_key_padding_mask
        if isinstance(s, list) and len(s) == 1 and isinstance(sp, list) and len(sp) == 1:
            s, sp = s[0], sp[0]
            sk = sk[0] if isinstance(sk, list) and len(sk) == 1 else sk
        n = self.frame_cis.shape[0]
        ok = ("memory attention" not in self.failed and not kw and src_mask is None and prompt_mask is None
              and sk is None and prompt_key_padding_mask is None and torch.is_tensor(s) and torch.is_tensor(sp)
              and torch.is_tensor(prompt) and torch.is_tensor(prompt_pos) and s.is_cuda and s.dim() == 3
              and s.shape[0] == n and prompt.shape[1] == s.shape[1] and s.shape[1] >= 1
              and n <= prompt.shape[0] <= MEM_TOKENS_MAX
              and (prompt.shape[0] - int(num_obj_ptr_tokens)) % n == 0)
        if ok:
            try:
                qc, qs, kc, ks = self._tables(int(prompt.shape[0]), int(num_obj_ptr_tokens))
                names = ["src", "prompt", "src_pos", "prompt_pos", "q_cos", "q_sin", "k_cos", "k_sin"]
                # every object on its own in the memory attention: more than the engine takes run in groups
                # of up to MEM_OBJECTS[2], the same maths (no object limit -- the user has tracked 9)
                g = MEM_OBJECTS[2]
                feeds = (s[:, :g], prompt[:, :g], sp[:, :g], prompt_pos[:, :g], qc, qs, kc, ks)
                dt =torch.get_autocast_dtype("cuda") if torch.is_autocast_enabled("cuda") else None

                def export(onnx_path):
                    dyn = {"src": {1: "b"}, "src_pos": {1: "b"}, "prompt": {0: "l", 1: "b"},
                           "prompt_pos": {0: "l", 1: "b"}, "k_cos": {0: "l"}, "k_sin": {0: "l"}, "out": {1: "b"}}
                    ac = torch.autocast("cuda", dtype=dt) if dt is not None else contextlib.nullcontext()
                    with _memattn_export_form(self.encoder), torch.no_grad(), ac:
                        torch.onnx.export(_MemAttnExport(self.encoder, self.encoder_forward).eval(), feeds, str(onnx_path),
                                          input_names=names, output_names=["out"], dynamic_axes=dyn,
                                          opset_version=17, do_constant_folding=True, dynamo=False)
                c, d, dm = s.shape[2], qc.shape[1], prompt.shape[2]
                prof = {}
                for b_i, l_i, i in ((MEM_OBJECTS[0], n, 0), (MEM_OBJECTS[1], MEM_TOKENS_OPT, 1),
                                    (MEM_OBJECTS[2], MEM_TOKENS_MAX, 2)):
                    shapes = {"src": (n, b_i, c), "src_pos": (n, b_i, c), "prompt": (l_i, b_i, dm),
                              "prompt_pos": (l_i, b_i, dm), "q_cos": (n, d), "q_sin": (n, d), "k_cos": (l_i, d),
                              "k_sin": (l_i, d)}
                    for k_, v_ in shapes.items():
                        prof.setdefault(k_, [None, None, None])[i] = v_
                engine = self._engine("memattn", export, profile={k_: tuple(v_) for k_, v_ in prof.items()})
                outs = []
                for a in range(0, s.shape[1], g):
                    part = (s[:, a:a + g], prompt[:, a:a + g], sp[:, a:a + g], prompt_pos[:, a:a + g], qc, qs, kc, ks)
                    outs.append(self._run(engine, dict(zip(names, part))))
                out = outs[0] if len(outs) == 1 else torch.cat(outs, dim=1)
                self.calls["memory attention"][0] += 1
                return {"memory": out, "pos_embed": sp, "padding_mask": sk}
            except Exception as e:
                self._fail("memory attention", e)
        self.calls["memory attention"][1] += 1
        return self.encoder_forward(**args)

    # -- the text detector --

    def _consts(self, device):
        """The encoder's level bookkeeping for one 72 x 72 level and no padding (what SAM 3 computes)."""
        if self._det_consts is None:
            self._det_consts = (torch.zeros(1, dtype=torch.long, device=device),
                                torch.tensor([[DET_SIDE, DET_SIDE]], dtype=torch.long, device=device),
                                torch.ones((1, 1, 2), device=device))
        return self._det_consts

    def _det_encoder(self, src, prompt, src_key_padding_mask=None, src_pos=None, prompt_key_padding_mask=None,
                     prompt_pos=None, feat_sizes=None, encoder_extra_kwargs=None):
        n = DET_SIDE * DET_SIDE
        m = self.det_encoder
        ok = ("text encoder" not in self.failed and encoder_extra_kwargs is None and src_key_padding_mask is None
              and isinstance(src, list) and len(src) == 1 and isinstance(src_pos, list) and len(src_pos) == 1
              and torch.is_tensor(src[0]) and torch.is_tensor(src_pos[0]) and src[0].is_cuda
              and tuple(src[0].shape[:2]) == (n, 1) and src_pos[0].shape == src[0].shape
              and feat_sizes is not None and len(feat_sizes) == 1 and tuple(feat_sizes[0]) == (DET_SIDE, DET_SIDE)
              and _tokens_ok(prompt, prompt_key_padding_mask))
        if ok:
            names = ["src", "pos", "prompt", "prompt_mask"]
            try:
                def export(onnx_path):
                    s = src[0].reshape(DET_SIDE, DET_SIDE, 1, -1).permute(2, 3, 0, 1)
                    p = src_pos[0].reshape(DET_SIDE, DET_SIDE, 1, -1).permute(2, 3, 0, 1)
                    with _no_attn_weights(m), torch.no_grad(), _autocast_now():
                        torch.onnx.export(_DetEncoderExport(m).eval(), (s, p, prompt, prompt_key_padding_mask),
                                          str(onnx_path), input_names=names, output_names=["memory", "pos_embed"],
                                          opset_version=17, do_constant_folding=True, dynamo=False)
                engine = self._engine("detenc", export)
            except Exception as e:
                self._fail("text encoder", e)
                ok = False
        if not ok:
            self.calls["text encoder"][1] += 1
            return self.det_encoder_forward(src=src, prompt=prompt, src_key_padding_mask=src_key_padding_mask,
                                            src_pos=src_pos, prompt_key_padding_mask=prompt_key_padding_mask,
                                            prompt_pos=prompt_pos, feat_sizes=feat_sizes,
                                            encoder_extra_kwargs=encoder_extra_kwargs)
        # SAM 3's fusion step as SAM 3 does it -- the caller's lists reshaped and the pooled text added in place
        from sam3.model.encoder import pool_text_feat
        src[0] = src[0].reshape(DET_SIDE, DET_SIDE, 1, -1).permute(2, 3, 0, 1)
        src_pos[0] = src_pos[0].reshape(DET_SIDE, DET_SIDE, 1, -1).permute(2, 3, 0, 1)
        if m.add_pooled_text_to_img_feat:
            pooled = m.text_pooling_proj(pool_text_feat(prompt, prompt_key_padding_mask, m.pool_text_with_mask))
            src[0] = src[0].add_(pooled[..., None, None])
        lsi, shapes, ratios = self._consts(prompt.device)
        try:
            out = self._run_all(engine, {"src": src[0], "pos": src_pos[0], "prompt": prompt,
                                         "prompt_mask": prompt_key_padding_mask})
            memory, pos_embed = out["memory"], out["pos_embed"]
            self.calls["text encoder"][0] += 1
        except Exception as e:                        # past the fusion: the rest in PyTorch, not all again
            self._fail("text encoder", e)
            self.calls["text encoder"][1] += 1
            res = super(type(m), m).forward(src, src_key_padding_masks=[None], pos=src_pos,
                                            prompt=prompt.transpose(0, 1),
                                            prompt_key_padding_mask=prompt_key_padding_mask)
            memory, pos_embed = res[0], res[2]
        return {"memory": memory, "padding_mask": None, "pos_embed": pos_embed, "memory_text": prompt,
                "level_start_index": lsi, "spatial_shapes": shapes, "valid_ratios": ratios}

    _DEC_NONE = ("memory_key_padding_mask", "reference_boxes", "tgt_mask", "decoder_extra_kwargs",
                 "obj_roi_memory_feat", "obj_roi_memory_mask", "box_head_trk")
    _DEC_KEYS = {"tgt", "memory", "pos", "level_start_index", "spatial_shapes", "valid_ratios", "memory_text",
                 "text_attention_mask", "apply_dac", "is_instance_prompt"} | set(_DEC_NONE)

    def _det_decoder(self, *a, **kw):
        n = DET_SIDE * DET_SIDE
        tgt, mem, pos = kw.get("tgt"), kw.get("memory"), kw.get("pos")
        ok = ("text decoder" not in self.failed and not a and set(kw) <= self._DEC_KEYS
              and all(kw.get(k) is None for k in self._DEC_NONE)
              and kw.get("apply_dac") is False and kw.get("is_instance_prompt", False) is False
              and self.det_decoder.presence_token is not None
              and torch.is_tensor(tgt) and tuple(tgt.shape) == (DET_QUERIES, 1, tgt.shape[2]) and tgt.is_cuda
              and torch.is_tensor(mem) and tuple(mem.shape[:2]) == (n, 1) and torch.is_tensor(pos)
              and pos.shape == mem.shape and torch.is_tensor(kw.get("valid_ratios"))
              and torch.is_tensor(kw.get("spatial_shapes")) and tuple(kw["spatial_shapes"].shape) == (1, 2)
              and torch.is_tensor(kw.get("level_start_index"))
              and _tokens_ok(kw.get("memory_text"), kw.get("text_attention_mask")))
        if ok:
            names = ["tgt", "memory", "pos", "valid_ratios", "memory_text", "text_mask", "level_start_index",
                     "spatial_shapes"]
            feeds = dict(zip(names, (tgt, mem, pos, kw["valid_ratios"], kw["memory_text"], kw["text_attention_mask"],
                                     kw["level_start_index"], kw["spatial_shapes"])))
            try:
                def export(onnx_path):
                    with _no_attn_weights(self.det_decoder), torch.no_grad(), _autocast_now():
                        torch.onnx.export(_DetDecoderExport(self.det_decoder, self.det_decoder_forward).eval(),
                                          tuple(feeds[k] for k in names), str(onnx_path), input_names=names,
                                          output_names=["hs", "boxes", "presence", "presence_feats"],
                                          opset_version=17, do_constant_folding=True, dynamo=False)
                engine = self._engine("detdec", export)
                out = self._run_all(engine, feeds)
                self.calls["text decoder"][0] += 1
                return out["hs"], out["boxes"], out["presence"], out["presence_feats"]
            except Exception as e:
                self._fail("text decoder", e)
        self.calls["text decoder"][1] += 1
        return self.det_decoder_forward(*a, **kw)

    def _mask_head(self, backbone_feats, obj_queries, image_ids, encoder_hidden_states=None, prompt=None,
                   prompt_mask=None, **kw):
        n = DET_SIDE * DET_SIDE
        f = backbone_feats
        ok = ("mask head" not in self.failed and not kw and self.mask_head.presence_head is None
              and isinstance(f, (list, tuple)) and len(f) == 3 and all(torch.is_tensor(x) and x.is_cuda for x in f)
              and f[0].shape[0] == 1 and tuple(f[2].shape[-2:]) == (DET_SIDE, DET_SIDE)
              and torch.is_tensor(obj_queries) and obj_queries.dim() == 4 and obj_queries.shape[1:3] == (1, DET_QUERIES)
              and torch.is_tensor(encoder_hidden_states) and tuple(encoder_hidden_states.shape[:2]) == (n, 1)
              and _tokens_ok(prompt, prompt_mask))
        if ok:
            names = ["f0", "f1", "f2", "queries", "enc", "prompt", "prompt_mask"]
            feeds = dict(zip(names, (f[0], f[1], f[2], obj_queries, encoder_hidden_states, prompt, prompt_mask)))
            try:
                def export(onnx_path):
                    with _no_attn_weights(self.mask_head), torch.no_grad(), _autocast_now():
                        torch.onnx.export(_MaskHeadExport(self.mask_head, self.mask_head_forward).eval(),
                                          tuple(feeds[k] for k in names), str(onnx_path), input_names=names,
                                          output_names=["pred_masks", "semantic_seg"],
                                          opset_version=17, do_constant_folding=True, dynamo=False)
                engine = self._engine("maskhead", export)
                out = self._run_all(engine, feeds)
                self.calls["mask head"][0] += 1
                return {"pred_masks": out["pred_masks"], "semantic_seg": out["semantic_seg"], "presence_logit": None}
            except Exception as e:
                self._fail("mask head", e)
        self.calls["mask head"][1] += 1
        return self.mask_head_forward(backbone_feats, obj_queries, image_ids, encoder_hidden_states=encoder_hidden_states,
                                      prompt=prompt, prompt_mask=prompt_mask, **kw)

    def summary(self) -> str:
        return "; ".join(f"{k} {v[0]} TensorRT / {v[1]} PyTorch" for k, v in self.calls.items())

    def release(self):
        self.engines.clear()
        self.scratch.release()


def enable(model):
    """TensorRT for SAM 3's trunk and memory attention, or None (logged) where it can't run."""
    why = unavailable_reason()
    if why:
        logger.info("SAM 3 runs without TensorRT: %s.", why)
        return None
    try:
        return Sam3TensorRT(model)
    except Exception as e:
        logger.warning("SAM 3 runs without TensorRT (%s: %s).", type(e).__name__, e)
        return None
