"""Test Render (user, 2026-10-15; studio/test_renders.py): each stage's right-click "Test Render" runs that
one clip into <output folder>/Test Renders, moves nothing to finished/ and isn't handed on -- "This Clip
Now" runs as before. The stages' heavy parts are stood in for: what's checked is what each run is started
with. Temp folders; configs and last_jobs.json redirected (studio.tests)."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from studio.legacy import install as _install_legacy  # noqa: E402

_install_legacy()
from studio.tests._qt import app as _qt_app  # noqa: E402
_app = _qt_app()
from studio.tests.test_splatting import _Shell  # noqa: E402
from studio import test_renders as TR  # noqa: E402


class _Base(unittest.TestCase):
    def setUp(self):
        import core.ui.job_handoff as jh
        import studio.settings_store as ss
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        saved = (ss.CONFIG_DIR, jh._RECORD_PATH)
        ss.CONFIG_DIR, jh._RECORD_PATH = self.tmp / "config", str(self.tmp / "last_jobs.json")
        self.addCleanup(lambda: (setattr(ss, "CONFIG_DIR", saved[0]), setattr(jh, "_RECORD_PATH", saved[1])))
        self.inp, self.out = self.tmp / "in", self.tmp / "out"
        self.inp.mkdir()
        self.out.mkdir()
        self.clip = self.inp / "shot.mp4"
        self.clip.write_bytes(b"x")

    def test_folder(self):
        self.assertEqual(TR.folder(str(self.out)), os.path.join(str(self.out), "Test Renders"))
        self.assertTrue(os.path.isdir(os.path.join(str(self.out), "Test Renders")))


class DepthCrafterTest(_Base):
    def run_one(self, test):
        from studio.stages.depthcrafter import controller as C
        ctl = C.DepthCrafterController(_Shell())
        ctl._map.insert("input_dir_or_file_var", str(self.inp))
        ctl._map.insert("output_dir", str(self.out))
        got = {}

        class Runner:
            def __init__(self, opts, hooks, stop, move_originals=True, stop_now=None):
                got.update(opts=dict(opts), move=move_originals)
                self.opts = opts

            def run_batch(self, *a):
                return True
        with mock.patch.object(C.R, "DepthCrafterRunner", Runner), \
                mock.patch.object(C.R, "discover_sources", return_value=([{"path": str(self.clip), "basename": "shot",
                                                                            "type": "video_file"}], False)), \
                mock.patch.object(ctl, "_begin", lambda *a, **k: True):
            (ctl.testClip if test else ctl.startClip)(str(self.clip))
        return got, ctl

    def test_a_test_render_goes_into_test_renders_and_moves_nothing(self):
        got, ctl = self.run_one(True)
        self.assertEqual(got["opts"]["output_dir"], os.path.join(str(self.out), "Test Renders"))
        self.assertIs(got["move"], False)
        self.assertTrue(got["opts"].get("test_render"))
        self.assertTrue(ctl._test_run)

    def test_this_clip_now_is_as_before(self):
        got, ctl = self.run_one(False)
        self.assertEqual(os.path.normpath(got["opts"]["output_dir"]), os.path.normpath(str(self.out)))
        self.assertIs(got["move"], True)
        self.assertFalse(ctl._test_run)

    def test_a_test_isnt_asked_to_overwrite(self):
        from studio.stages.depthcrafter.runner import DepthCrafterRunner
        r = SimpleNamespace(opts={"test_render": True})
        self.assertTrue(DepthCrafterRunner._opt(r, "test_render", False))


class InpaintingTest(_Base):
    def run_one(self, test):
        from studio.stages.inpainting import controller as C
        ctl = C.InpaintingController(_Shell())
        ctl._map.insert("input_folder", str(self.inp))
        ctl._map.insert("output_folder", str(self.out))
        captured = {}
        real = C.S.run_settings

        def run_settings(values):
            captured["values"] = dict(values)
            return real(values)

        class Thread:
            def __init__(self, target=None, name=None, daemon=None):
                pass

            def start(self):
                pass
        with mock.patch.object(ctl, "_claim", return_value=True), mock.patch.object(C.S, "run_settings", run_settings), \
                mock.patch.object(C.threading, "Thread", Thread):
            (ctl.testClip if test else ctl.startClip)(str(self.clip))
        return captured, ctl

    def test_a_test_render(self):
        captured, ctl = self.run_one(True)
        self.assertEqual(ctl._run_paths["output"], os.path.join(str(self.out), "Test Renders"))
        self.assertIs(captured["values"]["move_to_finished"], False)
        recorded = []
        with mock.patch.object(ctl, "_record_job_handoff", lambda: recorded.append(1)):
            from studio.outcome import Outcome
            ctl._on_finished(False, Outcome(done=1))
        self.assertEqual(recorded, [], "a test render was handed on")

    def test_this_clip_now_is_as_before(self):
        captured, ctl = self.run_one(False)
        self.assertEqual(os.path.normpath(ctl._run_paths["output"]), os.path.normpath(str(self.out)))
        recorded = []
        with mock.patch.object(ctl, "_record_job_handoff", lambda: recorded.append(1)):
            from studio.outcome import Outcome
            ctl._on_finished(False, Outcome(done=1))
        self.assertEqual(recorded, [1])


class SplattingTest(_Base):
    def run_ready(self, test):
        from studio.stages.splatting import controller as C
        ctl = C.SplattingController(_Shell())
        ctl._test_render = test
        settings = SimpleNamespace(output_splatted=str(self.out), move_to_finished=True)
        begun = {}
        with mock.patch.object(ctl, "_prepare", return_value=(object(), settings)), \
                mock.patch.object(ctl, "_begin", lambda p, s, *a, **k: begun.update(s=s)):
            ctl._single_test = ""
            ctl._on_single_ready({"video": str(self.clip), "depth": str(self.clip)})
        return begun["s"], ctl

    def test_a_test_render(self):
        s, ctl = self.run_ready(True)
        self.assertEqual(s.output_splatted, os.path.join(str(self.out), "Test Renders"))
        self.assertIs(s.move_to_finished, False)

    def test_this_clip_now_is_as_before(self):
        s, ctl = self.run_ready(False)
        self.assertEqual(s.output_splatted, str(self.out))
        self.assertIs(s.move_to_finished, True)

    def test_a_stale_test_flag_doesnt_carry_into_the_next_run(self):
        from studio.stages.splatting import controller as C
        ctl = C.SplattingController(_Shell())
        ctl._test_render = True
        with mock.patch.object(ctl, "_start_single", lambda need_loaded: None):
            ctl.startSingle()
        self.assertFalse(ctl._test_render)


class MergingTest(_Base):
    def run_one(self, test):
        from studio.stages.merging import controller as C
        ctl = C.MergingController(_Shell())
        ctl._map.insert("output_folder", str(self.out))
        started = {}
        eng = SimpleNamespace(is_processing=True,
                              start_processing=lambda: started.update(values=dict(ctl._engine_values)))
        ctl._engine_values.update({"output_folder": str(self.out), "resume": True})
        with mock.patch.object(ctl.preview, "release_for_job", lambda then: then()), \
                mock.patch.object(ctl, "_with_engine", lambda f: f(eng)):
            (ctl.testClip if test else ctl.startClip)(str(self.clip))
        return started["values"]

    def test_a_test_render(self):
        v = self.run_one(True)
        self.assertEqual(v["output_folder"], os.path.join(str(self.out), "Test Renders"))
        self.assertIs(v["resume"], False)

    def test_this_clip_now_is_as_before(self):
        v = self.run_one(False)
        self.assertEqual(v["output_folder"], str(self.out))
        self.assertIs(v["resume"], True)


if __name__ == "__main__":
    unittest.main()
