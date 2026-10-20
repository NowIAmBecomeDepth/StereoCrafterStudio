"""tolerant.py -- load a list of saved entries, keeping the ones that load.

The sidecars' lists (keyframes, Dilate regions) were loaded all or nothing:
one malformed entry -- a hand edit, an older or foreign writer -- gave an
empty set, the preview kept it, and the next autosave deleted every other
entry from the file (bug sweep 2026-10-09 A6 / A11). Here a bad entry is
skipped and logged, and the rest are kept.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, List, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def load_each(items: Any, load: Callable[[Any], T], what: str) -> List[T]:
    """``load(item)`` for each of ``items`` that loads; a bad one is skipped with
    a warning naming ``what`` and its place. Not a list: nothing (warned)."""
    if items is None:
        return []
    if not isinstance(items, (list, tuple)):
        logger.warning(f"{what}: not a list ({type(items).__name__}); ignored.")
        return []
    out = []
    for i, item in enumerate(items):
        try:
            out.append(load(item))
        except Exception as e:
            logger.warning(f"{what} {i + 1} of {len(items)}: malformed, skipped ({e}).")
    return out
