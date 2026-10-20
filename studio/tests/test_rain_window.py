"""Rain / Sparks read the source forward only (bug sweep 2026-10-09 #18).

The render read each batch, then the batch's window from `radius` frames back
(Rain and Sparks need the neighbours), then the next batch from inside that
window: two backward seeks per batch -- a keyframe seek and up to a GOP of
decoding with decord, an ffmpeg restart with Strict decode.
rain.WindowFrames reads each window once and keeps what the next shares.

    .venv\\Scripts\\python.exe -m unittest studio.tests.test_rain_window -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data

import numpy as np  # noqa: E402

from core.splatting.rain import WindowFrames, window_indices  # noqa: E402


class _Batch:
    def __init__(self, a):
        self.a = a

    def asnumpy(self):
        return self.a


class Reader:
    """A decord-like reader whose frame i is filled with i; it records reads."""

    def __init__(self, n):
        self.n, self.reads = n, []

    def __len__(self):
        return self.n

    def get_batch(self, idx):
        self.reads.append(list(idx))
        return _Batch(np.stack([np.full((2, 3, 3), i, np.uint8) for i in idx]))


class WindowFramesTests(unittest.TestCase):
    def batches(self, n=100, size=10):
        return [list(range(s, min(s + size, n))) for s in range(0, n, size)]

    def test_every_read_moves_forward_and_nothing_is_read_twice(self):
        r = Reader(100)
        w = WindowFrames(r, radius=6)
        for b in self.batches():
            w.read(b)
        flat = [i for read in r.reads for i in read]
        self.assertEqual(flat, list(range(100)), "a frame was read twice or out of order")

    def test_the_window_and_the_batch_are_the_frames_asked_for(self):
        r = Reader(100)
        w = WindowFrames(r, radius=6)
        for b in self.batches():
            win, centres = w.read(b)
            want, want_centres = window_indices(b, 100, 6)
            self.assertEqual([int(f[0, 0, 0]) for f in win], want)
            self.assertEqual(centres, want_centres)
            self.assertEqual([int(f[0, 0, 0]) for f in win[centres]], b)

    def test_only_the_shared_frames_are_kept(self):
        w = WindowFrames(Reader(100), radius=6)
        for b in self.batches():
            w.read(b)
            self.assertLessEqual(len(w._frames), 10 + 2 * 6)


if __name__ == "__main__":
    unittest.main()
