"""auto_estimate.py -- Auto 3D: each clip's Disparity from how close its shot is (2026-11-07).

Rowan3D's Auto 3D Strength rule from iw3 (nt_autostrength), ported from Thomas's patch ("StereoCrafter
Studio: Auto 3D Patch", 2026-10-31). A wide landscape gets less 3D, a close-up more:

  1. every EVERY-th frame of the clip, short side 224 (bicubic, antialiased), three 224x224 crops
     along the long side (start, middle, end), CLIP's mean / std;
  2. CLIP ViT-B/32's image encoder (open_clip ViT-B-32-quickgelu, laion400m_e32) embeds them, the three
     averaged; zero-shot against six shot scales (auto3d_prompts.json: each class's prompts encoded by the
     same model's text encoder): p = softmax(temperature * e . text);
  3. closeness = calib[0] * (p . values) + calib[1], clamped 0..1, the clip's the mean of its frames;
  4. Disparity = min + (max - min) * closeness ** gamma, gamma putting closeness 0.5 at Mid.

Without the model it falls back to a rough depth-only estimate. Studio's Mid is its own box (user,
2026-11-07): the Disparity dial loads each clip's own sidecar value, so it can't be the middle.

The weights: <models>/AutoStrength/ -- open_clip's own checkpoint (the Models window downloads it), or the
patch's fp16 image half (clip_vitb32_laion400m_visual_fp16.pth). studio/tools/build_auto3d_prompts.py
made the prompts file.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import torch  # before decord: decord first crashes the process on import (Windows)
import torch.nn as nn
import torch.nn.functional as F

logger = logging.getLogger(__name__)

FOLDER = "AutoStrength"
CHECKPOINT = "vit_b_32-quickgelu-laion400m_e32-46683a32.pt"       # open_clip's release, 605 MB
VISUAL_FP16 = "clip_vitb32_laion400m_visual_fp16.pth"             # the patch's image half, 176 MB
PROMPTS = Path(__file__).with_name("auto3d_prompts.json")
EVERY = 6                 # every 6th frame
BATCH = 8                 # frames per model call
SIZE = 224
MEAN = (0.48145466, 0.4578275, 0.40821073)
STD = (0.26862954, 0.26130258, 0.27577711)
MODEL_NAME = "clip-vitb32-laion400m"


# ---- CLIP ViT-B/32, open_clip's names --------------------------------------------------------

class QuickGELU(nn.Module):
    def forward(self, x):
        return x * torch.sigmoid(1.702 * x)


class _Attention(nn.Module):
    """nn.MultiheadAttention's weights (in_proj, out_proj), batch first, through SDPA."""

    def __init__(self, width: int, heads: int):
        super().__init__()
        self.heads = heads
        self.in_proj_weight = nn.Parameter(torch.empty(3 * width, width))
        self.in_proj_bias = nn.Parameter(torch.empty(3 * width))
        self.out_proj = nn.Linear(width, width)

    def forward(self, x, mask=None):
        b, n, d = x.shape
        q, k, v = F.linear(x, self.in_proj_weight, self.in_proj_bias).chunk(3, dim=-1)
        q, k, v = (t.view(b, n, self.heads, d // self.heads).transpose(1, 2) for t in (q, k, v))
        o = F.scaled_dot_product_attention(q, k, v, attn_mask=mask)
        return self.out_proj(o.transpose(1, 2).reshape(b, n, d))


class _Block(nn.Module):
    def __init__(self, width: int, heads: int):
        super().__init__()
        self.ln_1 = nn.LayerNorm(width)
        self.attn = _Attention(width, heads)
        self.ln_2 = nn.LayerNorm(width)
        self.mlp = nn.Sequential(OrderedDict(c_fc=nn.Linear(width, 4 * width), gelu=QuickGELU(),
                                             c_proj=nn.Linear(4 * width, width)))

    def forward(self, x, mask=None):
        x = x + self.attn(self.ln_1(x), mask)
        return x + self.mlp(self.ln_2(x))


class _Transformer(nn.Module):
    def __init__(self, width: int, layers: int, heads: int):
        super().__init__()
        self.resblocks = nn.ModuleList(_Block(width, heads) for _ in range(layers))

    def forward(self, x, mask=None):
        for blk in self.resblocks:
            x = blk(x, mask)
        return x


class ClipVisual(nn.Module):
    """CLIP ViT-B/32's image encoder: open_clip's "visual.*" weights, the prefix taken off."""

    def __init__(self, width: int = 768, layers: int = 12, heads: int = 12, patch: int = 32,
                 size: int = SIZE, out: int = 512):
        super().__init__()
        self.conv1 = nn.Conv2d(3, width, patch, patch, bias=False)
        scale = width ** -0.5
        self.class_embedding = nn.Parameter(scale * torch.randn(width))
        self.positional_embedding = nn.Parameter(scale * torch.randn((size // patch) ** 2 + 1, width))
        self.ln_pre = nn.LayerNorm(width)
        self.transformer = _Transformer(width, layers, heads)
        self.ln_post = nn.LayerNorm(width)
        self.proj = nn.Parameter(scale * torch.randn(width, out))

    def forward(self, x):
        x = self.conv1(x).flatten(2).transpose(1, 2)                       # (B, 49, width)
        cls = self.class_embedding.to(x.dtype).expand(x.shape[0], 1, -1)
        x = torch.cat([cls, x], dim=1) + self.positional_embedding.to(x.dtype)
        x = self.transformer(self.ln_pre(x))
        return self.ln_post(x[:, 0]) @ self.proj


def _state_dict(obj) -> Dict[str, torch.Tensor]:
    """A checkpoint's tensors, whichever way it was saved ({"state_dict": ...}, "module." prefixes)."""
    sd = obj.get("state_dict", obj) if isinstance(obj, dict) else obj
    return {(k[7:] if k.startswith("module.") else k): v for k, v in sd.items()}


def visual_weights(path: str) -> Dict[str, torch.Tensor]:
    """The image encoder's weights from `path`: open_clip's whole checkpoint (its visual.* keys), or the
    image half alone."""
    sd = _state_dict(torch.load(path, map_location="cpu", weights_only=True))
    if any(k.startswith("visual.") for k in sd):
        sd = {k[7:]: v for k, v in sd.items() if k.startswith("visual.")}
    return sd


# ---- the shared model ------------------------------------------------------------------------

def weights_file() -> Optional[str]:
    """The image encoder's file in the models folder, if there is one: the fp16 image half first."""
    try:
        from core import weights_paths
        roots = weights_paths.roots()
    except Exception:
        roots = []
    for base in roots:
        for name in (VISUAL_FP16, CHECKPOINT):
            p = Path(base) / FOLDER / name
            if p.is_file():
                return str(p)
    return None


def load_prompts(path: Path = PROMPTS) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    text = np.asarray(d["text"], np.float32)
    if text.ndim != 2 or text.shape[1] != 512 or len(d["values"]) != text.shape[0]:
        raise ValueError(f"{os.path.basename(str(path))}: text {text.shape}, {len(d['values'])} values")
    return {"text": text, "values": np.asarray(d["values"], np.float32), "temperature": float(d["temperature"]),
            "calib": [float(c) for c in d["calib"]], "views": str(d.get("views", "3crop"))}


class AutoStrength:
    """CLIP and the shot-scale classes, loaded once and shared by the batch's clips (behind a lock)."""

    _shared: Optional["AutoStrength"] = None
    _failed: Optional[str] = None
    _lock = threading.Lock()

    def __init__(self, weights: str, prompts: Path = PROMPTS, device: Optional[str] = None):
        self.device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
        self.dtype = torch.float16 if self.device.type == "cuda" else torch.float32
        p = load_prompts(prompts)
        model = ClipVisual()
        model.load_state_dict({k: v.float() for k, v in visual_weights(weights).items()})
        self.model = model.to(self.device, self.dtype).eval()
        self.text = torch.from_numpy(p["text"]).to(self.device, torch.float32)
        self.values = torch.from_numpy(p["values"]).to(self.device, torch.float32)
        self.temperature, self.calib = p["temperature"], p["calib"]
        self.mean = torch.tensor(MEAN, device=self.device).view(1, 3, 1, 1)
        self.std = torch.tensor(STD, device=self.device).view(1, 3, 1, 1)
        self.weights = weights

    @classmethod
    def get(cls) -> Optional["AutoStrength"]:
        """The shared one, loaded on first use; None (said once) when it can't be."""
        with cls._lock:
            if cls._shared is None and cls._failed is None:
                path = weights_file()
                if path is None:
                    cls._failed = f"no CLIP weights in <models>/{FOLDER}"
                else:
                    try:
                        cls._shared = cls(path)
                    except Exception as e:
                        cls._failed = f"{type(e).__name__}: {e}"
                        logger.warning("Auto 3D: CLIP didn't load (%s)", cls._failed, exc_info=True)
            return cls._shared

    @classmethod
    def release(cls) -> None:
        """Let CLIP go (the end of a batch) and look for its weights again next time."""
        with cls._lock:
            cls._shared, cls._failed = None, None
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    @torch.inference_mode()
    def closeness(self, rgb: torch.Tensor) -> torch.Tensor:
        """(B,) closeness 0..1 of frames `rgb` (B, 3, H, W) 0..1."""
        x = three_views(rgb.to(self.device, torch.float32))           # (B*3, 3, 224, 224)
        x = ((x - self.mean) / self.std).to(self.dtype)
        e = F.normalize(self.model(x).float(), dim=-1).view(rgb.shape[0], 3, -1).mean(1)
        e = F.normalize(e, dim=-1)
        p = torch.softmax(self.temperature * e @ self.text.T, dim=-1)
        return (self.calib[0] * (p @ self.values) + self.calib[1]).clamp(0.0, 1.0)


def three_views(x: torch.Tensor) -> torch.Tensor:
    """(B*3, 3, 224, 224): short side to 224 (bicubic, antialiased), three crops along the long side --
    start, middle, end -- frame by frame."""
    b, _c, h, w = x.shape
    s = SIZE / float(min(h, w))
    nh, nw = max(SIZE, int(round(h * s))), max(SIZE, int(round(w * s)))
    x = F.interpolate(x, size=(nh, nw), mode="bicubic", antialias=True, align_corners=False).clamp(0.0, 1.0)
    if nw >= nh:
        crops = [x[:, :, :SIZE, o:o + SIZE] for o in (0, (nw - SIZE) // 2, nw - SIZE)]
    else:
        crops = [x[:, :, o:o + SIZE, :SIZE] for o in (0, (nh - SIZE) // 2, nh - SIZE)]
    return torch.stack(crops, dim=1).flatten(0, 1)


def depth_closeness(depth: np.ndarray) -> float:
    """The rough depth-only closeness of frames `depth` (N, H, W) 0..1 (near bright): clamp(0.958 * mean
    - 0.526 * (bottom third - top third) + 0.282, 0, 1), the frames' mean."""
    d = depth.astype(np.float32)
    h = d.shape[1]
    top, bottom = d[:, : h // 3].mean(axis=(1, 2)), d[:, h - h // 3:].mean(axis=(1, 2))
    c = 0.958 * d.mean(axis=(1, 2)) - 0.526 * (bottom - top) + 0.282
    return float(np.clip(c, 0.0, 1.0).mean())


def strength_curve(s: float, lo: float, mid: float, hi: float) -> float:
    """lo + (hi - lo) * s ** gamma, gamma putting s = 0.5 at `mid`."""
    t = min(0.98, max(0.02, (mid - lo) / (hi - lo)))
    gamma = math.log(t) / math.log(0.5)
    return lo + (hi - lo) * min(1.0, max(0.0, float(s))) ** gamma


# ---- one clip ---------------------------------------------------------------------------------

def _reader(path: str, short: int):
    """decord at about `short` px on the short side (the decode does the big step down)."""
    from decord import VideoReader, cpu
    probe = VideoReader(path, ctx=cpu(0))
    h, w = probe[0].shape[:2]
    n = len(probe)
    del probe
    if min(h, w) <= short:
        return VideoReader(path, ctx=cpu(0)), n
    s = short / float(min(h, w))
    return VideoReader(path, ctx=cpu(0), width=int(round(w * s / 2)) * 2, height=int(round(h * s / 2)) * 2), n


def auto_strength_for_clip(rgb_path: str, depth_path: Optional[str], lo: float, mid: float, hi: float,
                           every: int = EVERY, stop_event: Optional[threading.Event] = None,
                           model: Optional[AutoStrength] = None, use_model: bool = True) -> Optional[Dict]:
    """The clip's Auto 3D Disparity: {"closeness", "frames", "strength", "min", "max", "typical", "every",
    "model"} -- "model" the CLIP's name, or "depth" for the fallback. None when stopped or it failed
    (said in the log)."""
    name = os.path.basename(rgb_path)
    every = max(1, int(every))
    try:
        if model is None and use_model:
            model = AutoStrength.get()
        values: List[float] = []
        if model is not None:
            vr, n = _reader(rgb_path, 2 * SIZE)
            idx = list(range(0, n, every))
            for i in range(0, len(idx), BATCH):
                if stop_event is not None and stop_event.is_set():
                    return None
                frames = vr.get_batch(idx[i:i + BATCH]).asnumpy()           # (B, H, W, 3) uint8
                x = torch.from_numpy(frames).permute(0, 3, 1, 2).float().div_(255.0)
                values += model.closeness(x).cpu().tolist()
            used = MODEL_NAME
        else:
            if not depth_path:
                logger.warning(f"Auto 3D: {name}: no CLIP and no depth map to estimate from")
                return None
            logger.info(f"Auto 3D: no CLIP weights ({AutoStrength._failed or 'not loaded'}): "
                        f"using the rougher depth-only estimate")
            vr, n = _reader(depth_path, 256)
            idx = list(range(0, n, every))
            for i in range(0, len(idx), BATCH):
                if stop_event is not None and stop_event.is_set():
                    return None
                frames = vr.get_batch(idx[i:i + BATCH]).asnumpy()
                values.append(depth_closeness(frames[..., 0].astype(np.float32) / 255.0) * len(frames))
            values = [sum(values) / max(1, len(idx))] * len(idx)
            used = "depth"
        if not values:
            logger.warning(f"Auto 3D: {name}: no frames read")
            return None
        c = float(np.mean(values))
        strength = round(strength_curve(c, lo, mid, hi), 1)
        logger.info(f"Auto 3D: {name} closeness {c:.3f} over {len(values)} frames -> Disparity {strength} "
                    f"({lo:g}..{hi:g} around {mid:g})")
        return {"closeness": round(c, 4), "frames": len(values), "strength": strength, "min": float(lo),
                "max": float(hi), "typical": float(mid), "every": every, "model": used}
    except Exception as e:
        logger.warning(f"Auto 3D: {name}: estimating failed ({type(e).__name__}: {e})", exc_info=True)
        return None


def release() -> None:
    AutoStrength.release()
