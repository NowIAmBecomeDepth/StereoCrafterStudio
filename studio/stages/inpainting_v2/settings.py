"""settings.py -- Inpainting V2 (Wan VACE) settings.

Keys are v1's config keys (``config_inpaint_v2.json``), so seeding is a copy
and a settings file saved by either version loads in the other. Types follow
v1's tk variables, because the generated runner reads them through its
``*_var`` stand-ins exactly as v1 did: the six numeric fields are STRINGS
(v1 StringVars, parsed by v1's own ``_validate_and_gather`` at Start), the
toggles are bools.

No Qt, no Tk, no torch here.
"""

from __future__ import annotations

from typing import Any, Dict

from studio.default_folders import localize as _localize

#: v1 WanInpaintingGUI._init_vars fallbacks.
DEFAULTS: Dict[str, Any] = {
    "input_folder": "./output_splatted",
    "output_folder": "./completed_output",
    "frames_chunk": "81",
    "frames_overlap": "10",
    # 64, not v1's 128 (user, 2026-10-12): ~9% faster on a 97-frame 1408x768 clip, no seam at the blends
    "tile_overlap": "64",
    "tile_num": "2",
    "inference_steps": "10",
    "seed": "0",
    "offload_mode": "MMGP streaming (int8)",   # user, 2026-10-18 (was Group offload)
    "use_sageattention": True,
    # Torch Compile on the page (user, 2026-10-11): each transformer block compiled
    "use_compile": False,
    "free_text_encoder": True,
    "move_to_finished": True,
}
# the "./" folders under <Videos>/StereoCrafter Studio, not Studio's own folder (release check 2026-10-17;
# studio/default_folders.py)
DEFAULTS = _localize(DEFAULTS)

#: Kept and written back untouched: v1's own window/theme state.
PASSTHROUGH = ("dark_mode_enabled", "frosted_glass", "window_geometry")

#: v1's offload option menu, in its order.
OFFLOAD_MODES = ["None", "Transformer only", "Group offload",
                 "Group offload (disk)", "FP8 resident", "MMGP streaming (int8)"]

#: The numeric fields: v1 StringVars, validated at Start by v1.
NUMERIC = ("frames_chunk", "frames_overlap", "tile_overlap", "tile_num", "inference_steps", "seed")

BOOLS = ("use_sageattention", "use_compile", "free_text_encoder", "move_to_finished")


def coerce(key: str, value: Any) -> Any:
    if key in BOOLS:
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if key in NUMERIC:
        # v1 stores str(var.get()); a number from an older file becomes its text.
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return str(value).strip()
    if key in DEFAULTS:
        return str(value)
    return value


def normalise(values: Dict[str, Any]) -> Dict[str, Any]:
    """Every DEFAULTS key with v1's type; passthrough keys kept as found."""
    out = {k: coerce(k, values.get(k, v)) for k, v in DEFAULTS.items()}
    for k in PASSTHROUGH:
        if k in values:
            out[k] = values[k]
    return out


def seed_from_v1(v1: Dict[str, Any]) -> Dict[str, Any]:
    """config_inpaint_v2.json -> v2's file: the same keys, v1's types."""
    return normalise(v1 or {})
