"""output.py -- where a Video Depth Anything map goes, and what it does to the map already there.

The map is <output>/<clip><suffix>.mp4, the name DepthCrafter writes, so Splatting finds it the same
way. When that name already holds a DepthCrafter map of the clip, the old map moves into
<output>/DepthCrafter/ first (user, 2026-10-25: "make a folder to place the dc map in so it
instantly shows the new results in splatting"): the VDA map takes its place, and the DepthCrafter
one is kept -- with Splatting's Multi-Map on, that folder is one of the maps it offers.

Which maps are VDA's: each carries a container tag (TAG) saying so; a map without it is taken for
DepthCrafter's (or one the Depth Map Editor saved over it). A VDA map in the way is replaced.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from typing import Optional

logger = logging.getLogger(__name__)

#: The tag a VDA map carries (format tag "comment").
TAG = "Video Depth Anything (StereoCrafter Studio)"
#: Where the DepthCrafter map it replaces goes, inside the output folder.
SET_ASIDE = "DepthCrafter"

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def map_path(output_dir: str, basename: str, suffix: str = "_depth") -> str:
    return os.path.join(output_dir, f"{basename}{suffix}.mp4")


def is_vda_map(path: str) -> bool:
    """Whether the map at `path` is one VDA wrote (its tag)."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format_tags=comment", "-of", "json", path],
                             capture_output=True, text=True, timeout=30, creationflags=_NO_WINDOW)
        tags = (json.loads(out.stdout or "{}").get("format") or {}).get("tags") or {}
        return any(str(v) == TAG for k, v in tags.items() if k.lower() == "comment")
    except Exception:
        logger.debug("Could not read the tags of %s", path, exc_info=True)
        return False


#: How much of a map's end tagged() reads: ffmpeg's MP4 muxer puts the tags last (measured: the tag ends 43 bytes
#: from the end of a 24- and a 3000-frame map alike).
TAIL = 4096


def tagged(path: str) -> bool:
    """Whether the map at `path` is VDA's, by its tag in the file's last bytes -- no ffprobe, so the clip list's
    badges (studio.clip_queue) can ask for every clip on the UI thread."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - TAIL))
            return TAG.encode("utf-8") in f.read()
    except OSError:
        return False


def _free_name(folder: str, name: str) -> str:
    dest = os.path.join(folder, name)
    if not os.path.exists(dest):
        return dest
    base, ext = os.path.splitext(name)
    return os.path.join(folder, f"{base}{time.strftime('_%Y%m%d%H%M%S')}{ext}")


def _move(src: str, dest: str, tries: int = 10) -> None:
    """os.replace, retried a moment while another reader lets go of the file (Windows locks open files)."""
    for i in range(tries):
        try:
            os.replace(src, dest)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(0.3)


def set_aside(path: str) -> Optional[str]:
    """Clear `path` for a new VDA map: a DepthCrafter map there moves to <its folder>/DepthCrafter/
    (a later one beside an earlier: a timestamp in its name). Returns where it went; None when there
    was nothing to keep (no file, or VDA's own earlier map, which the new one replaces)."""
    if not os.path.isfile(path) or is_vda_map(path):
        return None
    folder = os.path.join(os.path.dirname(path), SET_ASIDE)
    os.makedirs(folder, exist_ok=True)
    dest = _free_name(folder, os.path.basename(path))
    _move(path, dest)
    logger.info("The DepthCrafter map %s moved to %s; the Video Depth Anything map takes its place.",
                os.path.basename(path), dest)
    return dest


def publish(part: str, final: str) -> Optional[str]:
    """Put the finished `part` at `final`, setting aside the DepthCrafter map there first. Returns
    where that map went (None: there wasn't one). Raises when the new map can't be put in place."""
    moved = set_aside(final)
    _move(part, final)
    return moved
