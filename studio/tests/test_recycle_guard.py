"""The Recycle Bin, too big and guarded (user, 2026-10-14).

* Something too big for its drive's bin would be deleted for good: Studio asks first (as Windows
  does, but in Studio -- Windows' own box came up from job threads and stalled them), and keeps it
  unless the answer is yes; with no one to ask, it's kept.
* A drive whose bin is switched off counts as having none.
* The folder picker's Move to Recycle Bin leaves the open project (and a folder holding it) and
  every folder a running job uses alone.

Windows' own Recycle Bin call is faked throughout: nothing here reaches the real bin."""

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
from core.common import recycle_bin as R  # noqa: E402


class _FakeShell:
    """Stands in for shell32: SHFileOperationW 'recycles' by deleting the file (the test's own)."""

    def __init__(self, calls):
        self.calls = calls
        self.flags = None

    def SHFileOperationW(self, op_ref):  # noqa: N802 (Windows' name)
        op = op_ref._obj
        path = op.pFrom.rstrip("\0")
        self.calls.append(path)
        self.flags = op.fFlags
        os.remove(path)
        return 0


class TooBigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, "big.mov")
        with open(self.path, "wb") as f:
            f.write(b"x" * 4000)
        self.calls = []
        self.patches = [mock.patch.object(R, "has_recycle_bin", lambda p: True),
                        mock.patch.object(R, "bin_capacity", lambda p: 1000),      # smaller than the file
                        mock.patch.object(R.ctypes, "WinDLL", lambda name: _FakeShell(self.calls))]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_with_no_one_to_ask_it_is_kept(self):
        with mock.patch.object(R, "ASK_PERMANENT", None):
            self.assertFalse(R.send_to_recycle_bin(self.path))
        self.assertTrue(os.path.exists(self.path), "kept, not deleted for good")
        self.assertEqual(R.last_kept, "too_big")
        self.assertEqual(self.calls, [], "Windows' delete never called: it would delete for good")

    def test_asked_and_kept(self):
        asked = []
        with mock.patch.object(R, "ASK_PERMANENT", lambda p, n: asked.append((p, n)) or False):
            self.assertFalse(R.send_to_recycle_bin(self.path))
        self.assertEqual(asked, [(os.path.abspath(self.path), 4000)])
        self.assertTrue(os.path.exists(self.path))

    def test_asked_and_deleted_for_good(self):
        with mock.patch.object(R, "ASK_PERMANENT", lambda p, n: True):
            self.assertTrue(R.send_to_recycle_bin(self.path))
        self.assertFalse(os.path.exists(self.path))
        self.assertEqual(self.calls, [], "deleted by Studio, as asked -- not handed to Windows")

    def test_a_file_that_fits_goes_to_the_bin_unasked(self):
        small = os.path.join(self.tmp, "small.mov")
        with open(small, "wb") as f:
            f.write(b"x" * 10)
        with mock.patch.object(R, "ASK_PERMANENT", lambda p, n: self.fail("asked about a file that fits")):
            self.assertTrue(R.send_to_recycle_bin(small))
        self.assertEqual(self.calls, [os.path.abspath(small)])

    def test_windows_own_warning_box_is_no_longer_asked_for(self):
        """FOF_WANTNUKEWARNING (0x4000) came up ownerless from job threads and stalled them."""
        shells = []
        small = os.path.join(self.tmp, "small2.mov")
        with open(small, "wb") as f:
            f.write(b"x")
        with mock.patch.object(R.ctypes, "WinDLL", lambda name: shells.append(_FakeShell(self.calls)) or shells[-1]):
            R.send_to_recycle_bin(small)
        self.assertFalse(shells[-1].flags & 0x4000)


class BinSwitchedOffTest(unittest.TestCase):
    def test_a_bin_switched_off_counts_as_none(self):
        """'Don't move files to the Recycle Bin' on a drive: Windows would delete for good."""
        here = tempfile.gettempdir()
        with mock.patch.object(R, "_bin_settings", lambda p: {"NukeOnDelete": 1, "MaxCapacity": 1000}):
            self.assertFalse(R.has_recycle_bin(here))
        with mock.patch.object(R, "_bin_settings", lambda p: {"NukeOnDelete": 0, "MaxCapacity": 1000}):
            self.assertTrue(R.has_recycle_bin(here))


class PickerGuardTest(unittest.TestCase):
    def setUp(self):
        from studio import folder_browser as FB
        self.FB = FB
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.project = os.path.join(self.tmp, "Movies", "My Film")
        self.job_out = os.path.join(self.tmp, "Work", "Merged")
        for d in (self.project, self.job_out, os.path.join(self.tmp, "Old")):
            os.makedirs(d)
        open(os.path.join(self.job_out, "clip.mov"), "wb").close()
        self.sent = []
        for p in (mock.patch.object(FB, "has_recycle_bin", lambda p: True),
                  mock.patch.object(FB, "send_to_recycle_bin", lambda p: self.sent.append(p) or True),
                  mock.patch.object(FB, "PROTECTED", lambda: [(self.project, "the open project", False),
                                                              (self.job_out, "a folder Merging's job is using", True)])):
            p.start()
            self.addCleanup(p.stop)
        self.browser = FB.FolderBrowser()

    def test_the_open_project_and_a_folder_holding_it_are_refused(self):
        for target in (self.project, os.path.dirname(self.project)):
            res = self.browser.recycle(target)
            self.assertIn("the open project", res.get("error", ""), target)
        self.assertEqual(self.sent, [])

    def test_a_running_jobs_folder_and_what_is_in_it_are_refused(self):
        for target in (self.job_out, os.path.join(self.job_out, "clip.mov"), os.path.dirname(self.job_out)):
            res = self.browser.recycle(target)
            self.assertIn("job is using", res.get("error", ""), target)
        self.assertEqual(self.sent, [])

    def test_anything_else_still_goes(self):
        self.assertEqual(self.browser.recycle(os.path.join(self.tmp, "Old")), {"ok": True})


if __name__ == "__main__":
    unittest.main()
