"""Settings moved from config_v2 to config (user, 2026-10-02: no "v2" in any
name). A Studio updated from before finds its settings in the old folder and
moves them across once; a folder a test points CONFIG_DIR at never triggers
it. Temp folders only: the real repo is never touched.

    python -m unittest studio.tests.test_settings_move
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
import studio.settings_store as ss  # noqa: E402


class MoveTests(unittest.TestCase):
    def setUp(self):
        saved = (ss.CONFIG_DIR, ss.DEFAULT_DIR, ss.OLD_DIR, ss._moved)
        self.addCleanup(lambda: (setattr(ss, "CONFIG_DIR", saved[0]), setattr(ss, "DEFAULT_DIR", saved[1]),
                                 setattr(ss, "OLD_DIR", saved[2]), setattr(ss, "_moved", saved[3])))
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.d = Path(tmp.name)
        self.old = self.d / ("config" + "_v2")
        self.old.mkdir()
        (self.old / "studio.json").write_text(json.dumps({"palette": "Nord"}))
        ss.OLD_DIR = self.old
        ss._moved = False

    def test_the_old_folder_moves_across(self):
        ss.CONFIG_DIR = ss.DEFAULT_DIR = self.d / "config"
        self.assertEqual(ss.load("studio"), {"palette": "Nord"})
        self.assertFalse(self.old.exists())
        self.assertTrue((self.d / "config" / "studio.json").exists())

    def test_a_test_folder_never_moves_it(self):
        ss.DEFAULT_DIR = self.d / "config"
        ss.CONFIG_DIR = self.d / "elsewhere"
        ss.load("studio")
        self.assertTrue(self.old.exists())

    def test_an_existing_config_wins(self):
        ss.CONFIG_DIR = ss.DEFAULT_DIR = self.d / "config"
        (self.d / "config").mkdir()
        (self.d / "config" / "studio.json").write_text(json.dumps({"palette": "Moss"}))
        self.assertEqual(ss.load("studio"), {"palette": "Moss"})
        self.assertTrue(self.old.exists())         # left alone


class UnreadableTests(unittest.TestCase):
    """bug sweep 2026-10-09 A1: a settings file that won't read was replaced
    by the seed with no copy kept."""

    def setUp(self):
        saved = (ss.CONFIG_DIR, ss._moved)
        self.addCleanup(lambda: (setattr(ss, "CONFIG_DIR", saved[0]), setattr(ss, "_moved", saved[1])))
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ss.CONFIG_DIR = self.cfg = Path(tmp.name) / "config"
        ss._moved = True
        self.cfg.mkdir()
        self.broken = b'{"palette": "Moss", "ui_scale": "0.9",'          # cut short
        (self.cfg / "studio.json").write_bytes(self.broken)

    def test_it_is_kept_before_the_seed_goes_over_it(self):
        self.assertEqual(ss.load("studio"), {})
        self.assertEqual((self.cfg / "studio.json.unreadable").read_bytes(), self.broken)
        self.assertEqual(json.loads((self.cfg / "studio.json").read_text()), {})

    def test_no_copy_possible_nothing_written(self):
        from unittest import mock
        with mock.patch("shutil.copy2", side_effect=PermissionError("held open")):
            self.assertEqual(ss.load("studio"), {})
        self.assertEqual((self.cfg / "studio.json").read_bytes(), self.broken)


if __name__ == "__main__":
    unittest.main()
