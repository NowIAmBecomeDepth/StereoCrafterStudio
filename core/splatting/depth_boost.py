"""
Depth Boost ("extra front convergence").

Adds extra disparity to the nearest-camera pixels of a normalized depth map,
letting selected objects pop out further than the nominal Max Disparity allows.

Because the splat disparity is LINEAR in depth:

    disp = (depth - convergence) * 2.0 * (max_disp / 20.0) * tv_disp_comp

an additive boost in disparity space is exactly equivalent to an additive
remap in depth space:

    depth' = depth + w**curve * (boost_amount / max_disp)
    where w = clamp((depth - thresh) / (1.0 - thresh), 0, 1)

`boost_amount` is expressed in the SAME UNITS as the Max Disparity slider,
so boost_amount=5.0 gives the whitest pixels the extra pop that 5 additional
disparity units would have given a full-white pixel. tv_disp_comp and target
width scaling are applied downstream by the warp, so they affect the boost
consistently with everything else.

NOTE: boosted depth values intentionally EXCEED 1.0. The forward warp must
not clamp its incoming depth tensor to [0, 1] after this is applied.

Used by:
  - splatting_gui.py  (preview path, applied to disp_map_tensor pre-warp)
  - core BatchProcessor render path (applied at the same point, pre-warp)
Both paths MUST call this one function so preview == render.
"""

from typing import Union

import numpy as np
import torch

TensorOrArray = Union[torch.Tensor, np.ndarray]


def boost_is_active(boost_amount: float, max_disp: float) -> bool:
    """True when the boost parameters would actually change the depth map."""
    try:
        return float(boost_amount) > 0.0 and float(max_disp) > 0.0
    except (TypeError, ValueError):
        return False


def apply_depth_boost(
    depth: TensorOrArray,
    boost_thresh: float,
    boost_amount: float,
    boost_curve: float,
    max_disp: float,
    boost_knee: float = 0.0,
) -> TensorOrArray:
    """
    Apply the depth boost remap to a normalized ([0..1]) depth map.

    Args:
        depth:        torch.Tensor or np.ndarray, normalized depth (0=far, 1=near).
        boost_thresh: depth value where the boost starts ramping in (0.5..0.99).
        boost_amount: extra disparity at depth=1.0, in Max Disparity slider units.
        boost_curve:  ramp exponent between thresh and 1.0 (1=linear, higher=tighter).
        max_disp:     the current Max Disparity value (used to convert the boost
                      from disparity units into depth units; must be > 0).
        boost_knee:   width, in w units (0..1), of a smoothstep gate applied at
                      the bottom of the ramp. 0.0 = off, which is the exact
                      legacy behaviour.

                      Only matters for curve < 1.0. The ramp's slope is
                      curve * w**(curve - 1), which for curve < 1 DIVERGES as
                      w -> 0: at curve 0.5 the gradient amplification at the
                      threshold is unbounded, so any depth noise sitting right
                      at the threshold explodes into the disparity field and
                      then into the occlusion edges. The GUI exposes
                      curve down to 0.5, so the singularity is reachable.

                      For curve >= 1 the slope at w = 0 is finite (and zero for
                      curve > 1), so the ramp already starts smoothly and this
                      does nothing useful -- leave it at 0.0.

    Returns:
        Same type as input. Values above 1.0 are expected and intentional.
        Returns the input unchanged (same object) when the boost is inactive.
    """
    if not boost_is_active(boost_amount, max_disp):
        return depth

    thresh = min(max(float(boost_thresh), 0.0), 0.99)
    curve = max(float(boost_curve), 0.01)
    depth_units = float(boost_amount) / float(max_disp)

    knee = min(max(float(boost_knee), 0.0), 1.0)

    if isinstance(depth, torch.Tensor):
        w = ((depth - thresh) / (1.0 - thresh)).clamp(0.0, 1.0)
        ramp = w.pow(curve)
        if knee > 0.0:
            # smoothstep t**2 * (3 - 2t): zero derivative at both ends, so the
            # product with w**curve approaches 0 smoothly and the curve < 1
            # singularity at w = 0 is removed.
            t = (w / knee).clamp(0.0, 1.0)
            ramp = ramp * (t * t * (3.0 - 2.0 * t))
        return depth + ramp * depth_units

    w = np.clip((np.asarray(depth) - thresh) / (1.0 - thresh), 0.0, 1.0)
    ramp = np.power(w, curve, dtype=np.float32)
    if knee > 0.0:
        t = np.clip(w / knee, 0.0, 1.0).astype(np.float32)
        ramp = ramp * (t * t * (3.0 - 2.0 * t))
    return depth + ramp * np.float32(depth_units)

# --------------------------------------------------------------------------
# Pop-Out (user, 2026-10-01)
# --------------------------------------------------------------------------
#
# One dial that adds pop in front of the screen and leaves the rest of the
# scene where it was. With the splat's linear map
#
#     disp = (depth - c) * k          (c = convergence, k = pixels per depth unit)
#
# Pop-Out adds, in the same depth units Boost uses (P / max_disp per unit of
# P, so P is in Disparity-dial units):
#
#     extra = (P / max_disp) * u**2,   u = clamp((depth - c) / (peak - c), 0, 1)
#
# * depth <= c (on or behind the screen): u = 0, extra = 0 -- the background,
#   and the convergence plane itself, are exactly unchanged.
# * u**2 starts flat (zero slope at the screen), so subjects just in front of
#   the screen barely move: a fifth of the way forward gets 4% of P.
# * the clip's nearest depth (peak) gets the full P.
#
# Why not Boost with a low threshold: the extra's slope is what stretches a
# soft depth outline, and Boost packs its whole ramp into (1 - thresh) --
# d(extra)/d(depth) peaks at 2 (P/M) / (1 - thresh), 6.7x the P/M term at the
# default 0.85. Pop-Out spreads the same ramp over (peak - c): at c = 0.5 that
# is 2 (P/M) / 0.5, so the same pop warps outlines ~3x less.


def pop_out_is_active(pop_amount: float, max_disp: float) -> bool:
    try:
        return float(pop_amount) > 0.0 and float(max_disp) > 0.0
    except (TypeError, ValueError):
        return False


def pop_out_extra(depth: TensorOrArray, pop_amount: float, convergence: float, max_disp: float,
                  depth_peak: float = 1.0) -> TensorOrArray:
    """The Pop-Out addition to `depth` (normalized, post-gamma), in depth
    units: zero at and behind `convergence`, rising as u**2 to
    pop_amount / max_disp at `depth_peak` (the clip's nearest depth, same
    domain; 1.0 when unknown). Zeros when inactive or when the peak is not in
    front of the screen."""
    peak = min(max(float(depth_peak), 1e-3), 1.0)
    c = float(convergence)
    span = peak - c
    if not pop_out_is_active(pop_amount, max_disp) or span <= 1e-3:
        return depth * 0
    units = float(pop_amount) / float(max_disp)
    if isinstance(depth, torch.Tensor):
        u = ((depth - c) / span).clamp(0.0, 1.0)
        return u * u * units
    u = np.clip((np.asarray(depth) - c) / span, 0.0, 1.0).astype(np.float32)
    return u * u * np.float32(units)


# --------------------------------------------------------------------------
# Guided pre-filter
# --------------------------------------------------------------------------
#
# Why this exists, measured rather than assumed:
#
# The boost is a gain on depth GRADIENTS, not just on depth values.
# Differentiating the remap above gives
#
#     d(out)/d(depth) = 1 + curve * w**(curve-1) * depth_units / (1 - thresh)
#
# At the shipped defaults (thresh 0.85, curve 2.0, amount 5, max_disp 20) that
# reaches 4.1x by depth 0.99, driven mostly by the 1/(1-thresh) term -- 6.7x on
# its own at thresh 0.85. Whatever noise the depth map carries near white gets
# multiplied by that factor.
#
# It mattered because the amplified gradient fed straight into the smear
# detector of Stretch->Hole (removed 2026-10-09), which thresholded
# gap = 1 + d(x) - d(x+1). Small bright subjects (sparks, highlights) sit
# exactly in the amplified zone, so their quantized depth gradients spiked the
# gap, Stretch->Hole converted them into occlusion holes, and the inpainter
# regenerated a band that shifted every frame. The visible result is a warped,
# squiggly edge around boosted objects that crawls temporally.
#
# The knee at the threshold is NOT the cause: for curve > 1 the derivative of
# w**curve at w=0 is zero, so the ramp already starts smoothly -- at curve 2.0
# the amplification across the threshold goes 1.00 -> 1.22. See boost_knee
# below for the case where the knee genuinely does bite.
#
# The fix is therefore to reduce gradient noise BEFORE it is amplified, which
# is what this does: an edge-aware guided filter (He et al.) using the RGB
# source as the guide. It smooths depth within regions of uniform image content
# while snapping depth transitions onto real image edges, so the boost's
# threshold contour follows the subject outline rather than the quantization
# plateaus of the depth map.


def _box_mean(x: torch.Tensor, radius: int) -> torch.Tensor:
    """Mean over a (2r+1) square window, borders averaged over valid pixels only."""
    k = 2 * int(radius) + 1
    return torch.nn.functional.avg_pool2d(
        x, kernel_size=k, stride=1, padding=int(radius), count_include_pad=False
    )


def apply_guided_prefilter(
    depth: torch.Tensor,
    guide: torch.Tensor,
    radius: int = 4,
    eps: float = 1e-3,
) -> torch.Tensor:
    """Edge-aware smoothing of a depth map, guided by the source image.

    Args:
        depth: [B, 1, H, W] normalized depth.
        guide: [B, C, H, W] source image in [0, 1]. Converted to luma internally.
        radius: window radius in pixels. Larger removes more noise and rounds
            off fine detail; 3-6 suits the small bright subjects this targets.
        eps: edge sensitivity. Smaller preserves more edges but smooths less.

    Returns:
        Filtered depth, same shape and dtype. Returns the input unchanged (the
        same object) when radius < 1, so the feature costs nothing when off.
    """
    if radius is None or int(radius) < 1:
        return depth
    if depth is None or guide is None:
        return depth

    radius = int(radius)
    orig_dtype = depth.dtype

    # Guided filtering is a per-window least-squares fit, and fp16 loses the
    # variance term on low-contrast content -- which is exactly the content
    # this targets. Compute in fp32 and cast back.
    d = depth.float()
    g = guide.float()

    if g.shape[1] >= 3:
        # Rec.709 luma. A single-channel guide keeps this cheap; the full
        # colour guided filter needs a 3x3 solve per pixel and buys little on
        # depth, which carries no chroma structure of its own.
        g = 0.2126 * g[:, 0:1] + 0.7152 * g[:, 1:2] + 0.0722 * g[:, 2:3]
    else:
        g = g[:, 0:1]

    if g.shape[-2:] != d.shape[-2:]:
        g = torch.nn.functional.interpolate(
            g, size=d.shape[-2:], mode="bilinear", align_corners=False
        )

    mean_g = _box_mean(g, radius)
    mean_d = _box_mean(d, radius)
    var_g = _box_mean(g * g, radius) - mean_g * mean_g
    cov_gd = _box_mean(g * d, radius) - mean_g * mean_d

    a = cov_gd / (var_g + float(eps))
    b = mean_d - a * mean_g

    out = _box_mean(a, radius) * g + _box_mean(b, radius)
    return out.to(orig_dtype)
