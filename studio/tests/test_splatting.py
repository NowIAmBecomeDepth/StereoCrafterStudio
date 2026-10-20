"""Tests for v2 Splatting: settings, the controller's run path, and parity
with v1.

``SyncTest`` builds v1's own SplatterGUI (withdrawn, configs redirected, its
config save stubbed), sets non-default values, and asserts v1's
``_get_processing_settings()`` equals what v2 builds from the config v1 would
save -- every ProcessingSettings field. It fails when v1's settings path
changes: port the change into ``stages/splatting/settings.py``.

The controller tests never render: the batch processor is a fake that plays
the render's progress messages into the real queue.

    .venv\\Scripts\\python.exe -m unittest studio.tests.test_splatting -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from studio.legacy import install as _install_legacy  # noqa: E402

_install_legacy()

from studio.stages.splatting import settings as S  # noqa: E402


class SettingsTests(unittest.TestCase):
    def test_seed_applies_v1_load_remaps(self):
        v1 = {"convergence_point": "1.7", "multi_map_enabled": True, "enable_full_resolution": False,
              "enable_low_resolution": True, "depth_boost_amount": 0.4, "depth_boost_prefilter_radius": 3,
              "frame_break_erode_left": -2.0, "frame_break_edge_blur": 1.5, "encoding_encoder": "Force CPU",
              "encoder": "NVENC",
              "max_disp": "40", "not_a_setting": 1}
        s = S.normalise(S.seed_from_v1(v1))
        self.assertEqual(s["zero_disparity_anchor"], "1.00")      # clamped, as v1 does on load
        self.assertTrue(s["multi_map"])
        self.assertFalse(s["enable_full_res"])
        self.assertTrue(s["enable_low_res"])
        self.assertEqual(s["boost_amount"], 0.4)
        # Edge Clean left the page (user, 2026-10-11): a saved radius is held at 0
        self.assertEqual(s["boost_prefilter_radius"], 0)
        self.assertEqual(s["fb_erode_left"], -2.0)
        self.assertEqual(s["fb_edge_blur"], 1.5)
        self.assertEqual(s["encoder"], "Force CPU")
        self.assertEqual(s["max_disp"], "40")
        self.assertNotIn("not_a_setting", s)

    def test_types_follow_v1_variables(self):
        s = S.normalise({"max_disp": 25, "dual_eye": "true", "encoding_nvenc_lookahead": "20",
                         "boost_amount": "0.5"})
        self.assertEqual(s["max_disp"], "25")                     # StringVar
        self.assertIs(s["dual_eye"], True)                        # BooleanVar
        self.assertEqual(s["encoding_nvenc_lookahead"], 20)       # IntVar
        self.assertEqual(s["boost_amount"], 0.5)                  # DoubleVar

    def test_missing_folders_fall_back_to_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = S.restore_folders(S.normalise({"input_source_clips": tmp,
                                               "input_depth_maps": os.path.join(tmp, "gone")}))
        self.assertEqual(s["input_source_clips"], tmp)
        self.assertEqual(s["input_depth_maps"], S.DEFAULTS["input_depth_maps"])

    def test_a_single_file_input_survives_a_restart(self):
        """Select File: a clip and its depth map, kept while they exist (2026-10-16: reset to the defaults
        on every restart; the output stays a folder)."""
        with tempfile.TemporaryDirectory() as tmp:
            clip, depth = os.path.join(tmp, "shot.mkv"), os.path.join(tmp, "shot_depth.mp4")
            for p in (clip, depth):
                open(p, "wb").close()
            s = S.restore_folders(S.normalise({"input_source_clips": clip, "input_depth_maps": depth,
                                               "output_splatted": clip}))
            self.assertEqual((s["input_source_clips"], s["input_depth_maps"]), (clip, depth))
            self.assertEqual(s["output_splatted"], S.DEFAULTS["output_splatted"])
            os.remove(depth)
            s = S.restore_folders(S.normalise({"input_depth_maps": depth}))
            self.assertEqual(s["input_depth_maps"], S.DEFAULTS["input_depth_maps"])

    def test_clip_range_only_while_ticked(self):
        self.assertEqual(S.processing_range({"custom_clip_range": False, "process_from": "3"}), (0, None))
        # Custom Batch Clip Range is gone (user, 2026-10-01): a range saved ticked
        # in an older config no longer narrows the batch
        self.assertEqual(S.processing_range({"custom_clip_range": True, "process_from": "3",
                                             "process_to": "9"}), (0, None))
        self.assertEqual(S._v1_processing_range({"custom_clip_range": True, "process_from": "3",
                                                 "process_to": "9"}), (2, 9))

    def test_job_fields_use_forward_slashes(self):
        f = S.job_fields({"input_source_clips": "A:/s", "input_depth_maps": "A:/d", "output_splatted": "A:\\o\\"})
        self.assertEqual((f["hires"], f["lowres"]), ("A:/o/hires", "A:/o/lowres"))

    def test_multi_map_sidecars_live_in_a_subfolder(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(S.sidecar_folder({"multi_map": True, "input_depth_maps": tmp}),
                             os.path.join(tmp, "sidecars"))
            self.assertTrue(os.path.isdir(os.path.join(tmp, "sidecars")))
            self.assertEqual(S.sidecar_folder({"multi_map": True, "input_depth_maps": tmp,
                                               "selected_depth_map": "x"}, create=False),
                             os.path.join(tmp, "sidecars"))
            self.assertEqual(S.effective_depth_folder({"multi_map": True, "input_depth_maps": tmp,
                                                       "selected_depth_map": "x"}), os.path.join(tmp, "x"))


class KeyframedBoostTest(unittest.TestCase):
    """audit 2026-09-28 (low confidence, settled): the batch handed the boost
    settings -- depth_peak_raw among them -- to the render only when the
    clip's own boost_amount was above 0. A boost keyframed up from 0 then
    rendered on the flat 1.0 anchor (and the default threshold / curve),
    unlike the preview, which has the clip's peak."""

    def _vid(self, amount, keyframe_amounts):
        from core.common import keyframes
        track = keyframes.KeyframeTrack()
        for i, a in enumerate(keyframe_amounts):
            track.set_keyframe(i * 10, "static", {"boost_amount_var": a})
        return {"boost_amount": amount, "boost_thresh": 0.7, "boost_curve": 1.5, "depth_max_raw": 0.62,
                "keyframe_track": track}

    def test_a_boost_keyframed_up_from_zero_is_handed_on(self):
        from core.splatting.batch_processing import boost_kwargs_for
        kw = boost_kwargs_for(self._vid(0.0, [0.0, 3.0]))
        self.assertEqual((kw.get("depth_peak_raw"), kw.get("boost_thresh"), kw.get("boost_curve")), (0.62, 0.7, 1.5))

    def test_no_boost_anywhere_hands_on_nothing(self):
        from core.splatting.batch_processing import boost_kwargs_for
        self.assertEqual(boost_kwargs_for(self._vid(0.0, [0.0])), {})
        self.assertEqual(boost_kwargs_for({"boost_amount": 0.0}), {})

    def test_a_base_boost_is_handed_on_as_before(self):
        from core.splatting.batch_processing import boost_kwargs_for
        self.assertEqual(boost_kwargs_for(self._vid(2.0, []))["boost_amount"], 2.0)


class GlobalNormRangeTest(unittest.TestCase):
    """audit 2026-09-28 (low confidence, settled): with Global Normalization
    on, the preview scanned the clip's depth range through decord (8-bit)
    while it normalises 10-bit frames as 0..1023 codes -- on the 10-bit
    fixture it found 0..253 where the render found 59..939, so the preview
    showed a different normalisation from the render. Both read it the
    render's way now."""

    def test_the_preview_and_the_render_find_the_same_range(self):
        import queue as _q
        sys.path.insert(0, str(Path(__file__).parent / "checks"))
        import _fixtures
        from core.common.sidecar_manager import SidecarConfigManager
        from core.common.video_io import get_video_stream_info
        from core.splatting import BatchProcessor
        from core.splatting.analysis_service import AnalysisService
        path = str(_fixtures.path("hd") / "depth" / "hd_depth.mp4")
        info = get_video_stream_info(path) or {}
        w, h, n = int(info["width"]), int(info["height"]), int(info.get("nb_frames") or 48)
        preview = AnalysisService.compute_clip_global_depth_stats(depth_map_path=path, stop_event=None)
        _raw, lo, hi, _peak = BatchProcessor(_q.Queue(), threading.Event(), SidecarConfigManager()
                                             ).compute_depth_normalization(path, True, n, 8, w, h)
        self.assertEqual((round(preview[0], 3), round(preview[1], 3)), (round(lo, 3), round(hi, 3)))


@unittest.skipUnless(studio.tests.PROTOTYPE, studio.tests.NO_PROTOTYPE)
class PreviewSyncTest(unittest.TestCase):
    """audit 2026-09-28 (tooling): every generated engine has a test that it is
    what its generator makes today -- but Splatting's preview engine had none,
    so a splatting_gui.py or generator change could go unregenerated."""

    def test_preview_engine_matches_its_generator(self):
        from studio.tools import gen_splatting_preview as pgen
        fresh = pgen.build().replace("\n", "\r\n").encode("utf-8")
        self.assertEqual(fresh, pgen.TARGET.read_bytes(),
                         "preview_engine.py is stale; re-run: python -m studio.tools.gen_splatting_preview")


class DepthOnGpuTests(unittest.TestCase):
    """user 2026-09-29: the depth pre-processing runs on the GPU whenever CUDA
    is there -- renders, the preview and the analysis (one function), within
    one 16-bit unit of the CPU's (check_splat_fast_depth)."""

    def test_it_goes_to_the_gpu_with_cuda_and_the_cpu_without(self):
        from unittest import mock
        import numpy as np
        from core.splatting import depth_gpu, depth_processing as dp
        calls = []
        kw = dict(batch_depth_numpy_raw=np.zeros((1, 8, 8), np.float32), depth_gamma=1.0, depth_dilate_size_x=0,
                  depth_dilate_size_y=0, depth_blur_size_x=0, depth_blur_size_y=0, max_raw_value=1.0)
        with mock.patch.object(depth_gpu, "process_depth_batch_gpu", lambda **k: calls.append("gpu") or "g"),                 mock.patch.object(dp, "process_depth_batch_cpu", lambda **k: calls.append("cpu") or "c"):
            with mock.patch("torch.cuda.is_available", return_value=True):
                dp.process_depth_batch(**kw)
            with mock.patch("torch.cuda.is_available", return_value=False):
                dp.process_depth_batch(**kw)
        self.assertEqual(calls, ["gpu", "cpu"])


class _Shell:
    """What the controller asks of the shell."""

    def __init__(self):
        from PySide6.QtCore import QObject, Signal

        class _Sig(QObject):
            projectChanged = Signal()
        self._sig = _Sig()
        self.projectChanged = self._sig.projectChanged
        self.projectFolder = ""

    def listChapters(self):
        return []

    def stageChapter(self, stage):
        return {}

    def stagePaths(self, stage, num, name):
        return {}

    def setStageChapter(self, stage, chapter):
        pass

    def openFolder(self, path):
        return ""


class _FakeProcessor:
    """Plays a two-clip render into the queue, as BatchProcessor does."""

    def __init__(self, q, stop, gate=None, die=False, result=None):
        self.q, self.stop, self.gate, self.die = q, stop, gate, die
        self.result = {"done": 2} if result is None else result
        self.validated = None
        self.ran = None

    def validate_settings(self, settings):
        self.validated = settings
        return True, ""

    def run_batch_process(self, settings, from_index, to_index, video_list):
        self.ran = (settings, from_index, to_index)
        self.q.put(("total", 2))
        self.q.put(("clip", (1, 2)))
        self.q.put(("update_info", {"filename": "a.mp4", "resolution": "1920x1080", "frames": 48}))
        self.q.put(("processed", 1))
        if self.gate is not None:
            self.gate.wait(5)
        if self.die:
            raise SystemExit  # dies without "finished" -- the thread just ends
        self.q.put(("status", "Processing Full-Res for a.mp4"))
        self.q.put(("processed", 2))
        self.q.put(("result", self.result))
        self.q.put("finished")


class ControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtCore import QCoreApplication
        from studio.tests._qt import app as _qt_app
        cls.app = _qt_app()   # a GUI app: the whole suite shares it (tests/_qt.py)

    def setUp(self):
        import core.ui.job_handoff as jh
        import studio.settings_store as ss
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self._saved = (ss.CONFIG_DIR, jh._RECORD_PATH)
        ss.CONFIG_DIR = root / "config"
        jh._RECORD_PATH = str(root / "last_jobs.json")
        self.jh = jh
        for name in ("src", "dep", "out"):
            (root / name).mkdir()
        from studio.stages.splatting.controller import SplattingController
        self.ctl = SplattingController(_Shell())
        for key, name in (("input_source_clips", "src"), ("input_depth_maps", "dep"), ("output_splatted", "out")):
            self.ctl._set(key, str(root / name).replace("\\", "/"))

    def tearDown(self):
        import studio.settings_store as ss
        self.ctl._poll.stop()
        ss.CONFIG_DIR, self.jh._RECORD_PATH = self._saved
        self.tmp.cleanup()

    def wait_idle(self, timeout=5.0):
        end = time.monotonic() + timeout
        while self.ctl.busy and time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.02)
        self.app.processEvents()

    # audit 2026-09-28 (minor): "2.5" in a whole-number field passed the
    # number check, stayed text through from_config, and Start toasted
    # "'<=' not supported between instances of 'str' and 'int'".
    def typed(self, key, text):
        """What a QML field's edit does: the map changes, valueChanged runs."""
        self.ctl._map.insert(key, text)
        self.ctl._on_value_changed(key, text)

    def test_a_whole_number_field_refuses_a_fraction(self):
        rejected = []
        self.ctl.fieldRejected.connect(rejected.append)
        self.typed("batch_size", "2.5")
        self.app.processEvents()
        self.assertEqual(rejected, ["batch_size"])
        self.assertEqual(str(self.ctl._map.value("batch_size")), "10")
        self.typed("batch_size", "1e3")
        self.app.processEvents()
        self.assertEqual(str(self.ctl._map.value("batch_size")), "1000")

    # bug sweep 2026-10-09 (A6): Hole Shift opened at frame 10 kept editing
    # keyframe 10 after the preview moved to 50 (and viewing made keyframes)
    def test_hole_shift_follows_the_frame_shown(self):
        import types
        import numpy as np
        from core.common import dilate_regions as DR
        from studio.stages.splatting.editors import HoleShift
        occ = np.zeros((60, 80), np.float32)
        occ[20:40, 30:45] = 1.0                                  # one hole
        eng = types.SimpleNamespace(
            _dilreg_last_occ=occ, _warp_last_disp=np.zeros((60, 80), np.float32),
            _dilreg_last_splat=np.zeros((60, 80, 3), np.uint8),
            previewer=types.SimpleNamespace(last_loaded_frame_index=10, source_readers=[]),
            dilate_regions=DR.DilateRegions(),
            auto_save_sidecar_var=types.SimpleNamespace(get=lambda: False),
            dilreg_enabled_var=types.SimpleNamespace(get=lambda: True))
        hs = HoleShift(eng)
        self.assertIsNone(hs.start())
        hs.render(); hs.state()
        self.assertEqual(eng.dilate_regions.keyframes, [], "viewing made a keyframe")
        eng.previewer.last_loaded_frame_index = 50              # the user scrubs on
        hs.render()
        self.assertEqual(hs.state()["frame"], 50)
        from unittest import mock
        with mock.patch.object(DR, "hole_mask_to_source_polys", return_value=[[(0, 0), (1, 0), (1, 1)]]):
            hs.click(37 / 80, 30 / 60)                          # select the hole: an edit
        self.assertEqual([k.frame for k in eng.dilate_regions.keyframes], [50], "the edit landed elsewhere")

    # bug sweep 2026-10-09 (A6): Reset to Defaults left Particles, Detect Text,
    # Pop-Out and Boost as they were, so the next batch kept raining / boosting
    def test_reset_to_defaults_resets_the_newer_picture_settings(self):
        from studio.stages.splatting import settings as S
        changed = {"rain_mode": "Rain" if S.DEFAULTS["rain_mode"] != "Rain" else "Off",
                   "boost_amount": 0.77, "pop_amount": 0.66, "submask_grow": 9,
                   "fb_erode_left": 7, "dual_eye": not S.DEFAULTS["dual_eye"]}
        kept = {"encoder": "Force CPU", "process_from": "12", "preview_brightness": 1.7}
        for k, v in {**changed, **kept}.items():
            self.ctl._set(k, v)
        self.ctl.resetDefaults()
        for k in changed:
            self.assertEqual(self.ctl._map.value(k), S.DEFAULTS[k], k)
        for k, v in kept.items():
            self.assertEqual(self.ctl._map.value(k), v, f"{k} was reset")

    # bug sweep 2026-10-09: Process Single Clip handed the batch <dir>/finished, and
    # move_single_file adds its own "finished": the clip, its map and its sidecar
    # landed in finished/finished/, where Restore Finished never looks.
    def test_process_single_clip_moves_into_finished_beside_the_clip(self):
        from core.common.file_organizer import move_files_to_finished
        root = Path(self.tmp.name)
        video, depth = root / "src" / "a.mp4", root / "dep" / "a_depth.mp4"
        video.write_bytes(b"v")
        depth.write_bytes(b"d")
        begun = []
        self.ctl._begin = lambda processor, settings, *a, **k: begun.append(settings)
        self.ctl._on_single_ready({"video": str(video), "depth": str(depth)})
        self.assertEqual(len(begun), 1, "the single-clip run started")
        st = begun[0]
        self.assertTrue(st.move_to_finished)
        self.assertIsNone(st.single_finished_source_folder)
        self.assertIsNone(st.single_finished_depth_folder)
        # what the batch then moves (batch_processing step 4): the clip's own folders
        moved, failed, _ = move_files_to_finished([(str(video), str(video.parent)), (str(depth), str(depth.parent))])
        self.assertEqual((moved, failed), (2, 0))
        self.assertTrue((root / "src" / "finished" / "a.mp4").is_file())
        self.assertTrue((root / "dep" / "finished" / "a_depth.mp4").is_file())
        self.assertFalse((root / "src" / "finished" / "finished").exists())

    def test_a_fraction_from_an_old_config_is_a_plain_message(self):
        from core.splatting import ProcessingSettings
        folders = {"input_source_clips": "s", "input_depth_maps": "d", "output_splatted": "o"}
        self.assertEqual(ProcessingSettings.from_config({**folders, "batch_size": "1e3"}).full_res_batch_size, 1000)
        with self.assertRaisesRegex(ValueError, "whole number"):
            ProcessingSettings.from_config({**folders, "batch_size": "2.5"})

    def test_a_finished_run_reports_and_hands_off(self):
        fake = _FakeProcessor(self.ctl._queue, self.ctl._stop)
        self.ctl._processor = fake
        self.ctl._set("custom_clip_range", True)      # an old saved range: ignored now
        self.ctl._set("process_from", "2")
        self.ctl.start()
        self.assertTrue(self.ctl.busy)
        self.wait_idle()
        self.assertFalse(self.ctl.busy)
        self.assertEqual(self.ctl.status, "Processing finished")
        self.assertEqual(fake.ran[1:], (0, None))
        self.assertEqual(self.ctl.clipCount, "1 / 2")
        self.assertEqual(self.ctl.info["filename"], "a.mp4")
        self.assertEqual(self.ctl.info["frames"], "48")
        record = self.jh.get_source_record("inpainting") or {}
        self.assertTrue(record.get("lowres", "").endswith("/out/lowres"), record)

    # audit 2026-09-28 #20: the renderer's "processed" is FRAMES in the pass;
    # it used to overwrite the task count, so the bar and "Processed tasks:
    # 240/4" ran far past 100%
    def test_overall_progress_stays_in_tasks(self):
        c = self.ctl
        c._busy = True
        seen = []

        def feed(*messages):
            for m in messages:
                c._queue.put(m)
            c._drain()
            seen.append((c.progressDone, c.progressTotal, c.status))

        feed(("total", 4))                                   # 2 clips x (Full, Low)
        for clip, name in ((1, "a"), (2, "b")):
            feed(("clip", (clip, 2)), ("update_info", {"filename": name, "frames": 48}))
            for task in ("Full-Resolution", "Low-Resolution"):
                feed(("status", f"Processing {task} for {name}"))
                feed(("processed", 24))
                feed(("processed", 48))
                feed(("tasks_done", (clip - 1) * 2 + (task == "Low-Resolution") + 1))
        ratios = [d / t for d, t, _ in seen if t]
        self.assertTrue(all(0 <= r <= 1.0 for r in ratios), ratios)
        self.assertEqual(ratios, sorted(ratios), "the bar went backwards")
        self.assertAlmostEqual(ratios[-1], 1.0)
        # half of the first pass = an eighth of the batch
        self.assertAlmostEqual(ratios[3], 1 / 8, places=2)
        counts = [st for _d, _t, st in seen if st.startswith("Processed tasks")]
        self.assertTrue(counts and all(int(st.split(":")[1].split("/")[0]) <= 4 for st in counts), counts)
        c._busy = False

    def test_a_run_that_produced_nothing_is_not_handed_off(self):
        # audit #3: clips without depth maps, an empty folder -- not a success
        toasts = []
        self.ctl.toast.connect(lambda k, t, m: toasts.append((k, t, m)))
        self.ctl._processor = _FakeProcessor(self.ctl._queue, self.ctl._stop, result={"failed": 2})
        self.ctl.start()
        self.wait_idle()
        self.assertEqual(toasts[-1][:2], ("error", "Splatting failed"))
        self.assertIn("2 failed", toasts[-1][2])
        self.assertIsNone(self.jh.get_source_record("inpainting"))

    def test_a_stopped_run_is_not_handed_off(self):
        gate = threading.Event()
        self.ctl._processor = _FakeProcessor(self.ctl._queue, self.ctl._stop, gate=gate)
        self.ctl.start()
        self.ctl.stop()
        gate.set()
        self.wait_idle()
        self.assertEqual(self.ctl.status, "Processing stopped")
        self.assertIsNone(self.jh.get_source_record("inpainting"))

    def test_a_worker_that_dies_does_not_leave_the_stage_busy(self):
        self.ctl._processor = _FakeProcessor(self.ctl._queue, self.ctl._stop, die=True)
        self.ctl.start()
        self.wait_idle()
        self.assertFalse(self.ctl.busy)
        self.assertIn("unexpectedly", self.ctl.status)

    def test_a_bad_number_is_refused(self):
        rejected = []
        self.ctl.fieldRejected.connect(rejected.append)
        self.ctl._map.insert("max_disp", "30.0")
        self.ctl._on_value_changed("max_disp", "30.0")
        self.ctl._map.insert("max_disp", "abc")
        self.ctl._on_value_changed("max_disp", "abc")
        self.assertEqual(rejected, ["max_disp"])
        self.assertEqual(self.ctl._map.value("max_disp"), "30.0")


class ReleaseQueueTest(unittest.TestCase):
    """bug sweep 2026-10-09 #14: the preview kept ONE release callback, so a
    second release_for_job while the first was pending overwrote it -- that
    caller's Start / Move / hand-over never ran, until a restart."""

    @classmethod
    def setUpClass(cls):
        from studio.tests._qt import app as _qt_app
        cls.app = _qt_app()   # a GUI app: the whole suite shares it (tests/_qt.py)

    def test_two_releases_each_call_back_in_turn(self):
        from studio.stages.splatting.preview import SplattingPreview as Preview
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
        from studio.stages.splatting.preview import SplattingPreview as Preview
        pv = Preview(lambda: {})
        pv._thread = object()
        called = []
        pv.release_for_job(lambda: called.append("go"))
        self.assertEqual(called, ["go"])


class LowResPresetTests(unittest.TestCase):
    """Low Res presets and Auto Size (user, 2026-10-11): DepthCrafter's, in
    multiples of 16 (both Inpainting engines take it); none built in."""

    def test_four_suggestions_at_the_set_heights(self):
        # user, 2026-10-11: every width in steps of 16 was far too many -- heights 512,
        # 640, 768 and 960, each with the multiple-of-16 width nearest the ratio
        from studio.resolution_presets import ratio_value, offered
        sizes = S.sizes_for(ratio_value("16:9"))
        self.assertEqual([(w, h) for w, h, _d in sizes], [(912, 512), (1136, 640), (1360, 768), (1712, 960)])
        self.assertTrue(all(w % 16 == 0 for w, _h, _d in S.sizes_for(ratio_value("2.39:1"))))
        # the editor's list adds the sizes saved for the ratio (typed in by hand) and its Auto pick
        keys = [o["key"] for o in offered("16:9", S.sizes_for, [("16:9", [(1408, 768, "768p")])], {"16:9": (1280, 720)})]
        self.assertEqual(keys, ["912x512", "1136x640", "1280x720", "1360x768", "1408x768", "1712x960"])

    def test_auto_size_defaults(self):
        self.assertEqual(S.default_auto_size("16:9"), (1360, 768))
        self.assertEqual(S.default_auto_size("2.39:1"), (1840, 768))
        self.assertIs(S.DEFAULTS["low_res_auto_size"], False)

    def test_nothing_is_built_in(self):
        import tempfile
        from studio.stages.splatting import controller as C
        self.assertFalse(hasattr(C, "RESOLUTION_PRESETS"))
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(S.local_presets(d), [])

    def test_saving_keeps_a_sizes_tag(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, S.LOCAL_PRESETS_FILE), "w", encoding="utf-8") as f:
                f.write('{"presets": [{"category": "2.2:1", "sizes": [[848, 384, "480p"], [1280, 576, "720p"]]}]}')
            S.save_local_presets(d, {"2.2:1": [(848, 384), (1920, 880)]})
            self.assertEqual(S.local_presets(d), [("2.2:1", [(848, 384, "480p"), (1920, 880, None)])])

    def test_one_bad_entry_drops_only_itself(self):
        """bug sweep 2026-10-14c: one bad size read the whole file as empty (and the next save
        rewrote it without its other columns); a 0 height divided by zero in the menus."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, S.LOCAL_PRESETS_FILE), "w", encoding="utf-8") as f:
                f.write('{"presets": [{"category": "Mine", "sizes": [[1024, 432], ["x", 1]]},'
                        ' {"category": "2.2:1", "sizes": [[848, 0], [1280, 576]]}]}')
            self.assertEqual(S.local_presets(d), [("Mine", [(1024, 432, None)]), ("2.2:1", [(1280, 576, None)])])
            S.save_local_presets(d, {"2.2:1": [(848, 384), (0, 0), (-16, 16)]})
            self.assertEqual(S.local_presets(d), [("2.2:1", [(848, 384, None)]), ("Mine", [(1024, 432, None)])])

    def test_a_file_that_wont_read_is_kept_beside(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, S.LOCAL_PRESETS_FILE)
            with open(path, "w", encoding="utf-8") as f:
                f.write('{"presets": [{"category": "Mine", "sizes": [[1024, 432]]},]}')   # a trailing comma
            S.save_local_presets(d, {"2.2:1": [(848, 384)]})
            self.assertTrue(os.path.isfile(path + ".bak"))


class AutoSizeBatchTest(unittest.TestCase):
    """Auto Size in a batch (user, 2026-10-11): a clip of another shape runs its
    Low Res pass at its own shape's size; the others at the page's. The batch
    itself, with the clip's settings and readers stood in (no GPU, no files)."""

    def run_clip(self, path, size_for):
        import queue
        import threading
        from core.splatting.batch_processing import BatchProcessor, ProcessingSettings
        asked = []
        bp = BatchProcessor(queue.Queue(), threading.Event(), None)
        bp._get_video_specific_settings = lambda *a, **k: {"convergence_plane": 0.5, "actual_depth_map_path": "d.mp4"}
        bp._initialize_readers = lambda video, depth, settings, task: asked.append(
            (task.name, task.target_width, task.target_height)) or None
        st = ProcessingSettings(input_source_clips="", input_depth_maps="", output_splatted="",
                                enable_full_resolution=True, enable_low_resolution=True,
                                low_res_width=1408, low_res_height=768, low_res_size_for=size_for)
        bp._process_single_video_orchestration(path, st, None, 0, False)
        return asked

    def test_another_shape_gets_its_own_low_res_size(self):
        size_for = lambda p: (1280, 528) if "wide" in p else None
        self.assertEqual(self.run_clip("wide.mp4", size_for),
                         [("Full-Resolution", -1, -1), ("Low-Resolution", 1280, 528)])
        self.assertEqual(self.run_clip("tv.mp4", size_for),
                         [("Full-Resolution", -1, -1), ("Low-Resolution", 1408, 768)])

    def test_unset_is_the_page_size_for_every_clip(self):
        self.assertEqual(self.run_clip("wide.mp4", None)[1], ("Low-Resolution", 1408, 768))


@unittest.skipUnless(studio.tests.PROTOTYPE, studio.tests.NO_PROTOTYPE)
class SyncTest(unittest.TestCase):
    """v2's ProcessingSettings == v1's, from the config v1 would save.

    Runs _splat_v1_parity.py in its own process: v1's window needs the real
    core.ui package, which this process has replaced (legacy.install)."""

    def test_processing_settings_match_v1(self):
        import json
        import subprocess
        script = Path(__file__).with_name("_splat_v1_parity.py")
        run = subprocess.run([sys.executable, str(script)], cwd=str(REPO), capture_output=True, text=True,
                             timeout=600)
        line = next((ln for ln in run.stdout.splitlines() if ln.startswith("PARITY ")), None)
        if line is None:
            if "TclError" in run.stderr and "display" in run.stderr:  # pragma: no cover - no display
                self.skipTest("no Tk display")
            self.fail("parity script failed: " + run.stderr[-3000:])
        result = json.loads(line[len("PARITY "):])
        self.assertGreater(result["fields"], 50)
        # Studio holds these off on purpose (settings.RETIRED): v1 still carries them
        retired = {"depth_" + k for k in S.RETIRED}
        diff = {k: v for k, v in result["diff"].items() if k not in retired}
        self.assertEqual(diff, {}, "v1's settings path changed: port it into stages/splatting/settings.py")
        self.assertEqual(result["v1_range"], result["v2_range"])
        # The parity run picks "Force CPU": it must reach the render in both.
        self.assertEqual((result["v1_encoder"], result["v2_encoder"]), ("Force CPU", "Force CPU"))


class ExtSourcesCacheTests(unittest.TestCase):
    """bug sweep 2026-10-09 (A6): the Depth Map Editor 3D view's scan of Splatting's
    folders was kept by the two paths and their mtimes alone, so switching Multi-Map or
    the chosen map (or adding a map inside a map subfolder) served the old list."""

    def setUp(self):
        import types
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "src").mkdir(); (root / "dep" / "mapA").mkdir(parents=True); (root / "dep" / "mapB").mkdir()
        var = lambda v: types.SimpleNamespace(get=lambda: v[0], set=lambda x: v.__setitem__(0, x))
        self.multi, self.sel = [False], [""]
        self.scans = []
        self.eng = types.SimpleNamespace(
            input_source_clips_var=var([str(root / "src")]), input_depth_maps_var=var([str(root / "dep")]),
            multi_map_var=var(self.multi), selected_depth_map_var=var(self.sel),
            _find_preview_sources_callback=lambda: self.scans.append(1) or [{"n": len(self.scans)}])
        self.root = root
        self.pv = types.SimpleNamespace()

    def tearDown(self):
        self.tmp.cleanup()

    def sources(self):
        from studio.stages.splatting.preview import SplattingPreview
        return SplattingPreview._ext_sources(self.pv, self.eng)

    def test_kept_while_nothing_changes(self):
        self.sources(); self.sources()
        self.assertEqual(len(self.scans), 1)

    def test_multi_map_and_the_map_rescan(self):
        self.sources()
        self.multi[0] = True
        self.sources()
        self.sel[0] = "mapB"
        self.sources()
        self.assertEqual(len(self.scans), 3)

    def test_a_map_added_in_a_subfolder_rescans(self):
        import os, time
        self.multi[0] = True
        self.sources()
        time.sleep(0.02)
        (self.root / "dep" / "mapA" / "clip_depth.mp4").write_bytes(b"x")
        st = os.stat(self.root / "dep" / "mapA")
        os.utime(self.root / "dep" / "mapA", ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
        self.sources()
        self.assertEqual(len(self.scans), 2)


class PreviewBufferKeyTests(unittest.TestCase):
    """bug sweep 2026-10-09: the warp backend, its settings and Dual Eye were missing
    from the frame buffer's signature, so changing one re-showed the frame already
    drawn (scrubbing to a new frame showed the change)."""

    def test_each_changes_the_key(self):
        from core.ui.preview_buffer import PreviewFrameBuffer
        buf = PreviewFrameBuffer(max_frames=2, max_memory_mb=1)
        base = {"splat_backend": "SC", "m2s_fix_strength": 1.0, "hybrid_sensitivity": 50.0,
                "hybrid_taper": False, "dual_eye": False, "dual_eye_bias": 0.5}
        changed = {"splat_backend": "M2S", "m2s_fix_strength": 1.5, "hybrid_sensitivity": 70.0,
                   "hybrid_taper": True, "dual_eye": True, "dual_eye_bias": 0.3}
        key = buf._generate_signature_from_params(base, "x.mp4")
        for k, v in changed.items():
            self.assertNotEqual(key, buf._generate_signature_from_params({**base, k: v}, "x.mp4"), k)

    def test_the_preview_hands_dual_eye_over(self):
        import inspect
        from studio.stages.splatting.preview_engine import SplattingPreviewEngine
        src = inspect.getsource(SplattingPreviewEngine.get_current_preview_settings)
        for k in ('"dual_eye":', '"dual_eye_bias":', "**self._m2s_render_settings()"):
            self.assertIn(k, src)


class OutOfMemoryHintTest(unittest.TestCase):
    """A render out of graphics memory: the batch's result says to lower Batch (release check 2026-10-17).
    The batch's own steps are stood in for; what's tested is the renderer's flag reaching the result."""

    def run_batch(self, oom):
        import queue
        import threading
        from types import SimpleNamespace
        from unittest import mock
        from core.splatting import batch_processing as B

        class FakeRenderer:
            def __init__(self, **kw):
                pass

        bp = B.BatchProcessor(progress_queue=queue.Queue(), stop_event=threading.Event())

        def orchestrate(video_path, settings, renderer, initial_task_counter, is_single_file_mode):
            if oom:
                renderer.out_of_vram = True
            bp._clip_ok = False
            return 1
        with mock.patch.object(B, "RenderProcessor", FakeRenderer), \
                mock.patch.object(bp, "setup_batch_processing",
                                  return_value=SimpleNamespace(error="", input_videos=["a.mp4"], is_single_file_mode=True)), \
                mock.patch.object(bp, "get_defined_tasks", return_value=[{"type": "hires"}]), \
                mock.patch.object(bp, "_process_single_video_orchestration", side_effect=orchestrate), \
                mock.patch.object(B, "print_job_header", lambda *a, **k: None):
            bp.run_batch_process(SimpleNamespace())
        return bp.result

    def test_out_of_memory_says_lower_batch(self):
        r = self.run_batch(oom=True)
        self.assertEqual(r["failed"], 1)
        self.assertIn("Lower Batch", r.get("hint", ""))

    def test_another_failure_has_no_hint(self):
        r = self.run_batch(oom=False)
        self.assertEqual(r["failed"], 1)
        self.assertNotIn("hint", r)


if __name__ == "__main__":
    unittest.main()
