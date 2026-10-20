"""gpu_ops.py -- image operations on the GPU that were OpenCV on the CPU
(user, 2026-10-04: "anything in studio that isn't using the GPU ... that could
benefit from it").

* colour transfer (Lab mean / std matching) -- Merging and Inpainting: 142 ms
  a 1080p frame on the CPU, through an 8-bit round trip; here in float, so
  the 10-bit pipeline keeps its gradients;
* Gaussian blur, and dilate / erode with OpenCV's elliptical structuring
  element -- Detect Text's Text Grow / Soften / Clean, the Depth Map Editor's
  render at source resolution.

Each matches its OpenCV counterpart closely (the same kernels, the same
ellipse), not bit for bit: float throughout, where OpenCV rounded to 8 bits.
Everything takes and returns torch tensors on any device; `device()` is the
GPU when there is one, else the CPU (the same code, just slower).
"""

from __future__ import annotations

import math
from typing import Optional

import torch
import torch.nn.functional as F


def device() -> torch.device:
    return torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu")


def gpu_available() -> bool:
    return torch.cuda.is_available()


def vram_note(log, what: str, release: bool = False) -> None:
    """After `what`: with ``release``, PyTorch's cached blocks handed back to
    the driver (user, 2026-10-05: tracking left GBs reserved that nothing
    else could use, the card at 15.5 of 16 GB); and, at DEBUG, what this
    process really holds -- in use by tensors vs. reserved by PyTorch."""
    if not torch.cuda.is_available():
        return
    try:
        debug = log.isEnabledFor(10)
        if debug:
            used, held = torch.cuda.memory_allocated(), torch.cuda.memory_reserved()
        if release:
            torch.cuda.empty_cache()
        if debug:
            gb = 2 ** 30
            note = f"VRAM after {what}: {used / gb:.2f} GB in use, {held / gb:.2f} GB reserved by PyTorch"
            if release:
                note += f"; {(held - torch.cuda.memory_reserved()) / gb:.2f} GB handed back"
            log.debug(note)
    except Exception:
        log.debug("Could not read / release the CUDA cache", exc_info=True)


# --------------------------------------------------------------------------
# Gaussian blur
# --------------------------------------------------------------------------

def _gauss_kernel(sigma: float, ksize: int, dev, dtype) -> torch.Tensor:
    x = torch.arange(ksize, device=dev, dtype=torch.float64) - (ksize - 1) / 2.0
    k = torch.exp(-(x * x) / (2.0 * sigma * sigma))
    return (k / k.sum()).to(dtype)


def _ksize(sigma: float, eight_bit: bool) -> int:
    """OpenCV's kernel size for a sigma (createGaussianKernels): 3 sigma a
    side for 8-bit images, 4 for float."""
    return max(1, int(round(sigma * (3 if eight_bit else 4) * 2 + 1)) | 1)


def gaussian_blur(x: torch.Tensor, sigma: float, eight_bit: bool = False) -> torch.Tensor:
    """cv2.GaussianBlur(x, (0, 0), sigma) for x [N, C, H, W] float: separable,
    OpenCV's kernel size, its default border (reflect-101). A sigma so large
    the kernel outgrows the image is blurred at a smaller size and enlarged
    -- the same result to within the blur's own smoothness, far fewer taps."""
    if sigma <= 0:
        return x
    n, c, h, w = x.shape
    # very wide: shrink so the kernel is ~32 sigma-pixels at most a side
    if sigma > 24 and min(h, w) > 8:
        f = max(1, int(sigma // 12))
        if f > 1:
            sh, sw = max(1, h // f), max(1, w // f)
            small = F.interpolate(x, size=(sh, sw), mode="area")
            small = gaussian_blur(small, sigma * sh / h, eight_bit)
            return F.interpolate(small, size=(h, w), mode="bilinear", align_corners=False)
    k = _gauss_kernel(sigma, _ksize(sigma, eight_bit), x.device, x.dtype)
    r = (k.numel() - 1) // 2
    y = x.reshape(n * c, 1, h, w)
    # reflect needs a pad smaller than the side; past it, the edge repeats
    ph = min(r, h - 1)
    pw = min(r, w - 1)
    kx = k.view(1, 1, 1, -1)
    ky = k.view(1, 1, -1, 1)
    y = F.pad(y, (pw, pw, 0, 0), mode="reflect" if pw > 0 else "replicate")
    if pw < r:
        y = F.pad(y, (r - pw, r - pw, 0, 0), mode="replicate")
    y = F.conv2d(y, kx)
    y = F.pad(y, (0, 0, ph, ph), mode="reflect" if ph > 0 else "replicate")
    if ph < r:
        y = F.pad(y, (0, 0, r - ph, r - ph), mode="replicate")
    y = F.conv2d(y, ky)
    return y.reshape(n, c, h, w)


# --------------------------------------------------------------------------
# Box and guided filters
# --------------------------------------------------------------------------

def _pad_symmetric(x: torch.Tensor, r: int) -> torch.Tensor:
    """OpenCV's BORDER_REFLECT (fedcba|abcdef) on the last two dims."""
    h, w = x.shape[-2:]
    rh, rw = min(r, h), min(r, w)
    x = torch.cat([x[..., :rh, :].flip(-2), x, x[..., h - rh:, :].flip(-2)], -2)
    x = torch.cat([x[..., :rw].flip(-1), x, x[..., w - rw:].flip(-1)], -1)
    if rh < r or rw < r:                       # a side shorter than the radius
        x = F.pad(x, (r - rw, r - rw, r - rh, r - rh), mode="replicate")
    return x


def box_filter(x: torch.Tensor, r: int) -> torch.Tensor:
    """cv2.boxFilter(x, -1, (2r+1, 2r+1), normalize=True, BORDER_REFLECT),
    x [N, C, H, W] float."""
    if r <= 0:
        return x
    return F.avg_pool2d(_pad_symmetric(x, r), kernel_size=2 * r + 1, stride=1)


def guided_filter(guide: torch.Tensor, src: torch.Tensor, r: int, eps: float) -> torch.Tensor:
    """cv2.ximgproc.guidedFilter for a one-channel guide (He et al.):
    guide / src [N, 1, H, W] float."""
    mean_i, mean_p = box_filter(guide, r), box_filter(src, r)
    var_i = box_filter(guide * guide, r) - mean_i * mean_i
    cov_ip = box_filter(guide * src, r) - mean_i * mean_p
    a = cov_ip / (var_i + eps)
    b = mean_p - a * mean_i
    return box_filter(a, r) * guide + box_filter(b, r)


# --------------------------------------------------------------------------
# Morphology with OpenCV's ellipse
# --------------------------------------------------------------------------

def _ellipse_rows(r: int):
    """(dy, half-width) per row of cv2.getStructuringElement(MORPH_ELLIPSE,
    (2r+1, 2r+1)): dx = cvRound(sqrt(r^2 - dy^2))."""
    rows = []
    for dy in range(-r, r + 1):
        rows.append((dy, int(round(math.sqrt(max(0, r * r - dy * dy))))))
    return rows


def dilate_ellipse(x: torch.Tensor, r: int) -> torch.Tensor:
    """cv2.dilate with the (2r+1)^2 ellipse, x [N, C, H, W] float: the max
    over each of the ellipse's rows (a 1-D max filter of that row's width,
    shifted by its dy). Outside the image counts as nothing (OpenCV's
    default border for dilate)."""
    if r <= 0:
        return x
    n, c, h, w = x.shape
    y = x.reshape(n * c, h, w)
    lo = torch.finfo(x.dtype).min
    widths = {}
    out = None
    padded = F.pad(y.unsqueeze(1), (0, 0, r, r), value=lo).squeeze(1)       # rows above / below
    for dy, hw in _ellipse_rows(r):
        if hw not in widths:
            # [N*C, H+2r, W]: max_pool1d pools along W, each row on its own
            widths[hw] = F.max_pool1d(padded, kernel_size=2 * hw + 1, stride=1, padding=hw) if hw > 0 else padded
        row = widths[hw][:, r + dy:r + dy + h, :]
        out = row if out is None else torch.maximum(out, row)
    return out.reshape(n, c, h, w)


def erode_ellipse(x: torch.Tensor, r: int) -> torch.Tensor:
    """cv2.erode with the same ellipse (outside the image counts as full)."""
    return -dilate_ellipse(-x, r)


# --------------------------------------------------------------------------
# Colour transfer
# --------------------------------------------------------------------------

# OpenCV's sRGB (D65) <-> XYZ and its white point
_M = torch.tensor([[0.412453, 0.357580, 0.180423],
                   [0.212671, 0.715160, 0.072169],
                   [0.019334, 0.119193, 0.950227]], dtype=torch.float32)
_WHITE = torch.tensor([0.950456, 1.0, 1.088754], dtype=torch.float32)


def _to_linear(c):
    return torch.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055).clamp_min(0) ** 2.4)


def _to_srgb(c):
    c = c.clamp_min(0)
    return torch.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def rgb_to_lab(rgb: torch.Tensor) -> torch.Tensor:
    """[B, 3, H, W] sRGB 0..1 -> Lab (L 0..100, a / b about -128..127)."""
    m, white = _M.to(rgb), _WHITE.to(rgb)
    lin = _to_linear(rgb.clamp(0, 1))
    xyz = torch.einsum("ij,bjhw->bihw", m, lin) / white.view(1, 3, 1, 1)
    f = torch.where(xyz > 0.008856, xyz.clamp_min(1e-12) ** (1 / 3), 7.787 * xyz + 16 / 116)
    L = 116 * f[:, 1:2] - 16
    a = 500 * (f[:, 0:1] - f[:, 1:2])
    b = 200 * (f[:, 1:2] - f[:, 2:3])
    return torch.cat([L, a, b], 1)


def lab_to_rgb(lab: torch.Tensor) -> torch.Tensor:
    m_inv, white = torch.linalg.inv(_M).to(lab), _WHITE.to(lab)
    fy = (lab[:, 0:1] + 16) / 116
    fx = lab[:, 1:2] / 500 + fy
    fz = fy - lab[:, 2:3] / 200
    f = torch.cat([fx, fy, fz], 1)
    xyz = torch.where(f > 6 / 29, f ** 3, (f - 16 / 116) / 7.787) * white.view(1, 3, 1, 1)
    lin = torch.einsum("ij,bjhw->bihw", m_inv, xyz)
    return _to_srgb(lin).clamp(0, 1)


def color_transfer(ref: torch.Tensor, target: torch.Tensor, chunk: int = 8) -> torch.Tensor:
    """Each target frame takes its ref frame's Lab mean and spread (as
    image_processing.apply_color_transfer did with OpenCV), in float.
    ref / target: [B, 3, H, W] (or [3, H, W]) 0..1 on any device; computed on
    target's device, `chunk` frames at a time (1080p: ~0.4 GB a chunk).
    Returns target's shape, on target's device."""
    single = target.dim() == 3
    if single:
        ref, target = ref.unsqueeze(0), target.unsqueeze(0)
    ref = ref.to(target.device, torch.float32)
    tgt = target.to(torch.float32)
    out = torch.empty_like(tgt)
    for s in range(0, tgt.shape[0], chunk):
        r_lab = rgb_to_lab(ref[s:s + chunk])
        t_lab = rgb_to_lab(tgt[s:s + chunk])
        r_mean, r_std = r_lab.mean((2, 3), keepdim=True), r_lab.std((2, 3), keepdim=True, unbiased=False)
        t_mean, t_std = t_lab.mean((2, 3), keepdim=True), t_lab.std((2, 3), keepdim=True, unbiased=False)
        adj = (t_lab - t_mean) / t_std.clamp_min(1e-6) * r_std.clamp_min(1e-6) + r_mean
        # OpenCV's 8-bit Lab held each channel to its range: so does this
        adj = torch.cat([adj[:, 0:1].clamp(0, 100), adj[:, 1:3].clamp(-128, 127)], 1)
        out[s:s + chunk] = lab_to_rgb(adj)
        del r_lab, t_lab, adj
    out = out.to(target.dtype)
    return out[0] if single else out
