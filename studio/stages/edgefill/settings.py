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
    # Depth Aware's depth maps (user, 2026-11-07: "use depth map"): <clip>_depth.mp4 here or in finished/; empty:
    # Splatting's Input Depth Maps; no map: depth measured from the two eyes
    "depth_folder": "",
    "size": "560 (sharper texture)",
    # Pre-fill (user, 2026-10-26): what ROSE sees under its mask -- it isn't blanked
    "prefill": "Blur",
    # Inpainter (user, 2026-11-03): MiniMax-Remover (core/removal/minimax_env.py; the default: "Make it the
    # default"), or ROSE
    "inpainter": "MiniMax-Remover",
    # MiniMax-Remover's steps (user, 2026-11-03: "add the 12 step on to the minimax options"): 6 or 12
    "mmr_steps": "6",
    # Method (user, 2026-11-07): Fill paints the strip (the Inpainter); Black Bar and Zoom hide it, the
    # same on both eyes, in one quick pass (core/removal/edge_fill.cover)
    "method": "Fill",
    # Copy From Other Frames' model (user, 2026-11-07): AllTracker (the default; core/removal/track_fill.py)
    # or SEA-RAFT (flow_fill.py)
    "copy_with": "AllTracker",
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
    # Depth Aware (user, 2026-11-07: "lets add depth map awareness too"): what is nearer than the background beside
    # the strip -- measured from the merged L / R (core/removal/depth_guard.py) -- is left as it is
    "depth_aware": True,
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
#: Copy From Other Frames' models (core/removal/edge_fill.COPIERS)
COPIERS = ["AllTracker", "SEA-RAFT"]
#: Fill, or the cheap covers (core/removal/edge_fill.METHODS)
METHODS = ["Fill", "Black Bar", "Zoom"]
#: MiniMax-Remover's steps (core/removal/minimax_env.STEP_CHOICES)
MMR_STEPS = ["6", "12"]


#: A clip's sidecar (user, 2026-11-07: "different models/settings per clip and run them all after", saved "like
#: splatting" with a Create / Update Sidecar button): the fill's settings; the folders, the grid and Right Edge Too
#: (the strips are measured on it) stay the page's
CLIP_KEYS = ("method", "inpainter", "mmr_steps", "steps", "size", "prefill", "grain", "detail", "copy_frames",
             "copy_with", "depth_aware")
#: ...kept beside the merged clips, by clip name, as the fill's record and strips are
CLIPS_FILE = ".edge_fill_sidecars.json"


def clip_values(values: Dict[str, Any]) -> Dict[str, Any]:
    return {k: coerce(k, values[k]) for k in CLIP_KEYS if k in values}


def load_clip_settings(folder: str) -> Dict[str, Dict[str, Any]]:
    """The clips' sidecars kept in `folder` ({} when none, or the file won't read)."""
    import json
    import os
    try:
        with open(os.path.join(folder, CLIPS_FILE), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {c: clip_values(v) for c, v in data.items() if isinstance(c, str) and isinstance(v, dict)}


def save_clip_settings(folder: str, data: Dict[str, Dict[str, Any]]) -> bool:
    """Keep the clips' sidecars in `folder` (the file removed when no clip has one). False: it couldn't."""
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


def fill_options(values: Dict[str, Any]) -> Dict[str, Any]:
    """What a fill of one clip runs with, from its settings (the shared ones, its own over them)."""
    v = normalise(values)
    try:
        steps = max(1, int(float(v.get("steps") or 6)))
    except (TypeError, ValueError, OverflowError):
        steps = 6
    if v["inpainter"] == "MiniMax-Remover":           # its own Steps: 6 or 12
        steps = int(v["mmr_steps"])
    return {"method": v["method"], "inpainter": v["inpainter"], "steps": steps, "height": height(v["size"]),
            "size": str(v["size"]).split(" ")[0], "prefill": v["prefill"], "grain": bool(v["grain"]),
            "detail": bool(v["detail"]), "both_edges": bool(v["both_edges"]), "copy_frames": bool(v["copy_frames"]),
            "copier": v["copy_with"], "depth_aware": bool(v["depth_aware"])}


def describe(o: Dict[str, Any]) -> str:
    """fill_options() said in a few words, for the console."""
    if o["method"] != "Fill":
        return f"{o['method']}, both eyes"
    how = (f"MiniMax-Remover, {o['steps']} steps" if o["inpainter"] == "MiniMax-Remover"
           else f"ROSE, {o['steps']} steps, pre-fill {o['prefill'].lower()}")
    return (f"{how}, size {o['size']}, grain {'on' if o['grain'] else 'off'}, detail {'on' if o['detail'] else 'off'}"
            + (f", copy with {o['copier']}" if o["copy_frames"] else ", no copy")
            + (", depth aware" if o["depth_aware"] else ""))


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
        if key == "copy_with":
            return value if value in COPIERS else STRINGS["copy_with"]
        if key == "method":
            return value if value in METHODS else STRINGS["method"]
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
