"""child_procs (audit 2026-09-28 #13): a forced Stop / Quit ends the ffmpeg
Studio started -- and only that: a child with any other name is left alone,
and nothing outside Studio's own process tree is touched."""

from __future__ import annotations

import subprocess
import sys
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from studio import child_procs as C  # noqa: E402


@unittest.skipUnless(sys.platform.startswith("win"), "Windows only")
class KillEncodersTest(unittest.TestCase):
    def test_ends_our_ffmpeg_and_nothing_else(self):
        flags = subprocess.CREATE_NO_WINDOW
        ffmpeg = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-re", "-f", "lavfi",
                                   "-i", "testsrc2=size=64x64:rate=5:duration=60", "-f", "null", "-"],
                                  creationflags=flags)
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], creationflags=flags)
        self.addCleanup(lambda: [p.kill() for p in (ffmpeg, other) if p.poll() is None])
        time.sleep(0.5)
        ended = C.kill_encoders()
        self.assertIn(ffmpeg.pid, ended)
        self.assertIsNotNone(ffmpeg.wait(5), "ffmpeg is still running")
        self.assertIsNone(other.poll(), "a child that is not an encoder was ended")
        self.assertNotIn(other.pid, ended)

    def test_descendants_walks_the_tree(self):
        table = [(1, 0, "studio"), (2, 1, "ffmpeg.exe"), (3, 2, "ffprobe.exe"), (4, 9, "ffmpeg.exe")]
        self.assertEqual(sorted(C.descendants(1, table)), [(2, "ffmpeg.exe"), (3, "ffprobe.exe")])


if __name__ == "__main__":
    unittest.main()
