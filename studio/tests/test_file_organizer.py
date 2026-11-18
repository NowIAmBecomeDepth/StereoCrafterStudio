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
import unittest.mock
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

        def recycle(path, may_delete_for_good=True):    # the Recycle Bin, stood in for
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
        RB.send_to_recycle_bin = lambda path, may_delete_for_good=True: False
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


class RecycleOnlyTest(unittest.TestCase):
    """A source clip or a final Splatting / Inpainting / Merging output only ever goes to the Recycle Bin
    (user, 2026-11-18): a re-render's earlier output, and Move to Finished's identical duplicate, were
    replaced or deleted for good. Without a bin they are kept, never deleted."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.d = Path(tmp.name)
        self.recycled, self.bin_ok = [], True
        saved = RB.send_to_recycle_bin
        self.addCleanup(setattr, RB, "send_to_recycle_bin", saved)

        def recycle(path, may_delete_for_good=True):        # the Recycle Bin, stood in for
            self.recycled.append((Path(path).name, may_delete_for_good))
            if self.bin_ok:
                os.remove(path)
            return self.bin_ok
        RB.send_to_recycle_bin = recycle

    def write(self, name, data):
        p = self.d / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return str(p)

    def test_a_re_render_sends_the_earlier_output_to_the_bin(self):
        from core.common.partial_output import publish
        final = self.write("shot_splatted4.mp4", b"old")
        part = self.write("shot_splatted4.mp4.part", b"new")
        self.assertTrue(publish(part, final, keep_old=True))
        self.assertEqual(Path(final).read_bytes(), b"new")
        self.assertEqual(self.recycled, [("shot_splatted4 (earlier).mp4", False)], "the earlier output was not recycled")
        self.assertEqual(sorted(p.name for p in self.d.iterdir()), ["shot_splatted4.mp4"])

    def test_a_refused_replace_puts_the_earlier_output_back(self):
        from core.common import partial_output as PO
        final = self.write("shot_merged.mp4", b"old")
        part = self.write("shot_merged.mp4.part", b"new")
        real = os.replace

        def refuse(a, b):
            if str(a).endswith(".part"):
                raise OSError("refused")
            return real(a, b)
        with unittest.mock.patch.object(PO.os, "replace", refuse):
            self.assertFalse(PO.publish(part, final, keep_old=True))
        self.assertEqual(Path(final).read_bytes(), b"old")
        self.assertEqual(self.recycled, [])

    def test_without_a_bin_the_earlier_output_is_kept_as_old(self):
        from core.common.partial_output import publish
        self.bin_ok = False
        final = self.write("shot_merged.mp4", b"old")
        self.write("shot_merged.mp4.old", b"older")
        self.assertTrue(publish(self.write("shot_merged.mp4.part", b"new"), final, keep_old=True))
        self.assertEqual(Path(final).read_bytes(), b"new")
        self.assertEqual((self.d / "shot_merged.mp4.old").read_bytes(), b"older")
        self.assertEqual((self.d / "shot_merged.mp4.old 2").read_bytes(), b"old", "the earlier output was lost")

    def test_publish_without_keep_old_still_just_replaces(self):
        from core.common.partial_output import publish
        final = self.write("x.mp4", b"old")
        self.assertTrue(publish(self.write("x.mp4.part", b"new"), final))
        self.assertEqual(self.recycled, [])

    def _twins(self):
        src = self.write("in/a.mp4", b"same")
        arch = self.write("in/finished/a.mp4", b"same")
        t = os.stat(src).st_mtime
        os.utime(arch, (t, t))
        return src, arch

    def test_move_to_finished_recycles_an_identical_source(self):
        src, arch = self._twins()
        ok, err = FO.move_single_file(src, str(self.d / "in"))
        self.assertTrue(ok, err)
        self.assertEqual(self.recycled, [("a.mp4", False)], "the duplicate was deleted for good")
        self.assertTrue(os.path.isfile(arch))

    def test_move_to_finished_keeps_an_identical_source_without_a_bin(self):
        self.bin_ok = False
        src, arch = self._twins()
        ok, err = FO.move_single_file(src, str(self.d / "in"))
        self.assertTrue(ok, err)
        self.assertTrue((self.d / "in" / "finished" / "a (2).mp4").is_file(), "the source was deleted")

    def test_edge_fill_refill_keeps_the_once_filled_splat_without_a_bin(self):
        from core.removal import edge_fill
        self.bin_ok = False
        path = self.write("shot_splatted4.mp4", b"filled once")
        part = self.write("shot_splatted4.mp4.part", b"filled again")
        edge_fill._swap(path, part, refill=True)
        self.assertEqual(Path(path).read_bytes(), b"filled again")
        self.assertEqual((self.d / "shot_splatted4.mp4.old").read_bytes(), b"filled once")

    def test_every_final_writer_keeps_the_old(self):
        """Each place a Scene Cut / Splatting / Inpainting / Merging final is put in place recycles the
        earlier one."""
        import re
        for rel in ("core/splatting/render_processor.py", "core/common/video_io.py", "core/common/flat_sbs.py",
                    "studio/merge_all.py", "studio/stages/merging/engine.py", "studio/stages/scenecut/engine.py"):
            text = (REPO / rel).read_text(encoding="utf-8")
            calls = re.findall(r"\bpublish\((.*)", text)
            self.assertTrue(calls, rel)
            for c in calls:
                self.assertIn("keep_old=True", c, f"{rel}: publish({c}")
        for rel, final in (("studio/stages/merging/engine.py", "replace_keeping_old(encode_path, output_path)"),
                           ("studio/stages/inpainting_v2/engine.py",
                            "replace_keeping_old(self.part_path, self.output_path)")):
            self.assertIn(final, (REPO / rel).read_text(encoding="utf-8"), rel)


if __name__ == "__main__":
    unittest.main()
