"""Tests for v2 Inpainting V2 (Wan VACE): the generated engine/runner and the settings.

The 14B model is never loaded: ``load_models`` and ``run_inpaint`` are
replaced with fakes, and v1's own start_processing / validation / batch loop /
queue drain / finish report / finished-folder moves run for real in a temp
folder, through the generated WanInpaintRunner.

    .venv\\Scripts\\python.exe -m unittest studio.tests.test_inpainting_v2 -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from studio.legacy import install as _install_legacy  # noqa: E402

_install_legacy()

from studio.stages.inpainting_v2 import settings as S  # noqa: E402
try:                    # the Prototype's generators don't ship (release, 2026-10-17)
    from studio.tools import gen_inpainting_v2_engine as gen  # noqa: E402
except ImportError:
    gen = None


@unittest.skipUnless(studio.tests.PROTOTYPE, studio.tests.NO_PROTOTYPE)
class SyncTest(unittest.TestCase):
    def test_engine_matches_v1(self):
        """engine.py is exactly what the generator makes from today's v1.

        Fails when inpainting_gui_v2.py has changed since engine.py was
        generated: re-run studio.tools.gen_inpainting_v2_engine."""
        fresh = gen.build().replace("\n", "\r\n").encode("utf-8")
        self.assertEqual(fresh, gen.TARGET.read_bytes(),
                         "engine.py is stale; re-run: python -m studio.tools.gen_inpainting_v2_engine")

    def test_no_tk_in_engine(self):
        import ast
        tree = ast.parse(gen.TARGET.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        # core.ui.job_handoff is toolkit-neutral (Merging's engine imports it too):
        # V2's handoff for Merging's "Import from Inpainting V2"
        bad = [m for m in imported if m.startswith(("tkinter", "ttkthemes", "fire", "core.ui"))
               and m != "core.ui.job_handoff"]
        self.assertEqual(bad, [])
        self.assertFalse([n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "WanInpaintingGUI"])


class SettingsTest(unittest.TestCase):
    def test_seed_keeps_v1_types(self):
        v1 = {"frames_chunk": 81, "seed": "7", "use_sageattention": "true", "offload_mode": "FP8 resident",
              "window_geometry": "620x540+1+2", "stray": 1}
        s = S.seed_from_v1(v1)
        self.assertEqual(s["frames_chunk"], "81")          # a number from an old file -> v1's StringVar text
        self.assertEqual(s["seed"], "7")
        self.assertIs(s["use_sageattention"], True)
        self.assertEqual(s["offload_mode"], "FP8 resident")
        self.assertEqual(s["window_geometry"], "620x540+1+2")   # passed through untouched
        self.assertNotIn("stray", s)
        self.assertEqual(s["tile_overlap"], "64")          # default for a missing key

    @unittest.skipUnless(studio.tests.PROTOTYPE, studio.tests.NO_PROTOTYPE)
    def test_offload_modes_are_v1s(self):
        src = (REPO / "inpainting_gui_v2.py").read_text(encoding="utf-8")
        for mode in S.OFFLOAD_MODES:
            self.assertIn(f'"{mode}"', src)


class WeightsInstalledTest(unittest.TestCase):
    """The page's download row shows until every file V2 loads is there (user, 2026-10-02;
    since 2026-10-18 by weights.py's file list, not two folders not being empty)."""

    def test_every_file_needed(self):
        from studio.stages.inpainting_v2 import weights as W
        from studio.stages.inpainting_v2.controller import WEIGHT_DIRS, weights_installed
        self.assertEqual(WEIGHT_DIRS, (W.BASE.folder, W.MODEL.folder))
        root = Path(tempfile.mkdtemp())
        self.assertFalse(weights_installed(root))
        (root / "weights" / W.BASE.folder).mkdir(parents=True)
        (root / "weights" / W.BASE.folder / "model_index.json").write_text("{}")
        (root / "weights" / W.MODEL.folder).mkdir(parents=True)
        (root / "weights" / W.MODEL.folder / "config.json").write_text("{}")
        self.assertFalse(weights_installed(root))           # a folder with a file in it isn't the weights
        for part in W.REQUIRED:
            for path, _n in part.files:
                f = root / "weights" / part.folder / path
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(b"1")
        self.assertTrue(weights_installed(root))


class _Hooks:
    def __init__(self):
        self.vars, self.messages, self.runs, self.woken = [], [], [], 0
        self.claims = True

    def var(self, key, value):
        self.vars.append((key, value))

    def message(self, kind, title, text):
        self.messages.append((kind, title, text))

    def wake(self):
        self.woken += 1

    def running(self, on):
        self.runs.append(on)

    def claim(self):
        return self.claims

    def release(self):
        pass


class _FakeEngine:
    def __init__(self):
        self.text_encoder = object()
        self._prompt_cache = {}
        self.released = 0

    def release(self):
        self.released += 1


class RunnerTest(unittest.TestCase):
    """v1's WanInpaintingGUI logic, generated, over fakes of the model calls."""

    @classmethod
    def setUpClass(cls):
        from studio.stages.inpainting_v2 import engine as E   # torch + diffusers: ~9 s
        cls.E = E

    def setUp(self):
        E = self.E
        self.tmp = Path(tempfile.mkdtemp())
        self.inp = self.tmp / "in"
        self.out = self.tmp / "out"
        self.inp.mkdir()
        for name in ("a_splatted2.mp4", "b_splatted2.mp4", "notes.txt"):
            (self.inp / name).write_bytes(b"x")
        # Model folders v1 checks for before anything loads.
        self._saved = (E.PRE_TRAINED_PATH, E.TRANSFORMER_PATH, E.load_models, E.run_inpaint)
        (self.tmp / "base").mkdir()
        (self.tmp / "xf").mkdir()
        E.PRE_TRAINED_PATH, E.TRANSFORMER_PATH = str(self.tmp / "base"), str(self.tmp / "xf")
        self.loads, self.calls = [], []
        self.engine = _FakeEngine()

        self.load_kw = []

        def fake_load(pre, xf, **kw):
            self.loads.append((pre, xf, kw.get("offload_mode"), kw.get("use_sageattention")))
            self.load_kw.append(kw)
            return self.engine

        def fake_run(engine, video, out_dir, **kw):
            self.calls.append((os.path.basename(video), kw))
            kw["progress_cb"]("denoise", 1, 2, "step 1/2")
            if getattr(self, "block", None) is not None:
                while not kw["should_stop"]():
                    time.sleep(0.01)
                raise E.InferenceCancelled()

        E.load_models, E.run_inpaint = fake_load, fake_run
        self.hooks = _Hooks()
        self.values = dict(S.DEFAULTS, input_folder=str(self.inp), output_folder=str(self.out),
                           offload_mode="Group offload", inference_steps="4", seed="3")
        self.runner = E.WanInpaintRunner(self.values, self.hooks)

    def tearDown(self):
        E = self.E
        E.PRE_TRAINED_PATH, E.TRANSFORMER_PATH, E.load_models, E.run_inpaint = self._saved

    def _finish(self):
        t = self.runner.worker_thread
        if t is not None:
            t.join(timeout=10)
        self.runner._poll_queue()

    def status(self):
        return [v for k, v in self.hooks.vars if k == "status"][-1]

    def test_batch_runs_v1s_loop(self):
        self.runner.start_processing()
        self._finish()
        self.assertEqual([c[0] for c in self.calls], ["a_splatted2.mp4", "b_splatted2.mp4"])
        kw = self.calls[0][1]
        self.assertEqual((kw["inference_steps"], kw["seed"], kw["frames_chunk"]), (4, 3, 81))  # v1 parsed them
        self.assertEqual(self.loads, [(str(self.tmp / "base"), str(self.tmp / "xf"), "group", True)])
        self.assertTrue(self.status().startswith("Finished"), self.status())
        self.assertIn("2 done, 0 failed", self.status())
        self.assertEqual(self.hooks.runs, [True, False])
        # Resume: folder input -> each clip moved to finished/ (v1 move_files_to_finished).
        time.sleep(1.2)
        self.assertTrue((self.inp / "finished" / "a_splatted2.mp4").exists())
        self.assertTrue(self.runner.holds_gpu())              # v1 keeps the engine between batches

    def test_torch_compile_reaches_the_load(self):
        """Torch Compile on the page (user, 2026-10-11) goes to load_models; turning it
        on later loads the engine again (it's part of what a loaded engine is)."""
        self.values["move_to_finished"] = False
        self.runner.start_processing()
        self._finish()
        self.assertFalse(self.load_kw[-1].get("use_compile"))
        self.values["use_compile"] = True
        self.runner.start_processing()
        self._finish()
        self.assertEqual(len(self.loads), 2, "a different compile choice is a different engine")
        self.assertTrue(self.load_kw[-1].get("use_compile"))

    def test_engine_reused_then_released(self):
        self.values["move_to_finished"] = False
        self.runner.start_processing()
        self._finish()
        self.runner.start_processing()
        self._finish()
        self.assertEqual(len(self.loads), 1, "same key -> v1 reuses the loaded engine")
        self.runner.release_gpu()
        self.assertEqual(self.engine.released, 1)
        self.assertFalse(self.runner.holds_gpu())

    def test_v1_validation_messages(self):
        self.values["frames_overlap"] = "81"
        self.runner.start_processing()
        self.assertEqual(self.hooks.messages[-1][2], "'frames_overlap' must be smaller than 'frames_chunk'.")
        self.values["frames_overlap"] = "ten"
        self.runner.start_processing()
        self.assertEqual(self.hooks.messages[-1][2], "'frames_overlap' must be an integer (got 'ten').")
        self.assertIsNone(self.runner.worker_thread)
        self.assertEqual(self.calls, [])

    def test_resume_all_done(self):
        # v1's case: clips still in the input that finished/ already holds.
        (self.inp / "finished").mkdir()
        for n in ("a_splatted2.mp4", "b_splatted2.mp4"):
            (self.inp / "finished" / n).write_bytes(b"x")
        # bug sweep A7 (PERF): the split runs on the worker, not at Start on the UI thread;
        # an all-finished folder loads nothing and says why in the finish
        import threading
        from unittest import mock
        from core.common import file_organizer
        on_ui = []
        real = file_organizer.split_already_finished

        def watched(*a, **k):
            on_ui.append(threading.current_thread() is threading.main_thread())
            return real(*a, **k)
        with mock.patch.object(self.E, "split_already_finished", watched):
            self.runner.start_processing()
            self._finish()
        self.assertEqual(on_ui, [False], "the resume split ran on the UI thread")
        self.assertEqual((self.loads, self.calls), ([], []))
        self.assertIn("already processed", self.runner._resume_note)
        self.assertEqual(self.runner._resume_skipped, 2)

    # audit 2026-09-28 #8: a same-named but different clip in finished/ is a
    # re-render, not a finished one
    def test_resume_runs_a_re_splatted_clip(self):
        (self.inp / "finished").mkdir()
        (self.inp / "finished" / "a_splatted2.mp4").write_bytes(b"the old splat")
        (self.inp / "finished" / "b_splatted2.mp4").write_bytes(b"x")          # b: the very same file
        self.runner.start_processing()
        self._finish()
        self.assertEqual([c[0] for c in self.calls], ["a_splatted2.mp4"])

    def test_claim_refused(self):
        self.hooks.claims = False
        self.runner.start_processing()
        self.assertIsNone(self.runner.worker_thread)
        self.assertEqual(self.loads, [])

    def test_stop(self):
        self.block = True
        self.runner.start_processing()
        deadline = time.time() + 5
        while not self.calls and time.time() < deadline:
            time.sleep(0.01)
        self.runner.stop_processing()
        self._finish()
        self.assertTrue(self.status().startswith("Stopped after"), self.status())
        self.assertEqual(len(self.calls), 1)
        self.assertFalse((self.inp / "finished").exists())

    def test_progress_through_queue(self):
        self.values["move_to_finished"] = False
        self.runner.start_processing()
        self.runner.worker_thread.join(timeout=10)
        self.assertGreater(self.hooks.woken, 0, "each queue put wakes the UI")
        self.runner._poll_queue()
        statuses = [v for k, v in self.hooks.vars if k == "status"]
        self.assertTrue(any("[2/2] b_splatted2.mp4" in s and "step 1/2" in s for s in statuses), statuses)


class CompileTest(unittest.TestCase):
    """load_models' Torch Compile (user, 2026-10-11), over small stand-in models: None
    compiles every transformer block; MMGP is asked to compile its own; a hooked offload
    mode (Group / Transformer only) runs uncompiled; off, nothing is compiled."""

    @classmethod
    def setUpClass(cls):
        from studio.stages.inpainting_v2 import engine as E
        cls.E = E

    def load(self, mode, use_compile, profile=None):
        import torch
        from unittest import mock
        E = self.E

        class Part(torch.nn.Module):
            def __init__(self, with_blocks=False):
                super().__init__()
                self.lin = torch.nn.Linear(2, 2)
                if with_blocks:
                    self.blocks = torch.nn.ModuleList([torch.nn.Linear(2, 2) for _ in range(3)])
                    self.vace_blocks = torch.nn.ModuleList([torch.nn.Linear(2, 2) for _ in range(2)])

            def to(self, *a, **k):          # no GPU needed: the stand-ins stay where they are
                return self

        made = {}

        def transformer(*a, **k):
            made["t"] = Part(True)
            return made["t"]
        patches = [mock.patch.object(E.AutoTokenizer, "from_pretrained", lambda *a, **k: object()),
                   mock.patch.object(E.UMT5EncoderModel, "from_pretrained", lambda *a, **k: Part()),
                   mock.patch.object(E.AutoencoderKLWan, "from_pretrained", lambda *a, **k: Part()),
                   mock.patch.object(E.WanVACETransformer3DModel, "from_pretrained", transformer),
                   mock.patch.object(E, "apply_group_offloading", lambda *a, **k: None),
                   mock.patch.object(E, "WanInpaintEngine", lambda *a, **k: None)]
        if profile is not None:
            import mmgp.offload
            patches.append(mock.patch.object(mmgp.offload, "profile", profile))
        for pt in patches:
            pt.start()
        try:
            E.load_models("base", "xf", device=torch.device("cuda"), offload_mode=mode, use_compile=use_compile)
        finally:
            for pt in patches:
                pt.stop()
        t = made["t"]
        return [getattr(b, "_compiled_call_impl", None) is not None for b in list(t.blocks) + list(t.vace_blocks)]

    def test_none_compiles_every_block(self):
        self.assertEqual(self.load("none", True), [True] * 5)

    def test_off_compiles_nothing(self):
        self.assertEqual(self.load("none", False), [False] * 5)

    def test_hooked_offload_runs_uncompiled(self):
        with self.assertLogs(self.E.logger, "WARNING") as logs:
            got = self.load("group", True)
        self.assertEqual(got, [False] * 5)
        self.assertTrue(any("Torch Compile skipped" in m for m in logs.output), logs.output)

    def test_mmgp_compiles_its_own(self):
        asked = {}

        def profile(pipe, **kw):
            asked.update(kw)
            return None
        got = self.load("mmgp", True, profile=profile)
        self.assertEqual(asked.get("compile"), ["transformer"])
        self.assertEqual(got, [False] * 5, "mmgp's compile, not a second one on top")
        asked.clear()
        self.load("mmgp", False, profile=profile)
        self.assertFalse(asked.get("compile"))


class StuckGuardTest(unittest.TestCase):
    """audit 2026-09-28 (minor): when chunk progression stalls (the overlap
    leaves no new frames) the loop breaks -- and the clip used to be
    finalized as a success with only the frames written so far. The loop can
    only end short through that break, so a short clip is now an error: the
    writers abort (WriterTest: the previous output is kept) and the batch
    counts the clip failed. Checked on the source: tripping the guard for real
    needs the Wan model stack."""

    def test_a_short_clip_fails_instead_of_finishing(self):
        import inspect
        from studio.stages.inpainting_v2 import engine as E
        src = inspect.getsource(E.run_inpaint)
        loop_end = src.index("Chunk progression stuck")
        after = src[loop_end:src.index("completed = True", loop_end)]
        self.assertIn("if global_len < total_frames:", after)
        self.assertIn("raise RuntimeError", after)


class WriterTest(unittest.TestCase):
    """audit 2026-09-28 #4: re-running a clip and then Stop / an error must
    not cost its previous good output. The writer encodes to a .part file
    and only a clean close() puts it under the final name (real ffmpeg)."""

    @classmethod
    def setUpClass(cls):
        from studio.stages.inpainting_v2 import engine as E   # torch + diffusers: ~9 s
        cls.E = E

    def setUp(self):
        import numpy as np
        self.tmp = Path(tempfile.mkdtemp())
        self.final = self.tmp / "clip_right_eye.mp4"
        self.old = b"the previous good output"
        self.final.write_bytes(self.old)
        self.frames = np.zeros((4, 16, 16, 3), dtype=np.uint8)

    def encode_a_while(self, w):
        """Enough frames, and time, for ffmpeg to have opened its output."""
        for _ in range(6):
            w.append(self.frames)
        w.proc.stdin.flush()
        time.sleep(0.8)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def writer(self):
        return self.E.StreamingVideoWriter(str(self.final), 24, 16, 16)

    def test_stop_keeps_the_previous_output(self):
        w = self.writer()
        self.encode_a_while(w)
        self.assertEqual(self.final.read_bytes(), self.old, "the old output was overwritten while encoding")
        w.abort()
        self.assertEqual(self.final.read_bytes(), self.old, "Stop / an error deleted the previous output")
        self.assertEqual([p.name for p in self.tmp.iterdir()], [self.final.name], "a partial file was left behind")

    # bug sweep 2026-10-09 (A7): a refused replace (the output open in a player) left
    # the .part behind, and stopped the close loop with the other writers still running
    def test_a_refused_replace_leaves_no_part_file(self):
        from unittest import mock
        w = self.writer()
        self.encode_a_while(w)
        with mock.patch.object(self.E.os, "replace", side_effect=PermissionError("in use")):
            with self.assertRaises(PermissionError):
                w.close()
        self.assertEqual(self.final.read_bytes(), self.old)
        self.assertEqual([p.name for p in self.tmp.iterdir()], [self.final.name], "a .part was left behind")

    def test_one_failed_close_still_ends_every_writer(self):
        import inspect
        src = inspect.getsource(self.E)
        loop = src[src.index("first_error = None"):src.index("raise first_error")]
        self.assertIn("writer.abort()", loop)
        self.assertIn("except Exception", loop)

    def test_a_failed_encode_keeps_the_previous_output(self):
        w = self.writer()
        self.encode_a_while(w)
        w.proc.kill()                     # ffmpeg dies mid-encode
        w.proc.wait()
        with self.assertRaises(RuntimeError):
            w.close()
        self.assertEqual(self.final.read_bytes(), self.old)
        self.assertEqual([p.name for p in self.tmp.iterdir()], [self.final.name])

    def test_close_replaces_it(self):
        w = self.writer()
        w.append(self.frames)
        self.assertEqual(w.close(), str(self.final))
        data = self.final.read_bytes()
        self.assertNotEqual(data, self.old)
        self.assertIn(b"ftyp", data[:64])
        self.assertEqual([p.name for p in self.tmp.iterdir()], [self.final.name])


class PolishTest(unittest.TestCase):
    """bug sweep 2026-10-09 (A7, polish): the finish didn't say how many clips
    Resume skipped, and a number box took any text (fieldRejected was never sent)."""

    def test_the_finish_counts_the_resume_skips(self):
        import threading
        from types import SimpleNamespace
        from unittest import mock
        from studio.stages.inpainting_v2 import engine as E
        cls = next(c for c in vars(E).values() if isinstance(c, type) and hasattr(c, "_finish_verdict"))
        said = []
        fake = SimpleNamespace(_halted=False, stop_event=threading.Event(), _resume_skipped=3,
                               _record_job_handoff=lambda: None,
                               hooks=SimpleNamespace(toast=lambda kind, title, text: said.append(text)))
        with mock.patch("studio.outcome.announce"):
            cls._finish_verdict(fake, 2, 0)
        self.assertIn("3 skipped", said[0])

    def test_a_number_box_refuses_text(self):
        from types import SimpleNamespace
        from studio.stages.inpainting_v2.controller import InpaintingV2Controller as Ctl
        store, rejected = {"frames_chunk": "1.5.2"}, []
        fake = SimpleNamespace(_map=SimpleNamespace(insert=lambda k, v: store.__setitem__(k, v)),
                               _last_good={"frames_chunk": "81"},
                               fieldRejected=SimpleNamespace(emit=rejected.append),
                               _save_timer=SimpleNamespace(start=lambda: None))
        Ctl._on_value_changed(fake, "frames_chunk", "1.5.2")
        self.assertEqual((store["frames_chunk"], rejected), ("81", ["frames_chunk"]))
        Ctl._on_value_changed(fake, "frames_chunk", "97")
        self.assertEqual(fake._last_good["frames_chunk"], "97")


if __name__ == "__main__":
    unittest.main()
