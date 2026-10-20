"""chapter_paths.py -- where a chapter's clips actually are, "finished" depth and all.

core.ui.chapters (v1's, shared) works out a chapter's folders from the
pipeline's convention: each stage moves the clips it has used one "finished"
level down, so Splatting reads <source>/finished, Merging <source>/finished/
finished, and so on. Re-running a job breaks the count (user, 2026-09-27): the
used clips go into a NEW finished folder inside the last one, so the next pick
of that chapter points one level short and finds nothing.

So for a stage's INPUT folders, the convention is only the first guess: the
chain <chapter>, <chapter>/finished, <chapter>/finished/finished, ... is walked,
and the level that actually holds clips is used -- the expected one when it
does, else the nearest that does (deeper first on a tie: re-runs only ever go
down). Outputs are left where the convention puts them. v1 is not touched.
"""

from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("studio.chapter_paths")

#: What counts as the clips a stage reads (videos, or an image sequence).
MEDIA_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".mxf",
              ".png", ".exr", ".tif", ".tiff", ".jpg", ".jpeg"}
#: A finished chain deeper than this is not followed.
MAX_DEPTH = 12
FINISHED = "finished"

#: Each stage's input roles (stage_paths' field roles); the rest are outputs.
INPUT_ROLES = {
    "depthcrafter": ("input",),
    "splatting": ("source", "depth"),
    "inpainting": ("lowres", "hires"),
    "merging": ("inpainted", "original", "mask"),
}


def _split_finished(path: str) -> Tuple[str, int]:
    """(the folder above every trailing finished/, how many there were)."""
    p = os.path.normpath(path)
    depth = 0
    while os.path.basename(p).lower() == FINISHED and depth < MAX_DEPTH:
        p = os.path.dirname(p)
        depth += 1
    return p, depth


def finished_chain(base: str) -> List[str]:
    """base, base/finished, base/finished/finished, ... as far as they exist."""
    chain: List[str] = []
    p = base
    while os.path.isdir(p) and len(chain) <= MAX_DEPTH:
        chain.append(p)
        nxt = next((os.path.join(p, n) for n in _listdir(p)
                    if n.lower() == FINISHED and os.path.isdir(os.path.join(p, n))), None)
        if nxt is None:
            break
        p = nxt
    return chain


def _listdir(path: str) -> List[str]:
    try:
        return os.listdir(path)
    except OSError:
        return []


def has_media(path: str) -> bool:
    """Any clip (or frame) directly in `path`."""
    try:
        with os.scandir(path) as it:
            for entry in it:
                if entry.is_file() and os.path.splitext(entry.name)[1].lower() in MEDIA_EXTS:
                    return True
    except OSError:
        pass
    return False


def locate(path: str) -> str:
    """Where along `path`'s finished chain the clips are (see the module doc).
    `path` itself when none of it holds any, or it is a file."""
    if not path or os.path.isfile(path):
        return path
    base, expected = _split_finished(path)
    chain = finished_chain(base)
    if not chain:
        return path
    expected = min(expected, len(chain) - 1)
    if has_media(chain[expected]):
        return chain[expected]
    found = [i for i, d in enumerate(chain) if has_media(d)]
    if not found:
        return path
    best = min(found, key=lambda i: (abs(i - expected), -i))
    return chain[best]


def resolve(stage: str, paths: Dict[str, str]) -> Dict[str, str]:
    """stage_paths' answer, each input moved to where its clips are."""
    out = dict(paths)
    for role in INPUT_ROLES.get(stage, ()):
        p = out.get(role)
        if not p:
            continue
        found = locate(p)
        if os.path.normcase(os.path.normpath(found)) != os.path.normcase(os.path.normpath(p)):
            logger.info("%s %s: the clips are in %s (expected %s)", stage, role, found, p)
        out[role] = found.replace("\\", "/")
    return out


def same_place(a: Optional[str], b: Optional[str]) -> bool:
    """Two paths to the same folder, or to levels of one finished chain -- a
    chapter's field is still the chapter's after a job moves its clips down."""
    if not a or not b:
        return (a or "") == (b or "")
    na, nb = (os.path.normcase(os.path.normpath(str(x))) for x in (a, b))
    if na == nb:
        return True
    return _split_finished(na)[0] == _split_finished(nb)[0]
