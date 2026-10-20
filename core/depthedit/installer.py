"""installer.py -- install the Depth Map Editor's missing packages from inside the app.

Someone who has graduated out of Beginner Mode should be able to turn on an
optional feature without a terminal. The packages are already declared in
pyproject.toml; this runs uv against it.

Two rules, both about not surprising the person pressing "Install":

  * The plan is always shown first (``uv sync --dry-run``) and nothing runs
    until it is confirmed.
  * ``--inexact``. Plain ``uv sync`` makes the environment match the lockfile
    EXACTLY, which removes anything installed by hand that pyproject does not
    list. Nobody expects an Install button to uninstall things; --inexact
    only ever adds and updates.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Optional, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[2]
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_uv() -> Optional[str]:
    found = shutil.which("uv")
    if found:
        return found
    # The standalone installer's default location, which a GUI launched from
    # Explorer may not have on PATH.
    for candidate in (Path.home() / ".local" / "bin" / "uv.exe", Path.home() / ".cargo" / "bin" / "uv.exe"):
        if candidate.is_file():
            return str(candidate)
    return None


def _run(args, on_line: Optional[Callable[[str], None]] = None) -> Tuple[int, str]:
    uv = find_uv()
    if uv is None:
        return 127, "uv was not found. Install it from https://docs.astral.sh/uv/ or run the StereoCrafter installer."
    env = dict(os.environ)
    # uv colours its output for a terminal; a dialog shows the escape codes.
    env["NO_COLOR"] = "1"
    proc = subprocess.Popen([uv, *args], cwd=str(_REPO_ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", env=env, creationflags=_NO_WINDOW)
    lines = []
    for line in proc.stdout:
        line = line.rstrip()
        lines.append(line)
        if on_line is not None:
            on_line(line)
    return proc.wait(), "\n".join(lines)


def plan() -> Tuple[int, str]:
    """What `install()` would change. Writes nothing, not even uv.lock."""
    return _run(["sync", "--inexact", "--dry-run"])


def install(on_line: Optional[Callable[[str], None]] = None) -> Tuple[int, str]:
    return _run(["sync", "--inexact"], on_line)
