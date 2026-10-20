"""merge_all.py -- Merging's "Merge All SBS to 1 Video" (user, 2026-10-11).

Every scene of the project (or the episode in use) put back together as the
whole movie: each chapter's merged outputs, in chapter order and scene order,
as one video -- Full SBS, Half SBS, or a left and a right file (Settings >
Encoding > Merge All).

The scenes are the clips Scene Cut made: every video under each chapter of the
Source stage folder, at any finished level or subfolder (Part 2/...), named so
that their trailing number is where they start in the movie. A scene is merged
when the Merged stage folder holds a Full SBS (``<clip>_<width>_merged_full_sbs``),
a cross-eye or half SBS, or a split ``_L`` / ``_R`` pair for it -- in its
chapter's folder (any subfolder), or anywhere in that stage folder when the
chapter's has none.

A scene without one is "missing": scan() says which stage it still needs (the
first of DepthCrafter, Splatting, Inpainting, Merging with no output for it),
send_plan() is what loads it there, and the merge can go ahead anyway with a
black clip of the scene's own length in its place, so the soundtrack (the
movie file picked in the settings) still lines up.

Two ways to build, chosen per run:

  concat  every piece is an intra-frame codec (DNxHR, ProRes, FFV1) in one
          format: ffmpeg reads them as one stream (its concat demuxer) and
          the black fillers are made in that same format. Fastest.
  pipe    anything else -- long-GOP clips (H.264 / H.265: the concat demuxer
          mis-decodes HEVC pieces from different encodes, tested 2026-10-11),
          mixed sizes, mixed SBS / L-R: one decoder per scene writes raw
          frames, at the movie's picture size, into the one encoder.

Either way the encoder gets the full side-by-side picture and makes the
chosen layout from it. Output goes to ``<name>.part`` and is published whole
(core.common.partial_output); Stop discards it.

No Qt here: the Merging controller runs it on a thread.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional, Tuple

from core.common import partial_output

logger = logging.getLogger("studio.merge_all")

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".mxf"}
#: Folders a walk never goes into: the editor's / Erase's kept originals, and
#: the stages' own working folders (_inpaint_resume, ...).
SKIP_DIRS = {"edit_history"}
WALK_DEPTH = 6

#: A merged output: <clip>_<width>_merged_<kind>[_L|_R].<ext>
MERGED_RE = re.compile(r"^(?P<stem>.+?)_(?P<w>\d+)_merged_(?P<kind>full_sbsx|full_sbs|half_sbs)"
                       r"(?:_(?P<eye>[LR]))?\.(?:mp4|mov|mkv)$", re.IGNORECASE)
KIND_OF = {"full_sbs": "sbs", "full_sbsx": "sbsx", "half_sbs": "half"}

#: The stages, in order, as the missing list names them.
STAGES = ("depthcrafter", "splatting", "inpainting", "merging")
STAGE_TITLES = {"depthcrafter": "DepthCrafter", "splatting": "Splatting", "inpainting": "Inpainting",
                "merging": "Merging"}
STAGE_INDEX = {"depthcrafter": 0, "splatting": 1, "inpainting": 2, "merging": 3}

#: Codecs whose every frame stands alone: their files join without re-encoding.
INTRA_CODECS = {"dnxhd", "prores", "ffv1", "mjpeg", "rawvideo", "utvideo", "huffyuv"}

LAYOUTS = ("Full SBS", "Half SBS", "L and R files")
CODECS = ("H.265", "H.264", "DNxHR")
CONTAINERS = ("MKV", "MP4", "MOV")
AUDIO_CODECS = ("Copy", "AAC")
_EXT = {"MKV": ".mkv", "MP4": ".mp4", "MOV": ".mov"}

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ---- finding the scenes --------------------------------------------------------------------

def _walk(folder: str, depth: int = WALK_DEPTH) -> List[str]:
    """Every file under `folder`, down to `depth` levels, skipping SKIP_DIRS and
    folders starting with "_" or "."."""
    out: List[str] = []
    if not folder or depth < 0:
        return out
    try:
        with os.scandir(folder) as it:
            entries = list(it)
    except OSError:
        return out
    for e in entries:
        try:
            if e.is_dir():
                if e.name.lower() in SKIP_DIRS or e.name[:1] in ("_", "."):
                    continue
                out.extend(_walk(e.path, depth - 1))
            elif e.is_file():
                out.append(e.path)
        except OSError:
            continue
    return out


def _videos(folder: str) -> List[str]:
    return [p for p in _walk(folder) if os.path.splitext(p)[1].lower() in VIDEO_EXTS
            and not partial_output.is_part(p)]


def _is_scene(path: str) -> bool:
    """A clip Scene Cut made -- not what other stages leave beside one: Detect Text's
    <clip>_submask mask, or a depth map filed among the clips."""
    from core.common.submask_files import is_subtitle_mask
    stem = os.path.splitext(os.path.basename(path))[0].lower()
    return not (is_subtitle_mask(path) or stem.endswith("_depth"))


_TRAIL = re.compile(r"(\d+)$")


def _natural(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def _order(stems: List[str]) -> List[str]:
    """Scene order: by the trailing number (where the scene starts in the movie)
    when every name has one, else by name, numbers counted as numbers."""
    nums = [_TRAIL.search(s) for s in stems]
    if stems and all(nums):
        return [s for _n, s in sorted(zip((int(m.group(1)) for m in nums), stems), key=lambda t: (t[0], _natural(t[1])))]
    return sorted(stems, key=_natural)


def _mtime(p: str) -> float:
    try:
        return os.path.getmtime(p)
    except OSError:
        return 0.0


def _pick_output(entries: List[dict], chapter: int) -> Tuple[Optional[str], Tuple[str, ...], str]:
    """(kind, files, note) for one scene's merged outputs: an L / R pair from one
    folder first, then a Full SBS, a cross-eye SBS, a Half SBS -- the newest of
    each, the chapter's own folder preferred. (None, (), why) when there is none."""
    if not entries:
        return None, (), ""
    own = [e for e in entries if e["chapter"] == chapter]
    pool = own or entries
    pairs = []
    by_dir: Dict[Tuple[str, str], Dict[str, str]] = {}
    for e in pool:
        if e["kind"] == "sbs" and e["eye"]:
            by_dir.setdefault((os.path.dirname(e["path"]), e["base"]), {})[e["eye"]] = e["path"]
    for eyes in by_dir.values():
        if "L" in eyes and "R" in eyes:
            pairs.append((max(_mtime(eyes["L"]), _mtime(eyes["R"])), eyes["L"], eyes["R"]))
    if pairs:
        _t, left, right = max(pairs)
        return "lr", (left, right), ""
    for kind in ("sbs", "sbsx", "half"):
        whole = [e["path"] for e in pool if e["kind"] == kind and not e["eye"]]
        if whole:
            return kind, (max(whole, key=_mtime),), ""
    eyes = {e["eye"] for e in pool if e["eye"]}
    return None, (), ("only the left eye's file" if eyes == {"L"} else "only the right eye's file"
                      if eyes == {"R"} else "")


def _merged_index(merged_root: str) -> Dict[str, List[dict]]:
    """{scene name (lower case): its merged outputs} for the whole Merged stage folder."""
    from core.ui import chapters
    index: Dict[str, List[dict]] = {}
    if not merged_root or not os.path.isdir(merged_root):
        return index
    root = os.path.normpath(merged_root)
    for p in _walk(merged_root):
        m = MERGED_RE.match(os.path.basename(p))
        if not m:
            continue
        rel = os.path.relpath(os.path.normpath(p), root)
        top = rel.split(os.sep)[0] if os.sep in rel else ""
        num = chapters.chapter_number(top) if top else None
        index.setdefault(m.group("stem").lower(), []).append({
            "path": p, "kind": KIND_OF[m.group("kind").lower()], "eye": (m.group("eye") or "").upper(),
            "chapter": num, "base": os.path.basename(p)[: m.start("eye") - 1] if m.group("eye") else ""})
    return index


def _first(files: List[str], stem: str, must: str = "", inside: str = "") -> str:
    """The newest of `files` named for `stem` (it goes on with "_" or ".") that
    contains `must`, and lies under a folder named `inside` if given."""
    s = stem.lower()
    hits = []
    for p in files:
        n = os.path.basename(p).lower()
        if n.startswith(s) and n[len(s):len(s) + 1] in ("_", ".") and must in n:
            if inside and inside not in [part.lower() for part in os.path.normpath(p).split(os.sep)[:-1]]:
                continue
            hits.append(p)
    return max(hits, key=_mtime) if hits else ""


def _progress_of(stem: str, cd: Dict[str, str], cache: Dict[str, List[str]]) -> Dict[str, str]:
    """Where `stem` has got to: {"stage": the first stage with no output for it,
    and the files found on the way}."""
    def files(role: str) -> List[str]:
        if role not in cache:
            cache[role] = _videos(cd.get(role, ""))
        return cache[role]
    out: Dict[str, str] = {}
    depth = _first(files("depth"), stem, "_depth")
    if not depth:
        return {**out, "stage": "depthcrafter"}
    out["depth"] = depth
    low = _first(files("splats"), stem, "_splatted", inside="lowres")
    hi = _first(files("splats"), stem, "_splatted", inside="hires")
    if not (low or hi):
        low = _first(files("splats"), stem, "_splatted")
    if not (low or hi):
        return {**out, "stage": "splatting"}
    out["splat"] = low or hi
    if hi:
        out["hires_dir"] = os.path.dirname(hi)
    inpainted = _first(files("inpainted"), stem, "inpainted")
    if not inpainted:
        return {**out, "stage": "inpainting"}
    out["inpainted"] = inpainted
    return {**out, "stage": "merging"}


def scan(base: str) -> Dict:
    """Every scene of the project / episode at `base`, in movie order, with its
    merged output or what is missing. {"error"} when `base` has no Source folder."""
    from core.ui import chapters
    dirs = chapters.find_stage_dirs(base) if base and os.path.isdir(base) else {}
    if "source" not in dirs:
        return {"error": "There is no Source Clips folder in\n" + str(base or "(no project)")
                         + "\n\nMerge All finds the scenes there, chapter by chapter."}
    merged_root = dirs.get("merged") or chapters.stage_dirs(base)["merged"]
    index = _merged_index(merged_root)
    source_chapters = chapters.chapters_in(dirs["source"])
    scenes: List[dict] = []
    chapter_rows: List[dict] = []
    for num in sorted(source_chapters):
        names = source_chapters[num]
        cd = chapters.chapter_dirs(base, num, names[0])
        found: Dict[str, str] = {}
        for name in names:
            for p in filter(_is_scene, _videos(os.path.join(dirs["source"], name))):
                found.setdefault(os.path.splitext(os.path.basename(p))[0], p)
        if not found:
            continue
        cache: Dict[str, List[str]] = {}
        missing = 0
        for stem in _order(list(found)):
            kind, files, note = _pick_output(index.get(stem.lower(), []), num)
            row = {"chapter": num, "chapterName": names[0], "stem": stem, "source": found[stem].replace("\\", "/"),
                   "kind": kind or "", "files": [f.replace("\\", "/") for f in files]}
            if kind is None:
                missing += 1
                prog = _progress_of(stem, cd, cache)
                row.update({"stage": prog.pop("stage"), "found": prog, "note": note,
                            "dirs": {r: (cd.get(r) or "").replace("\\", "/") for r in ("depth", "splats", "inpainted", "merged")}})
                row["stageTitle"] = STAGE_TITLES[row["stage"]]
            scenes.append(row)
        chapter_rows.append({"num": num, "name": names[0], "scenes": len(found), "missing": missing})
    missing_rows = [s for s in scenes if not s["kind"]]
    return {"base": base.replace("\\", "/"), "name": os.path.basename(os.path.normpath(base)),
            "mergedRoot": (merged_root or "").replace("\\", "/"), "chapters": chapter_rows,
            "scenes": scenes, "missing": missing_rows, "total": len(scenes)}


def send_plan(scene: Dict) -> Dict:
    """What loads a missing scene into the stage it still needs: {"stage",
    "index", "clip", "input", "fields"} for that controller's loadSent -- for
    DepthCrafter {"source", "output"} for its loadForRerun."""
    stage, found, dirs = scene.get("stage"), dict(scene.get("found") or {}), dict(scene.get("dirs") or {})
    src = scene.get("source", "")
    plan = {"stage": stage, "index": STAGE_INDEX.get(stage, 3), "clip": os.path.basename(src)}
    if stage == "depthcrafter":
        plan.update({"source": src, "output": dirs.get("depth", ""), "input": src})
    elif stage == "splatting":
        plan["fields"] = {"input_source_clips": src, "input_depth_maps": found.get("depth", "")}
        if dirs.get("splats"):
            plan["fields"]["output_splatted"] = dirs["splats"]
        plan["input"] = found.get("depth", "")
    elif stage == "inpainting":
        plan["fields"] = {"input_folder": found.get("splat", "")}
        if found.get("hires_dir"):
            plan["fields"]["hires_blend_folder"] = found["hires_dir"]
        if dirs.get("inpainted"):
            plan["fields"]["output_folder"] = dirs["inpainted"]
        plan["input"] = found.get("splat", "")
    else:
        plan["fields"] = {"inpainted_folder": found.get("inpainted", ""), "original_folder": os.path.dirname(src)}
        mask = found.get("hires_dir") or (os.path.join(dirs["splats"], "hires") if dirs.get("splats") else "")
        if mask:
            plan["fields"]["mask_folder"] = mask
        if dirs.get("merged"):
            plan["fields"]["output_folder"] = dirs["merged"]
        plan["input"] = found.get("inpainted", "")
    return plan


# ---- probing ----------------------------------------------------------------------------------

def _run(cmd: List[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, creationflags=_NO_WINDOW, **kw)


def probe(path: str, count: bool = True) -> Dict:
    """The first video stream: codec, profile, pix_fmt, w, h, fps (a rational
    string) and frames -- the container's count, else its packets (no decode)."""
    r = _run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
              "stream=codec_name,profile,pix_fmt,width,height,r_frame_rate,nb_frames", "-of", "json", path])
    streams = (json.loads(r.stdout or "{}").get("streams") or []) if r.returncode == 0 else []
    if not streams:
        raise ValueError(f"No video stream in {path}")
    s = streams[0]
    try:
        frames = int(s.get("nb_frames") or 0)
    except ValueError:
        frames = 0
    if frames <= 0 and count:
        c = _run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                  "stream=nb_read_packets", "-of", "csv=p=0", path])
        try:
            frames = int((c.stdout.strip().splitlines() or ["0"])[0] or 0)
        except ValueError:
            frames = 0
    return {"codec": str(s.get("codec_name", "")), "profile": str(s.get("profile", "") or ""),
            "pix_fmt": str(s.get("pix_fmt", "")), "w": int(s.get("width") or 0), "h": int(s.get("height") or 0),
            "fps": str(s.get("r_frame_rate") or "24/1"), "frames": frames}


def _fps_float(fps: str) -> float:
    try:
        num, _, den = str(fps).partition("/")
        return float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return 24.0


def probe_all(paths: List[str], progress: Optional[Callable[[int, int], None]] = None,
              stop: Optional[threading.Event] = None, workers: int = 8) -> Dict[str, Dict]:
    """probe() of each path, `workers` at a time. Stops early (missing entries) on `stop`."""
    out: Dict[str, Dict] = {}
    todo = list(dict.fromkeys(paths))
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futs = {p: pool.submit(probe, p) for p in todo}
        for p, f in futs.items():
            if stop is not None and stop.is_set():
                for g in futs.values():
                    g.cancel()
                break
            out[p] = f.result()
            done += 1
            if progress is not None:
                progress(done, len(todo))
    return out


# ---- the plan ---------------------------------------------------------------------------------

def _same_format(a: Dict, b: Dict) -> bool:
    return all(a.get(k) == b.get(k) for k in ("codec", "profile", "pix_fmt", "w", "h", "fps"))


def plan_build(info: Dict, probes: Dict[str, Dict], override: bool) -> Dict:
    """How to build: the movie's picture (eye size, fps), the way (concat or pipe)
    and each scene's piece. {"error"} when it can't be built."""
    real = [s for s in info["scenes"] if s["kind"]]
    if not real:
        return {"error": "No scene has a merged output yet."}
    missing = [s for s in info["scenes"] if not s["kind"]]
    if missing and not override:
        return {"error": f"{len(missing)} scene(s) have no merged output."}
    counts: Dict[str, int] = {}
    for s in real:
        counts[s["kind"]] = counts.get(s["kind"], 0) + 1
    kind = max(counts, key=lambda k: (counts[k], k == "lr", k == "sbs"))
    ref_scene = next(s for s in real if s["kind"] == kind)
    ref = probes[ref_scene["files"][0]]
    if kind == "lr":
        eye_w, eye_h = ref["w"], ref["h"]
    elif kind == "half":
        eye_w, eye_h = ref["w"] // 2 * 2, ref["h"]
    else:
        eye_w, eye_h = ref["w"] // 2, ref["h"]
    uniform = all(s["kind"] == kind and all(_same_format(probes[f], ref) for f in s["files"]) for s in real)
    way = "concat" if uniform and ref["codec"] in INTRA_CODECS else "pipe"
    fps = ref["fps"]
    pieces = []
    for s in info["scenes"]:
        if s["kind"]:
            frames = min(probes[f]["frames"] for f in s["files"])
            if frames <= 0:
                # dropped silently, the sound drifted after it (bug sweep 2026-10-14c)
                return {"error": f"Can't tell how many frames {os.path.basename(s['files'][0])} has."}
            pieces.append({"stem": s["stem"], "kind": s["kind"], "files": list(s["files"]), "frames": frames})
        else:
            src = probes.get(s["source"]) or {}
            n = int(src.get("frames") or 0)
            if src.get("fps") and _fps_float(src["fps"]) > 0 and abs(_fps_float(src["fps"]) - _fps_float(fps)) > 1e-3:
                n = int(round(n * _fps_float(fps) / _fps_float(src["fps"])))
            if n <= 0:
                return {"error": f"Can't tell how long {os.path.basename(s['source'])} is, for its black clip."}
            pieces.append({"stem": s["stem"], "kind": "black", "files": [], "frames": n})
    return {"kind": kind, "ref": ref, "eye": (eye_w, eye_h), "fps": fps, "way": way, "pieces": pieces,
            "frames": sum(p["frames"] for p in pieces), "black": len(missing)}


# ---- encoding ---------------------------------------------------------------------------------

def _dnxhr(profile_label: str) -> Tuple[str, str]:
    """(ffmpeg profile, pix_fmt) for a DNxHR profile label of the settings."""
    key = (str(profile_label or "").strip().upper().split() or ["HQX"])[0]      # "HQX (10-bit 4:2:2)" -> HQX
    prof = {"SQ": "dnxhr_sq", "HQ": "dnxhr_hq", "HQX": "dnxhr_hqx", "444": "dnxhr_444"}.get(key, "dnxhr_hqx")
    pix ={"dnxhr_sq": "yuv422p", "dnxhr_hq": "yuv422p", "dnxhr_hqx": "yuv422p10le", "dnxhr_444": "yuv444p10le"}[prof]
    return prof, pix


def encoder_args(opts: Dict, ref: Dict) -> Tuple[List[str], str]:
    """(ffmpeg video encoder arguments, the raw pix_fmt the picture is made in)."""
    if opts.get("codec") == "DNxHR":
        prof, pix = _dnxhr(opts.get("dnxhr_profile", ""))
        return ["-c:v", "dnxhd", "-profile:v", prof, "-pix_fmt", pix], pix
    from core.common.encoding_utils import build_encoder_args
    bit_depth = opts.get("bit_depth") or "Auto"
    if bit_depth == "Auto":
        bit_depth = "10-bit (Main10)" if ("10" in ref.get("pix_fmt", "") or "12" in ref.get("pix_fmt", "")) else "8-bit"
    try:
        crf = int(float(opts.get("crf", 18)))
    except (TypeError, ValueError):
        crf = 18
    a = build_encoder_args(codec=opts.get("codec") or "H.265", encoder=opts.get("encoder") or "Auto",
                           quality=opts.get("quality") or "Medium", tune="None", crf=crf, bit_depth=bit_depth)
    args = ["-c:v", a["codec"], "-preset", a["preset"], "-pix_fmt", a["pix_fmt"]]
    if a.get("profile"):
        args += ["-profile:v", a["profile"]]
    args += list(a.get("extra_args") or [])
    if "libx265" in a["codec"]:
        args += ["-x265-params", "log-level=error"]
    raw = "yuv420p10le" if a["pix_fmt"] in ("p010le", "yuv420p10le") else "yuv420p"
    return args, raw


def _matching_args(ref: Dict) -> List[str]:
    """Encoder arguments for a piece in `ref`'s own format (the concat way's black clips)."""
    codec, prof, pix = ref["codec"], ref.get("profile", ""), ref["pix_fmt"]
    if codec == "dnxhd":
        p = prof.lower().replace(" ", "_")
        return ["-c:v", "dnxhd", "-profile:v", p if p.startswith("dnxhr") else "dnxhr_hqx", "-pix_fmt", pix]
    if codec == "prores":
        n = {"proxy": "0", "lt": "1", "standard": "2", "hq": "3", "4444": "4", "4444 xq": "5", "xq": "5"}.get(prof.lower(), "3")
        return ["-c:v", "prores_ks", "-profile:v", n, "-pix_fmt", pix]
    if codec == "ffv1":
        return ["-c:v", "ffv1", "-pix_fmt", pix]
    if codec == "mjpeg":
        return ["-c:v", "mjpeg", "-q:v", "2", "-pix_fmt", pix]
    return ["-c:v", codec, "-pix_fmt", pix]


def output_paths(info: Dict, opts: Dict) -> List[str]:
    """The finished file(s): <name> - 3D Full SBS.mkv, or its _L / _R pair."""
    folder = opts.get("output_folder") or info["base"]
    ext = _EXT.get(opts.get("container") or "MKV", ".mkv")
    if opts.get("codec") == "DNxHR" and ext == ".mp4":
        ext = ".mov"                     # DNxHR doesn't go in MP4
    name = info.get("name") or "Movie"
    layout = opts.get("layout") or "Full SBS"
    if layout == "L and R files":
        return [os.path.join(folder, f"{name} - 3D_L{ext}"), os.path.join(folder, f"{name} - 3D_R{ext}")]
    return [os.path.join(folder, f"{name} - 3D {layout}{ext}")]


def _layout_graph(src: str, layout: str) -> Tuple[str, List[str]]:
    """The filter from the full side-by-side picture `src` to the layout: (graph, output labels)."""
    if layout == "Half SBS":
        return f"{src}scale=iw/2:ih:flags=lanczos,setsar=1[v0]", ["[v0]"]
    if layout == "L and R files":
        return (f"{src}split=2[sl][sr];[sl]crop=iw/2:ih:0:0,setsar=1[v0];[sr]crop=iw/2:ih:iw/2:0,setsar=1[v1]",
                ["[v0]", "[v1]"])
    return f"{src}setsar=1[v0]", ["[v0]"]


def _audio_args(opts: Dict, index: int, ext: str) -> Tuple[List[str], List[str]]:
    """(input args, per-output map / codec args) for the movie's soundtrack."""
    path = str(opts.get("audio_file") or "").strip()
    if not path:
        return [], []
    codec = ["-c:a", "aac", "-b:a", "320k"] if opts.get("audio_codec") == "AAC" else ["-c:a", "copy"]
    extra = ["-strict", "-2"] if ext == ".mp4" else []
    return ["-i", path], ["-map", f"{index}:a?"] + codec + extra


def _drain(stream, sink: Optional[List[bytes]] = None) -> None:
    """Read `stream` to its end (into `sink`), then close it."""
    try:
        data = stream.read() or b""
        if sink is not None:
            sink.append(data)
    except (OSError, ValueError):
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _escape_concat(path: str) -> str:
    return "file '" + path.replace("\\", "/").replace("'", "'\\''") + "'\n"


#: A Merge All's work folder (black clips) or a half-written movie a quit left: swept, once old.
SWEEP_AGE_S = 6 * 3600


def _sweep(folder: str) -> None:
    """Remove the _mergeall_* folders and movie .part files earlier runs left in `folder` (a quit
    mid-run: the run's own cleanup never ran -- bug sweep 2026-10-14c), once they're hours old."""
    import time
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        path = os.path.join(folder, name)
        try:
            old = time.time() - os.path.getmtime(path) > SWEEP_AGE_S
        except OSError:
            continue
        if not old:
            continue
        if name.startswith("_mergeall_") and os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif partial_output.is_part(name) and os.path.isfile(path):
            partial_output.discard(path)


class Build:
    """One Merge All run. run() on a worker thread; stop() from any thread."""

    def __init__(self, info: Dict, plan: Dict, opts: Dict,
                 status: Callable[[str], None] = lambda s: None,
                 progress: Callable[[int, int], None] = lambda d, t: None):
        self.info, self.plan, self.opts = info, plan, opts
        self.status, self.progress = status, progress
        self._stop = threading.Event()
        self._procs: List[subprocess.Popen] = []
        self._lock = threading.Lock()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            for p in list(self._procs):
                try:
                    p.kill()
                except OSError:
                    pass

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def _popen(self, cmd: List[str], **kw) -> subprocess.Popen:
        p = subprocess.Popen(cmd, creationflags=_NO_WINDOW, **kw)
        with self._lock:
            self._procs.append(p)
            if self._stop.is_set():
                p.kill()
        return p

    def _done(self, p: subprocess.Popen) -> None:
        with self._lock:
            if p in self._procs:
                self._procs.remove(p)

    def run(self) -> Dict:
        finals = output_paths(self.info, self.opts)
        os.makedirs(os.path.dirname(finals[0]), exist_ok=True)
        parts = [partial_output.part_path(f) for f in finals]
        _sweep(os.path.dirname(finals[0]))
        self.short = []                       # scenes that decoded short (black-filled)
        tmp = tempfile.mkdtemp(prefix="_mergeall_", dir=os.path.dirname(finals[0]))
        try:
            if self.plan["way"] == "concat":
                err = self._run_concat(tmp, parts)
            else:
                err = self._run_pipe(parts)
            if self.stopped:
                return {"stopped": True}
            if err:
                return {"error": err}
            for part, final in zip(parts, finals):
                if not partial_output.publish(part, final):
                    return {"error": f"Could not finish {final}."}
            return {"outputs": finals, "frames": self.plan["frames"], "black": self.plan["black"],
                    "way": self.plan["way"], "short": list(self.short)}
        finally:
            for part in parts:
                if os.path.exists(part):
                    partial_output.discard(part)
            shutil.rmtree(tmp, ignore_errors=True)

    # -- the encoder's command, shared by both ways --
    def _encode_cmd(self, inputs: List[str], picture: str, parts: List[str]) -> List[str]:
        video, raw = encoder_args(self.opts, self.plan["ref"])
        ext = partial_output.final_ext(parts[0])
        n_inputs = sum(1 for a in inputs if a == "-i")
        a_in, a_map = _audio_args(self.opts, n_inputs, ext)
        graph, labels = _layout_graph(picture, self.opts.get("layout") or "Full SBS")
        cmd = ["ffmpeg", "-hide_banner", "-nostats", "-loglevel", "error", "-y"] + inputs + a_in
        cmd += ["-filter_complex", graph, "-progress", "pipe:1"]
        for label, part in zip(labels, parts):
            cmd += ["-map", label] + video + a_map + partial_output.ffmpeg_output_args(part)
        return cmd

    def _canvas(self) -> Tuple[int, int]:
        ew, eh = self.plan["eye"]
        return 2 * ew, eh

    # -- concat: one stream of identical intra-frame pieces --
    def _black_piece(self, tmp: str, n: int, i: int, w: int, h: int) -> Optional[str]:
        ref = self.plan["ref"]
        ext = ".mov" if ref["codec"] in ("dnxhd", "prores") else ".mkv"
        path = os.path.join(tmp, f"black_{i:05d}_{w}x{h}{ext}")
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
               f"color=black:s={w}x{h}:r={self.plan['fps']}", "-frames:v", str(n)] + _matching_args(ref) + [path]
        p = self._popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        _out, err = p.communicate()
        self._done(p)
        if p.returncode != 0 and not self.stopped:
            logger.error("Merge All: black clip failed: %s", (err or b"").decode("utf-8", "replace")[-800:])
            return None
        return path

    def _run_concat(self, tmp: str, parts: List[str]) -> str:
        kind, ref = self.plan["kind"], self.plan["ref"]
        lists = [[], []] if kind == "lr" else [[]]
        blacks = [p for p in self.plan["pieces"] if p["kind"] == "black"]
        made = 0
        for i, piece in enumerate(self.plan["pieces"]):
            if self.stopped:
                return ""
            if piece["kind"] == "black":
                made += 1
                self.status(f"Making black clip {made} of {len(blacks)}...")
                path = self._black_piece(tmp, piece["frames"], i, ref["w"], ref["h"])
                if path is None:
                    return "Could not make a black clip for a missing scene (see the console)."
                for lst in lists:
                    lst.append(path)
            else:
                for lst, f in zip(lists, piece["files"]):
                    lst.append(f)
        inputs = []
        for n, lst in enumerate(lists):
            txt = os.path.join(tmp, f"list_{n}.txt")
            with open(txt, "w", encoding="utf-8") as fh:
                fh.writelines(_escape_concat(p) for p in lst)
            inputs += ["-f", "concat", "-safe", "0", "-i", txt]
        picture = {"lr": "[0:v][1:v]hstack=2,", "sbs": "[0:v]", "sbsx": "[0:v]split=2[a][b];[a]crop=iw/2:ih:iw/2:0[ra];"
                   "[b]crop=iw/2:ih:0:0[rb];[ra][rb]hstack=2,", "half": f"[0:v]scale={self._canvas()[0]}:{self._canvas()[1]},"}[kind]
        if picture == "[0:v]":
            picture = "[0:v]null,"
        cmd = self._encode_cmd(inputs, picture, parts)
        self.status("Encoding the movie...")
        return self._encode(cmd)

    def _encode(self, cmd: List[str], stdin=None) -> str:
        p = self._popen(cmd, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        errs: List[bytes] = []
        t = threading.Thread(target=_drain, args=(p.stderr, errs), daemon=True)
        t.start()
        total = self.plan["frames"]
        for line in p.stdout:
            if line.startswith(b"frame="):
                try:
                    self.progress(int(line[6:].strip() or 0), total)
                except ValueError:
                    pass
        p.wait()
        p.stdout.close()
        t.join(5)
        self._done(p)
        if p.returncode != 0 and not self.stopped:
            text = b"".join(errs).decode("utf-8", "replace").strip()
            logger.error("Merge All: ffmpeg failed (%s): %s", p.returncode, text[-2000:])
            return "ffmpeg stopped with an error:\n\n" + (text[-600:] or f"exit code {p.returncode}")
        return ""

    # -- pipe: one decoder per scene, raw frames into the one encoder --
    def _piece_cmd(self, piece: Dict, raw: str) -> List[str]:
        cw, ch = self._canvas()
        fit = f"scale={cw}:{ch}:flags=lanczos,setsar=1"
        base = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
        out = ["-f", "rawvideo", "-pix_fmt", raw, "pipe:1"]
        k, files = piece["kind"], piece["files"]
        if k == "black":
            return base + ["-f", "lavfi", "-i", f"color=black:s={cw}x{ch}:r={self.plan['fps']}",
                           "-frames:v", str(piece["frames"])] + out
        if k == "lr":
            return base + ["-i", files[0], "-i", files[1], "-filter_complex",
                           f"[0:v][1:v]hstack=2,{fit}", "-frames:v", str(piece["frames"])] + out
        if k == "sbsx":
            return base + ["-i", files[0], "-filter_complex",
                           f"[0:v]split=2[a][b];[a]crop=iw/2:ih:iw/2:0[ra];[b]crop=iw/2:ih:0:0[rb];[ra][rb]hstack=2,{fit}"] + out
        return base + ["-i", files[0], "-vf", fit] + out

    def _run_pipe(self, parts: List[str]) -> str:
        cw, ch = self._canvas()
        _video, raw = encoder_args(self.opts, self.plan["ref"])
        if self.opts.get("codec") == "DNxHR":
            raw = _dnxhr(self.opts.get("dnxhr_profile", ""))[1]
        bpp = {"yuv420p": 1.5, "yuv420p10le": 3.0, "yuv422p": 2.0, "yuv422p10le": 4.0, "yuv444p10le": 6.0}[raw]
        frame_bytes = int(cw * ch * bpp)
        inputs = ["-f", "rawvideo", "-pix_fmt", raw, "-s", f"{cw}x{ch}", "-r", self.plan["fps"], "-i", "pipe:0"]
        cmd = self._encode_cmd(inputs, "[0:v]null,", parts)
        enc = self._popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        errs: List[bytes] = []
        threading.Thread(target=_drain, args=(enc.stderr, errs), daemon=True).start()
        threading.Thread(target=_drain, args=(enc.stdout,), daemon=True).start()   # -progress: not read here
        total, written = self.plan["frames"], 0
        buf = bytearray(frame_bytes)
        mv = memoryview(buf)
        self.status("Encoding the movie...")
        error = ""
        dec = None
        try:
            for piece in self.plan["pieces"]:
                if self.stopped:
                    break
                dec = self._popen(self._piece_cmd(piece, raw), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                derr: List[bytes] = []
                threading.Thread(target=_drain, args=(dec.stderr, derr), daemon=True).start()
                got_frames = 0
                while got_frames < piece["frames"] and not self.stopped:
                    got = 0
                    while got < frame_bytes:
                        n = dec.stdout.readinto(mv[got:])
                        if not n:
                            break
                        got += n
                    if got < frame_bytes:
                        break
                    enc.stdin.write(mv)
                    got_frames += 1
                    written += 1
                    if written % 12 == 0:
                        self.progress(written, total)
                try:
                    dec.kill()
                except OSError:
                    pass
                dec.wait()
                dec.stdout.close()
                self._done(dec)
                if self.stopped:
                    break
                if got_frames < piece["frames"]:
                    # a clip shorter than it said: its last frames repeated would drift the sound --
                    # so black in their place, keeping every scene at its length
                    logger.warning("Merge All: %s gave %d of %d frames; the rest are black.", piece["stem"],
                                   got_frames, piece["frames"])
                    self.short.append(f"{piece['stem']} ({piece['frames'] - got_frames} of {piece['frames']} frames)")
                    black = self._black_raw(raw, cw, ch)
                    for _ in range(piece["frames"] - got_frames):
                        enc.stdin.write(black)
                        written += 1
            self.progress(written, total)
        except (BrokenPipeError, OSError) as e:
            # the scene's decoder too: left running, blocked on a pipe nobody read (sweep 2026-10-14c)
            if dec is not None and dec.poll() is None:
                try:
                    dec.kill()
                    dec.wait(5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            if not self.stopped:
                error = f"The encoder stopped taking frames ({e})."
        finally:
            try:
                enc.stdin.close()
            except OSError:
                pass
            enc.wait()
            self._done(enc)
        if self.stopped:
            return ""
        if enc.returncode != 0:
            text = b"".join(errs).decode("utf-8", "replace").strip()
            logger.error("Merge All: ffmpeg failed (%s): %s", enc.returncode, text[-2000:])
            return "ffmpeg stopped with an error:\n\n" + (text[-600:] or f"exit code {enc.returncode}")
        return error

    @staticmethod
    def _black_raw(raw: str, w: int, h: int) -> bytes:
        """One black frame in `raw` (limited range: Y 16 / 64, chroma at the middle)."""
        import numpy as np
        ten = raw.endswith("10le")
        y = np.full((h, w), 64 if ten else 16, np.uint16 if ten else np.uint8)
        cw, chh = {"yuv420p": (w // 2, h // 2), "yuv420p10le": (w // 2, h // 2), "yuv422p": (w // 2, h),
                   "yuv422p10le": (w // 2, h), "yuv444p10le": (w, h)}[raw]
        c = np.full((chh, cw), 512 if ten else 128, y.dtype)
        return y.tobytes() + c.tobytes() + c.tobytes()


def options_from(values: Dict) -> Dict:
    """Merge All's settings (Merging's mergeall_* keys) as Build reads them."""
    return {"layout": values.get("mergeall_layout") or "Full SBS", "codec": values.get("mergeall_codec") or "H.265",
            "encoder": values.get("mergeall_encoder") or "Auto", "quality": values.get("mergeall_quality") or "Medium",
            "crf": values.get("mergeall_crf") or "18", "bit_depth": values.get("mergeall_bit_depth") or "Auto",
            "dnxhr_profile": values.get("mergeall_dnxhr_profile") or "HQX (10-bit 4:2:2)",
            "container": values.get("mergeall_container") or "MKV",
            "audio_file": values.get("mergeall_audio_file") or "", "audio_codec": values.get("mergeall_audio_codec") or "Copy",
            "output_folder": values.get("mergeall_output_folder") or ""}
