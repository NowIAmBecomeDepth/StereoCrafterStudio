"""autocrop.py -- Scene Cut's Auto Crop (user, 2026-10-11): each shot cut to its
own picture, for movies whose aspect ratio switches (IMAX sequences at 1.90:1
inside a 2.39:1 film, a 4:3 flashback in a 16:9 show).

Studio's own module (not one of the upstream-mirrored files beside it).

1. One pass over the movie, keyframes only (``-skip_frame nokey``: no full
   decode), through ffmpeg's ``cropdetect``: every keyframe's picture box,
   with its time.
2. The movie's own bar sizes: the boxes many keyframes share. A film switches
   between a few (two, for an IMAX film); a dark frame's box is a one-off.
3. Each shot gets the smallest of those boxes that holds every box seen in it,
   so a crop never cuts picture away; none holds it -> the full frame. A shot
   with no keyframe in it is read on its own (three frames).
4. The split crops on the GPU where it can: NVDEC's own decoder crop
   (cuvid_decoder), bit-identical to the crop filter; else the CPU crop filter.

Crops come back as (w, h, x, y), even numbers (4:2:0 video), keyed by the
shot's start frame; a shot left whole is absent.
"""

from __future__ import annotations

import logging
import re
import subprocess
import sys
from bisect import bisect_right
from typing import Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Below this a pixel counts as black bar (cropdetect's limit, as a fraction:
#: 24 of 255 -- the same for 8- and 10-bit sources).
LIMIT = 24 / 255
#: Boxes within this many pixels on every edge are the same bar size.
TOL = 8
#: A crop smaller than this share of the frame on both axes isn't worth making.
MIN_CUT = 0.01
#: A box this small is a black (or nearly black) frame, not a picture.
MIN_BOX = 0.30

_LINE = re.compile(r"\bt:(-?[\d.]+).*?crop=(\d+):(\d+):(\d+):(\d+)")
_NOWIN = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

Box = Tuple[int, int, int, int]  # x1, y1, x2, y2 (x2 / y2 exclusive)


def _parse(text: str, frame_w: int, frame_h: int) -> List[Tuple[float, Box]]:
    out = []
    for m in _LINE.finditer(text):
        t = float(m.group(1))
        w, h, x, y = (int(v) for v in m.groups()[1:])
        if w < MIN_BOX * frame_w or h < MIN_BOX * frame_h:
            continue                       # a black frame: no picture to measure
        out.append((t, (x, y, x + w, y + h)))
    return out


def _run(cmd: List[str], abort=None) -> str:
    """ffmpeg's stderr (cropdetect logs there); stopped early when `abort` is set."""
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                            errors="replace", creationflags=_NOWIN)
    lines = []
    with proc.stderr:
        for line in proc.stderr:
            lines.append(line)
            if abort is not None and abort.is_set():
                proc.kill()
                break
    proc.wait()
    if proc.returncode and not (abort is not None and abort.is_set()):
        # a file ffmpeg couldn't read looked like "no bars" (bug sweep 2026-10-14c): said
        tail = " | ".join(l.strip() for l in lines[-3:] if l.strip())
        logger.warning("Auto Crop: ffmpeg stopped with code %s reading %s: %s", proc.returncode,
                       cmd[cmd.index("-i") + 1] if "-i" in cmd else "?", tail or "no message")
    return "".join(lines)


def keyframe_boxes(video_path: str, frame_w: int, frame_h: int, abort=None) -> List[Tuple[float, Box]]:
    """Every keyframe's picture box: [(time in s, box)], in time order."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-skip_frame", "nokey", "-i", video_path, "-an", "-sn",
           "-vf", f"cropdetect=limit={LIMIT:.4f}:round=2:reset=1:skip=0", "-f", "null", "-"]
    return _parse(_run(cmd, abort), frame_w, frame_h)


def shot_boxes(video_path: str, start_s: float, end_s: float, frame_w: int, frame_h: int) -> List[Box]:
    """Three frames spread through a shot (for one no keyframe fell in)."""
    out = []
    span = max(0.0, end_s - start_s)
    for k in (0.2, 0.5, 0.8):
        t = start_s + span * k
        cmd = ["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{t:.3f}", "-i", video_path, "-an", "-sn",
               "-frames:v", "1", "-vf", f"cropdetect=limit={LIMIT:.4f}:round=2:reset=1:skip=0", "-f", "null", "-"]
        out += [b for _t, b in _parse(_run(cmd), frame_w, frame_h)]
    return out


def bar_sizes(boxes: Sequence[Box], min_count: int, frame_w: int, frame_h: int) -> List[Box]:
    """The movie's own bar sizes: boxes at least `min_count` keyframes share
    (within TOL), and centred -- real letterbox / pillarbox bars are the same
    top and bottom, left and right; a dark scene's box is lopsided. Each is the
    widest of its members' edges, so it holds every one of them."""
    clusters: List[List[Box]] = []
    for b in boxes:
        for c in clusters:
            ref = c[0]
            if all(abs(b[i] - ref[i]) <= TOL for i in range(4)):
                c.append(b)
                break
        else:
            clusters.append([b])
    out = []
    for c in clusters:
        if len(c) < min_count:
            continue
        box = (min(b[0] for b in c), min(b[1] for b in c), max(b[2] for b in c), max(b[3] for b in c))
        if abs(box[0] - (frame_w - box[2])) > 2 * TOL or abs(box[1] - (frame_h - box[3])) > 2 * TOL:
            continue                       # lopsided: not bars
        out.append(box)
    return out


def _holds(outer: Box, inner: Box) -> bool:
    return (outer[0] <= inner[0] + TOL and outer[1] <= inner[1] + TOL
            and outer[2] >= inner[2] - TOL and outer[3] >= inner[3] - TOL)


def _area(b: Box) -> int:
    return (b[2] - b[0]) * (b[3] - b[1])


def choose(seen: Sequence[Box], sizes: Sequence[Box], frame_w: int, frame_h: int) -> Optional[Tuple[int, int, int, int]]:
    """A shot's crop (w, h, x, y) from the boxes seen in it, or None (whole)."""
    if not seen:
        return None
    union = (min(b[0] for b in seen), min(b[1] for b in seen), max(b[2] for b in seen), max(b[3] for b in seen))
    fits = [s for s in sizes if _holds(s, union)]
    if not fits:
        return None
    size = min(fits, key=_area)
    # the outer edges of the bar size and what this shot showed, out to even: a size
    # a few pixels tighter than the shot never trims its picture
    x1, y1 = min(size[0], union[0]), min(size[1], union[1])
    x2, y2 = max(size[2], union[2]), max(size[3], union[3])
    x1, y1 = x1 - x1 % 2, y1 - y1 % 2
    x2, y2 = min(frame_w, x2 + x2 % 2), min(frame_h, y2 + y2 % 2)
    w, h = x2 - x1, y2 - y1
    w, h = w - w % 2, h - h % 2            # an odd frame's edge: back in to even (sweep 2026-10-14c)
    if w <= 0 or h <= 0:
        return None
    if (frame_w - w) < MIN_CUT * frame_w and (frame_h - h) < MIN_CUT * frame_h:
        return None                        # (nearly) the whole frame
    return w, h, x1, y1


def frame_size(video_path: str) -> Tuple[int, int]:
    """The video's coded width and height (ffprobe); (0, 0) when unreadable."""
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                              "stream=width,height", "-of", "csv=p=0:s=x", video_path],
                             capture_output=True, text=True, timeout=30, creationflags=_NOWIN).stdout
        w, h = out.strip().splitlines()[0].split("x")[:2]
        return int(w), int(h)
    except Exception:
        return 0, 0


#: ffprobe's codec names whose NVDEC decoder is named differently
_CUVID_NAMES = {"mpeg2video": "mpeg2_cuvid", "mpeg1video": "mpeg1_cuvid"}


def cuvid_decoder(video_path: str) -> Optional[str]:
    """The NVDEC decoder that can crop this source as it decodes ("h264_cuvid"), so
    a cropped shot stays on the GPU end to end -- proven on one frame through the
    split's own chain (crop, scale_cuda, NVENC); None when there isn't one."""
    try:
        codec = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                "stream=codec_name", "-of", "csv=p=0", video_path],
                               capture_output=True, text=True, timeout=30, creationflags=_NOWIN).stdout.strip()
        if not codec:
            return None
        decoder = _CUVID_NAMES.get(codec, f"{codec}_cuvid")
        ok = subprocess.run(["ffmpeg", "-v", "error", "-hide_banner", "-hwaccel", "cuda", "-hwaccel_output_format",
                             "cuda", "-c:v", decoder, "-crop", "2x2x2x2", "-i", video_path, "-frames:v", "1",
                             "-filter_complex", "[0:v]scale_cuda=format=p010le[v]", "-map", "[v]",
                             "-c:v", "hevc_nvenc", "-f", "null", "-"],
                            capture_output=True, text=True, timeout=60, creationflags=_NOWIN)
        return decoder if ok.returncode == 0 else None
    except Exception:
        return None


def shot_crops(video_path: str, spans: Sequence[Tuple[int, int]], fps_num: int, fps_den: int,
               start_time: float, abort=None,
               status: Optional[Callable[[str], None]] = None) -> Dict[int, Tuple[int, int, int, int]]:
    """{shot start frame: (w, h, x, y)} for the shots [(start, end frame)) to crop."""
    frame_w, frame_h = frame_size(video_path)
    if not spans or frame_w <= 0 or frame_h <= 0 or not fps_num or not fps_den:
        return {}
    if status:
        status("Auto Crop: reading each shot's picture...")
    samples = keyframe_boxes(video_path, frame_w, frame_h, abort)
    if abort is not None and abort.is_set():
        return {}
    rate = fps_num / fps_den
    if not samples:
        # a container with no keyframe flags: the pass saw nothing and every shot was left whole,
        # silently (bug sweep 2026-10-14c). Each shot read on its own instead (three frames).
        logger.warning("Auto Crop: no keyframes found in %s; reading each shot on its own (slower).",
                       video_path)
        for s, e in spans:
            if abort is not None and abort.is_set():
                return {}
            for b in shot_boxes(video_path, start_time + s / rate, start_time + e / rate, frame_w, frame_h):
                samples.append((start_time + s / rate, b))
    times = [t - start_time for t, _b in samples]
    min_count = max(3, len(samples) // 100)
    sizes = bar_sizes([b for _t, b in samples], min_count, frame_w, frame_h)
    starts = [s / rate for s, _e in spans]
    seen: Dict[int, List[Box]] = {}
    for t, (_t0, b) in zip(times, samples):
        i = bisect_right(starts, t) - 1
        if 0 <= i < len(spans) and t < spans[i][1] / rate:
            seen.setdefault(i, []).append(b)
    crops = {}
    for i, (s, e) in enumerate(spans):
        if abort is not None and abort.is_set():
            break
        boxes = seen.get(i)
        if not boxes:
            boxes = shot_boxes(video_path, start_time + s / rate, start_time + e / rate, frame_w, frame_h)
        crop = choose(boxes, sizes, frame_w, frame_h)
        if crop:
            crops[s] = crop
    logger.info("Auto Crop: %d of %d shot(s) cropped; the movie's picture sizes: %s.", len(crops), len(spans),
                ", ".join(f"{b[2] - b[0]}x{b[3] - b[1]}" for b in sizes) or "none found")
    return crops
