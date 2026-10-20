"""partial_output.py -- write a video under a temporary name, publish it whole.

Every encoder in the pipeline used to write straight onto its final file
name. A Stop, a crash or an ffmpeg failure then left a truncated clip under
that name -- which the next stage picked up as finished work -- or, on a
re-run, destroyed the good output from the run before (audit 2026-09-28
#4 / #5).

The pattern here, used by every encoder:

    part = part_path(final)                 # <final>.part
    cmd += ffmpeg_output_args(part)         # -f <container> <final>.part
    ... encode ...
    publish(part, final)   on success       # os.replace: all or nothing
    discard(part)          on failure/Stop  # the old output is untouched

The ``.part`` suffix keeps a leftover (a hard crash can still leave one) out
of every ``*.mp4`` / ``*.mov`` glob, so no clip list or later stage ever
reads it; ffmpeg is told the container with ``-f``, since it can no longer
guess it from the extension.
"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import List, Optional

logger = logging.getLogger(__name__)

PART_SUFFIX = ".part"

#: ffmpeg muxer for each final extension (the .part name hides it).
_MUXERS = {".mp4": "mp4", ".m4v": "mp4", ".mov": "mov", ".mkv": "matroska"}


def part_path(final_path: str) -> str:
    """Where ``final_path`` is written while it is being encoded."""
    return str(final_path) + PART_SUFFIX


def is_part(path: str) -> bool:
    return str(path).endswith(PART_SUFFIX)


def final_ext(path: str) -> str:
    """The extension the finished file will have (".mp4" for "x.mp4.part")."""
    path = str(path)
    if is_part(path):
        path = path[: -len(PART_SUFFIX)]
    return os.path.splitext(path)[1].lower()


def ffmpeg_output_args(path: str) -> List[str]:
    """ffmpeg's output arguments for ``path``: ``[-f muxer] path``. Encoders
    append these in place of the bare path, so a caller can hand them either
    a final name or a ``.part`` one."""
    path = str(path)
    if is_part(path):
        muxer = _MUXERS.get(final_ext(path))
        if muxer:
            return ["-f", muxer, path]
    return [path]


def publish(part: str, final: str) -> bool:
    """Put the finished ``part`` under its ``final`` name, replacing any
    earlier output there. False (logged) when it could not."""
    try:
        os.replace(part, final)
        return True
    except OSError as e:
        logger.error(f"Could not finish {final}: {e}")
        return False


def discard(path: Optional[str]) -> None:
    """Remove an unfinished ``.part`` file, quietly."""
    if not path:
        return
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError as e:
        logger.warning(f"Could not remove the unfinished file {path}: {e}. Delete it by hand.")


def reap(proc: Optional[subprocess.Popen], timeout: float = 10.0) -> None:
    """End an ffmpeg that is being abandoned (Stop, an error) and wait for it,
    so its output file is closed before anyone deletes or reads it."""
    if proc is None:
        return
    try:
        if proc.stdin:
            try:
                proc.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=timeout)
    except Exception as e:
        logger.warning(f"Could not end ffmpeg cleanly: {e}")
