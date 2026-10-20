"""clip_thumbs (audit 2026-09-28, minor): a clip the batch moved to finished/
turned blank the next time its row was made -- the thumbnail was looked up at
the old path only. A synthetic clip in a temp folder; never the user's."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PySide6.QtGui import QGuiApplication  # noqa: E402

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from studio.clip_thumbs import ClipThumbProvider  # noqa: E402

_app = QGuiApplication.instance() or QGuiApplication(sys.argv)


class FinishedClipTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.clip = self.dir / "clip.mp4"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=size=160x90:rate=24:duration=1", "-pix_fmt", "yuv420p", str(self.clip)],
                       check=True)

    def thumb(self, provider):
        return provider.requestImage(str(self.clip), None, None)

    def move_to_finished(self):
        (self.dir / "finished").mkdir()
        shutil.move(str(self.clip), str(self.dir / "finished" / "clip.mp4"))

    def test_a_clip_moved_to_finished_keeps_its_thumbnail(self):
        self.move_to_finished()
        self.assertGreater(self.thumb(ClipThumbProvider()).height(), 1, "blank for a clip in finished/")

    def test_a_row_remade_after_the_move_keeps_the_picture_it_had(self):
        provider = ClipThumbProvider()
        before = self.thumb(provider)
        self.assertGreater(before.height(), 1)
        os.remove(self.clip)                       # gone altogether (not in finished/ either)
        self.assertEqual(self.thumb(provider).size(), before.size(), "the thumbnail it had went blank")


if __name__ == "__main__":
    unittest.main()
