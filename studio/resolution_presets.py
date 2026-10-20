"""resolution_presets.py -- what DepthCrafter's and Splatting's Resolution
Preset menus share (user, 2026-10-11: Splatting got DepthCrafter's custom
presets and Auto Size): the aspect-ratio classes, a clip's shape, the sizes
offered for a ratio, and this machine's preset / auto-size files in config/.

The stages differ only in their numbers: DepthCrafter works in multiples of
64, Splatting's Low Res in multiples of 16 (both Inpainting engines take it:
V1 needs 8, V2 16). No Qt here.
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import tempfile
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: The aspect ratios offered, widest first.
CUSTOM_RATIOS = ("2.39:1", "2.35:1", "2.2:1", "2:1", "1.85:1", "16:9", "1.66:1", "3:2", "4:3", "5:4", "1:1")


def ratio_value(label: str):
    """'16:9' -> 1.777..., '2.39:1' -> 2.39; None when it isn't a ratio."""
    try:
        a, b = str(label).split(":")
        a, b = float(a), float(b)
        return a / b if a > 0 and b > 0 else None
    except (ValueError, ZeroDivisionError):
        return None


def disparity_label(w: int, h: int, ratio: float) -> str:
    """How far w x h is from the true ratio: 'exact', '2.3% wider', '1.5% taller'."""
    d = (w / h) / ratio - 1.0
    if abs(d) < 0.0005:
        return "exact"
    return f"{abs(d) * 100:.1f}% {'wider' if d > 0 else 'taller'}"


def ratio_class(ratio: Optional[float]) -> Optional[str]:
    """The CUSTOM_RATIOS label nearest `ratio` (by the ratio of the two, so
    2.39 and 2.35 stay apart and 1.33 never lands on 1.66); None for None."""
    if not ratio or ratio <= 0:
        return None
    return min(CUSTOM_RATIOS, key=lambda lab: abs(math.log(ratio / ratio_value(lab))))


def video_ratio(path: str) -> Optional[float]:
    """A video's display aspect ratio (width x pixel aspect / height) by
    ffprobe; None when it isn't a readable video."""
    import subprocess
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height,sample_aspect_ratio", "-of", "json", path],
            capture_output=True, text=True, timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        st = json.loads(out)["streams"][0]
        w, h = int(st["width"]), int(st["height"])
        sar = 1.0
        a, _, b = str(st.get("sample_aspect_ratio") or "1:1").partition(":")
        if a.isdigit() and b.isdigit() and int(a) > 0 and int(b) > 0:
            sar = int(a) / int(b)
        return w * sar / h if w > 0 and h > 0 else None
    except Exception as e:
        logger.debug(f"No aspect ratio for {path}: {e}")
        return None


def nearest_height(width: int, ratio: float, step: int) -> int:
    """The multiple of `step` whose width / height comes closest to `ratio`."""
    return min(range(step, 4097, step), key=lambda h: abs(width / h - ratio))


def sizes_for(ratio: float, step: int, width_from: int, max_w: int, max_h: int, min_h: int,
              both: bool, max_disparity: float = 0.10) -> List[Tuple[int, int, str]]:
    """Sizes offered for `ratio`: every width in steps of `step` from
    `width_from` up to `max_w`, each with a height in multiples of `step`
    within `max_disparity` of the ratio -- the multiples just under and just
    over the true height when `both` (the pick is the user's), else only the
    nearest. [(w, h, disparity label)]."""
    out = []
    for w in range(width_from, max_w + 1, step):
        exact = w / ratio
        if both:
            heights = sorted({int(exact // step) * step, int(-(-exact // step)) * step})
        else:
            heights = [nearest_height(w, ratio, step)]
        for h in heights:
            if h < min_h or h > max_h:
                continue
            if abs((w / h) / ratio - 1.0) > max_disparity:
                continue
            out.append((w, h, disparity_label(w, h, ratio)))
    return out


def offered(label: str, sizes_for, saved_cols, picks) -> list:
    """An editor's sizes for `label` -- the suggestions (`sizes_for(ratio)`),
    the sizes saved for it (typed in by hand, or from before) and its Auto pick,
    each once, smallest first: [{w, h, key, diff}]."""
    ratio = ratio_value(label)
    if not ratio:
        return []
    found = {(w, h) for w, h, _d in sizes_for(ratio)}
    for cat, items in saved_cols:
        if cat == label:
            found |= {(w, h) for w, h, _t in items}
    if label in picks:
        found.add(tuple(picks[label]))
    return [{"w": w, "h": h, "key": f"{w}x{h}", "diff": disparity_label(w, h, ratio)} for w, h in sorted(found)]


# ---- this machine's preset columns: config/<file> ----------------------------------

def read_presets(config_dir, filename: str) -> list:
    from studio.settings_store import local_presets
    return local_presets(filename, config_dir)


def save_presets(config_dir, filename: str, picked: Dict[str, list]) -> list:
    """Write the custom columns: {ratio label: [(w, h), ...]}, in CUSTOM_RATIOS'
    order (any other column already in the file kept after, as it was); a
    ratio with nothing picked is dropped; a size that stays keeps the tag it
    had ("720p"). Written whole, through a temp file. Returns what reads back."""
    from studio.settings_store import PRESET_MAX, _load_json_strict
    path = Path(config_dir) / filename
    if path.is_file():
        try:
            _load_json_strict(path)
        except Exception:
            # a file that won't read: kept beside, not overwritten with only the custom columns
            # (bug sweep 2026-10-14c)
            shutil.copyfile(path, str(path) + ".bak")
    old = {cat: sizes for cat, sizes in read_presets(config_dir, filename)}
    cols, done = [], set()
    order = list(CUSTOM_RATIOS) + [c for c in list(picked) + list(old) if c not in CUSTOM_RATIOS]
    for cat in order:
        if cat in done:
            continue
        done.add(cat)
        tags = {(w, h): t for w, h, t in old.get(cat, [])}
        if cat in picked:
            sizes = sorted({(int(w), int(h)) for w, h in picked[cat]
                            if 0 < int(w) <= PRESET_MAX and 0 < int(h) <= PRESET_MAX})
            entries = [[w, h] + ([tags[(w, h)]] if tags.get((w, h)) else []) for w, h in sizes]
        elif cat in old and cat not in CUSTOM_RATIOS:
            entries = [[w, h] + ([t] if t else []) for w, h, t in old[cat]]
        else:
            continue
        if entries:
            cols.append({"category": cat, "sizes": entries})
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write('{\n  "presets": [\n' + ",\n".join("    " + json.dumps(c) for c in cols) + "\n  ]\n}\n")
    os.replace(tmp, path)
    return read_presets(config_dir, filename)


# ---- Auto Size: each ratio's pick, config/<file> -----------------------------------

def load_auto_sizes(config_dir, filename: str, default: Callable[[str], Tuple[int, int]],
                    step: int) -> Dict[str, Tuple[int, int]]:
    """Every ratio's pick: the saved one (whole multiples of `step`), else default(label)."""
    saved = {}
    path = Path(config_dir) / filename
    if path.is_file():
        try:
            with open(path, "r", encoding="utf-8") as f:
                for lab, wh in (json.load(f).get("auto_sizes") or {}).items():
                    w, h = int(wh[0]), int(wh[1])
                    if lab in CUSTOM_RATIOS and w > 0 and h > 0 and w % step == 0 and h % step == 0:
                        saved[lab] = (w, h)
        except Exception as e:  # a hand-edited file: never stop the stage loading
            logger.warning(f"Ignoring {path}: {e}")
    return {lab: saved.get(lab) or default(lab) for lab in CUSTOM_RATIOS}


def save_auto_sizes(config_dir, filename: str, picks: Dict[str, tuple],
                    default: Callable[[str], Tuple[int, int]]) -> None:
    """Write the picks that differ from the default (so a later change to the
    default still reaches the ratios nobody chose for)."""
    keep = {lab: [int(wh[0]), int(wh[1])] for lab, wh in picks.items()
            if lab in CUSTOM_RATIOS and tuple(int(v) for v in wh) != tuple(default(lab))}
    path = Path(config_dir) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"auto_sizes": keep}, f, indent=2)
    os.replace(tmp, path)
