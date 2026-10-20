"""keys.py -- keyframes for a Depth Map Editor layer (user, 2026-10-05: "the
depth map editor needs dynamic keyframing too", as Splatting has).

A layer's number settings -- Depth, Push / pull, Contrast, Flatten, Smooth,
the edge moves, Grow / shrink, Edge snap, Feather, Opacity, the gradient's
near / far / curve / keep detail -- can be keyed per frame. The track and its
maths are Splatting's (core.common.keyframes.KeyframeTrack):

* a **static** key holds its values from its frame until the next key;
* a **dynamic** key is ramped to, linearly, from the key before it;
* before the first key, the first key's values hold.

A layer with no keys uses its settings as they are, everywhere. The choices
(Alter / Add, Fill holes, the gradient's shape, blend, on / off) and the frame
range are not keyed: they stay the layer's own.

``settings_at`` is the one place that turns a layer into its settings for a
frame -- the editor's preview, its overlay and the render all ask it, so they
agree frame for frame.
"""

from __future__ import annotations

import copy
from typing import Dict, Optional, Tuple

from core.common.keyframes import KeyframeTrack
from core.depthedit.edit_ops import LayerSettings

#: The keyed settings, as paths into LayerSettings.
KEY_PATHS: Tuple[Tuple[str, ...], ...] = (
    ("brightness",), ("contrast",), ("flatten",), ("smooth",), ("edge_top",), ("edge_bottom",),
    ("edge_left",), ("edge_right",), ("grow",), ("edge_snap",), ("feather",), ("opacity",), ("add_depth",),
    ("gradient", "near"), ("gradient", "far"), ("gradient", "curve"), ("gradient", "keep_detail"),
)
_INT = {"edge_snap"}


def key_name(path: Tuple[str, ...]) -> str:
    return ".".join(path)


def is_keyed_path(path: Tuple[str, ...]) -> bool:
    return tuple(path) in KEY_PATHS


def _get(s, path):
    for p in path:
        s = getattr(s, p)
    return s


def _set(s, path, value) -> None:
    for p in path[:-1]:
        s = getattr(s, p)
    if path[-1] in _INT:
        value = int(round(float(value)))
    setattr(s, path[-1], value)


def capture(settings: LayerSettings) -> Dict[str, float]:
    """The keyed values of `settings`, as a key's snapshot."""
    return {key_name(p): float(_get(settings, p)) for p in KEY_PATHS}


def settings_at(settings: LayerSettings, track: Optional[KeyframeTrack], frame: int) -> LayerSettings:
    """The layer's settings on `frame`: as they are with no keys (the same
    object -- nothing copied), else a copy with the keyed values for that frame."""
    if track is None or track.is_empty():
        return settings
    values = track.values_at(int(frame))
    if not values:
        return settings
    out = copy.deepcopy(settings)
    for p in KEY_PATHS:
        v = values.get(key_name(p))
        if v is not None:
            _set(out, p, v)
    return out


def update_key_value(track: KeyframeTrack, frame: int, path: Tuple[str, ...], value) -> bool:
    """A setting changed while on a key: that key takes the new value. False
    when there is no key on `frame` or the setting isn't keyed."""
    entry = track.frame_at(frame)
    if entry is None or not is_keyed_path(path):
        return False
    values = dict(entry["values"])
    values[key_name(path)] = float(value)
    track.set_keyframe(frame, entry["type"], values)
    return True
