"""Post-inference affine stabilization for DepthCrafter output.

Why this exists
---------------
DepthCrafter is affine-invariant per denoising window. For clips longer than the
window it runs segment-wise, anchoring each segment's scale and shift to the
previous one's denoised latents. That anchoring chain runs strictly forward, so
when new near content arrives late in a clip every subsequent segment
renegotiates the scale -- and a scene that never moved still drifts.

Measured on a 174-frame locked-off shot: a static background patch slid from
depth 90 to 16 while the camera and scene were unchanged, and the separation
between two static planes collapsed from 22.7 to 7.2 codes. Fitting a per-frame
gain and offset back onto a reference frame reconciled it to under a code of
residual, which means the drift is a pure global affine -- no geometry is being
re-estimated, so it is fully recoverable arithmetically.

This module solves that affine per frame and applies it, in float, before any
normalization or quantization happens.

Design notes
------------
* The solve is robust (MAD-based iterative reweighting), so genuinely moving
  content -- a figure entering frame late -- is rejected as outliers rather than
  dragging the fit. MAD tolerates contamination up to ~50% of the frame.
* Frames where too little of the reference remains visible are marked invalid
  and inherit the last good transform. That is the right behaviour for a big
  foreground entrance: hold the established scale rather than fit to a subject
  that isn't in the reference at all.
* The solve runs on a strided pixel grid (cheap); the apply is chunked and
  in-place so full-length videos don't double peak RAM.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

import numpy as np

_logger = logging.getLogger(__name__)

# Frames whose robust fit retains less than this fraction of the sampled pixels
# as inliers are considered to have too little of the reference scene left to
# fit against, and inherit the previous frame's transform instead.
_DEFAULT_MIN_INLIER_FRAC = 0.20

# Sanity bounds on the solved gain. A fit outside this is a failed solve, not a
# real 40x rescale, and applying it would destroy the frame.
_MIN_GAIN = 1e-3
_MAX_GAIN = 50.0

# Minimum Pearson correlation between the inlier source and reference values for
# a fit to be trusted. See the correlation gate in _robust_affine for why inlier
# count alone is not enough.
_MIN_CORRELATION = 0.5

# The drift is gradual -- it comes from the overlapping windows renegotiating the
# scale a little at a time -- so between neighbouring frames the gain moves a few
# percent at most. A fit further from its neighbour's than this ratio has locked
# onto something that came close, not onto the scene, and the frame keeps its
# neighbour's transform (user, 2026-10-12: a person passing right past the lens
# for 13 frames fitted at gain 0.03 against ~1.0 around it, which slipped through
# every other gate and flattened them into the background).
_MAX_GAIN_STEP = 1.25

# A reference candidate must look like the clip's usual scene: its sampled depth
# correlating at least this well with the per-pixel median over the clip. Frames
# where something fills much of the picture fall below it (the same clip's pass
# was picked as the reference by spread alone, and nothing fitted it).
_MIN_REFERENCE_CORRELATION = 0.8


def _sample_grid(shape: Tuple[int, int], stride: int) -> Tuple[np.ndarray, np.ndarray]:
    """Row/col index arrays for a strided sample of an (H, W) frame."""
    h, w = shape
    stride = max(1, int(stride))
    rows = np.arange(0, h, stride)
    cols = np.arange(0, w, stride)
    rr, cc = np.meshgrid(rows, cols, indexing="ij")
    return rr.ravel(), cc.ravel()


def _robust_affine(
    src: np.ndarray,
    ref: np.ndarray,
    max_iter: int = 4,
    mad_k: float = 2.5,
) -> Tuple[float, float, float]:
    """Solve ``a * src + b ~= ref`` with MAD-based outlier rejection.

    Returns ``(gain, offset, inlier_fraction)``. On a degenerate fit the gain is
    returned as NaN so the caller can fall back.
    """
    src = src.astype(np.float64, copy=False)
    ref = ref.astype(np.float64, copy=False)
    n = src.size
    if n < 16:
        return float("nan"), 0.0, 0.0

    mask = np.ones(n, dtype=bool)
    gain, offset = 1.0, 0.0

    for _ in range(max_iter):
        s = src[mask]
        r = ref[mask]
        if s.size < 16:
            return float("nan"), 0.0, 0.0

        s_var = float(s.var())
        if not np.isfinite(s_var) or s_var < 1e-12:
            # Source frame is flat: no scale information, offset only.
            gain = 1.0
            offset = float(r.mean() - s.mean())
            break

        design = np.stack([s, np.ones_like(s)], axis=1)
        try:
            solution, *_ = np.linalg.lstsq(design, r, rcond=None)
        except np.linalg.LinAlgError:
            return float("nan"), 0.0, 0.0
        gain, offset = float(solution[0]), float(solution[1])

        residual = gain * src + offset - ref
        med = float(np.median(residual[mask]))
        mad = float(np.median(np.abs(residual[mask] - med)))
        sigma = 1.4826 * mad
        if not np.isfinite(sigma) or sigma < 1e-9:
            # Already an essentially exact fit; further iterations are noise.
            break
        new_mask = np.abs(residual - med) < (mad_k * sigma)
        if new_mask.sum() < 16:
            break
        if np.array_equal(new_mask, mask):
            break
        mask = new_mask

    inlier_frac = float(mask.sum()) / float(n)
    if not (np.isfinite(gain) and np.isfinite(offset)):
        return float("nan"), 0.0, inlier_frac
    if not (_MIN_GAIN <= gain <= _MAX_GAIN):
        return float("nan"), 0.0, inlier_frac

    # Correlation gate. Inlier fraction alone cannot detect the important
    # failure: when a large near-uniform foreground object (a figure entering at
    # arm's length) covers much of the frame, it is the *tightest* cluster
    # present, so the reweighting converges onto it rather than the background.
    # A near-constant predictor leaves the slope unidentifiable and lstsq
    # answers with a gain near zero plus a big offset -- a fit that looks
    # well-conditioned by every other measure. Genuine affine agreement
    # correlates near 1.0; locking onto a flat blob correlates near 0.
    s_in, r_in = src[mask], ref[mask]
    s_sd, r_sd = float(s_in.std()), float(r_in.std())
    if s_sd < 1e-9 or r_sd < 1e-9:
        return float("nan"), 0.0, inlier_frac
    corr = float(np.mean((s_in - s_in.mean()) * (r_in - r_in.mean())) / (s_sd * r_sd))
    if not np.isfinite(corr) or corr < _MIN_CORRELATION:
        return float("nan"), 0.0, inlier_frac

    return gain, offset, inlier_frac


def _auto_reference_index(depth: np.ndarray, stride: int) -> int:
    """Pick the frame whose *scene* uses the most range.

    Every other frame gets rescaled onto the reference, so the best-conditioned
    choice is the frame that allocated the most range to the scene -- on a clip
    that drifts toward compression, that is near the uncompressed end.

    Spread is measured with MAD rather than a percentile range on purpose. A
    large near object entering frame (a figure at arm's length against a distant
    street) blows out any percentile range and would win this contest outright,
    which is exactly backwards: those are the most compressed frames, not the
    least. MAD tolerates contamination up to roughly half the frame, so the
    winner is chosen on background spread instead of foreground contrast.

    Past half the frame it doesn't (user, 2026-10-12: a person passing right
    past the lens won it, and nothing fitted that reference). So only frames
    that look like the clip's usual scene compete: their sample correlating
    with the clip's per-pixel median at _MIN_REFERENCE_CORRELATION or more.
    """
    rr, cc = _sample_grid(depth.shape[1:], stride)
    samples = np.stack([depth[t][rr, cc].astype(np.float64, copy=False) for t in range(depth.shape[0])])
    usual = _scene_correlation(samples) >= _MIN_REFERENCE_CORRELATION
    candidates = []
    for t, sample in enumerate(samples):
        med = float(np.median(sample))
        spread = float(np.median(np.abs(sample - med)))
        candidates.append((bool(usual[t]), spread, -t))
    typical = [c for c in candidates if c[0]] or candidates     # none typical: spread alone, as before
    return int(-max(typical, key=lambda c: (c[1], c[2]))[2])


def _scene_correlation(samples: np.ndarray) -> np.ndarray:
    """Per frame, how well its sampled depth correlates with the clip's per-pixel
    median -- near 1 for the clip's usual scene however its scale drifts (the
    drift is affine), low where something close fills much of the picture."""
    out = np.ones(samples.shape[0], dtype=np.float64)
    if samples.shape[0] <= 2:
        return out
    usual = np.median(samples, axis=0)
    if usual.std() <= 1e-9:
        return out
    for t, sample in enumerate(samples):
        if sample.std() > 1e-9:
            out[t] = float(np.corrcoef(sample, usual)[0, 1])
    return out


def _smooth_series(values: np.ndarray, window: int) -> np.ndarray:
    """Centered moving average, edge-padded. Window <= 1 is a no-op."""
    window = int(window)
    if window <= 1 or values.size < 3:
        return values
    if window % 2 == 0:
        window += 1
    if window > values.size:
        window = values.size if values.size % 2 == 1 else values.size - 1
    if window <= 1:
        return values
    pad = window // 2
    padded = np.pad(values, (pad, pad), mode="edge")
    kernel = np.ones(window, dtype=np.float64) / float(window)
    return np.convolve(padded, kernel, mode="valid")


def solve_affine_series(
    depth: np.ndarray,
    ref_index: Optional[int] = None,
    stride: int = 4,
    min_inlier_frac: float = _DEFAULT_MIN_INLIER_FRAC,
    smooth_window: int = 0,
) -> Tuple[np.ndarray, np.ndarray, dict]:
    """Solve a per-frame gain and offset mapping each frame onto a reference.

    Args:
        depth: float array of shape (T, H, W).
        ref_index: reference frame index, or None to choose automatically.
        stride: pixel sampling stride for the fit.
        min_inlier_frac: below this inlier fraction a frame inherits the
            previous frame's transform instead of using its own fit.
        smooth_window: optional moving-average window applied to the solved
            gain/offset series to suppress per-frame jitter.

    Returns:
        ``(gains, offsets, stats)`` where gains and offsets have length T.
    """
    if depth.ndim != 3:
        raise ValueError(f"solve_affine_series expects (T, H, W), got shape {depth.shape}")

    num_frames = depth.shape[0]
    if num_frames == 0:
        return np.ones(0), np.zeros(0), {"reference_index": 0, "frames_held": 0}

    if ref_index is None:
        ref_index = _auto_reference_index(depth, stride)
    ref_index = int(np.clip(ref_index, 0, num_frames - 1))

    rr, cc = _sample_grid(depth.shape[1:], stride)
    reference = depth[ref_index][rr, cc].astype(np.float64)

    gains = np.ones(num_frames, dtype=np.float64)
    offsets = np.zeros(num_frames, dtype=np.float64)
    held = np.zeros(num_frames, dtype=bool)
    inlier_fracs = np.zeros(num_frames, dtype=np.float64)

    fits = [None] * num_frames
    for t in range(num_frames):
        if t == ref_index:
            inlier_fracs[t] = 1.0
            continue
        fits[t] = _robust_affine(depth[t][rr, cc], reference)
        inlier_fracs[t] = fits[t][2]

    # Outward from the reference, both ways, so each frame is judged against the
    # neighbour nearer the reference: a fit that fails or keeps too little of the
    # scene keeps the neighbour's transform -- and so does one on a frame that
    # doesn't look like the clip's usual scene (something close fills it) and
    # jumps by more than _MAX_GAIN_STEP from that neighbour. A usual frame's fit
    # stands even after a jump: across a long pass the scale drifted on.
    usual = _scene_correlation(np.stack([depth[t][rr, cc].astype(np.float64, copy=False)
                                         for t in range(num_frames)])) >= _MIN_REFERENCE_CORRELATION
    for order in (range(ref_index + 1, num_frames), range(ref_index - 1, -1, -1)):
        last_gain, last_offset = 1.0, 0.0
        for t in order:
            gain, offset, frac = fits[t]
            jump = (not usual[t] and np.isfinite(gain) and gain > 0
                    and max(gain / last_gain, last_gain / gain) > _MAX_GAIN_STEP)
            if not np.isfinite(gain) or frac < min_inlier_frac or jump:
                gains[t], offsets[t] = last_gain, last_offset
                held[t] = True
            else:
                gains[t], offsets[t] = gain, offset
                last_gain, last_offset = gain, offset

    if smooth_window and smooth_window > 1:
        gains = _smooth_series(gains, smooth_window)
        offsets = _smooth_series(offsets, smooth_window)

    stats = {
        "reference_index": ref_index,
        "frames_held": int(held.sum()),
        "held_frame_indices": np.flatnonzero(held).tolist(),
        "gain_min": float(gains.min()),
        "gain_max": float(gains.max()),
        "mean_inlier_frac": float(inlier_fracs.mean()),
    }
    return gains, offsets, stats


def apply_affine_series(
    depth: np.ndarray,
    gains: np.ndarray,
    offsets: np.ndarray,
    chunk_size: int = 32,
    in_place: bool = True,
) -> np.ndarray:
    """Apply a solved gain/offset series to a (T, H, W) depth array.

    In the array's own float type, in place on each chunk. It went through
    float64 temporaries: 2.04 s against 0.15 s on 960 frames at 768x448, for a
    largest difference of 8e-8 of the depth range -- nothing once it is 16-bit
    (.claude/dc-speed/REPORT.md, 2026-10-11)."""
    if depth.shape[0] != len(gains) or depth.shape[0] != len(offsets):
        raise ValueError(
            f"Series length mismatch: {depth.shape[0]} frames vs {len(gains)} gains / {len(offsets)} offsets"
        )

    dtype = depth.dtype if np.issubdtype(depth.dtype, np.floating) else np.float32
    if in_place and depth.dtype == dtype:
        out = depth
    else:
        out = depth.astype(dtype, copy=True)
    g_all = np.asarray(gains, dtype=dtype).reshape(-1, 1, 1)
    b_all = np.asarray(offsets, dtype=dtype).reshape(-1, 1, 1)

    chunk_size = max(1, int(chunk_size))
    for start in range(0, depth.shape[0], chunk_size):
        stop = min(start + chunk_size, depth.shape[0])
        block = out[start:stop]
        block *= g_all[start:stop]
        block += b_all[start:stop]
    return out


def stabilize_depth_affine(
    depth: np.ndarray,
    ref_index: Optional[int] = None,
    stride: int = 4,
    min_inlier_frac: float = _DEFAULT_MIN_INLIER_FRAC,
    smooth_window: int = 0,
    in_place: bool = True,
) -> Tuple[np.ndarray, dict]:
    """Solve and apply per-frame affine stabilization. Returns (depth, stats).

    A clip short enough to have been denoised in a single window has no
    segment chain and therefore nothing to correct; the solve will return gains
    near 1.0 and the apply becomes a no-op, so it is safe to leave enabled.
    """
    if depth is None or depth.size == 0:
        return depth, {"skipped": "empty input"}
    if depth.ndim != 3:
        _logger.warning(
            "Depth stabilization skipped: expected a (T, H, W) array, got shape %s", depth.shape
        )
        return depth, {"skipped": f"unexpected shape {depth.shape}"}
    if depth.shape[0] < 2:
        return depth, {"skipped": "fewer than 2 frames"}

    if not np.issubdtype(depth.dtype, np.floating):
        depth = depth.astype(np.float32)
        in_place = True

    gains, offsets, stats = solve_affine_series(
        depth,
        ref_index=ref_index,
        stride=stride,
        min_inlier_frac=min_inlier_frac,
        smooth_window=smooth_window,
    )
    depth = apply_affine_series(depth, gains, offsets, in_place=in_place)

    stats["applied"] = True
    _logger.info(
        "Depth stabilization: reference frame %d, gain range %.3f-%.3f, %d frame(s) held, mean inliers %.0f%%",
        stats["reference_index"],
        stats["gain_min"],
        stats["gain_max"],
        stats["frames_held"],
        100.0 * stats["mean_inlier_frac"],
    )
    return depth, stats


def normalize_with_percentiles(
    depth: np.ndarray,
    low_percentile: float = 0.1,
    high_percentile: float = 99.9,
    sample_stride: int = 4,
    auto_top: bool = False,
) -> Tuple[np.ndarray, dict]:
    """Normalize to 0-1 using percentile bounds rather than raw min/max.

    A single hot pixel anywhere in the sequence sets the ceiling under raw
    min/max, which on a clip containing one very near object costs real range
    everywhere else. Percentile bounds clip those tails instead.

    ``auto_top`` (user, 2026-10-12): the percentiles are taken over the clip's
    usual frames only (_scene_correlation), so something that comes right up to
    the lens for a few frames clips to white instead of setting the top for the
    whole clip -- on chap2_00097213 (a person passing at frames 60-74) that put
    the top at the 97.6th percentile of the whole clip, the scene's own p99 at
    0.93 where 99.9 left it at 0.26. A clip without such frames is unchanged.
    """
    if depth is None or depth.size == 0:
        return depth, {"skipped": "empty input"}

    low_percentile = float(np.clip(low_percentile, 0.0, 100.0))
    high_percentile = float(np.clip(high_percentile, 0.0, 100.0))
    if high_percentile <= low_percentile:
        low_percentile, high_percentile = 0.0, 100.0

    if depth.ndim == 3 and sample_stride > 1:
        rr, cc = _sample_grid(depth.shape[1:], sample_stride)
        sample = depth[:, rr, cc]
    else:
        sample = depth

    unusual = []
    if auto_top and depth.ndim == 3:
        flat = np.asarray(sample, dtype=np.float64).reshape(sample.shape[0], -1)
        usual = _scene_correlation(flat) >= _MIN_REFERENCE_CORRELATION
        if usual.any() and not usual.all():
            unusual = np.flatnonzero(~usual).tolist()
            sample = flat[usual]

    lo, hi = np.percentile(sample, [low_percentile, high_percentile])
    lo, hi = float(lo), float(hi)

    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        lo, hi = float(np.min(depth)), float(np.max(depth))

    if hi <= lo:
        return np.zeros_like(depth, dtype=np.float32), {
            "low_value": lo,
            "high_value": hi,
            "degenerate": True,
        }

    out = np.clip((depth.astype(np.float32) - lo) / (hi - lo), 0.0, 1.0)
    stats = {
        "low_percentile": low_percentile,
        "high_percentile": high_percentile,
        "low_value": lo,
        "high_value": hi,
    }
    if auto_top:
        stats["auto_top_unusual_frames"] = unusual
    return out, stats
