"""submask_gpu.py -- Detect Text's mask on the GPU (user, 2026-10-01).

The same mask as core.ui.subtitle_mask_editor.compute_mask, step for step, in
PyTorch on CUDA:

* the brightness OpenCV uses (its fixed-point BGR->gray, the min of channels
  for White, min(G, R) - B/2 saturating for Yellow);
* the hysteresis -- keep the Edge components that hold a Core pixel -- as
  8-connected component labelling by label propagation with pointer jumping
  (every pixel of a component ends on the same label, the component's largest
  index), so the kept pixels are exactly connectedComponents' ones;
* dilation by OpenCV's own elliptical element, a Gaussian from OpenCV's own
  kernel with its reflect-101 border, the rectangle, the invert.

Hard masks match the CPU's bit for bit; a Soft edge's blur may differ by up to
two grey levels (of 255) where OpenCV blurs 8-bit in fixed point. Also here: opening a clip with
hardware decoding, and whether ffmpeg's NVENC works, for the mask render.
"""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from typing import Optional

import cv2
import numpy as np
import torch
import torch.nn.functional as F


def available() -> bool:
    try:
        return torch.cuda.is_available()
    except Exception:
        return False


def _brightness(f: torch.Tensor, mode: str) -> torch.Tensor:
    """(H, W, 3) int32 BGR -> (H, W) int32, as compute_mask's `v`."""
    b, g, r = f[..., 0], f[..., 1], f[..., 2]
    if mode == "White":
        return torch.minimum(torch.minimum(b, g), r)
    if mode == "Yellow":
        return (torch.minimum(g, r) - b // 2).clamp(min=0)
    # cv2.COLOR_BGR2GRAY on 8-bit: (B*1868 + G*9617 + R*4899 + 2^13) >> 14
    return (b * 1868 + g * 9617 + r * 4899 + 8192) >> 14


def _keep_touching(edge: torch.Tensor, core: torch.Tensor) -> torch.Tensor:
    """The pixels of `edge`'s 8-connected components that contain a `core`
    pixel (core is inside edge). Labels are flat indices (exact in float32 up
    to 16.7 M pixels: 4K is 8.3 M)."""
    h, w = edge.shape
    idx = torch.arange(h * w, device=edge.device, dtype=torch.float32).view(h, w)
    lab = torch.where(edge, idx, torch.full_like(idx, -1.0))
    while True:
        prev = lab
        nb = F.max_pool2d(lab[None, None], 3, stride=1, padding=1)[0, 0]
        lab = torch.where(edge, nb, lab)
        # pointer jumping: take the label your label's pixel has reached
        flat = lab.view(-1)
        for _ in range(2):
            jump = flat[lab.clamp(min=0).long()]
            lab = torch.where(edge, torch.maximum(lab, jump), lab)
            flat = lab.view(-1)
        if torch.equal(lab, prev):
            break
    hit = torch.zeros(h * w, dtype=torch.bool, device=edge.device)
    seeds = lab[core]
    if seeds.numel():
        hit[seeds.long()] = True
    return edge & hit[lab.clamp(min=0).long()]


@lru_cache(maxsize=16)
def _ellipse(rad: int):
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * rad + 1, 2 * rad + 1)).astype(np.float32)


@lru_cache(maxsize=16)
def _gauss(sigma: float):
    k = int(round(sigma * 3 * 2 + 1)) | 1           # OpenCV's size for 8-bit, from sigma
    return cv2.getGaussianKernel(k, sigma).astype(np.float64).ravel()


def compute_mask_torch(frame: torch.Tensor, settings: dict) -> torch.Tensor:
    """(H, W, 3) uint8 BGR on the GPU -> (H, W) uint8 mask, 0..255."""
    dev = frame.device
    v = _brightness(frame.to(torch.int32), settings["color"])
    core = v >= int(settings["core"])
    if settings["refine"]:
        edge = v >= min(int(settings["edge"]), int(settings["core"]))
        mask = _keep_touching(edge, core) if edge.any() else core
    else:
        mask = core
    m = mask.to(torch.float32)

    rad = int(settings["r"])
    if rad > 0:
        k = torch.from_numpy(_ellipse(rad)).to(dev)[None, None]
        m = (F.conv2d(m[None, None], k, padding=rad)[0, 0] > 0.5).to(torch.float32)
    m = m * 255.0

    if settings["edge_mode"] == "Soft":
        g = torch.from_numpy(_gauss(max(0.8, rad * 0.8))).to(dev, torch.float64)
        p = g.numel() // 2
        x = m.to(torch.float64)[None, None]
        x = F.conv2d(F.pad(x, (p, p, 0, 0), mode="reflect"), g.view(1, 1, 1, -1))
        x = F.conv2d(F.pad(x, (0, 0, p, p), mode="reflect"), g.view(1, 1, -1, 1))
        m = x[0, 0].round().clamp(0, 255).to(torch.float32)

    rect = settings.get("rect")
    if rect:
        x0, y0, x1, y1 = (int(c) for c in rect)
        limited = torch.zeros_like(m)
        limited[y0:y1, x0:x1] = m[y0:y1, x0:x1]
        m = limited
    if settings["invert"]:
        m = 255.0 - m
    return m.to(torch.uint8)


def compute_mask_gpu(frame_bgr: np.ndarray, settings: dict, device: str = "cuda") -> np.ndarray:
    """compute_mask's signature: a numpy frame in, a numpy mask out."""
    with torch.inference_mode():
        f = torch.from_numpy(np.ascontiguousarray(frame_bgr)).to(device, non_blocking=True)
        return compute_mask_torch(f, settings).cpu().numpy()


def open_capture(path: str, gpu: bool):
    """cv2.VideoCapture, decoding on the GPU (Windows' hardware decoder via
    OpenCV's FFmpeg backend) when `gpu`; plain when that won't open."""
    if gpu:
        try:
            cap = cv2.VideoCapture(path, cv2.CAP_FFMPEG,
                                   [cv2.CAP_PROP_HW_ACCELERATION, cv2.VIDEO_ACCELERATION_ANY])
            if cap.isOpened():
                return cap
            cap.release()
        except Exception:
            pass
    return cv2.VideoCapture(path)


def hw_decoding(cap) -> bool:
    try:
        return int(cap.get(cv2.CAP_PROP_HW_ACCELERATION)) not in (0, -1)
    except Exception:
        return False


@lru_cache(maxsize=1)
def nvenc_works() -> bool:
    """One tiny h264_nvenc encode: the encoder is listed on machines whose
    GPU or driver cannot actually run it."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=black:s=256x256:d=0.1",
             "-c:v", "h264_nvenc", "-f", "null", "-"],
            capture_output=True, timeout=20,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
        return r.returncode == 0
    except Exception:
        return False


def encoder_args(gpu: bool) -> list:
    """The mask video's codec arguments: NVENC at a near-lossless constant QP
    on the GPU, else libx264 at CRF 10 (as before)."""
    if gpu and nvenc_works():
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "constqp", "-qp", "10"]
    return ["-c:v", "libx264", "-crf", "10", "-preset", "fast"]
