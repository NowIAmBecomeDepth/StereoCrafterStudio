"""depth_io.py -- read and write depth videos as exact 10-bit code values.

Why code values and not a normalised float
------------------------------------------
DepthCrafter writes 10-bit HEVC in limited ("tv") range: its 0..1 depth lands
on Y codes 64..940. The obvious route -- decode to gray16, edit, encode from
gray16 -- runs ffmpeg's range conversion twice, once each way, and every
round trip squeezes the map a little further towards grey. An editor that
changes one object must hand back every other pixel untouched.

So both directions stay in YUV: frames are decoded to yuv420p10le and only the
Y plane is kept, edits happen in a float view of those codes (``to_unit`` /
``from_unit``, which know the range), and the writer feeds yuv420p10le back
with neutral chroma. ffmpeg never converts a range, so an unedited pixel
leaves with the code it arrived with.

The output is the same format DepthCrafter writes (depthcrafter/utils.py
save_video, main10_mp4) -- HEVC Main10, bt709 tags, hvc1 in MP4, NVENC with a
libx265 fallback -- so Splatting reads it exactly as it reads a DepthCrafter
map. One difference, on purpose: it is truly LOSSLESS (NVENC -tune lossless,
x265 lossless=1), not constant QP 0. Measured on a real 768x448 map, QP/CRF 0
moves pixels by up to 8 codes and 0.1-0.2 on average, which is the encoder
editing parts of the map the user never touched. Lossless costs about six
times the file size -- ~30 KB a frame at DepthCrafter's resolution.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import copy
from dataclasses import dataclass
from typing import Callable, Iterator, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

#: Every depth frame is decoded to this, whatever it was stored as.
CODE_BITS = 10
CODE_MAX = (1 << CODE_BITS) - 1
#: Limited-range luma at 10 bits: 16..235 scaled by 4.
TV_LO, TV_HI = 64, 940

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class ClipInfo:
    path: str
    width: int
    height: int
    num_frames: int
    fps: str  # ffmpeg rational, e.g. "24000/1001" -- kept exact for the writer
    pix_fmt: str
    full_range: bool
    #: The file's own (width, height) when the frames are read enlarged to
    #: width x height (at_size); None: read as they are.
    file_size: Optional[Tuple[int, int]] = None

    @property
    def fps_float(self) -> float:
        try:
            num, _, den = self.fps.partition("/")
            return float(num) / float(den or 1)
        except (ValueError, ZeroDivisionError):
            return 24.0

    @property
    def code_range(self) -> tuple:
        """(lo, hi) Y codes that mean 0.0 and 1.0 for this clip."""
        return (0, CODE_MAX) if self.full_range else (TV_LO, TV_HI)


def probe(path: str, count: bool = True) -> ClipInfo:
    """Size, frame count, rate and range of the first video stream.

    nb_frames is read from the container when it is there and counted by
    decoding when it is not (MKV leaves it out); a wrong count would make the
    writer stop short or the reader block waiting for frames that never come.
    ``count=False`` skips that decode (num_frames is 0 then): for callers that
    only want the size -- the decode took 15 s on a 17 s 1080p HEVC 10-bit
    MKV, and the editor asked for the size on the UI thread (2026-10-11).
    """
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,pix_fmt,color_range,nb_frames,r_frame_rate",
        "-of", "json", path,
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True, creationflags=_NO_WINDOW)
    streams = json.loads(out.stdout).get("streams") or []
    if not streams:
        raise ValueError(f"No video stream in {path}")
    s = streams[0]
    try:
        n = int(s.get("nb_frames") or 0)
    except ValueError:
        n = 0
    if n <= 0 and count:
        n = _count(path, "frames")
    pix_fmt = str(s.get("pix_fmt", ""))
    # Only a YUV stream carries a meaningful range. Gray and RGB inputs come
    # through ffmpeg's own conversion to yuv420p10le, which lands in tv range.
    full = pix_fmt.startswith("yuvj") or (
        pix_fmt.startswith("yuv") and str(s.get("color_range", "")).lower() in ("pc", "jpeg"))
    return ClipInfo(
        path=path,
        width=int(s["width"]),
        height=int(s["height"]),
        num_frames=n,
        fps=str(s.get("r_frame_rate") or "24/1"),
        pix_fmt=pix_fmt,
        full_range=full,
    )


def _count(path: str, what: str) -> int:
    """ffprobe's count of the first video stream's "frames" (decodes the
    stream) or "packets" (reads it only)."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", f"-count_{what}",
         "-show_entries", f"stream=nb_read_{what}", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True, creationflags=_NO_WINDOW,
    )
    return int((out.stdout.strip().splitlines() or ["0"])[0] or 0)


def count_frames(path: str, expect: int) -> int:
    """The frame count of ``path``, checked against ``expect``: the
    container's own count when it has one, else its packets (one per frame
    for nearly every video -- milliseconds, no decode), and only when those
    disagree with ``expect`` the decoded count, so a reported mismatch is real."""
    info = probe(path, count=False)
    if info.num_frames > 0:
        return info.num_frames
    n = _count(path, "packets")
    return n if n == expect else _count(path, "frames")


def at_size(info: ClipInfo, size: Optional[Tuple[int, int]]) -> ClipInfo:
    """`info` read at `size` (w, h): every frame enlarged as it is decoded,
    bilinear -- as Splatting enlarges a depth map, so what the editor leaves
    alone splats as it did (user, 2026-10-05: edit the map at the clip's own
    resolution). Its size as it is, or None: `info` itself."""
    native = info.file_size or (info.width, info.height)
    if not size or (tuple(size) == tuple(native) and info.file_size is None):
        return info
    out = copy.copy(info)
    out.width, out.height = int(size[0]), int(size[1])
    out.file_size = None if tuple(size) == tuple(native) else (int(native[0]), int(native[1]))
    return out


def _range_filter(info: ClipInfo) -> list:
    """Pin the range so ffmpeg's yuv -> yuv420p10le step cannot convert it."""
    if not info.pix_fmt.startswith("yuv"):
        return []
    rng = "pc" if info.full_range else "tv"
    return ["-vf", f"scale=in_range={rng}:out_range={rng}"]


def _plane_sizes(width: int, height: int) -> tuple:
    y = width * height
    c = ((width + 1) // 2) * ((height + 1) // 2)
    return y, c


# --------------------------------------------------------------------------
# Code <-> unit conversion
# --------------------------------------------------------------------------


def to_unit(codes: np.ndarray, info: ClipInfo) -> np.ndarray:
    """Y codes -> float32 depth where 0 is the far end and 1 the near end.

    Not clipped: a limited-range stream may carry a few codes outside 64..940,
    and those should come back out exactly as they went in.
    """
    lo, hi = info.code_range
    return (codes.astype(np.float32) - lo) / float(hi - lo)


def from_unit(unit: np.ndarray, info: ClipInfo) -> np.ndarray:
    lo, hi = info.code_range
    codes = np.rint(unit * float(hi - lo) + lo)
    return np.clip(codes, 0, CODE_MAX).astype(np.uint16)


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


def iter_codes(info: ClipInfo, stop: Optional[threading.Event] = None) -> Iterator[np.ndarray]:
    """Yield each frame's Y plane as (H, W) uint16 10-bit codes, in order."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", info.path,
           "-an", "-sn", "-dn", *_range_filter(info), "-f", "rawvideo", "-pix_fmt", "yuv420p10le", "pipe:1"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=_NO_WINDOW)
    fw, fh = info.file_size or (info.width, info.height)
    resize = (fw, fh) != (info.width, info.height)
    if resize:
        import cv2
    y_px, c_px = _plane_sizes(fw, fh)
    frame_bytes = (y_px + 2 * c_px) * 2
    # Drained on a thread: a chatty stderr that nobody reads fills its pipe
    # buffer and stalls ffmpeg mid-clip.
    err_chunks: list = []
    drain = threading.Thread(target=lambda: err_chunks.append(proc.stderr.read()), daemon=True)
    drain.start()
    try:
        while True:
            if stop is not None and stop.is_set():
                return
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            y = np.frombuffer(buf, dtype="<u2", count=y_px).reshape(fh, fw)
            if resize:
                yield cv2.resize(y, (info.width, info.height), interpolation=cv2.INTER_LINEAR)
            else:
                yield y.copy()
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        proc.kill()
        proc.wait()
        drain.join(timeout=2)
        try:
            proc.stderr.close()
        except Exception:
            pass
        err = b"".join(c for c in err_chunks if c).decode("utf-8", "replace").strip()
        if err:
            logger.debug("ffmpeg (depth decode) said: %s", err)


def load_codes(info: ClipInfo, progress: Optional[Callable[[int, int], None]] = None,
               stop: Optional[threading.Event] = None) -> np.ndarray:
    """Every frame at once, (N, H, W) uint16. For preview; renders stream."""
    out = np.empty((max(info.num_frames, 1), info.height, info.width), dtype=np.uint16)
    n = 0
    for n, codes in enumerate(iter_codes(info, stop), start=1):
        if n > out.shape[0]:
            out = np.concatenate([out, np.empty_like(out[:1])], axis=0)
        out[n - 1] = codes
        if progress is not None and (n % 8 == 0 or n == info.num_frames):
            progress(n, info.num_frames)
    return out[:n]


def iter_rgb(path: str, width: int, height: int, stop: Optional[threading.Event] = None) -> Iterator[np.ndarray]:
    """Yield frames of `path` as (H, W, 3) uint8 RGB, resized to width x height.

    The source is decoded at the DEPTH map's size, which is a stretch when
    DepthCrafter rounded the map to multiples of 64: that is deliberate. A
    depth pixel and the RGB pixel shown under it must be the same point in the
    frame, and DepthCrafter's resize was a stretch too.
    """
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-i", path, "-an", "-sn", "-dn",
           "-vf", f"scale={width}:{height}:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    frame_bytes = width * height * 3
    try:
        while True:
            if stop is not None and stop.is_set():
                return
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            yield np.frombuffer(buf, dtype=np.uint8).reshape(height, width, 3).copy()
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        proc.kill()
        proc.wait()


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

_NVENC_OK: Optional[bool] = None


def nvenc_available() -> bool:
    """Whether hevc_nvenc will take a 10-bit encode here. Probed once."""
    global _NVENC_OK
    if _NVENC_OK is None:
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
               "color=c=gray:s=256x256:d=0.1", "-frames:v", "1", "-c:v", "hevc_nvenc",
               "-pix_fmt", "p010le", "-tune", "lossless", "-f", "null", "-"]
        try:
            _NVENC_OK = subprocess.run(cmd, capture_output=True, timeout=30,
                                       creationflags=_NO_WINDOW).returncode == 0
        except Exception:
            _NVENC_OK = False
        logger.info("Depth Map Editor: NVENC %s for depth output.", "available" if _NVENC_OK else "unavailable")
    return _NVENC_OK


class DepthWriter:
    """Stream (H, W) uint16 Y codes into a 10-bit HEVC MP4."""

    def __init__(self, path: str, info: ClipInfo, use_nvenc: Optional[bool] = None,
                 metadata: Optional[dict] = None) -> None:
        """`path` may be a partial_output .part name (the container comes from the name under it);
        `metadata`: container tags, {key: value} (VDA marks its maps, core.vda.output)."""
        self.path = path
        self.info = info
        self.use_nvenc = nvenc_available() if use_nvenc is None else use_nvenc
        y_px, c_px = _plane_sizes(info.width, info.height)
        self._chroma = np.full(2 * c_px, 512, dtype="<u2").tobytes()
        rng = "pc" if info.full_range else "tv"
        if self.use_nvenc:
            codec = ["-c:v", "hevc_nvenc", "-pix_fmt", "p010le", "-tune", "lossless", "-preset", "p7"]
        else:
            codec = ["-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-preset", "medium",
                     "-x265-params", "lossless=1:log-level=error"]
        # The colour tags go on the INPUT as well as the output. Tagged on the
        # output alone, ffmpeg sees an untagged source headed for bt709 and
        # inserts a conversion to get there -- which clamps: a real map's one
        # pixel at code 944, just above the tv ceiling, came back as 943. With
        # both sides tagged the same there is nothing to convert.
        tags = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", rng]
        extra = []
        for key, value in (metadata or {}).items():
            extra += ["-metadata", f"{key}={value}"]
        from core.common.partial_output import ffmpeg_output_args
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "rawvideo", "-pix_fmt", "yuv420p10le", "-s", f"{info.width}x{info.height}",
            "-r", info.fps, *tags, "-i", "pipe:0",
            "-an", *codec, "-tag:v", "hvc1", *tags, *extra,
            *ffmpeg_output_args(path),
        ]
        self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=_NO_WINDOW)
        self._err: list = []
        self._drain = threading.Thread(target=lambda: self._err.append(self._proc.stderr.read()), daemon=True)
        self._drain.start()
        self.frames_written = 0

    def write(self, codes: np.ndarray) -> None:
        if codes.shape != (self.info.height, self.info.width):
            raise ValueError(f"frame is {codes.shape}, writer expects {(self.info.height, self.info.width)}")
        try:
            self._proc.stdin.write(codes.astype("<u2", copy=False).tobytes())
            self._proc.stdin.write(self._chroma)
        except OSError:
            # ffmpeg is gone: say why (close raises with what it said), not
            # "BrokenPipe / Errno 22" (bug sweep 2026-10-09, A9)
            self.close()
            raise
        self.frames_written += 1

    def close(self) -> None:
        """Finish the file. Raises if ffmpeg failed, with what it said."""
        try:
            self._proc.stdin.close()
        except Exception:
            pass
        code = self._proc.wait()
        self._drain.join(timeout=5)
        if code != 0:
            err = b"".join(c for c in self._err if c).decode("utf-8", "replace").strip()
            raise RuntimeError(f"ffmpeg exited {code} writing {self.path}: {err[-800:]}")

    def abort(self) -> None:
        try:
            self._proc.kill()
            self._proc.wait()
        except Exception:
            pass
