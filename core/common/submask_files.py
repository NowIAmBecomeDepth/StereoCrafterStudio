"""submask_files.py -- Detect Text's mask videos are not clips.

Detect Text saves <clip>_submask.mp4 beside the source clip (render_processor
.resolve_subtitle_mask finds it there). Anything that lists a source folder
as clips must skip it (user, 2026-10-02: Splatting listed it and splatted it
as a clip of its own, though the main render already uses it).
"""

from __future__ import annotations

import os

#: The mask video's name: the clip's, plus this, plus .mp4.
SUBMASK_SUFFIX = "_submask"


def is_subtitle_mask(path: str) -> bool:
    """True for a Detect Text mask video (<clip>_submask.<ext>)."""
    stem = os.path.splitext(os.path.basename(str(path or "")))[0]
    return stem.lower().endswith(SUBMASK_SUFFIX)
