"""settings_store seeds a new settings file from the Prototype's -- only in
the real settings folder (2026-10-10). The Prototype's files hold the user's
own project folders; a check's fresh settings seeded from them pointed its
stages at the user's footage.

    python -m unittest studio.tests.test_settings_seed
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
import studio.settings_store as ss  # noqa: E402


class SeedTests(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, True)
        saved = (ss.CONFIG_DIR, ss.DEFAULT_DIR, ss._moved)
        self.addCleanup(lambda: (setattr(ss, "CONFIG_DIR", saved[0]), setattr(ss, "DEFAULT_DIR", saved[1]),
                                 setattr(ss, "_moved", saved[2])))
        ss._moved = True
        self.v1 = self.d / "config_x.json"
        self.v1.write_text(json.dumps({"input_source_clips": "A:/3D PROJECTS/real/footage"}), encoding="utf-8")

    def test_a_tests_settings_start_from_the_defaults(self):
        ss.DEFAULT_DIR = self.d / "real_config"
        ss.CONFIG_DIR = self.d / "test_config"
        self.assertEqual(ss.load("x", self.v1), {})
        self.assertEqual(json.loads((ss.CONFIG_DIR / "x.json").read_text(encoding="utf-8")), {})

    def test_the_real_folder_still_seeds(self):
        ss.CONFIG_DIR = ss.DEFAULT_DIR = self.d / "config"
        self.assertEqual(ss.load("x", self.v1), {"input_source_clips": "A:/3D PROJECTS/real/footage"})

    def test_the_checks_settings_are_a_tests(self):
        """Every check that builds the app, a controller or settings points
        CONFIG_DIR away from the real folder (as the unit tests do through
        studio.tests), so none is seeded."""
        checks = Path(__file__).resolve().parent / "checks"
        loose = []
        for p in sorted(checks.glob("check_*.py")):
            src = p.read_text(encoding="utf-8", errors="replace")
            builds = any(k in src for k in ("studio.app", "studio import app", "settings_store", "controller"))
            if builds and "CONFIG_DIR" not in src:
                loose.append(p.name)
        self.assertEqual(loose, [])


if __name__ == "__main__":
    unittest.main()
