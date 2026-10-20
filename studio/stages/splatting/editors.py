"""editors.py -- Hole Shift, v1's region editor. (Its sibling, Vertical Warp
Fix, is gone from v2: the Depth Map Editor fixes vertical warp at the source,
user 2026-09-28.)

v1 builds it as a Tk Toplevel whose logic lives in closures inside one long
method (splatting_gui._dilreg_open), so unlike the rest of the
preview they cannot be generated. This is that logic, rule for rule, over the
generated engine's own state -- ``dilate_regions``, the
caches every preview render leaves (``_dilreg_last_splat``,
``_dilreg_last_occ``, ``_warp_last_disp``) and v1's sidecar save. The
window is QML (HoleShiftWindow.qml); every call here
runs on the preview's worker thread, like everything else that touches the
engine.

Coordinates arrive normalised (0..1 of the frame): QML knows where the
picture is drawn, this file knows the frame's pixel size.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import cv2
import numpy as np
from PIL import Image

from core.common import dilate_regions

logger = logging.getLogger("splatting.v2.preview")

NO_SPLAT = ("No splat available yet.\n\nLoad a frame in a mode that warps (Splat Result, an anaglyph, "
            "or SBS), then reopen this window.")

#: v1 _dilreg_open: the D-pad's step, and the screen/stored y convention
#: (offset_y is stored pixel-down; the arrows are spatial, readout negated).
HS_STEP = 0.1
HS_Y_SIGN = -1.0


def _apply(eng) -> None:
    """v1 _dilreg_apply: re-render the preview, then persist."""
    try:
        if eng.previewer.source_readers:
            eng.previewer.update_preview()
    except Exception:
        logger.debug("Editor: preview re-render failed", exc_info=True)
    try:
        if eng.auto_save_sidecar_var.get():
            eng._save_current_sidecar_data(is_auto_save=True)
    except Exception:
        logger.debug("Editor: sidecar auto-save failed", exc_info=True)


# =============================================================================
# Hole Shift (v1 _dilreg_open)
# =============================================================================


class HoleShift:
    def __init__(self, eng):
        self.eng = eng
        self.frame = 0
        self.holes: list = []
        self.occ = None
        self.disp = None
        self.open = False

    # ---- opening -------------------------------------------------------------

    def start(self) -> Optional[str]:
        """None, or the message v1 shows instead of opening."""
        e = self.eng
        if e._dilreg_last_occ is None or e._warp_last_disp is None or e._dilreg_last_splat is None:
            return NO_SPLAT
        holes = dilate_regions.find_holes(e._dilreg_last_occ)
        if not holes:
            return "No disocclusion holes found on this frame."
        self.frame = int(getattr(e.previewer, "last_loaded_frame_index", 0) or 0)
        self.occ, self.disp, self.holes = e._dilreg_last_occ, e._warp_last_disp, holes
        self.open = True
        return None

    # ---- helpers (v1's closures) -----------------------------------------------

    # Bug sweep 2026-10-09 (A6): the frame is the one the preview shows (render
    # follows the playhead), and only an EDIT makes a keyframe there -- looking
    # reads the one in force (v1 made a copy keyframe on every frame viewed).
    def _kf(self, create: bool = True):
        if create:
            return self.eng.dilate_regions.ensure_keyframe(self.frame)
        return self.eng.dilate_regions.keyframe_at(self.frame)

    def _regions(self, create: bool) -> list:
        kf = self._kf(create)
        return list(kf.regions) if kf is not None else []

    def _selected(self, create: bool = True) -> list:
        return [r for r in self._regions(create) if r.enabled]

    def _region_for(self, seed, tol=0.035, create: bool = True):
        best, best_d = None, tol
        for r in self._regions(create):
            d = max(abs(r.seed[0] - seed[0]), abs(r.seed[1] - seed[1]))
            if d < best_d:
                best, best_d = r, d
        return best

    def _seed_of(self, hole):
        _i, _mask, (x, y, w_, h_) = hole
        oh, ow = self.occ.shape[:2]
        return ((x + w_ / 2.0) / ow, (y + h_ / 2.0) / oh)

    # ---- the picture (v1 _render) -------------------------------------------------

    def render(self) -> Optional[Image.Image]:
        e = self.eng
        live_occ, live_splat = e._dilreg_last_occ, e._dilreg_last_splat
        if live_occ is None or live_splat is None:
            return None
        self.frame = int(getattr(e.previewer, "last_loaded_frame_index", self.frame) or 0)  # the frame shown
        self.occ = live_occ
        if e._warp_last_disp is not None:
            self.disp = e._warp_last_disp
        self.holes = dilate_regions.find_holes(live_occ)
        h, w = live_occ.shape[:2]
        if live_splat.shape[:2] != (h, w):
            live_splat = cv2.resize(live_splat, (w, h), interpolation=cv2.INTER_AREA)
        rgb = live_splat.astype(np.float32).copy()
        if rgb.ndim == 2:
            rgb = np.repeat(rgb[..., None], 3, axis=2)
        for hole in self.holes:
            _i, mask, _bb = hole
            sel = self._region_for(self._seed_of(hole), create=False)
            if sel is not None and sel.enabled:
                colour, alpha = np.array([60.0, 235.0, 110.0], np.float32), 0.62
            else:
                colour, alpha = np.array([235.0, 90.0, 60.0], np.float32), 0.40
            rgb[mask] = rgb[mask] * (1.0 - alpha) + colour * alpha
        return Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))

    def state(self) -> Dict[str, Any]:
        regs = self._selected(create=False)

        def common(attr, sign, fmt):
            if not regs:
                return "—"
            vals = {round(sign * float(getattr(r, attr, 0.0)), 4) for r in regs}
            return fmt.format(vals.pop()) if len(vals) == 1 else "mixed"
        def number(attr, sign):
            """The knobs' value: the selection's shared offset, else 0 (none, or mixed)."""
            vals = {round(sign * float(getattr(r, attr, 0.0)), 4) for r in regs}
            return vals.pop() if len(vals) == 1 else 0.0
        return {
            "frame": self.frame,
            "xv": number("offset_x", 1.0),
            "yv": number("offset_y", HS_Y_SIGN),
            "x": common("offset_x", 1.0, "{:+.1f}"),
            "y": common("offset_y", HS_Y_SIGN, "{:+.1f}"),
            "feather": common("feather", 1.0, "{:.1f}"),
            "info": f"{len(regs)} selected · {len(self.eng.dilate_regions.keyframes)} keyframe(s)",
            "enabled": bool(self.eng.dilreg_enabled_var.get()),
        }

    # ---- actions -------------------------------------------------------------

    def click(self, nx: float, ny: float) -> Optional[str]:
        if self.occ is None:
            return None
        h, w = self.occ.shape[:2]
        mx, my = int(nx * w), int(ny * h)
        if not (0 <= mx < w and 0 <= my < h):
            return None
        hit = next((hole for hole in self.holes if hole[1][my, mx]), None)
        if hit is None:
            return None
        seed = self._seed_of(hit)
        existing = self._region_for(seed)
        if existing is not None:
            self._kf().regions.remove(existing)
        else:
            polys = dilate_regions.hole_mask_to_source_polys(hit[1].astype(np.float32), self.disp)
            if not polys:
                return "Hole Shift: that hole did not map to a depth edge."
            self._kf().regions.append(dilate_regions.Region(
                polys=polys, offset_x=0.0, offset_y=0.0, feather=dilate_regions.DEFAULT_FEATHER, seed=seed))
        _apply(self.eng)
        return None

    def nudge(self, attr: str, direction: int) -> Optional[str]:
        """The D-pad and Feather +/-: relative to each selected zone's own value."""
        regs = self._selected()
        if not regs:
            return "Hole Shift: select a hole first."
        if attr == "offset_y":
            delta, lo, hi = HS_Y_SIGN * HS_STEP * direction, dilate_regions.MIN_OFFSET, dilate_regions.MAX_OFFSET
        elif attr == "offset_x":
            delta, lo, hi = HS_STEP * direction, dilate_regions.MIN_OFFSET, dilate_regions.MAX_OFFSET
        else:
            delta, lo, hi = HS_STEP * direction, 0.0, dilate_regions.MAX_FEATHER
        for r in regs:
            cur = float(getattr(r, attr, 0.0))
            setattr(r, attr, float(np.clip(round(cur + delta, 4), lo, hi)))
        return None

    def set_offset(self, attr: str, value: float) -> Optional[str]:
        """The X / Y knobs (user, 2026-09-27, in place of v1's D-pad): every
        selected hole to this offset. Y is as shown -- up is positive -- so
        it goes through HS_Y_SIGN like the D-pad's."""
        if attr not in ("offset_x", "offset_y"):
            raise ValueError(f"Hole Shift: no offset {attr!r}")
        regs = self._selected()
        if not regs:
            return "Hole Shift: select a hole first."
        v = float(value) * (HS_Y_SIGN if attr == "offset_y" else 1.0)
        v = float(np.clip(round(v, 4), dilate_regions.MIN_OFFSET, dilate_regions.MAX_OFFSET))
        for r in regs:
            setattr(r, attr, v)
        return None

    def commit(self) -> None:
        """v1 _commit_sliders, after the knobs' or Feather's debounce."""
        _apply(self.eng)

    def add_key(self) -> None:
        self.eng.dilate_regions.ensure_keyframe(int(getattr(self.eng.previewer, "last_loaded_frame_index", 0) or 0))
        try:
            if self.eng.auto_save_sidecar_var.get():
                self.eng._save_current_sidecar_data(is_auto_save=True)
        except Exception:
            logger.debug("Hole Shift: auto-save failed", exc_info=True)

    def del_key(self) -> None:
        if self.eng.dilate_regions.delete_keyframe(self.frame):
            _apply(self.eng)

    def clear_all(self) -> None:
        self.eng.dilate_regions = dilate_regions.DilateRegions()
        _apply(self.eng)

    def set_enabled(self, on: bool) -> None:
        self.eng.dilreg_enabled_var.set(bool(on))
        _apply(self.eng)

    def close(self) -> None:
        self.open = False
        try:
            if self.eng.auto_save_sidecar_var.get():
                self.eng._save_current_sidecar_data(is_auto_save=True)
        except Exception:
            logger.debug("Hole Shift: auto-save on close failed", exc_info=True)
