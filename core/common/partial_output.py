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


#: How long publish() keeps retrying a rename Windows refuses with "Access is
#: denied" (user, 2026-11-09: a Split L/R lost both eyes to it). A media
#: server, the search indexer or an antivirus scan opening a file in an output
#: folder holds it for a moment; the rename goes through once it lets go.
PUBLISH_RETRY_S = 5.0


def _free(path: str, fmt: str) -> str:
    """The first of fmt.format(path=..., n="") / n=" 2", " 3", ... that isn't taken."""
    out, n = fmt.format(n=""), 2
    while os.path.exists(out):
        out, n = fmt.format(n=f" {n}"), n + 1
    return out


def retire_old(path: str, kept_as: str = "") -> str:
    """A source clip or a final Splatting / Inpainting / Merging output that is being replaced only ever
    goes to the Recycle Bin, in case it is needed back (user, 2026-11-18): there, else -- no bin on its
    drive, too big for it -- kept as ``<kept_as or path>.old`` (``.old 2``, ...), outside every clip glob,
    never deleted. "" when nothing was there, "Recycle Bin", or the .old file's path. Raises OSError
    (PermissionError while it is open elsewhere) when it can't move."""
    if not os.path.isfile(path):
        return ""
    from core.common import recycle_bin
    try:
        recycle_bin.send_to_recycle_bin(path, may_delete_for_good=False)
    except Exception:
        logger.debug("Recycle Bin refused %s", path, exc_info=True)
    if not os.path.exists(path):
        logger.info(f"The earlier {os.path.basename(kept_as or path)} is in the Recycle Bin "
                    f"(as {os.path.basename(path)}).")
        return "Recycle Bin"
    old = _free(kept_as or path, (kept_as or path) + ".old{n}")
    os.rename(path, old)
    logger.warning(f"Kept the earlier {os.path.basename(kept_as or path)} as {os.path.basename(old)}: "
                   f"it couldn't go to the Recycle Bin.")
    return old


def replace_keeping_old(src: str, final: str) -> None:
    """os.replace(src, final), the earlier ``final`` set aside first as ``<stem> (earlier)<ext>`` and,
    once the new one is in, sent to the Recycle Bin under that name (retire_old). A refused replace
    puts the earlier one back, as it was (bug sweep 2026-10-09, A7). Raises what os.replace raises."""
    aside = ""
    if os.path.isfile(final):
        stem, ext = os.path.splitext(final)
        aside = _free(final, stem + " (earlier{n})" + ext)
        os.rename(final, aside)
    try:
        os.replace(src, final)
    except BaseException:
        if aside:
            try:
                os.rename(aside, final)
            except OSError:
                logger.error(f"The earlier {os.path.basename(final)} is kept as {os.path.basename(aside)}.")
        raise
    if aside:
        try:
            retire_old(aside, kept_as=final)
        except OSError as e:
            logger.warning(f"The earlier {os.path.basename(final)} stays as {os.path.basename(aside)}: {e}")


def publish(part: str, final: str, keep_old: bool = False) -> bool:
    """Put the finished ``part`` under its ``final`` name, replacing any
    earlier output there -- with `keep_old`, that one goes to the Recycle Bin
    (replace_keeping_old). False (logged) when it could not. A file another
    program has open only briefly (PermissionError) is retried for up to
    PUBLISH_RETRY_S seconds before giving up."""
    import time

    deadline = time.monotonic() + PUBLISH_RETRY_S
    delay = 0.1
    while True:
        try:
            if keep_old:
                replace_keeping_old(part, final)
                return True
            os.replace(part, final)
            return True
        except PermissionError as e:
            if time.monotonic() >= deadline:
                logger.error(f"Could not finish {final}: {e} (another program kept it open)")
                return False
            time.sleep(delay)
            delay = min(delay * 2, 1.0)
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
