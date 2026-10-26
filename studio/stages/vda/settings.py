"""settings.py -- Video Depth Anything's settings: defaults, coercion, run snapshot.

Its own folders (user, 2026-10-25), under DepthCrafter's key names: the clip list (clip_queue's
"depthcrafter" kind), Send to Splatting (send_on) and a project's chapters read them as they read
DepthCrafter's. Stored in config/vda.json.

No Qt and no torch here: the runner, the controller and the tests all use it.
"""

from __future__ import annotations

import math
from typing import Any, Dict

from core.vda.model import DEFAULT_INPUT, SIZES, snap_input
from core.vda.normalize import MODES as NORMALISE_MODES
from studio.default_folders import localize as _localize

DEFAULTS: Dict[str, Any] = {
    "input_dir_or_file_var": "./input_clips",
    "output_dir": "./output_depthmaps",
    "model_size": "Large",
    "input_size": DEFAULT_INPUT,
    "run_mode": "Online",
    "normalise_mode": "Whole clip",
    "ema_decay": 0.9,
    "ema_buffer": 30,
    "use_fp16": True,
    "process_length": -1,
    "output_suffix": "_depth",
    "move_to_finished": True,
    # the user's own checkpoints folder -- iw3's, say (user, 2026-10-25); "" = the models folder only
    "weights_dir": "",
}
DEFAULTS = _localize(DEFAULTS)

RUN_MODES = ["Online", "Offline"]

#: Input Size's menu (user, 2026-10-25: "a dropdown of compatible resolution numbers"): multiples of 14, the
#: only sizes the model takes -- every 56 (four patches) from 280, with 392 (iw3's default) and 518 (what the
#: model is trained at) among them. A size saved before that isn't listed still shows, and is kept.
INPUT_SIZES = [280, 336, 392, 448, 518, 560, 616, 672, 728, 784, 840, 896, 952, 1008]


def choices() -> Dict[str, list]:
    return {"model_size": list(SIZES), "run_mode": list(RUN_MODES), "normalise_mode": list(NORMALISE_MODES),
            "input_size": list(INPUT_SIZES)}


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
            number = int(float(value))      # (Input Size's menu holds numbers: not checked against it)
            if key == "input_size":
                return snap_input(number)
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
    """Every known key present and correctly typed; unknown keys dropped."""
    out = dict(DEFAULTS)
    for key, value in (data or {}).items():
        if key in DEFAULTS:
            out[key] = coerce(key, value)
    return out


def moves_finished(opts: Dict[str, Any], single: bool, test: bool) -> bool:
    """As DepthCrafter's Resume: a folder of clips moves each source into <input>/finished (or
    failed/) as it ends -- never a single clip or a Test Render."""
    return not single and not test and bool(opts.get("move_to_finished", True))


def snapshot(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Freeze everything a run reads. Taken on the UI thread at Start."""
    return normalise(settings)
