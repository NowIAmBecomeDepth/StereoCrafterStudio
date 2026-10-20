"""mask_side.py -- which half of a dual-eye _splatted2D grid holds the mask.

Merging's Load Preview used to find out for every dual-eye clip in the
folder, at scan time: a decord reader opened and frame 0 decoded per clip,
~0.1-0.3 s each, on every Load and after every batch (bug sweep 2026-10-09
#19). Now it is found for a clip when the clip is first shown (the preview
blend, Grain Match) and kept on the clip's entry. Frame 0, as the batch
reads it (engine.py's run_batch_process).
"""

from __future__ import annotations

import logging
from typing import Dict

logger = logging.getLogger(__name__)


def mask_side_of(src: Dict) -> str:
    """"left" or "right" for the clip entry `src` (a preview video_list dict),
    found once and kept there. "left" when it can't be read, as before."""
    side = src.get("mask_side")
    if side is None:
        side = "left"
        if src.get("is_dual_eye_sbs") and src.get("splatted"):
            try:
                import torch  # noqa: F401  -- before decord (it segfaults on import otherwise here)
                from decord import VideoReader, cpu

                from .engine import detect_mask_panel_side
                reader = VideoReader(src["splatted"], ctx=cpu(0))
                try:
                    side = detect_mask_panel_side(reader.get_batch([0]).asnumpy()[0])
                finally:
                    del reader
            except Exception:
                logger.debug("mask side of %s not read; left", src.get("splatted"), exc_info=True)
        src["mask_side"] = side
    return side
