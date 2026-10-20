"""project.py -- layers, their masks, and saving a clip's edit to disk.

A clip's edit is two files beside the edited depth map:

  <stem>_depthedit.json        layers, prompts, settings -- small, readable
  <stem>_depthedit_masks.npz   every layer's tracked mask, bit-packed

The masks are kept at the DEPTH map's resolution whatever SAM 3 ran on.
That is the only resolution they are ever applied at, and bit-packing makes
them small: a 768x448 mask is 43 KB, so a 1000-frame layer is about 43 MB in
memory and much less on disk once npz compresses the empty frames.

Prompts are stored as well as masks so a layer can be re-opened and refined.
SAM 3 holds one layer's objects at a time (a text prompt resets its whole
session), so switching to a layer replays that layer's prompts into a fresh
session -- see Sam3Engine.replay.
"""

from __future__ import annotations

import json
import os
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

import cv2
import numpy as np

from core.common.keyframes import KeyframeTrack

from .edit_ops import (GlobalSettings, LayerSettings, global_settings_from_dict, layer_settings_from_dict,
                       settings_to_dict)

PROJECT_VERSION = 1

#: Overlay colours, cycled as layers are added. Chosen to read against both a
#: grey depth map and a typical film frame.
LAYER_COLOURS = ("#ff5a5a", "#4fc3ff", "#ffd54a", "#7dff7a", "#d67dff", "#ff9d4a", "#4affd8", "#ff7ac8")

SOURCES = ("RGB", "Depth")


class MaskStore:
    """Per-frame boolean masks at one resolution, bit-packed."""

    def __init__(self, height: int, width: int) -> None:
        self.height = int(height)
        self.width = int(width)
        self._frames: Dict[int, np.ndarray] = {}
        #: steadied masks worked out, (frame, steady) -> mask; emptied by any change
        self._steadied: "OrderedDict" = OrderedDict()

    def set(self, frame: int, mask: Optional[np.ndarray]) -> None:
        self._steadied.clear()
        if mask is None or not mask.any():
            self._frames.pop(int(frame), None)
            return
        if mask.shape != (self.height, self.width):
            raise ValueError(f"mask {mask.shape} does not match store {(self.height, self.width)}")
        self._frames[int(frame)] = np.packbits(mask.astype(bool), axis=None)

    def _unpack(self, packed: np.ndarray) -> np.ndarray:
        return np.unpackbits(packed, count=self.height * self.width).reshape(self.height, self.width)

    def get(self, frame: int, steady: int = 0) -> Optional[np.ndarray]:
        """The frame's mask; with `steady`, steadied over that many frames either
        side (Steady, user 2026-10-11): a pixel is in it when at least half the
        masks in the window have it. Only frames that HAVE a mask vote -- a frame
        the object is missing from (or a layer not tracked yet) doesn't wear the
        others away -- and a frame with no mask stays without one."""
        frame = int(frame)
        packed = self._frames.get(frame)
        if packed is None:
            return None
        r = int(round(float(steady or 0)))
        if r <= 0:
            return self._unpack(packed).astype(bool)
        hit = self._steadied.get((frame, r))
        if hit is not None:
            self._steadied.move_to_end((frame, r))
            return hit.copy()
        window = [self._frames[f] for f in range(frame - r, frame + r + 1) if f in self._frames]
        if len(window) < 2:
            out = self._unpack(packed).astype(bool)
        else:
            votes = np.zeros((self.height, self.width), np.uint16)
            for p in window:
                votes += self._unpack(p)
            out = votes * 2 >= len(window)
        self._steadied[(frame, r)] = out
        while len(self._steadied) > 8:
            self._steadied.popitem(last=False)
        return out.copy()

    def frames(self) -> List[int]:
        return sorted(self._frames)

    def snapshot(self) -> "MaskStore":
        """A copy that later set()/clear() calls on this store cannot reach.

        Cheap: the packed arrays are never modified in place, only replaced,
        so the copy shares them.
        """
        other = MaskStore(self.height, self.width)
        other._frames = dict(self._frames)
        return other

    def clear(self) -> None:
        self._frames.clear()
        self._steadied.clear()

    def __len__(self) -> int:
        return len(self._frames)


@dataclass
class Click:
    frame: int
    obj_id: int
    x: float  # 0..1 of the frame
    y: float
    label: int  # 1 add, 0 remove, 2/3 box corners


@dataclass
class Layer:
    name: str
    source: str = "RGB"
    enabled: bool = True
    colour: str = LAYER_COLOURS[0]
    settings: LayerSettings = field(default_factory=LayerSettings)
    #: A concept prompt ("person", "car"), and the frame it was given on.
    text: str = ""
    text_frame: int = 0
    clicks: List[Click] = field(default_factory=list)
    uid: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    masks: Optional[MaskStore] = None
    #: The same masks at the RGB source's own size (core.depthedit.hires), for
    #: Render at source resolution; None until SAM 3 gives one (RGB layers).
    masks_hi: Optional[MaskStore] = None
    #: True once masks exist for more than the prompted frames.
    tracked: bool = False
    #: Static / dynamic keys of the number settings per frame (core.depthedit.keys;
    #: user, 2026-10-05). Empty: the settings hold on every frame.
    keys: KeyframeTrack = field(default_factory=KeyframeTrack)
    #: Patches (user, 2026-10-05; core.depthedit.patches): clicks and boxes
    #: made on a TRACKED layer outside its mask. Tracked in a session of their
    #: own and added to the layer's masks, so the layer's own tracking (a text
    #: search above all) is never redone around them.
    patch_clicks: List[Click] = field(default_factory=list)
    #: The masks as they were before the patch being made now: what a patch's
    #: preview and its tracking add to. Not saved; None when no patch is pending.
    patch_base: Any = field(default=None, repr=False, compare=False)
    #: Shapes (user, 2026-10-17; core.depthedit.shapes): a layer drawn, not tracked -- its shapes'
    #: mask on every frame of the clip (`shape_frames` of them). Saved as the shapes; masks drawn on load.
    shapes: List[Dict[str, Any]] = field(default_factory=list)
    shape_frames: int = 0

    def has_prompts(self) -> bool:
        return bool(self.text) or bool(self.clicks)

    def next_obj_id(self, existing: Iterable[int] = ()) -> int:
        used = {c.obj_id for c in self.clicks} | set(int(i) for i in existing)
        return (max(used) + 1) if used else 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uid": self.uid, "name": self.name, "source": self.source, "enabled": self.enabled,
            "colour": self.colour, "settings": settings_to_dict(self.settings),
            "text": self.text, "text_frame": self.text_frame,
            "clicks": [vars(c) for c in self.clicks], "tracked": self.tracked,
            **({"patch_clicks": [vars(c) for c in self.patch_clicks]} if self.patch_clicks else {}),
            **({"keyframes": self.keys.to_list()} if not self.keys.is_empty() else {}),
            **({"shapes": self.shapes, "shape_frames": self.shape_frames} if self.shapes else {}),
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Layer":
        layer = cls(
            name=str(d.get("name", "Layer")),
            source=d.get("source", "RGB") if d.get("source") in SOURCES else "RGB",
            enabled=bool(d.get("enabled", True)),
            colour=str(d.get("colour", LAYER_COLOURS[0])),
            settings=layer_settings_from_dict(d.get("settings", {})),
            text=str(d.get("text", "")),
            text_frame=int(d.get("text_frame", 0)),
            clicks=[Click(**c) for c in d.get("clicks", [])],
            tracked=bool(d.get("tracked", False)),
            keys=KeyframeTrack.from_dict(d.get("keyframes")),
            patch_clicks=[Click(**c) for c in d.get("patch_clicks", [])],
            shape_frames=int(d.get("shape_frames", 0) or 0),
        )
        from core.depthedit.shapes import clean
        layer.shapes = clean(d.get("shapes") or [])
        if d.get("uid"):
            layer.uid = str(d["uid"])
        return layer


@dataclass
class Project:
    rgb_path: str = ""
    depth_path: str = ""
    layers: List[Layer] = field(default_factory=list)
    global_settings: GlobalSettings = field(default_factory=GlobalSettings)

    def new_layer(self, height: int, width: int, source: str = "RGB") -> Layer:
        n = len(self.layers)
        layer = Layer(name=f"Layer {n + 1}", source=source, colour=LAYER_COLOURS[n % len(LAYER_COLOURS)])
        layer.masks = MaskStore(height, width)
        self.layers.append(layer)
        return layer

    def snapshot(self) -> "Project":
        """A copy to save from another thread: later edits (sliders, masks,
        names, clicks) cannot reach it. Cheap -- the masks share their packed
        arrays (MaskStore.snapshot). Studio audit 2026-09-28, PERF: Render
        saved on the UI thread."""
        import copy
        import dataclasses
        layers = [dataclasses.replace(ly, settings=copy.deepcopy(ly.settings), clicks=list(ly.clicks),
                                      patch_clicks=list(ly.patch_clicks),
                                      keys=copy.deepcopy(ly.keys),
                                      shapes=copy.deepcopy(ly.shapes),
                                      masks=ly.masks.snapshot() if ly.masks is not None else None,
                                      masks_hi=ly.masks_hi.snapshot() if ly.masks_hi is not None else None)
                  for ly in self.layers]
        return Project(rgb_path=self.rgb_path, depth_path=self.depth_path, layers=layers,
                       global_settings=copy.deepcopy(self.global_settings))

    # -- persistence -----------------------------------------------------

    def save(self, json_path: str) -> None:
        data = {
            "version": PROJECT_VERSION,
            "rgb_path": self.rgb_path,
            "depth_path": self.depth_path,
            "global": settings_to_dict(self.global_settings),
            "layers": [layer.to_dict() for layer in self.layers],
        }
        # Both files are written to temp names first, and only then put in
        # place, masks before layers: a save that failed at the masks (a full
        # disk) left the new layer list beside the old masks, and those layers
        # loaded tracked with no masks (bug sweep 2026-10-09, A9).
        tmp_json = json_path + ".tmp"
        with open(tmp_json, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

        arrays: Dict[str, np.ndarray] = {}
        for layer in self.layers:
            store = layer.masks
            if store is None or not len(store) or layer.shapes:     # shapes: drawn again on load
                continue
            frames = store.frames()
            arrays[f"{layer.uid}__frames"] = np.asarray(frames, dtype=np.int32)
            arrays[f"{layer.uid}__packed"] = np.stack([store._frames[f] for f in frames])
            arrays[f"{layer.uid}__shape"] = np.asarray([store.height, store.width], dtype=np.int32)
        for layer in self.layers:
            hi = layer.masks_hi
            if hi is None or not len(hi):
                continue
            frames = hi.frames()
            arrays[f"{layer.uid}__hi_frames"] = np.asarray(frames, dtype=np.int32)
            arrays[f"{layer.uid}__hi_packed"] = np.stack([hi._frames[f] for f in frames])
            arrays[f"{layer.uid}__hi_shape"] = np.asarray([hi.height, hi.width], dtype=np.int32)
        masks_path = masks_path_for(json_path)
        try:
            if arrays:
                # np.savez appends .npz to a name that lacks it, so the temp file
                # must already end in .npz or os.replace would miss it.
                tmp = masks_path[:-4] + ".tmp.npz"
                try:
                    np.savez_compressed(tmp, **arrays)
                except BaseException:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                    raise
                os.replace(tmp, masks_path)
            elif os.path.exists(masks_path):
                os.remove(masks_path)
        except BaseException:
            os.remove(tmp_json)
            raise
        os.replace(tmp_json, json_path)

    @classmethod
    def load(cls, json_path: str, height: int, width: int) -> "Project":
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        project = cls(
            rgb_path=str(data.get("rgb_path", "")),
            depth_path=str(data.get("depth_path", "")),
            global_settings=global_settings_from_dict(data.get("global", {})),
            layers=[Layer.from_dict(d) for d in data.get("layers", [])],
        )
        from core.depthedit.shapes import fill
        for layer in project.layers:
            layer.masks = MaskStore(height, width)
            if layer.shapes:
                fill(layer, layer.shape_frames)       # a shape layer: its masks are its shapes
        masks_path = masks_path_for(json_path)
        if not os.path.exists(masks_path):
            return project
        # Closed as soon as it is read: an open NpzFile holds the file, and on
        # Windows the next save's os.replace onto it then fails.
        with np.load(masks_path) as blobs:
            for layer in project.layers:
                if layer.shapes:
                    continue
                if f"{layer.uid}__hi_frames" in blobs:      # the full-size copy, at whatever size it was
                    hh, hw = (int(x) for x in blobs[f"{layer.uid}__hi_shape"])
                    layer.masks_hi = MaskStore(hh, hw)
                    for frame, packed in zip(blobs[f"{layer.uid}__hi_frames"], blobs[f"{layer.uid}__hi_packed"]):
                        layer.masks_hi._frames[int(frame)] = packed
                if f"{layer.uid}__frames" not in blobs:
                    continue
                shape = tuple(int(x) for x in blobs[f"{layer.uid}__shape"])
                hi = layer.masks_hi
                if shape != (height, width) and hi is not None and (hi.height, hi.width) == (height, width):
                    # the map is read at the size the full-size masks were kept
                    # at (Edit at source resolution, user 2026-10-05): they ARE
                    # this size's masks
                    layer.masks._frames = dict(hi._frames)
                elif shape != (height, width) and shape[0] >= height and shape[1] >= width:
                    # saved while the map was read larger: shrunk to this size,
                    # and kept as the full-size copy Render at source resolution uses
                    big = MaskStore(*shape)
                    for frame, packed in zip(blobs[f"{layer.uid}__frames"], blobs[f"{layer.uid}__packed"]):
                        big._frames[int(frame)] = packed
                    for frame in big.frames():
                        m = cv2.resize(big.get(frame).astype(np.float32), (width, height),
                                       interpolation=cv2.INTER_AREA) >= 0.5
                        layer.masks.set(frame, m)
                    if hi is None and layer.source == "RGB":
                        layer.masks_hi = big
                elif shape != (height, width):
                    # The depth map was re-rendered at another size since. The
                    # prompts survive; the masks need tracking again.
                    layer.tracked = False
                    continue
                else:
                    for frame, packed in zip(blobs[f"{layer.uid}__frames"], blobs[f"{layer.uid}__packed"]):
                        layer.masks._frames[int(frame)] = packed
                if layer.masks_hi is not None and (layer.masks_hi.height, layer.masks_hi.width) == (height, width):
                    layer.masks_hi = None           # the same size as the masks: nothing more to keep
        return project


#: Beside Splatting's depth maps: the maps Use in Splatting replaced -- the
#: original as <name>.original.mp4, earlier edits as <name>.edit-NN.mp4 --
#: with the edit's project and pending render. Splatting's Multi-Map never
#: takes it for a map (user, 2026-09-29: edited/originals nested without end).
HISTORY_DIR = "edit_history"


def original_path(depth_path: str) -> str:
    """Where Use in Splatting keeps the map an edit replaced:
    edit_history/<name>.original<ext> beside it."""
    stem, ext = os.path.splitext(os.path.basename(depth_path))
    return os.path.join(os.path.dirname(depth_path), HISTORY_DIR, f"{stem}.original{ext}")


def edit_record_path(original: str) -> str:
    """Beside a kept original: the size and time of the edit written in its place."""
    return original + ".edit.json"


def file_id(path: str):
    st = os.stat(path)
    return [st.st_size, st.st_mtime_ns]


def live_is_edit(live: str, original: str) -> bool:
    """The live map is still the edit Use in Splatting wrote over ``original``
    (a map regenerated in DepthCrafter since is not). An original kept before
    the record existed counts as current."""
    import json
    record = edit_record_path(original)
    if not os.path.isfile(record):
        return True
    try:
        with open(record, "r", encoding="utf-8") as f:
            return json.load(f).get("live") == file_id(live)
    except (OSError, ValueError):
        return True


def depth_edited(depth_path: str) -> bool:
    """Splatting reads an edit from the Depth Map Editor for this map (its
    original kept in edit_history, or the older edited/originals/)."""
    old = os.path.join(os.path.dirname(depth_path), "edited", "originals", os.path.basename(depth_path))
    for original in (original_path(depth_path), old):
        if os.path.isfile(original) and os.path.isfile(depth_path) and live_is_edit(depth_path, original):
            return True
    return False


def depth_stem(depth_path: str) -> str:
    """'clip_depth.mp4' -> 'clip'. The name Splatting pairs a clip by.
    A kept original ('clip_depth.original.mp4') has its clip's stem, so its
    project is the one its edits were saved to."""
    stem = os.path.splitext(os.path.basename(depth_path))[0]
    if stem.endswith(".original"):
        stem = stem[: -len(".original")]
    return stem[: -len("_depth")] if stem.endswith("_depth") else stem


def project_path_for(output_dir: str, depth_path: str) -> str:
    return os.path.join(output_dir, f"{depth_stem(depth_path)}_depthedit.json")


def masks_path_for(json_path: str) -> str:
    base = json_path[: -len(".json")] if json_path.endswith(".json") else json_path
    return base + "_masks.npz"


def output_depth_path(output_dir: str, depth_path: str) -> str:
    return os.path.join(output_dir, f"{depth_stem(depth_path)}_depth.mp4")
