"""FileOrganizerWorker (audit 2026-09-28, minor): wait_until_empty returned
while the last file was still being moved, and a stop() arriving just after a
file was taken dropped it. Temp files only."""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.common import file_organizer as FO  # noqa: E402


class WorkerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.clip = self.dir / "clip.mp4"
        self.clip.write_bytes(b"x")
        self.release = threading.Event()
        real = FO.move_single_file

        def slow_move(*a, **k):
            self.release.wait(5)
            return real(*a, **k)
        patcher = mock.patch.object(FO, "move_single_file", slow_move)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_wait_until_empty_waits_for_the_move_in_progress(self):
        w = FO.FileOrganizerWorker()
        w.start()
        w.put(str(self.clip), str(self.dir))
        time.sleep(0.8)                            # taken off the queue, moving
        threading.Timer(0.5, self.release.set).start()
        w.wait_until_empty(timeout=5)
        self.assertTrue((self.dir / "finished" / "clip.mp4").is_file(), "returned before the move finished")
        w.stop()

    def test_a_file_taken_before_stop_is_still_moved(self):
        w = FO.FileOrganizerWorker()
        w.start()
        w.put(str(self.clip), str(self.dir))
        time.sleep(0.8)
        threading.Timer(0.3, self.release.set).start()
        w.stop(wait=True, timeout=5)
        self.assertTrue((self.dir / "finished" / "clip.mp4").is_file(), "the file taken was dropped")


if __name__ == "__main__":
    unittest.main()
