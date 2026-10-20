"""M2S's warp on the GPU (StereoCrafter Studio, 2026-09-29).

renderer._build_m2s_numpy_outputs ran every frame through numpy on the CPU --
about 40x the SC warp's time. This is the same computation in torch, batched:

* scatter_image (vendored_m2s/warping.py) writes each source pixel to two
  target columns with numpy's ``a[idx] += v``. Duplicate indices do NOT add
  up there: the LAST write in row-major order wins. Here the winner of each
  target is the largest source index (``scatter_reduce(amax)``), then its
  value is gathered -- the same pixel numpy keeps, the same float32 products,
  added in the same order.
* the convergence shift (cv2.warpAffine, INTER_LINEAR, constant 0 border)
  moves every pixel by the same amount, quantised by OpenCV to 1/32 px. Its
  integer offset and two weights are read off OpenCV itself on a one-row
  probe, then applied to the batch; the hole mask's INTER_NEAREST shift
  likewise (border 255).

The result matches the numpy path to float32 rounding (see
studio/tests/test_m2s_gpu.py).
"""

from __future__ import annotations

from typing import Tuple

import cv2
import numpy as np
import torch


def _probe_shift(width: int, shift_px: float) -> Tuple[int, float, float, int]:
    """(offset, w0, w1, nearest_offset): OpenCV's warpAffine by shift_px along
    x, as dst[x] = src[x+offset]*w0 + src[x+offset+1]*w1 (INTER_LINEAR) and
    dst[x] = src[x+nearest_offset] (INTER_NEAREST)."""
    w = max(int(width), 8)
    ramp = (np.arange(w, dtype=np.float64) + 1.0).astype(np.float32)[None, :]
    m = np.array([[1.0, 0.0, float(shift_px)], [0.0, 1.0, 0.0]], dtype=np.float32)
    lin = cv2.warpAffine(ramp, m, (w, 1), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0.0)
    near = cv2.warpAffine(ramp, m, (w, 1), flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0.0)
    # an interior column: both taps inside the ramp
    for x in range(w):
        src = float(lin[0, x])
        if src > 1.5 and src < w - 0.5:
            t = src - (x + 1.0)
            off = int(np.floor(t + 1e-6))
            frac = round((t - off) * 32.0) / 32.0
            if frac >= 1.0:
                off, frac = off + 1, 0.0
            break
    else:
        # the shift pushes everything out of the frame
        off, frac = int(np.floor(-shift_px)), 0.0
    near_off = None
    for x in range(w):
        v = float(near[0, x])
        if v >= 1.0:
            near_off = int(round(v - (x + 1.0)))
            break
    if near_off is None:
        near_off = int(round(-shift_px))
    return off, float(np.float32(1.0 - frac)), float(np.float32(frac)), near_off


def _shift_linear(img: torch.Tensor, off: int, w0: float, w1: float) -> torch.Tensor:
    """img (B, H, W, C): dst[..., x, :] = img[x+off]*w0 + img[x+off+1]*w1, 0 outside."""
    b, h, w, c = img.shape
    pad = torch.zeros((b, h, 1, c), dtype=img.dtype, device=img.device)
    xs = torch.arange(w, device=img.device) + off

    def take(idx):
        inside = (idx >= 0) & (idx < w)
        out = img[:, :, idx.clamp(0, w - 1), :]
        return torch.where(inside[None, None, :, None], out, pad.expand(b, h, w, c))

    s0, s1 = take(xs), take(xs + 1)
    if w1 == 0.0:
        return s0 * w0
    return s0 * w0 + s1 * w1


def _shift_nearest(mask: torch.Tensor, off: int, border: float) -> torch.Tensor:
    """mask (B, H, W): dst[x] = mask[x+off], `border` outside."""
    b, h, w = mask.shape
    idx = torch.arange(w, device=mask.device) + off
    inside = (idx >= 0) & (idx < w)
    out = mask[:, :, idx.clamp(0, w - 1)]
    return torch.where(inside[None, None, :], out, torch.full_like(out, border))


def scatter_image_torch(frames: torch.Tensor, inverse_depth: torch.Tensor, scale_factor: float
                        ) -> Tuple[torch.Tensor, torch.Tensor]:
    """vendored scatter_image(direction=-1, inverse_ordering=False), batched.

    frames (B, H, W, C) float32, inverse_depth (B, H, W) float32. Returns
    (reprojected (B, H, W, C) float32, filled (B, H, W) bool)."""
    b, h, w, c = frames.shape
    dev = frames.device
    disp = (inverse_depth.to(torch.float32) * np.float32(scale_factor)).to(torch.float32)
    d_int = disp.to(torch.int32)                                  # astype(int32): toward zero
    w_plus1 = disp - d_int.to(torch.float32)
    d_int1 = (disp + 1.0).to(torch.int32)
    xs = torch.arange(w, device=dev, dtype=torch.int64).view(1, 1, w)
    tx0 = xs - d_int.to(torch.int64)                              # direction -1
    tx1 = xs - d_int1.to(torch.int64)

    plane = h * w
    base = (torch.arange(b, device=dev).view(b, 1, 1) * plane
            + torch.arange(h, device=dev).view(1, h, 1) * w)      # (B, H, 1)
    src = (base + xs).reshape(-1)                                 # source index, row-major = numpy's order
    flat = frames.reshape(b * plane, c)
    wflat = w_plus1.reshape(-1)

    def winners(tx):
        valid = ((tx >= 0) & (tx < w)).reshape(-1)
        tgt = (base + tx.clamp(0, w - 1)).reshape(-1)
        win = torch.full((b * plane,), -1, dtype=torch.int64, device=dev)
        win.scatter_reduce_(0, tgt[valid], src[valid], reduce="amax", include_self=True)
        return win

    img = torch.zeros((b * plane, c), dtype=torch.float32, device=dev)
    wt = torch.zeros((b * plane, c), dtype=torch.float32, device=dev)
    for tx, second in ((tx0, False), (tx1, True)):
        win = winners(tx)
        has = win >= 0
        s = win.clamp(min=0)
        wgt = wflat[s] if second else (1.0 - wflat[s])
        val = flat[s] * wgt[:, None]
        img = torch.where(has[:, None], img + val, img)
        wt = torch.where(has[:, None], wt + wgt[:, None], wt)
    nz = wt != 0
    img = torch.where(nz, img / torch.where(nz, wt, torch.ones_like(wt)), img)
    filled = nz[:, 0].reshape(b, h, w)
    return img.reshape(b, h, w, c), filled


def build_m2s_outputs_torch(left: torch.Tensor, depth: torch.Tensor, convergence_point: float,
                            max_disp_pixels: float) -> Tuple[torch.Tensor, torch.Tensor]:
    """renderer._build_m2s_numpy_outputs on the GPU.

    left (B, C, H, W), depth (B, 1, H, W). Returns (warped (B, C, H, W),
    hole mask (B, 1, H, W)), float32 on left's device."""
    if max_disp_pixels is None:
        raise ValueError("max_disp_pixels is required")
    scale_factor = float(max_disp_pixels) * 2.0
    shift_px = float(convergence_point) * scale_factor
    frames = left.detach().to(torch.float32).permute(0, 2, 3, 1).contiguous()
    # no ceiling: Boost, Pop-Out and Particles in front push depth past 1 on purpose, and
    # clamping it put all of them on one plane (bug sweep 2026-10-09; SC never clamped)
    d = torch.clamp(depth.detach().to(torch.float32)[:, 0], min=0.0)
    warped, filled = scatter_image_torch(frames, d, scale_factor)
    off, w0, w1, noff = _probe_shift(frames.shape[2], shift_px)
    warped = torch.clamp(_shift_linear(warped, off, w0, w1), 0.0, 1.0)
    hole = torch.where(filled, torch.zeros((), device=d.device), torch.full((), 255.0, device=d.device))
    hole = (_shift_nearest(hole, noff, 255.0) > 0).to(torch.float32)
    return warped.permute(0, 3, 1, 2).contiguous(), hole[:, None]
