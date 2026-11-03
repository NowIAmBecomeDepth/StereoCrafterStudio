"""settings.py -- Edge Fill's settings (Extra > Edge Fill; user, 2026-10-14).

Edge Fill began as a dialog on Merging (2026-10-13); its settings were Merging's
``edgefill_*`` keys, so a first start seeds them -- and the folders, from
Merging's Output and Mask folders -- from ``config/merging.json``.

No Qt here.
"""

from __future__ import annotations

from typing import Any, Dict

STRINGS: Dict[str, str] = {
    "merged_folder": "",       # the merged clips (Full SBS and / or _L / _R)
    "mask_folder": "",         # their splats (_splatted2*), which say how wide each strip is
    "size": "560 (sharper texture)",
    # Pre-fill (user, 2026-10-26): what ROSE sees under its mask -- it isn't blanked
    "prefill": "Blur",
    # Inpainter (user, 2026-11-03): MiniMax-Remover (core/removal/minimax_env.py; the default: "Make it the
    # default"), or ROSE
    "inpainter": "MiniMax-Remover",
    # MiniMax-Remover's steps (user, 2026-11-03: "add the 12 step on to the minimax options"): 6 or 12
    "mmr_steps": "6",
}
INTS: Dict[str, int] = {
    "steps": 6,                # ROSE's denoising steps per window
    "tile": 200,               # how wide the grid's stills are, px
}
BOOLS: Dict[str, bool] = {
    "grain": True,
    # Match Detail (user, 2026-10-14): ROSE's fill sharpened to the picture's fine detail
    "detail": True,
    # Right Edge Too (user, 2026-10-18): the strip is looked for on the right eye's left edge, where
    # Splatting leaves it; on, its right edge too
    "both_edges": False,
    # Copy From Other Frames (user, 2026-11-02): the real picture copied into the strip from the frames
    # where a pan or a move shows it (SEA-RAFT flow, core/removal/flow_fill.py); ROSE fills the rest
    "copy_frames": True,
}
DEFAULTS: Dict[str, Any] = {**STRINGS, **INTS, **BOOLS}

#: ROSE's working height for the edge crop: 560 keeps texture, 784 straighter lines (2026-10-13);
#: Native: the clip's own size in tiles, the sharpest and ~2.4x 560's time (user, 2026-10-14)
SIZES = ["560 (sharper texture)", "784 (straighter lines)", "Native (tiled, sharpest)"]
TILE_RANGE = (120, 420)
#: Blur: a wide blur from beside the strip (erase.prefill_frames); Stretched: the last good column
#: across it, as before 2026-10-26 (it came back as horizontal streaks)
PREFILLS = ["Blur", "Stretched"]
#: what paints the strip (core/removal/edge_fill.INPAINTERS)
INPAINTERS = ["MiniMax-Remover", "ROSE"]
#: MiniMax-Remover's steps (core/removal/minimax_env.STEP_CHOICES)
MMR_STEPS = ["6", "12"]


def height(value: Any) -> int:
    """The Size choice's ROSE height (560 for anything unreadable; 0: Native, tiled)."""
    if str(value).strip().lower().startswith("native"):
        return 0
    try:
        return int(str(value).split()[0])
    except (ValueError, IndexError):
        return 560


def coerce(key: str, value: Any) -> Any:
    try:
        if key in BOOLS:
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if key in INTS:
            v = int(float(value))
            if key == "tile":
                v = max(TILE_RANGE[0], min(TILE_RANGE[1], v))
            return v
        if key == "size":
            return value if value in SIZES else SIZES[0]
        if key == "prefill":
            return value if value in PREFILLS else PREFILLS[0]
        if key == "inpainter":
            return value if value in INPAINTERS else STRINGS["inpainter"]
        if key == "mmr_steps":
            return str(value) if str(value) in MMR_STEPS else STRINGS["mmr_steps"]
        if key in STRINGS:
            return str(value)
    except (TypeError, ValueError, OverflowError):      # "inf" (bug sweep 2026-10-14)
        return DEFAULTS[key]
    return value


def normalise(values: Dict[str, Any]) -> Dict[str, Any]:
    return {k: coerce(k, (values or {}).get(k, v)) for k, v in DEFAULTS.items()}


def seed_from_merging(merging: Dict[str, Any]) -> Dict[str, Any]:
    """The Fill Edges dialog's settings and Merging's folders, from config/merging.json."""
    m = merging or {}
    return normalise({"merged_folder": m.get("output_folder", ""), "mask_folder": m.get("mask_folder", ""),
                      "steps": m.get("edgefill_steps", INTS["steps"]), "size": m.get("edgefill_size", SIZES[0]),
                      "grain": m.get("edgefill_grain", True), "tile": m.get("edgefill_tile", INTS["tile"])})
