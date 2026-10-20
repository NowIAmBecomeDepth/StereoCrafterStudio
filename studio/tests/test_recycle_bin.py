"""recycle_bin (bug sweep 2026-10-09, A11): on a drive with no Recycle Bin the
file is kept, with no prompt -- Windows' "delete permanently?" box from a job
thread has no owner window and stalled Merging's batch until answered."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.common import recycle_bin as RB  # noqa: E402


@unittest.skipUnless(sys.platform == "win32", "Windows only")
class RecycleBinTests(unittest.TestCase):
    def test_no_bin_keeps_the_file_and_never_calls_the_shell(self):
        f = Path(tempfile.mkdtemp()) / "clip_sbs.mp4"
        f.write_bytes(b"sbs")
        with mock.patch.object(RB, "has_recycle_bin", return_value=False), \
                mock.patch.object(RB.ctypes, "WinDLL") as shell:
            self.assertFalse(RB.send_to_recycle_bin(str(f)))
        shell.assert_not_called()
        self.assertTrue(f.exists())

    def test_shares_have_no_bin(self):
        self.assertFalse(RB.has_recycle_bin(r"\\server\share\clip.mp4"))
        with mock.patch.object(RB.ctypes.windll.kernel32, "GetDriveTypeW", return_value=RB._DRIVE_REMOTE):
            self.assertFalse(RB.has_recycle_bin(r"Z:\clip.mp4"))

    def test_a_local_system_drive_has_one(self):
        # read only: nothing is sent to the user's Recycle Bin. A local drive
        # found bin-less would keep every SBS the user asked to delete.
        import os
        self.assertTrue(RB.has_recycle_bin(os.environ.get("SystemDrive", "C:") + "\\Windows"))


if __name__ == "__main__":
    unittest.main()
