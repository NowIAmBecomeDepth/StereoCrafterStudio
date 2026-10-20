"""subtitle_mask_adjust.py -- Splatting's own grow / soften for the Detect Text
mask (user, 2026-10-02: "the text needs its own adjustments", apart from the
depth map's Dilate / Blur).

Applied to the mask frame at its own resolution -- the source clip's -- so the
values are in source pixels and the preview (any size) matches the render.
The render (render_processor, step 3.6) and the preview (preview_engine's
_preview_submask_frame) both call this, before the mask is resized and pins
the text to the screen plane.

clean_depth is Text Clean (user, 2026-10-02): DepthCrafter's own copy of the
burned-in text -- often bigger than, or off from, the corrected mask -- sticks
out of the depth map, Dilate spreads it, and the pin only covers the inside of
the mask, so its rim warped past the mask's edge. Before Dilate / Blur, the
depth under the mask (grown by the Clean distance) is replaced with the
background depth around it; the pin then still puts the text on the screen
plane, and the only depth step left is at the mask's own edge.

Both run on the GPU when there is one (user, 2026-10-04): the same kernels
(OpenCV's ellipse, its Gaussian sizes -- core.common.gpu_ops), in float. On
the CPU Text Clean took ~2 s a 1080p frame of full-screen credits. Any GPU
failure falls back to the OpenCV code below.
"""

from __future__ import annotations

import logging

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def _on_gpu() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


def is_active(grow: float, soften: float) -> bool:
    return int(round(float(grow or 0))) != 0 or float(soften or 0) > 0


def adjust_mask(mask: np.ndarray, grow: float = 0.0, soften: float = 0.0) -> np.ndarray:
    """An 8-bit mask, (H, W) or a batch (N, H, W), grown (dilated) by `grow`
    pixels -- shrunk (eroded) when negative -- then softened by a Gaussian of
    sigma `soften` pixels. 0 / 0 returns it untouched."""
    if not is_active(grow, soften):
        return mask
    # small ones are quicker on the CPU than the copy to the GPU and back
    # (measured 2026-10-04: Grow 6 + Soften 3 even, Grow 20 + Soften 6 3x)
    if (abs(int(round(float(grow)))) >= 8 or float(soften or 0) >= 5) and _on_gpu():
        try:
            return _adjust_mask_gpu(mask, grow, soften)
        except Exception as e:
            logger.warning(f"Text Grow / Soften on the GPU failed ({e}); using the CPU.")
    if mask.ndim == 3:
        return np.stack([adjust_mask(m, grow, soften) for m in mask])
    m = np.ascontiguousarray(mask)
    g = int(round(float(grow)))
    if g != 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * abs(g) + 1, 2 * abs(g) + 1))
        m = cv2.dilate(m, k) if g > 0 else cv2.erode(m, k)
    s = float(soften or 0)
    if s > 0:
        m = cv2.GaussianBlur(m, (0, 0), s)
    return m


def _adjust_mask_gpu(mask: np.ndarray, grow: float, soften: float) -> np.ndarray:
    import torch
    from core.common import gpu_ops
    x = torch.from_numpy(np.ascontiguousarray(mask)).to("cuda", torch.float32)
    x = x.reshape(-1, 1, x.shape[-2], x.shape[-1])
    g = int(round(float(grow)))
    if g > 0:
        x = gpu_ops.dilate_ellipse(x, g)
    elif g < 0:
        x = gpu_ops.erode_ellipse(x, -g)
    s = float(soften or 0)
    if s > 0:
        x = gpu_ops.gaussian_blur(x, s, eight_bit=True)
    out = x.round_().clamp_(0, 255).to(torch.uint8).cpu().numpy()
    return out.reshape(mask.shape)


#: Mask values above this count as text for Text Clean (soft edges included).
CLEAN_THRESH = 0.02


def clean_depth(depth: np.ndarray, mask: np.ndarray, radius: float) -> np.ndarray:
    """Text Clean on one frame: `depth` (H, W) or (H, W, 1) float, `mask`
    (H, W) float 0..1 at the same size, `radius` in the depth's pixels. The
    depth under the mask, grown by `radius`, is filled from the depth around
    it (a normalised Gaussian fill, wider until every pixel is reached). A
    radius <= 0, or no mask, returns `depth` untouched."""
    r = float(radius or 0)
    if r <= 0 or mask is None:
        return depth
    region = (mask > CLEAN_THRESH).astype(np.uint8)
    if not region.any():
        return depth
    if _on_gpu():
        try:
            return _clean_depth_gpu(depth, region, r)
        except Exception as e:
            logger.warning(f"Text Clean on the GPU failed ({e}); using the CPU.")
    ri = max(1, int(round(r)))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * ri + 1, 2 * ri + 1))
    region = cv2.dilate(region, k)
    h, w = region.shape
    ys, xs = np.nonzero(region)
    pad = max(4 * ri, 32)                  # enough background around it to fill from
    y0, y1 = max(0, ys.min() - pad), min(h, ys.max() + pad + 1)
    x0, x1 = max(0, xs.min() - pad), min(w, xs.max() + pad + 1)

    squeeze = depth.ndim == 3
    d2 = depth[..., 0] if squeeze else depth
    crop = d2[y0:y1, x0:x1].astype(np.float32)
    hole = region[y0:y1, x0:x1].astype(bool)
    keep = (~hole).astype(np.float32)
    out = crop.copy()
    if keep.sum() == 0:
        out[hole] = float(np.median(d2[region == 0])) if (region == 0).any() else float(crop.mean())
    else:
        todo = hole.copy()
        sigma = max(float(ri), 2.0)
        limit = 2.0 * max(crop.shape)
        while todo.any():
            num = cv2.GaussianBlur(crop * keep, (0, 0), sigma)
            den = cv2.GaussianBlur(keep, (0, 0), sigma)
            ok = todo & (den > 1e-3)
            out[ok] = num[ok] / den[ok]
            todo &= ~ok
            if sigma > limit:
                out[todo] = float((crop * keep).sum() / keep.sum())
                break
            sigma *= 2.0
    res = np.array(depth, copy=True)
    if squeeze:
        res[y0:y1, x0:x1, 0] = out.astype(res.dtype)
    else:
        res[y0:y1, x0:x1] = out.astype(res.dtype)
    return res


def _clean_depth_gpu(depth: np.ndarray, region: np.ndarray, r: float) -> np.ndarray:
    """clean_depth's fill on the GPU: the same steps (the region grown by the
    ellipse, cropped with room around it, a normalised Gaussian fill widened
    until every pixel is reached)."""
    import torch
    from core.common import gpu_ops
    ri = max(1, int(round(r)))
    reg = torch.from_numpy(region).to("cuda", torch.float32)[None, None]
    reg = gpu_ops.dilate_ellipse(reg, ri)[0, 0] > 0.5
    h, w = reg.shape
    ys = torch.nonzero(reg.any(1)).flatten()
    xs = torch.nonzero(reg.any(0)).flatten()
    pad = max(4 * ri, 32)
    y0, y1 = max(0, int(ys[0]) - pad), min(h, int(ys[-1]) + pad + 1)
    x0, x1 = max(0, int(xs[0]) - pad), min(w, int(xs[-1]) + pad + 1)

    squeeze = depth.ndim == 3
    d2 = depth[..., 0] if squeeze else depth
    crop = torch.from_numpy(np.ascontiguousarray(d2[y0:y1, x0:x1], dtype=np.float32)).to("cuda")
    hole = reg[y0:y1, x0:x1]
    keep = (~hole).to(torch.float32)
    out = crop.clone()
    if float(keep.sum()) == 0:
        whole = torch.from_numpy(np.ascontiguousarray(d2, dtype=np.float32)).to("cuda")
        outside = whole[~reg]
        out[hole] = outside.median() if outside.numel() else crop.mean()
    else:
        todo = hole.clone()
        sigma = max(float(ri), 2.0)
        limit = 2.0 * max(crop.shape)
        ck = (crop * keep)[None, None]
        k4 = keep[None, None]
        while bool(todo.any()):
            num = gpu_ops.gaussian_blur(ck, sigma)[0, 0]
            den = gpu_ops.gaussian_blur(k4, sigma)[0, 0]
            ok = todo & (den > 1e-3)
            out[ok] = num[ok] / den[ok]
            todo &= ~ok
            if sigma > limit:
                out[todo] = (crop * keep).sum() / keep.sum()
                break
            sigma *= 2.0
    res = np.array(depth, copy=True)
    vals = out.cpu().numpy().astype(res.dtype)
    if squeeze:
        res[y0:y1, x0:x1, 0] = vals
    else:
        res[y0:y1, x0:x1] = vals
    return res
