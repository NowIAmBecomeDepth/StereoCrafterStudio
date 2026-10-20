"""fast_decode.py -- the preview's frames without an ffmpeg per frame.

v1 decodes each preview frame with a fresh ffmpeg process: the 10-bit depth
through ``-ss <t> -frames:v 1`` (video_previewer._read_depth_frame_ffmpeg),
and -- with "ffmpeg" (strict decode) ticked -- the source through
``select='eq(n,idx)'`` (FFmpegRGBSingleFrameReader), which decodes the clip
from its first frame every time. At 1080p the depth alone is ~0.14 s a frame.

Here one ffmpeg stays open per stream and runs forward:

* the same frame, bit for bit, as v1 asks for it: the depth stream starts
  with v1's own ``-ss`` seek and filter, the source stream with v1's own
  filter chain behind ``select='gte(n,idx)'`` -- so frame k after the start
  is the frame v1's per-frame decode of idx+k returns (constant frame rate,
  which is what the pipeline writes; the preview tests check it);
* a read-ahead thread keeps up to AHEAD frames decoded, so stepping and
  playback find the next frame waiting;
* a jump backward, or far forward, restarts the process at the new frame.

``FrameCache`` is the decoded-frame cache the preview keeps per clip:
changing a slider re-renders the same frame without decoding it again.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from collections import OrderedDict
from typing import Callable, List, Optional

import numpy as np

logger = logging.getLogger("splatting.v2.preview")

#: Frames decoded ahead of the one asked for.
AHEAD = 8
#: A jump forward by up to this many frames reads through instead of restarting.
READ_THROUGH = 48

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class FFmpegStream:
    """One ffmpeg decoding forward from a start frame, with read-ahead.

    ``build_cmd(start)`` returns the command whose FIRST output frame is
    frame ``start``; every frame is ``frame_bytes`` of raw video.
    """

    def __init__(self, build_cmd: Callable[[int], List[str]], frame_bytes: int, total: int = -1):
        self._build_cmd = build_cmd
        self.timed_out = False        # the last read() ran out of time (rather than failing)
        self.seek_fps = 0.0           # rgb_stream: seek with -ss at this rate (0: decode from frame 0)
        self._frame_bytes = int(frame_bytes)
        self._total = int(total)
        self._cv = threading.Condition()
        self._proc: Optional[subprocess.Popen] = None
        self._gen = 0                 # bumped on every restart / close
        self._next = 0                # the frame the process produces next
        self._ahead: "OrderedDict[int, bytes]" = OrderedDict()
        self._failed = False          # the current process ended early
        self._thread: Optional[threading.Thread] = None
        self._closed = False

    # ---- public ------------------------------------------------------------

    def read(self, idx: int, timeout: float = 30.0) -> Optional[bytes]:
        idx = int(idx)
        with self._cv:
            self.timed_out = False
            if self._closed:
                return None
            if self._total > 0 and idx >= self._total:
                return None          # past the end: not an ffmpeg run over the whole clip to learn it
            if idx in self._ahead:
                return self._take(idx)
            behind = self._proc is None or idx < self._next or idx > self._next + READ_THROUGH
            if behind or self._failed:
                self._restart(idx)
            self._want = idx
            self._cv.notify_all()
            gen = self._gen
            ok = self._cv.wait_for(lambda: idx in self._ahead or self._failed or self._gen != gen, timeout)
            if idx in self._ahead:
                return self._take(idx)
            if not ok:
                self.timed_out = True
                logger.warning("Preview decode timed out at frame %d", idx)
            return None

    def close(self) -> None:
        with self._cv:
            self._closed = True
            self._stop_proc()
            self._ahead.clear()
            self._cv.notify_all()

    # ---- inside -------------------------------------------------------------

    def _take(self, idx: int) -> bytes:
        data = self._ahead.pop(idx)
        for k in [k for k in self._ahead if k < idx]:
            del self._ahead[k]
        self._want = idx + 1
        self._cv.notify_all()        # room for the read-ahead
        return data

    def _stop_proc(self) -> None:
        self._gen += 1
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                if proc.stdout:
                    proc.stdout.close()
            except Exception:
                pass

    def _restart(self, start: int) -> None:
        self._stop_proc()
        self._ahead.clear()
        self._failed = False
        self._next = start
        self._want = start
        self._proc = subprocess.Popen(self._build_cmd(start), stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                      creationflags=_NO_WINDOW)
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._pump, name="preview-decode", daemon=True)
            self._thread.start()

    def _pump(self) -> None:
        while True:
            with self._cv:
                # Wait until there is a live process and room ahead of the reader.
                self._cv.wait_for(lambda: self._closed or (
                    self._proc is not None and not self._failed
                    and self._next <= getattr(self, "_want", self._next) + AHEAD))
                if self._closed:
                    return
                proc, gen, frame = self._proc, self._gen, self._next
            data = self._read_exact(proc, self._frame_bytes)
            with self._cv:
                if gen != self._gen:
                    continue                      # restarted meanwhile: drop it
                if data is None:
                    self._failed = True
                else:
                    self._ahead[frame] = data
                    self._next = frame + 1
                self._cv.notify_all()

    @staticmethod
    def _read_exact(proc, n: int) -> Optional[bytes]:
        try:
            buf = bytearray()
            while len(buf) < n:
                chunk = proc.stdout.read(n - len(buf))
                if not chunk:
                    return None
                buf += chunk
            return bytes(buf)
        except Exception:
            return None


# ---- the two streams the preview uses ---------------------------------------


def depth_stream(path: str, width: int, height: int, fps: float, vf: str, total: int = -1) -> FFmpegStream:
    """v1 _read_depth_frame_ffmpeg's decode, kept open: its seek, its filter."""
    def cmd(start: int) -> List[str]:
        t = (float(start) / fps) if fps and fps > 0 else 0.0
        return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{t:.6f}", "-i", path,
                "-an", "-sn", "-dn", "-vf", vf, "-fps_mode", "passthrough", "-f", "rawvideo", "pipe:1"]
    return FFmpegStream(cmd, int(width) * int(height) * 2, total)


def rgb_stream(path: str, width: int, height: int, in_range: str, in_matrix: str, strict: bool = True,
               total: int = -1) -> FFmpegStream:
    """FFmpegRGBSingleFrameReader's decode, kept open: its scale/format chain
    behind a select that starts the output at the frame asked for."""
    def cmd(start: int) -> List[str]:
        if strict:
            chain = (f"scale={width}:{height}:flags=bicubic:in_range={in_range}:out_range={in_range}:"
                     f"in_color_matrix={in_matrix}:out_color_matrix={in_matrix},format=rgb24")
        else:
            chain = f"scale={width}:{height}:flags=bicubic,format=rgb24"
        fps = float(stream.seek_fps or 0.0)
        if start > 0 and fps > 0:
            # a seek, half a frame early, so the first frame out is `start` (as depth_stream);
            # `select` alone decoded the clip from frame 0 on every far jump, and a long clip
            # outran the 30 s wait twice over (bug sweep 2026-10-09, A6)
            seek, vf = ["-ss", f"{max(0.0, (start - 0.5) / fps):.6f}"], chain
        else:
            seek, vf = [], f"select='gte(n\\,{int(start)})',{chain}"
        return ["ffmpeg", "-v", "error", "-nostdin", *seek, "-i", path, "-an", "-sn", "-dn", "-vf", vf,
                "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    stream = FFmpegStream(cmd, int(width) * int(height) * 3, total)
    return stream


class _Batch:
    """What decord's get_batch returns, as far as the preview uses it."""

    def __init__(self, arr: np.ndarray):
        self._arr = arr

    def asnumpy(self) -> np.ndarray:
        return self._arr


class StreamRGBReader:
    """Drop-in for FFmpegRGBSingleFrameReader over an FFmpegStream."""

    def __init__(self, original, stream_factory):
        self._orig = original
        self.width, self.height = int(original.width), int(original.height)
        try:
            self._fps = float(original.get_avg_fps() or 0.0)
        except Exception:
            self._fps = 0.0
        self._strict = stream_factory(True)
        self._strict.seek_fps = self._fps
        self._fallback_factory = stream_factory
        self._stream = self._strict

    def __len__(self):
        return len(self._orig)

    def get_avg_fps(self):
        return self._orig.get_avg_fps()

    def get_batch(self, indices):
        frames = np.empty((len(indices), self.height, self.width, 3), dtype=np.uint8)
        n = len(self)
        for j, idx in enumerate(indices):
            if 0 < n <= int(idx):
                return self._orig.get_batch(indices)      # past the end: decord says what is there
            data = self._stream.read(int(idx))
            if data is None and self._stream is self._strict and not self._strict.timed_out:
                # v1 falls back to plain scale when the strict params fail -- fail, not run
                # slow: a timeout made the plain chain (and its colour) stick for the clip
                self._stream = self._fallback_factory(False)
                self._stream.seek_fps = self._fps
                data = self._stream.read(int(idx))
            if data is None:
                return self._orig.get_batch(indices)      # v1's own path, for this read
            frames[j] = np.frombuffer(data, dtype=np.uint8).reshape(self.height, self.width, 3)
        return _Batch(frames)

    def close(self):
        for s in {id(self._strict): self._strict, id(self._stream): self._stream}.values():
            s.close()

    def __getattr__(self, name):
        return getattr(self._orig, name)


class FrameCache:
    """Decoded frames by (source key, frame), newest kept (LRU by bytes)."""

    def __init__(self, max_bytes: int = 768 * 1024 * 1024):
        self._max = int(max_bytes)
        self._items: "OrderedDict[tuple, np.ndarray]" = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    def get(self, key) -> Optional[np.ndarray]:
        with self._lock:
            arr = self._items.get(key)
            if arr is not None:
                self._items.move_to_end(key)
            return arr

    def put(self, key, arr: np.ndarray) -> None:
        with self._lock:
            old = self._items.pop(key, None)
            if old is not None:
                self._bytes -= old.nbytes
            self._items[key] = arr
            self._bytes += arr.nbytes
            while self._bytes > self._max and len(self._items) > 1:
                _k, dropped = self._items.popitem(last=False)
                self._bytes -= dropped.nbytes

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._bytes = 0


class CachedReader:
    """A decord (or stream) reader whose single-frame reads are cached."""

    def __init__(self, reader, key: str, cache: FrameCache):
        self._reader = reader
        self._key = key
        self._cache = cache

    def __len__(self):
        return len(self._reader)

    def __getitem__(self, i):
        return self._reader[i]

    def get_avg_fps(self):
        return self._reader.get_avg_fps()

    def get_batch(self, indices):
        indices = list(indices)
        if len(indices) == 1:
            k = (self._key, int(indices[0]))
            arr = self._cache.get(k)
            if arr is None:
                arr = self._reader.get_batch(indices).asnumpy()
                self._cache.put(k, arr)
            return _Batch(arr)
        return self._reader.get_batch(indices)

    def close(self):
        close = getattr(self._reader, "close", None)
        if close is not None:
            close()

    def __getattr__(self, name):
        return getattr(self._reader, name)
