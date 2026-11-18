"""core/common/file_organizer: the finished/ archive never loses a file.

Restore Finished (audit 2026-09-28 #9) used to delete the archived copy for
good whenever the input folder already held a file of its name -- whatever
either file was. In temp folders; the Recycle Bin is stood in for.

    .venv\\Scripts\\python.exe -m unittest studio.tests.test_file_organizer -v
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.common import file_organizer as FO  # noqa: E402
import core.common.recycle_bin as RB  # noqa: E402


class LooksIdenticalTest(unittest.TestCase):
    """bug sweep 2026-10-09 A11: a DNxHR re-render that differs only mid-clip
    (fixed-size frames, the moov at the end) has the same size and the same
    first and last MiB, so it passed as "the same file": Resume skipped it and
    the move to finished/ deleted it. A re-render has its own modified time."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.d = Path(tmp.name)
        self.body = bytes(3 << 20)                       # 3 MiB: head, middle, tail

    def test_a_re_render_differing_mid_clip_is_not_the_same(self):
        old, new = self.d / "old.mov", self.d / "new.mov"
        old.write_bytes(self.body)
        middle = bytearray(self.body); middle[len(middle) // 2] = 1
        new.write_bytes(bytes(middle))
        os.utime(old, (1_000_000_000, 1_000_000_000))       # rendered long before
        self.assertFalse(FO._looks_identical(str(new), str(old)))

    def test_a_copy_is_the_same(self):
        import shutil
        a, b = self.d / "a.mov", self.d / "b.mov"
        a.write_bytes(self.body)
        shutil.copy2(a, b)
        self.assertTrue(FO._looks_identical(str(a), str(b)))


class RestoreFinishedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.inp = self.tmp / "in"
        self.fin = self.inp / "finished"
        self.fin.mkdir(parents=True)
        self.recycled = []
        self._saved = RB.send_to_recycle_bin

        def recycle(path):                      # the Recycle Bin, stood in for
            self.recycled.append(Path(path).name)
            os.remove(path)
            return True
        RB.send_to_recycle_bin = recycle

    def tearDown(self):
        RB.send_to_recycle_bin = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def restore(self):
        return FO.restore_finished_files([(str(self.inp), "finished")])

    def test_a_plain_restore_moves_it_back(self):
        (self.fin / "a.mp4").write_bytes(b"a")
        restored, failed, _ = self.restore()
        self.assertEqual((restored, failed), (1, 0))
        self.assertEqual((self.inp / "a.mp4").read_bytes(), b"a")
        self.assertFalse(self.fin.exists(), "the emptied finished/ folder stays")

    def test_a_different_namesake_keeps_both(self):
        # the user re-rendered a.mp4 after the old one was archived
        (self.inp / "a.mp4").write_bytes(b"the new render")
        (self.fin / "a.mp4").write_bytes(b"the archived old one")
        restored, failed, failures = self.restore()
        self.assertEqual((self.fin / "a.mp4").read_bytes(), b"the archived old one",
                         "the archived copy was deleted")
        self.assertEqual((self.inp / "a.mp4").read_bytes(), b"the new render")
        self.assertEqual((restored, failed), (0, 1))
        self.assertIn("differs", failures[0][1])
        self.assertEqual(self.recycled, [])

    def test_a_true_duplicate_goes_to_the_recycle_bin(self):
        (self.inp / "a.mp4").write_bytes(b"same")
        (self.fin / "a.mp4").write_bytes(b"same")
        restored, failed, _ = self.restore()
        self.assertEqual(self.recycled, ["a.mp4"], "the duplicate was deleted for good, not recycled")
        self.assertEqual((self.inp / "a.mp4").read_bytes(), b"same")
        self.assertEqual((restored, failed), (1, 0))

    def test_a_duplicate_that_cannot_be_recycled_stays(self):
        RB.send_to_recycle_bin = lambda path: False
        (self.inp / "a.mp4").write_bytes(b"same")
        (self.fin / "a.mp4").write_bytes(b"same")
        restored, failed, _ = self.restore()
        self.assertTrue((self.fin / "a.mp4").exists())
        self.assertEqual((restored, failed), (0, 1))


class AlreadyFinishedTest(unittest.TestCase):
    """A file already in <dest>/finished is its own "identical copy already in finished": the move removed
    it as a duplicate and the clip was gone (user's tester, 2026-11-18). Merging finds originals and splats
    a "finished" level down since 9b5e821, and moved them to the folder they were already in."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.d = Path(tmp.name)

    def touch(self, *parts):
        p = self.d.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"clip")
        return str(p)

    def test_a_file_already_there_stays(self):
        clip = self.touch("orig", "finished", "shot.mp4")
        ok, err = FO.move_single_file(clip, str(self.d / "orig"))
        self.assertTrue(ok, err)
        self.assertTrue(os.path.isfile(clip))

    def test_merging_move_to_finished_keeps_an_original_splatting_moved(self):
        from studio import clip_queue as Q
        inp = self.touch("inp", "shot_1408_inpainted_right_eye.mp4")
        grid = self.touch("mask", "finished", "shot_1408_splatted4.mp4")
        orig = self.touch("orig", "finished", "shot.mp4")
        vals = {"inpainted_folder": str(self.d / "inp"), "mask_folder": str(self.d / "mask"),
                "original_folder": str(self.d / "orig")}
        moves = Q.merge_finished_moves(vals, [inp])
        self.assertIn((orig, str(self.d / "orig")), moves)
        FO.move_files_to_finished(moves)
        self.assertTrue(os.path.isfile(orig), "the source clip was deleted")
        self.assertTrue(os.path.isfile(str(self.d / "inp" / "finished" / os.path.basename(inp))))
        self.assertTrue(os.path.isfile(grid) or os.path.isfile(str(self.d / "mask" / "finished" / "finished"
                                                                   / os.path.basename(grid))))


if __name__ == "__main__":
    unittest.main()
