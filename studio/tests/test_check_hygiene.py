"""The checks keep house (bug sweep 2026-10-09, A12): they end through the
fixtures (their temp folders removed, no DLL-teardown crash after a green run),
a check that can't run says SKIP rather than PASS, the GPU-heavy ones don't
share the pool, and nothing grabs screen pixels without the user's OK.

    python -m unittest studio.tests.test_check_hygiene
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data

CHECKS = Path(__file__).resolve().parent / "checks"


class HygieneTests(unittest.TestCase):
    def test_checks_with_temp_folders_end_through_the_fixtures(self):
        loose = []
        for p in sorted(CHECKS.glob("check_*.py")):
            src = p.read_text(encoding="utf-8", errors="replace")
            if "mkdtemp" not in src and "A.build" not in src:
                continue
            last = [ln for ln in src.splitlines() if ln.strip()][-1]
            if not re.match(r"^_fixtures\.(exit_now|skip)\(", last):
                loose.append(f"{p.name}: {last.strip()}")
        self.assertEqual(loose, [])

    def test_the_gpu_heavy_checks_run_alone(self):
        sys.path.insert(0, str(CHECKS))
        import run_all
        for name in ("check_sam3_directions.py", "check_sam3_vram.py", "check_erase_rose.py",
                     "check_depthedit_erase.py"):
            self.assertIn(name, run_all.SOLO)

    def test_no_screen_pixels_without_the_users_ok(self):
        for p in sorted((CHECKS / "onscreen").glob("*.py")):
            src = p.read_text(encoding="utf-8", errors="replace")
            if "ImageGrab" in src and "import ImageGrab" in src:
                self.assertIn('os.environ.get("STUDIO_ALLOW_SCREEN_GRAB") != "1"', src, p.name)
                self.assertLess(src.index("STUDIO_ALLOW_SCREEN_GRAB"), src.index("import ImageGrab"), p.name)

    def test_a_check_that_cant_run_is_a_skip(self):
        probe = CHECKS / "check_zz_skip_probe.py"
        probe.write_text("import sys, os\n"
                         "sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _fixtures\n"
                         "_fixtures.skip('no such hardware here')\n", encoding="utf-8")
        try:
            out = subprocess.run([sys.executable, str(CHECKS / "run_all.py"), "zz_skip_probe", "--no-unit"],
                                 capture_output=True, text=True, timeout=300).stdout
        finally:
            probe.unlink()
        self.assertIn("[SKIP] check_zz_skip_probe.py", out)
        self.assertIn("no such hardware here", out)
        self.assertIn("1 skipped", out)
        self.assertNotIn("[PASS] check_zz_skip_probe.py", out)


if __name__ == "__main__":
    unittest.main()
