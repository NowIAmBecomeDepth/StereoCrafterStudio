"""erase.py -- take an object out of a clip with ROSE (user, 2026-10-05).

The Depth Map Editor's Erase layers say what goes. This:

  1. works out, per frame, the full-size mask of everything erased (each
     layer's tracked mask as edited -- its full-size one when it has one),
     grown a little: ROSE wants the whole object covered;
  2. plans the windows ROSE runs on -- only the stretches with something to
     erase (plus a little context), each 16n+1 frames, at most WINDOW long,
     overlapping by OVERLAP;
  3. hands them to ROSE in its own environment (rose_worker.py) at about
     480p, the size it was trained at;
  4. puts ROSE's result back into the FULL-SIZE frames inside the (feathered)
     mask only -- every other pixel is the original;
  5. writes the clip in the original's place (same name: Splatting and the
     sidecars pair by it) and keeps the original in edit_history/ beside it,
     with the erase mask, so the erased object can be put back later as its
     own layer (original - erased, inside the mask).

No Qt here: the editor's controller runs erase() on a worker thread.
"""

from __future__ import annotations

import json
import logging
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from core.depthedit import edit_ops, hires, keys
from . import rose_env

logger = logging.getLogger(__name__)

WINDOW = 81               # frames ROSE takes at once (16n+1; its base model's length)
OVERLAP = 16              # frames two windows share, cross-faded
CONTEXT = 8               # frames of context either side of a stretch to erase
WORK_AREA = 480 * 720     # ROSE's working size: about its training size, the clip's shape
STEPS = 50
#: ROSE's classifier-free guidance. With its empty prompt both halves of the
#: guided batch get the same inputs, so guidance changes nothing -- measured
#: 2026-10-06: 6 vs 1 at most 1/255 apart -- and 1 skips the second half:
#: ~1.7x faster.
GUIDANCE = 1.0
#: ROSE's working sizes, as areas in the clip's shape (user, 2026-10-06):
#: about its training size, and two finer (more time and VRAM, past what it
#: was trained on).
SIZES = {"480p": 480 * 720, "576p": 576 * 1024, "720p": 720 * 1280}
WINDOWS = (33, 49, 65, 81)
#: Pre-fill (user, 2026-10-15): ROSE sees the clip un-blanked under the mask (to take an object's
#: shadows and reflections with it), and a big object -- a person filling 30% of a close-up -- came
#: back REDRAWN. Pre-filled, the hole is a smooth guess from around it and ROSE invents what's behind.
#: Auto: on when the object covers at least this share of the frame (on the frames it's on).
PREFILL_AUTO_SHARE = 0.10
PREFILLS = ("Auto", "On", "Off")
#: Refine at Full Size (user, 2026-10-15): a second ROSE pass in tiles at the clip's own size over the
#: first pass's result (sharper), its tone locked to the first pass's below REFINE_TONE px (the tiles
#: each drift in colour). Tiles at most REFINE_TILE (w, h): ROSE's own size. Edge Fill found ROSE smears
#: a much taller tile, and 1024-wide tiles (2 x 2 at 1080p) came back with broken, rocky texture and dark
#: blotches on a test clip where 832 x 560 (3 x 2) didn't (2026-10-15). Tiles with nothing to erase
#: are skipped.
REFINE_TILE = (832, 560)
REFINE_OVERLAP = 80
REFINE_TONE = 48.0
HISTORY_DIR = "edit_history"   # the editor's own (core.depthedit.project.HISTORY_DIR)

Progress = Callable[[float, str], None]


class Stopped(Exception):
    pass


@dataclass
class EraseLayer:
    """One Erase layer, frozen on the UI thread when Erase from Clip starts."""
    settings: edit_ops.LayerSettings
    track: object                       # its KeyframeTrack, or None
    lo: object                          # MaskStore at the depth map's size
    hi: object = None                   # MaskStore at the clip's size, or None


# ---- paths ------------------------------------------------------------------------

#: How many finished/ levels below a clip's folder its history is looked for (Restore Finished
#: brings a clip back up; its history stays where the erase put it).
_DOWN_LEVELS = 3


def history_candidates(folder: str) -> List[str]:
    """The edit_history folders a clip in `folder` may keep its history in, nearest first: beside
    it; then those of the folders it was moved down from -- a stage moves a consumed clip into
    finished/ and its edit_history stays put (user, 2026-10-18: Splatting after an erase lost
    Merging's Put Back); then those below it, for a clip Restore Finished brought back up."""
    folder = os.path.normpath(folder or ".")
    out = [os.path.join(folder, HISTORY_DIR)]
    up = folder
    while os.path.basename(up).lower() == "finished" and os.path.dirname(up) != up:
        up = os.path.dirname(up)
        out.append(os.path.join(up, HISTORY_DIR))
    down = folder
    for _ in range(_DOWN_LEVELS):
        down = os.path.join(down, "finished")
        out.append(os.path.join(down, HISTORY_DIR))
    return out


def history_dir(clip: str) -> str:
    """The clip's edit_history: the nearest of history_candidates() holding its erase record,
    else the one beside it (where a first erase puts it)."""
    cands = history_candidates(os.path.dirname(clip))
    name = os.path.basename(clip) + ".erase.json"
    for d in cands:
        if os.path.isfile(os.path.join(d, name)):
            return d
    return cands[0]


def record_path(clip: str) -> str:
    """Beside the kept original: what Erase from Clip wrote in the clip's place."""
    return os.path.join(history_dir(clip), os.path.basename(clip) + ".erase.json")


def _file_id(path: str):
    st = os.stat(path)
    return [st.st_size, st.st_mtime_ns]


def source_for(clip: str) -> str:
    """The clip to erase FROM: the kept original when the clip there now is an
    erase of it (so erasing again starts from the real footage, not from
    what an earlier erase left), else the clip itself."""
    try:
        with open(record_path(clip), "r", encoding="utf-8") as f:
            rec = json.load(f)
        if rec.get("live") == _file_id(clip) and os.path.isfile(rec.get("original", "")):
            return rec["original"]
    except (OSError, ValueError):
        pass
    return clip


def _backup_path(clip: str) -> str:
    """Where the clip being replaced is kept: <stem>.original<ext>, or
    .original-2, -3... when an older original is already there."""
    stem, ext = os.path.splitext(os.path.basename(clip))
    d = history_dir(clip)
    path, n = os.path.join(d, f"{stem}.original{ext}"), 2
    while os.path.exists(path):
        path, n = os.path.join(d, f"{stem}.original-{n}{ext}"), n + 1
    return path


# ---- masks and windows ----------------------------------------------------------------

def grow_px(width: int) -> int:
    """How far the erase mask reaches past the object: ~0.6% of the width
    (12 px at 1920), so ROSE has the whole object and its fringe."""
    return max(4, int(round(width / 160)))


def frame_mask(layers: Sequence[EraseLayer], idx: int, size: Tuple[int, int]) -> Optional[np.ndarray]:
    """Everything erased on frame `idx`, at `size` (w, h), before growing."""
    w, h = size
    out = None
    for ly in layers:
        s = keys.settings_at(ly.settings, ly.track, idx)
        if not edit_ops.layer_active(s, idx) or s.opacity <= 0:
            continue
        m = None
        hi = ly.hi.get(idx) if ly.hi is not None else None
        if hi is not None and hi.shape == (h, w):
            m = edit_ops.effective_mask(hi, hires.scaled(s, w / float(ly.lo.width), snap=False), None) > 0
        else:
            lo = ly.lo.get(idx)
            if lo is not None:
                m = hires.enlarge_mask(edit_ops.effective_mask(lo, hires.scaled(s, 1.0, snap=False), None), (w, h))
        if m is not None and m.any():
            out = m if out is None else (out | m)
    return out


def work_size(width: int, height: int, area: int = WORK_AREA) -> Tuple[int, int]:
    """ROSE's (w, h): the clip's shape at about WORK_AREA, multiples of 16."""
    aspect = width / float(height)
    h = max(16, int(round(math.sqrt(area / aspect) / 16.0)) * 16)
    w = max(16, int(round(h * aspect / 16.0)) * 16)
    return w, h


def _len16(n: int) -> int:
    """The smallest 16k+1 >= n (and >= 17)."""
    return max(17, 16 * int(math.ceil((n - 1) / 16.0)) + 1)


def _len4(n: int) -> int:
    """The smallest 4k+1 >= n (and >= 17): what ROSE's model takes -- its VAE packs 4 frames to one
    latent after the first (rose pipeline: (num_frames - 1) // 4 + 1). A stretch shorter than the window
    was padded to the next 16k+1: a 70-frame clip ran 81 frames, 11 of them the last frame again
    (user, 2026-10-15); now 73."""
    return max(17, 4 * int(math.ceil((n - 1) / 4.0)) + 1)


def plan_windows(flags: Sequence[bool], window: int = WINDOW, overlap: int = OVERLAP,
                 context: int = CONTEXT) -> List[Tuple[int, int]]:
    """[(start, length)] windows covering every flagged frame plus `context`
    either side; each length is 4k+1 <= window (a window may reach past the
    clip's end -- the frames there are padding). Consecutive windows of one
    stretch overlap by `overlap`."""
    n = len(flags)
    runs, i = [], 0
    while i < n:
        if flags[i]:
            j = i
            while j + 1 < n and flags[j + 1]:
                j += 1
            runs.append([max(0, i - context), min(n, j + 1 + context)])
            i = j + 1
        else:
            i += 1
    merged: List[List[int]] = []
    for a, b in runs:                      # stretches closer than a window's overlap become one
        if merged and a <= merged[-1][1] + overlap:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    out = []
    for a, b in merged:
        if b - a <= window:
            out.append((a, _len4(b - a)))
            continue
        step = window - overlap
        s = a
        while True:
            if s + window >= b:
                out.append((max(a, b - window), window))
                break
            out.append((s, window))
            s += step
    return out


# ---- video in and out -------------------------------------------------------------------

def _probe(path: str) -> Dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                          "stream=width,height,r_frame_rate,pix_fmt,nb_read_packets,color_space,color_transfer,"
                          "color_primaries,color_range", "-of", "json", path],
                         capture_output=True, text=True, check=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    st = json.loads(out)["streams"][0]
    has_audio = bool(json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "json", path],
        capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout or "{}")
        .get("streams"))
    return {"w": int(st["width"]), "h": int(st["height"]), "rate": st.get("r_frame_rate", "24000/1001"),
            "frames": int(st.get("nb_read_packets") or 0), "pix_fmt": st.get("pix_fmt", ""), "audio": has_audio,
            "color": {k: st[k] for k in ("color_space", "color_transfer", "color_primaries", "color_range")
                      if st.get(k) and st[k] != "unknown"}}


def _reader(path: str, w: int, h: int, pix_fmt: str, scale: Optional[Tuple[int, int]] = None):
    """Frames of `path` as arrays: rgb24 (uint8) or rgb48le (uint16)."""
    vf = ["-vf", f"scale={scale[0]}:{scale[1]}:flags=area"] if scale else []
    ow, oh = scale or (w, h)
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-i", path, *vf, "-f", "rawvideo", "-pix_fmt", pix_fmt, "-"],
                            stdout=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    dtype, ch = (np.uint16, 6) if pix_fmt == "rgb48le" else (np.uint8, 3)
    size = ow * oh * ch
    try:
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, dtype).reshape(oh, ow, 3)
    finally:
        proc.stdout.close()
        proc.kill()
        proc.wait()


def _writer(path: str, src: str, info: Dict):
    """An ffmpeg taking rgb48le frames: HEVC 10-bit, near-lossless, the
    source's colour tags and audio kept."""
    color = []
    for key, flag in (("color_space", "-colorspace"), ("color_transfer", "-color_trc"),
                      ("color_primaries", "-color_primaries"), ("color_range", "-color_range")):
        if key in info["color"]:
            color += [flag, info["color"][key]]
    audio = ["-i", src, "-map", "0:v", "-map", "1:a?", "-c:a", "copy"] if info["audio"] else []
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb48le", "-s", f"{info['w']}x{info['h']}",
           "-r", info["rate"], "-i", "-", *audio, "-c:v", "libx265", "-preset", "medium", "-crf", "10",
           "-x265-params", "log-level=error", "-pix_fmt", "yuv420p10le", *color, path]
    from core.common.color_tags import with_color_tags
    cmd = with_color_tags(cmd)     # primaries / transfer into the file, not just asked for
    return _with_stderr(subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE,
                                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)))


def _mask_writer(path: str, w: int, h: int, rate: str):
    return _with_stderr(subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{w}x{h}",
         "-r", rate, "-i", "-", "-c:v", "ffv1", path],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)))


def _with_stderr(proc):
    """A writer whose stderr is read as it comes (``proc.err_tail``, the last
    lines): an encoder that exits early says why, instead of the next write's
    "BrokenPipe / Errno 22" (bug sweep 2026-10-09, A9)."""
    proc.err_tail = []

    def drain():
        for raw in proc.stderr:
            proc.err_tail.append(raw.decode("utf-8", "replace").rstrip())
            del proc.err_tail[:-20]
    proc.err_thread = threading.Thread(target=drain, daemon=True)
    proc.err_thread.start()
    return proc


def writers_failed(writers, written: int, n: int, what: str = "Writing the clip") -> Optional[str]:
    """After the writers were waited for: why the clip isn't whole, or None."""
    for p in writers:
        if p.returncode != 0:
            t = getattr(p, "err_thread", None)
            if t is not None:
                t.join(timeout=2)
            tail = "\n".join(line for line in getattr(p, "err_tail", []) if line)[-800:]
            return (f"{what} failed (ffmpeg exit {p.returncode}, {written} of {n} frames)."
                    + (f"\n\nffmpeg said:\n{tail}" if tail else ""))
    if written != n:
        return f"{what} failed ({written} of {n} frames)."
    return None


def sweep_stale_temp(clip: str) -> None:
    """An erase's temp folder (frames and masks as .npy, gigabytes) left by
    the app closing mid-erase: the daemon thread died without its rmtree (bug
    sweep 2026-10-09, A9). One erase runs at a time and one Studio, so any
    ``.erase_*`` beside the clip when an erase starts is a leftover."""
    folder = os.path.dirname(os.path.abspath(clip))
    try:
        names = os.listdir(folder)
    except OSError:
        return
    for name in names:
        path = os.path.join(folder, name)
        if name.startswith(".erase_") and os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
            if not os.path.exists(path):
                logger.info(f"Removed {name}, left by an erase that didn't finish.")


# ---- the job ------------------------------------------------------------------------------

def step_bar(chunk: int, n_chunks: int, step: int, steps: int, per_step: Optional[float]) -> None:
    """One ROSE window's denoising steps as a console bar (Fill Edges, user 2026-10-14: like
    DepthCrafter's and Inpainting's), with the seconds a step takes; the line ends at the last."""
    from core.common.cli_utils import draw_progress_bar
    rate = f"{per_step:.2f}s/it" if per_step else "?s/it"
    try:                                   # a console that can't show the bar never ends the fill
        draw_progress_bar(step, steps, bar_length=40, prefix=f"  ROSE window {chunk}/{n_chunks}:",
                          suffix=f"{step}/{steps} [{rate}]", keep_suffix=True)
    except (UnicodeError, OSError, ValueError):
        pass


def _run_worker(job_path: str, n_chunks: int, steps: int, progress: Progress, stop: threading.Event,
                lo: float, hi: float, bars: bool = False) -> None:
    """Run ROSE on the job's chunks. `bars`: each window's steps also as a console bar."""
    proc = subprocess.Popen([str(rose_env.python()), "-u", str(rose_env.WORKER), job_path],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace",
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    # ended with Studio, however Studio ends: left on after a close or a crash it held ~14 GB of the
    # card (user, 2026-10-15)
    from core.common.child_job import bind
    bind(proc)
    err_lines: List[str] = []

    def drain():                         # stderr: ROSE's warnings; kept for a failure's message
        for line in proc.stderr:
            err_lines.append(line.rstrip())
            del err_lines[:-40]
    drainer = threading.Thread(target=drain, daemon=True)
    drainer.start()

    def watch():                         # Stop ends the worker at once, not at its next line
        while proc.poll() is None:       # (bug sweep 2026-10-09, A1: during a model load or a long
            if stop.wait(0.25):          # step none came, Quit's 10 s join ran out, and the worker
                proc.kill()              # kept its VRAM)
                return
    threading.Thread(target=watch, name="rose-stop-watch", daemon=True).start()
    chunk, error = 0, ""
    bar = {"open": False, "t": None, "first": None}   # the window's bar: open, last step's time, its start
    try:
        for line in proc.stdout:
            if stop.is_set():
                raise Stopped()
            parts = line.split()
            if not parts:
                continue
            if parts[0] == "CHUNK":
                chunk = int(parts[1])
                progress(lo + (hi - lo) * (chunk - 1) / n_chunks, f"ROSE: window {chunk} of {n_chunks}, loading")
                if bars:
                    bar.update(open=True, t=time.monotonic(), first=None)
                    step_bar(chunk, n_chunks, 0, steps, None)
            elif parts[0] == "STEP":
                s = int(parts[1])
                progress(lo + (hi - lo) * ((chunk - 1) + s / steps) / n_chunks,
                         f"ROSE: window {chunk} of {n_chunks}, step {s} of {steps}")
                if bars:
                    now = time.monotonic()
                    # s/it from the first step's end on: the first carries the window's set-up
                    # (and the model load, in window 1)
                    if bar["first"] is None:
                        bar["first"] = (now, s)
                    t0, s0 = bar["first"]
                    # "?" until a second step: the first one's time is mostly the window's set-up
                    per = (now - t0) / (s - s0) if s > s0 else None
                    step_bar(chunk, n_chunks, s, steps, per)
                    bar["open"] = s < steps
            elif parts[0] == "DONE":
                logger.info(f"ROSE window {parts[1]} of {n_chunks} done ({' '.join(parts[2:])}).")
            elif parts[0] == "ERROR":
                error = line[len("ERROR"):].strip()
        if stop.is_set():
            raise Stopped()              # killed by the watch: a stop, not a failure
        if proc.wait() != 0 or error:
            drainer.join(timeout=5)          # its last lines are the traceback that matters
            tail = "\n".join(err_lines[-8:])
            raise RuntimeError(error or f"ROSE stopped with exit code {proc.returncode}.\n{tail}")
    finally:
        if bars and bar["open"]:               # cut short: the bar's line still ends
            sys.stdout.write("\n")
            sys.stdout.flush()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        # the pipes closed, not left to the garbage collector (the tests' ResourceWarnings;
        # bug sweep 2026-10-14)
        drainer.join(timeout=5)
        # stderr only once its reader is done with it (closing it under a read can block)
        for pipe in ((proc.stdout,) if drainer.is_alive() else (proc.stdout, proc.stderr)):
            try:
                pipe.close()
            except (OSError, ValueError):
                pass


def erase(clip: str, layers: Sequence[EraseLayer], progress: Progress, stop: threading.Event,
          steps: int = STEPS, seed: int = 42, work_area: int = WORK_AREA, window: int = WINDOW,
          grow: Optional[int] = None, feather: Optional[float] = None, guidance: float = GUIDANCE,
          prefill: str = "Auto", refine: bool = False) -> str:
    """Erase `layers` from `clip`, in place (the original kept in
    edit_history/). Returns where the original is. Raises Stopped.

    grow: px (at the clip's size) the mask reaches past the object, None =
    grow_px; feather: px of the blend back into the original, None = half of
    grow; window: frames per ROSE pass (16n+1); prefill: "Auto" / "On" / "Off" (PREFILL_AUTO_SHARE);
    refine: the full-size tiled second pass (REFINE_TILE)."""
    if not rose_env.ready():
        raise RuntimeError("ROSE is not set up: " + ", ".join(rose_env.missing()))
    src = source_for(clip)
    info = _probe(src)
    W, H = info["w"], info["h"]
    g = grow_px(W) if grow is None else max(0, int(grow))
    window = max(17, _len16(min(int(window), WINDOW)) if (int(window) - 1) % 16 else int(window))
    overlap = min(OVERLAP, (window - 1) // 2)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * g + 1, 2 * g + 1)) if g > 0 else None

    def mask(idx, tight=None):
        m = frame_mask(layers, idx, (W, H)) if tight is None else tight
        if m is None or kernel is None:
            return m
        return cv2.dilate(m.astype(np.uint8), kernel) > 0

    # 1. which frames have something to erase (the mask's own frame count is the clip's)
    progress(0.0, "Finding the frames to erase")
    n = info["frames"]
    flags = []
    for i in range(n):
        if stop.is_set():
            raise Stopped()
        flags.append(mask(i) is not None)
    if not any(flags):
        raise RuntimeError("No Erase layer has a mask on any frame. Track the object through the clip first.")
    windows = plan_windows(flags, window=window, overlap=overlap)
    ww, wh = work_size(W, H, work_area)
    logger.info(f"Erase: {sum(flags)} of {n} frames, {len(windows)} ROSE window(s) at {ww}x{wh}, {steps} steps.")

    sweep_stale_temp(clip)
    tmp = tempfile.mkdtemp(prefix=".erase_", dir=os.path.dirname(os.path.abspath(clip)))
    try:
        # 2. the windows' frames and masks at ROSE's size
        progress(0.02, "Reading the clip at ROSE's size")
        need = {}
        for k, (a, length) in enumerate(windows):
            for t in range(min(length, n - a)):
                need.setdefault(a + t, []).append((k, t))
        frames = {k: np.zeros((length, wh, ww, 3), np.uint8) for k, (a, length) in enumerate(windows)}
        masks = {k: np.zeros((length, wh, ww), np.uint8) for k, (a, length) in enumerate(windows)}
        for i, fr in enumerate(_reader(src, W, H, "rgb24", (ww, wh))):
            if stop.is_set():
                raise Stopped()
            hits = need.get(i, ())
            m = mask(i) if hits else None
            small = None if m is None else (cv2.resize(m.astype(np.uint8), (ww, wh), interpolation=cv2.INTER_AREA) > 0) * 255
            for k, t in hits:
                frames[k][t] = fr
                if small is not None:
                    masks[k][t] = small
        # past the clip's end: its last frame again -- with its mask. Unmasked, the padding showed ROSE the
        # object as something to keep, and it faded back in over the clip's last frames (a test clip, 2026-10-15:
        # 70 frames run as 73, the erase weakening from frame ~50 on).
        for k, (a, length) in enumerate(windows):
            for t in range(n - a, length):
                frames[k][t] = frames[k][n - 1 - a]
                masks[k][t] = masks[k][n - 1 - a]
        share = _mask_share(masks)
        use_prefill = prefill == "On" or (prefill == "Auto" and share >= PREFILL_AUTO_SHARE)
        if use_prefill:
            progress(0.04, f"Pre-filling the object ({100 * share:.0f}% of the frame)")
            for k in frames:
                frames[k] = prefill_frames(frames[k], masks[k] > 0)
        logger.info(f"Erase: the object covers {100 * share:.0f}% of the frame; pre-fill {'on' if use_prefill else 'off'}.")
        chunks = []
        for k in range(len(windows)):
            fp, mp, op = (os.path.join(tmp, f"w{k}_{x}.npy") for x in ("frames", "masks", "clean"))
            np.save(fp, frames[k]); np.save(mp, masks[k])
            chunks.append({"frames": fp, "masks": mp, "out": op})
        del frames, masks
        job = {"rose_dir": str(rose_env.ROSE_DIR), "embeds": str(rose_env.EMBEDS), "size": [wh, ww],
               "steps": int(steps), "seed": int(seed), "guidance": float(guidance), "chunks": chunks}
        job_path = os.path.join(tmp, "job.json")
        with open(job_path, "w", encoding="utf-8") as f:
            json.dump(job, f)

        # 3. ROSE
        _run_worker(job_path, len(chunks), int(steps), progress, stop, 0.05, 0.55 if refine else 0.9)
        cleaned = [np.load(c["out"], mmap_mode="r") for c in chunks]
        refined = None
        if refine:
            refined = _refine(src, W, H, n, windows, cleaned, mask, feather if feather is not None else g / 2.0,
                              tmp, steps, seed, guidance, progress, stop)

        # 4. back into the full-size frames, inside the mask
        progress(0.9, "Putting ROSE's result into the full-size frames")
        os.makedirs(history_dir(clip), exist_ok=True)
        stem, ext = os.path.splitext(os.path.basename(clip))
        out_tmp = os.path.join(tmp, f"{stem}.erased{ext or '.mp4'}")
        mask_tmp = os.path.join(tmp, f"{stem}.erase_mask.mkv")
        # the object's own outline too, softened a pixel: what Merging's
        # put-back lays back over the eyes (core.removal.putback, "Over")
        obj_tmp = os.path.join(tmp, f"{stem}.object_mask.mkv")
        writer = _writer(out_tmp, src, info)
        mwriter = _mask_writer(mask_tmp, W, H, info["rate"])
        owriter = _mask_writer(obj_tmp, W, H, info["rate"])
        feather = max(0.5, g / 2.0) if feather is None else max(0.5, float(feather))
        written = 0
        try:
            for i, orig in enumerate(_reader(src, W, H, "rgb48le")):
                if stop.is_set():
                    raise Stopped()
                tight = frame_mask(layers, i, (W, H))
                m = mask(i, tight) if tight is not None else None
                obj8 = np.zeros((H, W), np.uint8) if tight is None else np.clip(
                    cv2.GaussianBlur(tight.astype(np.float32), (0, 0), 1.0) * 255 + 0.5, 0, 255).astype(np.uint8)
                alpha8 = np.zeros((H, W), np.uint8)
                out = orig
                if m is not None:
                    acc, wsum = None, 0.0
                    for k, (a, length) in enumerate(windows):
                        t = i - a
                        if 0 <= t < length:
                            # cross-fade where windows overlap: weight falls off near a window's ends
                            wgt = float(min(t + 1, length - t, overlap)) / max(overlap, 1) if len(windows) > 1 else 1.0
                            f = cleaned[k][t].astype(np.float32) * wgt
                            acc = f if acc is None else acc + f
                            wsum += wgt
                    if refined is not None and acc is not None and wsum > 0:
                        # the full-size pass: its tiles' detail on the first pass's tone
                        acc, wsum = refined.fill(i, cv2.resize(acc / wsum, (W, H), interpolation=cv2.INTER_CUBIC)), 1.0
                    if acc is not None and wsum > 0:
                        # divided by the windows' weights at any size: a clip already at the work size
                        # (1280x720 at 720p) skipped it, and a window's ends came out dark (review 2026-10-17)
                        clean = acc / wsum
                        if clean.shape[:2] != (H, W):
                            clean = cv2.resize(clean, (W, H), interpolation=cv2.INTER_CUBIC)
                        clean = clean * 257.0
                        alpha = cv2.GaussianBlur(m.astype(np.float32), (0, 0), feather)[..., None]
                        out = np.clip(orig.astype(np.float32) * (1 - alpha) + clean * alpha, 0, 65535).astype(np.uint16)
                        alpha8 = (alpha[..., 0] * 255 + 0.5).astype(np.uint8)
                try:
                    writer.stdin.write(np.ascontiguousarray(out).tobytes())
                    mwriter.stdin.write(alpha8.tobytes())
                    owriter.stdin.write(obj8.tobytes())
                except OSError:
                    break                    # an encoder exited: its reason is read below
                written += 1
                if written % 8 == 0:
                    progress(0.9 + 0.1 * written / max(n, 1), f"Writing the clip ({written} of {n})")
        finally:
            for p in (writer, mwriter, owriter):
                try:
                    p.stdin.close()
                except OSError:
                    pass
            for p in (writer, mwriter, owriter):
                p.wait()
        failed = writers_failed((writer, mwriter, owriter), written, n, "Writing the erased clip")
        if failed:
            raise RuntimeError(failed)

        # 5. in the original's place; the original and the mask kept
        original = place(clip, src, out_tmp, mask_tmp, obj_tmp,
                         {"method": "rose", "frames": n, "windows": windows, "work_size": [ww, wh],
                          "steps": int(steps), "seed": int(seed), "grow": g, "feather": feather,
                          "prefill": bool(use_prefill), "refine": bool(refine)})
        progress(1.0, "Erased")
        return original
    finally:
        # ROSE's output is read memory-mapped: open, Windows won't delete it, and the rmtree (errors
        # ignored) left it -- ~200 MB a window (user's erase, 2026-10-15)
        cleaned = refined = None
        import gc
        gc.collect()
        shutil.rmtree(tmp, ignore_errors=True)


# ---- pre-fill and the full-size pass (user, 2026-10-15) -------------------------------------

def _device():
    """CUDA when there is one: Studio's video work runs on the GPU, the CPU only without it."""
    import torch
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def _gauss(x, sigma: float, scale: int = 1):
    """A Gaussian blur of (N, C, h, w) by `sigma` px; `scale` > 1: done at 1/scale size (a wide blur
    is the same there, on far fewer pixels) and brought back up."""
    import torch
    import torch.nn.functional as F
    h, w = x.shape[-2:]
    y = F.interpolate(x, size=(max(1, h // scale), max(1, w // scale)), mode="area") if scale > 1 else x
    s = sigma / scale
    r = max(1, int(round(3 * s)))
    k = torch.arange(-r, r + 1, device=x.device, dtype=x.dtype)
    g = torch.exp(-(k * k) / (2 * s * s))
    g = g / g.sum()
    c = y.shape[1]
    y = F.conv2d(F.pad(y, (r, r, 0, 0), mode="replicate"), g.view(1, 1, 1, -1).repeat(c, 1, 1, 1), groups=c)
    y = F.conv2d(F.pad(y, (0, 0, r, r), mode="replicate"), g.view(1, 1, -1, 1).repeat(c, 1, 1, 1), groups=c)
    return F.interpolate(y, size=(h, w), mode="bilinear", align_corners=False) if scale > 1 else y


def _mask_share(masks: Dict[int, np.ndarray]) -> float:
    """How much of the frame the object covers, on the frames it's on (0..1)."""
    shares = [float((m > 0).mean()) for arr in masks.values() for m in arr if (m > 0).any()]
    return float(np.mean(shares)) if shares else 0.0


def prefill_frames(frames: np.ndarray, holes: np.ndarray, batch: int = 16) -> np.ndarray:
    """(N, h, w, 3) uint8 with each hole filled from around it, smoothly, with no trace of the object's
    outline inside (a coarse-to-fine fill drew a person's silhouette in rings, and ROSE redrew him): one
    wide normalised convolution (sigma 96 at 1296 px wide, scaled to the frame), softened again inside."""
    import torch
    dev = _device()
    out = frames.copy()
    s1 = 96.0 * frames.shape[2] / 1296.0
    with torch.no_grad():
        for a in range(0, len(frames), batch):
            img = torch.from_numpy(frames[a:a + batch]).to(dev).permute(0, 3, 1, 2).float()
            hole = torch.from_numpy(holes[a:a + batch]).to(dev)[:, None].float()
            known = 1 - hole
            est = _gauss(img * known, s1, 8) / _gauss(known, s1, 8).clamp_min(1e-4)
            fill = img * known + est * hole
            fill = img * known + _gauss(fill, s1 / 4, 4) * hole
            out[a:a + batch] = fill.clamp(0, 255).round().byte().permute(0, 2, 3, 1).cpu().numpy()
    if dev.type == "cuda":
        torch.cuda.empty_cache()            # gone before ROSE's worker needs the card
    return out


def refine_tiles(w: int, h: int) -> List[Tuple[int, int, int, int]]:
    """The full-size pass's tiles over a w x h frame: (y0, y1, x0, x1), at most REFINE_TILE, sides
    multiples of 16, overlapping at least REFINE_OVERLAP."""
    from .edge_fill import tile_rows
    tw = max(16, min(REFINE_TILE[0], w) // 16 * 16)
    th = max(16, min(REFINE_TILE[1], h) // 16 * 16)
    return [(ya, yb, xa, xb) for ya, yb in tile_rows(h, th, REFINE_OVERLAP) for xa, xb in tile_rows(w, tw, REFINE_OVERLAP)]


class _Refined:
    """The full-size pass's result, per window, for the paste: each frame's tiles faded together and set
    on the first pass's tone (REFINE_TONE)."""

    def __init__(self, windows, overlap, tiles, ys, xs, wy, wx, first, outs, W, H):
        self.windows, self.overlap, self.tiles = windows, overlap, tiles
        self.ys, self.xs, self.wy, self.wx = ys, xs, wy, wx
        self.first, self.outs, self.W, self.H = first, outs, W, H

    def fill(self, i: int, coarse: np.ndarray) -> np.ndarray:
        """Frame i's fill (H, W, 3) float 0..255: the tiles' detail on the first pass's tone; where no
        tile reached, `coarse` (the first pass, as before)."""
        import torch
        dev = _device()
        acc = np.zeros((self.H, self.W, 3), np.float32)
        wsum = np.zeros((self.H, self.W, 1), np.float32)
        base_acc = np.zeros((self.H, self.W, 3), np.float32)
        base_w = 0.0
        for k, (a, length) in enumerate(self.windows):
            t = i - a
            if not 0 <= t < length:
                continue
            wk = float(min(t + 1, length - t, self.overlap)) / max(self.overlap, 1) if len(self.windows) > 1 else 1.0
            base_acc += self.first[k][t].astype(np.float32) * wk
            base_w += wk
            for ti, (ya, yb, xa, xb) in enumerate(self.tiles):
                out = self.outs.get((k, ti))
                if out is None:
                    continue
                wt = (self.wy[self.ys.index((ya, yb)), ya:yb][:, None]
                      * self.wx[self.xs.index((xa, xb)), xa:xb][None, :])[..., None] * wk
                acc[ya:yb, xa:xb] += out[t].astype(np.float32) * wt
                wsum[ya:yb, xa:xb] += wt
        if base_w <= 0:
            return coarse
        first = base_acc / base_w
        tiled = np.where(wsum > 1e-6, acc / np.maximum(wsum, 1e-6), first)
        with torch.no_grad():
            T = lambda x: torch.from_numpy(np.ascontiguousarray(x)).to(dev).permute(2, 0, 1)[None]
            ft, tt = T(first), T(tiled)
            out = _gauss(ft, REFINE_TONE, 4) + (tt - _gauss(tt, REFINE_TONE, 4))
            return out[0].permute(1, 2, 0).clamp(0, 255).cpu().numpy()


def _refine(src, W, H, n, windows, cleaned, mask, feather, tmp, steps, seed, guidance, progress, stop):
    """The full-size pass: per window the first pass at the clip's size (pasted into the clip inside the
    feathered mask -- what ROSE then redraws sharper), cut into the tiles that have something to erase;
    ROSE on them (one model load)."""
    import torch
    from .edge_fill import tile_weights, tile_rows
    dev = _device()
    tiles = refine_tiles(W, H)
    tw = max(16, min(REFINE_TILE[0], W) // 16 * 16)
    th = max(16, min(REFINE_TILE[1], H) // 16 * 16)
    ys, xs = tile_rows(H, th, REFINE_OVERLAP), tile_rows(W, tw, REFINE_OVERLAP)
    wy, wx = tile_weights(ys, H), tile_weights(xs, W)
    first, chunks, owner = {}, [], []
    progress(0.56, "Building the first pass at full size")
    # one read of the clip: each frame into every window it's in (frames and holes on disk, not memory)
    arrs, holes = {}, {}
    for k, (a, length) in enumerate(windows):
        arrs[k] = np.lib.format.open_memmap(os.path.join(tmp, f"w{k}_first.npy"), mode="w+", dtype=np.uint8,
                                            shape=(length, H, W, 3))
        holes[k] = np.lib.format.open_memmap(os.path.join(tmp, f"w{k}_holes.npy"), mode="w+", dtype=np.bool_,
                                             shape=(length, H, W))
    for i, orig in enumerate(_reader(src, W, H, "rgb24")):
        if stop.is_set():
            raise Stopped()
        if i >= n:
            break
        m = mask(i)
        for k, (a, length) in enumerate(windows):
            t = i - a
            if not 0 <= t < length:
                continue
            if m is None:
                arrs[k][t] = orig
                continue
            holes[k][t] = m
            with torch.no_grad():
                up = torch.nn.functional.interpolate(
                    torch.from_numpy(np.ascontiguousarray(cleaned[k][t])).to(dev).permute(2, 0, 1)[None].float(),
                    size=(H, W), mode="bicubic", align_corners=False)
                al = _gauss(torch.from_numpy(m).to(dev)[None, None].float(), max(0.5, float(feather)))
                o = torch.from_numpy(orig).to(dev).permute(2, 0, 1)[None].float()
                arrs[k][t] = (o * (1 - al) + up * al).clamp(0, 255).round().byte()[0].permute(1, 2, 0).cpu().numpy()
    for k, (a, length) in enumerate(windows):
        last = max(0, min(n, a + length) - a - 1)
        for t in range(last + 1, length):                 # past the clip's end: its last frame
            arrs[k][t] = arrs[k][last]
            holes[k][t] = holes[k][last]
        arrs[k].flush()
        holes[k].flush()
        first[k] = arrs[k]
        union = np.zeros((H, W), bool)
        for t in range(length):
            union |= holes[k][t]
        for ti, (ya, yb, xa, xb) in enumerate(tiles):
            if not union[ya:yb, xa:xb].any():
                continue                                   # nothing to erase here: left as the first pass
            fp, mp, op = (os.path.join(tmp, f"r{k}t{ti}_{x}.npy") for x in ("frames", "masks", "clean"))
            np.save(fp, np.ascontiguousarray(first[k][:, ya:yb, xa:xb]))
            np.save(mp, (np.asarray(holes[k][:, ya:yb, xa:xb]) * 255).astype(np.uint8))
            chunks.append({"frames": fp, "masks": mp, "out": op})
            owner.append((k, ti))
    del holes
    if dev.type == "cuda":
        torch.cuda.empty_cache()
    logger.info(f"Erase: refining at full size, {len(chunks)} tile pass(es) of up to {tw}x{th}.")
    outs = {}
    if chunks:
        job = {"rose_dir": str(rose_env.ROSE_DIR), "embeds": str(rose_env.EMBEDS), "size": [th, tw],
               "steps": int(steps), "seed": int(seed), "guidance": float(guidance), "chunks": chunks}
        jp = os.path.join(tmp, "refine_job.json")
        with open(jp, "w", encoding="utf-8") as f:
            json.dump(job, f)
        _run_worker(jp, len(chunks), int(steps), progress, stop, 0.6, 0.9)
        outs = {o: np.load(c["out"], mmap_mode="r") for o, c in zip(owner, chunks)}
    return _Refined(windows, OVERLAP, tiles, ys, xs, wy, wx, first, outs, W, H)


def place(clip: str, src: str, out_tmp: str, mask_tmp: str, obj_tmp: str, record: Dict) -> str:
    """An erase's output in the clip's place: the clip it replaces kept in
    edit_history (unless it was already an erase of the kept original), the
    repaint and object masks beside it, and the record. Returns the original."""
    stem = os.path.splitext(os.path.basename(clip))[0]
    os.makedirs(history_dir(clip), exist_ok=True)
    # Merging's put-back keeps readers on the clip and its masks (bug sweep 2026-10-09 #11)
    from . import putback
    putback.close_all()
    try:
        if src == clip:
            original = _backup_path(clip)
            os.replace(clip, original)
        else:
            original = src
        try:
            os.replace(out_tmp, clip)
        except OSError:
            if src == clip:
                os.replace(original, clip)    # the clip back as it was
            raise
    except OSError as e:
        # held open elsewhere: the finished erase was thrown away with the temp
        # folder (bug sweep 2026-10-09 #11); keep it, and say where
        kept = os.path.join(history_dir(clip), os.path.basename(out_tmp))
        shutil.move(out_tmp, kept)
        raise RuntimeError(f"{os.path.basename(clip)} is open elsewhere, so the erase couldn't replace it "
                           f"({e.strerror or e}). The clip is unchanged; the erased version is kept as {kept}. "
                           f"Close the clip in the other stage and erase again.") from e
    for t, name in ((mask_tmp, "erase_mask"), (obj_tmp, "object_mask")):
        dest = os.path.join(history_dir(clip), f"{stem}.{name}.mkv")
        if os.path.exists(dest):
            os.remove(dest)
        shutil.move(t, dest)
    with open(record_path(clip), "w", encoding="utf-8") as f:
        json.dump({"live": _file_id(clip), "original": original, **record}, f, indent=1)
    return original
