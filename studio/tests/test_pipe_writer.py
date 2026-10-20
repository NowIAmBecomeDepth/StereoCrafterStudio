"""PipeWriter and a stalled encoder (bug sweep 2026-10-09, A11): ffmpeg alive
but no longer reading blocked the writer thread in its write, and the
Splatting render's close() waited for ever -- before the code that kills a
stuck ffmpeg. put() and close() now give up after their timeout."""
from __future__ import annotations

import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.common.pipe_writer import EncoderStalled, PipeWriter  # noqa: E402


class _Stdin:
    """An encoder's stdin: takes `room` writes, then blocks until killed."""

    def __init__(self, room: int):
        self.room, self.written, self.killed = room, [], threading.Event()

    def write(self, data):
        if len(self.written) >= self.room:
            self.killed.wait()                      # stuck, as a hung ffmpeg leaves it
            raise BrokenPipeError("encoder ended")
        self.written.append(bytes(data))


class _Proc:
    def __init__(self, room: int):
        self.stdin = _Stdin(room)

    def kill(self):
        self.stdin.killed.set()


class PipeWriterTests(unittest.TestCase):
    def test_everything_is_written_when_the_encoder_reads(self):
        proc = _Proc(room=100)
        w = PipeWriter(proc)
        for i in range(10):
            w.put(bytes([i]))
        w.close(timeout=5)
        self.assertEqual(len(proc.stdin.written), 10)

    def test_close_gives_up_on_a_stalled_encoder_and_ends_after_a_kill(self):
        proc = _Proc(room=1)
        w = PipeWriter(proc, depth=3)
        for i in range(3):
            w.put(bytes([i]))
        t0 = time.monotonic()
        with self.assertRaises(EncoderStalled):
            w.close(timeout=0.3)
        self.assertLess(time.monotonic() - t0, 3, "close() waited on the stuck write")
        self.assertFalse(w.done)
        proc.kill()                                  # what the render does next
        w.close(abandon=True, timeout=5)
        self.assertTrue(w.done)

    def test_put_gives_up_when_the_queue_never_drains(self):
        proc = _Proc(room=0)
        w = PipeWriter(proc, depth=2)
        with self.assertRaises(EncoderStalled):
            for i in range(10):
                w.put(bytes([i]), timeout=0.3)
        proc.kill()
        w.close(abandon=True, timeout=5)

    def test_a_dead_encoder_still_fails_the_clip(self):
        proc = _Proc(room=0)
        proc.kill()                                  # every write raises BrokenPipe at once
        w = PipeWriter(proc)
        w.put(b"x")
        with self.assertRaises(BrokenPipeError):
            w.close(timeout=5)


if __name__ == "__main__":
    unittest.main()
