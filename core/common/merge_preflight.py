"""merge_preflight.py -- Merging refuses to start when a clip's original is missing
(user, 2026-10-07: "it's still allowing the job to begin and only outputting the
blended right eye. i'd rather it fail so i know i need to fix the path").

A single-eye inpaint (`*_inpainted_right_eye*`, made from a `_splatted2` grid or a
`_splat.mov` split pair) has no left eye of its own: Merging takes it from the source
clip in the Original folder. Without it the batch used to carry on, write a black left
eye and log a warning. A `_splatted4` grid carries its own left eye, and a dual-eye SBS
(`*_inpainted_dual_sbs*`) is both eyes already: neither needs the original.

The same rules as the batch's own pairing (merging run_batch_process), applied to every
clip before the first one starts.

Since 2026-10-09 (user: "meet in the middle") the page ASKS rather than refusing: the
point was never to forbid a right-eye-only merge, only to stop one happening unnoticed
while the user was away. Continue merges those clips right eye only (no colour transfer
or grain auto-match: their reference would be a black frame); Cancel starts nothing.
"""
from __future__ import annotations

import glob
import os
import re
from typing import List, Optional, Sequence

from core.common.sidecar_manager import find_video_by_core_name

_RIGHT_EYE = re.compile(r"_inpainted_right_eyeF?\.(mp4|mov|mkv)$")
_SBS = re.compile(r"_inpainted_(dual_)?sbsF?\.(mp4|mov|mkv)$")
_DUAL_EYE_SBS = re.compile(r"_inpainted_dual_sbsF?\.(mp4|mov|mkv)$")


def core_name_of(base_name: str) -> Optional[str]:
    """The clip's name without its `_<width>_inpainted_...` tail, or None."""
    m = (_SBS if _SBS.search(base_name) else _RIGHT_EYE).search(base_name)
    if not m:
        return None
    with_width = base_name[: -len(m.group(0))]
    cut = with_width.rfind("_")
    return with_width[:cut] if cut != -1 else None


def _matches(folder: str, pattern: str) -> List[str]:
    """`pattern`'s own wildcards match; the folder is literal (callers escape names)."""
    if not folder:
        return []
    folder = glob.escape(folder)
    return glob.glob(os.path.join(folder, pattern + ".mp4")) + glob.glob(os.path.join(folder, pattern + ".mov"))


def needs_original(base_name: str, core_name: str, mask_folder: str,
                   explicit_splatted_file: Optional[str] = None) -> bool:
    """True when this clip's left eye must come from its source in the Original folder."""
    if _DUAL_EYE_SBS.search(base_name):
        return False                                    # both eyes already in the file
    if explicit_splatted_file:
        return "_splatted4" not in os.path.basename(explicit_splatted_file).lower()
    # names with [ ] * ? are literal, not patterns (bug sweep 2026-10-09)
    core = glob.escape(core_name)
    if _matches(mask_folder, f"{core}_*_splatted4*"):
        return False                                    # the quad grid carries the left eye
    if _matches(mask_folder, f"{core}_*_splatted2*"):
        return True
    if (glob.glob(os.path.join(glob.escape(mask_folder), f"{core}_*_splat.mov"))
            or glob.glob(os.path.join(glob.escape(mask_folder), "splat", f"{core}_*_splat.mov"))):
        return True
    return False                                        # no splat at all: the batch reports that itself


def missing_originals(inpainted_videos: Sequence[str], mask_folder: str, original_folder: str,
                      explicit_original_file: Optional[str] = None,
                      explicit_splatted_file: Optional[str] = None) -> List[str]:
    """The clips (core names) whose left eye must come from an original that isn't there."""
    if explicit_original_file and os.path.isfile(explicit_original_file):
        return []
    missing = []
    for path in inpainted_videos:
        base = os.path.basename(path)
        core = core_name_of(base)
        if not core or not needs_original(base, core, mask_folder, explicit_splatted_file):
            continue
        if not (original_folder and os.path.isdir(original_folder)
                and find_video_by_core_name(original_folder, core)):
            missing.append(core)
    return missing


def question(missing: Sequence[str], original_folder: str) -> str:
    """The prompt before such a batch starts: what is missing, and what Continue does."""
    shown = ", ".join(missing[:5]) + (f" and {len(missing) - 5} more" if len(missing) > 5 else "")
    where = original_folder or "(no Original folder set)"
    return (f"No original (left-eye) video for {len(missing)} clip(s): {shown}.\n\n"
            f"Original folder: {where}\n\n"
            f"Continue merges {'that clip' if len(missing) == 1 else 'those clips'} as the right eye only "
            f"(without colour transfer or grain auto-match). Cancel to fix the path first.")


def describe(missing: Sequence[str], original_folder: str) -> str:
    """The message for a refused start."""
    shown = ", ".join(missing[:5]) + (f" and {len(missing) - 5} more" if len(missing) > 5 else "")
    where = original_folder or "(no Original folder set)"
    return (f"No original (left-eye) video for {len(missing)} clip(s) in the Original folder: {shown}. "
            f"Original folder: {where}. Fix the path (or move the source clips there) and start again -- "
            f"without them Merging could only write the right eye.")
