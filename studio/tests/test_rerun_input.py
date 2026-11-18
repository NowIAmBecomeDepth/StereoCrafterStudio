"""A one-clip rerun puts its input field back (tester carlobs, 2026-11-18): after Splatting's "Rerun with DVD" the
Depth tab's input still named the one clip, so the next batch would have run only it. DVD, Video Depth Anything and
DepthCrafter: loadForRerun remembers the field, the job that ran on that clip puts it back when it ends; a field
changed by hand in between is left alone; a Test Render doesn't count. Temp folders only.

    python -m unittest studio.tests.test_rerun_input
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["CUDA_VISIBLE_DEVICES"] = "-1"

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data


class _Base(unittest.TestCase):
    def setUp(self):
        import core.ui.job_handoff as jh
        import studio.settings_store as ss
        from studio.tests._qt import app
        self.app = app()
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self._saved = (ss.CONFIG_DIR, jh._RECORD_PATH)
        ss.CONFIG_DIR = root / "config"
        jh._RECORD_PATH = str(root / "last_jobs.json")
        self.addCleanup(self._restore)
        self.folder = os.path.normpath(str(root / "Source"))
        os.makedirs(self.folder)
        self.clip = os.path.join(self.folder, "LC_V2-0578.mp4")
        Path(self.clip).write_bytes(b"x")
        self.depth = os.path.normpath(str(root / "Depth"))
        os.makedirs(self.depth)

    def _restore(self):
        import core.ui.job_handoff as jh
        import studio.settings_store as ss
        ss.CONFIG_DIR, jh._RECORD_PATH = self._saved
        self.tmp.cleanup()

    def controllers(self):
        from studio.tests.test_splatting import _Shell
        from studio.stages.dvd.controller import DvdController
        from studio.stages.vda.controller import VdaController
        return {"dvd": DvdController(_Shell()), "vda": VdaController(_Shell())}

    @staticmethod
    def finish(ctl, ran, test=False):
        """The end of a job that ran on `ran`, as the stage's worker reports it."""
        from studio.outcome import Outcome
        ctl._test_run = test
        ctl._runner = SimpleNamespace(opts={"input_dir_or_file_var": ran, "output_dir": ""}, outcome=Outcome(done=1),
                                      made=[])
        ctl._on_finished(True)

    def field(self, ctl):
        return os.path.normpath(str(ctl._map.value("input_dir_or_file_var") or ""))


class RerunInputTest(_Base):
    def test_back_to_the_folder_once_the_rerun_has_run(self):
        for name, ctl in self.controllers().items():
            ctl._map.insert("input_dir_or_file_var", self.folder)
            ctl.loadForRerun(self.clip, self.depth)
            self.assertEqual(self.field(ctl), self.clip, f"{name}: the one clip loaded")
            self.finish(ctl, self.clip, test=True)
            self.assertEqual(self.field(ctl), self.clip, f"{name}: a Test Render doesn't count")
            self.finish(ctl, self.clip)
            self.assertEqual(self.field(ctl), self.folder, f"{name}: the folder back once it has run")

    def test_a_field_changed_by_hand_is_left(self):
        for name, ctl in self.controllers().items():
            ctl._map.insert("input_dir_or_file_var", self.folder)
            ctl.loadForRerun(self.clip, self.depth)
            other = os.path.join(os.path.dirname(self.folder), "Other")
            ctl._map.insert("input_dir_or_file_var", other)
            self.finish(ctl, other)
            self.assertEqual(self.field(ctl), os.path.normpath(other), name)
            self.finish(ctl, other)                       # and forgotten: no later run puts the old one back
            self.assertEqual(self.field(ctl), os.path.normpath(other), name)

    def test_a_rerun_after_a_rerun_goes_back_to_the_first_field(self):
        ctl = self.controllers()["dvd"]
        ctl._map.insert("input_dir_or_file_var", self.folder)
        second = os.path.join(self.folder, "LC_V2-0579.mp4")
        Path(second).write_bytes(b"x")
        ctl.loadForRerun(self.clip, self.depth)
        ctl.loadForRerun(second, self.depth)
        self.finish(ctl, second)
        self.assertEqual(self.field(ctl), self.folder)


class DepthCrafterRerunInputTest(_Base):
    def test_depthcrafter_too(self):
        from studio.outcome import Outcome
        from studio.stages.depthcrafter.controller import DepthCrafterController
        from studio.tests.test_splatting import _Shell
        ctl = DepthCrafterController(_Shell())
        ctl._map.insert("input_dir_or_file_var", self.folder)
        ctl.loadForRerun(self.clip, self.depth)
        self.assertEqual(self.field(ctl), self.clip)
        ctl._test_run = False
        ctl._batch_runner = SimpleNamespace(opts={"input_dir_or_file_var": self.clip, "output_dir": self.depth},
                                            outcome=Outcome(done=1))
        ctl._on_finished("batch", True)
        self.assertEqual(self.field(ctl), self.folder)


if __name__ == "__main__":
    unittest.main()
