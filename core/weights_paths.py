"""weights_paths.py -- where Studio's models are (user, 2026-10-20: "a setting to point Studio at
weights stored elsewhere").

One folder holds every model: <repo>/weights unless Settings > Studio > Models points somewhere
else (config/studio.json "weights_dir"; STEREOCRAFTER_WEIGHTS overrides both). A model that isn't
in the chosen folder is still found in <repo>/weights, so an install keeps working while its
models move one at a time; downloads always go to the chosen folder.

Every module that names a model folder resolves it through find() and keeps the answer in its own
global, which on_change() refreshes when the folder changes or a download lands (refresh()), so
code that reads those globals at job time needs no change.

No Qt and no torch here: core/ stays toolkit-neutral; Studio calls set_root() from its settings.
"""

from __future__ import annotations

import logging
import os
import weakref
from pathlib import Path
from typing import Callable, List, Optional, Union

logger = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parent.parent
#: Where the installer puts the models, and the fallback for any not in the chosen folder.
DEFAULT = REPO / "weights"
#: Overrides the setting, and is the only place looked in (the checks point it at a temp folder).
ENV = "STEREOCRAFTER_WEIGHTS"

_root: Optional[Path] = Path(os.environ[ENV]) if os.environ.get(ENV) else None
_fallback = not os.environ.get(ENV)
_listeners: list = []      # functions, or weakref.WeakMethod for a bound method


def root() -> Path:
    """The chosen models folder: where downloads go."""
    return _root if _root is not None else DEFAULT


def roots() -> List[Path]:
    """Where models are looked for, in order: the chosen folder, then <repo>/weights."""
    out = [root()]
    if _fallback and _norm(DEFAULT) != _norm(root()):
        out.append(DEFAULT)
    return out


def is_default() -> bool:
    return _norm(root()) == _norm(DEFAULT)


def set_root(path: Union[str, Path, None], fallback: bool = True) -> None:
    """Point every stage at `path` ("" or None: <repo>/weights). `fallback` False looks only
    there (tests). The environment variable, when set, wins over a setting and has no fallback."""
    global _root, _fallback
    if os.environ.get(ENV):
        path, fallback = os.environ[ENV], False
    _root = Path(path).expanduser() if path else None
    _fallback = bool(fallback)
    logger.info("Models folder: %s", root())
    refresh()


def find(*parts: str, marker: Optional[str] = None) -> Path:
    """<folder>/parts in the first folder of roots() that has it -- with `marker` inside it when
    given (a file the model can't be without) -- else under root(), where it would be downloaded."""
    for base in roots():
        cand = base.joinpath(*parts)
        try:
            if (cand / marker).is_file() if marker else cand.exists():
                return cand
        except OSError:
            pass
    return root().joinpath(*parts)


def on_change(fn: Callable[[], None]) -> Callable[[], None]:
    """Run `fn` after the folder changes or refresh() is called -- from whichever thread called
    it (a download's). Returns fn (a decorator). A bound method is held weakly, so a controller
    that goes away stops being called."""
    _listeners.append(weakref.WeakMethod(fn) if hasattr(fn, "__self__") and hasattr(fn, "__func__") else fn)
    return fn


def refresh() -> None:
    """Tell every module to look for its models again (a new folder, a finished download)."""
    for entry in list(_listeners):
        fn = entry() if isinstance(entry, weakref.WeakMethod) else entry
        if fn is None:
            _listeners.remove(entry)
            continue
        try:
            fn()
        except RuntimeError as e:          # a Qt object already deleted (a check's window closed)
            logger.debug("A model-path listener is gone: %s", e)
        except Exception:
            logger.exception("Could not update a model path")


def check_folder(path: Union[str, Path]) -> str:
    """Why `path` can't be the models folder, or "" when it can: it must exist (or be creatable)
    and take a file."""
    p = Path(path).expanduser()
    if not str(path).strip():
        return "No folder given."
    if not p.is_absolute():
        return "Give the whole path, with the drive."
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return f"Studio can't make that folder: {e.strerror or e}."
    if not p.is_dir():
        return "That is a file, not a folder."
    probe = p / ".studio_write_test"
    try:
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as e:
        return f"Studio can't write in that folder: {e.strerror or e}."
    return ""


def _norm(p: Path) -> str:
    try:
        return os.path.normcase(str(p.resolve()))
    except OSError:
        return os.path.normcase(os.path.abspath(str(p)))
