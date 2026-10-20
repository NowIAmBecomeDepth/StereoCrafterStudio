"""DepthCrafter's batch runs its clips in the order Upcoming Jobs shows them (2026-10-19): the list
(clip_queue.dc_clips) sorts by name, the batch (runner.discover_sources) read os.listdir's order -- the
file system's -- so with 130 clips loaded Start began at clip #51.

    .venv\\Scripts\\python.exe -m unittest studio.tests.test_dc_batch_order -v
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data

from studio import clip_queue  # noqa: E402
from studio.stages.depthcrafter import runner as R  # noqa: E402

NAMES = ["Clip_10.mp4", "clip_02.mp4", "Clip_01.mp4", "clipA.mp4", "clip-b.mp4", "Clip_100.mp4"]


class BatchOrderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dcorder_")
        for n in NAMES:
            Path(self.tmp, n).write_bytes(b"x")
        real = os.listdir
        # the file system's own order, whatever it is -- here scrambled on purpose
        self.scrambled = lambda p: list(reversed(sorted(real(p), key=len)))  # noqa: E731

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_batch_runs_in_upcoming_jobs_order(self):
        with mock.patch("os.listdir", side_effect=self.scrambled):
            specs, single = R.discover_sources(self.tmp)
            shown = clip_queue.dc_clips({"input_dir_or_file_var": self.tmp})
        self.assertFalse(single)
        batch = [os.path.basename(s["path"]) for s in specs]
        self.assertEqual(batch, [os.path.basename(p) for p in shown], "the batch starts at the top of the list")
        self.assertEqual(batch, sorted(NAMES, key=str.lower))


if __name__ == "__main__":
    unittest.main()
