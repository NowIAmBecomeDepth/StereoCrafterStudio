"""depth_guard.py -- Edge Fill's depth awareness (user, 2026-11-07: "lets add depth map awareness too").

A near subject passing the frame's edge is real picture in the warped eye -- Splatting moves it over the edge,
so the splat mask has no hole there -- but the fill's pad and fade past each row's hole, and copies brought in
from other frames, painted over it (ch.3 BACKTOFORMULA00017844: the general's name plate came out "ꟻLOCUM").
This finds what is NEAR beside the strip, so the fill leaves it alone.

Depth as it is on screen, from the merged pair itself (user: "Measure from L/R"): each pixel of the filled eye's
crop is matched against the other eye along the row -- the shift that matches is its disparity, everything
Splatting did included. The background's shift beside the strip is the hole's own width (Splatting's shift
opened it). A pixel is near when it matches clearly better at a nearer shift than at the background's: a
plain wall matches at any shift and stays background.

Crops come as Edge Fill has them: the strip on the left (a right edge mirrored, both eyes alike), so the
filled eye's x shows the other eye's x - shift for every pixel (shift = the background's hole width).
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)

HALF = 2                  # matched at half size
PATCH = 7                 # the matching window, half-size px
NEAR_BY = 0.35            # near: its shift this share of the background's (or MIN_GAP px) nearer...
MIN_GAP = 6.0
CLEARLY = 0.75            # ...and its match that much better than at the background's shift
GROW = 3                  # full-size px the near mask is grown by...
SOFT = 2.0                # ...and softened over


def near_maps(eye, other, bg_shift: np.ndarray, reach: int, progress: Optional[Callable[[float], None]] = None,
              stop=None) -> np.ndarray:
    """(n, h, w) uint8 0-255: how near each pixel of `eye` (n, h, w, 3) uint8 is -- 255 near, 0 background.
    `other`: the other eye's crop (n, h, >= w, 3) uint8, lined up the same way; `bg_shift` (n,): each frame's
    background shift in px (0: no strip that frame -- nothing near); `reach`: the largest shift either way."""
    import cv2
    import torch
    from .erase import Stopped
    n, h, w = eye.shape[:3]
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    hh, ww = h // HALF, w // HALF
    r = max(4, int(reach) // HALF + 2)
    out = np.zeros((n, h, w), np.uint8)
    pool = torch.nn.AvgPool2d(PATCH, stride=1, padding=PATCH // 2, count_include_pad=False)
    big = 1e4
    with torch.no_grad():
        for t in range(n):
            if stop is not None and stop.is_set():
                raise Stopped()
            sb = float(bg_shift[t])
            if sb < 1:
                continue
            a = torch.from_numpy(cv2.resize(np.asarray(eye[t]), (ww, hh), interpolation=cv2.INTER_AREA)).to(dev).float()
            ow = np.asarray(other[t]).shape[1]
            b = torch.from_numpy(cv2.resize(np.asarray(other[t]), (ow // HALF, hh), interpolation=cv2.INTER_AREA)).to(dev).float()
            costs = []
            shifts = range(-r, r + 1)
            for s in shifts:                     # eye(x) ~ other(x - s): positive s is the background's way
                c = torch.full((hh, ww), big, device=dev)
                lo, hi = max(0, s), min(ww, b.shape[1] + s)
                if hi > lo:
                    c[:, lo:hi] = (a[:, lo:hi] - b[:, lo - s:hi - s]).abs().mean(-1)
                costs.append(c)
            cost = pool(torch.stack(costs)[None])[0]                  # (S, hh, ww)
            best, arg = cost.min(0)
            shift = (arg.float() - r) * HALF                          # full-size px
            sbi = int(round(sb / HALF)) + r
            at_bg = cost[min(max(sbi, 0), len(shifts) - 1)]
            gap = max(MIN_GAP, NEAR_BY * sb)
            near = (shift < sb - gap) & (best < CLEARLY * at_bg) & (best < big / 2)
            m = near.byte().cpu().numpy() * 255
            m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
            m = cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR)
            m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * GROW + 1, 2 * GROW + 1)))
            out[t] = cv2.GaussianBlur(m, (0, 0), SOFT)
            if progress is not None and t % 8 == 0:
                progress(t / n)
    if dev.type == "cuda":
        torch.cuda.empty_cache()
    share = float((out > 127).mean())
    logger.info(f"  Depth: {share:.1%} of the edge's crop is nearer than the background beside the strip; "
                f"the fill leaves it alone")
    return out


# ---- from the clip's depth map (user, 2026-11-07: "Yes, use depth map") ------------------------------------
#
# The L / R match above flickered at a subject's outline: per frame, it left holes in the subject and took
# streaks of plain wall for near (ch.3 BACKTOFORMULA00017844: 3.2% of the pixels by the strip flipped
# near <-> background each frame). The clip's DepthCrafter map is steady -- it flipped 1.6%, the subject whole,
# the wall clean. It is the left eye's (the source's), so it is moved into the filled eye as Splatting moves the
# picture: each pixel by its shift, the nearer winning. Its shift is fitted per clip, a * depth + b, on the pixels
# the L / R match is sure of -- on that clip 200,000 of them to within 0.4 px -- so Splatting's own settings
# needn't be known.

FIT_EVERY = 4             # every 4th frame's sure matches fit the line
SURE_BY = 1.6             # sure: the best shift's cost this much under the next best
SURE_COST = 8.0           # ...and low
FIT_LEAST = 2000          # fewer sure pixels than this: no fit (the L / R maps are used)
FIT_RESIDUAL = 2.0        # ...or a fit further off than this, px (a map that isn't this clip's, or Dual Eye)
FIT_AGREE = 0.6           # ...or one calling less than this share of what the eyes show near, near


def depth_shift_fit(eye, other, depth, bg_shift: np.ndarray, reach: int, stop=None):
    """(a, b): the shift of the other eye's pixel of depth d into the filled eye, a * d + b, from the L / R
    matches that are sure; None when there aren't enough or the line doesn't hold."""
    import torch
    import torch.nn.functional as F
    from .erase import Stopped
    n, h, w = eye.shape[:3]
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    ds, ss, bs = [], [], []
    with torch.no_grad():
        for t in range(0, n, FIT_EVERY):
            if stop is not None and stop.is_set():
                raise Stopped()
            a = torch.from_numpy(np.ascontiguousarray(eye[t])).to(dev).float()
            b = torch.from_numpy(np.ascontiguousarray(other[t])).to(dev).float()
            d = torch.from_numpy(np.ascontiguousarray(depth[t])).to(dev).float()
            costs = []
            shifts = list(range(-reach, reach + 1, 2))
            for s in shifts:
                c = torch.full((h, w), 1e4, device=dev)
                lo, hi = max(0, s), min(w, b.shape[1] + s)
                if hi > lo:
                    c[:, lo:hi] = (a[:, lo:hi] - b[:, lo - s:hi - s]).abs().mean(-1)
                costs.append(c)
            cost = F.avg_pool2d(torch.stack(costs)[None], 9, 1, 4, count_include_pad=False)[0]
            top = cost.topk(2, dim=0, largest=False)
            sure = (top.values[1] > SURE_BY * top.values[0]) & (top.values[0] < SURE_COST)
            shift = top.indices[0].float() * 2 - reach
            past = int(np.ceil(float(bg_shift[t]))) + 4          # beyond the hole: real picture
            sure[:, :past] = False
            yy, xx = torch.nonzero(sure, as_tuple=True)
            if not len(yy):
                continue
            sv = shift[yy, xx]
            lx = (xx.float() - sv).round().long().clamp(0, d.shape[1] - 1)
            ds.append(d[yy, lx].cpu().numpy())
            ss.append(sv.cpu().numpy())
            bs.append(np.full(len(yy), float(bg_shift[t]), np.float64))
    if not ds:
        return None
    # float64: in float32 polyfit's solve broke down over a few hundred thousand pixels (a line of slope ~1)
    ds, ss = np.concatenate(ds).astype(np.float64), np.concatenate(ss).astype(np.float64)
    bs = np.concatenate(bs)
    if len(ds) < FIT_LEAST or float(ds.std()) < 1e-4:
        return None
    fit = np.polyfit(ds, ss, 1)
    inl = np.ones(len(ds), bool)
    for _ in range(5):
        res = np.abs(np.polyval(fit, ds) - ss)
        inl = res < max(4.0, float(np.percentile(res, 60)))
        if inl.sum() < FIT_LEAST // 2:
            return None
        fit = np.polyfit(ds[inl], ss[inl], 1)
    resid = float(np.median(np.abs(np.polyval(fit, ds[inl]) - ss[inl])))
    logger.info(f"  Depth map: shift = {fit[0]:.1f} x depth + {fit[1]:.1f} ({int(inl.sum())} sure pixels, "
                f"{resid:.1f} px off)")
    if resid > FIT_RESIDUAL:
        return None
    # what the eyes clearly show as near, the line must call near too: a flat line fits a clip that is mostly
    # background closely and misses every subject (another clip's map, a map of the wrong eye)
    gap = np.maximum(MIN_GAP, NEAR_BY * bs)
    seen_near = ss < bs - gap
    if seen_near.mean() > 0.005:
        agree = float((np.polyval(fit, ds[seen_near]) < bs[seen_near] - gap[seen_near]).mean())
        if agree < FIT_AGREE:
            logger.info(f"  Depth map: it calls only {agree:.0%} of what the eyes show near, near")
            return None
    return float(fit[0]), float(fit[1])


def near_maps_from_depth(eye, other, depth, bg_shift: np.ndarray, reach: int,
                         progress: Optional[Callable[[float], None]] = None, stop=None) -> Optional[np.ndarray]:
    """near_maps' answer from the clip's depth map: `depth` (n, h, >= w) float 0..1, lined up with `other` (the
    eye the map is of). Each of its pixels moved into the filled eye by its fitted shift, the nearer winning;
    near where that shift is the background's minus the same gap. None when the fit doesn't hold (the caller
    falls back to near_maps)."""
    import cv2
    import torch
    from .erase import Stopped
    fit = depth_shift_fit(eye, other, depth, bg_shift, reach, stop)
    if fit is None:
        logger.info("  Depth map: its depth doesn't line up with this clip's two eyes; measuring from them instead")
        return None
    a, b = fit
    n, h, w = eye.shape[:3]
    dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    out = np.zeros((n, h, w), np.uint8)
    with torch.no_grad():
        for t in range(n):
            if stop is not None and stop.is_set():
                raise Stopped()
            sb = float(bg_shift[t])
            if sb < 1:
                continue
            d = torch.from_numpy(np.ascontiguousarray(depth[t])).to(dev).float()
            s = a * d + b                                                   # (h, ow) each source pixel's shift
            xr = (torch.arange(d.shape[1], device=dev).float()[None, :] + s).round().long()
            ok = (xr >= 0) & (xr < w)
            rows = torch.arange(h, device=dev)[:, None].expand_as(xr)
            flat = (rows[ok] * w + xr[ok])
            landed = torch.full((h * w,), float("inf"), device=dev)
            landed.scatter_reduce_(0, flat, s[ok], reduce="amin")           # the nearer (smaller shift) wins
            landed = landed.reshape(h, w)
            gap = max(MIN_GAP, NEAR_BY * sb)
            m = ((landed < sb - gap) & torch.isfinite(landed)).byte().cpu().numpy() * 255
            m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
            m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * GROW + 1, 2 * GROW + 1)))
            out[t] = cv2.GaussianBlur(m, (0, 0), SOFT)
            if progress is not None and t % 8 == 0:
                progress(t / n)
    if dev.type == "cuda":
        torch.cuda.empty_cache()
    logger.info(f"  Depth (from the depth map): {float((out > 127).mean()):.1%} of the edge's crop is nearer than "
                f"the background beside the strip; the fill leaves it alone")
    return out
