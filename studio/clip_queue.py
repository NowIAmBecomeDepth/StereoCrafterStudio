"""Upcoming Jobs' clip list (JobQueue.qml), straight from the stage's input
folders -- not from Load Preview (user, 2026-09-27): the list is there as
soon as the folders are right, and it stays through a job (the preview lets
go of its clips for the job; this list does not), so the rows' bars fill as
the batch works through them.

The scans mirror the preview engines' pairing (their
``_find_preview_sources_callback``), without importing those engines (torch
and the rest) -- a few globs and stats, on a thread of their own.
"""
from __future__ import annotations

import glob
import json
import logging
import os
import re
import threading
from typing import Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

logger = logging.getLogger("studio.clip_queue")

VIDEO_GLOBS = ("*.mp4", "*.avi", "*.mov", "*.mkv")
SIDECAR_EXT = ".fssidecar"

from core.common.clip_names import is_own_file  # noqa: E402


# ---------------------------------------------------------------------------
# Splatting: source clips that have a depth map (v2 preview_engine, 2A / 2B)
# ---------------------------------------------------------------------------

def _fuzzy_depth(folder: str, base_name: str) -> Optional[str]:
    best, best_len = None, 0
    try:
        cands = glob.glob(os.path.join(folder, "*_depth.mp4")) + glob.glob(os.path.join(folder, "*_depth.npz"))
    except Exception:
        return None
    bn = base_name.lower()
    for dp in cands:
        core = re.sub(r"_depth\.(mp4|npz)$", "", os.path.basename(dp), flags=re.IGNORECASE).lower()
        if len(core) >= 5 and (bn.startswith(core) or core.startswith(bn)) and len(core) > best_len:
            best, best_len = dp, len(core)
    return best


def splat_pairs(values: dict) -> List[Tuple[str, str]]:
    """[(source clip, depth map)], in the batch's order."""
    source = str(values.get("input_source_clips") or "")
    depth = str(values.get("input_depth_maps") or "")
    if not source or not depth:
        return []
    if os.path.isfile(source):
        if os.path.isfile(depth):
            return [(source, depth)]
        if not os.path.isdir(depth):
            return []
        base = os.path.splitext(os.path.basename(source))[0]
        for dp in (os.path.join(depth, f"{base}_depth.mp4"), os.path.join(depth, f"{base}_depth.npz")):
            if os.path.exists(dp):
                return [(source, dp)]
        fz = _fuzzy_depth(depth, base)
        return [(source, fz)] if fz else []
    if not os.path.isdir(source) or not os.path.isdir(depth):
        return []
    from core.common.submask_files import is_subtitle_mask      # Detect Text's masks are not clips
    videos = sorted(p for ext in VIDEO_GLOBS for p in glob.glob(os.path.join(source, ext))
                    if not is_subtitle_mask(p))
    out: List[Tuple[str, str]] = []
    if values.get("multi_map"):
        try:
            folders = [os.path.join(depth, e) for e in os.listdir(depth)
                       if os.path.isdir(os.path.join(depth, e)) and e.lower() not in ("sidecars", "edit_history")]
        except OSError:
            return []
        for v in videos:
            base = os.path.splitext(os.path.basename(v))[0]
            hit = None
            for d in folders:
                for dp in (os.path.join(d, f"{base}_depth.mp4"), os.path.join(d, f"{base}_depth.npz")):
                    if os.path.exists(dp):
                        hit = dp
                        break
                if hit:
                    break
            if not hit:
                for d in folders:
                    hit = _fuzzy_depth(d, base)
                    if hit:
                        break
            if hit:
                out.append((v, hit))
        return out
    for v in videos:
        base = os.path.splitext(os.path.basename(v))[0]
        hit = next((dp for dp in (os.path.join(depth, f"{base}_depth.mp4"), os.path.join(depth, f"{base}_depth.npz"),
                                  os.path.join(depth, f"{base}.mp4"), os.path.join(depth, f"{base}.npz"))
                    if os.path.exists(dp)), None) or _fuzzy_depth(depth, base)
        if hit:
            out.append((v, hit))
    return out


def _sidecar_folder(values: dict) -> str:
    """v2 preview_engine _get_sidecar_base_folder, without creating anything."""
    base = str(values.get("input_depth_maps") or "")
    if base and os.path.isfile(base):
        base = os.path.dirname(base)
    return os.path.join(base, "sidecars") if values.get("multi_map") else base


def _edited_flags(depths: List[str]) -> List[bool]:
    """Per depth map: Splatting reads an edit from the Depth Map Editor (user,
    2026-09-30: a badge). One listing of each edit_history folder; the edit's
    record is read only for a map with a kept original."""
    from core.depthedit.project import HISTORY_DIR, depth_edited
    kept: Dict[str, set] = {}
    out = []
    for depth in depths:
        folder = os.path.dirname(depth)
        if folder not in kept:
            names = set()
            for sub in (HISTORY_DIR, os.path.join("edited", "originals")):
                try:
                    with os.scandir(os.path.join(folder, sub)) as it:
                        names.update(os.path.normcase(e.name) for e in it)
                except OSError:
                    pass
            kept[folder] = names
        stem, ext = os.path.splitext(os.path.basename(depth))
        maybe = {os.path.normcase(f"{stem}.original{ext}"), os.path.normcase(stem + ext)} & kept[folder]
        try:
            out.append(bool(maybe) and depth_edited(depth))
        except OSError:
            out.append(False)
    return out


def _erased_flags(clips: List[str]) -> List[bool]:
    """Per clip: Erase from Clip took an object out of it and it is still that erase (core.removal.erase's
    record beside it in edit_history, live -- user, 2026-10-16: a badge). One listing of each edit_history
    folder; the record is read only for a clip that has one."""
    from core.removal import erase as E
    from core.removal import putback as P
    names: Dict[str, set] = {}
    out = []
    for clip in clips:
        rec = os.path.normcase(os.path.basename(clip) + ".erase.json")
        hit = False
        # beside it, or where it was before a stage moved it into finished/ (E.history_candidates)
        for hist in E.history_candidates(os.path.dirname(clip)):
            if hist not in names:
                names[hist] = {os.path.normcase(n) for n in _listing(hist) if n.lower().endswith(".erase.json")}
            if rec in names[hist]:
                hit = True
                break
        try:
            out.append(hit and P.erased(clip) is not None)
        except Exception:
            out.append(False)
    return out


def _text_mask_flags(paths: List[str]) -> List[bool]:
    """Per source clip: Detect Text saved a mask for it -- the
    <clip>_submask.mp4 beside it that a render picks up (user, 2026-10-03: a
    badge). One listing of each clip folder, not a stat per clip."""
    names: Dict[str, set] = {}
    out = []
    for path in paths:
        folder = os.path.dirname(path)
        if folder not in names:
            try:
                with os.scandir(folder or ".") as it:
                    names[folder] = {os.path.normcase(e.name) for e in it if e.name.lower().endswith("_submask.mp4")}
            except OSError:
                names[folder] = set()
        stem = os.path.splitext(os.path.basename(path))[0]
        out.append(os.path.normcase(stem + "_submask.mp4") in names[folder])
    return out


def splat_fx(values: dict, depths: List[str], cache: Dict, paths: Optional[List[str]] = None) -> List[List[bool]]:
    """[keyframes, frame break, pop boost, sidecar saved, depth map edited,
    text mask, particles, object removed] per clip. The first four from its SAVED sidecar (what a
    render uses); a sidecar is read again only when its file changes. The
    fourth is whether the clip has a sidecar at all (user, 2026-09-28: a badge
    on the row); the fifth whether its depth map is an edit from the Depth Map
    Editor (2026-09-30); the sixth, given the source clips, whether Detect Text
    saved a mask for it (2026-10-03) -- False without them; the seventh whether its
    sidecar has Particles on (2026-10-08); the eighth an object erased from it (2026-10-16); the ninth
    whether its depth map came from Video Depth Anything (2026-10-25)."""
    folder = _sidecar_folder(values)
    # One listing of the folder, not two stats per clip (Studio audit
    # 2026-09-28, PERF: 14 ms for 300 clips, on the UI thread, after most
    # preview jobs). On Windows scandir brings each file's time along.
    times: Dict[str, float] = {}
    try:
        with os.scandir(folder or ".") as it:
            for e in it:
                if e.name.lower().endswith(SIDECAR_EXT):
                    try:
                        if e.is_file():
                            times[os.path.normcase(e.name)] = e.stat().st_mtime
                    except OSError:
                        pass
    except OSError:
        pass
    out, particles = [], []
    # Rain: only a sidecar that SAVED it on, like the other effects (user, 2026-10-10: turning Rain on
    # in the page lit the badge on every clip, and off took it off every clip). Was the sidecar's,
    # else the page's (bug sweep 2026-10-09, A2) -- the batch still falls back to the page's Rain
    # for a clip whose sidecar never saved it (_saved_or).
    for depth in depths:
        flags = [False, False, False, False, None]
        try:
            name = os.path.splitext(os.path.basename(depth))[0] + SIDECAR_EXT
            path = os.path.join(folder, name)
            mtime = times.get(os.path.normcase(name))
            if mtime is not None:
                key = (path, mtime)
                flags = cache.get(key)
                if flags is None:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f) or {}
                    num = lambda k: float(data.get(k) or 0.0)  # noqa: E731
                    rain = data.get("rain_mode")
                    flags = [bool(data.get("keyframe_track")), num("frame_break_bars") > 0, num("boost_amount") > 0,
                             True, None if rain is None else str(rain).lower()]
                    cache[key] = flags
        except Exception:
            flags = [False, False, False, False, None]
        out.append(list(flags[:4]))
        particles.append(len(flags) > 4 and flags[4] in ("moving", "static"))
    for row, edited in zip(out, _edited_flags(depths)):
        row.append(edited)
    texts = _text_mask_flags(paths) if paths is not None else [False] * len(out)
    erased = _erased_flags(paths) if paths is not None else [False] * len(out)
    for row, text, part, gone in zip(out, texts, particles, erased):
        row.append(text)
        row.append(part)
        row.append(gone)            # the eighth: an object erased from it (2026-10-16)
    for row, vda in zip(out, _vda_flags(depths, cache)):
        row.append(vda)             # the ninth: a Video Depth Anything map (user, 2026-10-25)
    return out


def _vda_flags(depths: List[str], cache: Dict) -> List[bool]:
    """Per depth map: Video Depth Anything made it (its tag, core.vda.output.tagged) -- read again only when
    the file changes (size, time)."""
    from core.vda.output import tagged
    out = []
    for depth in depths:
        try:
            st = os.stat(depth)
        except OSError:
            out.append(False)
            continue
        key = ("vda", depth, st.st_mtime, st.st_size)
        hit = cache.get(key)
        if hit is None:
            hit = cache[key] = tagged(depth)
        out.append(hit)
    return out


def splat_finished_moves(values: dict, rows: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """Move Selected to Finished (user, 2026-09-30): what a batch moves for a
    clip it finished (core.splatting.batch_processing), as (file, folder) --
    the clip, its Detect Text mask, its depth map and its sidecars; each goes
    into <folder>/finished."""
    ext = SIDECAR_EXT
    side_folder = _sidecar_folder(values)
    out = []
    for video, depth in rows:
        name = os.path.splitext(os.path.basename(video))[0]
        src_dir, depth_dir = os.path.dirname(video), os.path.dirname(depth) if depth else ""
        out.append((video, src_dir))
        mask = os.path.join(src_dir, name + "_submask.mp4")       # its Detect Text mask goes with it
        if os.path.isfile(mask):
            out.append((mask, src_dir))
        if depth:
            out.append((depth, depth_dir))
        for sc in (os.path.join(side_folder, f"{name}_depth{ext}"), os.path.join(src_dir, f"{name}{ext}"),
                   os.path.join(depth_dir, f"{name}_depth{ext}") if depth_dir else ""):
            if sc and os.path.isfile(sc) and (sc, src_dir) not in out and (sc, depth_dir) not in out:
                out.append((sc, src_dir if os.path.dirname(sc) == src_dir else depth_dir))
    return out


def merge_finished_moves(values: dict, paths: List[str]) -> List[Tuple[str, str]]:
    """Merging's side (user, 2026-09-30): the inpainted clip and its sidecars,
    its splat grid(s) in the mask folder, and its original with sidecars --
    what the batch moves for a merged clip, found as the batch finds them."""
    from core.common.sidecar_manager import find_video_by_core_name

    def folder(key):
        f = str(values.get(key) or "")
        return os.path.dirname(f) if f and os.path.isfile(f) else f
    mask, orig = folder("mask_folder"), folder("original_folder")
    out = []
    for p in paths:
        core = merge_core_name(p)
        home = os.path.dirname(p)
        out.append((p, home))
        for ext in (".fssidecar", ".json"):
            sc = os.path.splitext(p)[0] + ext
            if os.path.isfile(sc):
                out.append((sc, home))
        if not core:
            continue
        if mask and os.path.isdir(mask):
            pat = glob.escape(core) + "_*"
            for g in sorted(glob.glob(os.path.join(mask, pat))):
                low = os.path.basename(g).lower()
                # the clip's own, not a longer clip's ("shot" took "shot_02_..."; sweep 2026-10-14c)
                if (os.path.isfile(g) and is_own_file(core, os.path.basename(g))
                        and ("_splatted" in low or low.endswith("_splat.mov") or "_mask." in low)):
                    out.append((g, mask))
        if orig and os.path.isdir(orig):
            o = find_video_by_core_name(orig, core)
            if o:
                out.append((o, orig))
                for ext in (".fssidecar", ".json"):
                    sc = os.path.splitext(o)[0] + ext
                    if os.path.isfile(sc):
                        out.append((sc, orig))
    return out


def merge_related(values: dict, path: str) -> List[Tuple[str, str]]:
    """Merging's "Play" items beside the inpainted clip: its original, and its
    splat (the hi-res grid the merge reads), found as the merge finds them."""
    from core.common.sidecar_manager import find_video_by_core_name

    def folder(key):
        f = str(values.get(key) or "")
        return os.path.dirname(f) if f and os.path.isfile(f) else f
    core = merge_core_name(path)
    if not core:
        return []
    out = []
    orig = folder("original_folder")
    if orig and os.path.isdir(orig):
        o = find_video_by_core_name(orig, core)
        if o:
            out.append(("Play Original", o))
    mask = folder("mask_folder")
    if mask and os.path.isdir(mask):
        grids = sorted(g for g in glob.glob(os.path.join(mask, glob.escape(core) + "_*_splatted*"))
                       if os.path.isfile(g) and is_own_file(core, os.path.basename(g)))
        if grids:
            out.append(("Play Splat", grids[0]))
    return out


# ---------------------------------------------------------------------------
# Merging: inpainted clips that have their splat (v2 preview_engine scan)
# ---------------------------------------------------------------------------

_INPAINTED = re.compile(r"_inpainted_(right_eye|(dual_)?sbs)F?\.(mp4|mov|mkv)$")


def merge_core_name(path: str) -> str:
    base = os.path.basename(path)
    m = _INPAINTED.search(base)
    if not m:
        return ""
    core = base[: -len(m.group(0))]
    i = core.rfind("_")
    return core[:i] if i > 0 else ""


def _listing(folder: str, cache: Optional[Dict[str, List[str]]] = None) -> List[str]:
    """The file names in `folder`, read once (one scandir), or [] when it can't be."""
    key = os.path.normcase(os.path.abspath(folder or "."))
    if cache is not None and key in cache:
        return cache[key]
    names: List[str] = []
    try:
        with os.scandir(folder or ".") as it:
            for e in it:
                try:
                    if e.is_file():
                        names.append(e.name)
                except OSError:
                    pass
    except OSError:
        pass
    if cache is not None:
        cache[key] = names
    return names


def merge_clips(values: dict) -> List[str]:
    inpainted = str(values.get("inpainted_folder") or "")
    if os.path.isfile(inpainted):
        videos = [inpainted]
    elif os.path.isdir(inpainted):
        videos = sorted(p for ext in ("*.mp4", "*.mov", "*.mkv") for p in glob.glob(os.path.join(inpainted, ext)))
    else:
        return []
    mask = str(values.get("mask_folder") or "")
    if mask and os.path.isfile(mask):
        mask = os.path.dirname(mask)
    # the mask folder listed once, not globbed once per video (bug sweep A2); fnmatch is
    # glob's own matcher, case-blind on Windows as glob is
    import fnmatch
    mask_names = _listing(mask) if mask else []
    out = []
    for v in videos:
        core = merge_core_name(v)
        if not core:
            continue
        pat = glob.escape(core) + "_*_splatted[24]*"
        if mask and not any(fnmatch.fnmatch(n, pat) and is_own_file(core, n) for n in mask_names):
            continue                      # the preview and the batch both pass these by
        out.append(v)
    return out


def merge_fx(values: dict, paths: List[str]) -> List[List[bool]]:
    """Merging's badges (user, 2026-09-28): the same four flags as Splatting's,
    only the last used -- whether the clip has a sidecar (Grain Match's Save
    writes one), found as the merge finds it (core.common.sidecar_manager.
    read_clip_sidecar): <core>.fssidecar / .json in the inpainted folder, then
    the original clips' folder, then beside the clip."""
    folders = []
    is_file = {}                              # each setting asked once (the erased-original flag below too)
    for key in ("inpainted_folder", "original_folder"):
        f = str(values.get(key) or "")
        if f:
            is_file[key] = os.path.isfile(f)
            folders.append(os.path.dirname(f) if is_file[key] else f)
    # each folder listed once, not up to six isfile calls a clip on the UI thread
    # (bug sweep A2; splat_fx did the same in the 2026-09-28 audit)
    cache: Dict[str, List[str]] = {}

    def has(folder: str, name: str) -> bool:
        key = "\0set\0" + os.path.normcase(os.path.abspath(folder or "."))
        if key not in cache:
            cache[key] = {os.path.normcase(n) for n in _listing(folder)}
        return os.path.normcase(name) in cache[key]
    out = []
    for p in paths:
        core = merge_core_name(p)
        found = False
        try:
            exts = (".fssidecar", ".json")
            found = bool(core) and any(has(f, core + ext) for f in folders for ext in exts)
            stem = os.path.splitext(os.path.basename(p))[0]
            found = found or any(has(os.path.dirname(p), stem + ext) for ext in exts)
        except Exception:
            found = False
        out.append([False, False, False, found])
    # the eighth flag (Splatting's order): its original clip had an object erased -- Merging's Put Back
    # (2026-10-16), the erased clip found by its core name in the Original folder, as the merge finds it
    orig = str(values.get("original_folder") or "")
    # the setting and its edit_history folders looked at once, not once a clip on the UI thread
    orig_is_file = bool(orig) and is_file.get("original_folder", False)
    hists = None
    clips = []
    for p in paths:
        core = merge_core_name(p)
        clip = ""
        try:
            if orig_is_file:
                clip = orig if os.path.splitext(os.path.basename(orig))[0] == core else ""
            elif orig and core:
                if hists is None:
                    from core.removal import erase as _E
                    hists = list(_E.history_candidates(orig))   # also where it was before a move to finished/
                for hist in hists:
                    key = "\0erase\0" + os.path.normcase(os.path.abspath(hist))
                    if key not in cache:
                        cache[key] = [n[:-len(".erase.json")] for n in _listing(hist) if n.lower().endswith(".erase.json")]
                    clip = next((os.path.join(orig, n) for n in cache[key] if os.path.splitext(n)[0] == core), "")
                    if clip:
                        break
        except Exception:
            clip = ""
        clips.append(clip)
    gone = _erased_flags([c for c in clips if c])
    it = iter(gone)
    for row, clip in zip(out, clips):
        row.extend([False, False, False, next(it) if clip else False])
    return out


# ---------------------------------------------------------------------------
# DepthCrafter and Inpainting (user, 2026-10-10): their own lists
# ---------------------------------------------------------------------------

def dc_clips(values: dict) -> List[str]:
    """DepthCrafter: what a Start would process (its runner's own discovery) --
    the clips and image-sequence folders in the input folder, or the one input."""
    from studio.stages.depthcrafter.runner import InputError, discover_sources
    try:
        specs, _single = discover_sources(str(values.get("input_dir_or_file_var") or ""))
    except InputError:
        return []
    return sorted((s["path"] for s in specs), key=lambda p: os.path.basename(p).lower())


def inpaint_clips(values: dict) -> List[str]:
    """Inpainting V1: the Lo-Res splats a Start would process (the engine's own
    glob), or the one clip picked as the input."""
    folder = str(values.get("input_folder") or "")
    if os.path.isfile(folder):
        return [folder]
    if not os.path.isdir(folder):
        return []
    # .mkv too: Splatting's Container may be MKV (user, 2026-10-25), as the engine's own glob
    return sorted(glob.glob(os.path.join(folder, "*.mp4")) + glob.glob(os.path.join(folder, "*.mov"))
                  + glob.glob(os.path.join(folder, "*.mkv")))


#: Inpainting V2's input types (its engine's VIDEO_EXTENSIONS).
INPAINT_V2_EXTS = (".mp4", ".mkv", ".mov", ".avi", ".webm")


def inpaint_v2_clips(values: dict) -> List[str]:
    """Inpainting V2 (user, 2026-10-11: Upcoming Jobs as V1 has): the videos its Start
    would process -- the engine's own _gather_videos -- or the one clip picked."""
    folder = str(values.get("input_folder") or "")
    if os.path.isfile(folder):
        return [folder]
    if not os.path.isdir(folder):
        return []
    return [os.path.join(folder, n) for n in sorted(os.listdir(folder)) if n.lower().endswith(INPAINT_V2_EXTS)]


def dc_finished_moves(paths: List[str]) -> List[Tuple[str, str]]:
    """DepthCrafter's side of Move to Finished: the source (a clip, or an image
    sequence's folder) into finished/ beside it, as the runner moves it."""
    return [(p, os.path.dirname(p)) for p in paths]


_SPLAT_SUFFIXES = ("_splatted2DF", "_splatted2D", "_splatted2F", "_splatted2", "_splatted4F", "_splatted4")


def inpaint_hires_of(low_res: str, hires_folder: str) -> str:
    """The Hi-Res splat Inpainting would blend with this Lo-Res one: the
    engine's own name match (_find_high_res_match), without reading the videos."""
    if not hires_folder or not os.path.isdir(hires_folder):
        return ""
    if os.path.normcase(os.path.normpath(hires_folder)) == os.path.normcase(os.path.normpath(os.path.dirname(low_res))):
        return ""
    stem = os.path.splitext(os.path.basename(low_res))[0]
    core = next((s for s in _SPLAT_SUFFIXES if stem.endswith(s)), None)
    if core is None:
        return ""
    with_dim = stem[: stem.rfind(core)]
    base = with_dim[: with_dim.rfind("_")] if "_" in with_dim else with_dim
    if not base:
        return ""
    # <base>_<width><core>: the clip's own hi-res, not a longer clip's (sweep 2026-10-14c)
    own = re.compile(re.escape(base) + r"_\d+" + re.escape(core) + r"\.(?:mp4|mkv|mov)$", re.IGNORECASE)
    hits = [m for ext in (".mp4", ".mov", ".mkv")
            for m in glob.glob(os.path.join(hires_folder, f"{glob.escape(base)}_*{core}{ext}"))
            if os.path.normcase(m) != os.path.normcase(low_res) and own.match(os.path.basename(m))]
    return hits[0] if hits else ""


def inpaint_finished_moves(values: dict, paths: List[str]) -> List[Tuple[str, str]]:
    """Inpainting's side: what its batch moves for a finished clip -- the Lo-Res
    splat and its .spsidecar into the input's finished/, its Hi-Res splat into
    the Hi-Res folder's."""
    hires = str(values.get("hires_blend_folder") or "")
    if hires and os.path.isfile(hires):
        hires = os.path.dirname(hires)
    out = []
    for p in paths:
        home = os.path.dirname(p)
        out.append((p, home))
        sc = os.path.splitext(p)[0] + ".spsidecar"
        if os.path.isfile(sc):
            out.append((sc, home))
        hi = inpaint_hires_of(p, hires)
        if hi:
            out.append((hi, hires))
    return out


def frame_count(path: str, cache: Dict) -> int:
    """A clip's frame count from its header (cv2), cached by path and mtime:
    a rescan only opens new or changed files. 0 when unreadable."""
    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        return 0
    if key not in cache:
        n = 0
        try:
            import cv2
            cap = cv2.VideoCapture(path)
            n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
            cap.release()
        except Exception:
            n = 0
        cache[key] = max(0, n)
    return cache[key]


def splat_panels(path: str) -> Tuple[int, int]:
    """(across, down): how many panels a splat's frame holds, by its name -- a _splatted2 is two
    side by side, a _splatted4 / _splatted2D a 2 x 2 grid; (1, 1) for anything else."""
    stem = os.path.splitext(os.path.basename(path))[0].lower()
    if stem.endswith(("_splatted2", "_splatted2f")):
        return 2, 1
    if stem.endswith(("_splatted4", "_splatted4f", "_splatted2d", "_splatted2df")):
        return 2, 2
    return 1, 1


def clip_ratio(path: str, cache: Dict) -> str:
    """A clip's aspect-ratio class ("1.85:1") from its header (cv2: width x
    pixel aspect / height), cached by path and mtime; "" when unreadable
    (user, 2026-10-11: the shape beside each clip in the list). A splat's is one
    panel's -- the video's own shape, not the panels' together (user, 2026-10-14)."""
    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        return ""
    if key not in cache:
        label = ""
        try:
            import cv2
            from studio.resolution_presets import ratio_class
            cap = cv2.VideoCapture(path)
            w, h = cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
            num = cap.get(getattr(cv2, "CAP_PROP_SAR_NUM", 40)) or 0
            den = cap.get(getattr(cv2, "CAP_PROP_SAR_DEN", 41)) or 0
            cap.release()
            sar = num / den if num > 0 and den > 0 else 1.0
            across, down = splat_panels(path)
            w, h = w / across, h / down
            label = (ratio_class(w * sar / h) or "") if w > 0 and h > 0 else ""
        except Exception:
            label = ""
        cache[key] = label
    return cache[key]


# ---------------------------------------------------------------------------

def move_pending(queue, toast=None) -> bool:
    """True (and said) while Upcoming Jobs' Move to Finished is still moving clips: a Start then
    would read the clips it's taking (bug sweep 2026-10-14c)."""
    if queue is not None and getattr(queue, "_moving", False):
        if toast is not None:
            toast.emit("info", "Moving clips", "Move to Finished is still moving clips; start again in a moment.")
        return True
    return False


class ClipQueue(QObject):
    """The list, rescanned (on its own thread) when a folder setting changes
    or Load Preview is pressed -- never while a job runs, so the job's clips
    stay listed with their bars even as the batch moves them to finished."""

    changed = Signal()
    #: the ticks (user, 2026-09-30): their own signal, so ticking a row does
    #: not hand the ListView a new model (every row rebuilt)
    checkedChanged = Signal()
    #: Move Selected to Finished is done: (moved clips, clips left behind)
    movedToFinished = Signal(int, "QVariantList")
    _sig_done = Signal(object)
    _sig_frames = Signal(object)
    _sig_moved = Signal(object)

    def __init__(self, kind: str, values_fn: Callable[[], dict], parent=None):
        super().__init__(parent)
        self._kind = kind                      # "splatting" | "merging" | "depthcrafter" | "inpainting" | "inpainting_v2"
        self._values_fn = values_fn
        self._paths: List[str] = []
        self._depths: List[str] = []
        self._fx: List[List[bool]] = []
        self._fx_cache: Dict = {}
        self._frames: List[int] = []
        self._frames_cache: Dict = {}
        self._ratios: List[str] = []
        self._ratio_cache: Dict = {}
        self._hold = False
        self._stale = False     # a rescan was asked for while held: do it on release (#28)
        self._fx_stale = False  # ...or only the badges (a sidecar saved mid-job)
        self._running = False
        self._again = False
        self._timer = QTimer(self, singleShot=True, interval=250, timeout=self._start)
        self._checked: set = set()           # normcased paths of the ticked clips
        self._moving = False
        self._sig_done.connect(self._on_done)
        self._sig_frames.connect(self._on_frames)
        self._sig_moved.connect(self._on_moved)

    clips = Property("QVariantList", lambda self: [os.path.basename(p) for p in self._paths], notify=changed)
    clipPaths = Property("QVariantList", lambda self: list(self._paths), notify=changed)
    clipFx = Property("QVariantList", lambda self: [list(f) for f in self._fx], notify=changed)
    #: each clip's frame count (user, 2026-09-27); 0 until counted / unreadable
    clipFrames = Property("QVariantList", lambda self: list(self._frames), notify=changed)
    #: each clip's aspect-ratio class, "1.85:1" (user, 2026-10-11); "" until read / unreadable
    clipRatios = Property("QVariantList", lambda self: list(self._ratios), notify=changed)

    # ---- the ticks: Process Selected, Move Selected to Finished (user, 2026-09-30)

    @staticmethod
    def _key(path: str) -> str:
        return os.path.normcase(os.path.abspath(path))

    def _get_checked(self) -> list:
        return [self._key(p) in self._checked for p in self._paths]

    checked = Property("QVariantList", _get_checked, notify=checkedChanged)
    checkedCount = Property(int, lambda self: sum(self._get_checked()), notify=checkedChanged)
    moving = Property(bool, lambda self: self._moving, notify=checkedChanged)

    def checked_paths(self) -> List[str]:
        """The ticked clips, in the list's order."""
        return [p for p in self._paths if self._key(p) in self._checked]

    def frames_of(self, paths, cap: int = -1) -> List[Tuple[str, int]]:
        """[(path, frames)] for a run's clips: the list's own counts, else the header's; at most
        `cap` each when it is set (Process Length). For the batch's ETA (job_stats.batch_begin)."""
        known = {self._key(p): f for p, f in zip(self._paths, self._frames)}
        out = []
        for p in paths:
            n = known.get(self._key(p)) or frame_count(p, self._frames_cache)
            out.append((p, min(n, cap) if cap and cap > 0 else n))
        return out

    @Slot(str, bool)
    def setChecked(self, path: str, on: bool) -> None:
        key = self._key(path)
        if on and key not in self._checked:
            self._checked.add(key)
        elif not on and key in self._checked:
            self._checked.discard(key)
        else:
            return
        self.checkedChanged.emit()

    @Slot()
    def checkAll(self) -> None:
        self._checked = {self._key(p) for p in self._paths}
        self.checkedChanged.emit()

    @Slot()
    def checkNone(self) -> None:
        self._checked = set()
        self.checkedChanged.emit()

    def _prune_checked(self) -> None:
        """A clip that left the list (moved, renamed) is no longer ticked."""
        listed = {self._key(p) for p in self._paths}
        if self._checked - listed:
            self._checked &= listed
        self.checkedChanged.emit()

    @Slot()
    def moveCheckedToFinished(self) -> None:
        """The ticked clips -- and what a batch would move with each -- into
        their folders' finished/, as if processed; they leave the list at once.
        Never while a job holds the list. The preview lets go of its readers
        first (Windows won't move an open file); the one it showed, if moved,
        is cleared rather than reloaded."""
        self._move_to_finished(lambda p: self._key(p) in self._checked)

    @Slot(str)
    def moveToFinished(self, path: str) -> None:
        """One clip's right-click Move to Finished (user, 2026-10-10): as the
        ticked clips' move, for that clip alone."""
        key = self._key(path)
        self._move_to_finished(lambda p: self._key(p) == key)

    @Slot(str, result=str)
    def depthOf(self, path: str) -> str:
        """Splatting: the depth map listed with this clip ("" if none was found)."""
        key = self._key(path)
        for p, d in zip(self._paths, self._depths or []):
            if self._key(p) == key:
                return d or ""
        return ""

    @Slot(str, "QVariantMap", result="QVariantMap")
    def sendPlan(self, path: str, chapter) -> dict:
        """"Send to <next stage>" (user, 2026-10-11): what the next stage loads for
        this finished clip (studio.send_on.plan); `chapter` is the next stage's
        chapter paths when this one is on a chapter."""
        from studio import send_on
        try:
            kind = "inpainting" if self._kind == "inpainting_v2" else self._kind     # V2 hands on as V1 does
            return send_on.plan(kind, path, dict(self._values_fn()), dict(chapter or {}))
        except Exception as e:
            logger.exception("Send to the next stage: no plan for %s", path)
            return {"error": str(e)}

    def _related(self, path: str) -> list:
        """The files that go with this row's clip at this stage, as [(kind, path)], whether or not
        they are there: what "Play ..." and "Open ... in Explorer" offer."""
        values = dict(self._values_fn())
        if self._kind == "depthcrafter":
            return [("Image Sequence Folder" if os.path.isdir(path) else "Source Clip", path)]
        if self._kind == "splatting":
            return [("Source Clip", path), ("Depth Map", self.depthOf(path))]
        if self._kind == "inpainting":
            hires = str(values.get("hires_blend_folder") or "")
            return [("Lo-Res Splat", path),
                    ("Hi-Res Splat", hires if os.path.isfile(hires) else inpaint_hires_of(path, hires))]
        if self._kind == "inpainting_v2":
            return [("Splat", path)]
        return [("Inpainted Clip", path)] + [(text[len("Play "):] if text.startswith("Play ") else text, p)
                                             for text, p in merge_related(values, path)]

    @Slot(str, result="QVariantList")
    def playables(self, path: str) -> list:
        """A row's "Play ..." items (user, 2026-10-10): the files that go with this
        clip at this stage, as [{text, path}] -- only those that are there."""
        out = []
        for kind, p in self._related(path):
            # a video or a folder only: a .npz depth map went to whatever Windows opens that with
            # (bug sweep 2026-10-14c)
            if p and os.path.exists(p) and (os.path.isdir(p) or os.path.splitext(p)[1].lower() in
                                            (".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v")):
                out.append({"text": "Open " + kind if os.path.isdir(p) else "Play " + kind, "path": p})
        return out

    @Slot(str, result="QVariantList")
    def explorables(self, path: str) -> list:
        """A row's "Open ... in Explorer" items (user, 2026-10-18): each file that goes with the
        clip, selected in Explorer -- any kind of file (a .npz depth map too), only those there."""
        return [{"text": f"Open {kind} in Explorer", "path": p}
                for kind, p in self._related(path) if p and os.path.exists(p)]

    def _move_to_finished(self, wanted) -> None:
        if self._hold or self._moving:
            return
        rows = [(p, d) for p, d in zip(self._paths, self._depths or [""] * len(self._paths)) if wanted(p)]
        if not rows:
            return
        values = dict(self._values_fn())
        listed = [p for p, _ in rows]
        moves = (splat_finished_moves(values, rows) if self._kind == "splatting"
                 else dc_finished_moves(listed) if self._kind in ("depthcrafter", "inpainting_v2")
                 else inpaint_finished_moves(values, listed) if self._kind == "inpainting"
                 else merge_finished_moves(values, listed))
        owner = self.parent()
        current = ""
        try:
            if owner is not None and owner.property("loaded"):
                paths, idx = list(owner.property("clipPaths") or []), int(owner.property("videoIndex") or 0)
                current = paths[idx] if 0 <= idx < len(paths) else ""
        except Exception:
            current = ""
        self._moving = True
        self.checkedChanged.emit()

        def work():
            from core.common.file_organizer import move_files_to_finished
            try:
                move_files_to_finished(moves, logger=logger, wait_before_move=0.3, max_retries=5, retry_delay=0.5)
            except Exception:
                logger.exception("Move Selected to Finished failed")
            self._sig_moved.emit((rows, current))

        def go():
            threading.Thread(target=work, name=f"{self._kind}-move-finished", daemon=True).start()
        release = getattr(owner, "release_for_job", None)
        if release is not None:
            release(go)
        else:
            go()

    def _on_moved(self, out) -> None:
        rows, current = out
        self._moving = False
        left = [os.path.basename(p) for p, _ in rows if os.path.exists(p)]
        moved = len(rows) - len(left)
        owner = self.parent()
        if owner is not None:
            if current and not os.path.exists(current):
                # the clip on screen went to finished: nothing to come back to
                if hasattr(owner, "_resume_after_job"):
                    owner._resume_after_job = False
                if hasattr(owner, "clear"):
                    owner.clear()
            if hasattr(owner, "restore_after_job"):
                owner.restore_after_job()
        logger.info("Move Selected to Finished: %d clip(s) moved%s.", moved,
                    f", {len(left)} left (in use?): {', '.join(left)}" if left else "")
        self.movedToFinished.emit(moved, left)
        self.checkedChanged.emit()
        self._timer.stop()
        self._start()                   # the list again now: the moved clips leave it

    @Slot()
    def rescan(self) -> None:
        if not self._hold:
            self._timer.start()
        else:
            self._stale = True

    @Slot()
    def refreshFx(self) -> None:
        """A sidecar may have been saved: the badges again (the same clips)."""
        if self._hold:
            self._fx_stale = True   # re-read when the job lets go (#28)
            return
        if self._kind == "splatting" and self._depths:
            self._fx = splat_fx(dict(self._values_fn()), self._depths, self._fx_cache, self._paths)
            self.changed.emit()
        elif self._kind == "merging" and self._paths:
            self._fx = merge_fx(dict(self._values_fn()), self._paths)
            self.changed.emit()

    def hold(self, on: bool) -> None:
        """A job is running: keep the list as it is. A rescan asked for
        meanwhile (a folder or chapter changed) happens when it is let go
        (audit 2026-09-28 #28: it used to be lost)."""
        was, self._hold = self._hold, bool(on)
        if was and not self._hold:
            if self._stale:
                self._stale = self._fx_stale = False
                self._timer.start()
            elif self._fx_stale:
                self._fx_stale = False
                self.refreshFx()

    def _start(self) -> None:
        if self._running:
            self._again = True
            return
        self._running = True
        values = dict(self._values_fn())
        threading.Thread(target=self._scan, args=(values,), name=f"{self._kind}-clip-queue", daemon=True).start()

    def _scan(self, values: dict) -> None:
        try:
            if self._kind == "splatting":
                pairs = splat_pairs(values)
                paths, depths = [p for p, _ in pairs], [d for _, d in pairs]
            elif self._kind == "depthcrafter":
                paths, depths = dc_clips(values), []
            elif self._kind == "inpainting":
                paths, depths = inpaint_clips(values), []
            elif self._kind == "inpainting_v2":
                paths, depths = inpaint_v2_clips(values), []
            else:
                paths, depths = merge_clips(values), []
        except Exception:
            logger.debug("Clip list scan failed", exc_info=True)
            paths, depths = [], []
        self._sig_done.emit((paths, depths))
        # then the frame counts: the list shows first, the numbers follow
        frames = [frame_count(p, self._frames_cache) for p in paths]
        ratios = [clip_ratio(p, self._ratio_cache) for p in paths]
        self._sig_frames.emit((paths, frames, ratios))

    def _on_done(self, out) -> None:
        self._running = False
        if self._hold:
            self._stale = True      # what it found is shown after the job (#28)
        else:
            if out[0] != self._paths:
                self._frames = []           # the new clips' counts follow
                self._ratios = []           # ...and their shapes
            self._paths, self._depths = out
            self._prune_checked()
            # the badges read here, not on the scan's thread: a sidecar saved
            # while the scan ran must not be overwritten by what it saw first
            values = dict(self._values_fn())
            self._fx = (splat_fx(values, self._depths, self._fx_cache, self._paths) if self._kind == "splatting"
                        else merge_fx(values, self._paths) if self._kind == "merging"
                        else [])                    # DepthCrafter / Inpainting: no badges
            self.changed.emit()
        if self._again:
            self._again = False
            self._start()

    def _on_frames(self, out) -> None:
        paths, frames = out[0], out[1]
        ratios = out[2] if len(out) > 2 else []
        # the counts of the clips listed now land even while a job holds the
        # list (#28: Splatting's H / L bars divide by them, and never filled)
        if paths == self._paths:
            self._frames = frames
            self._ratios = ratios
            self.changed.emit()
