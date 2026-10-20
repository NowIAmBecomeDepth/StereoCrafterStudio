"""Less work on the UI thread (bug sweep 2026-10-09, batch 9):

* Merging's clip list and badges list each folder once, instead of a glob per
  video and up to six isfile calls a clip -- with the same answers (A2);
* the folder picker asks "any folders in here?" with one scandir that stops at
  the first (A2);
* the Updates dialog reads the checkout's branch once, not a git call per read (A1).

    python -m unittest studio.tests.test_ui_thread_perf
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data


class MergeListTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.d = Path(d.name)
        for sub in ("inp", "mask", "orig"):
            (self.d / sub).mkdir()
        for core in ("Shot[1]", "a_b", "Lone", "Beside"):
            (self.d / "inp" / f"{core}_1080_inpainted_right_eye.mp4").write_bytes(b"x")
        (self.d / "mask" / "Shot[1]_1080_splatted2.mp4").write_bytes(b"x")
        (self.d / "mask" / "a_b_1080_splatted4.mp4").write_bytes(b"x")
        (self.d / "mask" / "Beside_1080_SPLATTED2.MP4").write_bytes(b"x")     # case-blind, as glob is
        (self.d / "inp" / "Shot[1].fssidecar").write_text("{}")                 # in the inpainted folder
        (self.d / "orig" / "a_b.json").write_text("{}")                         # in the originals' folder
        (self.d / "inp" / "Beside_1080_inpainted_right_eye.fssidecar").write_text("{}")   # beside the clip
        self.values = {"inpainted_folder": str(self.d / "inp"), "mask_folder": str(self.d / "mask"),
                       "original_folder": str(self.d / "orig")}

    def test_the_same_clips_and_badges(self):
        import studio.clip_queue as Q
        clips = Q.merge_clips(self.values)
        self.assertEqual([Path(c).name.split("_1080")[0] for c in clips], ["Beside", "Shot[1]", "a_b"])
        fx = Q.merge_fx(self.values, clips)
        self.assertEqual([f[3] for f in fx], [True, True, True])
        lone = str(self.d / "inp" / "Lone_1080_inpainted_right_eye.mp4")
        self.assertEqual(Q.merge_fx(self.values, [lone])[0][3], False)

    def test_one_listing_per_folder(self):
        import glob
        import studio.clip_queue as Q
        clips = [str(self.d / "inp" / f"c{i}_1080_inpainted_right_eye.mp4") for i in range(40)]
        for c in clips:
            Path(c).write_bytes(b"x")
        real_isfile = os.path.isfile
        isfile_calls = []
        with mock.patch("os.path.isfile", side_effect=lambda p: (isfile_calls.append(p), real_isfile(p))[1]):
            Q.merge_fx(self.values, clips)
        self.assertLessEqual(len(isfile_calls), 2, "isfile per clip (one a folder setting is fine)")
        real_glob = glob.glob
        calls = []
        with mock.patch("glob.glob", side_effect=lambda *a, **k: (calls.append(a), real_glob(*a, **k))[1]):
            Q.merge_clips(self.values)
        self.assertLessEqual(len(calls), 3, "a glob per video")


class FolderPickerTests(unittest.TestCase):
    def test_has_dirs_stops_at_the_first(self):
        from studio.folder_browser import FolderBrowser
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        root = Path(d.name)
        fb = FolderBrowser()
        (root / "clip.mp4").write_bytes(b"x")
        self.assertFalse(fb.hasDirs(str(root)))
        (root / ".hidden").mkdir()
        self.assertEqual(fb.hasDirs(str(root)), bool(fb.dirs(str(root))))
        (root / "Chapter 1").mkdir()
        self.assertTrue(fb.hasDirs(str(root)))
        self.assertFalse(fb.hasDirs(str(root / "nope")))

    def test_the_picker_asks_has_dirs(self):
        qml = (Path(__file__).resolve().parents[1] / "qml/Studio/FolderPicker.qml").read_text(encoding="utf-8")
        self.assertIn("!Folders.hasDirs(leaf)", qml)
        self.assertNotIn("Folders.dirs(leaf).length === 0", qml)


class UpdaterBranchTest(unittest.TestCase):
    def test_the_branch_is_read_once(self):
        import studio.updater as U
        calls = []
        with mock.patch.object(U, "current_branch", lambda repo: (calls.append(1), "studio")[1]):
            up = U.Updater(run_check=lambda *a: {})
            up._data.pop("branch", None)
            up._data["repo"] = "https://github.com/me/fork.git"   # not Studio's own: its main needs no git
            got = [up._get_branch() for _ in range(5)]
        self.assertEqual(got, ["studio"] * 5)
        self.assertEqual(len(calls), 1)
        up._data["branch"] = "main"
        self.assertEqual(up._get_branch(), "main")


if __name__ == "__main__":
    unittest.main()
