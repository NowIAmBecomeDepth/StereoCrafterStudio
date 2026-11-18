"""finished_chain.py -- a clip's files wherever its "finished" chain has them (tester carlobs, 2026-11-18).

Each stage moves what it has finished one "finished" level down (Splatting: <Source>/finished ->
<Source>/finished/finished; Inpainting: <Splats>/hires -> <Splats>/hires/finished), and a project gives Merging
those deeper folders -- but only the ones that exist when the stage is opened (resolve_subfolder), and a clip
done on its own (Splatting's single clip, Inpainting with no hi-res field) isn't moved at all. A lookup in that
one folder then missed the clip: "no original", or not in the Merge tab. These look through the chain instead.

No Qt here; core.common.sidecar_manager.find_video_by_core_name (Merging's every original lookup), the merge
preflight, Merging's list and its batch all use it.
"""

from __future__ import annotations

import glob
import os
from typing import Callable, Iterable, List, Optional

FINISHED = "finished"
#: how many levels above and below the folder are looked at
DEPTH = 2


def chain(folder: str, depth: int = DEPTH) -> List[str]:
    """The folders a clip's files may be in, those that exist, in the order to look: `folder`, the levels it
    was moved down from (its parents while they are "finished" folders), then the levels below it."""
    if not folder:
        return []
    folder = os.path.normpath(folder)
    out = [folder]
    up = folder
    for _ in range(depth):
        if os.path.basename(up).lower() != FINISHED:
            break
        up = os.path.dirname(up)
        out.append(up)
    down = folder
    for _ in range(depth):
        down = os.path.join(down, FINISHED)
        out.append(down)
    return [f for f in out if os.path.isdir(f)]


def dir_holding(folder: str, pattern: str, exts: Iterable[str] = (".mp4", ".mov", ".mkv")) -> str:
    """The first folder of `folder`'s chain holding a file that matches `pattern` (its own wildcards; callers
    escape names) with one of `exts`; `folder` itself when none does (the caller reports what's missing)."""
    for f in chain(folder):
        base = glob.escape(f)
        if any(os.path.isfile(m) for ext in exts for m in glob.glob(os.path.join(base, pattern + ext))):
            return f
    return folder


def newest_per_core(paths: List[str], core_of: Callable[[str], Optional[str]]) -> List[str]:
    """`paths` with only the newest file (by modified time) of each clip -- its core name, by `core_of` on the
    file name -- in `paths`' order. A low-res inpaint and a later hi-res one of the same clip differ only by
    width: the clip is merged once, from the one made last. A path with no core name is kept."""
    best = {}
    for p in paths:
        core = core_of(os.path.basename(p))
        if not core:
            continue
        try:
            t = os.path.getmtime(p)
        except OSError:
            t = 0.0
        key = core.lower()
        if key not in best or t > best[key][0]:
            best[key] = (t, p)
    keep = {v[1] for v in best.values()}
    return [p for p in paths if p in keep or not core_of(os.path.basename(p))]
