"""The Splatting preview's depth stream (bug sweep 2026-10-09, A6): one frame
the planes filter couldn't give (past the map's end, a timeout) switched the
clip to the gray filter for good, and later frames decoded differently. The
gray filter is tried on a stream of its own and kept only when it reads.

    python -m unittest studio.tests.test_depth_stream_fallback
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data

W, H = 4, 2
N = W * H * 2


class Stream:
    """A depth stream: `gives(vf, idx)` says whether it has the frame."""
    made = []

    def __init__(self, vf, gives):
        self.vf, self.gives, self.closed = vf, gives, False
        Stream.made.append(self)

    def read(self, idx):
        return bytes([1 if self.vf.startswith("extract") else 2]) * N if self.gives(self.vf, idx) else None

    def close(self):
        self.closed = True


class FallbackTests(unittest.TestCase):
    def owner(self, gives):
        from studio.legacy import install
        install()
        from studio.stages.splatting import preview_engine as PE, fast_decode
        cls = next(c for c in vars(PE).values() if isinstance(c, type) and hasattr(c, "_fast_depth_bytes"))
        Stream.made = []
        fake = SimpleNamespace(_depth_path="clip_depth.mp4", frame_cache=fast_decode.FrameCache(),
                               _depth_stream=None, _depth_stream_key=None, _depth_stream_vf=None)
        patcher = mock.patch.object(PE.fast_decode, "depth_stream",
                                    lambda path, w, h, fps, vf, total=-1: Stream(vf, gives))
        patcher.start()
        self.addCleanup(patcher.stop)
        return lambda i: cls._fast_depth_bytes(fake, i, 24.0, W, H, N), fake

    def test_a_missing_frame_keeps_the_planes_filter(self):
        read, fake = self.owner(lambda vf, i: i < 10)          # a map 10 frames long
        self.assertEqual(read(3)[0], 1)
        self.assertIsNone(read(12))
        self.assertEqual(fake._depth_stream_vf, "extractplanes=y,format=gray16le")
        self.assertEqual(read(5)[0], 1, "a later frame decoded with the gray filter")

    def test_a_true_gray_source_switches(self):
        read, fake = self.owner(lambda vf, i: not vf.startswith("extract"))
        self.assertEqual(read(3)[0], 2)
        self.assertEqual(fake._depth_stream_vf, "format=gray16le")
        self.assertEqual(read(4)[0], 2)
        self.assertEqual(sum(1 for s in Stream.made if not s.vf.startswith("extract")), 1, "one gray stream, kept")


if __name__ == "__main__":
    unittest.main()
