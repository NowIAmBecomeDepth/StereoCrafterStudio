"""Tests for v2 Merging: the generated engine and the settings.

The batch test runs v1's own run_batch_process for real, on CPU, over a
synthetic finished dual-eye SBS clip (``_inpainted_dual_sbs``: v1 passes it
through without a splat partner), in temp folders -- encode, progress, the
L/R split, the finished-folder cleanup thread and the job handoff (redirected).

    .venv\\Scripts\\python.exe -m unittest studio.tests.test_merging -v
"""

from __future__ import annotations

import glob
import json
import shutil
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

# As the app does (app._utf8_streams): run with its output piped, the batch's
# progress bar (a '█') otherwise dies in the cp1252 code page.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="backslashreplace")
    except Exception:
        pass
import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from studio.legacy import install as _install_legacy  # noqa: E402

_install_legacy()

from studio.stages.merging import settings as S  # noqa: E402
try:                    # the Prototype's generators don't ship (release, 2026-10-17)
    from studio.tools import gen_merging_engine as gen  # noqa: E402
except ImportError:
    gen = None


@unittest.skipUnless(studio.tests.PROTOTYPE, studio.tests.NO_PROTOTYPE)
class SyncTest(unittest.TestCase):
    def test_engine_matches_v1(self):
        """engine.py is exactly what the generator makes from today's v1.

        Fails when merging_gui.py has changed since engine.py was generated:
        re-run studio.tools.gen_merging_engine."""
        fresh = gen.build().replace("\n", "\r\n").encode("utf-8")
        self.assertEqual(fresh, gen.TARGET.read_bytes(),
                         "engine.py is stale; re-run: python -m studio.tools.gen_merging_engine")

    def test_preview_engine_matches_v1(self):
        """preview_engine.py likewise, from merging_gui.py and video_previewer.py."""
        from studio.tools import gen_merging_preview as pgen
        fresh = pgen.build().replace("\n", "\r\n").encode("utf-8")
        self.assertEqual(fresh, pgen.TARGET.read_bytes(),
                         "preview_engine.py is stale; re-run: python -m studio.tools.gen_merging_preview")


class SettingsTest(unittest.TestCase):
    def test_seed_keeps_v1_types(self):
        v1 = {"batch_chunk_size": 35, "output_crf": 10, "mask_dilate_kernel_size": 3, "grain_seed": -1,
              "split_lr": True, "nvenc_lookahead": "16", "theme_name": "Ink", "window_x": 0, "stray": 1}
        s = S.seed_from_v1(v1)
        self.assertEqual(s["batch_chunk_size"], "35")      # StringVar(value=str(...))
        self.assertEqual(s["output_crf"], "10")
        self.assertEqual(s["mask_dilate_kernel_size"], 3.0)  # DoubleVar
        self.assertEqual(s["grain_seed"], "-1")
        self.assertEqual(s["nvenc_lookahead"], 16)           # IntVar
        self.assertIs(s["split_lr"], True)
        self.assertEqual((s["theme_name"], s["window_x"]), ("Ink", 0))   # passed through
        self.assertNotIn("stray", s)

    def test_session_start_is_v1s(self):
        s = S.at_session_start(S.normalise({"add_borders": False, "preview_size": "100%"}))
        self.assertIs(s["add_borders"], True)
        self.assertEqual(s["preview_size"], "30%")

    @unittest.skipUnless(studio.tests.PROTOTYPE, studio.tests.NO_PROTOTYPE)
    def test_mask_sliders_are_v1s(self):
        src = (REPO / "merging_gui.py").read_text(encoding="utf-8")
        for key, label, *_ in S.MASK_SLIDERS:
            self.assertIn(f'"{label}"', src)
            self.assertIn(f"self.{key}_var", src)


class SettingsFileTest(unittest.TestCase):
    """audit 2026-09-28 (minor): Save Settings wrote output_format "Split L/R
    Output" -- a Studio choice v1 does not have, so v1 could not load the
    file. It is written as v1 spells it (Full SBS + Split L/R + Delete SBS),
    and read back as the one choice."""

    def test_split_lr_is_saved_as_v1_spells_it_and_read_back(self):
        values = S.normalise({"output_format": S.SPLIT_LR_FORMAT})
        data = S.settings_file_data(values)
        self.assertEqual((data["output_format"], data["split_lr"], data["delete_sbs_after_split"]),
                         ("Full SBS (Left-Right)", True, True))
        self.assertIn(data["output_format"], S.OUTPUT_FORMATS)
        self.assertEqual(S.from_settings_file(data)["output_format"], S.SPLIT_LR_FORMAT)

    def test_other_formats_are_untouched(self):
        for fmt in ("Half SBS (Left-Right)", "Anaglyph Half-Color"):
            data = S.settings_file_data(S.normalise({"output_format": fmt}))
            self.assertEqual(data["output_format"], fmt)
            self.assertEqual(S.from_settings_file(data)["output_format"], fmt)
        plain = {"output_format": "Full SBS (Left-Right)", "split_lr": True, "delete_sbs_after_split": False}
        self.assertEqual(S.from_settings_file(plain)["output_format"], "Full SBS (Left-Right)")


class GrainScanTest(unittest.TestCase):
    """audit 2026-09-28 (minor): the suggested-frame scan finishing moved Grain
    Match to its best frame even after the user had picked one."""

    def grain(self):
        from studio.stages.merging.grain import GrainMatch
        g = GrainMatch(eng=None, on_scan_done=lambda top4: None)
        g.open, g.n_frames, g.frame, g.suggestions = True, 100, 0, None
        return g

    def test_the_scan_lands_on_its_best_frame_when_the_user_has_not_moved(self):
        g = self.grain()
        g.scan_done([(0.9, 40), (0.5, 10)])
        self.assertEqual(g.frame, 40)
        self.assertEqual([x["frame"] for x in g.suggestions], [40, 10])

    def test_the_scan_leaves_a_frame_the_user_picked(self):
        g = self.grain()
        g.seek(73)
        g.scan_done([(0.9, 40), (0.5, 10)])
        self.assertEqual(g.frame, 73, "the scan jumped away from the user's frame")
        self.assertEqual([x["frame"] for x in g.suggestions], [40, 10])


class GrainDuringJobTest(unittest.TestCase):
    """audit 2026-09-28 (minor): with Grain Match open, a Merging job released
    the preview but a setting change (or a Grain Match control) still
    rendered Grain Match on the GPU while the job ran."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtCore import QCoreApplication
        from studio.tests._qt import app as _qt_app
        cls.app = _qt_app()   # a GUI app: the whole suite shares it (tests/_qt.py)

    def setUp(self):
        from studio.stages.merging.preview import MergingPreview
        pv = self.pv = MergingPreview(lambda: {})
        pv._thread = object()                   # no worker: jobs stay queued
        pv._synced = True

        class _G:
            open, renders, frame, images = True, 0, 0, {}
            def render(g): g.renders += 1
            def seek(g, f): g.frame = f
            def state(g): return {}
        self.g = pv._grain = _G()
        pv._grain_open = True

    def tearDown(self):
        self.pv._grain_timer.stop()

    def test_nothing_renders_while_the_job_holds_the_gpu_and_it_catches_up_after(self):
        pv, g = self.pv, self.g
        pv.release_for_job(lambda: None)
        pv._grain_job(None, "seek", [12])
        pv._grain_job(None, "render", [])
        self.assertEqual(g.frame, 12, "the op itself still applies")
        self.assertEqual(g.renders, 0, "Grain Match rendered on the GPU during the job")
        pv._jobs.clear()
        pv.restore_after_job()
        self.assertIn(("grain", "render"), [(j[0], j[1].get("op")) for j in pv._jobs if j is not None and j[0] == "grain"],
                      "no catch-up render after the job")

    def test_it_renders_as_before_when_no_job_runs(self):
        self.pv._grain_job(None, "seek", [3])
        self.assertEqual(self.g.renders, 1)


class ReleaseQueueTest(unittest.TestCase):
    """bug sweep 2026-10-09 #14: the preview kept ONE release callback, so a
    second release_for_job while the first was pending overwrote it -- that
    caller's Start / Move / hand-over never ran, until a restart."""

    @classmethod
    def setUpClass(cls):
        from studio.tests._qt import app as _qt_app
        cls.app = _qt_app()   # a GUI app: the whole suite shares it (tests/_qt.py)

    def test_two_releases_each_call_back_in_turn(self):
        from studio.stages.merging.preview import MergingPreview as Preview
        pv = Preview(lambda: {})
        pv._thread = object()                   # no worker: jobs stay queued
        pv._synced = True
        pv._engine = object()                   # something to let go of
        called = []
        pv.release_for_job(lambda: called.append("start"))
        pv.release_for_job(lambda: called.append("move"))
        self.assertEqual([j[0] for j in pv._jobs if j is not None].count("release"), 2)
        pv._on_done({"kind": "release", "error": "x"})       # the first release's result
        self.assertEqual(called, ["start"])
        pv._on_done({"kind": "release", "error": "x"})
        self.assertEqual(called, ["start", "move"])

    def test_nothing_to_release_calls_back_at_once(self):
        from studio.stages.merging.preview import MergingPreview as Preview
        pv = Preview(lambda: {})
        pv._thread = object()
        called = []
        pv.release_for_job(lambda: called.append("go"))
        self.assertEqual(called, ["go"])


class PlayCacheTest(unittest.TestCase):
    """bug sweep 2026-10-09 (A8): a play frame already on the worker when a setting
    changed was cached after the drop, and replayed the old look on every loop."""

    @classmethod
    def setUpClass(cls):
        from studio.tests._qt import app as _qt_app
        cls.app = _qt_app()

    def play_one(self, change_midway):
        from PySide6.QtGui import QImage
        from studio.stages.merging.preview import MergingPreview as Preview
        pv = Preview(lambda: {})
        pv._thread = object()                   # no worker: jobs stay queued
        pv._synced = True
        pv._playing, pv._frames, pv._frame = True, 10, 0
        pv._play_tick()                          # frame 1 goes to the worker
        self.assertTrue(pv._play_inflight)
        if change_midway:
            pv.note_change("mask_dilate_kernel_size", 41.0)
        pv.provider.put("frame", QImage(8, 8, QImage.Format_RGB888))
        pv._on_done({"kind": "play", "frame": 1, "frames": 10, "index": 0, "count": 1, "loaded": True,
                     "writes": {}, "info": {}})
        return pv._play_cache

    def test_a_frame_drawn_before_a_change_is_not_cached(self):
        self.assertNotIn(1, self.play_one(change_midway=True))

    def test_an_unchanged_frame_is_cached(self):
        self.assertIn(1, self.play_one(change_midway=False))


class FrameGenTest(unittest.TestCase):
    """bug sweep 2026-10-09 (A8): the flip card landed on the next frame, which during
    playback was a play frame already on the worker with the old side. Each job now
    carries the settings generation it is drawn with, and the frame on screen says it."""

    @classmethod
    def setUpClass(cls):
        from studio.tests._qt import app as _qt_app
        cls.app = _qt_app()

    def test_a_frame_says_which_settings_drew_it(self):
        from studio.stages.merging.preview import MergingPreview as Preview
        pv = Preview(lambda: {})
        pv._thread = object()                   # no worker: jobs stay queued
        pv._synced = True
        pv._playing, pv._frames, pv._frame, pv._loaded = True, 10, 0, True
        pv._play_tick()                          # a play frame, old side
        old = pv._jobs[-1][3]
        pv.note_change("preview_source", "Blended Image")   # the card's swap
        self.assertGreater(pv.gen, old)
        pv._submit("render")
        self.assertEqual(pv._jobs[-1][3], pv.gen)
        done = {"frames": 10, "index": 0, "count": 1, "loaded": True, "writes": {}, "info": {}}
        pv._on_done(dict(done, kind="play", frame=1, gen=old))
        self.assertLess(pv.frameGen, pv.gen, "the in-flight play frame is not the new side")
        pv._on_done(dict(done, kind="render", frame=1, gen=pv.gen))
        self.assertEqual(pv.frameGen, pv.gen)


class MaskSideTest(unittest.TestCase):
    """bug sweep 2026-10-09 #19: Load Preview opened every dual-eye grid and
    decoded its frame 0 to find the mask side, ~0.1-0.3 s a clip on every
    Load. Now found for a clip when it is first shown, and kept."""

    def grid(self, path):
        import numpy as np
        rng = np.random.default_rng(0)
        h, w = 96, 128
        frame = np.zeros((h, w, 3), np.uint8)
        frame[:, : w // 2] = rng.integers(40, 216, (h, w // 2, 3), dtype=np.uint8)   # the warped picture
        frame[:, w // 2:] = 0
        frame[: h // 2, w // 2 + 16: w - 16] = 255                                   # the mask, on the RIGHT
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
                        "-r", "12", "-i", "-", "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv444p", str(path)],
                       input=frame.tobytes() * 3, check=True)

    def test_found_once_when_first_shown(self):
        from studio.stages.merging.mask_side import mask_side_of
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "c_splatted2D.mp4"
            self.grid(path)
            src = {"is_dual_eye_sbs": True, "splatted": str(path)}
            self.assertEqual(mask_side_of(src), "right")
            path.unlink()                                     # kept: not read again
            self.assertEqual(mask_side_of(src), "right")
        self.assertEqual(mask_side_of({"is_dual_eye_sbs": False}), "left")

    def test_the_scan_opens_no_grid(self):
        import inspect
        from studio.stages.merging.preview_engine import MergingPreviewEngine
        scan = inspect.getsource(MergingPreviewEngine._find_preview_sources_callback)
        self.assertNotIn("detect_mask_panel_side", scan)
        self.assertNotIn("VideoReader(", scan)


class _Hooks:
    """The controller's side, without Qt: posts run on the test's thread."""

    def __init__(self):
        self.vars, self.messages, self.runs, self.toasts = [], [], [], []
        self._posted = []
        self._lock = threading.Lock()

    def var(self, key, value):
        self.vars.append((key, value))

    def message(self, kind, title, text):
        self.messages.append((kind, title, text))

    def toast(self, kind, title, text):
        self.toasts.append((kind, title, text))

    def post(self, delay_ms, fn):
        with self._lock:
            self._posted.append(fn)

    def running(self, on):
        self.runs.append(on)

    def claim(self):
        return True

    def release(self):
        pass

    def release_preview(self):
        pass

    def info_from_preview(self):
        pass

    def pump(self):
        with self._lock:
            todo, self._posted = self._posted, []
        for fn in todo:
            fn()
        return len(todo)


class EncoderWaitTest(unittest.TestCase):
    """bug sweep 2026-10-09 (A8): a slow encoder flush past 120 s was killed and its
    finished merge discarded. The wait now lasts while ffmpeg runs, until Stop."""

    def test_a_long_flush_is_waited_for_and_stop_still_ends_it(self):
        import subprocess as sp
        from studio.stages.merging import engine as E

        class Proc:
            def __init__(self, polls):
                self.polls = polls

            def wait(self, timeout=None):
                if self.polls > 0:
                    self.polls -= 1
                    raise sp.TimeoutExpired("ffmpeg", timeout)
                return 0

        stop = threading.Event()
        E._wait_for_encoder(Proc(200), stop, poll=0.0)          # 200 polls: far past any fixed limit
        stop.set()
        with self.assertRaises(sp.TimeoutExpired):
            E._wait_for_encoder(Proc(10**9), stop, poll=0.0)
        self.assertNotIn("wait(timeout=120)", Path(E.__file__).read_text(encoding="utf-8"))


class EngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from studio.stages.merging import engine as E
        cls.E = E

    def setUp(self):
        import core.ui.job_handoff as jh
        self.tmp = Path(tempfile.mkdtemp())
        self._rec = jh._RECORD_PATH
        jh._RECORD_PATH = str(self.tmp / "last_jobs.json")
        self.values = S.at_session_start(S.normalise({}))
        self.hooks = _Hooks()
        self.eng = self.E.MergingEngine(self.values, self.hooks)

    def tearDown(self):
        import core.ui.job_handoff as jh
        jh._RECORD_PATH = self._rec

    # user, 2026-10-09: no original for a single-eye clip -- ask before merging it right
    # eye only (it used to be refused outright; before that it ran unnoticed). Continue
    # merges it without colour transfer, whose reference would be a black frame.
    def test_a_missing_original_is_asked_about_then_merged_right_eye_only(self):
        sys.path.insert(0, str(REPO / "studio" / "tests" / "checks"))
        import _fixtures
        fix = _fixtures.copy("merge", self.tmp / "fix")
        (fix / "orig" / "mA.mp4").unlink()
        out = self.tmp / "out"
        asked = []
        self.hooks.ask_missing_originals = lambda missing, folder: asked.append((list(missing), folder))
        settings = S.current_settings(S.normalise({
            "inpainted_folder": str(fix / "inpainted"), "original_folder": str(fix / "orig"),
            "mask_folder": str(fix / "mask"), "output_folder": str(out), "output_format": "Full SBS (Left-Right)",
            "encoding_encoder": "Force CPU", "codec": "H.264", "use_gpu": False, "resume": False,
            "split_lr": False, "dnxhr_fullres_split": False, "grain_enable": False,
            "enable_color_transfer": True}))
        clip = str(fix / "inpainted" / "mA_320_inpainted_right_eye.mp4")
        self.eng.run_batch_process(dict(settings), single_video_path=clip)
        while self.hooks.pump():
            pass
        self.assertEqual(asked, [(["mA"], str(fix / "orig"))])
        self.assertFalse(out.exists() and any(out.glob("*.mp4")), "merged before the answer")
        self.assertEqual(self.hooks.toasts, [], "a failure toast for a question")
        self.assertTrue(self.eng.status_label_var.get().startswith("Waiting"), self.eng.status_label_var.get())

        self.eng.accept_missing_originals = frozenset({"mA"})        # Continue
        self.eng.stop_event.clear()
        self.eng.run_batch_process(dict(settings), single_video_path=clip)
        while self.hooks.pump():
            pass
        made = sorted(p.name for p in out.glob("*.mp4"))
        self.assertEqual(len(made), 1, made)
        self.assertIn("right", made[0].lower(), "not written as the right eye only")
        self.assertEqual(len(asked), 1, "asked again after Continue")

    # bug sweep 2026-10-09 (A8): a refused start or a failed batch left the status
    # line reading "Processing completed." under the "Merging failed" toast
    def test_the_status_line_follows_how_the_batch_ended(self):
        from studio.outcome import Outcome
        cases = [(Outcome(error="Original missing for mA"), False, "Merging failed."),
                 (Outcome(done=2), False, "Processing completed."),
                 (Outcome(done=1), True, "Processing stopped."),
                 (Outcome(), False, "Merging: nothing processed.")]
        for outcome, stopped, want in cases:
            self.eng.outcome = outcome
            self.eng.processing_done(stopped)
            self.assertEqual(self.eng.status_label_var.get(), want, (outcome, stopped))

    # audit 2026-09-28 (minor): both anaglyph formats wrote <clip>_merged_anaglyph
    # at the same width -- the second one run replaced the first.
    def test_the_two_anaglyph_formats_write_different_files(self):
        sys.path.insert(0, str(REPO / "studio" / "tests" / "checks"))
        import _fixtures
        fix = _fixtures.copy("merge", self.tmp / "fix")
        out = self.tmp / "out"
        names = []
        for fmt in ("Anaglyph (Red/Cyan)", "Anaglyph Half-Color"):
            settings = S.current_settings(S.normalise({
                "inpainted_folder": str(fix / "inpainted"), "original_folder": str(fix / "orig"),
                "mask_folder": str(fix / "mask"), "output_folder": str(out), "output_format": fmt,
                "encoding_encoder": "Force CPU", "codec": "H.264", "use_gpu": False, "resume": False,
                "split_lr": False, "dnxhr_fullres_split": False, "grain_enable": False,
                "enable_color_transfer": False}))
            before = set(p.name for p in out.glob("*.mp4")) if out.exists() else set()
            self.eng.stop_event.clear()
            self.eng.run_batch_process(settings, single_video_path=str(fix / "inpainted" / "mA_320_inpainted_right_eye.mp4"))
            new = sorted(set(p.name for p in out.glob("*.mp4")) - before)
            names.append(new)
        self.assertTrue(all(len(n) == 1 for n in names), f"each format should add its own file: {names}")
        self.assertNotEqual(names[0], names[1])

    # audit 2026-09-28 (minor): Split L/R re-encoded each eye with a blocking
    # ffmpeg (minutes at 4K) that Stop could not end, straight onto the final
    # _L / _R names -- a stopped or failed split left a partial eye there.
    def _sbs(self, seconds):
        sbs = self.tmp / "clip_1920_merged_full_sbs.mp4"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        f"testsrc2=size=3840x1080:rate=24:duration={seconds}", "-c:v", "libx264",
                        "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(sbs)], check=True)
        return sbs

    def _split_settings(self):
        return {"codec": "H.264", "encoding_encoder": "Force CPU", "output_crf": 18,
                "pix_fmt": "Auto", "bit_depth": "Auto", "dnxhr_fullres_split": False}

    def test_split_writes_both_eyes_whole(self):
        sbs = self._sbs(1)
        self.eng.stop_event.clear()
        ok = self.eng._split_lr_output(str(sbs), "Full SBS (Left-Right)", self._split_settings())
        names = sorted(p.name for p in self.tmp.iterdir() if p.is_file())
        self.assertTrue(ok)
        self.assertEqual(names, ["clip_1920_merged_full_sbs.mp4", "clip_1920_merged_full_sbs_L.mp4",
                                 "clip_1920_merged_full_sbs_R.mp4"])

    def test_stop_ends_the_split_and_leaves_no_partial_eye(self):
        sbs = self._sbs(20)
        self.eng.stop_event.clear()
        threading.Timer(0.8, self.eng.stop_event.set).start()
        t0 = time.monotonic()
        ok = self.eng._split_lr_output(str(sbs), "Full SBS (Left-Right)", self._split_settings())
        took = time.monotonic() - t0
        self.eng.stop_event.clear()
        names = sorted(p.name for p in self.tmp.iterdir() if p.is_file())
        self.assertFalse(ok)
        self.assertLess(took, 5, "the split ran on after Stop")
        self.assertEqual(names, ["clip_1920_merged_full_sbs.mp4"], "a partial eye was left")

    # audit 2026-09-28 #10: a file that can never be moved (held open by a
    # player, path too long, disk full) used to be retried every 3 s forever;
    # the cleanup thread never ended, so Merging stayed "busy" and every other
    # stage was refused. It gives up now, keeps the file for the next Start,
    # and says so once.
    def _stuck_worker(self, move):
        saved = self.E.move_single_file
        self.E.move_single_file = move
        self.addCleanup(setattr, self.E, "move_single_file", saved)
        self.eng.move_retries, self.eng.move_retry_delay = 3, 0.0
        src = self.tmp / "clip_320_inpainted_right_eye.mp4"
        src.write_bytes(b"x")
        self.eng.cleanup_queue.put((str(src), str(self.tmp)))
        self.eng.cleanup_queue.put(None)
        t = threading.Thread(target=self.eng._cleanup_worker, daemon=True)
        t.start()
        t.join(timeout=15)
        return t, src

    def test_cleanup_gives_up_on_a_file_that_never_moves(self):
        tries = []

        def locked(src, dest, logger=None):
            tries.append(src)
            return False, "[WinError 32] The process cannot access the file"
        t, src = self._stuck_worker(locked)
        self.assertFalse(t.is_alive(), "the cleanup thread never ended -- Merging would stay busy")
        self.assertEqual(len(tries), 3)
        self.assertEqual(self.eng.failed_moves, [(str(src), str(self.tmp))])
        warned = [x for x in self.hooks.toasts if x[0] == "warning"]
        self.assertEqual(len(warned), 1, self.hooks.toasts)
        self.assertIn(src.name, warned[0][2])

    def test_next_start_retries_the_files_it_gave_up_on(self):
        moved = []
        self.eng.failed_moves = [(str(self.tmp / "a.mp4"), str(self.tmp))]
        (self.tmp / "a.mp4").write_bytes(b"x")
        saved = self.E.move_single_file

        def ok(src, dest, logger=None):
            moved.append(src)
            return True, ""
        self.E.move_single_file = ok
        self.addCleanup(setattr, self.E, "move_single_file", saved)
        self.eng.run_batch_process = lambda *a, **k: None
        self.eng.start_processing()
        self.eng.cleanup_queue.put(None)
        self.eng.cleanup_thread.join(timeout=15)
        self.assertEqual(moved, [str(self.tmp / "a.mp4")])
        self.assertEqual(self.eng.failed_moves, [])

    # audit 2026-09-28 (minor): Batch Chunk Size 0 crashed the merge
    # (range(..., 0)) and a negative one wrote an empty clip.
    def test_chunk_size_below_one_is_refused_and_never_reaches_the_job(self):
        import types
        from studio.stages.merging.controller import MergingController
        rejected, stored = [], {}
        fake = types.SimpleNamespace(
            _map=types.SimpleNamespace(insert=lambda k, v: stored.__setitem__(k, v)),
            fieldRejected=types.SimpleNamespace(emit=rejected.append),
            _last_good={"batch_chunk_size": "20"}, _chapter_applying=False,
            encodingChanged=types.SimpleNamespace(emit=lambda: None))
        for bad in ("0", "-4"):
            rejected.clear()
            MergingController._on_value_changed(fake, "batch_chunk_size", bad)
            self.assertEqual((rejected, stored.get("batch_chunk_size")), (["batch_chunk_size"], "20"), bad)
        cfg = S.current_settings(S.normalise({"batch_chunk_size": "0"}))   # an old config holding 0
        self.assertEqual(cfg["batch_chunk_size"], 1)

    def test_current_settings_matches_v1(self):
        """settings.current_settings == v1's get_current_settings (generated)."""
        self.values.update({"batch_chunk_size": "35", "output_crf": "12", "mask_dilate_kernel_size": 7.6,
                            "shadow_shift": 4.4, "grain_seed": "12.9", "preview_brightness": 1.5,
                            "split_lr": True, "encoding_encoder": "Force CPU"})
        self.assertEqual(S.current_settings(self.values), self.eng.get_current_settings())
        self.values["grain_seed"] = "not a number"
        self.assertEqual(S.current_settings(self.values)["grain_seed"], self.eng.get_current_settings()["grain_seed"])

    def test_bad_number_is_v1s_dialog(self):
        self.values["batch_chunk_size"] = "lots"
        self.assertIsNone(self.eng.get_current_settings())
        self.assertEqual(self.hooks.messages[-1][1], "Invalid Settings")
        with self.assertRaises(ValueError):
            S.current_settings(self.values)

    def _dual_sbs_fixture(self):
        inp, out = self.tmp / "inpainted", self.tmp / "merged"
        inp.mkdir()
        clip = inp / "shotA_640_inpainted_dual_sbs.mp4"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=size=640x360:rate=24:duration=1", "-filter_complex", "[0]split[a][b];[a][b]hstack",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)], check=True)
        self.values.update({"inpainted_folder": str(inp), "original_folder": str(self.tmp / "orig"),
                            "mask_folder": str(self.tmp / "mask"), "output_folder": str(out),
                            "encoding_encoder": "Force CPU", "codec": "H.264", "bit_depth": "Auto",
                            "pix_fmt": "Auto", "batch_chunk_size": "8", "resume": True})
        return inp, out, clip

    def _run(self, timeout=120):
        end = time.time() + timeout
        while time.time() < end:
            self.hooks.pump()
            if self.hooks.runs and self.hooks.runs[-1] is False and not self.eng.is_busy():
                self.hooks.pump()
                return
            time.sleep(0.05)
        self.fail("batch did not finish")

    def test_batch_dual_sbs_passthrough(self):
        inp, out, clip = self._dual_sbs_fixture()
        self.values.update({"split_lr": True, "output_format": "Full SBS (Left-Right)"})
        self.eng.start_processing()           # v1: cleanup thread, settings, the worker
        self._run()
        made = sorted(os.path.basename(p) for p in glob.glob(str(out / "**" / "*.mp4"), recursive=True))
        self.assertTrue(any(m.endswith(".mp4") and "_L" not in m and "_R" not in m for m in made), made)
        self.assertTrue(any("_L" in m for m in made) and any("_R" in m for m in made), f"L/R split: {made}")
        statuses = [v for k, v in self.hooks.vars if k == "status_label"]
        self.assertEqual(statuses[-1], "Processing completed.", statuses[-4:])
        self.assertIn(("processing_clip_count", "1 / 1"), self.hooks.vars)
        self.assertIn(("progress_maximum", 1), self.hooks.vars)
        self.assertEqual(self.hooks.runs, [True, False])
        # Resume: the consumed clip went to finished/ (v1's cleanup thread).
        self.assertTrue((inp / "finished" / clip.name).exists())
        rec = json.loads((self.tmp / "last_jobs.json").read_text())
        self.assertEqual(rec["merging"]["merged_out"], str(out))

    def test_resume_nothing_left(self):
        inp, out, clip = self._dual_sbs_fixture()
        (inp / "finished").mkdir()
        shutil.copy(clip, inp / "finished" / clip.name)      # the very same file
        self.eng.start_processing()
        self._run()
        # audit #3: said once, by the finish toast, and nothing handed on
        self.assertEqual(self.hooks.toasts[-1][0], "warning")
        self.assertEqual(self.hooks.toasts[-1][2], "All videos have already been processed (Resume mode).")
        self.assertFalse(out.exists() and list(out.glob("*.mp4")))
        self.assertFalse((self.tmp / "last_jobs.json").exists()
                         and "merging" in json.loads((self.tmp / "last_jobs.json").read_text()))

    # audit 2026-09-28 #8: re-inpainted after its old version went to
    # finished/ -- merged, and archived beside the old one, never deleted
    def test_resume_merges_a_re_inpainted_clip(self):
        inp, out, clip = self._dual_sbs_fixture()
        (inp / "finished").mkdir()
        (inp / "finished" / clip.name).write_bytes(b"the old inpaint")
        new = clip.read_bytes()
        self.eng.start_processing()
        self._run()
        self.assertTrue(out.exists() and list(out.glob("*.mp4")), "the re-inpainted clip was skipped")
        archived = sorted(p.name for p in (inp / "finished").iterdir())
        self.assertEqual((inp / "finished" / clip.name).read_bytes(), b"the old inpaint")
        self.assertEqual(len(archived), 2, archived)
        self.assertIn(new, [p.read_bytes() for p in (inp / "finished").iterdir()],
                      "the consumed clip was deleted instead of archived")

    # audit 2026-09-28: the preview and Upcoming Jobs list .mkv clips; the
    # batch only looked for .mp4 / .mov and silently skipped them
    def test_an_mkv_clip_is_merged(self):
        inp, out, clip = self._dual_sbs_fixture()
        mkv = inp / "shotM_640_inpainted_dual_sbs.mkv"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(clip), "-c", "copy", str(mkv)], check=True)
        clip.unlink()
        self.eng.start_processing()
        self._run()
        made = [os.path.basename(p) for p in glob.glob(str(out / "**" / "*.mp4"), recursive=True)]
        self.assertTrue(made and all("shotM" in m for m in made), f"the .mkv clip was not merged: {made}")

    # user, 2026-10-27: "my merging outputs have been missing real 10 bit info". Decord read every
    # input as 8-bit RGB, about 6 codes (of 1023) darker than FFmpeg's BT.709. A 10-bit grey ramp
    # rising 1 code every 8 px, merged as a dual-eye passthrough (no grain, no colour transfer: the
    # read and the write are all between them), comes out with its 1-code steps and its level.
    def test_grain_match_measures_its_frames_in_frame_order(self):
        """user 2026-10-27, "why does grain match feel slow?": it rendered its picks best-fill
        first, back and forth through the clip, and every jump back restarted the FFmpeg readers
        (49 restarts, 24 s of a clip's 30). In frame order they only run forward; the answer, a
        median, is the same."""
        import numpy as np
        from studio.stages.merging import engine as E
        eng = E.MergingEngine.__new__(E.MergingEngine)
        n = 40
        cover = {fi: 0.02 + ((fi * 7) % n) / 1000.0 for fi in range(n)}   # best fill all over the clip

        class _Splat:
            def get_batch(self, idx):
                return type("B", (), {"asnumpy": lambda s: np.full((1, 4, 4, 3), idx[0], np.uint8)})()

        order = []
        eng._grain_mask_coverage = lambda f_np, ctx: cover[int(f_np[0, 0, 0])]
        eng._render_grain_frame = lambda ctx, fi, params, grained=True: order.append(fi) or {"x": fi}
        eng._grain_match_stats = lambda res, **k: {"ok": True, "amount": res["x"] / 1000.0, "master_fallback": False,
                                                   "master_sigma": 0.0, "fill_sigma_raw": 0.0, "gain": 0.5}
        amount, info = eng._automatch_grain_for_clip(
            {"splat_reader": _Splat()}, n, {"grain_size": 1.0, "grain_luma_falloff": 0.0, "grain_chroma": 0.0},
            "clip", n_samples=24)
        self.assertEqual(len(order), 24)
        self.assertEqual(order, sorted(order), "the picks jump back and forth through the clip")

    @unittest.skipUnless(__import__("torch").cuda.is_available(), "needs CUDA")
    def test_grain_match_measures_on_the_gpu_and_agrees_with_the_cpu(self):
        """user 2026-10-27: Grain Match's measuring ran on the CPU even with Use GPU on (17 -> 10 s a
        clip on CUDA). Same frame, same seeded probe: the GPU's Amount is the CPU's."""
        from unittest import mock
        import torch
        from studio.stages.merging import engine as E
        eng = E.MergingEngine.__new__(E.MergingEngine)
        g = torch.Generator().manual_seed(7)
        h, w = 180, 320
        base = torch.linspace(0.3, 0.6, w).expand(3, h, w).clone()
        master = (base + 0.02 * torch.randn(3, h, w, generator=g)).clamp(0, 1)
        blended = (base + 0.004 * torch.randn(3, h, w, generator=g)).clamp(0, 1)
        mask = torch.zeros(1, h, w)
        mask[:, 40:140, 100:220] = 1.0
        res = {"blended": blended, "mask": mask, "original_left": master}
        kw = dict(grain_size=0.5, luma_falloff=0.34, chroma=0.11)
        devices = []
        real = E.measure_grain_sigma
        with mock.patch.object(E, "measure_grain_sigma",
                               side_effect=lambda f, *a, **k: devices.append(f.device.type) or real(f, *a, **k)):
            gpu = eng._grain_match_stats(dict(res), **kw)
        with mock.patch("torch.cuda.is_available", return_value=False):
            cpu = eng._grain_match_stats(dict(res), **kw)
        self.assertTrue(devices and set(devices) == {"cuda"}, f"measured on {set(devices)}")
        self.assertTrue(gpu["ok"] and cpu["ok"] and cpu["amount"] > 0)
        self.assertAlmostEqual(gpu["amount"], cpu["amount"], delta=cpu["amount"] * 1e-3)

    def test_a_10_bit_clip_keeps_its_10_bits_and_its_level(self):
        import numpy as np
        inp, out = self.tmp / "inpainted", self.tmp / "merged"
        inp.mkdir()
        clip = inp / "shotT_640_inpainted_dual_sbs.mp4"
        w, h, n = 640, 360, 6
        ramp = (300 + np.arange(w // 2) // 8).astype(np.uint16)          # 300..339, 1 code per 8 px
        y = np.tile(np.concatenate([ramp, ramp]), (h, 1))
        frame = np.concatenate([y.ravel(), np.full(w * h * 2, 512, np.uint16)]).tobytes()
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "yuv444p10le",
                        "-s", f"{w}x{h}", "-r", "24", "-i", "-", "-c:v", "libx265", "-x265-params",
                        "lossless=1:log-level=error", "-pix_fmt", "yuv444p10le", "-color_range", "tv",
                        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", str(clip)],
                       input=frame * n, check=True)
        self.values.update({"inpainted_folder": str(inp), "original_folder": str(self.tmp / "orig"),
                            "mask_folder": str(self.tmp / "mask"), "output_folder": str(out),
                            "encoding_encoder": "Force CPU", "codec": "H.265", "bit_depth": "10-bit (Main10)",
                            "pix_fmt": "Auto", "output_crf": "4", "batch_chunk_size": "8", "resume": False,
                            "split_lr": False, "output_format": "Full SBS (Left-Right)", "grain_enable": False,
                            "enable_color_transfer": False, "use_gpu": False})
        self.eng.start_processing()
        self._run()
        made = glob.glob(str(out / "**" / "*.mp4"), recursive=True)
        self.assertEqual(len(made), 1, made)
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", made[0], "-frames:v", "1", "-vf", "format=yuv444p10le",
                              "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
        W = len(raw) // (h * 3 * 2)
        got = np.frombuffer(raw, np.uint16)[:W * h].reshape(h, W).astype(np.float64)
        prof = got[h // 4: 3 * h // 4, 16: W // 2 - 16].mean(axis=0)     # one eye, rows averaged: no codec noise
        want = ramp[16 * W // w: (W // 2 - 16) * w // W].astype(np.float64)
        self.assertEqual(W, w, "the SBS width")
        self.assertLess(abs(prof.mean() - want.mean()), 1.5, f"darker or lighter by {prof.mean() - want.mean():.2f} codes")
        steps = np.abs(np.diff(np.round(prof)))
        self.assertLessEqual(steps.max(), 1.0, f"steps of {steps.max():.0f} codes: an 8-bit read (4 codes a step)")

    def test_single_file_in_the_inpainted_field(self):
        """v1's Select File: the batch takes a single clip in the folder field."""
        inp, out, clip = self._dual_sbs_fixture()
        other = inp / "shotB_640_inpainted_dual_sbs.mp4"
        other.write_bytes(clip.read_bytes())
        self.values["inpainted_folder"] = str(other)
        self.eng.start_processing()
        self._run()
        made = [os.path.basename(p) for p in glob.glob(str(out / "**" / "*.mp4"), recursive=True)]
        self.assertTrue(made and all("shotB" in m for m in made), made)

    def test_restore_finished_asks_first(self):
        inp, out, clip = self._dual_sbs_fixture()
        (inp / "finished").mkdir()
        clip.rename(inp / "finished" / clip.name)
        self.eng.restore_finished_files()                 # answer False: v1 returns
        self.assertFalse(clip.exists())
        self.E.messagebox.answer = True
        try:
            self.eng.restore_finished_files()
        finally:
            self.E.messagebox.answer = False
        self.assertTrue(clip.exists())
        self.assertEqual(self.hooks.messages[-1][1], "Restore Complete")
        self.assertEqual(self.hooks.messages[-1][2], "1 file restored.")     # describe_restore (#9)


if __name__ == "__main__":
    unittest.main()
