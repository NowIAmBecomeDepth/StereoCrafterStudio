"""settings.py -- Scene Cut settings: v1's saved keys, v1's types.

Keys are ``config_scenecut.json``'s (v1 ``_save_settings``), so seeding is a
copy and a file saved by either version loads in the other. ``detector_params``
is v1's per-detector cache of the parameter fields, as strings (they are
entry text in v1, cast at Start by the detector spec).

No Qt, no Tk, no torch here.
"""

from __future__ import annotations

import copy
from typing import Any, Dict

#: v1 SceneCutGUI.__init__ / _load_settings fallbacks, by the var's type.
STRINGS: Dict[str, str] = {
    "video_path": "",
    "output_dir": "",
    "detector_type": "AutoShot",
    "ffmpeg_codec": "hevc_nvenc",
    "ffmpeg_preset": "p7",
}
INTS: Dict[str, int] = {
    # v1's _load_settings falls back to 3 for a file without the key (the var
    # itself starts at 5 -- the load wins whenever a config exists).
    "ai_window": 3,
    "flash_sensitivity": 15,
    "num_images": 3,
    "frame_margin": 1,
    "sc_offset": 0,
    "ffmpeg_cq": 16,
}
BOOLS: Dict[str, bool] = {
    "refinements_enabled": True,
    "snap_cuts": True,
    "ai_validate": False,
    "export_csv": False,
    "export_html": False,
    "export_sc": False,
    "save_images": False,
    "split_video": True,                # user, 2026-10-18: Auto Crop needs the split
    "split_by_chapter": True,           # user, 2026-10-18: chapter folders
    # Auto Crop (user, 2026-10-11): each split shot cut to its own picture, for movies
    # whose aspect ratio switches (core.scenecut.autocrop)
    "auto_crop": True,                  # user, 2026-10-18
}

DEFAULTS: Dict[str, Any] = {**STRINGS, **INTS, **BOOLS, "detector_params": {}}

#: v1's spin boxes: key -> (from, to).
SPIN_RANGES = {"ai_window": (2, 10), "flash_sensitivity": (10, 80), "num_images": (1, 10),
               "frame_margin": (0, 30), "sc_offset": (-5, 5), "ffmpeg_cq": (16, 30)}

PRESETS = [f"p{i}" for i in range(1, 8)]


def coerce(key: str, value: Any) -> Any:
    try:
        if key in BOOLS:
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if key in INTS:
            v = int(float(value))
            lo, hi = SPIN_RANGES.get(key, (None, None))
            return v if lo is None else max(lo, min(hi, v))
        if key in STRINGS:
            return str(value)
    except (TypeError, ValueError):
        return DEFAULTS[key]
    return value


def normalise(values: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: coerce(k, values.get(k, v)) for k, v in DEFAULTS.items() if k != "detector_params"}
    params = values.get("detector_params")
    out["detector_params"] = copy.deepcopy(params) if isinstance(params, dict) else {}
    # v1 _load_settings: the codec is fixed, whatever a legacy file says.
    out["ffmpeg_codec"] = "hevc_nvenc"
    return out


def seed_from_v1(v1: Dict[str, Any]) -> Dict[str, Any]:
    return normalise(v1 or {})


def to_v1(values: Dict[str, Any]) -> Dict[str, Any]:
    """v1 _save_settings' dict, in its order."""
    keys = ["video_path", "output_dir", "detector_type", "detector_params", "refinements_enabled", "snap_cuts",
            "ai_validate", "ai_window", "flash_sensitivity", "export_csv", "export_html", "export_sc",
            "save_images", "split_video", "split_by_chapter", "num_images", "frame_margin", "sc_offset",
            "ffmpeg_codec", "ffmpeg_preset", "ffmpeg_cq"]
    return {k: values.get(k, DEFAULTS.get(k)) for k in keys}
