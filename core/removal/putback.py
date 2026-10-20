"""putback.py -- an erased object back in the 3D picture, at a depth of its own
(user, 2026-10-06: "save the masked object to place back into 3D space, like
a translucent or rainy window").

Erase from Clip (erase.py) leaves everything this needs in the clip's
edit_history/: the original clip, the repaint mask (where ROSE changed the
clip, object and side effects) and the object's own mask. The clip itself is
the erased one, so DepthCrafter, Splatting and Inpainting never see the
object. Merging then lays it back over BOTH finished eyes:

  * Over -- the original's pixels inside the object's own mask, laid over:
    for a solid object (a pole, a sign) at a depth chosen for it;
  * Add  -- what erasing took away (original - erased) inside the repaint
    mask, added back: for glass, reflections, rain, smoke -- anything seen
    THROUGH, so the scene behind keeps its own depth.

Depth is a constant PARALLAX, in percent of the eye's width, as Splatting
shifts: the left eye is the source, the right eye has the layer shifted left
by the parallax. 0 = the screen plane (both eyes the same), + in front of
the screen, - behind it. The editor's Sample reads the object's own depth
and turns it into parallax with the clip's Splatting settings
(parallax_for_depth).

Or a parallax per FRAME (user, 2026-10-06): Sample, with a depth factor,
reads the surface's depth under the mask on every frame (x1 = on the
surface, x0.5 = inside it, as a mirror image sits) and saves the track, so
the layer follows the surface when the camera or the object moves. Merging
has no depth maps; the editor works the track out and Merging follows it.

Or the depth from the ORIGINAL clip's depth map, pixel by pixel (user, 2026-10-15: "using the depth
map of the original clip with the object in it for accurate depth"): depth "Depth Map". Erase from
Clip keeps that map (edit_history/<stem>.original_depth.*: the clip's own name would be overwritten by
the erased clip's), and Merging warps the object with it as Splatting warped the scene -- the clean
clip's Splatting settings, its keyframes, the same normalisation and warp (core.putback.engine) --
so a slanted, bowed thing (a cage's bars) keeps its shape in 3D. Over only; Add keeps parallax.
Merging's Put Back controls: Convergence (off the measured depth), Grow (+ dilate / - erode the
object's mask) and Blur (its edge), saved here.

Settings per clip: edit_history/<clip stem>.putback.json (the editor writes
it; Merging reads it). Off until switched on.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Dict, Optional, Tuple

from typing import Callable

import numpy as np

from . import erase as E

logger = logging.getLogger(__name__)

DEFAULTS = {"enabled": False, "mode": "Add", "parallax": 0.0, "opacity": 1.0,
            # Sample's depth factor, and the per-frame parallax it made ([] = `parallax` on every frame)
            "factor": 1.0, "track": [],
            # depth from the original clip's map (user, 2026-10-15), and Merging's controls
            "depth": "Parallax", "original_depth": "", "conv": 0.0, "grow": 0.0, "blur": 0.0,
            # Disparity (user, 2026-10-16): the object's depth strength, % of the scene's (Depth Map only)
            "disp": 100.0,
            # Original Eyes (user, 2026-10-16): where the original clip's merge is (a folder, or a file in it)
            "eyes": ""}
MODES = ("Add", "Over")
DEPTHS = ("Original Eyes", "Original Depth", "Depth Map", "Parallax", "Glass Plane")
#: Over's depths and Add's: Add lays what erasing took away back -- glass's reflections, dust -- at one parallax,
#: or on the glass itself (Glass Plane, user 2026-10-17: each window a plane fitted to the depth map around it)
OVER_DEPTHS = ("Original Eyes", "Original Depth", "Depth Map", "Parallax")
ADD_DEPTHS = ("Glass Plane", "Parallax")
#: the depths warped with the original clip's depth map: fitted onto the clean map's scale ("Depth Map": an
#: object inside the scene's depth, the cage's bars), or at the original's own ("Original Depth", 2026-10-16:
#: a subject nearer than anything left in the clean scene -- a person near the camera -- maps past the clean map's top there and
#: clips flat; at the original's scale he keeps his relief, as the original conversion had him)
MAP_DEPTHS = ("Depth Map", "Original Depth")
#: Merging's controls' ranges: Convergence off the measured depth, Grow (px, + dilate / - erode), Blur (px)
CONV_RANGE, GROW_RANGE, BLUR_RANGE = (-2.0, 2.0), (-10.0, 10.0), (0.0, 10.0)   # Convergence: +-0.5 until 2026-10-16
#: Disparity, % of the clean clip's Splatting disparity: 100 = as the scene, 0 = flat at the screen plane
DISP_RANGE = (0.0, 300.0)

#: (clean clip's core name) -> (its depth map, Splatting's ProcessingSettings) or None: set by the app
#: (Splatting's depth folder and page settings) -- the clean clip's geometry for "Depth Map"
GEOMETRY: Optional[Callable[[str], Optional[Tuple[str, object]]]] = None


# ---- where things are ------------------------------------------------------------------

def _stem(clip: str) -> str:
    return os.path.splitext(os.path.basename(clip))[0]


def settings_path(clip: str) -> str:
    return os.path.join(E.history_dir(clip), f"{_stem(clip)}.putback.json")


def repaint_mask_path(clip: str) -> str:
    return os.path.join(E.history_dir(clip), f"{_stem(clip)}.erase_mask.mkv")


def object_mask_path(clip: str) -> str:
    return os.path.join(E.history_dir(clip), f"{_stem(clip)}.object_mask.mkv")


def erased(clip: str) -> Optional[Dict]:
    """The erase record when `clip` is (still) Erase from Clip's output, else None."""
    try:
        with open(E.record_path(clip), "r", encoding="utf-8") as f:
            rec = json.load(f)
        if rec.get("live") == E._file_id(clip) and os.path.isfile(rec.get("original", "")):
            return rec
    except (OSError, ValueError):
        pass
    return None


def erase_fills_depth(clip: str, method: str) -> bool:
    """Whether the editor's Erase layers fill the depth for `clip` (edit_ops.ERASE_FILLS_DEPTH): ROSE's
    erase does, Dust and Static don't (the glass is seen through). A clip already erased goes by its own
    erase, whatever Erase's Method shows now -- the Method is one setting for every clip, and a ROSE-erased
    clip opened with Dust chosen kept the object's shape in its depth map (review 2026-10-17); a clip
    not erased yet by `method`, the erase it is set up for."""
    rec = erased(clip) if clip else None
    if rec is not None:
        return str(rec.get("method") or "rose").lower() not in ("static", "dust")
    return str(method or "ROSE") == "ROSE"


def modes_for(rec: Optional[Dict]) -> Tuple[str, ...]:
    """The put-back modes an erase can use. A Static erase has no object of
    its own -- its object mask is the whole glass area -- so Over would lay
    the original's glass, reflections and all, over both eyes (bug sweep
    2026-10-09, A9): Add only."""
    return ("Add",) if (rec or {}).get("method") in ("static", "dust") else MODES   # Dust: the same (2026-10-17)


def load_settings(clip: str) -> Dict:
    out = dict(DEFAULTS)
    try:
        with open(settings_path(clip), "r", encoding="utf-8") as f:
            data = json.load(f)
        for k in DEFAULTS:
            if k in data:
                out[k] = data[k]
    except (OSError, ValueError):
        pass
    out["enabled"] = bool(out["enabled"])
    out["mode"] = out["mode"] if out["mode"] in MODES else DEFAULTS["mode"]
    out["parallax"] = float(out["parallax"])
    out["opacity"] = max(0.0, min(1.0, float(out["opacity"])))
    out["factor"] = max(0.0, min(4.0, float(out["factor"])))
    try:
        out["track"] = [float(v) for v in (out["track"] or [])]
    except (TypeError, ValueError):
        out["track"] = []
    out["depth"] = out["depth"] if out["depth"] in DEPTHS else DEFAULTS["depth"]
    out["original_depth"] = str(out["original_depth"] or "")
    out["eyes"] = str(out.get("eyes") or "")
    for key, (lo, hi) in (("conv", CONV_RANGE), ("grow", GROW_RANGE), ("blur", BLUR_RANGE), ("disp", DISP_RANGE)):
        try:
            out[key] = max(lo, min(hi, float(out[key])))
        except (TypeError, ValueError):
            out[key] = DEFAULTS[key]
    return out


def original_depth_path(clip: str, depth_map: str) -> str:
    """Where Erase from Clip keeps the original clip's depth map (its own name: the erased clip's
    depth map will take the clip's)."""
    return os.path.join(E.history_dir(clip), f"{_stem(clip)}.original_depth{os.path.splitext(depth_map)[1] or '.mp4'}")


def keep_original_depth(clip: str, depth_map: str) -> str:
    """Erase from Clip, done: the depth map the clip had (the object in it) kept beside the original
    clip, and the put-back pointed at it -- depth "Original Depth" for a put-back not set yet (the user's
    default, 2026-10-16: "use the original depth map that already tracks him"). Returns the
    kept copy ("" when there was no map)."""
    import shutil
    if not depth_map or not os.path.isfile(depth_map):
        return ""
    dst = original_depth_path(clip, depth_map)
    if os.path.normcase(os.path.abspath(dst)) != os.path.normcase(os.path.abspath(depth_map)):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(depth_map, dst + ".part")
        os.replace(dst + ".part", dst)
    fresh = not os.path.isfile(settings_path(clip))
    s = load_settings(clip)
    s["original_depth"] = dst
    if fresh:
        s["depth"] = "Original Depth"
    save_settings(clip, s)
    return dst


def save_settings(clip: str, data: Dict) -> None:
    os.makedirs(E.history_dir(clip), exist_ok=True)
    tmp = settings_path(clip) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({k: data.get(k, v) for k, v in DEFAULTS.items()}, f, indent=1)
    os.replace(tmp, settings_path(clip))


def parallax_for_depth(depth: float, convergence: float = 0.5, max_disparity: float = 20.0) -> float:
    """Splatting's disparity for a depth (0..1, 1 nearest), in percent of the
    width: (d - convergence) * 2 * max_disparity / 20 / 100 * 100
    (core.splatting.forward_warp.execute_forward_warp)."""
    return (float(depth) - float(convergence)) * float(max_disparity) / 10.0


# ---- the layer ---------------------------------------------------------------------------

class PutBack:
    """One clip's put-back: reads its frames at the eyes' size, on demand."""

    def __init__(self, clip: str, settings: Dict, rec: Dict):
        self.clip = clip
        self.settings = settings
        self.original = rec["original"]
        self.modes = modes_for(rec)
        rep, obj = repaint_mask_path(clip), object_mask_path(clip)
        self.repaint_mask = rep if os.path.isfile(rep) else None
        # an erase from before object masks were kept: the repaint mask stands in
        self.object_mask = obj if os.path.isfile(obj) else self.repaint_mask
        self._readers: Dict[Tuple[str, int, int], object] = {}
        self._lock = threading.Lock()
        self._dm = None              # the Depth Map layer (core.putback.engine), built on first use
        self._dm_failed = ""         # why it couldn't be (said once, parallax used)
        self._gl = None              # Add's Glass Plane layer (2026-10-17), built on first use
        self._gl_failed = ""

    def depth_map_layer(self):
        """The Depth Map layer, or None (parallax then): depth "Depth Map", Over, its original map kept,
        and the clean clip's geometry found."""
        s = self.settings
        # Original Eyes without its merge: Original Depth (said in Merging's section)
        if s.get("depth") not in MAP_DEPTHS + ("Original Eyes",) or s.get("mode") != "Over" or self._dm_failed:
            return None
        if self._dm is not None:
            return self._dm
        try:
            od = s.get("original_depth") or ""
            if not os.path.isfile(od):
                raise RuntimeError("its original depth map isn't there -- erase it again from the Depth Map Editor")
            found = GEOMETRY(_stem(self.clip)) if GEOMETRY is not None else None
            if not found:
                raise RuntimeError("the clean clip's depth map wasn't found in Splatting's depth folder")
            clean_depth, settings = found
            # kept across settings edits (Merging's dials rewrite the settings, and find() makes a new
            # PutBack): built from the maps and the mask, so only they make it again
            own = s.get("depth") in ("Original Depth", "Original Eyes")
            key = tuple((p, os.path.getmtime(p)) for p in (od, clean_depth, self.object_mask)) + (own,)
            dm = _DM_CACHE.get(self.clip)
            if dm is None or dm[0] != key:
                if dm is not None:
                    dm[1].close()
                from core.putback.engine import DepthMapLayer
                _DM_CACHE[self.clip] = (key, DepthMapLayer(od, clean_depth, self.object_mask, settings,
                                                           os.path.basename(self.clip), original_scale=own))
            self._dm = _DM_CACHE[self.clip][1]
        except Exception as e:
            self._dm_failed = str(e) or e.__class__.__name__
            logger.warning("Put-back for %s: the depth map can't be used (%s); parallax instead.",
                           _stem(self.clip), self._dm_failed)
            return None
        return self._dm

    def eyes(self):
        """The original merge's eye files (left, right), or None: depth "Original Eyes", Over, and found."""
        s = self.settings
        if s.get("depth") != "Original Eyes" or s.get("mode") != "Over":
            return None
        from core.putback.engine import find_eyes
        return find_eyes(s.get("eyes", ""), _stem(self.clip))

    def glass_layer(self):
        """Add's Glass Plane layer (core.putback.engine.GlassLayer), or None (parallax then): depth "Glass Plane",
        Add, and the clean clip's depth map found (its planes are read off it)."""
        s = self.settings
        if s.get("depth") != "Glass Plane" or s.get("mode") != "Add" or self._gl_failed:
            return None
        if self._gl is not None:
            return self._gl
        try:
            found = GEOMETRY(_stem(self.clip)) if GEOMETRY is not None else None
            if not found:
                raise RuntimeError("the clean clip's depth map wasn't found in Splatting's depth folder")
            clean_depth, settings = found
            mask = self.repaint_mask
            if mask is None:
                raise RuntimeError("the erase's mask isn't there")
            key = tuple((p, os.path.getmtime(p)) for p in (clean_depth, mask))
            gl = _GL_CACHE.get(self.clip)
            if gl is None or gl[0] != key:
                if gl is not None:
                    gl[1].close()
                from core.putback.engine import GlassLayer
                _GL_CACHE[self.clip] = (key, GlassLayer(clean_depth, mask, settings, os.path.basename(self.clip)))
            self._gl = _GL_CACHE[self.clip][1]
        except Exception as e:
            self._gl_failed = str(e) or e.__class__.__name__
            logger.warning("Put-back for %s: Glass Plane can't be used (%s); parallax instead.",
                           _stem(self.clip), self._gl_failed)
            return None
        return self._gl

    def _read(self, path: str, idx, w: int, h: int) -> np.ndarray:
        import torch  # noqa: F401  -- before decord (it segfaults on import otherwise here)
        from decord import VideoReader, cpu
        key = (path, w, h)
        with self._lock:
            r = self._readers.get(key)
            if r is None:
                r = self._readers[key] = VideoReader(path, ctx=cpu(0), width=w, height=h)
            n = len(r)
            return r.get_batch([min(max(int(i), 0), n - 1) for i in idx]).asnumpy()

    def apply(self, left, right, start: int, flipped: bool = False):
        """Both eyes (N, 3, H, W float 0..1 tensors; `left` may be None) with
        the layer for frames start..start+N-1 put back. In Merging's flipped
        space when the clip is flipped (the layer is flipped to match)."""
        import torch
        s = self.settings
        n, _c, h, w = right.shape
        idx = list(range(int(start), int(start) + n))
        dev = right.device

        def frames(path):
            t = torch.from_numpy(self._read(path, idx, w, h)).to(dev).permute(0, 3, 1, 2).float() / 255.0
            return torch.flip(t, dims=[3]) if flipped else t

        def mask(path):
            return _shape(frames(path)[:, :1], float(s.get("grow", 0.0)), float(s.get("blur", 0.0))) \
                * float(s["opacity"])

        # px the right eye's layer moves left: per frame from the track, else the one parallax
        track = s.get("track") or []
        shifts = [(track[min(i, len(track) - 1)] if track else float(s["parallax"])) / 100.0 * w for i in idx]

        def shifted(t):
            if len(set(shifts)) == 1:
                return _shift_left(t, shifts[0])
            return torch.cat([_shift_left(t[k:k + 1], sh) for k, sh in enumerate(shifts)], 0)
        if s["mode"] == "Over" and "Over" in self.modes:
            if self.object_mask is None:
                return left, right
            eyes = self.eyes()
            if eyes is not None:
                return self._apply_eyes(left, right, eyes, frames, idx, w, h, flipped)
            o, a = frames(self.original), mask(self.object_mask)
            dm = self.depth_map_layer()
            if dm is not None:
                # the object warped with the original's depth map, as Splatting warped the scene
                lefts, rights = [], []
                for k, fi in enumerate(idx):
                    rgb = o[k].permute(1, 2, 0).float().cpu().numpy()
                    al = a[k, 0].float().cpu().numpy()
                    lay = dm.warp(fi, rgb, al, float(s.get("conv", 0.0)), flipped,
                                  disp=float(s.get("disp", 100.0)) / 100.0)
                    pm, ar = lay["right"]
                    rights.append((torch.from_numpy(pm).to(dev).permute(2, 0, 1), torch.from_numpy(ar).to(dev)[None]))
                    if lay["left"] is not None:
                        lpm, la = lay["left"]
                        lefts.append((torch.from_numpy(lpm).to(dev).permute(2, 0, 1), torch.from_numpy(la).to(dev)[None]))
                pm_r = torch.stack([p for p, _a in rights])
                a_r = torch.stack([q for _p, q in rights])
                right = right * (1 - a_r) + pm_r
                if left is not None:
                    if lefts:          # Dual Eye: the left eye's object warped too
                        pm_l = torch.stack([p for p, _a in lefts])
                        a_l = torch.stack([q for _p, q in lefts])
                        left = left * (1 - a_l) + pm_l
                    else:
                        left = left * (1 - a) + o * a
                return left, right
            if left is not None:
                left = left * (1 - a) + o * a
            o_r, a_r = shifted(o), shifted(a)
            right = right * (1 - a_r) + o_r * a_r
        else:
            if self.repaint_mask is None:
                return left, right
            gl = self.glass_layer()
            if gl is not None:
                # Glass Plane (2026-10-17): the layer warped at its windows' planes, Convergence and Disparity on top
                raw = frames(self.original) - frames(self.clip)
                a = mask(self.repaint_mask)[:, :1]
                rights, lefts = [], []
                for k, fi in enumerate(idx):
                    lay = gl.warp(fi, raw[k], a[k], float(s.get("conv", 0.0)), float(s.get("disp", 100.0)) / 100.0,
                                  flipped)
                    rights.append(lay["right"])
                    lefts.append(lay["left"])
                right = (right + torch.stack(rights)).clamp(0, 1)
                if left is not None:
                    left = (left + (torch.stack(lefts) if lefts[0] is not None else raw * a)).clamp(0, 1)
                return left, right
            diff = (frames(self.original) - frames(self.clip)) * mask(self.repaint_mask)
            if left is not None:
                left = (left + diff).clamp(0, 1)
            right = (right + shifted(diff)).clamp(0, 1)
        return left, right

    def _apply_eyes(self, left, right, eyes, frames, idx, w: int, h: int, flipped: bool):
        """Original Eyes: him cut from the original merge's own eyes (core.putback.engine), on the GPU."""
        import torch
        from core.putback.engine import EYES_SEARCH, eyes_disparity, eyes_right_mask
        s = self.settings
        dev = right.device
        if eyes[0] == eyes[1]:           # one side-by-side file: its halves, each brought to the eye's size
            sbs = torch.from_numpy(self._read(eyes[0], idx, 2 * w, h)).to(dev).permute(0, 3, 1, 2).float() / 255.0
            o_l, o_r = sbs[..., :w], sbs[..., w:]
        else:
            o_l, o_r = (torch.from_numpy(self._read(p, idx, w, h)).to(dev).permute(0, 3, 1, 2).float() / 255.0
                        for p in eyes)
        if flipped:
            o_l, o_r = torch.flip(o_l, dims=[3]), torch.flip(o_r, dims=[3])
        a = frames(self.object_mask)[:, :1]
        inside = -torch.nn.functional.max_pool2d(-(a > 0.5).float(), 15, 1, 7)      # 7 px in: surely him
        with torch.no_grad():
            disp = eyes_disparity(o_l, o_r, inside, EYES_SEARCH * w)
            a_r = eyes_right_mask(a, disp)
        grow, blur, op = float(s.get("grow", 0.0)), float(s.get("blur", 0.0)), float(s["opacity"])
        a_l, a_r = _shape(a, grow, blur) * op, _shape(a_r, grow, blur) * op
        if left is not None:
            left = left * (1 - a_l) + o_l * a_l
        right = right * (1 - a_r) + o_r * a_r
        return left, right

    def close(self) -> None:
        with self._lock:
            self._readers.clear()
            self._dm = None              # the layer itself stays in _DM_CACHE (close_all ends it)
            self._gl = None              # ...and Glass Plane's in _GL_CACHE


def _shape(a, grow: float, blur: float):
    """The object's mask (N, 1, H, W): `grow` px out (+, dilate) or in (-, erode), then softened over
    `blur` px (Merging's Put Back controls, user 2026-10-15)."""
    import torch
    import torch.nn.functional as F
    r = int(round(abs(grow)))
    if r > 0:
        k = 2 * r + 1
        a = F.max_pool2d(a, k, 1, r) if grow > 0 else -F.max_pool2d(-a, k, 1, r)
    if blur > 0:
        sigma = blur / 2.0
        rad = max(1, int(round(3 * sigma)))
        x = torch.arange(-rad, rad + 1, device=a.device, dtype=a.dtype)
        g = torch.exp(-(x * x) / (2 * sigma * sigma))
        g = g / g.sum()
        a = F.conv2d(F.pad(a, (rad, rad, 0, 0), mode="replicate"), g.view(1, 1, 1, -1))
        a = F.conv2d(F.pad(a, (0, 0, rad, rad), mode="replicate"), g.view(1, 1, -1, 1))
    return a.clamp(0, 1)


def _shift_left(t, px: float):
    """`t` moved left by `px` (fractional: the two whole shifts blended);
    what comes in at the edge is empty."""
    import math
    import torch
    if abs(px) < 1e-6:
        return t
    k = math.floor(px)
    f = px - k

    def whole(n):
        out = torch.zeros_like(t)
        w = t.shape[3]
        if n >= w or -n >= w:
            return out
        if n > 0:
            out[..., : w - n] = t[..., n:]
        elif n < 0:
            out[..., -n:] = t[..., : w + n]
        else:
            out = t.clone()
        return out
    a = whole(k)
    return a if f < 1e-6 else a * (1 - f) + whole(k + 1) * f


_CACHE: Dict[str, Tuple[tuple, PutBack]] = {}
#: each clip's Depth Map layer: {clip: ((its files, mtimes), DepthMapLayer)}
_DM_CACHE: Dict[str, Tuple[tuple, object]] = {}
#: each clip's Glass Plane layer, the same way
_GL_CACHE: Dict[str, Tuple[tuple, object]] = {}


def signature(original_folder: str, core_name: str) -> Optional[tuple]:
    """When the clip's put-back last changed -- its settings' and the clip's
    modified times -- for a preview cache to notice an edit; None: there is none."""
    try:
        if not original_folder or not core_name:
            return None
        if os.path.isfile(original_folder):
            clip = original_folder if _stem(original_folder) == core_name else None
        else:
            from core.common.sidecar_manager import find_video_by_core_name
            clip = find_video_by_core_name(original_folder, core_name)
        if not clip:
            return None
        sp = settings_path(clip)
        return (os.path.getmtime(sp), os.path.getmtime(clip)) if os.path.isfile(sp) else None
    except Exception:
        return None


def find(original_folder: str, core_name: str) -> Optional[PutBack]:
    """The put-back for a clip Merging pairs by `core_name` in its Original
    folder (or that file), or None: no erased clip there, or put-back off.
    Kept open between calls (the preview asks every frame)."""
    try:
        if not original_folder or not core_name:
            return None
        if os.path.isfile(original_folder):
            clip = original_folder if _stem(original_folder) == core_name else None
        else:
            from core.common.sidecar_manager import find_video_by_core_name
            clip = find_video_by_core_name(original_folder, core_name)
        if not clip:
            return None
        sp = settings_path(clip)
        if not os.path.isfile(sp):
            return None
        key = (os.path.getmtime(sp), os.path.getmtime(clip))
        hit = _CACHE.get(clip)
        if hit is not None and hit[0] == key:
            return hit[1] if hit[1].settings["enabled"] else None
        rec = erased(clip)
        s = load_settings(clip)
        if rec is None:
            return None
        pb = PutBack(clip, s, rec)
        if hit is not None:
            hit[1].close()
        _CACHE[clip] = (key, pb)
        return pb if s["enabled"] else None
    except Exception:
        logger.warning("Put-back for %s not read", core_name, exc_info=True)
        return None


def close_all() -> None:
    """Close every cached put-back's readers, so the erased clip, its
    original and its masks can be moved (bug sweep 2026-10-09: they stayed
    open all session and the clip never reached finished/). The next
    find() opens them again."""
    hits = list(_CACHE.values())
    _CACHE.clear()
    for _key, pb in hits:
        pb.close()
    layers = list(_DM_CACHE.values()) + list(_GL_CACHE.values())
    _DM_CACHE.clear()
    _GL_CACHE.clear()
    for _key, dm in layers:
        dm.close()
