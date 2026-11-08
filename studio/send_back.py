"""send_back.py -- Upcoming Jobs' "Rerun in <stage before>" (user, 2026-10-31).

A clip a stage has worked on, its input not good enough: the stage before is
set to make it again. Splatting -> DepthCrafter / Video Depth Anything,
Inpainting (V1 or V2) -> Splatting, Merging -> Inpainting / Inpainting V2. One
clip, or every ticked clip at once.

Resume (each stage's move to finished/) moves a clip's inputs one "finished"
level down as the stage finishes it (studio.chapter_paths), so the files the
stage before needs may be anywhere down its input folder's finished chain. So:

* the stage before's inputs for each clip are found wherever they are in its
  chain and moved back up to the level it reads (its input setting), with the
  sidecars and the Detect Text mask that go with them;
* old copies of what is being made again -- this stage's input for the clip,
  archived in finished/ below the level this stage reads -- go into a
  "replaced" folder beside them (user, 2026-10-31), out of every scan: left
  there, the new version would be filed beside them as "name (2)" and Merging,
  which picks a splat grid by name, could take the old one. The live copies
  are left for the rerun to overwrite, so a rerun that is never started loses
  nothing;
* the stage before's output is pointed where this stage reads, and the clips
  are ticked in its list for Process Selected (user: tick and wait, no start).

With Resume off nothing has moved: the moves are empty and the rerun writes
over the old files in place.

One clip sent to DepthCrafter / Video Depth Anything keeps its rerun of
before (loadForRerun: that clip as the input, its own auto size, nothing moved
when it ends) -- the clip is only put back where Splatting lists it.

plan() reads folders only; apply() moves. No Qt here.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from typing import Callable, Dict, List, Optional, Tuple

from studio import chapter_paths
from studio.clip_queue import inpaint_hires_of, merge_core_name

logger = logging.getLogger("studio.send_back")

#: The stages each stage can send a clip back to (its kind -> target kinds).
PREV = {
    "splatting": ("depthcrafter", "vda", "dvd"),
    "inpainting": ("splatting",),
    "inpainting_v2": ("splatting",),
    "merging": ("inpainting", "inpainting_v2"),
}
TITLES = {"depthcrafter": "DepthCrafter", "vda": "Video Depth Anything", "dvd": "DVD", "splatting": "Splatting",
          "inpainting": "Inpainting", "inpainting_v2": "Inpainting V2"}
#: The folder old copies go into, beside where they were.
REPLACED = "replaced"

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".mxf"}
SIDECAR_EXT = ".fssidecar"


# ---------------------------------------------------------------------------
# folders
# ---------------------------------------------------------------------------

def _level(value) -> str:
    """The folder a setting names: a folder as it is, a file's own folder, else ""."""
    v = str(value or "")
    if not v:
        return ""
    if os.path.isfile(v):
        return os.path.dirname(v)
    return os.path.normpath(v) if os.path.isdir(v) else ""


def _base(folder: str) -> str:
    return chapter_paths._split_finished(folder)[0] if folder else ""


def below(folder: str) -> List[str]:
    """The finished levels under `folder` -- what a stage reading `folder` has archived."""
    return chapter_paths.finished_chain(folder)[1:] if folder and os.path.isdir(folder) else []


def search_order(level: str) -> List[str]:
    """Where to look for a file that belongs at `level`: there and down its chain first,
    then the levels above it (a stage further on may read from higher up)."""
    if not level or not os.path.isdir(level):
        return []
    down = chapter_paths.finished_chain(level)
    seen = {os.path.normcase(d) for d in down}
    up = [d for d in chapter_paths.finished_chain(_base(level)) if os.path.normcase(d) not in seen]
    return down + up


def _names(folder: str) -> List[str]:
    try:
        return sorted(os.listdir(folder))
    except OSError:
        return []


def _same(a: str, b: str) -> bool:
    return bool(a) and bool(b) and os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _find(folders: List[str], match: Callable[[str, str], bool]) -> str:
    """The first entry (file or folder) `match(folder, name)` takes, in `folders`' order."""
    for d in folders:
        for n in _names(d):
            if match(d, n):
                return os.path.join(d, n)
    return ""


def _is_video(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in VIDEO_EXTS


# ---------------------------------------------------------------------------
# names: the clip a row is, and the files of it each stage reads
# ---------------------------------------------------------------------------

def clip_name(kind: str, path: str) -> str:
    """The clip's own name from a row of `kind`'s list."""
    from studio.send_on import splat_base
    stem = os.path.splitext(os.path.basename(path))[0] if not os.path.isdir(path) else os.path.basename(path)
    if kind in ("inpainting", "inpainting_v2"):
        return splat_base(path) or stem
    if kind == "merging":
        return merge_core_name(path) or stem
    return stem


def _source_match(name: str, folders_ok: bool = False):
    low = name.lower()

    def m(d: str, n: str) -> bool:
        p = os.path.join(d, n)
        if folders_ok and os.path.isdir(p):
            return n.lower() == low and n.lower() != chapter_paths.FINISHED
        return os.path.isfile(p) and _is_video(n) and os.path.splitext(n)[0].lower() == low
    return m


def _depth_match(name: str):
    want = {f"{name}_depth.mp4", f"{name}_depth.npz", f"{name}.mp4", f"{name}.npz"}
    want = {w.lower() for w in want}
    return lambda d, n: n.lower() in want and os.path.isfile(os.path.join(d, n))


def _splat_match(name: str):
    from studio.send_on import splat_base

    def m(d: str, n: str) -> bool:
        return (_is_video(n) and os.path.isfile(os.path.join(d, n))
                and (splat_base(n) or os.path.splitext(n)[0]).lower() == name.lower())
    return m


def _inpainted_match(name: str):
    return lambda d, n: (_is_video(n) and os.path.isfile(os.path.join(d, n))
                         and (merge_core_name(n) or "").lower() == name.lower())


# ---------------------------------------------------------------------------
# the plan
# ---------------------------------------------------------------------------

class _Plan:
    def __init__(self):
        self.moves: List[Tuple[str, str]] = []      # (file, the folder it goes into)
        self.aside: List[str] = []                  # old copies, into a "replaced" folder beside them
        self.fields: Dict[str, str] = {}            # the stage before's settings
        self.own: Dict[str, str] = {}               # this stage's, where it must read the rerun's output
        self.ticks: List[str] = []                  # its rows, once moved
        self.missing: List[str] = []                # clips whose inputs weren't found
        self.clips: List[str] = []                  # the clips sent
        self.single: Dict[str, str] = {}            # DepthCrafter / VDA, one clip: {"source", "output"}
        self.note: str = ""

    def put(self, path: str, level: str) -> str:
        """`path` to end up at `level` (a move when it isn't there); its path there."""
        if not path:
            return ""
        if _same(os.path.dirname(path), level):
            return path
        if (path, level) not in self.moves:
            self.moves.append((path, level))
        return os.path.join(level, os.path.basename(path))

    def put_beside(self, folders: List[str], level: str, names: List[str]) -> None:
        """Companions (sidecars, masks): the first of each name found, up to `level`
        -- unless one is there already."""
        for n in names:
            if os.path.exists(os.path.join(level, n)):
                continue
            hit = _find(folders, lambda d, x, n=n: x.lower() == n.lower() and os.path.isfile(os.path.join(d, x)))
            if hit:
                self.put(hit, level)

    def set_aside(self, folders: List[str], match) -> None:
        for d in folders:
            for n in _names(d):
                p = os.path.join(d, n)
                if match(d, n) and p not in self.aside and not any(_same(p, m[0]) for m in self.moves):
                    self.aside.append(p)

    def out(self, target: str, error: str = "") -> Dict:
        if error:
            return {"error": error, "target": target}
        return {"target": target, "title": TITLES.get(target, target), "moves": [list(m) for m in self.moves],
                "aside": list(self.aside), "fields": dict(self.fields), "own_fields": dict(self.own),
                "ticks": list(self.ticks),
                "missing": list(self.missing), "clips": list(self.clips), "single": dict(self.single),
                "note": self.note}


def plan(kind: str, target: str, paths: List[str], values: Dict, target_values: Dict) -> Dict:
    """What sending these rows of `kind`'s list back to `target` does: see the module doc.
    `values` are this stage's settings, `target_values` the stage before's."""
    p = _Plan()
    if target not in PREV.get(kind, ()):
        return p.out(target, "That stage doesn't come before this one.")
    paths = [str(x) for x in paths or [] if x]
    if not paths:
        return p.out(target, "No clip to send.")
    names: List[str] = []
    for x in paths:
        n = clip_name(kind, x)
        if n and n.lower() not in (m.lower() for m in names):
            names.append(n)
    p.clips = names
    if kind == "splatting":
        err = _to_depth(p, names, values, target_values)
    elif kind in ("inpainting", "inpainting_v2"):
        err = _to_splatting(p, kind, names, values, target_values)
    else:
        err = _to_inpainting(p, target, names, values, target_values)
    if err:
        return p.out(target, err)
    if not p.ticks and not p.single:
        return p.out(target, "Couldn't find what " + TITLES.get(target, target) + " needs for "
                     + ", ".join(p.missing or names) + ".")
    return p.out(target)


def _to_depth(p: _Plan, names: List[str], values: Dict, tv: Dict) -> str:
    """Splatting -> DepthCrafter / VDA: the source clip back, the old depth maps aside."""
    src = _level(values.get("input_source_clips"))
    dep = _level(values.get("input_depth_maps"))
    if not src:
        return "Splatting's Input Source Clips folder isn't there."
    one = len(names) == 1
    if one:
        level = src                          # loadForRerun: the clip where Splatting lists it
    else:
        # the folder that, after the stage's own Resume move (or none), is Splatting's
        # Input Source Clips -- so the new map and its clip meet there again
        resume = bool(tv.get("move_to_finished", True))
        level = (os.path.dirname(src) if resume and os.path.basename(src).lower() == chapter_paths.FINISHED
                 else src)
        if not _same(str(tv.get("input_dir_or_file_var") or ""), level):
            p.fields["input_dir_or_file_var"] = level
        if resume and _same(level, src):
            p.note = ("Its Resume will move them into finished/ there when they are done, "
                      "a level below Splatting's Input Source Clips.")
    order = search_order(level) or search_order(src)
    src_order = search_order(src)
    dep_order = search_order(dep) if dep else []
    for n in names:
        clip = _find(order, _source_match(n, folders_ok=True))
        if not clip:
            p.missing.append(n)
            continue
        final = p.put(clip, level)
        # what goes with the clip at Splatting's level: its sidecar and Detect Text mask
        p.put_beside(src_order, src, [n + SIDECAR_EXT, n + "_submask.mp4"])
        if dep:
            p.put_beside(dep_order, dep, [f"{n}_depth{SIDECAR_EXT}"])
            p.set_aside(below(dep), _depth_match(n))
        if one:
            p.single = {"source": final, "output": dep}
        else:
            p.ticks.append(final)
    if dep and not one and not _same(_level(tv.get("output_dir")), dep):
        p.fields["output_dir"] = dep
    return ""


def _splat_output_for(folder: str) -> str:
    """The Splatting output folder whose splats land in `folder` (<out>/lowres, <out>/hires,
    <out>/m2s/lowres), or "" when `folder` isn't one of those."""
    base = _base(folder)
    if os.path.basename(base).lower() not in ("lowres", "hires"):
        return ""
    out = os.path.dirname(base)
    return os.path.dirname(out) if os.path.basename(out).lower() == "m2s" else out


def _to_splatting(p: _Plan, kind: str, names: List[str], values: Dict, tv: Dict) -> str:
    """Inpainting (V1 / V2) -> Splatting: the source clip and depth map back, the old splats aside."""
    src = _level(tv.get("input_source_clips"))
    dep = _level(tv.get("input_depth_maps"))
    if not src or not dep:
        return "Set Splatting's Input Source Clips and Input Depth Maps folders first."
    if os.path.isfile(str(tv.get("input_source_clips") or "")):
        p.fields["input_source_clips"] = src            # a folder, so the clips can be ticked
    if os.path.isfile(str(tv.get("input_depth_maps") or "")):
        p.fields["input_depth_maps"] = dep
    multi = bool(tv.get("multi_map"))
    src_order = search_order(src)
    if multi:
        subs = [os.path.join(dep, e) for e in _names(dep)
                if os.path.isdir(os.path.join(dep, e)) and e.lower() not in ("sidecars", "edit_history",
                                                                               chapter_paths.FINISHED)]
    else:
        subs = [dep]
    here = _level(values.get("input_folder"))
    hires = _level(values.get("hires_blend_folder")) if kind == "inpainting" else ""
    # A chapter picked once its clips were done reads each input where the clips are
    # (chapter_paths.locate): this stage <splats>/lowres/finished, Splatting <source>/finished/
    # finished. Splatting writes its new splats at the top of <out>/lowres and <out>/hires, so
    # this stage is pointed back there, and every archived copy -- the level it read included --
    # goes aside (a tester, 2026-11-02: the old splat was inpainted again, the new ones were
    # never moved to finished/ and the clip never reached Merging).
    archive = {}                                         # its folder -> the levels to set aside
    for key, folder in (("input_folder", here), ("hires_blend_folder", hires)):
        if not folder:
            continue
        base = _base(folder)
        if not _same(base, folder) and _splat_output_for(folder) and os.path.isdir(base):
            if not os.path.isfile(str(values.get(key) or "")):
                p.own[key] = base
            archive[folder] = below(base)
        else:
            archive[folder] = below(folder)
    found = []
    for n in names:
        clip = _find(src_order, _source_match(n))
        depth, at = "", ""
        for d in subs:
            depth = _find(search_order(d), _depth_match(n))
            if depth:
                at = d
                break
        if not clip or not depth:
            p.missing.append(n)
            continue
        found.append((n, clip, depth, at))
    # ...and Splatting's own input there too: its source at <source>/finished/finished, the clip
    # found right at that level is the archive Merging reads its original from. Splatting reads it
    # from a level up, so its Resume move puts it back there, not a level deeper.
    resume = bool(tv.get("move_to_finished", True))
    lift = (resume and found and _base(src) != os.path.normpath(src)
            and all(_same(os.path.dirname(c), src) for _n, c, _d, _a in found))
    if lift:
        src = os.path.dirname(src)
        p.fields["input_source_clips"] = src
        if not multi and _base(dep) != os.path.normpath(dep) and all(_same(a, dep) and _same(os.path.dirname(d), dep)
                                                                    for _n, _c, d, a in found):
            dep = os.path.dirname(dep)
            p.fields["input_depth_maps"] = dep
            found = [(n, c, d, dep) for n, c, d, _a in found]
    for n, clip, depth, at in found:
        p.ticks.append(p.put(clip, src))
        p.put(depth, at)
        p.put_beside(src_order, src, [n + SIDECAR_EXT, n + "_submask.mp4"])
        p.put_beside(search_order(at), at, [f"{n}_depth{SIDECAR_EXT}"])
        if multi:
            p.put_beside(search_order(os.path.join(dep, "sidecars")), os.path.join(dep, "sidecars"),
                         [f"{n}_depth{SIDECAR_EXT}"])
        if here:
            match = _splat_match(n)
            p.set_aside(archive[here], lambda d, x, match=match: match(d, x) or (
                x.lower().endswith(".spsidecar") and match(d, os.path.splitext(x)[0] + ".mp4")))
        if hires and not _same(hires, here):
            p.set_aside(archive[hires], _splat_match(n))
    out = _splat_output_for(here) if here else ""
    if out and not _same(_level(tv.get("output_splatted")), out):
        p.fields["output_splatted"] = out
    if p.own:
        title = TITLES.get(kind, kind)
        p.note = (f"{title} now reads " + " and ".join(os.path.normpath(v) for v in p.own.values())
                  + ", where Splatting writes the new splats.")
    return ""


def _to_inpainting(p: _Plan, target: str, names: List[str], values: Dict, tv: Dict) -> str:
    """Merging -> Inpainting / Inpainting V2: the splats back, the old inpainted clips aside."""
    title = TITLES[target]
    low = _level(tv.get("input_folder"))
    if not low:
        return f"Set {title}'s input folder first."
    if os.path.isfile(str(tv.get("input_folder") or "")):
        p.fields["input_folder"] = low
    hires = _level(tv.get("hires_blend_folder")) if target == "inpainting" else ""
    low_order = search_order(low)
    hires_order = search_order(hires) if hires and not _same(hires, low) else []
    here = _level(values.get("inpainted_folder"))
    for n in names:
        splat = _find(low_order, _splat_match(n))
        if not splat:
            p.missing.append(n)
            continue
        p.ticks.append(p.put(splat, low))
        sc = os.path.splitext(splat)[0] + ".spsidecar"
        if os.path.isfile(sc):
            p.put(sc, low)
        for d in hires_order:
            hi = inpaint_hires_of(splat, d)
            if hi:
                p.put(hi, hires)
                break
        if here:
            match = _inpainted_match(n)
            p.set_aside(below(here), match)
            # its sidecars (Grain Match's) are the user's: back beside the clip Merging reads
            for d in below(here):
                for x in _names(d):
                    stem, ext = os.path.splitext(x)
                    if ext.lower() in (SIDECAR_EXT, ".json") and (stem.lower() == n.lower() or match(d, stem + ".mp4")):
                        if not os.path.exists(os.path.join(here, x)):
                            p.put(os.path.join(d, x), here)
    if here and not _same(_level(tv.get("output_folder")), here):
        p.fields["output_folder"] = here
    return ""


# ---------------------------------------------------------------------------
# doing it
# ---------------------------------------------------------------------------

def take_back(ctl, queue, p: Dict, set_field: Callable[[str, str], None]) -> None:
    """The stage before's side, after apply(): its settings pointed at the clips, them ticked
    in its list, and a toast that says so (each controller's takeBack calls this)."""
    title = p.get("title") or "the stage before"
    if bool(ctl.property("busy")):
        ctl.toast.emit("warning", f"{title} is busy", "Wait for its job to finish, then try again.")
        return
    single = p.get("single") or {}
    if single and hasattr(ctl, "loadForRerun"):
        ctl.loadForRerun(single["source"], single.get("output", ""))      # it says what it loaded
        return
    fields = dict(p.get("fields") or {})
    for key, value in fields.items():
        set_field(key, os.path.normpath(str(value)))
    leave = getattr(ctl, "_maybe_leave_chapter", None)
    if fields and leave is not None:
        leave()                                  # a folder set by hand isn't the chapter's any more
    ctl.save()
    ticks = list(p.get("ticks") or [])
    queue.want_ticks(ticks)
    names = [os.path.basename(t) for t in ticks]
    lines = [(f"{len(ticks)} clips ticked: " if len(ticks) > 1 else "Ticked: ")
             + ", ".join(names[:6]) + (f" (+{len(names) - 6} more)" if len(names) > 6 else "")]
    moved = len(p.get("moves") or [])
    if moved:
        lines.append(f"{moved} file{'s' if moved != 1 else ''} brought back out of finished/.")
    aside = len(p.get("aside") or [])
    if aside:
        lines.append(f"{aside} old cop{'ies' if aside != 1 else 'y'} moved into '{REPLACED}' folders.")
    for key, value in fields.items():
        lines.append(f"{'Input' if 'input' in key else 'Output'}: {os.path.normpath(str(value))}")
    if p.get("missing"):
        lines.append("Not found, so not sent: " + ", ".join(p["missing"]))
    if p.get("note"):
        lines.append(p["note"])
    lines.append("Check the settings, then press Process Selected.")
    ctl.toast.emit("warning" if p.get("missing") else "info", f"Ready to rerun in {title}", "\n".join(lines))


def _free(path: str) -> str:
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    i = 2
    while os.path.exists(f"{stem} ({i}){ext}"):
        i += 1
    return f"{stem} ({i}){ext}"


def _move(src: str, dest: str, retries: int = 5, delay: float = 0.4) -> Optional[str]:
    for attempt in range(retries):
        try:
            shutil.move(src, dest)
            return None
        except OSError as e:                     # Windows: still open somewhere
            if attempt == retries - 1:
                return str(e)
            time.sleep(delay)
    return "not moved"


def apply(p: Dict, log: Optional[logging.Logger] = None) -> List[str]:
    """Make the moves; the names of the files that wouldn't move (in use?)."""
    log = log or logger
    failed: List[str] = []
    for src, level in p.get("moves") or []:
        if not os.path.exists(src):
            continue
        dest = os.path.join(level, os.path.basename(src))
        if os.path.exists(dest):
            continue                              # one is there already: it stays
        os.makedirs(level, exist_ok=True)
        err = _move(src, dest)
        if err:
            failed.append(os.path.basename(src))
            log.warning("Rerun: couldn't move %s back to %s: %s", src, level, err)
        else:
            log.info("Rerun: %s back to %s", os.path.basename(src), level)
    for old in p.get("aside") or []:
        if not os.path.exists(old):
            continue
        folder = os.path.join(os.path.dirname(old), REPLACED)
        os.makedirs(folder, exist_ok=True)
        err = _move(old, _free(os.path.join(folder, os.path.basename(old))))
        if err:
            failed.append(os.path.basename(old))
            log.warning("Rerun: couldn't move the old %s aside: %s", old, err)
        else:
            log.info("Rerun: old %s into %s", os.path.basename(old), folder)
    return failed
