"""job_handoff.py -- remember what each stage last produced, so the next stage
can fill its input fields from it.

The pipeline is a chain: DepthCrafter -> Splatting -> Inpainting -> Merging.
Each stage's inputs are the previous stage's outputs, so retyping four paths
per stage is pure transcription work that this removes.

Why one chained record rather than one per stage
------------------------------------------------
Merging needs paths from TWO stages back, not one. Its Mask Folder comes from
Splatting's hi-res output and its Original Video Folder from Splatting's source
clips, while only its Inpainted Video Folder comes from Inpainting. So a record
that held only "the previous stage's outputs" could not fill Merging at all.

Instead each stage, when it finishes a job, copies the record it inherited and
adds its own fields. The record accumulates down the chain, and any stage can
read whatever upstream path it needs. That also means a stage that was skipped
(running Merging on clips inpainted yesterday) still leaves the upstream paths
intact rather than blanking them.

Stored at the repo root so every stage sees the same file whether it was
launched standalone or inside the unified shell, and so the handoff survives a
restart -- a batch that finishes overnight should still be importable.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Order matters: _inherit() walks backwards from a stage to find what to carry.
#
# Scene Cut sits ahead of the four numbered stages without being one of them.
# It is optional -- most jobs start at DepthCrafter -- but when it does run, the
# clips it writes are what DepthCrafter should be pointed at, and being first in
# this tuple is what makes DepthCrafter's import button appear. A record it
# never wrote leaves that button hidden, so nothing changes for jobs that skip
# it.
#
# The Depth Map Editor (Bonus tab) sits between DepthCrafter and Splatting the same
# way: optional, and when it has run, its edited map is what Splatting should
# import. Unlike Scene Cut it is in the MIDDLE of the chain, so "the stage
# before Splatting" can no longer mean one fixed stage -- see _upstream().
STAGE_ORDER = ("scenecut", "depthcrafter", "depthedit", "splatting", "inpainting", "merging")

#: Stages a job may skip. Looking upstream walks past them to the first stage
#: that is not optional, and takes whichever of those records is newest.
OPTIONAL_STAGES = frozenset({"depthedit"})

#: Stages recorded beside a chain stage rather than in the chain. Inpainting
#: V2 does Inpainting's job: its record inherits from the stages before
#: Inpainting, but lives under its own key, so it never stands in for V1's
#: record -- Merging offers each through its own import button.
SIDE_STAGES = {"inpainting_v2": "inpainting"}

#: The unit tests point it at a temp file before anything loads
#: (studio/tests/__init__.py): a test's job must never land in the user's record.
_RECORD_PATH = os.environ.get("STEREOCRAFTER_TEST_RECORD_PATH") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "last_jobs.json",
)


def _read_all() -> Dict[str, Any]:
    from core.common.atomic_json import read_json
    try:
        data = read_json(_RECORD_PATH)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        logger.warning("Could not read %s; handoff disabled this session.", _RECORD_PATH, exc_info=True)
        return {}


def record_job(stage: str, fields: Dict[str, Any]) -> bool:
    """Record what `stage` just finished producing.

    The stored entry is the previous stage's entry updated with `fields`, so
    upstream paths carry forward automatically. Blank values are dropped rather
    than stored, so a half-filled form never overwrites a good path from an
    earlier run with an empty string.

    Never raises: a handoff is a convenience, and failing to write one must not
    take down a render that just succeeded.
    """
    # The write is atomic, the read-merge-write wasn't: Scene Cut and the Depth
    # Map Editor record from worker threads beside another stage, and two at
    # once could each drop the other's entry (bug sweep 2026-10-09, A2).
    with _RECORD_LOCK:
        return _record_job(stage, fields)


_RECORD_LOCK = threading.Lock()


def _record_job(stage: str, fields: Dict[str, Any]) -> bool:
    if stage not in STAGE_ORDER and stage not in SIDE_STAGES:
        return False
    from core.common.atomic_json import read_json, write_json_atomic
    try:
        # Read strictly (audit 2026-09-28, minor): taking an unreadable file as
        # {} and writing that back wiped every other stage's handoff.
        try:
            all_records = read_json(_RECORD_PATH)
            if not isinstance(all_records, dict):
                raise ValueError("not a JSON object")
        except FileNotFoundError:
            all_records = {}
        except PermissionError:
            logger.warning("%s is locked by another program; the %s handoff was not recorded "
                           "(the file is left as it is).", _RECORD_PATH, stage)
            return False
        except ValueError:
            aside = _RECORD_PATH + ".corrupt"
            os.replace(_RECORD_PATH, aside)
            logger.warning("%s could not be read; kept as %s and started afresh.", _RECORD_PATH, aside)
            all_records = {}
        inherited = dict(_inherit(all_records, SIDE_STAGES.get(stage, stage)))
        inherited.update({k: v for k, v in fields.items() if v not in (None, "")})
        _rebase(inherited, fields)
        inherited["_stage"] = stage
        inherited["_recorded_at"] = time.time()
        all_records[stage] = inherited

        write_json_atomic(_RECORD_PATH, all_records, indent=2)
        logger.info("Recorded %s job for handoff.", stage)
        return True
    except Exception:
        logger.exception("Could not record %s job handoff", stage)
        return False


#: A record's folder keys and where each lives in a project chapter: (stage
#: role, sub-folder).
_KEY_ROLES = {"source": ("source", ""), "depth": ("depth", ""), "splat_out": ("splats", ""),
              "hires": ("splats", "hires"), "lowres": ("splats", "lowres"),
              "inpainted_out": ("inpainted", "")}


def _rebase(record: Dict[str, Any], fields: Dict[str, Any]) -> Dict[str, Any]:
    """The folders a record carries over from an earlier stage, kept only when
    they are this job's chapter (user, 2026-10-02: Inpainting ran on one film's
    chapter 6 while the last Splatting job was another project; the record kept that one's
    source, and Merging's Import filled Original from it). When the job's own
    folders lie in a project chapter, an inherited folder from another chapter
    or project is replaced with this chapter's own (if it exists), else
    dropped -- an Import then leaves that field alone rather than filling in
    the wrong movie. Outside a project, nothing changes."""
    try:
        from core.ui import chapters
    except Exception:
        return record
    here = None
    for key in _KEY_ROLES:
        value = fields.get(key)
        if isinstance(value, str) and value:
            here = chapters.locate_chapter(value)
            if here:
                break
    if not here:
        return record
    unit, num, name = here
    dirs = None
    for key, (role, sub) in _KEY_ROLES.items():
        if key in fields or not record.get(key):
            continue
        at = chapters.locate_chapter(str(record[key]))
        if at and os.path.normcase(at[0]) == os.path.normcase(unit) and at[1] == num:
            continue
        if dirs is None:
            dirs = chapters.chapter_dirs(unit, num, name)
        folder = dirs.get(role)
        if folder and sub:
            folder = folder.rstrip("/") + "/" + sub
        if folder and os.path.isdir(folder):
            record[key] = folder.replace("\\", "/")
        else:
            record.pop(key, None)
    return record


def _upstream(all_records: Dict[str, Any], stage: str, strict: bool = False) -> Optional[Dict[str, Any]]:
    """The record `stage` should build on, or None.

    Walks back from the stage before it, past optional stages, to the first
    required one, and takes the NEWEST record found on the way. So Splatting
    gets the Depth Map Editor's map when that ran after DepthCrafter, and
    DepthCrafter's when a re-run of it has made an older edit stale. A record
    written before timestamps existed counts as oldest.

    `strict` stops at the first required stage even when it has no record --
    what an import button wants, so Splatting never offers Scene Cut's clips
    as if DepthCrafter had run. Inheritance is not strict, and keeps walking
    back to carry whatever upstream paths exist.
    """
    try:
        idx = STAGE_ORDER.index(stage)
    except ValueError:
        return None
    candidates = []
    for prev in reversed(STAGE_ORDER[:idx]):
        entry = all_records.get(prev)
        if isinstance(entry, dict):
            candidates.append(entry)
        if prev not in OPTIONAL_STAGES and (strict or candidates):
            break
    if not candidates:
        return None
    return max(candidates, key=lambda e: float(e.get("_recorded_at", 0) or 0))


def _inherit(all_records: Dict[str, Any], stage: str) -> Dict[str, Any]:
    """The upstream record `stage` builds on, or {} if there is none."""
    return _upstream(all_records, stage) or {}


def get_record(stage: str) -> Optional[Dict[str, Any]]:
    """The record `stage` left behind, or None if it has not run."""
    entry = _read_all().get(stage)
    return entry if isinstance(entry, dict) else None


def get_source_record(for_stage: str) -> Optional[Dict[str, Any]]:
    """The record the stage BEFORE `for_stage` left behind.

    This is what an import button reads. Returns None when the previous stage
    has not run, which is the signal to keep the button hidden rather than
    offering an import that would fill nothing.
    """
    return _upstream(_read_all(), for_stage, strict=True)


def in_project(record: Optional[Dict[str, Any]], project: str, keys) -> bool:
    """Whether `record`'s folders (`keys`, any one of them) lie inside
    `project` (user, 2026-10-02: with a project picked in the title bar, an
    Import offers only that project's jobs; Manual paths stay free). No
    project picked: every record counts."""
    if not record:
        return False
    if not project:
        return True
    root = os.path.normcase(os.path.normpath(str(project)))
    for key in keys:
        value = record.get(key)
        if not value:
            continue
        path = os.path.normcase(os.path.normpath(str(value)))
        if path == root or path.startswith(root + os.sep):
            return True
    return False


def resolve_subfolder(path: str, *parts: str) -> str:
    """Append `parts` to `path`, but only if the result actually exists.

    The pipeline moves files into "finished" subfolders as each stage consumes
    them, so the folder a later stage should read from is usually one or two
    levels deeper than the folder the earlier stage was pointed at:

        Splatting reads  <source>/finished          (DepthCrafter moved them)
        Merging reads    <source>/finished/finished (Splatting moved them again)
        Merging reads    <hires>/finished           (Inpainting moved them)

    Falling back to the base path when the subfolder is missing keeps this
    honest in the cases where the convention does not hold -- a single-file
    job, a stage run with file-moving turned off, or a folder the user picked
    by hand. Pointing at a directory that does not exist would be worse than
    pointing one level up, because the field would look filled but find
    nothing.
    """
    if not path:
        return path
    try:
        if not os.path.isdir(path):
            # A single file, or a folder that isn't there -- nothing to descend
            # into, so hand back exactly what was recorded.
            return path
        candidate = os.path.join(path, *parts)
        return candidate if os.path.isdir(candidate) else path
    except Exception:
        return path


_LABELS = {
    "scenecut": "Scene Cut",
    "depthcrafter": "DepthCrafter",
    "depthedit": "Depth Map Editor",
    "splatting": "Splatting",
    "inpainting": "Inpainting",
    "inpainting_v2": "Inpainting V2",
    "merging": "Merging",
}


def previous_stage_label(for_stage: str) -> str:
    """Display name of the stage an import button would pull from right now."""
    record = get_source_record(for_stage)
    return _LABELS.get(str(record.get("_stage", "")), "") if record else ""


def describe(record: Dict[str, Any]) -> str:
    """Short human summary of a record, for a tooltip."""
    if not record:
        return ""
    lines = []
    for key, label in (
        ("source", "Source clips"),
        ("depth", "Depth maps"),
        ("splat_out", "Splatted output"),
        ("hires", "Hi-res splats"),
        ("lowres", "Lo-res splats"),
        ("inpainted_out", "Inpainted output"),
    ):
        value = record.get(key)
        if value:
            lines.append(f"{label}: {value}")
    return "\n".join(lines)
