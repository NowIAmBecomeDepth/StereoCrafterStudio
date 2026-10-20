"""Detect Text (splatting/submask.py) while a batch runs (audit 2026-09-28 #29):
the editor held an OpenCV capture on the source clip for as long as its
window was open, so the batch's move to finished/ failed (WinError 32).
A job now suspends it -- the clip let go -- and the next frame it is asked
for opens the clip again, in finished/ if the batch moved it there.
A small clip ffmpeg makes in a temp folder; never the user's footage."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import cv2  # noqa: E402

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from studio.stages.splatting.submask import SubtitleMask  # noqa: E402


class SuspendTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.clip = self.dir / "clip.mp4"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=size=64x48:rate=12", "-frames:v", "12", "-pix_fmt", "yuv420p", str(self.clip)],
                       check=True)
        self.ed = SubtitleMask(eng=None)
        self.addCleanup(self.ed.close)
        # what start() does once it has found the clip
        self.ed.cap, self.ed.video_path = cv2.VideoCapture(str(self.clip)), str(self.clip)
        self.ed.n_frames, self.ed.open = 12, True
        self.ed.seek(3)

    def move(self, src, dest):
        for _ in range(20):
            try:
                os.replace(src, dest)
                return True
            except PermissionError:
                time.sleep(0.1)
        return False

    def test_a_job_makes_it_let_go_of_the_clip(self):
        self.ed.suspend()
        finished = self.dir / "finished"
        finished.mkdir()
        self.assertTrue(self.move(self.clip, finished / "clip.mp4"), "the clip is still held open")

    def test_the_next_frame_opens_it_again_even_in_finished(self):
        self.ed.suspend()
        (self.dir / "finished").mkdir()
        self.assertTrue(self.move(self.clip, self.dir / "finished" / "clip.mp4"))
        self.ed.seek(7)
        self.assertEqual(self.ed.frame_idx, 7)
        self.assertIsNotNone(self.ed.frame_bgr)


if __name__ == "__main__":
    unittest.main()
