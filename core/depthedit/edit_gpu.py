"""edit_gpu.py -- edit_ops.apply_all on the GPU, for a big frame (user, 2026-10-31: the render back to
Splatting "feels slower"; a 1920x1038 frame with four layers took ~295 ms on the CPU, one thread).

The same steps as edit_ops, in the same order, on CUDA tensors: the frame goes up once, each layer's
mask goes up, the result comes down once. The blur, the guided filter (Edge snap) and the ellipse
dilate / erode are core.common.gpu_ops's -- OpenCV's kernels and borders, the ones edit_ops itself
uses for a big frame -- so the result is the CPU's to float rounding. What isn't here runs edit_ops's
own code for that one step (the frame down and back up): Erase, Despeck, Level to edge, Fill holes,
Close gaps, and the Global Despeck / Local Match. Nothing is kept between frames.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Optional, Sequence, Tuple

import threading

import numpy as np
import torch
import torch.nn.functional as F

from core.common import gpu_ops
from core.depthedit import edit_ops as E

DEV = torch.device("cuda")


def _up(a: np.ndarray, dtype=torch.float32) -> torch.Tensor:
    return torch.from_numpy(np.ascontiguousarray(a)).to(DEV, non_blocking=False).to(dtype)


def _down(t: torch.Tensor) -> np.ndarray:
    return t.float().cpu().numpy()


def _4d(t: torch.Tensor) -> torch.Tensor:
    return t[None, None]


def _gaussian(a: torch.Tensor, sigma: float) -> torch.Tensor:
    return gpu_ops.gaussian_blur(_4d(a), float(sigma))[0, 0]


# ---- masks (float 0/1 tensors) ----------------------------------------------------------------

def _snap(mask: torch.Tensor, guide: Optional[torch.Tensor], radius: int) -> torch.Tensor:
    if radius <= 0 or guide is None or not bool(mask.any()):
        return mask
    g = guide.clamp(0.0, 1.0)
    a = gpu_ops.guided_filter(_4d(g), _4d(mask), int(radius), E.EDGE_SNAP_EPS)[0, 0]
    return (a >= 0.5).float()


def _shape(snapped: torch.Tensor, s: E.LayerSettings) -> torch.Tensor:
    """edit_ops.shape_mask on a snapped mask. Close gaps / Fill holes: edit_ops's own."""
    m = snapped
    if (int(round(s.close_gaps)) > 0 or s.fill_holes) and bool(m.any()):
        m = _up(E.shape_mask(_down(m) > 0.5, replace(s, grow=0.0)).astype(np.float32))
    grow = E._split_px(s.grow)[0]
    if grow > 0:
        m = gpu_ops.dilate_ellipse(_4d(m), grow)[0, 0]
    elif grow < 0:
        m = gpu_ops.erode_ellipse(_4d(m), -grow)[0, 0]
    return m


def _line_max(v: torch.Tensor, n: int, side: str, inward: bool, replicate: bool) -> torch.Tensor:
    """cv2.dilate with edit_ops._line(n, side) anchored at edit_ops._anchor(n, side, not inward):
    each pixel the max of itself and the n pixels one way -- `replicate` for BORDER_REPLICATE,
    else outside counts as nothing (dilate's default border)."""
    vertical = side in ("top", "bottom")
    # which way the window reaches: _anchor's "first" means it starts at the pixel (looks down /
    # right); else it ends there (looks up / left)
    first = (side in ("top", "left")) != (not inward)
    x = _4d(v)
    if vertical:
        pad = (0, 0, 0, n) if first else (0, 0, n, 0)
        k = (n + 1, 1)
    else:
        pad = (0, n, 0, 0) if first else (n, 0, 0, 0)
        k = (1, n + 1)
    if replicate:
        x = F.pad(x, pad, mode="replicate")
    else:
        x = F.pad(x, pad, value=float("-inf"))
    return F.max_pool2d(x, kernel_size=k, stride=1)[0, 0]


def _edge_shift_whole(v: torch.Tensor, px: int, side: str) -> torch.Tensor:
    n = abs(int(px))
    if n == 0:
        return v
    if px < 0:      # pull in: erode looking outward
        return -_line_max(-v, n, side, inward=False, replicate=True)
    return _line_max(v, n, side, inward=True, replicate=True)


def _edge_shift(v: torch.Tensor, px: float, side: str) -> torch.Tensor:
    whole, frac = E._split_px(px)
    sign = -1 if float(px) < 0 else 1
    lo = _edge_shift_whole(v, whole, side)
    if frac == 0.0:
        return lo
    hi = _edge_shift_whole(v, whole + sign, side)
    return lo + (hi - lo) * float(np.float32(frac))


def _edge_moves(x: torch.Tensor, s: E.LayerSettings) -> torch.Tensor:
    for name, side in E.EDGE_SIDES:
        px = getattr(s, name)
        if px:
            x = _edge_shift(x, px, side)
    return x


def _edge_region(m: torch.Tensor, s: E.LayerSettings) -> torch.Tensor:
    r = m
    for name, side in E.EDGE_SIDES:
        px = getattr(s, name)
        if px > 0:
            r = _line_max(r, int(np.ceil(px)), side, inward=True, replicate=False)
    return r


def _feather(m: torch.Tensor, feather: float) -> torch.Tensor:
    return _gaussian(m, max(feather / 2.0, 0.5)) if feather > 0 else m


def _masked_blur(v: torch.Tensor, m: torch.Tensor, sigma: float) -> torch.Tensor:
    num = _gaussian(v * m, sigma)
    den = _gaussian(m, sigma)
    return torch.where(den > 1e-4, num / den.clamp_min(1e-4), v)


def _bbox(m: torch.Tensor) -> Optional[Tuple[int, int, int, int]]:
    b = m > 0.5
    ys = torch.nonzero(b.any(dim=1)).flatten()
    if ys.numel() == 0:
        return None
    xs = torch.nonzero(b.any(dim=0)).flatten()
    return int(xs[0]), int(ys[0]), int(xs[-1]) + 1, int(ys[-1]) + 1


_Q = None


def _stats(v: torch.Tensor, m: torch.Tensor) -> Optional[Tuple[float, float, float]]:
    """edit_ops.object_stats: (p5, median, p95) under the mask, sampled as it samples."""
    global _Q
    vals = v[m > 0.5]
    if vals.numel() == 0:
        return None
    if vals.numel() > 200_000:
        vals = vals[:: vals.numel() // 200_000 + 1]
    if _Q is None:
        _Q = torch.tensor([0.05, 0.5, 0.95], device=DEV, dtype=torch.float64)
    q = torch.quantile(vals.double(), _Q).tolist()
    return q[0], q[1], q[2]


def _gradient_field(g: E.Gradient, w: int, h: int, bbox) -> torch.Tensor:
    (ax, ay), (bx, by) = E.gradient_endpoints(g, w, h, bbox)
    xs = torch.arange(w, device=DEV, dtype=torch.float32)[None, :] + 0.5
    ys = torch.arange(h, device=DEV, dtype=torch.float32)[:, None] + 0.5
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq < 1e-6:
        return torch.zeros((h, w), device=DEV)
    if g.shape == "Radial":
        t = torch.sqrt((xs - ax) ** 2 + (ys - ay) ** 2) / float(np.sqrt(length_sq))
    else:
        t = ((xs - ax) * dx + (ys - ay) * dy) / length_sq
    t = t.clamp(0.0, 1.0)
    if g.curve != 1.0:
        t = torch.pow(t, max(g.curve, 1e-3))
    return t


def _apply_gradient(x: torch.Tensor, m: torch.Tensor, g: E.Gradient, bbox) -> torch.Tensor:
    h, w = x.shape
    ramp = g.near + (g.far - g.near) * _gradient_field(g, w, h, bbox)
    if g.keep_detail > 0:
        size = max((bbox[2] - bbox[0]) if bbox else w, (bbox[3] - bbox[1]) if bbox else h)
        base = _masked_blur(x, m, sigma=max(size / 12.0, 2.0))
        ramp = ramp + g.keep_detail * (x - base)
    if g.blend == "Only nearer":
        return torch.maximum(x, ramp)
    if g.blend == "Only further":
        return torch.minimum(x, ramp)
    if g.blend == "Add":
        return x + (ramp - (g.near + g.far) * 0.5)
    return ramp


# ---- layers ------------------------------------------------------------------------------------

def _weight(snapped, m, s, alter: bool):
    """The layer's blend weight before Feather: the mask (Alter: stretched past an edge pushed
    out), and the fraction of a pixel of Grow blended in from the next whole pixel's mask."""
    weight = _edge_region(m, s) if alter and E.has_edge_moves(s) else m
    whole, frac = E._split_px(s.grow)
    if frac > 0.0:
        step = replace(s, grow=float(whole + (-1 if s.grow < 0 else 1)))
        m2 = _shape(snapped, step)
        r2 = _edge_region(m2, s) if alter and E.has_edge_moves(s) else m2
        weight = weight + (r2 - weight) * float(np.float32(frac))
    return weight


def _alter(v, mask, s, guide):
    snapped = _snap(mask, guide, s.edge_snap) if s.edge_snap > 0 and guide is not None else mask
    m = _shape(snapped, s)
    if not bool(m.any()):
        return v
    st = _stats(v, m)
    med = st[1] if st else 0.5
    x = v
    if E.has_edge_moves(s):
        x = _edge_moves(x, s)
    if s.smooth > 0:
        x = _masked_blur(x, m, float(s.smooth))
    if s.contrast != 1.0:
        x = med + (x - med) * s.contrast
    if s.flatten > 0:
        x = x + (med - x) * s.flatten
    if s.brightness != 0.0:
        x = x + s.brightness
    if s.gradient.enabled:
        x = _apply_gradient(x, m, s.gradient, _bbox(m))
    x = x.clamp(0.0, 1.0)
    alpha = _feather(_weight(snapped, m, s, alter=True), s.feather) * float(s.opacity)
    return v + (x - v) * alpha


def _add(v, mask, s):
    s = replace(s, edge_snap=0)
    m = _shape(mask, s)
    if not bool(m.any()):
        return v
    x = torch.full(v.shape, float(s.add_depth) + float(s.brightness), device=DEV)
    if s.gradient.enabled:
        x = _apply_gradient(x, m, s.gradient, _bbox(m))
    if s.smooth > 0 or s.contrast != 1.0 or s.flatten > 0:
        if s.smooth > 0:
            x = _masked_blur(x, m, float(s.smooth))
        med = float(torch.quantile(x[m > 0.5].double(), 0.5))
        if s.contrast != 1.0:
            x = med + (x - med) * s.contrast
        if s.flatten > 0:
            x = x + (med - x) * s.flatten
    x = x.clamp(0.0, 1.0)
    weight = _weight(mask, m, s, alter=False)
    if E.has_edge_moves(s):
        weight = _edge_moves(weight, s)
    alpha = _feather(weight, s.feather) * float(s.opacity)
    return v + (x - v) * alpha


def _on_cpu(s: E.LayerSettings) -> bool:
    return s.mode == "Erase" or E.has_despeck(s) or E.has_level_edge(s)


def _global(v: torch.Tensor, v_np: np.ndarray, g: E.GlobalSettings) -> torch.Tensor:
    if g.is_identity():
        return v
    if g.has_match() or g.has_despeck():
        return _up(E.apply_global(v_np, g))
    x = 1.0 - v if g.invert else v
    span = max(g.in_white - g.in_black, 1e-4)
    x = (x - g.in_black) / span
    if g.gamma != 1.0:
        x = torch.pow(x.clamp(0.0, 1.0), 1.0 / max(g.gamma, 1e-3))
    x = (x - 0.5) * g.contrast + 0.5 + g.brightness
    return x.clamp(0.0, 1.0)


#: PyTorch's cached blocks handed back this long after the last frame edited (a render, a preview
#: redraw): nothing held between them (user, 2026-10-15: everything releases its GPU).
IDLE_RELEASE_S = 2.0
_release_timer: Optional[threading.Timer] = None
_release_lock = threading.Lock()


def _release() -> None:
    try:
        torch.cuda.empty_cache()
    except Exception:
        pass


def _release_later() -> None:
    global _release_timer
    with _release_lock:
        if _release_timer is not None:
            _release_timer.cancel()
        _release_timer = threading.Timer(IDLE_RELEASE_S, _release)
        _release_timer.daemon = True
        _release_timer.start()


def apply_all(v: np.ndarray, global_settings: E.GlobalSettings,
              layers: Sequence[Tuple[E.LayerSettings, Optional[np.ndarray]]],
              frame_idx: int) -> np.ndarray:
    """edit_ops.apply_all, on the GPU."""
    try:
        return _apply_all(v, global_settings, layers, frame_idx)
    finally:
        _release_later()


@torch.inference_mode()
def _apply_all(v: np.ndarray, global_settings: E.GlobalSettings,
              layers: Sequence[Tuple[E.LayerSettings, Optional[np.ndarray]]],
              frame_idx: int) -> np.ndarray:
    v_np = np.asarray(v, dtype=np.float32)
    guide = _up(v_np)
    x = _global(guide, v_np, global_settings)
    for s, mask in layers:
        if not E.layer_active(s, frame_idx) or mask is None or s.opacity <= 0:
            continue
        if _on_cpu(s):
            x = _up(E.apply_layer(_down(x), mask, s, guide=v_np))
            continue
        m = _up(np.asarray(mask), torch.uint8).float()
        if m.shape != x.shape:
            raise ValueError(f"mask {tuple(m.shape)} for a {tuple(x.shape)} frame")
        x = _add(x, m, s) if s.mode == "Add" else _alter(x, m, s, guide)
    return _down(x)
