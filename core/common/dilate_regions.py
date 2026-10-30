"""
dilate_regions.py — Localized hole shift.

(The module and sidecar key keep the "dilate" name for compatibility with
sidecars already written; the operation is a translation, see below.)

What this does
--------------
Moves an individual disocclusion hole along X or Y while keeping its shape.

Dilating a region is NOT the same thing and was the first attempt here: growing
the occluder expands it symmetrically, so the hole gets wider rather than
moving. Measured on a synthetic silhouette, dilate offsets of +2/+4 took an
8px hole to 10px and 12px, while translating the same region by +2/+4 moved the
hole exactly 2 and 4 px with the width pinned at 8.

So a region translates the (already globally dilated) depth inside its mask:

    depth_in_region(x, y) = depth(x - shift_x, y - shift_y)

Shifting the occluder's depth moves both edges of the hole by the same amount,
which is a pure translation. Shift values are in pixels and map 1:1 to how far
the hole moves. Everything outside a region is untouched, so a clip with no
regions renders bit-identically to before.

Why regions are stored in SOURCE space
--------------------------------------
Dilation runs in `process_depth_batch`, which is *before* disparity exists --
disparity is derived from the dilated depth. So a hole (which lives in warped
TARGET space) cannot be mapped back to its causing depth edge at apply time.

Instead the mapping happens once, at click time in the GUI, where the previous
frame's disparity is already cached: `hole_mask_to_source_polys` gathers the
clicked hole back through the disparity and returns a SOURCE-space polygon.
From then on the region needs no disparity at all.

A hole has no source pixels landing inside it -- that is what makes it a hole --
so the gather is run on a slightly widened copy of the hole. What it catches are
the source pixels flanking the hole, i.e. the silhouette that created it, which
is exactly the edge that needs re-dilating.

Blending
--------
Two dilations of the same depth are blended by a feathered mask. Measured on a
synthetic silhouette, the rim of that blend produces a maximum warp gap of 1.00
(i.e. no stretch smear), so the boundary is safe. Feather above ~3px starts
fragmenting the hole it is meant to move, hence MAX_FEATHER.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from core.common.tolerant import load_each

try:
    import cv2
except Exception:  # pragma: no cover
    cv2 = None  # type: ignore

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
SIDECAR_KEY = "dilate_regions"

# Shift in PIXELS at the processing resolution, scaled by res_scale so a region
# tuned on the low-res preview moves the hole the same relative distance at full
# res. Positive X moves the hole right, positive Y moves it down.
MIN_OFFSET = -20.0
MAX_OFFSET = 20.0

# Feather is in SOURCE pixels, and is a genuine tradeoff:
#   * 0.0 gives an exact translation.
#   * >0 softens the region boundary, but blends shifted and unshifted depth at
#     the polygon's edges, which can smear the hole's outline there.
#   * >3 starts splitting the very hole it is supposed to move.
# The default is small: the boundary usually sits in flat background where a
# hard edge costs nothing, so softness is only worth paying for when the region
# edge crosses real depth structure.
DEFAULT_FEATHER = 0.8
MAX_FEATHER = 3.0


# ----------------------------------------------------------------------------
# Data model
# ----------------------------------------------------------------------------

@dataclass
class Region:
    """A source-space area whose depth is translated to move one hole.

    `polys` are normalized 0..1 contours, so a region survives the resolution
    change between the low-res preview and the full-res render.
    """
    polys: List[List[Tuple[float, float]]] = field(default_factory=list)
    offset_x: float = 0.0
    offset_y: float = 0.0
    feather: float = DEFAULT_FEATHER
    enabled: bool = True
    label: str = ""
    # Normalized TARGET-space centroid of the hole this region came from.
    # Identification only -- never used by apply -- so the GUI can tell whether
    # a clicked hole is already selected without rasterizing anything.
    seed: Tuple[float, float] = (-1.0, -1.0)

    def is_active(self) -> bool:
        """Whether this region actually changes the depth.

        A selected region with zero shift is still stored and still shown as
        selected in the GUI -- it just has nothing to do yet.
        """
        return (
            self.enabled
            and bool(self.polys)
            and (abs(self.offset_x) > 1e-5 or abs(self.offset_y) > 1e-5)
        )

    def to_dict(self) -> dict:
        return {
            "polys": [[[round(float(x), 5), round(float(y), 5)] for x, y in p]
                      for p in self.polys],
            "offset_x": round(float(self.offset_x), 4),
            "offset_y": round(float(self.offset_y), 4),
            "feather": round(float(self.feather), 3),
            "enabled": bool(self.enabled),
            "label": str(self.label),
            "seed": [round(float(self.seed[0]), 5), round(float(self.seed[1]), 5)],
        }

    @staticmethod
    def from_dict(d: dict) -> "Region":
        return Region(
            polys=[[(float(p[0]), float(p[1])) for p in poly]
                   for poly in d.get("polys", [])],
            offset_x=float(np.clip(float(d.get("offset_x", 0.0)), MIN_OFFSET, MAX_OFFSET)),
            offset_y=float(np.clip(float(d.get("offset_y", 0.0)), MIN_OFFSET, MAX_OFFSET)),
            feather=float(np.clip(float(d.get("feather", DEFAULT_FEATHER)), 0.0, MAX_FEATHER)),
            enabled=bool(d.get("enabled", True)),
            label=str(d.get("label", "")),
            seed=tuple(d.get("seed", (-1.0, -1.0)))[:2],  # type: ignore[arg-type]
        )


@dataclass
class Keyframe:
    """Regions valid from `frame` until the next keyframe."""
    frame: int = 0
    regions: List[Region] = field(default_factory=list)
    enabled: bool = True

    def to_dict(self) -> dict:
        return {
            "frame": int(self.frame),
            "enabled": bool(self.enabled),
            "regions": [r.to_dict() for r in self.regions],
        }

    @staticmethod
    def from_dict(d: dict) -> "Keyframe":
        return Keyframe(
            frame=int(d.get("frame", 0)),
            enabled=bool(d.get("enabled", True)),
            regions=load_each(d.get("regions", []), Region.from_dict, "Dilate region"),
        )


@dataclass
class DilateRegions:
    """Per-clip localized dilation. Serializes into the .fssidecar.

    `keyed` False (user, 2026-10-30: the default): one set of regions for the whole clip -- the
    first keyframe, whatever its frame. True: keyframes as before, each from its frame to the next.
    A sidecar written before this has no "keyed" and stays keyed, so it renders as it did."""
    version: int = SCHEMA_VERSION
    enabled: bool = True
    keyframes: List[Keyframe] = field(default_factory=list)
    keyed: bool = False

    # ---- keyframe access -------------------------------------------------

    def keyframe_at(self, frame: int) -> Optional[Keyframe]:
        """Most recent keyframe at or before `frame` (step interpolation); not keyed, the one set.

        Step rather than linear: region topology can differ between keyframes,
        and there is nothing coherent to interpolate between a 2-region set and
        a 5-region one.
        """
        if not self.keyed:
            return min(self.keyframes, key=lambda k: k.frame) if self.keyframes else None
        best = None
        for kf in self.keyframes:
            if kf.frame <= frame and (best is None or kf.frame > best.frame):
                best = kf
        return best

    def ensure_keyframe(self, frame: int) -> Keyframe:
        """Keyframe exactly at `frame`, created as a copy of the active one; not keyed, the one set
        (made at frame 0 when there is none)."""
        if not self.keyed:
            kf = self.keyframe_at(0)
            if kf is None:
                kf = Keyframe(frame=0, enabled=True, regions=[])
                self.keyframes.append(kf)
            return kf
        for kf in self.keyframes:
            if kf.frame == frame:
                return kf
        src = self.keyframe_at(frame)
        new = Keyframe(
            frame=frame,
            enabled=True,
            regions=[Region.from_dict(r.to_dict()) for r in (src.regions if src else [])],
        )
        self.keyframes.append(new)
        self.keyframes.sort(key=lambda k: k.frame)
        return new

    def set_keyed(self, on: bool, frame: int = 0) -> int:
        """Keyframes on or off. Off: the set in force at `frame` becomes the whole clip's, from frame 0,
        and the other keyframes go. Returns how many were removed."""
        on = bool(on)
        if on == self.keyed:
            return 0
        if on:
            self.keyed = True
            return 0
        if not self.keyframes:
            self.keyed = False
            return 0
        self.keyed = True                       # read the set in force here as keyframes saw it
        keep = self.keyframe_at(frame) or min(self.keyframes, key=lambda k: k.frame)
        removed = len(self.keyframes) - 1
        keep.frame = 0
        self.keyframes = [keep]
        self.keyed = False
        return removed

    def delete_keyframe(self, frame: int) -> bool:
        before = len(self.keyframes)
        self.keyframes = [k for k in self.keyframes if k.frame != frame]
        return len(self.keyframes) != before

    def is_empty(self) -> bool:
        """True when there is nothing worth persisting.

        Deliberately ignores `enabled` and per-region offsets: a disabled
        region still holds the user's selection, and treating it as empty would
        make the sidecar writer delete it on the next save.
        """
        return not any(kf.regions for kf in self.keyframes)

    def active_at(self, frame: int) -> bool:
        if not self.enabled:
            return False
        kf = self.keyframe_at(frame)
        return bool(kf and kf.enabled and any(r.is_active() for r in kf.regions))

    # ---- serialization ---------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "enabled": bool(self.enabled),
            "keyed": bool(self.keyed),
            "keyframes": [k.to_dict() for k in self.keyframes],
        }

    @staticmethod
    def from_dict(d: Optional[dict]) -> "DilateRegions":
        if not d:
            return DilateRegions()
        try:
            return DilateRegions(
                version=int(d.get("version", SCHEMA_VERSION)),
                enabled=bool(d.get("enabled", True)),
                # a malformed keyframe or region is skipped, the rest kept (bug sweep
                # 2026-10-09 A11: one bad entry emptied the set, and the next save
                # deleted it from the sidecar)
                keyframes=load_each(d.get("keyframes", []), Keyframe.from_dict, "Dilate keyframe"),
                # written before "keyed" (2026-10-30): keyframes, as it was made
                keyed=bool(d.get("keyed", True)),
            )
        except Exception as e:
            logger.warning(f"DilateRegions: malformed data ignored ({e}).")
            return DilateRegions()


# ----------------------------------------------------------------------------
# Rasterization
# ----------------------------------------------------------------------------

def rasterize_region(region: Region, h: int, w: int) -> np.ndarray:
    """Render one region's polygons into a feathered 0..1 float32 mask."""
    m = np.zeros((h, w), np.uint8)
    if cv2 is None or not region.polys:
        return m.astype(np.float32)

    for poly in region.polys:
        if len(poly) < 3:
            continue
        pts = np.array([[int(round(x * w)), int(round(y * h))] for x, y in poly],
                       dtype=np.int32)
        cv2.fillPoly(m, [pts], 255)

    out = m.astype(np.float32) / 255.0
    fe = float(np.clip(region.feather, 0.0, MAX_FEATHER))
    if fe > 0.05 and out.max() > 0.0:
        out = np.clip(cv2.GaussianBlur(out, (0, 0), fe), 0.0, 1.0)
    return out


_RASTERS: Dict[Tuple[str, float, int, int], np.ndarray] = {}


def _raster_cached(region: Region, h: int, w: int) -> np.ndarray:
    """rasterize_region, kept by the region's outline and feather (2026-10-30: re-drawn ~30 ms at 1080p on
    every knob step, though a shift doesn't change it). Read-only: callers make new arrays from it."""
    key = (json.dumps(region.polys), round(float(region.feather), 3), int(h), int(w))
    m = _RASTERS.get(key)
    if m is None:
        m = rasterize_region(region, h, w)
        if len(_RASTERS) >= 32:
            _RASTERS.pop(next(iter(_RASTERS)))
        _RASTERS[key] = m
    return m


def build_offset_groups(kf: Keyframe, h: int, w: int
                        ) -> List[Tuple[np.ndarray, float, float]]:
    """Composite regions into (mask, offset_x, offset_y) groups.

    Regions sharing the same shift are merged into one mask so the translation
    runs once per distinct shift rather than once per region. Later regions win
    where they overlap, matching paint-tool behaviour.
    """
    if not kf.enabled:
        return []

    groups: Dict[Tuple[float, float], np.ndarray] = {}
    claimed = np.zeros((h, w), np.float32)

    # Walk in reverse so a later region takes precedence, then subtract what
    # has already been claimed.
    for region in reversed(kf.regions):
        if not region.is_active():
            continue
        m = _raster_cached(region, h, w)
        if m.max() <= 0.0:
            continue
        m = np.clip(m - claimed, 0.0, 1.0)
        if m.max() <= 0.0:
            continue
        claimed = np.clip(claimed + m, 0.0, 1.0)

        key = (round(float(region.offset_x), 4), round(float(region.offset_y), 4))
        if key in groups:
            groups[key] = np.maximum(groups[key], m)
        else:
            groups[key] = m

    return [(m, ox, oy) for (ox, oy), m in groups.items()]


# ----------------------------------------------------------------------------
# Apply
# ----------------------------------------------------------------------------

#: build_offset_groups' masks, kept across renders (2026-10-30: re-drawn on every preview frame, ~30 ms at
#: 1080p): keyed by the keyframe's content and the size, so any edit makes a new entry
_GROUPS: Dict[Tuple[str, int, int], List[Tuple[np.ndarray, float, float]]] = {}
_GROUPS_T: Dict[Tuple[str, int, int, str], list] = {}
_GROUPS_MAX = 8


def _groups_cached(kf: Keyframe, h: int, w: int, device: Optional[str] = None) -> list:
    """build_offset_groups for `kf`, from the cache when its regions are unchanged; with `device`, the
    masks as tensors there (moved once)."""
    key = (json.dumps(kf.to_dict(), sort_keys=True), int(h), int(w))
    groups = _GROUPS.get(key)
    if groups is None:
        groups = build_offset_groups(kf, h, w)
        if len(_GROUPS) >= _GROUPS_MAX:
            _GROUPS.pop(next(iter(_GROUPS)))
        _GROUPS[key] = groups
    if device is None:
        return groups
    tkey = key + (str(device),)
    out = _GROUPS_T.get(tkey)
    if out is None:
        import torch
        out = [(torch.from_numpy(m).to(device), ox, oy) for m, ox, oy in groups]
        if len(_GROUPS_T) >= _GROUPS_MAX:
            _GROUPS_T.pop(next(iter(_GROUPS_T)))
        _GROUPS_T[tkey] = out
    return out


def _translate_t(plane: Any, dx: float, dy: float) -> Any:
    """_translate on the GPU (2026-10-30: the region shift was per-frame OpenCV, the plane copied off the
    GPU and back): dst(x, y) = src(x - dx, y - dy), bilinear, the edge repeated -- as warpAffine does,
    the offsets rounded to its 1/32 px, so the two agree to float rounding."""
    import math
    import torch
    h, w = int(plane.shape[-2]), int(plane.shape[-1])
    dx, dy = round(float(dx) * 32.0) / 32.0, round(float(dy) * 32.0) / 32.0
    ix, iy = math.floor(dx), math.floor(dy)
    fx, fy = dx - ix, dy - iy
    xs = torch.arange(w, device=plane.device)
    ys = torch.arange(h, device=plane.device)

    def shifted(sx: int, sy: int):
        return plane.index_select(-1, (xs - sx).clamp_(0, w - 1)).index_select(-2, (ys - sy).clamp_(0, h - 1))
    top = shifted(ix, iy) if fx <= 0 else shifted(ix, iy) * (1.0 - fx) + shifted(ix + 1, iy) * fx
    if fy <= 0:
        return top
    bot = shifted(ix, iy + 1) if fx <= 0 else shifted(ix, iy + 1) * (1.0 - fx) + shifted(ix + 1, iy + 1) * fx
    return top * (1.0 - fy) + bot * fy


def _translate(plane: np.ndarray, dx: float, dy: float) -> np.ndarray:
    """Shift a 2D depth plane by (dx, dy) pixels, sub-pixel accurate.

    BORDER_REPLICATE rather than zero fill: a zero would read as far depth and
    manufacture a bogus edge at the frame boundary.
    """
    if cv2 is None:
        return plane
    m = np.float32([[1.0, 0.0, float(dx)], [0.0, 1.0, float(dy)]])
    return cv2.warpAffine(plane, m, (plane.shape[1], plane.shape[0]),
                          flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def apply_regional_shift(tensor_4d: Any,
                         global_x: float,
                         global_y: float,
                         correction: Optional[DilateRegions],
                         frame_indices: Sequence[int],
                         dilate_fn: Callable[[Any, float, float], Any],
                         res_scale: float = 1.0) -> Any:
    """Globally dilate a [B,1,H,W] batch, then translate depth inside regions.

    `dilate_fn(tensor, kx, ky)` is the existing global dilation and is applied
    exactly as before; regions then shift the result within their own masks, so
    a region with zero shift is indistinguishable from no region at all.

    `global_x/global_y` are the already-decoded, already-res-scaled kernel
    sizes. Region shifts are scaled by `res_scale` here so a region tuned on the
    low-res preview moves the hole the same relative distance at full res.

    Falls back to a plain global dilation whenever no frame in the batch has an
    active region, so the existing code path is bit-identical when unused.
    """
    if correction is None or not correction.enabled:
        return dilate_fn(tensor_4d, global_x, global_y)

    idxs = list(frame_indices)
    if not any(correction.active_at(f) for f in idxs):
        return dilate_fn(tensor_4d, global_x, global_y)

    try:
        base = dilate_fn(tensor_4d, global_x, global_y)
        h, w = int(tensor_4d.shape[-2]), int(tensor_4d.shape[-1])

        # The masks depend only on the keyframe and the size: kept across renders (_groups_cached).
        # A batch on the GPU shifts there, without the plane leaving it (2026-10-30).
        on_gpu = False
        try:
            import torch
            on_gpu = isinstance(base, torch.Tensor) and base.is_cuda
        except Exception:
            pass

        for bi, f in enumerate(idxs):
            if not correction.active_at(f):
                continue
            kf = correction.keyframe_at(f)
            if kf is None:
                continue
            if on_gpu:
                groups_t = _groups_cached(kf, h, w, str(base.device))
                if not groups_t:
                    continue
                plane_t = base[bi, 0].float()
                for mask_t, ox, oy in groups_t:
                    dx, dy = ox * res_scale, oy * res_scale
                    m_t = mask_t
                    if abs(dx) > 0.5 or abs(dy) > 0.5:
                        m_t = torch.maximum(mask_t, _translate_t(mask_t, dx, dy))
                    plane_t = plane_t * (1.0 - m_t) + _translate_t(plane_t, dx, dy) * m_t
                base[bi, 0] = plane_t.to(base.dtype)
                continue
            groups = _groups_cached(kf, h, w)
            if not groups:
                continue

            plane = _plane_of(base, bi)
            for mask, ox, oy in groups:
                dx, dy = ox * res_scale, oy * res_scale
                # The mask must cover where the occluder is GOING, not just
                # where it is: otherwise a shift larger than the region's
                # margin clips the occluder at the mask edge and the hole
                # narrows instead of moving. Union with the shifted mask is
                # exactly the swept area, and stays directional (it does not
                # grow the region on the side being moved away from).
                m = mask
                if abs(dx) > 0.5 or abs(dy) > 0.5:
                    m = np.maximum(mask, _translate(mask, dx, dy))
                shifted = _translate(plane, dx, dy)
                plane = plane * (1.0 - m) + shifted * m
            _set_plane(base, bi, plane)

        return base

    except Exception as e:
        logger.error(f"apply_regional_dilate failed: {e}", exc_info=True)
        return dilate_fn(tensor_4d, global_x, global_y)


def _plane_of(batch: Any, bi: int) -> np.ndarray:
    """Extract batch element `bi` as a 2D float32 numpy plane."""
    try:
        import torch

        if isinstance(batch, torch.Tensor):
            return batch[bi, 0].detach().cpu().numpy().astype(np.float32, copy=True)
    except Exception:
        pass
    arr = np.asarray(batch)
    return arr[bi, 0].astype(np.float32, copy=True)


def _set_plane(batch: Any, bi: int, plane: np.ndarray) -> None:
    """Write a 2D plane back into batch element `bi`."""
    try:
        import torch

        if isinstance(batch, torch.Tensor):
            batch[bi, 0] = torch.from_numpy(plane).to(device=batch.device, dtype=batch.dtype)
            return
    except Exception:
        pass
    batch[bi, 0] = plane


# ----------------------------------------------------------------------------
# Hole selection helpers (GUI side, click time)
# ----------------------------------------------------------------------------

def find_holes(occlusion_mask: np.ndarray,
               min_area_px: int = 12) -> List[Tuple[int, np.ndarray, Tuple[int, int, int, int]]]:
    """Split an occlusion mask into selectable components.

    Returns [(component_id, boolean_mask, (x, y, w, h))] sorted largest first,
    which is the order the GUI paints its highlights in.
    """
    if cv2 is None:
        return []
    try:
        om = np.asarray(occlusion_mask)
        if om.ndim == 4:
            om = om[0, 0]
        elif om.ndim == 3:
            om = om[0] if om.shape[0] <= 4 else om[..., 0]
        binary = (om > 0.5).astype(np.uint8)
        if not binary.any():
            return []

        n, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
        out = []
        for i in range(1, n):
            x, y, w, h, area = stats[i]
            if area < min_area_px:
                continue
            # compared inside its box only (2026-10-30: a whole-frame compare per hole)
            m = np.zeros(labels.shape, bool)
            m[y:y + h, x:x + w] = labels[y:y + h, x:x + w] == i
            out.append((i, m, (int(x), int(y), int(w), int(h)), int(area)))
        out.sort(key=lambda r: r[3], reverse=True)
        return [(i, m, bb) for (i, m, bb, _a) in out]
    except Exception as e:
        logger.error(f"find_holes failed: {e}", exc_info=True)
        return []


def hole_mask_to_source_polys(hole_mask: np.ndarray,
                              disp_px: np.ndarray,
                              margin_px: int = 8,
                              max_points: int = 40,
                              span_cap_frac: float = 0.12,
                              pad_x: int = 6,
                              pad_y: int = 8) -> List[List[Tuple[float, float]]]:
    """Map a TARGET-space hole to SOURCE-space polygons via the disparity.

    A hole contains no source pixels by construction, so the mask is widened by
    `margin_px` before the gather. What comes back are the source pixels that
    land beside the hole -- the silhouette that created it -- which is the edge
    that needs re-dilating.

    Returns normalized 0..1 contours, or [] if nothing mapped.

    Note on what the gather actually returns: the occluder's own pixels do NOT
    land beside the hole -- they jump to wherever the near object is drawn, far
    away. What lands beside the hole is background on either side, so the gather
    comes back as two bands with a gap between them, and that gap is precisely
    the occluder that vacated. Dilating the background bands does almost
    nothing; dilating the occluder is what moves the hole. So the bands are
    bridged per row before contours are taken.
    """
    if cv2 is None:
        return []
    try:
        hm = np.asarray(hole_mask).astype(np.float32)
        if hm.ndim > 2:
            hm = hm.squeeze()
        d = np.asarray(disp_px, dtype=np.float32)
        if d.ndim == 4:
            d = d[0, 0]
        elif d.ndim == 3:
            d = d[0]
        if d.shape[:2] != hm.shape[:2]:
            d = cv2.resize(d, (hm.shape[1], hm.shape[0]), interpolation=cv2.INTER_NEAREST)

        h, w = hm.shape[:2]
        if margin_px > 0:
            hm = cv2.dilate(hm, np.ones((1, 2 * margin_px + 1), np.uint8))

        # Gather: source pixel x lands at x + flow(x), flow = -disp.
        xs = np.arange(w, dtype=np.float32)[None, :]
        tx = np.rint(xs - d)
        inside = (tx >= 0.0) & (tx <= float(w - 1))
        idx = np.clip(tx, 0, w - 1).astype(np.int32)
        src = np.where(inside, np.take_along_axis(hm, idx, axis=1), 0.0)

        binary = (src > 0.5).astype(np.uint8)
        if not binary.any():
            return []

        # Bridge each row from its first to its last gathered column, so the
        # occluder sitting between the two background bands is included. Capped
        # so a hole that happens to gather two unrelated bands cannot swallow
        # the space between them.
        span_cap = max(8, int(round(span_cap_frac * w)))
        rows = np.where(binary.any(axis=1))[0]
        for y in rows:
            cols = np.flatnonzero(binary[y])
            lo, hi = int(cols[0]), int(cols[-1])
            if (hi - lo) <= span_cap:
                binary[y, lo:hi + 1] = 1

        # Close small gaps so a band broken by rounding becomes one contour.
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))

        # Grow the region so the local dilation has somewhere to expand INTO.
        # Without this the region hugs the hole's own rows, and a Y offset
        # pushes the occluder into rows outside the mask where the global
        # result is used instead -- so the Y slider would appear to do nothing.
        py = max(1, int(round(pad_y)))
        px = max(1, int(round(pad_x)))
        binary = cv2.dilate(binary, np.ones((2 * py + 1, 2 * px + 1), np.uint8))

        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        polys: List[List[Tuple[float, float]]] = []
        for c in contours:
            if cv2.contourArea(c) < 4:
                continue
            eps = 0.004 * cv2.arcLength(c, True)
            approx = cv2.approxPolyDP(c, eps, True)
            if len(approx) > max_points:
                approx = cv2.approxPolyDP(c, eps * 3.0, True)
            pts = [(float(p[0][0]) / w, float(p[0][1]) / h) for p in approx]
            if len(pts) >= 3:
                polys.append(pts)
        return polys
    except Exception as e:
        logger.error(f"hole_mask_to_source_polys failed: {e}", exc_info=True)
        return []


# ----------------------------------------------------------------------------
# Sidecar helpers
# ----------------------------------------------------------------------------

def load_from_sidecar(sidecar_path: str) -> DilateRegions:
    try:
        with open(sidecar_path, "r") as f:
            data = json.load(f) or {}
        return DilateRegions.from_dict(data.get(SIDECAR_KEY))
    except FileNotFoundError:
        return DilateRegions()
    except Exception as e:
        logger.warning(f"dilate_regions: could not read from sidecar ({e}).")
        return DilateRegions()


def inject_into_sidecar_dict(data: Dict[str, Any],
                             correction: Optional[DilateRegions]) -> Dict[str, Any]:
    """Write regions into a sidecar dict, or strip the key when empty so
    untouched clips gain no new keys (same convention as boost_*)."""
    if correction is None or correction.is_empty():
        data.pop(SIDECAR_KEY, None)
    else:
        data[SIDECAR_KEY] = correction.to_dict()
    return data
