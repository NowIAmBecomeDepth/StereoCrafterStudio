"""settings.py -- DVD's settings: defaults, coercion, run snapshot.

Its own folders, under DepthCrafter's key names as VDA's are: the clip list (clip_queue's "depthcrafter"
kind), Send to Splatting and a project's chapters read them as they read DepthCrafter's. Stored in
config/dvd.json.

No Qt and no torch here: the runner, the controller and the tests all use it.
"""

from __future__ import annotations

import math
from typing import Any, Dict

from core.dvd.model import (DEFAULT_INPUT, DEFAULT_OVERLAP, DEFAULT_WIDTH, DEFAULT_WINDOW, MAX_WINDOW, MIN_WINDOW,
                            snap_input, snap_width, snap_window)
from core.vda.formats import FORMATS as OUTPUT_FORMATS
from core.vda.normalize import MODES as NORMALISE_MODES
from studio.default_folders import localize as _localize

DEFAULTS: Dict[str, Any] = {
    "input_dir_or_file_var": "./input_clips",
    "output_dir": "./output_depthmaps",
    "input_size": DEFAULT_INPUT,
    "width": DEFAULT_WIDTH,
    "early_downsample": False,      # Billy's is on; off keeps the short side at Input Size (DVD's 480)
    "window": DEFAULT_WINDOW,
    "overlap": DEFAULT_OVERLAP,
    "normalise_mode": "Whole clip",
    "ema_decay": 0.9,
    "ema_buffer": 30,
    "process_length": -1,
    "output_suffix": "_depth",
    "output_format": OUTPUT_FORMATS[0],     # core.vda.formats (user, 2026-11-13); the first: the maps as they were
    "move_to_finished": True,
    "use_tensorrt": False,          # the VAE on TensorRT (core.dvd.vae_trt; user, 2026-11-07)
}
DEFAULTS = _localize(DEFAULTS)

#: Input Size's menu: the short side, multiples of 16. 480 is DVD's training scale (480x640).
INPUT_SIZES = [320, 384, 480, 576, 640, 720]
#: Window's menu: Billy's GUI's 9..81 in steps of 9; 81 is upstream's default and the longest Wan takes.
WINDOWS = list(range(MIN_WINDOW, MAX_WINDOW + 1, 9))


def choices() -> Dict[str, list]:
    return {"normalise_mode": list(NORMALISE_MODES), "input_size": list(INPUT_SIZES), "window": list(WINDOWS),
            "output_format": list(OUTPUT_FORMATS)}


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
        if isinstance(default, int):
            number = int(float(value))      # (the menus hold numbers: not checked against them)
            if key == "input_size":
                return snap_input(number)
            if key == "window":
                return snap_window(number)
            if key == "width":
                return snap_width(number)
            if key == "overlap":
                return max(0, number)
            if key == "ema_buffer":
                return max(1, number)
            return number
        if isinstance(default, float):
            number = float(value)
            if not math.isfinite(number):
                raise ValueError(value)
            if key == "ema_decay":
                return min(max(number, 0.0), 0.999)
            return number
        text = "" if value is None else str(value)
        menu = choices().get(key)
        return text if menu is None or text in menu else default
    except (TypeError, ValueError, OverflowError):
        return default


def normalise(data: Dict[str, Any]) -> Dict[str, Any]:
    """Every known key present and correctly typed; unknown keys dropped. The overlap is kept below the
    window (a window must move on by at least one frame)."""
    out = dict(DEFAULTS)
    for key, value in (data or {}).items():
        if key in DEFAULTS:
            out[key] = coerce(key, value)
    out["overlap"] = min(out["overlap"], out["window"] - 1)
    return out


def moves_finished(opts: Dict[str, Any], single: bool, test: bool) -> bool:
    """As DepthCrafter's Resume: a folder of clips moves each source into <input>/finished (or
    failed/) as it ends -- never a single clip or a Test Render."""
    return not single and not test and bool(opts.get("move_to_finished", True))


def snapshot(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Freeze everything a run reads. Taken on the UI thread at Start."""
    return normalise(settings)
