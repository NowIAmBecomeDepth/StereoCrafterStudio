"""settings_store.py -- Studio's own settings files, seeded once from the
Prototype's.

Every settings file lives in ``config/`` at the repo root, one JSON file
per owner (``studio.json`` for the shell, ``depthcrafter.json`` for the stage).
The first time an owner is loaded and its file does not exist, it is seeded
from the matching v1 file if there is one, and saved straight away. From then
on the two versions never read or write each other's settings.

Writes go through a temp file and ``os.replace`` so a crash mid-save cannot
leave a truncated file behind.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .legacy import REPO

logger = logging.getLogger(__name__)

#: Where the settings lived until 2026-10-02 (user: no "v2" in any name). A
#: Studio updated from then finds its settings there and moves them across,
#: once, on its first load or save -- only for the real folder, never one a
#: test points CONFIG_DIR at.
DEFAULT_DIR = REPO / "config"
#: The unit tests point it at a temp folder before anything loads
#: (studio/tests/__init__.py), so no test can write the user's settings.
CONFIG_DIR = (Path(os.environ["STEREOCRAFTER_TEST_CONFIG_DIR"])
              if os.environ.get("STEREOCRAFTER_TEST_CONFIG_DIR") else DEFAULT_DIR)
OLD_DIR = REPO / ("config" + "_v2")
_moved = False


def _move_old_dir() -> None:
    global _moved
    if _moved:
        return
    _moved = True
    if CONFIG_DIR != DEFAULT_DIR or CONFIG_DIR.exists() or not OLD_DIR.is_dir():
        return
    try:
        os.replace(OLD_DIR, CONFIG_DIR)
        logger.info("Moved Studio's settings from %s to %s.", OLD_DIR.name, CONFIG_DIR.name)
    except OSError:
        logger.warning("Could not move %s to %s; starting from fresh settings.", OLD_DIR, CONFIG_DIR,
                       exc_info=True)


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        from core.common.atomic_json import read_json
        data = read_json(str(path))           # a moment's lock is retried, not taken as unreadable
        return data if isinstance(data, dict) else None
    except FileNotFoundError:
        return None
    except Exception:
        logger.warning("Could not read %s", path, exc_info=True)
        return None


def load(owner: str, seed_from: Optional[Path] = None,
         seed_filter: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Settings for `owner`, seeding from a v1 file on first use.

    `seed_filter` picks and renames what is taken from the v1 file; the v1
    file itself is only ever read.
    """
    _move_old_dir()
    path = CONFIG_DIR / f"{owner}.json"
    data = _read_json(path)
    if data is not None:
        return data
    seeded: Dict[str, Any] = {}
    # Only the real settings folder seeds from the Prototype's files: they hold
    # the user's own project folders, and a test's fresh settings seeded from
    # them pointed its stages at the user's footage (2026-10-10:
    # check_depthedit_undo's 3D view scanned the real Splatting input folder,
    # and the previews save sidecars beside what they load). A test starts
    # from the defaults.
    if seed_from is not None and CONFIG_DIR != DEFAULT_DIR:
        logger.debug("Not seeding %s from %s: a test's settings folder", owner, seed_from.name)
        seed_from = None
    if seed_from is not None:
        v1 = _read_json(seed_from)
        if v1:
            seeded = seed_filter(v1) if seed_filter else dict(v1)
            logger.info("Seeded %s settings from %s", owner, seed_from.name)
    if path.exists():
        # There but unreadable (a hand edit, a sync conflict, a crash mid-write):
        # kept before the seed is written over it (bug sweep 2026-10-09 A1: it was
        # replaced with no copy). Not even a copy possible: nothing is written.
        kept = _keep_aside(path)
        if kept is None:
            logger.error("Could not read %s, nor keep a copy of it: using %s's defaults this session "
                         "without saving over it.", path, owner)
            return seeded
        logger.warning("Could not read %s; kept it as %s and started %s from %s.", path.name, kept.name,
                       owner, "its seed" if seeded else "the defaults")
    save(owner, seeded)
    return seeded


def _keep_aside(path: Path) -> Optional[Path]:
    """A copy of ``path`` as <name>.unreadable (or -<time> when that exists), or None."""
    import shutil
    import time
    kept = path.with_name(path.name + ".unreadable")
    if kept.exists():
        kept = path.with_name(f"{path.name}.unreadable-{time.strftime('%Y%m%d-%H%M%S')}")
    try:
        shutil.copy2(path, kept)
        return kept
    except OSError:
        logger.warning("Could not copy %s aside", path, exc_info=True)
        return None


def save(owner: str, data: Dict[str, Any]) -> bool:
    _move_old_dir()
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        path = CONFIG_DIR / f"{owner}.json"
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
        os.replace(tmp, path)
        return True
    except Exception:
        logger.warning("Could not save %s settings", owner, exc_info=True)
        return False


def local_option(key: str, default: Any = None, config_dir=None) -> Any:
    """One of this machine's own options, from ``config_dir / local.json``
    (default CONFIG_DIR, which git ignores, so they never ship) -- tuning for
    one PC that would be wrong on others, such as DepthCrafter's VRAM cap
    (user, 2026-10-02). `default` when the file or the key is missing."""
    data = _read_json(Path(config_dir or CONFIG_DIR) / "local.json") or {}
    return data.get(key, default)


#: the largest side a preset size may have (a hand-edited file's 99999 never reaches a menu)
PRESET_MAX = 16384


def local_presets(filename: str, config_dir=None) -> list:
    """This machine's own extra preset columns (user, 2026-10-01), from
    ``config_dir / filename`` (default CONFIG_DIR, which git ignores, so they
    never ship), in the stages' preset shape [(category, [(w, h, tag), ...])]:

        {"presets": [{"category": "2.2:1", "sizes": [[960, 448], [1280, 576, "720p"]]}]}

    [] when there is no file or it will not read."""
    path = Path(config_dir if config_dir is not None else CONFIG_DIR) / filename
    if not path.is_file():
        return []
    try:
        data = _load_json_strict(path)
        out = []
        for col in data.get("presets", []):
            # entry by entry: one bad size read the whole file as empty, and the next save
            # rewrote it without its hand-written columns; a 0 height divided by zero in the
            # menus (bug sweep 2026-10-14c)
            sizes = []
            for e in col.get("sizes", []):
                try:
                    w, h = int(e[0]), int(e[1])
                except (TypeError, ValueError, IndexError):
                    w = h = 0
                if not (0 < w <= PRESET_MAX and 0 < h <= PRESET_MAX):
                    logger.warning(f"Ignoring the size {e!r} in {path}")
                    continue
                sizes.append((w, h, (str(e[2]) if len(e) > 2 else None)))
            if sizes:
                out.append((str(col.get("category", "")), sizes))
        return out
    except Exception as e:  # a hand-edited file: never stop a stage loading
        logger.warning(f"Ignoring {path}: {e}")
        return []


def _load_json_strict(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
