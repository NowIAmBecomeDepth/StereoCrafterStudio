"""A clip with no depth to give it -- end credits, a title card -- as a flat 2D Full SBS:
the same picture in both eyes, written straight into the Merged folder under Merging's own
name (``<clip>_<width>_merged_full_sbs``) with Merging's Encoding Settings, so it sits
there as finished as any merged clip (user, 2026-11-08).

One ffmpeg run: the frame split and set side by side, no depth, no splat, no inpaint.
"""
import json
import logging
import os
import subprocess
from typing import Callable, Dict, Optional

from core.common.encoding_utils import video_args_from_settings
from core.common.partial_output import discard, publish

logger = logging.getLogger(__name__)

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

#: Merging's Full SBS suffix (stages/merging/engine.py: "Full SBS (Left-Right)")
SUFFIX = "_merged_full_sbs"


def probe(path: str) -> Dict:
    """The first video stream: w, h, pix_fmt, frames (0 if unknown), its colour tags."""
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=width,height,pix_fmt,nb_frames,r_frame_rate,color_primaries,color_transfer,"
                        "color_space,color_range:format=duration", "-of", "json", path],
                       capture_output=True, text=True, creationflags=_NO_WINDOW)
    data = json.loads(r.stdout or "{}") if r.returncode == 0 else {}
    streams = data.get("streams") or []
    if not streams:
        raise ValueError(f"No video stream in {path}")
    s = streams[0]
    try:
        frames = int(s.get("nb_frames") or 0)
    except ValueError:
        frames = 0
    if frames <= 0:                     # MKV keeps no count: from the duration
        try:
            num, _, den = str(s.get("r_frame_rate") or "24/1").partition("/")
            frames = int(round(float((data.get("format") or {}).get("duration") or 0) * float(num) / float(den or 1)))
        except (ValueError, ZeroDivisionError):
            frames = 0
    tags = {k: str(s.get(k) or "") for k in ("color_primaries", "color_transfer", "color_space", "color_range")}
    return {"w": int(s.get("width") or 0), "h": int(s.get("height") or 0), "pix_fmt": str(s.get("pix_fmt") or ""),
            "frames": frames, **tags}


def output_path(src: str, folder: str, settings: Dict, width: int) -> str:
    """Where Merging would write `src`'s Full SBS in `folder`."""
    from core.common.encoding_utils import output_ext
    stem = os.path.splitext(os.path.basename(src))[0]
    return os.path.join(folder, f"{stem}_{width}{SUFFIX}{output_ext(settings)}")


def _color_args(info: Dict) -> list:
    """The source's own colour tags, carried over (a 2D copy is the source's picture)."""
    out = []
    for key, flag in (("color_primaries", "-color_primaries"), ("color_transfer", "-color_trc"),
                      ("color_space", "-colorspace"), ("color_range", "-color_range")):
        v = info.get(key, "")
        if v and v not in ("unknown", "reserved"):
            out += [flag, v]
    return out


def render(src: str, folder: str, settings: Dict, stop: Optional[Callable[[], bool]] = None,
           progress: Optional[Callable[[int, int], None]] = None) -> str:
    """Write `src` as a flat Full SBS into `folder`; the finished path. Raises on failure;
    a stop (stop() true) leaves nothing behind and raises InterruptedError."""
    info = probe(src)
    if info["w"] <= 0 or info["h"] <= 0:
        raise ValueError(f"Couldn't read the size of {src}")
    s = dict(settings or {})
    if (s.get("bit_depth") or "Auto") == "Auto" and not s.get("dnxhr_fullres_split"):
        # 10-bit stays 10-bit (user rule): Auto follows the source
        s["bit_depth"] = "10-bit (Main10)" if any(b in info["pix_fmt"] for b in ("10", "12", "16")) else "8-bit"
    vargs, _ext = video_args_from_settings(s)
    os.makedirs(folder, exist_ok=True)
    final = output_path(src, folder, s, info["w"])
    stem, ext = os.path.splitext(final)
    part = f"{stem}.partial{ext}"       # ffmpeg picks its muxer from the extension (as Merging writes)
    discard(part)
    cmd = ["ffmpeg", "-y", "-v", "error", "-nostdin", "-nostats", "-progress", "pipe:1", "-i", src,
           "-map", "0:v:0", "-an", "-sn", "-vf", "split[l][r];[l][r]hstack,setsar=1",
           *vargs, *_color_args(info), part]
    logger.info("2D Full SBS: %s -> %s", os.path.basename(src), final)
    logger.debug("ffmpeg: %s", " ".join(cmd))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            creationflags=_NO_WINDOW)
    err = []
    import threading
    t = threading.Thread(target=lambda: err.extend(proc.stderr.read().splitlines()), daemon=True)
    t.start()
    stopped = False
    try:
        for line in proc.stdout:
            if stop is not None and stop():
                stopped = True
                proc.kill()
                break
            if line.startswith("frame=") and progress is not None:
                try:
                    progress(int(line.split("=", 1)[1]), info["frames"])
                except ValueError:
                    pass
        proc.wait()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        t.join(5)
        for f in (proc.stdout, proc.stderr):
            f.close()
    if stopped:
        discard(part)
        raise InterruptedError("Stopped")
    if proc.returncode != 0 or not os.path.isfile(part):
        discard(part)
        raise RuntimeError("ffmpeg failed: " + ("; ".join(err[-4:]) or f"exit {proc.returncode}"))
    if not publish(part, final):
        discard(part)
        raise RuntimeError(f"Couldn't finish {final}")
    if progress is not None and info["frames"]:
        progress(info["frames"], info["frames"])
    return final
