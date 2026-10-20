"""Beginner Mode's writes (audit 2026-09-28, PERF). Its re-pin runs on every
stage switch, and it rewrote every stage's settings file (and studio.json)
each time, on the UI thread, whether or not anything had changed. Now a stage
is saved only when a pinned value of its was put back, and studio.json only
when the mode's own record changed."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtQml import QQmlPropertyMap  # noqa: E402

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from studio import beginner as B  # noqa: E402

from studio.tests._qt import app as _qt_app
_app = _qt_app()   # a GUI app: the whole suite shares it (tests/_qt.py)


class _Ctl:
    def __init__(self, values):
        self._map = QQmlPropertyMap()
        for k, v in values.items():
            self._map.insert(k, v)
        self.saves = 0
        self.busy = False

    def save(self):
        self.saves += 1


class _Shell:
    def __init__(self):
        self._data = {"beginner_mode": True}
        self.saves = 0

    def save(self):
        self.saves += 1


class BeginnerSavesTest(unittest.TestCase):
    def setUp(self):
        stages = {}
        for _rule, (stage, key, value) in B.FORCE.items():
            stages.setdefault(stage, {})[key] = value            # already as the mode pins them
        stages["merging"] = {"grain_enable": True, "grain_automatch": True}
        self.ctl = {name: _Ctl(values) for name, values in stages.items()}
        self.shell = _Shell()
        self.b = B.Beginner(self.shell, self.ctl)
        for c in self.ctl.values():
            c.saves = 0
        self.shell.saves = 0

    def test_a_stage_switch_with_nothing_to_put_back_writes_nothing(self):
        self.b.reapply()
        self.assertEqual({n: c.saves for n, c in self.ctl.items() if c.saves}, {},
                         "settings files rewritten with nothing changed")
        self.assertEqual(self.shell.saves, 0, "studio.json rewritten with nothing changed")

    def test_a_pinned_value_changed_behind_saves_that_stage_only(self):
        self.ctl["splatting"]._map.insert("enable_low_res", False)
        self.b.reapply()
        self.assertIs(self.ctl["splatting"]._map.value("enable_low_res"), True)
        self.assertEqual({n: c.saves for n, c in self.ctl.items() if c.saves}, {"splatting": 1})


if __name__ == "__main__":
    unittest.main()
