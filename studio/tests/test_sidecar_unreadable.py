"""A sidecar that won't read (bug sweep 2026-10-09 A11): load_sidecar_data
answers it with the defaults, which no caller could tell from a real one --
the batch rendered the clip with them, and the preview's autosave wrote them
over the file. Now the batch fails that clip, and a save keeps the unreadable
file as <name>.unreadable before writing.

    .venv\\Scripts\\python.exe -m unittest studio.tests.test_sidecar_unreadable -v
"""
from __future__ import annotations

import queue
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data

from core.common.sidecar_manager import SidecarConfigManager  # noqa: E402

BROKEN = b'{"convergence_plane": 0.62, "max_disparity": 31.0,'     # cut short (a sync conflict, a hand edit)


class SidecarTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.sc = self.dir / "clip_depth.fssidecar"
        self.m = SidecarConfigManager()

    def test_read_error(self):
        self.assertIsNone(self.m.read_error(str(self.sc)))                  # none there: fine
        self.sc.write_text('{"convergence_plane": 0.6}')
        self.assertIsNone(self.m.read_error(str(self.sc)))
        self.sc.write_bytes(BROKEN)
        self.assertTrue(self.m.read_error(str(self.sc)))

    def test_a_save_keeps_the_unreadable_file_first(self):
        self.sc.write_bytes(BROKEN)
        self.assertTrue(self.m.save_sidecar_data(str(self.sc), {"convergence_plane": 0.5}))
        self.assertEqual((self.dir / "clip_depth.fssidecar.unreadable").read_bytes(), BROKEN)
        self.assertIsNone(self.m.read_error(str(self.sc)))
        # a second time: the first copy is not overwritten
        self.sc.write_bytes(BROKEN + b" again")
        self.assertTrue(self.m.save_sidecar_data(str(self.sc), {"convergence_plane": 0.5}))
        self.assertEqual((self.dir / "clip_depth.fssidecar.unreadable").read_bytes(), BROKEN)
        self.assertEqual(len(list(self.dir.glob("clip_depth.fssidecar.unreadable-*"))), 1)

    def test_the_batch_fails_the_clip(self):
        from core.splatting.batch_processing import BatchProcessor
        self.sc.write_bytes(BROKEN)
        settings = types.SimpleNamespace(sidecar_folder=str(self.dir), sidecar_ext=".fssidecar")
        bp = BatchProcessor(queue.Queue(), threading.Event(), self.m)
        out = bp._get_video_specific_settings(str(self.dir / "clip.mp4"), settings, False)
        self.assertIn("can't be read", out.get("error", ""))


if __name__ == "__main__":
    unittest.main()
