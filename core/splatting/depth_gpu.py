"""depth_gpu.py -- Splatting's depth pre-processing on the GPU.

core.splatting.depth_processing.process_depth_batch_cpu does its dilate, blur and
dilate-left with OpenCV on the CPU (~60 ms a 1080p frame). This is the same
function step for step in torch. First used only while a preview slider was
dragged (fast_depth.py, 2026-09-25); since 2026-09-29 (user) it is what
process_depth_batch runs whenever CUDA is there -- renders, the preview and
the analysis alike, so the preview still matches the render. The two agree
to within one unit of the 16-bit intermediate (0.07% of pixels, measured).

What is kept the same as the CPU version:

* the 16-bit quantisation each OpenCV step does (clip to 0..65535, truncate
  to uint16) -- done on float tensors of whole numbers;
* the fractional kernels (a blend of the two odd sizes either side);
* cv2.dilate with a rectangle: a max filter whose border never wins
  (max_pool2d pads with -inf); erosion as the min filter;
* dilate-left: OpenCV's kernel anchored at (0, 0), i.e. the max over
  src[x .. x+k-1];
* GaussianBlur(sigma 0): cv2.getGaussianKernel's own coefficients,
  reflect-101 border (torch "reflect"), rounded back to uint16.

Hole Shift regions go through the CPU function unchanged.
"""

from __future__ import annotations

import math
from typing import Any, Optional

import cv2
import numpy as np
import torch
import torch.nn.functional as F

from core.splatting import depth_processing as _dp


def _q16(norm: torch.Tensor) -> torch.Tensor:
    """np.clip(x * 65535, 0, 65535).astype(np.uint16), as whole-number floats."""
    return torch.clamp(norm * 65535.0, 0.0, 65535.0).floor()


def _dilation_params(value: float):
    if value <= 1e-5:
        return 1, 1, 0.0
    if value < 3.0:
        return 1, 3, value / 3.0
    base = 3 + 2 * int((value - 3) // 2)
    return base, base + 2, (value - base) / 2.0


def _morph(img: torch.Tensor, k_w: int, k_h: int, erode: bool) -> torch.Tensor:
    if k_w <= 1 and k_h <= 1:
        return img
    src = -img if erode else img
    out = F.max_pool2d(src, kernel_size=(k_h, k_w), stride=1, padding=(k_h // 2, k_w // 2))
    return -out if erode else out


def gpu_dilate(t: torch.Tensor, kx_raw: float, ky_raw: float, max_value: float) -> torch.Tensor:
    kx_raw, ky_raw = float(kx_raw), float(ky_raw)
    if abs(kx_raw) <= 1e-5 and abs(ky_raw) <= 1e-5:
        return t
    if (kx_raw > 0 > ky_raw) or (kx_raw < 0 < ky_raw):
        return gpu_dilate(gpu_dilate(t, kx_raw, 0, max_value), 0, ky_raw, max_value)
    erode = kx_raw < 0 or ky_raw < 0
    kxl, kxh, tx = _dilation_params(abs(kx_raw))
    kyl, kyh, ty = _dilation_params(abs(ky_raw))
    m = max(max_value, 1e-5)
    src = _q16(t / m)
    xi, yi = tx <= 1e-4, ty <= 1e-4
    if xi and yi:
        out = _morph(src, kxl, kyl, erode)
    elif not xi and yi:
        out = (1.0 - tx) * _morph(src, kxl, kyl, erode) + tx * _morph(src, kxh, kyl, erode)
    elif xi and not yi:
        out = (1.0 - ty) * _morph(src, kxl, kyl, erode) + ty * _morph(src, kxl, kyh, erode)
    else:
        r11, r12 = _morph(src, kxl, kyl, erode), _morph(src, kxl, kyh, erode)
        r21, r22 = _morph(src, kxh, kyl, erode), _morph(src, kxh, kyh, erode)
        out = (1.0 - tx) * ((1.0 - ty) * r11 + ty * r12) + tx * ((1.0 - ty) * r21 + ty * r22)
    return (out / 65535.0) * m


def gpu_dilate_left(t: torch.Tensor, k_raw: float, max_value: float) -> torch.Tensor:
    k_raw = float(k_raw)
    if abs(k_raw) <= 1e-5:
        return t
    erode = k_raw < 0
    kwl, kwh, frac = _dilation_params(abs(k_raw))
    kl, kh = kwl // 2, kwh // 2
    if kl <= 0 and kh <= 0:
        return t
    m = max(float(max_value), 1e-5)
    src = _q16(t / m)

    def op(k_int: int) -> torch.Tensor:
        if k_int <= 0:
            return src
        k_w = k_int + 1
        s = -src if erode else src
        # anchor (0, 0): dst(x) = max(src[x .. x+k_w-1]); past the edge never wins
        padded = F.pad(s, (0, k_w - 1), value=float("-inf"))
        o = F.max_pool2d(padded, kernel_size=(1, k_w), stride=1)
        return -o if erode else o

    out = op(kl) if abs(frac) <= 1e-4 else (1.0 - frac) * op(kl) + frac * op(kh)
    out = torch.clamp(out, 0.0, 65535.0).floor()        # astype(uint16)
    return (out / 65535.0) * m


_KERNELS: dict = {}


def _gauss(k: int, device) -> torch.Tensor:
    key = (k, str(device))
    if key not in _KERNELS:
        _KERNELS[key] = torch.from_numpy(cv2.getGaussianKernel(k, 0).astype(np.float64).reshape(-1)).to(
            device=device, dtype=torch.float64)
    return _KERNELS[key]


def gpu_blur(t: torch.Tensor, kx: float, ky: float, max_value: float) -> torch.Tensor:
    k_x, k_y = int(kx), int(ky)
    if k_x <= 0 and k_y <= 0:
        return t
    k_x = k_x if k_x % 2 == 1 else k_x + 1
    k_y = k_y if k_y % 2 == 1 else k_y + 1
    m = max(max_value, 1e-5)
    img = _q16(t / m).double()
    if k_x > 1:
        g = _gauss(k_x, img.device).view(1, 1, 1, k_x)
        img = F.conv2d(F.pad(img, (k_x // 2, k_x // 2, 0, 0), mode="reflect"), g)
    if k_y > 1:
        g = _gauss(k_y, img.device).view(1, 1, k_y, 1)
        img = F.conv2d(F.pad(img, (0, 0, k_y // 2, k_y // 2), mode="reflect"), g)
    out = torch.clamp(torch.round(img), 0.0, 65535.0).float()
    return (out / 65535.0) * m


def process_depth_batch_gpu(batch_depth_numpy_raw: np.ndarray, depth_gamma: float, depth_dilate_size_x: float,
                            depth_dilate_size_y: float, depth_blur_size_x: float, depth_blur_size_y: float,
                            max_raw_value: float, depth_dilate_left: float = 0.0, depth_blur_left: float = 0.0,
                            depth_blur_left_mix: float = 0.5, skip_preprocessing: bool = False,
                            debug_task_name: str = "Render", dilate_regions: Any = None,
                            frame_indices: Optional[Any] = None) -> np.ndarray:
    """core.splatting.depth_processing.process_depth_batch, on the GPU."""
    kwargs = dict(locals())
    if not torch.cuda.is_available():
        return _dp.process_depth_batch_cpu(**kwargs)
    idxs = list(frame_indices) if frame_indices is not None else None
    if dilate_regions is not None and getattr(dilate_regions, "enabled", False) and idxs and any(
            dilate_regions.active_at(int(f)) for f in idxs):
        return _dp.process_depth_batch_cpu(**kwargs)

    raw = batch_depth_numpy_raw
    if raw.ndim == 4 and raw.shape[-1] == 3:
        depth = raw.mean(axis=-1)
    else:
        depth = raw.squeeze(-1) if raw.ndim == 4 else raw
    depth = depth.astype(np.float32)
    if skip_preprocessing:
        return depth

    width = raw.shape[2] if raw.ndim == 4 else raw.shape[1]
    res_scale = math.sqrt(width / 960.0)

    def map_val(v):
        f_v = float(v)
        return -(f_v - 30.0) if 30.0 < f_v <= 40.0 else f_v

    dx, dy = map_val(depth_dilate_size_x) * res_scale, map_val(depth_dilate_size_y) * res_scale
    bx, by = depth_blur_size_x * res_scale, depth_blur_size_y * res_scale
    dl, bl = float(depth_dilate_left) * res_scale, float(depth_blur_left) * res_scale
    if not (abs(dl) > 1e-5 or bl > 0 or abs(dx) > 1e-5 or abs(dy) > 1e-5 or bx > 0 or by > 0):
        return depth

    t = torch.from_numpy(depth).unsqueeze(1).cuda()
    if abs(dl) > 1e-5:
        t = gpu_dilate_left(t, dl, max_raw_value)
    if bl > 0:
        m = max(max_raw_value, 1e-5)
        step = m * (3.0 / 255.0)
        edge_mask = torch.zeros_like(t)
        edge_mask[:, :, :, 1:] = ((t[:, :, :, 1:] - t[:, :, :, :-1]) > step).float()
        k_blur = int(round(bl))
        k_blur = k_blur if k_blur % 2 == 1 else k_blur + 1
        half = max(1, int(math.ceil(k_blur / 4.0)))
        band = (F.max_pool2d(edge_mask, kernel_size=(1, 2 * half + 1), stride=1, padding=(0, half)) > 0.5).float()
        alpha = torch.clamp(gpu_blur(band, 7, 1, 1.0), 0.0, 1.0)
        mix = max(0.0, min(1.0, float(depth_blur_left_mix)))
        wv, wh = mix, 1.0 - mix
        bh = gpu_blur(t, k_blur, 1, max_raw_value) if wh > 1e-6 else None
        bv = gpu_blur(t, 1, k_blur, max_raw_value) if wv > 1e-6 else None
        if bh is not None and bv is not None:
            blurred = (bh * wh + bv * wv) / max(wh + wv, 1e-6)
        else:
            blurred = bh if bh is not None else (bv if bv is not None else t)
        t = t * (1.0 - alpha) + blurred * alpha
    if abs(dx) > 1e-5 or abs(dy) > 1e-5:
        t = gpu_dilate(t, dx, dy, max_raw_value)
    if bx > 0 or by > 0:
        t = gpu_blur(t, bx, by, max_raw_value)
    return t.squeeze(1).float().cpu().numpy()


def normalize_and_gamma_depth_gpu(batch_depth_numpy_raw: np.ndarray, assume_raw_input: bool, global_depth_max: float,
                                  global_depth_min: float, max_expected_raw_value: float,
                                  zero_disparity_anchor_val: float, depth_gamma: float,
                                  debug_task_name: str = "Render") -> np.ndarray:
    """core.splatting.depth_processing.normalize_and_gamma_depth, on the GPU
    (dragging only, like the rest of this file). Same branches, same order."""
    kwargs = dict(locals())
    raw = batch_depth_numpy_raw
    if not torch.cuda.is_available() or raw.size < 4096:
        return _dp.normalize_and_gamma_depth(**kwargs)
    if raw.ndim == 4 and raw.shape[-1] == 3:
        gray = raw.mean(axis=-1)
    elif raw.ndim == 4 and raw.shape[-1] == 1:
        gray = raw.squeeze(-1)
    else:
        gray = raw
    d = torch.from_numpy(np.ascontiguousarray(gray, dtype=np.float32)).cuda()
    curr_max = float(d.max())
    if assume_raw_input:
        if global_depth_max > 1.05:
            d = d / global_depth_max
        elif curr_max > 1.05:
            if curr_max > max_expected_raw_value * 1.5:
                divisor = (255.0 if curr_max <= 255.0 else 1023.0 if curr_max <= 1024.0
                           else 4095.0 if curr_max <= 4096.0 else 65535.0)
            else:
                divisor = max(max_expected_raw_value, 1.0)
            d = d / divisor
    else:
        rng = global_depth_max - global_depth_min
        if rng > 1e-5:
            d = (d - global_depth_min) / rng
        else:
            d = torch.full_like(d, float(zero_disparity_anchor_val))
    d = torch.clamp(d, 0.0, 1.0)
    if round(float(depth_gamma), 2) != 1.0:
        d = torch.clamp(1.0 - torch.pow(1.0 - d, float(depth_gamma)), 0.0, 1.0)
    return d.cpu().numpy()
