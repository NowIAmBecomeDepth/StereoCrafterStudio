"""send_on.py -- Upcoming Jobs' "Send to <next stage>" (user, 2026-10-11).

A clip a stage has just finished (its row green) goes on to the next stage on
its own: its output loaded as the next stage's input -- a single file, which
every stage takes -- the other inputs it needs beside it, and the output set to
the chapter's folder for that stage, so one clip can be tried through the
whole pipeline without running the chapter.

    DepthCrafter -> Splatting:  the source clip + its new depth map
    Splatting    -> Inpainting: its Lo-Res splat (+ the Hi-Res folder)
    Inpainting   -> Merging:    its inpainted clip (+ the chapter's original / splat folders)

plan() only works out the paths; the target controller's loadSent() applies them.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from studio import chapter_paths
from studio.clip_queue import _SPLAT_SUFFIXES, merge_core_name

#: Each stage's next one, and its name as the menu says it.
NEXT = {"depthcrafter": "splatting", "splatting": "inpainting", "inpainting": "merging"}
TITLES = {"splatting": "Splatting", "inpainting": "Inpainting", "merging": "Merging"}

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".mxf"}


def _videos(folder: str) -> List[str]:
    try:
        with os.scandir(folder) as it:
            return [e.path for e in it if e.is_file() and os.path.splitext(e.name)[1].lower() in VIDEO_EXTS]
    except OSError:
        return []


def _chain(folder: str) -> List[str]:
    """`folder` and the finished levels under it (a later stage may have moved
    the file down one already)."""
    if not folder or not os.path.isdir(folder):
        return []
    return chapter_paths.finished_chain(folder) or [folder]


def _newest(paths: List[str]) -> str:
    try:
        return max(paths, key=os.path.getmtime) if paths else ""
    except OSError:
        return paths[0] if paths else ""


def made_from(folder: str, stem: str, must: str = "") -> str:
    """The newest video in `folder` (or a finished level under it) named for
    `stem`: its own file -- the name, then "." or an optional "_<width>" and a
    stage's word (clip_names) -- so clip1's search never takes clip10's, nor
    "shot" take "shot_2_depth" (bug sweep 2026-10-14c)."""
    from core.common.clip_names import is_own_file
    hits = []
    for d in _chain(folder):
        for p in _videos(d):
            n = os.path.basename(p)
            if is_own_file(stem, n) and must in n.lower():
                hits.append(p)
    return _newest(hits)


def find_clip(path: str) -> str:
    """The clip where it is now: where it was listed, or moved to a finished
    level below there by the job that just ran."""
    if path and os.path.exists(path):
        return path
    for d in _chain(os.path.dirname(path or "")):
        cand = os.path.join(d, os.path.basename(path))
        if os.path.exists(cand):
            return cand
    return ""


def _folder(value) -> str:
    v = str(value or "")
    return os.path.dirname(v) if v and os.path.isfile(v) else v


def splat_base(path: str) -> str:
    """A splat's clip name without its "_<size>_splatted2" ending (the name
    Inpainting's and Merging's own matching go by)."""
    stem = os.path.splitext(os.path.basename(path))[0]
    core = next((s for s in _SPLAT_SUFFIXES if stem.endswith(s)), None)
    if core is None:
        return ""
    with_dim = stem[: stem.rfind(core)]
    return with_dim[: with_dim.rfind("_")] if "_" in with_dim else with_dim


def plan(kind: str, path: str, values: Dict, chapter: Optional[Dict] = None) -> Dict:
    """What "Send to <next stage>" loads: {"stage", "fields": {key: value},
    "clip", "input"} -- or {"error"}. `values` are the SENDING stage's
    settings, `chapter` the next stage's chapter paths (Shell.stagePaths) when
    the sending stage is on a chapter; its output (and the inputs not loaded
    here) come from it."""
    target = NEXT.get(kind)
    if target is None:
        return {"error": "There is no next stage after this one."}
    chapter = dict(chapter or {})
    name = os.path.basename(path or "")
    fields: Dict[str, str] = {}

    if kind == "depthcrafter":
        src = find_clip(path)
        if not src:
            return {"error": f"{name} isn't where it was listed any more."}
        stem = os.path.basename(src) if os.path.isdir(src) else os.path.splitext(os.path.basename(src))[0]
        depth = made_from(_folder(values.get("output_dir")), stem)
        if not depth:
            return {"error": f"No depth map for {name} in DepthCrafter's output folder."}
        fields = {"input_source_clips": src, "input_depth_maps": depth}
        if chapter.get("output"):
            fields["output_splatted"] = chapter["output"]
        return {"stage": target, "fields": fields, "clip": name, "input": depth}

    if kind == "splatting":
        stem = os.path.splitext(name)[0]
        out = _folder(values.get("output_splatted"))
        if not out:
            return {"error": "Splatting has no output folder set."}
        low = made_from(os.path.join(out, "lowres"), stem, "_splatted")
        hires_dir = os.path.join(out, "hires")
        hi = made_from(hires_dir, stem, "_splatted")
        if not (low or hi):
            low = made_from(out, stem, "_splatted")
        if not (low or hi):
            return {"error": f"No splat of {name} in Splatting's output folder."}
        fields = {"input_folder": low or hi}
        if low and hi:
            fields["hires_blend_folder"] = os.path.dirname(hi)
        elif chapter.get("hires") and low:
            fields["hires_blend_folder"] = chapter["hires"]
        if chapter.get("output"):
            fields["output_folder"] = chapter["output"]
        out_plan = {"stage": target, "fields": fields, "clip": name, "input": fields["input_folder"]}
        if hi and not low:
            # the Hi-Res splat as the Lo-Res input works (slower), but say so (bug sweep 2026-10-14c)
            out_plan["note"] = "Only its Hi-Res splat was found: Inpainting takes that as its input."
        return out_plan

    # inpainting -> merging
    base = splat_base(path)
    out = _folder(values.get("output_folder"))
    if not base or not out:
        return {"error": f"Can't tell which inpainted clip came from {name}."}
    hits = [p for d in _chain(out) for p in _videos(d) if merge_core_name(p) == base]
    inpainted = _newest(hits)
    if not inpainted:
        return {"error": f"No inpainted clip for {name} in Inpainting's output folder."}
    fields = {"inpainted_folder": inpainted}
    for role, key in (("original", "original_folder"), ("mask", "mask_folder"), ("output", "output_folder")):
        if chapter.get(role):
            fields[key] = chapter[role]
    hires = _folder(values.get("hires_blend_folder"))
    if "mask_folder" not in fields and hires and os.path.isdir(hires):
        # no chapter: the splats it was blended with (moved a finished level down by now)
        fields["mask_folder"] = chapter_paths.locate(hires)
    return {"stage": target, "fields": fields, "clip": name, "input": inpainted}
