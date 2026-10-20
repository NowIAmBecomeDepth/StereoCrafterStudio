"""hires.py -- render the edited depth map at the source video's resolution.

The user, 2026-10-03: a mannequin that DepthCrafter left as a few polygons --
SAM 3 tracks its real outline on the RGB, but the editor applied that mask to
the 768x448 depth map, so the outline was squeezed back onto the depth map's
coarse grid. Detect Text gets clean edges because its mask meets the depth
only in Splatting, after the depth is enlarged to the video's size. This does
the same for the editor's Render:

* SAM 3's masks come back at the video's own size; an RGB layer keeps that
  full-size copy (Layer.masks_hi) beside its depth-sized mask (Layer.masks,
  which the editor's previews keep using -- they stay fast);
* Render, when "Render at source resolution" is on and the clip has an RGB
  source, enlarges each depth frame to the RGB's size (even numbers: the
  writer's 4:2:0), applies every layer there -- an RGB layer with its
  full-size mask, any other with its mask enlarged -- with the settings
  measured in depth-map pixels scaled by the same factor, and writes that.

Edge Snap is left off for full-size masks: it pulls a mask's edge onto the
depth map's own edges, which here would drag the RGB outline back onto the
coarse depth it is there to correct. Splatting enlarges every depth map to
the video's size first, so it simply receives a sharper one.
"""

from __future__ import annotations

import copy
import functools
import os
from dataclasses import replace
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

from core.depthedit import edit_ops
from core.depthedit.project import MaskStore

Size = Tuple[int, int]          # (width, height)


@functools.lru_cache(maxsize=16)
def _probe_size(path: str, mtime: float) -> Optional[Size]:
    from core.depthedit import depth_io
    try:
        info = depth_io.probe(path, count=False)   # the size only: no frame count (2026-10-11)
    except Exception:
        return None
    w, h = int(info.width), int(info.height)
    w, h = w - (w % 2), h - (h % 2)            # 4:2:0 wants even sizes
    return (w, h) if w > 0 and h > 0 else None


def source_size(rgb_path: str) -> Optional[Size]:
    """The RGB source's size (even), or None without one."""
    if not rgb_path or not os.path.isfile(rgb_path):
        return None
    try:
        return _probe_size(os.path.normcase(os.path.abspath(rgb_path)), os.path.getmtime(rgb_path))
    except OSError:
        return None


def new_store(size: Optional[Size], depth: Optional[Size] = None) -> Optional[MaskStore]:
    """A store for full-size masks -- none when the map is already read at
    that size (Edit at source resolution): its own masks are full size."""
    if not size or (depth is not None and tuple(depth) == tuple(size)):
        return None
    return MaskStore(size[1], size[0])


#: Upscale Map (user, 2026-10-05): the whole map is held in
#: memory as 10-bit codes, 2 bytes a pixel -- 1920x1038 is ~4 MB a frame, so
#: a 750-frame scene ~3 GB against ~0.5 GB at DepthCrafter's 768x448.
EDIT_BUDGET = 6 * 1024 ** 3


def edit_size(rgb_path: str, info, budget: int = EDIT_BUDGET) -> Tuple[Optional[Size], str]:
    """(the size to read the map at, a note): the RGB source's size when it is
    larger than the map and the whole clip fits `budget`; else (None, why)."""
    size = source_size(rgb_path)
    if not size:
        return None, "Upscale Map needs the clip's source video (Source video, above)."
    if size[0] <= info.width and size[1] <= info.height:
        return None, "The map is already at the source's size."
    need = max(info.num_frames, 1) * size[0] * size[1] * 2
    if need > budget:
        return None, (f"This clip is too long to upscale ({need / 1024 ** 3:.1f} GB at "
                      f"{size[0]}x{size[1]}); it stays at the map's own size.")
    return size, ""


def upscale_layers(project, size: Size) -> List[str]:
    """Every layer's masks at `size` after Upscale Map: an RGB layer's
    full-size masks become its masks (SAM 3's own outline); any other's are
    enlarged. Returns the names of the layers enlarged -- a Start Tracking
    on them gives the finer outline."""
    w, h = int(size[0]), int(size[1])
    enlarged = []
    for layer in project.layers:
        lo, hi = layer.masks, getattr(layer, "masks_hi", None)
        if lo is None:
            continue
        new = MaskStore(h, w)
        if getattr(layer, "shapes", None):
            # shapes (2026-10-17): drawn again at this size, not enlarged
            layer.masks = new
            from core.depthedit.shapes import fill
            fill(layer, layer.shape_frames)
            continue
        if hi is not None and (hi.width, hi.height) == (w, h) and len(hi):
            new._frames = dict(hi._frames)
        else:
            for frame in lo.frames():
                new.set(frame, enlarge_mask(lo.get(frame), (w, h)))
            if len(lo):
                enlarged.append(layer.name)
        layer.masks = new
        layer.masks_hi = None               # the masks are full size now
    return enlarged


def put(store: Optional[MaskStore], frame: int, result) -> None:
    """SAM 3's result for `frame`, at the store's (full) size."""
    if store is None:
        return
    from core.depthedit import sam3_backend as sam
    store.set(frame, sam.union_mask(result, store.height, store.width))


def store_result(layer, frame: int, result, size: Optional[Size]) -> None:
    """Keep an RGB layer's full-size mask for `frame` beside its depth-sized one."""
    if not size or getattr(layer, "source", "RGB") != "RGB":
        return
    lo = getattr(layer, "masks", None)
    if lo is not None and (lo.width, lo.height) == tuple(size):
        return                                  # the map is read at full size: its masks are these
    store = getattr(layer, "masks_hi", None)
    if store is None or (store.width, store.height) != tuple(size):
        store = layer.masks_hi = new_store(size)
    put(store, frame, result)


def info_at(info, size: Optional[Size]):
    """The clip's info at another size (the writer's), or as it is."""
    if not size:
        return info
    out = copy.copy(info)
    out.width, out.height = int(size[0]), int(size[1])
    return out


#: The RGB view's height (user, 2026-10-03: the view at the depth map's
#: 768x448 was too coarse to see what SAM 3 was outlining). 720p is ~2.9 MB a
#: frame for 1920x1038 against ~1 MB at the depth map's size.
VIEW_HEIGHT = 720


def view_size(src: Optional[Size], depth: Size, frames: int, budget: int) -> Size:
    """The size the RGB view is decoded at: the source at up to VIEW_HEIGHT
    tall (even sizes), when that is finer than the depth map and the whole
    clip still fits `budget` bytes; otherwise the depth map's own size."""
    dw, dh = int(depth[0]), int(depth[1])
    if not src or src[0] <= 0 or src[1] <= 0:
        return dw, dh
    h = min(int(src[1]), VIEW_HEIGHT)
    w = int(round(src[0] * h / float(src[1])))
    w, h = w - (w % 2), h - (h % 2)
    if w <= dw or h <= dh:
        # the map is as fine as the view or finer: the view at the map's size,
        # unless the cache can't hold that (a map read at source resolution)
        # and the view at VIEW_HEIGHT is smaller and fits
        if frames * dw * dh * 3 > budget and w * h < dw * dh and frames * w * h * 3 <= budget:
            return w, h
        return dw, dh
    if frames * w * h * 3 > budget:
        return dw, dh
    return w, h


def view_mask(lo: Optional[np.ndarray], hi: Optional[np.ndarray], s: edit_ops.LayerSettings,
              unit: np.ndarray, size: Size, use_hi: bool) -> Optional[np.ndarray]:
    """A layer's mask as edited, for a picture of `size`: what the editor's
    overlay outlines. At the depth map's size it is the editor's own; larger,
    a layer's full-size mask (when Render will use it) shrunk to `size` with
    its settings scaled, as render_frame applies it -- else the depth-sized
    one enlarged. Add ignores Edge snap, so its outline does too."""
    if s.mode == "Add":
        s = replace(s, edge_snap=0)
    dh, dw = unit.shape[:2]
    if tuple(size) == (dw, dh):
        return None if lo is None else edit_ops.effective_mask(lo, s, unit)
    if use_hi and hi is not None:
        m = cv2.resize(hi.astype(np.float32), tuple(size), interpolation=cv2.INTER_AREA) >= 0.5
        return edit_ops.effective_mask(m, scaled(s, size[0] / float(dw), snap=False), None)
    if lo is None:
        return None
    return enlarge_mask(edit_ops.effective_mask(lo, s, unit), size)


def scaled(s: edit_ops.LayerSettings, k: float, snap: bool = True) -> edit_ops.LayerSettings:
    """`s` with its depth-map pixel settings in pixels `k` times smaller."""
    out = replace(s, smooth=s.smooth * k, edge_top=s.edge_top * k, edge_bottom=s.edge_bottom * k,
                  edge_left=s.edge_left * k, edge_right=s.edge_right * k, grow=s.grow * k, feather=s.feather * k,
                  close_gaps=s.close_gaps * k,
                  edge_snap=int(round(s.edge_snap * k)) if snap else 0)
    return out


def enlarge(unit: np.ndarray, size: Size) -> np.ndarray:
    """A 0..1 depth frame at `size` (bicubic, kept in range)."""
    return np.clip(cv2.resize(unit.astype(np.float32), size, interpolation=cv2.INTER_CUBIC), 0.0, 1.0)


def enlarge_mask(mask: Optional[np.ndarray], size: Size) -> Optional[np.ndarray]:
    if mask is None:
        return None
    return cv2.resize(mask.astype(np.float32), size, interpolation=cv2.INTER_LINEAR) >= 0.5


def render_frame(unit: np.ndarray, glob: edit_ops.GlobalSettings,
                 layers: Sequence[Tuple[edit_ops.LayerSettings, Optional[np.ndarray], Optional[np.ndarray]]],
                 frame_idx: int, size: Size) -> np.ndarray:
    """One frame of the edit at `size`. `layers` is (settings, depth-sized
    mask, full-size mask or None) bottom-up."""
    k = size[0] / float(unit.shape[1])
    big = enlarge(unit, size)
    stack = []
    for s, lo, hi in layers:
        if hi is not None and hi.shape == (size[1], size[0]):
            stack.append((scaled(s, k, snap=False), hi))         # the RGB's own outline
        else:
            stack.append((scaled(s, k), enlarge_mask(lo, size)))
    return edit_ops.apply_all(big, glob, stack, frame_idx)
