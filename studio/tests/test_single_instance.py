"""One Studio at a time (bug sweep 2026-10-09, A1): a named mutex. Test names
only -- never Studio's own, so a running Studio is not touched."""
from __future__ import annotations

import subprocess
import sys
import unittest
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from studio import single_instance as SI  # noqa: E402


@unittest.skipUnless(sys.platform == "win32", "Windows only")
class SingleInstanceTests(unittest.TestCase):
    def test_the_second_claim_is_refused(self):
        name = "Local\\SCStudioTest-" + uuid.uuid4().hex
        self.assertFalse(SI.running(name))
        held = SI._held
        try:
            self.assertTrue(SI.claim(name))
            self.assertTrue(SI.running(name))
            self.assertFalse(SI.claim(name), "a second Studio was let in")
        finally:
            SI._held = held

    def test_another_process_sees_it(self):
        name = "Local\\SCStudioTest-" + uuid.uuid4().hex
        probe = ("import sys; sys.path.insert(0, %r); from studio import single_instance as S; "
                 "sys.exit(3 if S.running(%r) else 0)") % (str(REPO), name)
        self.assertEqual(subprocess.run([sys.executable, "-c", probe]).returncode, 0)
        held = SI._held
        try:
            SI.claim(name)
            self.assertEqual(subprocess.run([sys.executable, "-c", probe]).returncode, 3)
        finally:
            SI._held = held

    def test_the_check_imports_nothing_heavy(self):
        # the launchers run it before every start: no Qt, no torch
        code = ("import sys; sys.path.insert(0, %r); import studio.single_instance; "
                "print(sorted(m for m in sys.modules if m.split('.')[0] in ('PySide6', 'torch', 'cv2')))") % str(REPO)
        out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout.strip()
        self.assertEqual(out, "[]")


if __name__ == "__main__":
    unittest.main()
