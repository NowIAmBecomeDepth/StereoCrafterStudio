"""A move waits out a file that is open a moment, and names who keeps one open (a tester, 2026-11-17: "Rerun in
Splatting, BUT after waiting 10 seconds works" -- the Rerun had given up after two).

    python -m unittest studio.tests.test_file_holders
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

import studio.tests  # noqa: F401  -- the data redirect first


def _hold(path: Path, seconds: float) -> threading.Thread:
    """`path` held open (as Windows locks it) for `seconds`, on a thread."""
    fh = open(path, "rb")

    def let_go():
        time.sleep(seconds)
        fh.close()
    t = threading.Thread(target=let_go, daemon=True)
    t.start()
    return t


@unittest.skipUnless(sys.platform == "win32", "Windows locks open files; elsewhere a move just goes")
class HoldersTest(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="sc_holders_"))
        (self.d / "finished").mkdir()
        self.src = self.d / "finished" / "LC_V2-0411_depth.mp4"
        self.src.write_bytes(b"d" * 4096)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_rerun_waits_out_a_file_open_a_few_seconds(self):
        from studio import send_back
        t = _hold(self.src, 3.0)                 # longer than the old 5 x 0.4 s
        failed = send_back.apply({"moves": [[str(self.src), str(self.d)]], "aside": []})
        t.join()
        self.assertEqual(failed, [])
        self.assertTrue((self.d / self.src.name).is_file())

    def test_who_has_it_open(self):
        from core.common import file_holders as H
        with open(self.src, "rb"):
            self.assertIn("Studio itself", H.holders(str(self.src)))
        self.assertEqual(H.holders(str(self.src)), [])

    def test_a_file_kept_open_is_named_with_who_has_it(self):
        from core.common import file_holders as H
        from studio import send_back
        real = H.WAIT_FOR_FREE
        H.WAIT_FOR_FREE = 1.0                    # the defaults are read at call time below
        try:
            with open(self.src, "rb"):
                err = H.move_when_free(str(self.src), str(self.d / self.src.name), wait=1.0)
                self.assertIn("open in Studio itself", err or "")
                self.assertEqual(send_back._failed_name(str(self.src), err),
                                 "LC_V2-0411_depth.mp4 (open in Studio itself)")
        finally:
            H.WAIT_FOR_FREE = real

    def test_a_reader_studio_dropped_lets_go(self):
        """Tester carlobs, 2026-11-18: a Rerun with DVD couldn't move LC_V2-0578_depth.mp4, "open in Studio itself",
        after the previews had let go. A reader that was dropped but not yet collected (a reference cycle) keeps
        its file open until the garbage collector runs: the move collects once before it waits."""
        import gc
        from core.common import file_holders as H

        class Reader:                            # a file kept by an object in a cycle, as a decoder's can be
            def __init__(self, path):
                self.f = open(path, "rb")
                self.me = self
        was = gc.isenabled()
        gc.disable()                             # no collection on its own while the move waits
        try:
            Reader(self.src)                     # dropped at once; the cycle keeps the file open
            err = H.move_when_free(str(self.src), str(self.d / self.src.name), wait=1.0)
        finally:
            if was:
                gc.enable()
        self.assertIsNone(err, err)
        self.assertTrue((self.d / self.src.name).is_file())

    def test_move_to_finished_waits_too(self):
        from PySide6.QtCore import QObject
        from studio.clip_queue import ClipQueue
        from studio.tests._qt import app
        qt = app()
        clip = self.d / "LC_V2-0411.mp4"
        clip.write_bytes(b"c" * 4096)
        owner = QObject()                    # kept: the queue's parent
        q = ClipQueue("depthcrafter", lambda: {}, owner)
        q._start = lambda: None
        q._paths, q._depths = [str(clip)], [""]
        got = []
        q.movedToFinished.connect(lambda moved, left: got.append((moved, list(left))))
        t = _hold(clip, 3.0)
        q.moveToFinished(str(clip))
        end = time.monotonic() + 30
        while not got and time.monotonic() < end:
            qt.processEvents()
            time.sleep(0.02)
        t.join()
        self.assertEqual(got, [(1, [])])
        self.assertTrue((self.d / "finished" / clip.name).is_file())

if __name__ == "__main__":
    unittest.main()
