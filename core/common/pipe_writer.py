"""pipe_writer.py -- feed an encoder's stdin from its own thread.

Splatting and Merging used to write each frame to ffmpeg on the render
thread, after converting it there: the GPU (or the CPU blend) waited for the
whole conversion and write before starting the next batch (audit 2026-09-28
O1 / O3). A PipeWriter takes the frames and does both on its own thread.

    writer = PipeWriter(ffmpeg_process)
    writer.put(uint16_frame)                  # bytes-like: written as is
    writer.put(float_frame, convert=to_bgr16) # converted on the writer thread
    writer.close()                            # all written (abandon=True: drop what waits)

A bounded queue keeps at most ``depth`` frames waiting. A conversion or write
error is kept and raised by the next put() / close(), so a dead encoder still
fails the clip.

An encoder that stays alive but stops reading (an NVENC or driver hang, a
DNxHR finalize that stalls) blocks the writer thread inside its write. put()
and close() then wait at most their timeout and raise ``EncoderStalled``
(bug sweep 2026-10-09, A11: the render's close waited for ever, before the
code that kills a stuck ffmpeg). Ending the process frees the write; the
caller does that, then closes again.
"""

from __future__ import annotations

import queue
import threading
from typing import Any, Callable, Optional

import numpy as np


class EncoderStalled(TimeoutError):
    """The encoder stopped reading: a write has been stuck past the timeout."""


#: How long one frame may wait for room in the queue: far past any real
#: encoder's frame time, so only a stalled one reaches it.
PUT_TIMEOUT = 300.0


class PipeWriter:
    def __init__(self, process: Any, depth: int = 3, name: str = "pipe-writer"):
        self._process = process
        self._queue: "queue.Queue" = queue.Queue(maxsize=depth)
        self._error: Optional[BaseException] = None
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                return
            if self._error is not None:
                continue            # drain after a failure; nothing more is written
            frame, convert = item
            try:
                data = convert(frame) if convert is not None else frame
                if isinstance(data, np.ndarray):
                    data = memoryview(np.ascontiguousarray(data)).cast("B")
                self._process.stdin.write(data)
            except BaseException as e:  # BrokenPipe when the encoder died, etc.
                self._error = e

    def put(self, frame: Any, convert: Optional[Callable[[Any], Any]] = None,
            timeout: Optional[float] = PUT_TIMEOUT) -> None:
        if self._error is not None:
            raise self._error
        try:
            self._queue.put((frame, convert), timeout=timeout)
        except queue.Full:
            raise EncoderStalled(f"{self._thread.name}: no frame written for {timeout:g}s; "
                                 "the encoder stopped reading") from None

    def close(self, abandon: bool = False, timeout: Optional[float] = None) -> None:
        """Wait until everything queued is written (``abandon``: drop what is
        still waiting -- a stopped or failed clip's output is discarded).
        ``timeout``: raise EncoderStalled if the writer is still stuck after it
        (None: wait for ever, as before)."""
        if abandon:
            try:
                while True:
                    self._queue.get_nowait()
            except queue.Empty:
                pass
        try:
            self._queue.put(None, timeout=timeout)
        except queue.Full:
            raise EncoderStalled(f"{self._thread.name}: still writing after {timeout:g}s; "
                                 "the encoder stopped reading") from None
        self._thread.join(timeout)
        if self._thread.is_alive():
            raise EncoderStalled(f"{self._thread.name}: still writing after {timeout:g}s; "
                                 "the encoder stopped reading")
        if self._error is not None and not abandon:
            raise self._error

    @property
    def done(self) -> bool:
        return not self._thread.is_alive()
