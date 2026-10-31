"""Video I/O utilities for StereoCrafter.

Provides video reading and frame extraction utilities using decord
for efficient video loading.
"""

import os
import re
import json
import shutil
import threading
import time
import logging
import subprocess
from typing import Optional, Tuple, Any

import numpy as np
import torch
import cv2
from decord import VideoReader, cpu

from core.common.partial_output import discard, ffmpeg_output_args, part_path, publish, reap
from core.common.color_tags import with_color_tags
from core.common.encoding_utils import build_encoder_args, get_encoding_config_from_dict, nvenc_available

#: ffmpeg / ffprobe start with no console window: under Studio's .exe (no
#: console of its own) each one flashed one (audit 2026-09-28, minor).
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

#: Raw-frame decodes read through a 1 MB pipe: the 4 KB default halved FFmpeg's throughput
#: (Merging's three 16-bit reads took 7.6 s where Decord took 1.8, 2026-10-27).
from core.common.frame_pipe import popen_frames as _popen_frames  # noqa: E402
from core.common.frame_pipe import keep_coded_size  # noqa: E402

logger = logging.getLogger(__name__)

# Raw/All only below WARNING (see unified_gui/console_window.py). The full
# ffmpeg command lines go here: a stage tab should say that encoding started,
# not carry 300 characters of flags on every clip.
housekeeping_log = logging.getLogger(__name__ + ".housekeeping")


class _NumpyBatch:
    """Minimal wrapper to match Decord's get_batch(...).asnumpy() API."""

    def __init__(self, arr: np.ndarray):
        """Initialize with a numpy array.

        Args:
            arr: Numpy array to wrap
        """
        self._arr = arr

    def asnumpy(self) -> np.ndarray:
        """Return the underlying numpy array.

        Returns:
            The wrapped numpy array
        """
        return self._arr


class VideoIO:
    """Video input/output operations for video processing."""

    @staticmethod
    def read_video_info(video_path: str) -> Tuple[int, int, int, float]:
        """Read video information without loading frames.

        Args:
            video_path: Path to the video file

        Returns:
            Tuple of (total_frames, height, width, fps)
        """
        logger.debug(f"==> Reading video info: {video_path}")
        reader = VideoReader(video_path, ctx=cpu(0))
        total_frames = len(reader)
        first_frame = reader.get_batch([0]).asnumpy()
        height, width = first_frame.shape[1:3]
        fps = float(reader.get_avg_fps())

        logger.debug(f"==> Video info: {total_frames} frames, {width}x{height}, {fps} fps")

        return total_frames, height, width, fps

    @staticmethod
    def read_frame(reader: VideoReader, index: int) -> np.ndarray:
        """Read a single frame from a video reader.

        Args:
            reader: Active VideoReader instance
            index: Frame index to read

        Returns:
            Frame as numpy array [H, W, C]
        """
        return reader.get_batch([index]).asnumpy()[0]

    @staticmethod
    def read_frames_batch(reader: VideoReader, indices: list) -> np.ndarray:
        """Read multiple frames from a video reader.

        Args:
            reader: Active VideoReader instance
            indices: List of frame indices to read

        Returns:
            Frames as numpy array [N, H, W, C]
        """
        return reader.get_batch(indices).asnumpy()


class _StderrDrain:
    """Read a child process's stderr to EOF on a daemon thread.

    A pipe nobody reads holds about 64 KB. Once ffmpeg fills it, ffmpeg blocks
    writing -- while we are blocked reading its stdout, waiting for frames it
    will never send. Nothing times out and nothing checks a stop flag inside
    those blocking reads, so the only way out is killing the process.

    It costs one daemon thread per ffmpeg process and buys two things: the
    deadlock cannot happen, and the tail of ffmpeg's complaints is kept so a
    failure can say why instead of just returning a code.
    """

    def __init__(self, proc, label: str = "ffmpeg", keep_lines: int = 20):
        self._label = label
        self._keep = keep_lines
        self._lines = []
        self._lock = threading.Lock()
        self._thread = None
        stream = getattr(proc, "stderr", None)
        if stream is None:
            return
        self._thread = threading.Thread(target=self._pump, args=(stream,), daemon=True)
        self._thread.start()

    def _pump(self, stream):
        try:
            for raw in iter(stream.readline, b""):
                if not raw:
                    break
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="ignore")
                line = raw.rstrip()
                if not line:
                    continue
                with self._lock:
                    self._lines.append(line)
                    if len(self._lines) > self._keep:
                        del self._lines[0]
        except Exception:
            # A closed or killed pipe is the normal way this ends.
            pass

    def text(self) -> str:
        """The tail of what the child said, newest last."""
        with self._lock:
            return "\n".join(self._lines)

    def log_if(self, failed: bool) -> None:
        """Log the tail when something went wrong, at most once."""
        if not failed:
            return
        tail = self.text()
        if tail:
            logger.error(f"{self._label} stderr:\n{tail}")


def _stop_pipe_process(proc) -> None:
    """End a decode ffmpeg and wait for it, so its handle on the source is
    gone when this returns (audit 2026-09-28 #26: a reader's ffmpeg that was
    only killed, or never stopped, held the clip open -- WinError 32 when it
    was moved to finished/ -- and lived on, one per clip)."""
    if proc is None:
        return
    for pipe in (proc.stdout, proc.stderr):
        if pipe:
            try:
                pipe.close()
            except Exception:
                pass
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass


#: Planar YUV the reader takes from FFmpeg as it is and converts itself (_yuv_to_rgb255):
#: 8-bit (yuvj: full range) and 9-16-bit little-endian.
_NATIVE_YUV = re.compile(r"^yuv(j?)(420|422|444)p(?:(9|10|12|14|16)le)?$")
#: Codecs handed to NVDEC (-hwaccel cuda) -- the ones it decodes at 10 bits; FFmpeg
#: decodes in software where the card can't (4:2:2 before Blackwell, ...).
_NVDEC_CODECS = ("hevc", "av1", "vp9")
_YUV_K = {"bt709": (0.2126, 0.0722), "bt601": (0.299, 0.114), "bt2020": (0.2627, 0.0593)}


def _yuv_to_rgb255(raw, w: int, h: int, sub_x: int, sub_y: int, bits: int, in_range: str, in_matrix: str,
                   device, as_uint8: bool = False) -> np.ndarray:
    """One planar YUV frame (little-endian, ``bits`` deep) as RGB float16 on the 0..255 scale
    (``as_uint8``: rounded to uint8, for the 8-bit reader).

    The exact BT.601/709/2020 maths (FFmpeg's rgb48 conversion ran up to 1.0 of 255 dark: white
    at 254.0); chroma brought to full size linearly, sited as MPEG-2/H.264/HEVC put it --
    horizontally on the even luma samples, vertically between rows. ``raw`` must be writable."""
    import torch.nn.functional as F
    cw, ch = -(-w // sub_x), -(-h // sub_y)
    bps = 1 if bits <= 8 else 2
    planes = [torch.frombuffer(raw, dtype=torch.uint8 if bps == 1 else torch.int16, count=n, offset=o).view(r, c)
              for n, o, r, c in ((w * h, 0, h, w), (cw * ch, w * h * bps, ch, cw),
                                 (cw * ch, (w * h + cw * ch) * bps, ch, cw))]
    y, u, v = (p.to(device, non_blocking=True).float() for p in planes)

    def full(c):
        c = c[None, None]
        if sub_y == 2:
            c = F.interpolate(c, size=(h, c.shape[3]), mode="bilinear", align_corners=False)
        if sub_x == 2:
            nxt = torch.cat([c[..., 1:], c[..., -1:]], dim=3)
            c = torch.stack([c, (c + nxt) * 0.5], dim=4).flatten(3)[..., :w]
        return c[0, 0]

    u, v = full(u), full(v)
    s = float(1 << (bits - 8))
    if in_range == "pc":
        m = float((1 << bits) - 1)
        y, u, v = y / m, (u - 128 * s) / m, (v - 128 * s) / m
    else:
        y, u, v = (y - 16 * s) / (219 * s), (u - 128 * s) / (224 * s), (v - 128 * s) / (224 * s)
    kr, kb = _YUV_K.get(in_matrix, _YUV_K["bt709"])
    r = y + 2 * (1 - kr) * v
    b = y + 2 * (1 - kb) * u
    g = (y - kr * r - kb * b) / (1 - kr - kb)
    rgb = torch.stack([r, g, b], dim=2).clamp_(0, 1) * 255
    if as_uint8:
        return rgb.add_(0.5).to(torch.uint8).cpu().numpy()
    return rgb.half().cpu().numpy()


class FFmpegRGBPipeReader:
    """
    Sequential RGB frame reader backed by an FFmpeg pipe (rawvideo).
    Designed for render-time usage where get_batch() is called with
    increasing frame indices (typically contiguous batches).

    bit16: decode as rgb48 and hand back float16 frames on the SAME 0..255
    scale as the 8-bit ones, so a 10-bit source keeps its precision through
    code written for Decord's uint8 (``.float() / 255.0``). See open_rgb_reader.

    yuv_src: the source's stream info, given when the frames are read at its own size.
    The reader then takes planar YUV as it is -- NVDEC decoding where it can -- and
    converts it in torch, on the GPU when there is one: half the bytes through the
    pipe, no swscale, and exact (2026-10-27: Merging's three 16-bit reads 5.4 s -> 3.7;
    8-bit sources too, as uint8).
    """

    def __init__(
        self,
        video_path: str,
        width: int,
        height: int,
        fps: float,
        total_frames: int,
        in_range: str = "tv",
        in_matrix: str = "bt709",
        bit16: bool = False,
        yuv_src: Optional[dict] = None,
    ):
        self.video_path = video_path
        self.width = int(width)
        self.height = int(height)
        self.fps = float(fps) if fps else 0.0
        self.total_frames = int(total_frames) if total_frames is not None else -1
        self.in_range = in_range
        self.in_matrix = in_matrix
        self._proc = None
        self._next_frame = 0
        self.bit16 = bool(bit16)
        self._pix_fmt = "rgb48le" if self.bit16 else "rgb24"
        self._frame_size = self.width * self.height * (6 if self.bit16 else 3)
        self._force_fallback = False  # set True if strict scale params fail
        self._yuv = None              # (pix_fmt, sub_x, sub_y, bits) for a native-YUV read
        self._device = "cpu"
        self._nvdec = False
        m = _NATIVE_YUV.match(str((yuv_src or {}).get("pix_fmt") or ""))
        bits = int(m.group(3) or 8) if m else 0
        # 8-bit into uint8 frames, deeper into float16; only with CUDA -- on the CPU the
        # conversion takes 30 ms a 1080p frame, where FFmpeg's own is quicker
        if m and (bits > 8) == self.bit16 and torch.cuda.is_available():
            sub = {"420": (2, 2), "422": (2, 1), "444": (1, 1)}[m.group(2)]
            self._yuv = (m.group(0), sub[0], sub[1], bits)
            if m.group(1):                    # yuvj: full range whatever the tags say
                self.in_range = "pc"
            cw, ch = -(-self.width // sub[0]), -(-self.height // sub[1])
            self._pix_fmt = m.group(0)
            self._frame_size = (self.width * self.height + 2 * cw * ch) * (2 if bits > 8 else 1)
            self._device = "cuda"
            # H.264 left to the CPU: NVDEC's frames cost a repack, and 8-bit decodes fast anyway
            self._nvdec = str(yuv_src.get("codec_name") or "") in _NVDEC_CODECS

    def __len__(self):
        return self.total_frames if self.total_frames >= 0 else 0

    def get_avg_fps(self):
        return self.fps

    def _build_cmd(self, start_frame: int = 0):
        # Start from the requested frame index. For render, this is usually 0.
        # Use -ss time seek as a best-effort fast start for non-zero start_frame.
        if self._force_fallback and self._yuv:   # a failed native read: FFmpeg's RGB instead
            self._yuv, self._nvdec = None, False
            self._pix_fmt = "rgb48le" if self.bit16 else "rgb24"
            self._frame_size = self.width * self.height * (6 if self.bit16 else 3)
        args = ["ffmpeg", "-v", "error", "-nostdin", *keep_coded_size()]   # ffprobe's size, a container crop or not
        if self._nvdec:
            args += ["-hwaccel", "cuda"]
        if start_frame and self.fps:
            start_time = start_frame / self.fps
            if self.bit16:
                # Half a frame early: the seek lands on the first frame AT or after
                # it, and a time rounded up past frame n's own lands on n + 1.
                start_time = (start_frame - 0.5) / self.fps
            args += ["-ss", f"{start_time:.6f}"]
        args += ["-i", self.video_path, "-an", "-sn", "-dn"]

        vf_strict = (
            f"scale={self.width}:{self.height}:flags=bicubic:"
            f"in_range={self.in_range}:out_range={self.in_range}:"
            f"in_color_matrix={self.in_matrix}:out_color_matrix={self.in_matrix},"
            f"format={self._pix_fmt}"
        )
        vf_fallback = f"scale={self.width}:{self.height}:flags=bicubic,format={self._pix_fmt}"
        vf = vf_fallback if self._force_fallback else vf_strict
        if self._yuv:
            args += ["-f", "rawvideo", "-pix_fmt", self._pix_fmt, "-fps_mode", "passthrough", "-"]
            return args
        args += ["-vf", vf, "-f", "rawvideo", "-pix_fmt", self._pix_fmt, "-fps_mode", "passthrough", "-"]
        return args

    def _frame(self, raw: bytes) -> np.ndarray:
        if self._yuv:
            _, sub_x, sub_y, bits = self._yuv
            return _yuv_to_rgb255(raw, self.width, self.height, sub_x, sub_y, bits, self.in_range,
                                  self.in_matrix, self._device, as_uint8=not self.bit16)
        if not self.bit16:
            return np.frombuffer(raw, dtype=np.uint8).reshape(self.height, self.width, 3)
        u16 = np.frombuffer(raw, dtype=np.uint16).reshape(self.height, self.width, 3)
        # torch's threaded casts: 4 ms a 1080p frame where NumPy's took 26 (Merging read three
        # clips this way at half its old speed); the values are the same
        return torch.from_numpy(u16).to(torch.float32).mul_(255.0 / 65535.0).to(torch.float16).numpy()

    def _ensure_process(self, start_frame: int):
        if self._proc is not None:
            return
        cmd = self._build_cmd(start_frame=start_frame)
        self._proc = _popen_frames(cmd)
        # Nothing reads this pipe otherwise, and a full one deadlocks the
        # decode against our own blocking stdout read.
        self._stderr = _StderrDrain(self._proc, f"ffmpeg rgb decode {os.path.basename(self.video_path)}")
        self._next_frame = start_frame

    def _restart(self, start_frame: int):
        try:
            _stop_pipe_process(self._proc)  # reaped, not just killed (#26)
        finally:
            self._proc = None
        self._ensure_process(start_frame=start_frame)

    def close(self):
        """End the decode ffmpeg and release the source (#26)."""
        try:
            _stop_pipe_process(self._proc)
        finally:
            self._proc = None

    def __del__(self):
        self.close()

    def _why(self) -> str:
        """What FFmpeg said before it stopped sending frames, for the error: a 12 GB user's Merging
        failed on every clip with only "reached EOF" in the log, FFmpeg's reason kept but never shown
        (2026-10-30)."""
        try:
            self._proc.wait(timeout=2)
        except Exception:
            pass
        drain = getattr(self, "_stderr", None)
        if drain is None:
            return ""
        if drain._thread is not None:
            drain._thread.join(timeout=1)
        said = drain.text().strip()
        return f" {os.path.basename(self.video_path)}: FFmpeg said: {said[-800:]}" if said else ""

    def get_batch(self, indices):
        dtype = np.float16 if self.bit16 else np.uint8
        if not indices:
            return _NumpyBatch(np.empty((0, self.height, self.width, 3), dtype=dtype))

        try:
            # Expect indices to be increasing most of the time; if not, restart.
            min_idx = int(min(indices))
            max_idx = int(max(indices))

            if self._proc is None:
                self._ensure_process(start_frame=min_idx)
            elif min_idx < self._next_frame:
                self._restart(start_frame=min_idx)

            # Discard frames until we reach min_idx
            while self._next_frame < min_idx:
                junk = self._proc.stdout.read(self._frame_size)
                if not junk or len(junk) < self._frame_size:
                    if not self._force_fallback:
                        self._force_fallback = True
                        self._restart(start_frame=min_idx)
                        return self.get_batch(indices)
                    raise EOFError("FFmpegRGBPipeReader reached EOF while skipping frames." + self._why())
                self._next_frame += 1

            # Read frames for requested indices
            out = np.empty((len(indices), self.height, self.width, 3), dtype=dtype)
            for j, idx in enumerate(indices):
                idx = int(idx)
                if idx < self._next_frame:
                    # non-monotonic request; restart and recurse (rare)
                    self._restart(start_frame=idx)
                    return self.get_batch(indices)

                # Skip gap frames if needed
                while self._next_frame < idx:
                    junk = self._proc.stdout.read(self._frame_size)
                    if not junk or len(junk) < self._frame_size:
                        if not self._force_fallback:
                            self._force_fallback = True
                            self._restart(start_frame=min_idx)
                            return self.get_batch(indices)
                        raise EOFError("FFmpegRGBPipeReader reached EOF while skipping gap frames." + self._why())
                    self._next_frame += 1

                raw = bytearray(self._frame_size)     # writable: torch takes the 16-bit frame as is
                if self._proc.stdout.readinto(raw) < self._frame_size:
                    if not self._force_fallback:
                        self._force_fallback = True
                        self._restart(start_frame=min_idx)
                        return self.get_batch(indices)
                    raise EOFError("FFmpegRGBPipeReader reached EOF while reading a frame." + self._why())
                out[j] = self._frame(raw)
                self._next_frame += 1

            return _NumpyBatch(out)

        except EOFError as e:
            if not self._force_fallback:
                # Some FFmpeg builds don't support scale=in_range/in_color_matrix.
                # Retry once with a simpler filter chain.
                self._force_fallback = True
                try:
                    self._restart(start_frame=int(min(indices)) if indices else 0)
                except Exception:
                    pass
                return self.get_batch(indices)
            raise


def as_uint8(frames: np.ndarray) -> np.ndarray:
    """Frames on the 0..255 scale as uint8: a bit16 reader's float16 rounded, uint8 as is.
    For what needs 8-bit pictures (OpenCV detection), never for the picture itself."""
    if frames.dtype == np.uint8:
        return frames
    return np.clip(frames.astype(np.float32) + 0.5, 0, 255).astype(np.uint8)


class Uint8Frames:
    """A reader's frames as uint8 (as_uint8), the Decord shape kept: len, get_avg_fps, get_batch."""

    def __init__(self, reader):
        self._inner = reader

    def __len__(self):
        return len(self._inner)

    def get_avg_fps(self):
        return self._inner.get_avg_fps()

    def get_batch(self, indices):
        return _NumpyBatch(as_uint8(self._inner.get_batch(indices).asnumpy()))


def _frame_count(video_path: str) -> int:
    """The video stream's frame count: the container's, else counted packets."""
    for entry in ("stream=nb_frames", "stream=nb_read_packets"):
        cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", entry, "-of", "csv=p=0"]
        if entry.endswith("packets"):
            cmd.insert(1, "-count_packets")
        try:
            out = subprocess.run(cmd + [video_path], capture_output=True, text=True, timeout=120,
                                 creationflags=_NO_WINDOW).stdout.strip().split(",")[0]
            if out.isdigit() and int(out) > 0:
                return int(out)
        except (OSError, subprocess.SubprocessError):
            pass
    return 0


class ReadAhead:
    """A reader whose next range is read on a thread of its own while the caller works on this one (user,
    2026-10-30: Merging read its three or four clips a chunk at a time between its GPU work, a third of its
    time). After a run of consecutive frames it reads the run after it, as long again, so a chunked loop
    always finds its next chunk ready; anything else -- one frame, a jump -- is read there and then, once
    the read in flight is done (the reader is read by one thread at a time, in order). Ended by close() or
    by being dropped, which closes the reader under it too: `del reader` frees the file as before.
    """

    def __init__(self, reader, name: str = "read-ahead"):
        from concurrent.futures import ThreadPoolExecutor
        self._r = reader
        self._pool = ThreadPoolExecutor(1, thread_name_prefix=name)
        self._next = None           # (the indices read ahead, their Future)

    def __len__(self) -> int:
        return len(self._r)

    def __getattr__(self, name):
        return getattr(self.__dict__["_r"], name)

    def _settle(self):
        """The read in flight, finished (or failed) before the reader is used again; its result."""
        nxt, self._next = self._next, None
        if nxt is None:
            return None, None
        try:
            return nxt[0], nxt[1].result()
        except Exception:
            return None, None       # read again in the caller's thread, where an error is the caller's

    def get_batch(self, indices):
        idx = [int(i) for i in indices]
        ahead, batch = self._settle()
        if ahead != tuple(idx) or batch is None:
            batch = self._r.get_batch(idx)
        n = len(idx)
        if n > 1 and idx == list(range(idx[0], idx[0] + n)):
            start, stop = idx[-1] + 1, min(idx[-1] + 1 + n, len(self._r))
            if start < stop:
                nxt = tuple(range(start, stop))
                self._next = (nxt, self._pool.submit(self._r.get_batch, list(nxt)))
        return batch

    def close(self):
        if self.__dict__.get("_pool") is None:
            return
        self._settle()
        self._pool.shutdown(wait=True)
        self._pool = None
        close = getattr(self._r, "close", None)
        if close is not None:
            close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def open_rgb_reader_ahead(video_path: str) -> ReadAhead:
    """open_rgb_reader's reader, its next chunk read while this one is used (ReadAhead)."""
    return ReadAhead(open_rgb_reader(video_path), name="merge-read")


def open_rgb_reader(video_path: str) -> "FFmpegRGBPipeReader":
    """A Decord-shaped colour reader (len, get_avg_fps, get_batch(...).asnumpy())
    decoded by FFmpeg at the file's own bit depth: above 8 bits it hands back
    float16 on the 0..255 scale (FFmpegRGBPipeReader bit16), so ``/ 255.0``
    still gives 0..1 -- with the 10 bits kept. Decord decodes everything to
    8 bits, and about 6 codes (of 1023) darker than FFmpeg's BT.709 (user,
    2026-10-27: Merging's eyes banded and dark)."""
    info = get_video_stream_info(video_path) or {}
    w, h = int(info.get("width") or 0), int(info.get("height") or 0)
    if w <= 0 or h <= 0:
        raise ValueError(f"Could not read the video size of {video_path}")
    fps = 0.0
    fr = str(info.get("r_frame_rate") or "0/0")
    if "/" in fr:
        num, den = fr.split("/")
        fps = float(num) / float(den) if float(den) > 0 else 0.0
    cr = str(info.get("color_range") or "").lower()
    cs = str(info.get("color_space") or "").lower()
    in_range = "pc" if ("full" in cr or cr == "pc") else "tv"
    in_matrix = "bt2020" if "2020" in cs else ("bt601" if ("601" in cs or "170m" in cs or "470bg" in cs) else "bt709")
    bits = _pix_fmt_bits(str(info.get("pix_fmt") or ""))
    return FFmpegRGBPipeReader(video_path, w, h, fps, _frame_count(video_path), in_range=in_range,
                               in_matrix=in_matrix, bit16=bits > 8, yuv_src=info)


def _pix_fmt_bits(pix_fmt: str) -> int:
    """Bits per component of an FFmpeg pix_fmt name (8 when it says none)."""
    p = pix_fmt.lower()
    if p in ("p010le", "p010be", "p016le", "p016be"):
        return 10 if "010" in p else 16
    if p.startswith(("rgb48", "bgr48", "rgba64", "bgra64", "gray16", "y210", "v210")):
        return 16 if "16" in p or "48" in p or "64" in p else 10
    m = re.search(r"p(9|10|12|14|16)(le|be)$", p) or re.search(r"(9|10|12|14|16)(le|be)$", p)
    return int(m.group(1)) if m else 8


class FFmpegRGBSingleFrameReader:
    """
    Random-access RGB reader for preview usage.
    Each get_batch([idx]) spawns a small FFmpeg decode for that frame.
    Slower than Decord, but matches FFmpeg's YUV->RGB conversion.
    """

    def __init__(
        self,
        video_path: str,
        width: int,
        height: int,
        fps: float,
        total_frames: int,
        in_range: str = "tv",
        in_matrix: str = "bt709",
    ):
        self.video_path = video_path
        self.width = int(width)
        self.height = int(height)
        self.fps = float(fps) if fps else 0.0
        self.total_frames = int(total_frames) if total_frames is not None else -1
        self.in_range = in_range
        self.in_matrix = in_matrix
        self._frame_size = self.width * self.height * 3
        self._force_fallback = False  # set True if strict scale params fail

    def __len__(self):
        return self.total_frames if self.total_frames >= 0 else 0

    def get_avg_fps(self):
        return self.fps

    def get_batch(self, indices):
        if not indices:
            return _NumpyBatch(np.empty((0, self.height, self.width, 3), dtype=np.uint8))

        def _read_exact(proc, nbytes: int) -> bytes:
            buf = b""
            while len(buf) < nbytes:
                chunk = proc.stdout.read(nbytes - len(buf)) if proc.stdout else b""
                if not chunk:
                    break
                buf += chunk
            return buf

        def _decode_one(idx: int, vf: str) -> tuple[bytes, str, int]:
            cmd = [
                "ffmpeg",
                "-v",
                "error",
                "-nostdin",
                "-i",
                self.video_path,
                "-an",
                "-sn",
                "-dn",
                "-vf",
                vf,
                "-frames:v",
                "1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-",
            ]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=_NO_WINDOW)
            # Drained from the start rather than read after the fact: reading
            # stderr only once stdout is satisfied is exactly the order that
            # deadlocks when ffmpeg has more to say than the pipe can hold.
            drain = _StderrDrain(proc, "ffmpeg single-frame decode")
            raw = b""
            err = ""
            try:
                raw = _read_exact(proc, self._frame_size)
                err = drain.text()
            finally:
                try:
                    if proc.stdout:
                        proc.stdout.close()
                except Exception:
                    pass
                try:
                    if proc.stderr:
                        proc.stderr.close()
                except Exception:
                    pass
                try:
                    proc.wait()
                except Exception:
                    pass
            return raw, err, int(proc.returncode or 0)

        frames = np.empty((len(indices), self.height, self.width, 3), dtype=np.uint8)
        for j, idx in enumerate(indices):
            idx = int(idx)

            # Try the strict matrix/range path first; if unsupported by the user's ffmpeg build,
            # fall back to a plain scale->rgb24 path (still FFmpeg-based conversion, just less explicit).
            vf_strict = (
                f"select='eq(n\\,{idx})',"
                f"scale={self.width}:{self.height}:flags=bicubic:"
                f"in_range={self.in_range}:out_range={self.in_range}:"
                f"in_color_matrix={self.in_matrix}:out_color_matrix={self.in_matrix},"
                "format=rgb24"
            )
            vf_fallback = f"select='eq(n\\,{idx})',scale={self.width}:{self.height}:flags=bicubic,format=rgb24"

            raw, err, rc = _decode_one(idx, vf_strict)
            if not raw or len(raw) < self._frame_size:
                raw2, err2, rc2 = _decode_one(idx, vf_fallback)
                if raw2 and len(raw2) >= self._frame_size:
                    raw, err, rc = raw2, err2, rc2
                else:
                    msg = (err2 or err or "").strip()
                    if msg:
                        raise EOFError(f"FFmpegRGBSingleFrameReader failed to decode frame {idx}: {msg}")
                    raise EOFError(f"FFmpegRGBSingleFrameReader failed to decode frame {idx}.")

            frame = np.frombuffer(raw, dtype=np.uint8).reshape(self.height, self.width, 3)
            frames[j] = frame

        return _NumpyBatch(frames)


def _infer_depth_bit_depth(depth_stream_info: Optional[dict]) -> int:
    """Infer the bit depth of a depth stream from ffprobe info."""
    if not depth_stream_info:
        return 8
    pix_fmt = str(depth_stream_info.get("pix_fmt", "")).lower()
    profile = str(depth_stream_info.get("profile", "")).lower()
    m = re.search(r"(?:p|gray)(\d+)", pix_fmt)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            pass
    if "main10" in profile or "10" in profile:
        return 10
    return 8


def _build_depth_vf(pix_fmt: str, out_w: int, out_h: int) -> str:
    """Build FFmpeg video filter for depth map extraction."""
    pix_fmt = (pix_fmt or "").lower()
    if pix_fmt.startswith("gray"):
        return f"scale={out_w}:{out_h}:flags=bilinear,format=gray16le"
    return f"extractplanes=y,scale={out_w}:{out_h}:flags=bilinear,format=gray16le"


class FFmpegDepthPipeReader:
    """Sequential FFmpeg-backed depth reader preserving 10-bit+ values."""

    def __init__(self, path: str, out_w: int, out_h: int, bit_depth: int, num_frames: int, pix_fmt: str = "",
                 src_size: Optional[Tuple[int, int]] = None):
        """src_size (the depth's own width, height): given with CUDA there and a different out size, FFmpeg
        decodes the depth at its own size and get_batch resizes it on the GPU, bilinear (user, 2026-10-30:
        FFmpeg's scale of 768x320 up to 2580x1080 was a fifth of a Splatting clip's time, on the CPU). It is
        the preview's own resize (cv2 INTER_LINEAR on floats: 0.0000 apart), so frames come back float32;
        FFmpeg's fixed-point bilinear was up to ~5 codes of 1023 off it on smooth depth, 16 at hard edges."""
        self.path = path
        self.out_w = int(out_w)
        self.out_h = int(out_h)
        self._gpu_from = None
        if src_size and tuple(int(v) for v in src_size) != (self.out_w, self.out_h) and torch.cuda.is_available():
            self._gpu_from = (int(src_size[0]), int(src_size[1]))
        self._dec_w, self._dec_h = self._gpu_from or (self.out_w, self.out_h)
        self.bit_depth = int(bit_depth) if bit_depth else 16
        try:
            if isinstance(num_frames, (int, float)) and not isinstance(num_frames, bool):
                self._num_frames = int(num_frames)
            elif isinstance(num_frames, str) and num_frames not in ("", "N/A"):
                self._num_frames = int(float(num_frames))
            else:
                self._num_frames = 0
        except (ValueError, TypeError):
            self._num_frames = 0
        self._pix_fmt = pix_fmt or ""
        self._proc: Optional[subprocess.Popen] = None
        self._next_index = 0
        self._frame_bytes = self._dec_w * self._dec_h * 2
        self._msb_shift: Optional[int] = None
        self._use_16_to_n_scale: bool = False
        self._start_process()

    def __len__(self) -> int:
        return self._num_frames

    def _start_process(self):
        self.close()
        vf = _build_depth_vf(self._pix_fmt, self._dec_w, self._dec_h)
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-i",
            self.path,
            "-an",
            "-sn",
            "-dn",
            "-vframes",
            str(self._num_frames) if isinstance(self._num_frames, int) and self._num_frames > 0 else "999999999",
            "-vf",
            vf,
            "-f",
            "rawvideo",
            "pipe:1",
        ]
        self._proc = _popen_frames(cmd)
        self._stderr = _StderrDrain(self._proc, "ffmpeg depth decode")
        self._next_index = 0
        self._msb_shift = None

    def close(self):
        try:
            _stop_pipe_process(self._proc)  # reaped, so the file is free (#26)
        finally:
            self._proc = None

    def __del__(self):
        self.close()

    def seek(self, idx: int):
        idx = int(idx)
        if idx == self._next_index:
            return
        if idx < self._next_index:
            self._start_process()
        to_skip = idx - self._next_index
        if to_skip <= 0:
            self._next_index = idx
            return
        if self._proc is None or self._proc.stdout is None:
            self._start_process()
        # Skipped in pieces of at most 64 MB (Studio audit 2026-09-28, PERF:
        # one read of the whole skip -- 20 GB to start a 1080p range at 5000).
        remaining = to_skip * self._frame_bytes
        piece = max(self._frame_bytes, (64 * 2 ** 20 // self._frame_bytes) * self._frame_bytes)
        while remaining > 0:
            got = self._proc.stdout.read(min(piece, remaining))
            if not got:
                break
            remaining -= len(got)
        self._next_index = idx

    def _read_exact(self, nbytes: int) -> bytes:
        if self._proc is None or self._proc.stdout is None:
            self._start_process()
        buf = b""
        while len(buf) < nbytes:
            chunk = self._proc.stdout.read(nbytes - len(buf))
            if not chunk:
                break
            buf += chunk
        return buf

    def _maybe_apply_shift(self, arr_u16: np.ndarray) -> np.ndarray:
        if self._msb_shift is None:
            expected_max = (1 << self.bit_depth) - 1 if 0 < self.bit_depth < 16 else None
            if expected_max is None:
                self._msb_shift = 0
                self._use_16_to_n_scale = False
            else:
                flat = arr_u16.reshape(-1)
                step = max(1, flat.size // 50000)
                sample = flat[::step]
                max_val = int(sample.max(initial=0))
                if max_val <= expected_max:
                    # Nothing here tells a shift from a scale: the stream is always
                    # widened to 16 bits (_build_depth_vf), so this is a near-black
                    # batch -- a clip opening on black. Latching "no scaling" on it
                    # left every later batch up to 64x too large (bug sweep 2026-10-09
                    # A11). Scaled from 16 bits, which is exact for both at values this
                    # low, and decided again on the next batch.
                    arr32 = arr_u16.astype(np.uint32)
                    return ((arr32 * expected_max + 32767) // 65535).astype(np.uint16)
                else:
                    shift = 16 - self.bit_depth
                    if shift <= 0:
                        self._msb_shift = 0
                        self._use_16_to_n_scale = False
                    else:
                        low_mask = (1 << shift) - 1
                        low_bits_max = int((sample & low_mask).max(initial=0))
                        if low_bits_max == 0:
                            self._msb_shift = shift
                            self._use_16_to_n_scale = False
                        else:
                            self._msb_shift = 0
                            self._use_16_to_n_scale = True
        expected_max = (1 << self.bit_depth) - 1 if 0 < self.bit_depth < 16 else None
        if expected_max is None:
            return arr_u16
        if self._msb_shift and self._msb_shift > 0:
            return (arr_u16 >> self._msb_shift).astype(np.uint16)
        if self._use_16_to_n_scale:
            arr32 = arr_u16.astype(np.uint32)
            return ((arr32 * expected_max + 32767) // 65535).astype(np.uint16)
        return arr_u16

    def get_batch(self, indices):
        indices = list(indices)
        if not indices:
            return _NumpyBatch(np.zeros((0, self.out_h, self.out_w, 1), dtype=np.uint16))
        first = int(indices[0])
        if first != self._next_index:
            self.seek(first)
        n = len(indices)
        expected = self._frame_bytes * n
        buf = self._read_exact(expected)
        if len(buf) != expected:
            raise EOFError(f"FFmpegDepthPipeReader: expected {expected} bytes, got {len(buf)}")
        arr = np.frombuffer(buf, dtype=np.uint16).reshape(n, self._dec_h, self._dec_w, 1)
        arr = self._maybe_apply_shift(arr)
        self._next_index = first + n
        if self._gpu_from is not None:
            import torch.nn.functional as F
            t = torch.from_numpy(np.ascontiguousarray(arr[..., 0])).to("cuda", non_blocking=True).float()
            up = F.interpolate(t[:, None], size=(self.out_h, self.out_w), mode="bilinear", align_corners=False)
            return _NumpyBatch(up[:, 0, :, :, None].cpu().numpy())
        return _NumpyBatch(arr.copy())


class _ResizingDepthReader:
    """Wrapper reader that resizes depth frames to target resolution."""

    def __init__(self, inner_reader, out_w, out_h):
        self._inner = inner_reader
        self._out_w = int(out_w)
        self._out_h = int(out_h)

    def __len__(self) -> int:
        return len(self._inner)

    def seek(self, *args, **kwargs):
        return self._inner.seek(*args, **kwargs)

    def get_batch(self, indices):
        _batch = self._inner.get_batch(indices)
        arr = _batch.asnumpy()
        in_h, in_w = arr.shape[1:3]
        if in_w == self._out_w and in_h == self._out_h:
            return _batch
        interp = cv2.INTER_LINEAR if (self._out_w > in_w or self._out_h > in_h) else cv2.INTER_AREA
        if arr.ndim == 4:
            out = np.empty((arr.shape[0], self._out_h, self._out_w, arr.shape[3]), dtype=arr.dtype)
            for i in range(arr.shape[0]):
                res = cv2.resize(arr[i], (self._out_w, self._out_h), interpolation=interp)
                if res.ndim == 2:
                    res = res[..., np.newaxis]
                out[i] = res
        else:
            out = np.empty((arr.shape[0], self._out_h, self._out_w), dtype=arr.dtype)
            for i in range(arr.shape[0]):
                out[i] = cv2.resize(arr[i], (self._out_w, self._out_h), interpolation=interp)
        return _NumpyBatch(out)


def read_video_frames(
    video_path: str,
    process_length: int,
    set_pre_res: bool,
    pre_res_width: int,
    pre_res_height: int,
    strict_ffmpeg_decode: bool = False,
    dataset: str = "open",
    is_depth: bool = False,
    high_bit_source: bool = False,
) -> Tuple[Any, float, int, int, int, int, Optional[dict], int]:
    """Initialize a VideoReader for chunked reading, with optional high-bit depth support.

    high_bit_source: a colour source above 8 bits is read by FFmpeg at 16 bits
    (FFmpegRGBPipeReader bit16: float16 on the 0..255 scale) instead of Decord's
    8-bit RGB (user, 2026-10-27: Splatting's warped eye lost the source's 10 bits)."""
    try:
        process_length = int(process_length) if process_length not in (None, "", "N/A") else -1
    except (ValueError, TypeError):
        process_length = -1

    if dataset != "open":
        raise NotImplementedError(f"Dataset '{dataset}' not supported.")

    logger.info(f"==> Initializing VideoReader for: {video_path}")
    video_stream_info = get_video_stream_info(video_path)
    fps = 0.0
    original_height, original_width = 0, 0
    total_frames_available = 0

    # Use bit-depth awareness for depth maps
    bit_depth = 8
    if is_depth:
        bit_depth = _infer_depth_bit_depth(video_stream_info)

    # Determine dimensions and frame count
    try:
        # Use decord for metadata if ffprobe failed or was insufficient
        tmp = VideoReader(video_path, ctx=cpu(0))
        original_height, original_width = tmp.get_batch([0]).asnumpy().shape[1:3]
        total_frames_available = len(tmp)
        fps = float(tmp.get_avg_fps())
        del tmp
    except Exception:
        if video_stream_info:
            original_height = int(video_stream_info.get("height", 0))
            original_width = int(video_stream_info.get("width", 0))
            total_frames_available = int(video_stream_info.get("nb_frames", 0))
            fr_str = video_stream_info.get("r_frame_rate", "0/0")
            if "/" in fr_str:
                num, den = fr_str.split("/")
                fps = float(num) / float(den) if float(den) > 0 else 0.0

    if original_height == 0 or original_width == 0:
        raise ValueError(f"Could not determine video dimensions for {video_path}")

    h_reader = pre_res_height if set_pre_res and pre_res_height > 0 else original_height
    w_reader = pre_res_width if set_pre_res and pre_res_width > 0 else original_width

    source_bits = 8 if is_depth else _pix_fmt_bits(str((video_stream_info or {}).get("pix_fmt", "")))

    # Choose reader implementation
    if is_depth and bit_depth > 8:
        logger.info(f"==> Using high-bit depth path (FFmpegDepthPipeReader) for {bit_depth}-bit source.")
        video_reader = FFmpegDepthPipeReader(
            video_path,
            out_w=w_reader,
            out_h=h_reader,
            bit_depth=bit_depth,
            num_frames=total_frames_available,
            pix_fmt=str(video_stream_info.get("pix_fmt", "")),
            src_size=(original_width, original_height),     # resized on the GPU when it's there
        )
    elif strict_ffmpeg_decode or (high_bit_source and source_bits > 8):
        in_range, in_matrix = "tv", "bt709"
        try:
            cr = str((video_stream_info or {}).get("color_range") or "").lower()
            cs = str((video_stream_info or {}).get("color_space") or "").lower()
            if "full" in cr or cr == "pc":
                in_range = "pc"
            if "2020" in cs:
                in_matrix = "bt2020"
            elif "601" in cs:
                in_matrix = "bt601"
        except Exception:
            pass
        video_reader = FFmpegRGBPipeReader(
            video_path=video_path,
            width=w_reader,
            height=h_reader,
            fps=fps,
            total_frames=total_frames_available,
            in_range=in_range,
            in_matrix=in_matrix,
            bit16=high_bit_source and source_bits > 8,
            # at the source's own size: its YUV as it is, converted in torch
            yuv_src=video_stream_info if (w_reader, h_reader) == (original_width, original_height) else None,
        )
        if video_reader.bit16 or video_reader._yuv:
            logger.info(f"==> {source_bits}-bit source: read by FFmpeg"
                        + (" at 16 bits" if video_reader.bit16 else "")
                        + (f" ({video_reader._pix_fmt}{', NVDEC' if video_reader._nvdec else ''}, "
                           f"converted on the {video_reader._device.upper()})." if video_reader._yuv else "."))
    else:
        video_reader = VideoReader(video_path, ctx=cpu(0), width=w_reader, height=h_reader)
        # Wrap in ResizingDepthReader if Decord didn't resize or if we need specific parity
        if is_depth:
            actual_h, actual_w = video_reader.get_batch([0]).asnumpy().shape[1:3]
            if actual_h != h_reader or actual_w != w_reader:
                video_reader = _ResizingDepthReader(video_reader, out_w=w_reader, out_h=h_reader)

    # Verify final dimensions
    first_shape = video_reader.get_batch([0]).asnumpy().shape
    actual_h, actual_w = first_shape[1:3]

    total_to_process = total_frames_available
    if total_frames_available > 0 and process_length != -1 and process_length < total_frames_available:
        total_to_process = process_length

    return (video_reader, fps, original_height, original_width, actual_h, actual_w, video_stream_info, total_to_process)


_FFPROBE_AVAIL: Optional[bool] = None
_INFO_CACHE: dict = {}


def get_video_stream_info(video_path: str) -> Optional[dict]:
    """Get video stream information using ffprobe.

    Args:
        video_path: Path to the video file

    Returns:
        Dictionary containing stream info (width, height, codec, etc.) or None if unavailable
    """
    global _FFPROBE_AVAIL, _INFO_CACHE
    if not video_path:
        return None
    # Keyed on the file as it is now, and each caller gets its own copy (audit
    # 2026-09-28 #27: keyed on the path alone, a depth map re-rendered under its
    # name kept its old pix_fmt / bit depth all session; and the shared dict
    # let one caller's edit change it for every other).
    try:
        st = os.stat(video_path)
        cache_key = (os.path.normcase(os.path.abspath(video_path)), st.st_mtime_ns, st.st_size)
    except OSError:
        cache_key = None
    if cache_key is not None and cache_key in _INFO_CACHE:
        return dict(_INFO_CACHE[cache_key])

    if _FFPROBE_AVAIL is None:
        try:
            subprocess.run(["ffprobe", "-version"], check=True, capture_output=True, creationflags=_NO_WINDOW)
            _FFPROBE_AVAIL = True
        except Exception:
            _FFPROBE_AVAIL = False
    if not _FFPROBE_AVAIL:
        return None

    try:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,codec_name,profile,pix_fmt,color_range,color_primaries,color_transfer,color_space,r_frame_rate",
            # No side_data= entry (2026-10-27): it made ffprobe decode every frame of the
            # clip -- 3.5 s for 72 frames of 1080p HEVC, minutes for a film -- and only
            # the stream entry below was ever read.
            "-of",
            "json",
            video_path,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True, creationflags=_NO_WINDOW)
        data = json.loads(res.stdout)
        if "streams" in data and data["streams"]:
            info = {k: v for k, v in data["streams"][0].items() if v and v not in ("N/A", "und", "unknown")}
            # ffprobe calls it color_transfer; asking for transfer_characteristics
            # (as this did) returns nothing, so HDR chains came out tagged SDR and
            # HDR was never detected (bug sweep 2026-10-09 #17). The key the
            # encoders read stays transfer_characteristics.
            if info.get("color_transfer"):
                info["transfer_characteristics"] = info["color_transfer"]
            if cache_key is not None:
                _INFO_CACHE[cache_key] = dict(info)
            return info
    except Exception:
        pass
    return None


def read_video_frames_decord(
    video_path: str,
    process_length: int = -1,
    target_fps: float = -1.0,
    set_res_width: Optional[int] = None,
    set_res_height: Optional[int] = None,
    decord_ctx=cpu(0),
    as_uint8: bool = False,
) -> Tuple[np.ndarray, float, int, int, int, int, Optional[dict]]:
    """Read video frames using decord with optional resizing and fps conversion.

    as_uint8: the decoded frames as they are (uint8 0-255), for a caller that
    wants those -- Inpainting V1 turned the float32 0-1 frames straight back
    into uint8, two full float32 copies of the clip for nothing (bug sweep
    2026-10-09, A11).

    Args:
        video_path: Path to the video file
        process_length: Number of frames to process (-1 for all)
        target_fps: Target fps (-1 for original)
        set_res_width: Target width (None for original)
        set_res_height: Target height (None for original)
        decord_ctx: Decord context for reading

    Returns:
        Tuple of (frames as float32 numpy array [T,H,W,C] normalized to 0-1,
                  fps, original_height, original_width, output_height, output_width,
                  stream_info)
    """
    info = get_video_stream_info(video_path)
    temp_reader = VideoReader(video_path, ctx=cpu(0))
    oh, ow = temp_reader.get_batch([0]).shape[1:3]
    del temp_reader
    dw, dh = (set_res_width, set_res_height) if set_res_width and set_res_height else (ow, oh)
    vid = VideoReader(video_path, ctx=decord_ctx, width=dw, height=dh)
    total = len(vid)
    fps = target_fps if target_fps > 0 else vid.get_avg_fps()
    stride = max(round(vid.get_avg_fps() / fps), 1)
    idxs = list(range(0, total, stride))
    if process_length != -1:
        idxs = idxs[:process_length]
    frames = vid.get_batch(idxs).asnumpy()
    if not as_uint8:
        frames = frames.astype("float32")
        frames /= 255.0                  # in place: the same values, one float32 copy
    return frames, fps, oh, ow, frames.shape[1], frames.shape[2], info


def build_encode_cmd(
    input_args: list,
    final_output_mp4_path: str,
    video_stream_info: Optional[dict],
    user_output_crf: Optional[int] = None,
    encoding_options: Optional[dict] = None,
) -> tuple:
    """(ffmpeg command, the .part path it writes): encode_frames_to_mp4's command, its input given
    by `input_args` -- a PNG sequence there, raw frames on stdin for RawFrameEncoder (2026-10-29), the
    rest the same."""
    ffmpeg_cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"] + list(input_args)

    # Determine codec, pixel format, and CRF
    output_codec, output_pix_fmt, crf, output_profile = "libx264", "yuv420p", "23", "main"

    if user_output_crf is not None:
        crf = str(user_output_crf)

    is_hdr = (
        video_stream_info
        and video_stream_info.get("color_primaries") == "bt2020"
        and video_stream_info.get("transfer_characteristics") in ("smpte2084", "arib-std-b67")
    )
    orig_pix = video_stream_info.get("pix_fmt", "") if video_stream_info else ""
    is_high_bit = "10" in orig_pix or "12" in orig_pix or "16" in orig_pix

    if is_hdr or (is_high_bit and video_stream_info.get("codec_name") in ("hevc", "prores", "dnxhd", "dnxhr")):
        output_codec = "hevc_nvenc" if nvenc_available() else "libx265"
        output_pix_fmt = "yuv420p10le"
        output_profile = "main10"
    else:
        output_codec = "h264_nvenc" if nvenc_available() else "libx264"

    # Check for color_tags_mode to force 10-bit if needed
    color_tags_mode = str(video_stream_info.get("color_tags_mode", "")).lower() if video_stream_info else ""
    if color_tags_mode in ("bt.2020 pq", "bt.2020 hlg", "bt.2020"):
        output_codec = "hevc_nvenc" if nvenc_available() else "libx265"
        output_pix_fmt = "yuv420p10le"
        output_profile = "main10"

    # Explicit encoding options (codec / encoder / bit depth / pixel format from the
    # GUI dropdowns) take priority over the auto-detection above. Without this the
    # inpaint stage silently ignores the user's Pixel Format / Bit Depth choices,
    # because everything above only triggers on HDR or 10-bit *source* tags -- which
    # a 10-bit SDR source (or a splatted intermediate) does not carry. Routing the
    # choice through build_encoder_args() keeps inpaint consistent with the splatting
    # and merging stages (same codec/pix_fmt/profile resolution logic).
    override_preset = None
    override_extra_args = None
    if encoding_options:
        from core.common.encoding_utils import build_encoder_args, get_encoding_config_from_dict

        enc_cfg = get_encoding_config_from_dict(encoding_options)
        has_explicit = (
            enc_cfg.get("bit_depth", "Auto") != "Auto"
            or enc_cfg.get("pix_fmt", "Auto") != "Auto"
            or encoding_options.get("codec", "Auto") != "Auto"
            or encoding_options.get("encoder", encoding_options.get("encoding_encoder", "Auto")) not in ("Auto", None)
        )
        if has_explicit:
            # Preserve the HDR / high-bit-source auto-detection performed above:
            # if it already chose a 10-bit pix_fmt, carry that intent into
            # build_encoder_args as force_10bit. Explicit Bit Depth / Pixel Format
            # dropdown choices still override it inside build_encoder_args; only
            # "Auto" defers to this detection. Without this, an HDR source with
            # everything on Auto would be silently downgraded to 8-bit.
            auto_wants_10bit = output_pix_fmt in ("yuv420p10le", "p010le")
            _args = build_encoder_args(
                codec=enc_cfg.get("codec", "H.265"),
                encoder=enc_cfg.get("encoder", "NVENC"),
                quality=enc_cfg.get("quality", "Medium"),
                tune=enc_cfg.get("tune", "None"),
                crf=int(crf) if str(crf).isdigit() else 23,
                force_10bit=auto_wants_10bit,
                bit_depth=enc_cfg.get("bit_depth", "Auto"),
                pix_fmt_override=enc_cfg.get("pix_fmt", "Auto"),
                nvenc_options={
                    "lookahead_enabled": enc_cfg.get("nvenc_lookahead_enabled", False),
                    "lookahead": enc_cfg.get("nvenc_lookahead", 16),
                    "spatial_aq": enc_cfg.get("nvenc_spatial_aq", False),
                    "temporal_aq": enc_cfg.get("nvenc_temporal_aq", False),
                    "aq_strength": enc_cfg.get("nvenc_aq_strength", 8),
                },
            )
            output_codec = _args["codec"]
            output_pix_fmt = _args["pix_fmt"]
            output_profile = _args["profile"]  # may be None (correct for libx265)
            # Use the resolved preset and quality/AQ args instead of the hardcoded
            # "-preset p4 -qp" below. Spatial/temporal AQ matters for dark smooth
            # gradients where constant-QP NVENC otherwise produces visible banding.
            override_preset = _args.get("preset")
            override_extra_args = _args.get("extra_args") or []

    # DNxHR (user, 2026-10-11: Inpainting's Enable DNxHR, off by default, for a DNxHR
    # pipeline after DepthCrafter): its profile and pixel format, no CRF / preset.
    # The caller names the file .mov.
    if encoding_options and encoding_options.get("dnxhr"):
        from core.common.encoding_utils import dnxhr_video_args
        _dnx = dnxhr_video_args(encoding_options.get("dnxhr_profile", ""))
        output_codec, output_profile, output_pix_fmt = "dnxhd", _dnx[3], _dnx[5]
        override_preset, override_extra_args = None, []

    # Build ffmpeg command
    ffmpeg_cmd.extend(["-c:v", output_codec])

    # Add quality args. When the GUI encoding options produced an override,
    # use its resolved preset + extra_args (QP/CRF plus lookahead and AQ flags);
    # otherwise fall back to the legacy hardcoded behavior.
    if override_extra_args is not None:
        if override_preset:
            ffmpeg_cmd.extend(["-preset", override_preset])
        ffmpeg_cmd.extend(override_extra_args)
    elif "nvenc" in output_codec:
        ffmpeg_cmd.extend(["-preset", "p4", "-qp", crf])
    else:
        ffmpeg_cmd.extend(["-crf", crf])

    ffmpeg_cmd.extend(["-pix_fmt", output_pix_fmt])
    if output_profile:
        ffmpeg_cmd.extend(["-profile:v", output_profile])

    if video_stream_info:
        for k, f in [
            ("color_primaries", "-color_primaries"),
            ("transfer_characteristics", "-color_trc"),
            ("color_space", "-colorspace"),
            ("color_range", "-color_range"),
        ]:
            if video_stream_info.get(k):
                ffmpeg_cmd.extend([f, video_stream_info[k]])

    if os.path.splitext(final_output_mp4_path)[1].lower() in (".mp4", ".mov", ".m4v"):
        ffmpeg_cmd.extend(["-movflags", "+write_colr"])

    # Encoded under <final>.part and put in place only when ffmpeg succeeds:
    # a Stop or a failure leaves the previous output, if any, untouched and
    # nothing truncated under the final name (core/common/partial_output).
    encode_path = part_path(final_output_mp4_path)
    ffmpeg_cmd.extend(ffmpeg_output_args(encode_path))
    ffmpeg_cmd = with_color_tags(ffmpeg_cmd)     # primaries / transfer into the file (core.common.color_tags)
    return ffmpeg_cmd, encode_path


def encode_frames_to_mp4(
    temp_png_dir: str,
    final_output_mp4_path: str,
    fps: float,
    total_output_frames: int,
    video_stream_info: Optional[dict],
    stop_event: Optional[threading.Event] = None,
    sidecar_json_data: Optional[dict] = None,
    user_output_crf: Optional[int] = None,
    output_sidecar_ext: str = ".json",
    encoding_options: Optional[dict] = None,
) -> bool:
    """Encode a directory of PNG frames to an MP4 video using FFmpeg.

    Args:
        temp_png_dir: Directory containing PNG frames named %05d.png
        final_output_mp4_path: Path to the output MP4 file
        fps: Output video frame rate
        total_output_frames: Total number of frames to encode
        video_stream_info: Source video stream info for matching color space
        stop_event: Optional threading event to cancel encoding
        sidecar_json_data: Optional data to save to a JSON sidecar file
        user_output_crf: Optional override for CRF (quality) setting
        output_sidecar_ext: Extension for the sidecar file

    Returns:
        True if encoding succeeded, False otherwise
    """
    if total_output_frames == 0:
        logger.warning(f"No frames to encode for {os.path.basename(final_output_mp4_path)}. Skipping.")
        if os.path.exists(temp_png_dir):
            shutil.rmtree(temp_png_dir)
        return False

    ffmpeg_cmd, encode_path = build_encode_cmd(
        ["-framerate", str(fps), "-i", os.path.join(temp_png_dir, "%05d.png")],
        final_output_mp4_path, video_stream_info, user_output_crf, encoding_options,
    )

    process = None
    try:
        process = subprocess.Popen(
            ffmpeg_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
            creationflags=_NO_WINDOW,
        )
        stderr_drain = _StderrDrain(process, f"ffmpeg encode {os.path.basename(final_output_mp4_path)}")
        while process.poll() is None:
            if stop_event and stop_event.is_set():
                reap(process)           # waited for: the part is closed before it goes
                discard(encode_path)
                return False
            time.sleep(0.1)
        if process.returncode != 0:
            # Previously this returned a bare False and threw away ffmpeg's
            # explanation, which was sitting unread in the pipe.
            stderr_drain.log_if(True)
            discard(encode_path)
            return False
        if not publish(encode_path, final_output_mp4_path):
            discard(encode_path)
            return False
    except Exception as e:
        logger.error(f"Encoding failed: {e}")
        reap(process)
        discard(encode_path)
        return False
    finally:
        # ignore_errors: a frame still open elsewhere must not turn a
        # finished (or stopped) encode into a crash
        if os.path.exists(temp_png_dir):
            shutil.rmtree(temp_png_dir, ignore_errors=True)

    if sidecar_json_data:
        path = f"{os.path.splitext(final_output_mp4_path)[0]}{output_sidecar_ext}"
        from core.common.atomic_json import write_json_atomic  # whole or not at all
        write_json_atomic(path, sidecar_json_data, indent=4)
    return True

class RawFrameEncoder:
    """ffmpeg encoding 16-bit RGB frames written to its stdin: the command encode_frames_to_mp4 builds for
    a PNG sequence (build_encode_cmd), the frames never on disk. A user's Inpainting patch (2026-10-29):
    a 778-frame Hi-Res finalize spent 4.2 min writing 16-bit PNGs and reading them back. PNG is lossless,
    so ffmpeg gets the same samples either way. Encoded under <final>.part: finish() puts it in place,
    abort() throws it away -- as the PNG encode does."""

    def __init__(self, width: int, height: int, final_output_path: str, fps: float,
                 video_stream_info: Optional[dict] = None, user_output_crf: Optional[int] = None,
                 encoding_options: Optional[dict] = None):
        self.width, self.height = int(width), int(height)
        self.final_output_path = final_output_path
        self.frames = 0
        input_args = ["-f", "rawvideo", "-pix_fmt", "rgb48le", "-s", f"{self.width}x{self.height}",
                      "-framerate", str(fps), "-i", "pipe:0"]
        self.cmd, self.encode_path = build_encode_cmd(input_args, final_output_path, video_stream_info,
                                                      user_output_crf, encoding_options)
        from core.common.frame_pipe import popen_frames_in
        self._proc = popen_frames_in(self.cmd)
        self._drain = _StderrDrain(self._proc, f"ffmpeg encode {os.path.basename(final_output_path)}")

    def write(self, frame: np.ndarray) -> None:
        """One [H, W, 3] uint16 RGB frame."""
        if frame.shape != (self.height, self.width, 3) or frame.dtype != np.uint16:
            raise ValueError(f"expected a {self.height}x{self.width}x3 uint16 frame, got {frame.shape} {frame.dtype}")
        a = np.ascontiguousarray(frame, dtype="<u2")
        try:
            self._proc.stdin.write(memoryview(a).cast("B"))
        except OSError:                  # ffmpeg gave up: say why
            self._drain.log_if(True)
            raise
        self.frames += 1

    def finish(self, stop_event: Optional[threading.Event] = None) -> bool:
        """Close the input and wait for ffmpeg; True once the file is in place under its own name."""
        try:
            self._proc.stdin.close()
        except OSError:
            pass
        while self._proc.poll() is None:
            if stop_event is not None and stop_event.is_set():
                self.abort()
                return False
            time.sleep(0.05)
        if self._proc.returncode != 0 or self.frames == 0:
            self._drain.log_if(self._proc.returncode != 0)
            discard(self.encode_path)
            return False
        if not publish(self.encode_path, self.final_output_path):
            discard(self.encode_path)
            return False
        return True

    def abort(self) -> None:
        """Stop ffmpeg and throw away what it wrote."""
        try:
            self._proc.stdin.close()
        except OSError:
            pass
        reap(self._proc)
        discard(self.encode_path)


def start_ffmpeg_pipe_process(
    content_width: int,
    content_height: int,
    final_output_mp4_path: str,
    fps: float,
    video_stream_info: Optional[dict] = None,
    output_format_str: str = "",
    user_output_crf: Optional[int] = None,
    pad_to_16_9: bool = False,
    debug_label: Optional[str] = None,
    encoding_options: Optional[dict] = None,
    comment: Optional[str] = None,
) -> Optional[subprocess.Popen]:
    """Start an FFmpeg process that reads raw video from a pipe.

    Args:
        content_width: Width of input frames
        content_height: Height of input frames
        final_output_mp4_path: Output path for the video
        fps: Frame rate
        video_stream_info: Source video stream info (may contain color_tags_mode)
        output_format_str: Optional format string
        user_output_crf: Optional CRF override
        pad_to_16_9: Letterbox/pillarbox the output to a 16:9 frame
        debug_label: Optional label for logging
        encoding_options: Optional dict with encoding settings:
            - codec: "Auto", "H.264", "H.265"
            - encoding_encoder: "Auto", "Force CPU"
            - encoding_quality: quality preset
            - encoding_tune: tune option
            - output_crf: CRF value
            - nvenc_lookahead_enabled, nvenc_lookahead, etc.
        comment: Optional text for the file's comment tag -- how it was made (Splatting's grids
            record their warp and mask, 2026-10-22: a stage that needs one kind can tell)

    Returns:
        The subprocess.Popen instance
    """
    from core.common.encoding_utils import build_encoder_args, get_encoding_config_from_dict

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-vcodec",
        "rawvideo",
        "-s",
        f"{content_width}x{content_height}",
        "-pix_fmt",
        "bgr48le",
        "-r",
        str(fps),
        "-i",
        "-",
    ]

    # Determine if we need 10-bit output based on color tags
    color_tags_mode = str(video_stream_info.get("color_tags_mode", "")).lower() if video_stream_info else ""
    # Also check encoding_options for color_tags if not in video_stream_info
    if not color_tags_mode and encoding_options and "color_tags" in encoding_options:
        color_tags_mode = str(encoding_options["color_tags"]).lower()
    force_10bit = color_tags_mode in ("bt.2020 pq", "bt.2020 hlg", "bt.2020")

    # An explicit Bit Depth selection (Auto / 8-bit / 10-bit (Main10)) overrides
    # the HDR-tag-driven auto-detection above. This is what lets 10-bit SDR
    # sources (which never trigger the HDR check) still get forced to 10-bit.
    bit_depth_choice = str(encoding_options.get("bit_depth", "Auto")) if encoding_options else "Auto"
    if bit_depth_choice == "10-bit (Main10)":
        force_10bit = True
    elif bit_depth_choice == "8-bit":
        force_10bit = False

    # Build encoding arguments from encoding_options if provided
    if encoding_options:
        enc_config = get_encoding_config_from_dict(encoding_options)
        encoder_args = build_encoder_args(
            codec=enc_config.get("codec", "Auto"),
            encoder=enc_config.get("encoder", "Auto"),
            quality=enc_config.get("quality", "Medium"),
            tune=enc_config.get("tune", "None"),
            crf=int(enc_config.get("crf", 23)) if user_output_crf is None else user_output_crf,
            force_10bit=force_10bit,
            bit_depth=enc_config.get("bit_depth", "Auto"),
            pix_fmt_override=enc_config.get("pix_fmt", "Auto"),
            nvenc_options={
                "lookahead_enabled": enc_config.get("nvenc_lookahead_enabled", False),
                "lookahead": enc_config.get("nvenc_lookahead", 16),
                "spatial_aq": enc_config.get("nvenc_spatial_aq", False),
                "temporal_aq": enc_config.get("nvenc_temporal_aq", False),
                "aq_strength": enc_config.get("nvenc_aq_strength", 8),
            },
            # always the whole dict: build_encoder_args reads each option on its
            # own, and passing None without Lookahead dropped Spatial / Temporal
            # AQ from Splatting's and Merging's encodes (audit 2026-09-28 #19)
        )
        codec = encoder_args["codec"]
        crf = str(encoder_args.get("crf", enc_config.get("crf", 23)))
        preset = encoder_args["preset"]
        tune_flag = encoder_args["tune"]
        pix = encoder_args["pix_fmt"]
        profile = encoder_args.get("profile")
        extra_args = encoder_args.get("extra_args", [])
    else:
        # Fallback to old behavior (simple auto-detection)
        codec, crf = "libx264", "23"
        # nvenc_available(), not CUDA_AVAILABLE: imported by value that was
        # False for ever, so this never took NVENC (Studio audit 2026-09-28)
        if nvenc_available():
            codec = "h264_nvenc"
        if user_output_crf is not None:
            crf = str(user_output_crf)
        pix = "yuv420p10le" if force_10bit else "yuv420p"
        preset, tune_flag, extra_args = None, None, []
        profile = "main10" if (force_10bit and "hevc" in codec) else None

    # Force the RGB->YUV conversion matrix explicitly. Without this, swscale
    # defaults to BT.601 for the bgr48le -> YUV conversion while the metadata
    # below tags the stream as BT.709 -- so every player and DaVinci decode the
    # pixels with the wrong matrix (dark / color-shifted output). BT.2020 content
    # (HDR-tagged sources) gets the bt2020 matrix instead.
    out_matrix = "bt2020" if color_tags_mode.startswith("bt.2020") else "bt709"
    # pad_to_16_9 was previously accepted and ignored here -- the GUI checkbox
    # saved it and passed it in, and nothing ever built a filter from it.
    cmd.extend(["-vf", _build_output_filters(content_width, content_height, out_matrix, pad_to_16_9)])

    cmd.extend(["-c:v", codec])

    # Add preset and tune
    if preset:
        if "nvenc" in codec:
            cmd.extend(["-preset", preset])
        else:
            cmd.extend(["-preset", preset])
    elif "nvenc" in codec:
        cmd.extend(["-preset", "p4"])

    # Add CRF/QP
    if extra_args:
        # Handle nvenc args which are in extra_args
        cmd.extend(extra_args)
    else:
        if "nvenc" in codec:
            cmd.extend(["-qp", crf])
        else:
            cmd.extend(["-crf", crf])

    cmd.extend(["-pix_fmt", pix])

    # Explicit Main10 profile for 10-bit HEVC output. Some players/decoders
    # are more reliable about recognizing 10-bit content when the profile is
    # stated explicitly rather than implied from pix_fmt alone.
    if profile and "hevc" in codec:
        cmd.extend(["-profile:v", profile])

    # Add color metadata. Previously this only happened for HDR-triggered 10-bit
    # output, leaving SDR files untagged (transfer/primaries "unknown"), which
    # invites players and DaVinci to guess. Copy source tags when present and
    # fall back to bt709/tv defaults matching the explicit conversion above.
    _tag_defaults = {
        "color_primaries": "bt2020" if out_matrix == "bt2020" else "bt709",
        "transfer_characteristics": None,  # never guess HDR transfer; copy-only
        "color_space": out_matrix if out_matrix == "bt2020" else "bt709",
        "color_range": "tv",
    }
    if not (color_tags_mode.startswith("bt.2020")):
        _tag_defaults["transfer_characteristics"] = "bt709"
    for k, f in [
        ("color_primaries", "-color_primaries"),
        ("transfer_characteristics", "-color_trc"),
        ("color_space", "-colorspace"),
        ("color_range", "-color_range"),
    ]:
        val = (video_stream_info.get(k) if video_stream_info else None) or _tag_defaults.get(k)
        if val:
            cmd.extend([f, val])

    if comment:
        cmd.extend(["-metadata", f"comment={comment}"])
    cmd.extend(["-movflags", "+write_colr", *ffmpeg_output_args(final_output_mp4_path)])
    cmd = with_color_tags(cmd)     # primaries / transfer into the file (core.common.color_tags)
    housekeeping_log.info(f"Starting FFmpeg pipe: {' '.join(cmd)}")
    return subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=_NO_WINDOW)


def _build_output_filters(
    content_width: int, content_height: int, out_matrix: str, pad_to_16_9: bool
) -> str:
    """Build the -vf chain shared by both encoder paths.

    Always starts with the colour-matrix conversion; appends a pad filter when
    16:9 padding is requested and the frame is not already 16:9.

    One chain, not two -vf arguments: ffmpeg honours only the last -vf given,
    so appending a second one would silently discard the colour conversion and
    produce dark / colour-shifted output.
    """
    filters = [f"scale=out_color_matrix={out_matrix}:out_range=tv"]
    if pad_to_16_9 and content_width > 0 and content_height > 0:
        target_w, target_h = content_width, round(content_width * 9 / 16)
        if target_h < content_height:
            # Taller than 16:9 -- pillarbox by widening, never crop.
            target_w, target_h = round(content_height * 16 / 9), content_height
        target_w += target_w % 2   # even dimensions required by chroma subsampling
        target_h += target_h % 2
        if (target_w, target_h) != (content_width, content_height):
            filters.append(f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:color=black")
            logger.info(
                "Padding output to 16:9: %dx%d -> %dx%d",
                content_width, content_height, target_w, target_h,
            )
    return ",".join(filters)


def start_ffmpeg_pipe_process_dnxhr(
    content_width: int,
    content_height: int,
    final_output_mov_path: str,
    fps: float,
    dnxhr_profile: str = "HQX",
    pad_to_16_9: bool = False,
    comment: Optional[str] = None,
) -> Optional[subprocess.Popen]:
    """Start an FFmpeg process for high-quality DNxHR output via pipe.

    Args:
        content_width: Width of input frames
        content_height: Height of input frames
        final_output_mov_path: Output path for the MOV file
        fps: Frame rate
        dnxhr_profile: DNxHR profile (SQ, HQ, HQX, 444)
        pad_to_16_9: Letterbox/pillarbox the output to a 16:9 frame
        comment: Optional text for the file's comment tag (start_ffmpeg_pipe_process's)

    Returns:
        The subprocess.Popen instance
    """
    _key = dnxhr_profile.strip().upper().split(" ")[0]  # "SQ"/"HQ"/"HQX"/"444"
    prof = {"SQ": "dnxhr_sq", "HQ": "dnxhr_hq", "HQX": "dnxhr_hqx", "444": "dnxhr_444"}.get(
        _key, "dnxhr_hqx"
    )
    # SQ and HQ are 8-bit 4:2:2 profiles; HQX is 10-bit 4:2:2; 444 is 10-bit 4:4:4.
    if prof == "dnxhr_444":
        pix = "yuv444p10le"
    elif prof in ("dnxhr_sq", "dnxhr_hq"):
        pix = "yuv422p"
    else:
        pix = "yuv422p10le"
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-f",
        "rawvideo",
        "-vcodec",
        "rawvideo",
        "-s",
        f"{content_width}x{content_height}",
        "-pix_fmt",
        "bgr48le",
        "-r",
        str(fps),
        "-i",
        "-",
        # Force the RGB->YUV conversion to use the BT.709 matrix with TV (limited)
        # range. Without this, swscale silently defaults to BT.601, producing
        # pixels that decode darker / color-shifted when players and DaVinci
        # interpret the HD file as BT.709 (which they always do).
        "-vf",
        _build_output_filters(content_width, content_height, "bt709", pad_to_16_9),
        "-c:v",
        "dnxhd",
        "-profile:v",
        prof,
        "-pix_fmt",
        pix,
        # Tag the stream explicitly and write the MOV 'colr' atom so QuickTime
        # and DaVinci read the intended interpretation instead of guessing
        # (untagged MOVs trigger Resolve's Rec.709-A gamma assumption -> shift).
        "-colorspace",
        "bt709",
        "-color_primaries",
        "bt709",
        "-color_trc",
        "bt709",
        "-color_range",
        "tv",
        "-movflags",
        "write_colr",
        "-an",
        *(["-metadata", f"comment={comment}"] if comment else []),
        *ffmpeg_output_args(final_output_mov_path),   # a .part name: see partial_output
    ]
    cmd = with_color_tags(cmd)     # primaries / transfer into the file (core.common.color_tags)
    housekeeping_log.info(f"Starting DNxHR pipe: {' '.join(cmd)}")
    return subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=_NO_WINDOW)


def resolve_frame_break_mask(splatted_or_output_path: str) -> str:
    """Sibling path for a Frame Break protrusion mask: <stem>_framebreak.mkv.

    Shared naming convention between the two stages that need to agree on it
    without importing each other: RenderProcessor.render_video (splatting
    stage) writes this file next to its main splatted output when a clip's
    sidecar has frame_break_bars > 0; the merging stage reads it back (via
    FFmpegGrayPipeReader) when compositing that clip's final frame, using
    this same function on whichever splatted/grid file it already resolved.
    Same role as resolve_subtitle_mask, opposite direction (that one is read
    during splatting; this one is written during splatting and read later
    during merging).
    """
    return os.path.splitext(splatted_or_output_path)[0] + "_framebreak.mkv"


def start_ffmpeg_pipe_process_mask(
    content_width: int,
    content_height: int,
    output_path: str,
    fps: float,
) -> Optional[subprocess.Popen]:
    """Start an FFmpeg process that writes a single-channel (grayscale) mask
    video from a raw gray8 pipe, encoded as FFV1 (mathematically lossless,
    intra-only) in a Matroska container.

    Used for auxiliary HARD-cutout masks such as Frame Break's protrusion
    mask, where downstream compositing does a binary select on the value
    (kept vs. black) rather than a blend -- any lossy re-encode risks
    softening the edge exactly where a clean, non-fading cutout is required.
    FFV1 sidesteps that entirely (byte-exact roundtrip) rather than relying
    on a lossy codec's "visually lossless" settings. Pairs with
    FFmpegGrayPipeReader for reading it back.
    """
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-vcodec",
        "rawvideo",
        "-s",
        f"{content_width}x{content_height}",
        "-pix_fmt",
        "gray",
        "-r",
        str(fps),
        "-i",
        "-",
        "-c:v",
        "ffv1",
        "-pix_fmt",
        "gray",
        "-an",
        *ffmpeg_output_args(output_path),   # a .part name: see partial_output
    ]
    housekeeping_log.info(f"Starting lossless Frame Break mask pipe: {' '.join(cmd)}")
    return subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=_NO_WINDOW)


class FFmpegGrayPipeReader:
    """Sequential FFmpeg-backed reader for single-channel gray8 mask videos
    (e.g. those written by start_ffmpeg_pipe_process_mask).

    Forward-sequential by design, mirroring FFmpegRGBPipeReader: a request
    starting before the current position restarts the process (FFV1 is
    intra-only, so `-ss` seeking lands on an exact frame). A request starting
    after the current position discards frames until it catches up. get_batch
    returns a _NumpyBatch of shape [N, H, W] uint8 -- 0 or 255, no in-between
    values, since the source encode is lossless.
    """

    def __init__(self, path: str, width: int, height: int, fps: float, total_frames: int = -1):
        self.path = path
        self.width = int(width)
        self.height = int(height)
        self.fps = float(fps) if fps else 0.0
        self.total_frames = int(total_frames) if total_frames is not None else -1
        self._frame_size = self.width * self.height
        self._proc: Optional[subprocess.Popen] = None
        self._next_index = 0

    def __len__(self):
        return self.total_frames if self.total_frames >= 0 else 0

    def _build_cmd(self, start_frame: int = 0):
        args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin"]
        if start_frame and self.fps:
            args += ["-ss", f"{start_frame / self.fps:.6f}"]
        args += [
            "-i", self.path, "-an", "-sn", "-dn",
            "-vf", "format=gray",
            "-f", "rawvideo", "-pix_fmt", "gray",
            "-fps_mode", "passthrough", "-",
        ]
        return args

    def _ensure_process(self, start_frame: int):
        if self._proc is not None:
            return
        cmd = self._build_cmd(start_frame=start_frame)
        self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=_NO_WINDOW)
        self._stderr = _StderrDrain(self._proc, "ffmpeg gray decode")
        self._next_index = start_frame

    def _restart(self, start_frame: int):
        self.close()
        self._ensure_process(start_frame=start_frame)

    def close(self):
        try:
            _stop_pipe_process(self._proc)  # reaped, so the file is free (#26)
        finally:
            self._proc = None

    def __del__(self):
        self.close()

    def _read_exact(self, nbytes: int) -> bytes:
        if self._proc is None or self._proc.stdout is None:
            self._ensure_process(self._next_index)
        buf = b""
        while len(buf) < nbytes:
            chunk = self._proc.stdout.read(nbytes - len(buf))
            if not chunk:
                break
            buf += chunk
        return buf

    def get_batch(self, indices):
        indices = list(indices)
        if not indices:
            return _NumpyBatch(np.zeros((0, self.height, self.width), dtype=np.uint8))

        first = int(indices[0])
        if self._proc is None:
            self._ensure_process(start_frame=first)
        elif first < self._next_index:
            self._restart(start_frame=first)
        elif first > self._next_index:
            to_skip = first - self._next_index
            _ = self._read_exact(to_skip * self._frame_size)
            self._next_index = first

        n = len(indices)
        expected = self._frame_size * n
        buf = self._read_exact(expected)
        if len(buf) != expected:
            # Short read (EOF, or a mask that's a few frames shorter than the
            # clip it's paired with) -- pad with 255 (fully kept/no bar) so a
            # slightly short mask never blacks out the tail of a clip instead
            # of raising and aborting the whole merge.
            buf = buf + b"\xff" * (expected - len(buf))
        arr = np.frombuffer(buf, dtype=np.uint8).reshape(n, self.height, self.width).copy()
        self._next_index = first + n
        return _NumpyBatch(arr)


def reverse_frames(frames: torch.Tensor) -> torch.Tensor:
    """Reverse the temporal order of video frames.

    Args:
        frames: Tensor of shape [T, C, H, W] or [T, H, W, C]

    Returns:
        Reversed frames tensor
    """
    return torch.flip(frames, dims=[0])


def reverse_frames_numpy(frames: np.ndarray) -> np.ndarray:
    """Reverse the temporal order of video frames (numpy).

    Args:
        frames: Numpy array of shape [T, H, W, C] or [T, H, W]

    Returns:
        Reversed frames array
    """
    return np.flip(frames, axis=0)
