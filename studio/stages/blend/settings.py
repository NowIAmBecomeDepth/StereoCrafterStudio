"""settings.py -- the Blend tab's settings (config/blend.json): defaults, coercion, the run snapshot.

No Qt and no torch here.
"""

from __future__ import annotations

import math
from typing import Any, Dict

from core import depthblend as B
from studio.default_folders import localize as _localize

DEFAULTS: Dict[str, Any] = {
    # Splatting's depth maps: where VDA's rerun leaves a clip's two maps, and where the blend goes
    "depth_folder": "./output_depthmaps",
    "other": "VDA",                 # the map blended with DepthCrafter's: VDA's or DVD's (user, 2026-11-07)
    "mode": "Mix",
    "mix": 0.5,                     # 0 = DepthCrafter, 1 = the other map
    "base": "DepthCrafter",         # the map whose range (Mix) or layout (Layout + Detail) is kept
    "detail_size": 8.0,             # Layout + Detail: the blur's radius, in the blend's pixels
    "detail_amount": 1.0,
    # manual controls (user, 2026-11-07; core.depthblend): By Depth's Near / Far Mix, Edge Keep, Detail
    # Limit / Noise Floor (% of the depth range; 0 = off), Edge Band (px of the blend; 0 = off)
    "mix_by_depth": False,
    "mix_near": 0.7,
    "mix_far": 0.3,
    "mix_split": 0.5,
    "mix_soft": 0.2,
    "edge_keep": 0.0,
    "detail_limit": 0.0,
    "detail_floor": 0.0,
    "edge_band": 0.0,
    "edge_band_sides": "Left + Top",
    # Detail Smoothing (frames each side; 0 = off) and Levels (user, 2026-11-07: "go ahead with 5-7")
    "detail_smooth": 0.0,
    "level_black": 0.0,
    "level_white": 1.0,
    "level_gamma": 1.0,
    "align": "Per clip",
    "size": "Larger",
    "output_suffix": "_depth",
}
DEFAULTS = _localize(DEFAULTS)

_MENUS = {"mode": B.MODES, "other": B.OTHERS, "base": B.BASES, "align": B.ALIGNS, "size": B.SIZES,
          "edge_band_sides": B.BAND_SIDES}
_RANGES = {"mix": (0.0, 1.0), "detail_size": (0.5, 64.0), "detail_amount": (0.0, 3.0),
           "mix_near": (0.0, 1.0), "mix_far": (0.0, 1.0), "mix_split": (0.0, 1.0), "mix_soft": (0.02, 1.0),
           "edge_keep": (0.0, 1.0), "detail_limit": (0.0, 20.0), "detail_floor": (0.0, 5.0),
           "edge_band": (0.0, 256.0), "detail_smooth": (0.0, 6.0), "level_black": (0.0, 0.5),
           "level_white": (0.5, 1.0), "level_gamma": (0.2, 5.0)}

#: The settings every clip shares; the rest a clip can have its own of (Own Settings; user, 2026-11-07)
GLOBAL_KEYS = ("depth_folder", "other", "output_suffix")
CLIP_KEYS = tuple(k for k in DEFAULTS if k not in GLOBAL_KEYS)
#: A folder's clips' own settings: {clip: {key: value}}, beside their maps
CLIPS_FILE = "blend_clips.json"


def choices(other: str = "VDA") -> Dict[str, list]:
    """The menus; Base and Output Size name only the other map picked."""
    out = {k: list(v) for k, v in _MENUS.items()}
    out["base"] = [v for v in B.BASES if v == "DepthCrafter" or v == other]
    out["size"] = [v for v in B.SIZES if v not in B.OTHERS or v == other]
    return out


def coerce(key: str, value: Any) -> Any:
    """`value` as the type of the key's default, or the default if it won't go."""
    default = DEFAULTS.get(key)
    if default is None:
        return value
    try:
        if isinstance(default, bool):
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if isinstance(default, float):
            number = float(value)
            if not math.isfinite(number):
                raise ValueError(value)
            lo, hi = _RANGES.get(key, (-math.inf, math.inf))
            return min(max(number, lo), hi)
        text = "" if value is None else str(value)
        menu = _MENUS.get(key)
        return text if menu is None or text in menu else default
    except (TypeError, ValueError, OverflowError):
        return default


def normalise(data: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(DEFAULTS)
    for key, value in (data or {}).items():
        if key in DEFAULTS:
            out[key] = coerce(key, value)
    return follow_other(out)


def follow_other(values: Dict[str, Any]) -> Dict[str, Any]:
    """Base and Output Size naming the other model's map name the one picked (VDA -> DVD when it changes)."""
    for key in ("base", "size"):
        if values.get(key) in B.OTHERS:
            values[key] = values.get("other", "VDA")
    return values


def clip_values(values: Dict[str, Any]) -> Dict[str, Any]:
    """The per-clip part of `values`."""
    return {k: values[k] for k in CLIP_KEYS if k in values}


def load_clip_settings(folder: str) -> Dict[str, Dict[str, Any]]:
    """The clips' own settings kept in `folder` ({} when none, or the file won't read)."""
    import json
    import os
    try:
        with open(os.path.join(folder, CLIPS_FILE), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for clip, vals in data.items():
        if isinstance(clip, str) and isinstance(vals, dict):
            out[clip] = {k: coerce(k, v) for k, v in vals.items() if k in CLIP_KEYS}
    return out


def save_clip_settings(folder: str, data: Dict[str, Dict[str, Any]]) -> bool:
    """Keep the clips' own settings in `folder` (the file removed when no clip has any). False: it couldn't."""
    import os
    path = os.path.join(folder, CLIPS_FILE)
    try:
        if not data:
            if os.path.exists(path):
                os.remove(path)
            return True
        from core.common.atomic_json import write_json_atomic
        write_json_atomic(path, {c: clip_values(v) for c, v in sorted(data.items())}, indent=2)
        return True
    except OSError:
        return False


def opts_for(global_values: Dict[str, Any], own: Dict[str, Any] = None) -> Dict[str, Any]:
    """A clip's run settings: the shared ones, its own over them."""
    return snapshot({**(global_values or {}), **clip_values(own or {})})


def snapshot(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Freeze everything a run reads. Taken on the UI thread at Start."""
    return normalise(settings)
