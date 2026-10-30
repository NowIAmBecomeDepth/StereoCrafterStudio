"""clip_names.py -- is this file one of this clip's? (bug sweep 2026-10-14c)

Stages name their outputs for the clip: ``<clip>_depth.mp4``, ``<clip>_<width>_splatted2.mp4``,
``<clip>_<width>_inpainted_right_eye.mp4``, ``<clip>_<width>_merged_full_sbs.mov``... A search for
``<clip>_*`` also takes a longer clip's (clip ``shot`` took ``shot_02_1920_splatted2.mp4``): Move to
Finished moved the other clip's splat, Inpainting read its hi-res. A file is the clip's when what
follows the clip's name is an extension, or a stage's word (after ``_<width>`` for the outputs
that carry one).
"""

from __future__ import annotations

import re

#: what may follow "<clip>" in one of its own files' names: a stage's word, with "_<width>" before
#: it for the outputs that carry one (a depth map doesn't: "shot_02_depth" is clip shot_02's)
STAGE_WORDS = ("depth", "splatted", "splat", "mask", "inpainted", "merged")
WIDTH_WORDS = ("splatted", "splat", "inpainted", "merged")
_OWN = re.compile(r"(?:_\d+_(?:" + "|".join(WIDTH_WORDS) + r")|_(?:" + "|".join(STAGE_WORDS) + r"))",
                  re.IGNORECASE)


_DIGITS = re.compile(r"(\d+)")


def natural_key(name: str) -> tuple:
    """Sort key for names in number order: clip_110 < clip_1009 < clip_1100 (a tester, 2026-10-29:
    a plain sort put clip_1100 after clip_110). Case-blind; the plain name breaks ties. The split
    puts text at the even places and numbers at the odd ones, so two keys compare like with like."""
    parts = _DIGITS.split(name.lower())
    return (tuple(int(p) if i % 2 else p for i, p in enumerate(parts)), name)


def is_own_file(stem: str, name: str) -> bool:
    """True when file `name` (a basename) is clip `stem`'s own: ``stem`` then ``.ext``, or a
    stage's word (``_<width>`` first for splats, inpainted and merged outputs). Case-blind."""
    if not stem or not name.lower().startswith(stem.lower()):
        return False
    rest = name[len(stem):]
    return rest.startswith(".") or bool(_OWN.match(rest))
