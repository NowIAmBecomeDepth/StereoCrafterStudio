"""Tests for the v2 DepthCrafter runner and settings -- no GPU, no model.

The model and the segment merge are replaced by fakes; the real
``depthcrafter.utils`` filename conventions are used, so the tests fail if the
runner drifts from what v1 writes on disk. Everything happens in a temp folder.

Run from the repo root with the project venv:
    .venv\\Scripts\\python.exe -m unittest studio.tests.test_depthcrafter_runner -v
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from studio.stages.depthcrafter import runner as R  # noqa: E402
from studio.stages.depthcrafter import settings as S  # noqa: E402


class RecordingHooks(R.Hooks):
    def __init__(self, answers=None):
        self.statuses, self.progress_calls, self.clips, self.asked = [], [], [], []
        self.answers = list(answers or [])

    def status(self, text):
        self.statuses.append(text)

    def progress(self, done, total):
        self.progress_calls.append((done, total))

    def clip(self, filename, resolution, frames):
        self.clips.append((filename, resolution, frames))

    def ask(self, title, message, allow_cancel=True):
        self.asked.append(title)
        return self.answers.pop(0) if self.answers else None


class FakeDemo:
    """Stands in for DepthCrafterDemo: writes a file where the real one would."""

    instances = []
    fail_segments = set()
    full_status = "success"

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        self.runs = []
        self.pipe = None
        FakeDemo.instances.append(self)

    @staticmethod
    def get_latent_checkpoint_path(base_output_folder, basename):
        return os.path.join(base_output_folder, f"{basename}_latents.ckpt")

    raise_in_run = None
    stop_mid_run = False

    def run(self, **kw):
        if FakeDemo.raise_in_run is not None:
            raise FakeDemo.raise_in_run
        if FakeDemo.stop_mid_run and getattr(self, "stop_event", None) is not None:
            # what DepthCrafterDemo does between denoising steps once Stop is set
            self.runs.append(kw)
            self.stop_event.set()
            raise type("JobStopped", (Exception,), {})("stopped by the user")
        from depthcrafter.utils import (get_full_video_output_filename, get_segment_npz_output_filename,
                                        get_segment_output_folder_name, get_sidecar_json_filename)
        self.runs.append(kw)
        info = kw["video_path_or_frames_or_info"]
        base = kw["original_video_basename_override"]
        out = kw["base_output_folder"]
        seg = kw["segment_job_info_param"]
        if seg is not None:
            folder = os.path.join(out, get_segment_output_folder_name(base))
            os.makedirs(folder, exist_ok=True)
            name = get_segment_npz_output_filename(base, seg["segment_id"], seg["total_segments"])
            path = os.path.join(folder, name)
            Path(path).write_bytes(b"npz")
            ok = seg["segment_id"] not in FakeDemo.fail_segments
            meta = {"status": "success" if ok else "failure_fake", "segment_id": seg["segment_id"],
                    "output_segment_filename": name, "frames_in_output_video": 10,
                    "internal_processing_duration_seconds": 2.0, "processed_height": 448, "processed_width": 768}
            Path(get_sidecar_json_filename(path)).write_text(json.dumps(meta))
            return (path if ok else None), meta
        path = os.path.join(out, get_full_video_output_filename(base, "mp4"))
        os.makedirs(out, exist_ok=True)
        Path(path).write_bytes(b"mp4")
        return path, {"status": FakeDemo.full_status, "output_video_filename": os.path.basename(path),
                      "frames_in_output_video": info.get("original_video_raw_frame_count", 0),
                      "internal_processing_duration_seconds": 4.0, "processed_height": 448, "processed_width": 768}


def fake_define(total_segments=3):
    def define_video_segments(video_path_or_folder, original_basename, gui_target_fps_setting,
                              gui_process_length_overall, gui_segment_output_window_frames,
                              gui_segment_output_overlap_frames, source_type,
                              gui_target_height_setting, gui_target_width_setting):
        base = {"video_path": video_path_or_folder, "original_basename": original_basename,
                "original_video_raw_frame_count": 240, "original_video_fps": 24.0,
                "source_type": source_type}
        segs = [{**base, "segment_id": i, "total_segments": total_segments, "is_segment": True,
                 "num_frames_to_load_raw": 110, "start_frame_raw_index": i * 85,
                 "gui_desired_output_window_frames": gui_segment_output_window_frames,
                 "gui_desired_output_overlap_frames": gui_segment_output_overlap_frames}
                for i in range(total_segments)]
        return segs, base
    return define_video_segments


class RunnerTestBase(unittest.TestCase):
    def setUp(self):
        import depthcrafter.utils as utils

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.inp = self.root / "in"
        self.out = self.root / "out"
        self.inp.mkdir()
        FakeDemo.instances.clear()
        FakeDemo.fail_segments = set()
        FakeDemo.stop_mid_run = False
        FakeDemo.raise_in_run = None
        FakeDemo.full_status = "success"
        self.merges = []

        fake_logic = types.ModuleType("depthcrafter.depthcrafter_logic")
        fake_logic.DepthCrafterDemo = FakeDemo
        self._saved_logic = sys.modules.get("depthcrafter.depthcrafter_logic")
        sys.modules["depthcrafter.depthcrafter_logic"] = fake_logic

        self._saved_define = utils.define_video_segments
        utils.define_video_segments = fake_define()
        self.utils = utils

        merges = self.merges

        def merge_depth_segments(**kw):
            merges.append(kw)
            path = os.path.join(kw["output_path_arg"], kw["output_filename_override_base"] + ".mp4")
            Path(path).write_bytes(b"merged")
            return path

        self._saved_merge = R._merge_module
        R._merge_module = lambda: types.SimpleNamespace(merge_depth_segments=merge_depth_segments)
        # the models aren't looked for on this PC (2026-10-20: none there is now an error)
        self.real_model_paths = R.model_paths
        R.model_paths = lambda repo=None: ("U:/unet", "U:/svd")

    def tearDown(self):
        self.utils.define_video_segments = self._saved_define
        R._merge_module = self._saved_merge
        R.model_paths = self.real_model_paths
        if self._saved_logic is not None:
            sys.modules["depthcrafter.depthcrafter_logic"] = self._saved_logic
        else:
            sys.modules.pop("depthcrafter.depthcrafter_logic", None)
        self.tmp.cleanup()

    def opts(self, **overrides):
        data = dict(S.DEFAULTS)
        data.update({"input_dir_or_file_var": str(self.inp), "output_dir": str(self.out)})
        data.update(overrides)
        return S.snapshot(data)

    def clip(self, name):
        p = self.inp / name
        p.write_bytes(b"video")
        return p


class DiscoveryTests(RunnerTestBase):
    def test_batch_folder_finds_videos_and_sequences_only(self):
        self.clip("a.mp4")
        self.clip("b.mkv")
        (self.inp / "notes.txt").write_text("x")
        seq = self.inp / "shot_seq"
        seq.mkdir()
        for i in range(6):
            (seq / f"f{i:03}.png").write_bytes(b"png")
        specs, single = R.discover_sources(str(self.inp))
        self.assertFalse(single)
        self.assertEqual(sorted((s["basename"], s["type"]) for s in specs),
                         [("a", "video_file"), ("b", "video_file"), ("shot_seq", "image_sequence_folder")])

    def test_single_file_is_single_source(self):
        p = self.clip("solo.mov")
        specs, single = R.discover_sources(str(p))
        self.assertTrue(single)
        self.assertEqual(specs, [{"path": str(p), "type": "single_video_file", "basename": "solo"}])

    def test_missing_path_raises(self):
        with self.assertRaises(R.InputError):
            R.discover_sources(str(self.root / "nope"))


class FullVideoTests(RunnerTestBase):
    def test_batch_runs_every_source_and_moves_originals(self):
        self.clip("a.mp4")
        self.clip("b.mp4")
        specs, _ = R.discover_sources(str(self.inp))
        hooks = RecordingHooks()
        runner = R.DepthCrafterRunner(self.opts(), hooks, threading.Event())
        self.assertTrue(runner.run_batch(specs, 42))

        self.assertTrue((self.out / "a_depth.mp4").exists())
        self.assertTrue((self.out / "b_depth.mp4").exists())
        self.assertEqual(sorted(p.name for p in (self.inp / "finished").iterdir()), ["a.mp4", "b.mp4"])
        self.assertEqual(hooks.progress_calls[0], (0, 2))
        self.assertEqual(hooks.progress_calls[-1], (2, 2))
        self.assertEqual(hooks.statuses[-1], "Processing Finished.")
        # Model built once, with the snapshot's options.
        self.assertEqual(len(FakeDemo.instances), 1)
        self.assertEqual(FakeDemo.instances[0].init_kwargs["cpu_offload"], "model")
        run = FakeDemo.instances[0].runs[0]
        self.assertEqual(run["seed"], 42)
        self.assertEqual(run["output_format_config"], "main10_mp4")
        self.assertIsNone(run["output_crf_config"])  # "auto"
        # Clip info reports the processed resolution after the job.
        self.assertEqual(hooks.clips[-1][1], "768x448")

    def test_auto_top_reaches_the_save(self):
        # Percentile Output Norm's Auto Top (user, 2026-10-12): off by default, on when switched
        self.clip("a.mp4")
        specs, _ = R.discover_sources(str(self.inp))
        R.DepthCrafterRunner(self.opts(), RecordingHooks(), threading.Event()).run_batch(specs, 42)
        self.assertIs(FakeDemo.instances[-1].runs[0]["norm_auto_top_config"], False)
        self.clip("b.mp4")
        specs, _ = R.discover_sources(str(self.inp))
        R.DepthCrafterRunner(self.opts(full_percentile_norm_var=True, full_norm_auto_top_var=True),
                             RecordingHooks(), threading.Event()).run_batch(specs, 42)
        self.assertIs(FakeDemo.instances[-1].runs[0]["norm_auto_top_config"], True)

    def test_single_source_is_not_moved(self):
        p = self.clip("solo.mp4")
        specs, single = R.discover_sources(str(p))
        runner = R.DepthCrafterRunner(self.opts(input_dir_or_file_var=str(p)), RecordingHooks(),
                                      threading.Event(), move_originals=not single)
        runner.run_batch(specs, 1)
        self.assertTrue(p.exists())
        self.assertFalse((self.inp / "finished").exists())

    def test_existing_output_asks_and_no_skips(self):
        self.clip("a.mp4")
        self.out.mkdir()
        (self.out / "a_depth.mp4").write_bytes(b"old")
        hooks = RecordingHooks(answers=[False])
        specs, _ = R.discover_sources(str(self.inp))
        R.DepthCrafterRunner(self.opts(), hooks, threading.Event()).run_batch(specs, 1)
        self.assertEqual(hooks.asked, ["Overwrite?"])
        self.assertEqual((self.out / "a_depth.mp4").read_bytes(), b"old")
        self.assertEqual(FakeDemo.instances[0].runs, [])

    def test_cancel_stops_before_next_source(self):
        self.clip("a.mp4")
        self.clip("b.mp4")
        stop = threading.Event()

        class CancelAfterFirst(RecordingHooks):
            def clip(self, filename, resolution, frames):
                super().clip(filename, resolution, frames)
                stop.set()

        hooks = CancelAfterFirst()
        specs, _ = R.discover_sources(str(self.inp))
        finished = R.DepthCrafterRunner(self.opts(), hooks, stop).run_batch(specs, 1)
        self.assertFalse(finished)
        self.assertEqual(len(FakeDemo.instances[0].runs), 1)
        self.assertEqual(hooks.statuses[-1], "Processing Cancelled.")

    # audit 2026-09-28 #13: Stop / Quit stop a clip NOW. The model is handed the
    # stop event and ends at its next denoising step; the runner takes that as a
    # stop (not an error): nothing written, the clip not moved, no next clip.
    def test_stop_mid_clip_is_a_stop_not_an_error(self):
        self.clip("a.mp4")
        self.clip("b.mp4")
        FakeDemo.stop_mid_run = True
        stop = threading.Event()
        hooks = RecordingHooks()
        specs, _ = R.discover_sources(str(self.inp))
        with self.assertNoLogs(R._logger, level="ERROR"):
            finished = R.DepthCrafterRunner(self.opts(), hooks, stop).run_batch(specs, 1)
        self.assertFalse(finished)
        self.assertIs(FakeDemo.instances[0].stop_event, stop)
        self.assertEqual(len(FakeDemo.instances[0].runs), 1)
        self.assertFalse((self.out / "a_depth.mp4").exists())
        self.assertFalse((self.inp / "finished").exists())
        self.assertEqual(hooks.statuses[-1], "Processing Cancelled.")

    def test_stop_at_the_safe_point_leaves_the_model_running(self):
        # "Stop after this segment": the runner's stop only; the model is
        # handed the separate stop-now event, which stays clear.
        self.clip("a.mp4")
        stop, now = threading.Event(), threading.Event()
        specs, _ = R.discover_sources(str(self.inp))
        R.DepthCrafterRunner(self.opts(), RecordingHooks(), stop, stop_now=now).run_batch(specs, 1)
        self.assertIs(FakeDemo.instances[0].stop_event, now)

    # bug sweep 2026-10-09 (A3): "Stop after this clip" during the last (or only)
    # clip left a finished depth map unfinalized -- not moved to finished/, not
    # counted done, no JSON -- and the next Start asked to overwrite it.
    def test_stop_after_this_clip_still_finalizes_it(self):
        self.clip("a.mp4")
        self.clip("b.mp4")
        stop, now = threading.Event(), threading.Event()

        class StopAfterFirst(RecordingHooks):
            def clip(self, filename, resolution, frames):
                super().clip(filename, resolution, frames)
                stop.set()                      # the safe-point Stop, while a.mp4 runs

        specs, _ = R.discover_sources(str(self.inp))
        runner = R.DepthCrafterRunner(self.opts(), StopAfterFirst(), stop, stop_now=now)
        runner.run_batch(specs, 1)
        self.assertTrue((self.out / "a_depth.mp4").exists())
        self.assertTrue((self.inp / "finished" / "a.mp4").exists(), "the finished clip was not moved")
        self.assertTrue((self.inp / "b.mp4").exists(), "the next clip ran (or moved)")
        self.assertEqual(len(FakeDemo.instances[0].runs), 1)

    def test_sidecar_json_written_when_asked(self):
        self.clip("a.mp4")
        specs, _ = R.discover_sources(str(self.inp))
        R.DepthCrafterRunner(self.opts(save_final_output_json_var=True), RecordingHooks(),
                             threading.Event()).run_batch(specs, 1)
        sidecar = json.loads((self.out / "a_depth.json").read_text())
        self.assertEqual(sidecar["processing_mode"], "full_video")

    def test_no_sidecar_when_the_save_failed(self):
        # bug sweep 2026-10-09 #8: the sidecar pointed at a truncated (or an older) map
        self.clip("a.mp4")
        FakeDemo.full_status = "failure_mp4_save"
        specs, _ = R.discover_sources(str(self.inp))
        R.DepthCrafterRunner(self.opts(save_final_output_json_var=True), RecordingHooks(),
                             threading.Event()).run_batch(specs, 1)
        self.assertFalse((self.out / "a_depth.json").exists())


class ModelPathsTest(RunnerTestBase):
    """DepthCrafter loads the models in the models folder (release check 2026-10-17: it only ever asked the
    Hub, so the installer's download sat unused and the first job fetched them again). 2026-10-20: the Hub
    only when its cache already has them; otherwise the run says to download them in Settings > Studio >
    Models, rather than fetching ~5 GB from a gated repo without a word."""

    def setUp(self):
        super().setUp()
        from unittest import mock
        self.cached = set()
        patcher = mock.patch.object(R, "_in_hub_cache", lambda repo, _f: repo in self.cached)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _weights(self, root, svd_names=("stable-video-diffusion-img2vid-xt-1-1",), unet=True):
        from studio import model_catalog as C
        w = os.path.join(root, "weights")
        parts = [(C.SVD, n) for n in svd_names] + ([(C.DEPTHCRAFTER, "DepthCrafter")] if unet else [])
        for part, folder in parts:
            for f, _n in part.files:
                path = os.path.join(w, folder, f)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                Path(path).write_bytes(b"1")
        return w

    def test_missing_says_where_to_get_them(self):
        root = tempfile.mkdtemp(dir=self.tmp.name)
        with self.assertRaises(R.ModelsMissing) as cm:
            self.real_model_paths(root)
        self.assertIn("DepthCrafter and Stable Video Diffusion 1.1 are not in your models folder", str(cm.exception))
        self.assertIn("Settings > Studio > Models", str(cm.exception))

    def test_hub_ids_when_the_cache_has_them(self):
        root = tempfile.mkdtemp(dir=self.tmp.name)
        self.cached = {R.DEPTHCRAFTER_HUB, R.SVD_HUB}
        self.assertEqual(self.real_model_paths(root), (R.DEPTHCRAFTER_HUB, R.SVD_HUB))

    def test_the_models_folders_when_there(self):
        root = tempfile.mkdtemp(dir=self.tmp.name)
        w = self._weights(root)
        self.assertEqual(self.real_model_paths(root), (os.path.join(w, "DepthCrafter"),
                                                       os.path.join(w, "stable-video-diffusion-img2vid-xt-1-1")))

    def test_the_xt_folder_counts_and_an_empty_folder_doesnt(self):
        root = tempfile.mkdtemp(dir=self.tmp.name)
        w = self._weights(root, ("stable-video-diffusion-img2vid-xt",), unet=False)
        os.makedirs(os.path.join(w, "DepthCrafter"))          # an empty folder: not the model
        with self.assertRaises(R.ModelsMissing) as cm:
            self.real_model_paths(root)
        self.assertTrue(str(cm.exception).startswith("DepthCrafter is not in your models folder"), str(cm.exception))
        self.cached = {R.DEPTHCRAFTER_HUB}
        self.assertEqual(self.real_model_paths(root), (R.DEPTHCRAFTER_HUB, os.path.join(w, "stable-video-diffusion-img2vid-xt")))

    def test_the_run_says_what_is_missing(self):
        self.clip("a.mp4")
        specs, _ = R.discover_sources(str(self.inp))
        R.model_paths = lambda repo=None: self.real_model_paths(self.tmp.name)
        runner = R.DepthCrafterRunner(self.opts(), RecordingHooks(), threading.Event())
        self.assertFalse(runner.run_batch(specs, 42))
        self.assertIn("Settings > Studio > Models", runner.outcome.error)
        self.assertEqual(FakeDemo.instances, [], "nothing loaded")

    def test_the_run_loads_what_model_paths_says(self):
        from unittest import mock
        self.clip("a.mp4")
        specs, _ = R.discover_sources(str(self.inp))
        with mock.patch.object(R, "model_paths", return_value=("U:/unet", "U:/svd")):
            R.DepthCrafterRunner(self.opts(), RecordingHooks(), threading.Event()).run_batch(specs, 42)
        kw = FakeDemo.instances[-1].init_kwargs
        self.assertEqual((kw["unet_path"], kw["pre_train_path"]), ("U:/unet", "U:/svd"))


class OutOfMemoryHintTest(RunnerTestBase):
    """Out of graphics memory: the run says to turn on Process as Segments or lower the window / size
    (release check 2026-10-17: a smaller card got only "1 failed")."""

    def run_with(self, exc):
        FakeDemo.raise_in_run = exc
        self.clip("a.mp4")
        specs, _ = R.discover_sources(str(self.inp))
        runner = R.DepthCrafterRunner(self.opts(), RecordingHooks(), threading.Event())
        runner.run_batch(specs, 42)
        return runner.outcome

    def test_out_of_memory_says_what_to_change(self):
        o = self.run_with(type("OutOfMemoryError", (RuntimeError,), {})("CUDA out of memory"))
        self.assertGreaterEqual(o.failed, 1)
        self.assertEqual(o.hint, R.OOM_HINT)
        self.assertIn("Process as Segments", o.hint)

    def test_another_failure_has_no_hint(self):
        o = self.run_with(ValueError("a bad frame"))
        self.assertGreaterEqual(o.failed, 1)
        self.assertEqual(o.hint, "")


class OomRetryTest(unittest.TestCase):
    """bug sweep 2026-10-09 (A3): the retry ran inside the except, whose traceback
    kept the failed attempt's tensors alive, so it began with them still in VRAM."""

    def test_the_failed_attempts_memory_is_gone_before_the_retry(self):
        import gc
        import weakref

        class OutOfMemoryError(Exception):
            pass

        class Big:                                  # stands in for a run's GPU tensors
            pass

        alive_at_retry = []

        class Demo:
            calls = 0

            def run(self, **kw):
                Demo.calls += 1
                if Demo.calls == 1:
                    latents = Big()                  # a local of the failed run
                    Demo.ref = weakref.ref(latents)
                    raise OutOfMemoryError("CUDA out of memory")
                gc.collect()
                alive_at_retry.append(Demo.ref() is not None)
                return "ok", {}

        runner = R.DepthCrafterRunner.__new__(R.DepthCrafterRunner)
        runner._capped = True
        runner._cap_vram = lambda on: False
        self.assertEqual(runner._run_demo(Demo()), ("ok", {}))
        self.assertEqual(alive_at_retry, [False], "the failed run's locals were still alive at the retry")


class SaveJsonTest(unittest.TestCase):
    def test_a_failed_write_leaves_the_old_file(self):
        # bug sweep 2026-10-09 #9: save_json_file wrote in place, so a failure mid-dump truncated it
        from depthcrafter.utils import save_json_file
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "a_master_meta.json")
            self.assertTrue(save_json_file({"jobs_info": [1, 2]}, path))
            self.assertFalse(save_json_file({"jobs_info": [1, object()]}, path))   # not serialisable halfway
            self.assertEqual(json.loads(Path(path).read_text()), {"jobs_info": [1, 2]})
            self.assertEqual(os.listdir(d), ["a_master_meta.json"])


class SaveVideoPartTests(unittest.TestCase):
    """save_video writes <name>.part and renames it into place only when the
    encode finished (bug sweep 2026-10-09 #8). CPU: libx265, no NVENC."""

    def setUp(self):
        import numpy as np
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.final = os.path.join(self.tmp.name, "a_depth.mp4")
        self.frames = np.linspace(0, 1, 6 * 64 * 96, dtype=np.float32).reshape(6, 64, 96)

    def test_a_finished_encode_is_published_whole(self):
        from depthcrafter.utils import save_video
        save_video(self.frames, self.final, fps=24, output_format="main10_mp4", use_nvenc=False)
        self.assertGreater(os.path.getsize(self.final), 0)
        self.assertEqual(os.listdir(self.tmp.name), ["a_depth.mp4"])

    def test_a_failed_encode_leaves_no_partial_and_keeps_the_old_map(self):
        import mediapy
        from depthcrafter import utils
        Path(self.final).write_bytes(b"the earlier finished map")
        written = []

        def dies_halfway(path, frames, **kw):
            written.append(path)
            Path(path).write_bytes(b"truncated")
            raise RuntimeError("ffmpeg died")
        real = mediapy.write_video
        mediapy.write_video = dies_halfway
        self.addCleanup(setattr, mediapy, "write_video", real)
        with self.assertRaises(RuntimeError):
            utils.save_video(self.frames, self.final, fps=24, output_format="main10_mp4", use_nvenc=False)
        self.assertNotEqual(written[0], self.final)
        self.assertEqual(Path(self.final).read_bytes(), b"the earlier finished map")
        self.assertEqual(os.listdir(self.tmp.name), ["a_depth.mp4"])


class StopBetweenStepsTest(unittest.TestCase):
    """The real DepthCrafterDemo (no weights): its pipe call is given a step
    callback that ends the inference once the stop event is set (#13)."""

    def test_inference_ends_at_the_next_step(self):
        import numpy as np
        saved = sys.modules.pop("depthcrafter.depthcrafter_logic", None)
        try:
            import depthcrafter.depthcrafter_logic as L
        finally:
            if saved is not None:
                sys.modules["depthcrafter.depthcrafter_logic"] = saved
        steps = []
        stop = threading.Event()

        class Pipe:
            def __call__(self, frames, callback_on_step_end=None, **kw):
                for i in range(kw["num_inference_steps"]):
                    steps.append(i)
                    if i == 1:
                        stop.set()      # the user presses Stop during step 1
                    if callback_on_step_end is not None:
                        callback_on_step_end(self, i, 0, {"latents": None})
                raise AssertionError("the inference ran to the end")

        demo = L.DepthCrafterDemo.__new__(L.DepthCrafterDemo)
        demo.pipe = Pipe()
        demo.stop_event = stop
        with self.assertRaises(L.JobStopped):
            demo._perform_inference(np.zeros((4, 8, 8, 3), np.float32), 1.0, 5, 4, 0, None, 8, 8)
        self.assertEqual(steps, [0, 1])


class WindowOverlapTests(unittest.TestCase):
    """audit 2026-09-28 #21 / #22: Window and Overlap are checked at Start.
    Full video: Overlap >= Window made the pipeline's window step 0 or
    negative (it never advanced -- a hang) and Overlap 0 crashed after the
    first window. Segments: Overlap above half the Window made the merge
    emit frames twice (the depth drifts against the video)."""

    def problem(self, window, overlap, segments):
        return S.window_overlap_problem({"window_size": window, "overlap": overlap,
                                         "process_as_segments_var": segments})

    def test_full_video_needs_one_to_window_minus_one(self):
        for window, overlap in ((110, 110), (110, 150), (110, 0), (1, 0)):
            self.assertTrue(self.problem(window, overlap, False), (window, overlap))
        for window, overlap in ((110, 1), (110, 25), (110, 109)):
            self.assertEqual(self.problem(window, overlap, False), "", (window, overlap))

    def test_segments_need_at_most_half_the_window(self):
        for window, overlap in ((110, 56), (110, 110), (110, -1)):
            self.assertTrue(self.problem(window, overlap, True), (window, overlap))
        for window, overlap in ((110, 0), (110, 25), (110, 55)):
            self.assertEqual(self.problem(window, overlap, True), "", (window, overlap))

    def test_start_refuses_and_says_why(self):
        from studio.stages.depthcrafter.controller import DepthCrafterController
        said, built = [], []
        values = dict(S.DEFAULTS, window_size=110, overlap=110, process_as_segments_var=False,
                      input_dir_or_file_var=str(REPO))
        fake = types.SimpleNamespace(
            save=lambda: None, _values=lambda: values, _on_status=lambda t: None,
            toast=types.SimpleNamespace(emit=lambda *a: said.append(a)),
            _begin=lambda *a: built.append(a))
        saved = R.discover_sources
        R.discover_sources = lambda path: ([{"path": "x.mp4"}], False)
        try:
            DepthCrafterController.start(fake)
        finally:
            R.discover_sources = saved
        self.assertEqual(built, [], "a run started with Overlap = Window")
        self.assertTrue(said and said[-1][0] == "error" and "Overlap" in said[-1][2], said)


class AutoSizeStartTest(unittest.TestCase):
    """bug sweep 2026-10-09 (A3): Start right after the input changed used the
    previous input's shape (its probe still running), so the runner replaced the
    size the user had just set for the new clips."""

    def test_start_reads_the_shape_of_the_input_it_runs(self):
        import types
        from studio.stages.depthcrafter.controller import DepthCrafterController
        built = []
        values = dict(S.DEFAULTS, auto_resolution_var=True, input_dir_or_file_var="D:/new_scope")
        fake = types.SimpleNamespace(
            save=lambda: None, _values=lambda: values, _on_status=lambda t: None,
            toast=types.SimpleNamespace(emit=lambda *a: None), _begin=lambda *a, **k: built.append(a),
            shapeChanged=types.SimpleNamespace(emit=lambda: None),
            presetsChanged=types.SimpleNamespace(emit=lambda: None),
            _shape="16:9", _shape_for="D:/old_169", _shape_path="D:/old_169",
            _shape_of=DepthCrafterController._shape_of, _stop=threading.Event(), _stop_now=threading.Event())
        saved = (R.discover_sources, S.video_ratio)
        R.discover_sources = lambda path: ([{"path": "scope.mp4", "type": "video_file"}], False)
        S.video_ratio = lambda p: 2.39
        try:
            DepthCrafterController.start(fake)
        finally:
            R.discover_sources, S.video_ratio = saved
        self.assertTrue(built, "nothing started")
        runner = built[0][1].__self__
        self.assertEqual(runner.opts["auto_loaded_class"], S.ratio_class(2.39), "the old input's shape was used")
        self.assertEqual(fake._shape_for, "D:/new_scope")


class HandoffFoldersTest(unittest.TestCase):
    """bug sweep 2026-10-09 (A3): the job handoff read the page's folders when the batch
    ended, so a project switch during the run handed Splatting the new project's folders
    instead of the ones the depth maps were written to."""

    def test_the_handoff_records_the_folders_the_run_used(self):
        import types
        import core.ui.job_handoff as jh
        from studio.outcome import Outcome
        from studio.stages.depthcrafter.controller import DepthCrafterController
        page = {"input_dir_or_file_var": "D:/new_project/clips", "output_dir": "D:/new_project/depth"}
        run = {"input_dir_or_file_var": "D:/old_project/clips", "output_dir": "D:/old_project/depth"}
        sig = types.SimpleNamespace(emit=lambda *a: None)
        fake = types.SimpleNamespace(
            _task="batch", _thread=None, stateChanged=sig, infoChanged=sig, toast=sig, _stop=threading.Event(),
            _map=types.SimpleNamespace(value=page.get),
            _batch_runner=types.SimpleNamespace(opts=run, outcome=Outcome(done=1)),
            queue=types.SimpleNamespace(hold=lambda on: None, rescan=lambda: None))   # Upcoming Jobs (2026-10-10)
        fake._record_job_handoff = lambda opts=None: DepthCrafterController._record_job_handoff(fake, opts)
        got, saved = [], jh.record_job
        jh.record_job = lambda stage, paths: got.append((stage, paths))
        try:
            DepthCrafterController._on_finished(fake, "batch", True)
        finally:
            jh.record_job = saved
        self.assertEqual(got, [("depthcrafter", {"source": run["input_dir_or_file_var"], "depth": run["output_dir"]})])


class RefusedStartTest(unittest.TestCase):
    """bug sweep 2026-10-09 (A3): with another stage running, Start's refusal (the GPU
    claim) came after it had reset the bar to 0 / N, the Filename row to N/A and the
    status -- the last run's figures were lost to a run that never began."""

    def test_a_refused_start_leaves_the_figures(self):
        import types
        from studio.stages.depthcrafter.controller import DepthCrafterController
        statuses = []
        values = dict(S.DEFAULTS, input_dir_or_file_var="D:/clips", auto_resolution_var=False)
        fake = types.SimpleNamespace(
            save=lambda: None, _values=lambda: values, _on_status=statuses.append,
            toast=types.SimpleNamespace(emit=lambda *a: None), _thread=None, _claim=lambda: False,
            _stop=threading.Event(), _stop_now=threading.Event(),
            _clip=("last.mp4", "12.0 s", "1920x1080"), _done=7, _total=7, _batch_runner=None, _shape=None)
        fake._begin = lambda *a, **k: DepthCrafterController._begin(fake, *a, **k)
        saved = R.discover_sources
        R.discover_sources = lambda path: ([{"path": "a.mp4", "type": "video_file"}] * 3, False)
        try:
            DepthCrafterController.start(fake)
        finally:
            R.discover_sources = saved
        self.assertEqual((fake._clip, fake._done, fake._total), (("last.mp4", "12.0 s", "1920x1080"), 7, 7))
        self.assertIsNone(fake._batch_runner)
        self.assertEqual(statuses[-1], "Ready")


class NonFiniteFieldTest(unittest.TestCase):
    """bug sweep 2026-10-09 (A3): "inf" / "1e999" passed the number check; an int field
    then raised OverflowError on every save and Start, a float field ran with infinity."""

    def test_coerce_gives_the_default(self):
        for text in ("inf", "1e999", "-inf", "nan"):
            self.assertEqual(S.coerce("window_size", text), S.DEFAULTS["window_size"], text)
        float_key = next(k for k, v in S.DEFAULTS.items() if isinstance(v, float) and not isinstance(v, bool))
        for text in ("inf", "1e999", "nan"):
            self.assertEqual(S.coerce(float_key, text), S.DEFAULTS[float_key], (float_key, text))

    def test_the_field_refuses_it(self):
        import types
        from studio.stages.depthcrafter.controller import DepthCrafterController
        rejected, inserted = [], {}
        fake = types.SimpleNamespace(
            _map=types.SimpleNamespace(insert=lambda k, v: inserted.__setitem__(k, v)),
            _last_good={"window_size": 110}, fieldRejected=types.SimpleNamespace(emit=rejected.append))
        DepthCrafterController._on_value_changed(fake, "window_size", "1e999")
        self.assertEqual((rejected, inserted), (["window_size"], {"window_size": 110}))


class ImageSourceTests(unittest.TestCase):
    """audit 2026-09-28 #24: an image-sequence folder or a single image went
    through _load_frames without its processed size being recorded, so every
    such job ended in a KeyError. The real DepthCrafterDemo, no weights."""

    @classmethod
    def setUpClass(cls):
        saved = sys.modules.pop("depthcrafter.depthcrafter_logic", None)
        try:
            import depthcrafter.depthcrafter_logic as L
        finally:
            if saved is not None:
                sys.modules["depthcrafter.depthcrafter_logic"] = saved
        cls.L = L

    def load(self, source):
        demo = self.L.DepthCrafterDemo.__new__(self.L.DepthCrafterDemo)
        meta = {}
        frames, fps, h, w = demo._load_frames(
            video_path_or_job_info=source, frames_array_if_provided=None, process_length_for_read=-1,
            user_target_height=192, user_target_width=320, segment_job_info=None, job_specific_metadata=meta)
        return frames, h, w

    def images(self, n):
        import numpy as np
        from PIL import Image
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for i in range(n):
            Image.fromarray(np.full((90, 160, 3), 40 * i, np.uint8)).save(Path(tmp.name) / f"f{i:03d}.png")
        return Path(tmp.name)

    def test_image_sequence_loads(self):
        folder = self.images(3)
        frames, h, w = self.load({"type": "image_sequence_folder", "path": str(folder)})
        self.assertEqual((len(frames), h, w), (3, 192, 320))
        self.assertEqual(frames.shape[1:3], (192, 320))

    def test_single_image_loads(self):
        folder = self.images(1)
        frames, h, w = self.load({"type": "single_image_file", "path": str(folder / "f000.png")})
        self.assertEqual((h, w), (192, 320))
        self.assertEqual(frames.shape[1:3], (192, 320))


class VaeUpcastTest(unittest.TestCase):
    """The VAE encode runs in fp16 (2026-10-11; it was always upcast to fp32 for
    SVD's force_upcast) and only overflowed latents are encoded again upcast.
    audit 2026-09-28 #25: the upcast VAE is cast back even when that encode
    raised (out of memory on a long clip), or every later clip ran on an fp32
    VAE. A stand-in VAE, no weights."""

    def setUp(self):
        import torch
        self.torch = torch
        saved = sys.modules.pop("depthcrafter.depth_crafter_ppl", None)
        try:
            import depthcrafter.depth_crafter_ppl as P
        finally:
            if saved is not None:
                sys.modules["depthcrafter.depth_crafter_ppl"] = saved
        self.P = P

        class Vae:
            dtype = torch.float16
            config = types.SimpleNamespace(force_upcast=True)

            def to(self, dtype=None):
                self.dtype = dtype
                return self

        self.vae = Vae()

    def test_a_finite_encode_stays_in_fp16(self):
        torch = self.torch
        seen = []

        def encode(video, chunk_size, target_dtype, progress_cb=None):
            seen.append(target_dtype)
            return torch.ones(2, 4, 1, 1, dtype=target_dtype)
        out = self.P.encode_video_latents_upcast(self.vae, encode, torch.zeros(2, 3, 8, 8), 8, None)
        self.assertEqual(seen, [torch.float16], "one encode, in fp16")
        self.assertEqual(out.dtype, torch.float16)
        self.assertEqual(self.vae.dtype, torch.float16)

    def test_an_overflowed_encode_runs_again_upcast(self):
        torch = self.torch
        seen = []

        def encode(video, chunk_size, target_dtype, progress_cb=None):
            seen.append(target_dtype)
            fill = float("inf") if target_dtype == torch.float16 else 1.0
            return torch.full((2, 4, 1, 1), fill, dtype=target_dtype)
        out = self.P.encode_video_latents_upcast(self.vae, encode, torch.zeros(2, 3, 8, 8), 8, None)
        self.assertEqual(seen, [torch.float16, torch.float32])
        self.assertTrue(bool(torch.isfinite(out).all()))
        self.assertEqual(self.vae.dtype, torch.float16, "the VAE was left in fp32")

    def test_a_failed_upcast_encode_still_casts_the_vae_back(self):
        torch = self.torch
        seen = []

        def encode(video, chunk_size, target_dtype, progress_cb=None):
            seen.append(target_dtype)
            if target_dtype == torch.float16:
                return torch.full((2, 4, 1, 1), float("nan"), dtype=target_dtype)
            raise torch.cuda.OutOfMemoryError("stand-in")
        with self.assertRaises(torch.cuda.OutOfMemoryError):
            self.P.encode_video_latents_upcast(self.vae, encode, torch.zeros(2, 3, 8, 8), 8, None)
        self.assertEqual(seen, [torch.float16, torch.float32], "the retry should run upcast")
        self.assertEqual(self.vae.dtype, torch.float16, "the VAE was left in fp32 after a failed encode")


class StabiliseApplyTest(unittest.TestCase):
    """2026-10-11: the stabiliser's gain/offset apply runs in the array's own
    float32, in place (it went through float64 temporaries; 13x slower)."""

    def setUp(self):
        import numpy as np
        from depthcrafter.depth_stabilize import apply_affine_series
        rng = np.random.default_rng(0)
        self.np, self.apply = np, apply_affine_series
        self.depth = rng.random((70, 24, 40), dtype=np.float32)
        self.gains = 1 + 0.1 * rng.standard_normal(70)
        self.offsets = 0.05 * rng.standard_normal(70)
        self.want = (self.depth.astype(np.float64) * self.gains[:, None, None]
                     + self.offsets[:, None, None]).astype(np.float32)

    def test_the_same_values_as_float64(self):
        out = self.apply(self.depth.copy(), self.gains, self.offsets, chunk_size=32, in_place=True)
        self.assertEqual(out.dtype, self.np.float32)
        self.assertLess(float(self.np.abs(out - self.want).max()), 1e-6)

    def test_in_place_writes_the_input(self):
        d = self.depth.copy()
        out = self.apply(d, self.gains, self.offsets, in_place=True)
        self.assertIs(out, d)

    def test_not_in_place_leaves_the_input(self):
        d = self.depth.copy()
        out = self.apply(d, self.gains, self.offsets, in_place=False)
        self.assertIsNot(out, d)
        self.assertTrue(self.np.array_equal(d, self.depth))
        self.assertLess(float(self.np.abs(out - self.want).max()), 1e-6)


class ChunkSizeTest(unittest.TestCase):
    """2026-10-21: the decode runs 4 frames a chunk again. 8 (2026-10-11, 7% less
    flicker) reserved 12.7 GB at 768x448 against 6.6 GB, for the same speed, and
    a 12 GB card spilled it into system memory: 3-4x slower around every window.
    The encodes and the noise stay at 4, or the random stream -- and the depth --
    would change."""

    def test_decode_4_encode_4(self):
        import inspect
        saved = sys.modules.pop("depthcrafter.depth_crafter_ppl", None)
        try:
            import depthcrafter.depth_crafter_ppl as P
        finally:
            if saved is not None:
                sys.modules["depthcrafter.depth_crafter_ppl"] = saved
        params = inspect.signature(P.DepthCrafterPipeline.__call__).parameters
        self.assertEqual(params["decode_chunk_size"].default, 4)
        self.assertEqual(params["encode_chunk_size"].default, 4)


class SingleTokenCrossAttentionTest(unittest.TestCase):
    """2026-10-11: cross-attention onto one key token is to_out(to_v(ctx)) for
    every query (softmax over one key is 1), so depthcrafter.attention skips
    the attention. Real diffusers Attention layers on the CPU, random weights."""

    def setUp(self):
        import torch
        from diffusers.models.attention_processor import Attention, AttnProcessor2_0
        from depthcrafter.attention import (SingleTokenCrossAttnProcessor,
                                            shortcut_single_token_cross_attention)
        torch.manual_seed(0)
        self.torch, self.Wrapper, self.shortcut = torch, SingleTokenCrossAttnProcessor, shortcut_single_token_cross_attention
        model = torch.nn.Module()
        model.attn1 = Attention(query_dim=64, heads=4, dim_head=16)
        model.attn2 = Attention(query_dim=64, cross_attention_dim=32, heads=4, dim_head=16)
        for m in (model.attn1, model.attn2):
            m.set_processor(AttnProcessor2_0())
        self.model = model.eval()

    def test_only_the_cross_attention_is_wrapped(self):
        self.assertEqual(self.shortcut(self.model), 1)
        self.assertIsInstance(self.model.attn2.processor, self.Wrapper)
        self.assertNotIsInstance(self.model.attn1.processor, self.Wrapper)
        self.assertEqual(self.shortcut(self.model), 0, "wrapping twice")

    def test_the_same_output_as_the_attention(self):
        torch = self.torch
        x = torch.randn(3, 50, 64)
        ctx = torch.randn(3, 1, 32)
        with torch.no_grad():
            plain = self.model.attn2(x, encoder_hidden_states=ctx)
            self.shortcut(self.model)
            fast = self.model.attn2(x, encoder_hidden_states=ctx)
        self.assertEqual(fast.shape, plain.shape)
        torch.testing.assert_close(fast, plain, rtol=1e-5, atol=1e-5)

    def test_more_than_one_key_token_takes_the_attention(self):
        torch = self.torch
        x = torch.randn(2, 20, 64)
        ctx = torch.randn(2, 5, 32)
        with torch.no_grad():
            plain = self.model.attn2(x, encoder_hidden_states=ctx)
            self.shortcut(self.model)
            wrapped = self.model.attn2(x, encoder_hidden_states=ctx)
        torch.testing.assert_close(wrapped, plain, rtol=0, atol=0)


class SegmentDefinitionDecodeTests(unittest.TestCase):
    """audit 2026-09-28 O4: defining a video's segments decoded the WHOLE
    clip (at 128x128) only to count its frames -- a count ffprobe already
    had. The real define_video_segments on a real 48-frame clip: the same
    segments and metadata as before, and no frame decoded."""

    def test_no_frames_are_decoded_to_count_them(self):
        import subprocess
        import depthcrafter.utils as U
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        clip = Path(tmp.name) / "clip.mp4"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=size=320x180:rate=24", "-frames:v", "48", "-pix_fmt", "yuv420p", str(clip)],
                       check=True)
        decoded = []
        real = U.VideoReader

        class Counting:
            def __init__(self, *a, **kw):
                self._r = real(*a, **kw)

            def get_batch(self, idx):
                decoded.append(len(idx))
                return self._r.get_batch(idx)

            def __getattr__(self, name):
                return getattr(self._r, name)

            def __len__(self):
                return len(self._r)
        U.VideoReader = Counting
        self.addCleanup(setattr, U, "VideoReader", real)
        segments, base = U.define_video_segments(str(clip), "clip", -1, -1, 20, 4, "video_file", 576, 1024)
        self.assertEqual(sum(decoded), 0, f"{sum(decoded)} frames decoded just to count them")
        # what the full decode produced (pinned from the old code on this clip)
        self.assertEqual((base["original_video_raw_frame_count"], base["original_video_fps"],
                          base["original_height"], base["original_width"]), (48, 24.0, 180, 320))
        self.assertTrue(base["video_stream_ffprobe_info"])
        self.assertEqual([(j["start_frame_raw_index"], j["num_frames_to_load_raw"]) for j in segments],
                         [(0, 20), (16, 20), (32, 16)])


class SegmentSaveTests(unittest.TestCase):
    """audit 2026-09-28 O5: each segment was saved with savez_compressed --
    ~3.4 s of CPU a 40-frame segment at 768x448 (11 s for 110 frames at
    1024x576) while the GPU waited, for ~13 % less disk. Saved uncompressed
    now: the same frames back, and old compressed segments still load."""

    def test_segments_are_stored_uncompressed_and_load_back_the_same(self):
        import zipfile
        import numpy as np
        saved = sys.modules.pop("depthcrafter.depthcrafter_logic", None)
        try:
            import depthcrafter.depthcrafter_logic as L
        finally:
            if saved is not None:
                sys.modules["depthcrafter.depthcrafter_logic"] = saved
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        res = np.random.default_rng(0).random((6, 16, 24), dtype=np.float32)
        path = os.path.join(tmp.name, "seg_000.npz")
        demo = L.DepthCrafterDemo.__new__(L.DepthCrafterDemo)
        meta = {}
        self.assertTrue(demo._save_segment_npz(res, path, meta))
        with zipfile.ZipFile(path) as z:
            kinds = {i.compress_type for i in z.infolist()}
        self.assertEqual(kinds, {zipfile.ZIP_STORED}, "the segment was compressed")
        with np.load(path) as data:
            self.assertTrue(np.array_equal(data["frames"], res))
        old = os.path.join(tmp.name, "old_000.npz")      # a segment from before the change
        np.savez_compressed(old, frames=res)
        with np.load(old) as data:
            self.assertTrue(np.array_equal(data["frames"], res))


class SegmentTests(RunnerTestBase):
    def seg_opts(self, **kw):
        return self.opts(process_as_segments_var=True, merge_alignment_method_var="Linear Blend",
                         merge_output_suffix_var="_dm", **kw)

    def test_segments_merge_then_clean_up(self):
        self.clip("a.mp4")
        specs, _ = R.discover_sources(str(self.inp))
        R.DepthCrafterRunner(self.seg_opts(), RecordingHooks(), threading.Event()).run_batch(specs, 7)
        self.assertEqual(len(FakeDemo.instances[0].runs), 3)
        self.assertEqual(len(self.merges), 1)
        kw = self.merges[0]
        self.assertEqual(kw["merge_alignment_method"], "linear_blend")
        self.assertEqual(kw["output_filename_override_base"], "a_dm")
        self.assertTrue(kw["master_meta_path"].endswith(os.path.join("a_seg", "a_master_meta.json")))
        self.assertTrue((self.out / "a_dm.mp4").exists())
        # Keep NPZ off: the segment folder goes once merged.
        self.assertFalse((self.out / "a_seg").exists())

    # audit 2026-09-28 (minor): Output CRF / Preset reached only a clip done
    # whole -- the merged depth of a segmented clip always got the defaults.
    def test_crf_and_preset_reach_the_merged_output(self):
        self.clip("a.mp4")
        specs, _ = R.discover_sources(str(self.inp))
        R.DepthCrafterRunner(self.seg_opts(output_crf_var="12", output_preset_var="slow"), RecordingHooks(),
                             threading.Event()).run_batch(specs, 7)
        kw = self.merges[0]
        self.assertEqual((kw.get("output_crf"), kw.get("output_preset")), (12, "slow"))

    def test_the_merge_encodes_with_them(self):
        import importlib
        import numpy as np
        saved = sys.modules.pop("depthcrafter.merge_depth_segments", None)
        try:
            M = importlib.import_module("depthcrafter.merge_depth_segments")
        finally:
            if saved is not None:
                sys.modules["depthcrafter.merge_depth_segments"] = saved
        got = {}
        real = M.dc_utils.save_video
        M.dc_utils.save_video = lambda frames, path, **kw: got.update(kw)
        self.addCleanup(setattr, M.dc_utils, "save_video", real)
        M._save_output_to_disk(np.zeros((2, 8, 8), np.float32), str(self.root / "x.mp4"), "main10_mp4", 24.0,
                               crf=12, preset="slow")
        self.assertEqual((got.get("crf"), got.get("preset"), got.get("output_format")), (12, "slow", "main10_mp4"))

    def test_keep_npz_keeps_master_meta(self):
        self.clip("a.mp4")
        specs, _ = R.discover_sources(str(self.inp))
        R.DepthCrafterRunner(self.seg_opts(keep_intermediate_npz_var=True), RecordingHooks(),
                             threading.Event()).run_batch(specs, 7)
        meta = json.loads((self.out / "a_seg" / "a_master_meta.json").read_text())
        self.assertEqual(meta["overall_status"], "all_success")
        self.assertEqual(meta["global_processing_settings"]["seed_setting"], 7)
        self.assertEqual(meta["completed_successful_jobs"], 3)

    def test_partial_failure_goes_to_failed(self):
        FakeDemo.fail_segments = {1}
        self.clip("a.mp4")
        specs, _ = R.discover_sources(str(self.inp))
        R.DepthCrafterRunner(self.seg_opts(keep_intermediate_npz_var=True), RecordingHooks(),
                             threading.Event()).run_batch(specs, 7)
        meta = json.loads((self.out / "a_seg" / "a_master_meta.json").read_text())
        self.assertEqual(meta["overall_status"], "partial_success")
        self.assertTrue((self.inp / "failed" / "a.mp4").exists())

    # audit 2026-09-28 #23: a failed segment leaves a hole; merging the rest
    # stitched across it into <clip>_depth.mp4 -- a depth map shorter than the
    # clip, under the final name, that Splatting would read. No merge until
    # every segment is there (Resume fills the hole, then it merges).
    def test_a_failed_segment_is_not_merged_across(self):
        FakeDemo.fail_segments = {1}
        self.clip("a.mp4")
        specs, _ = R.discover_sources(str(self.inp))
        R.DepthCrafterRunner(self.seg_opts(), RecordingHooks(), threading.Event()).run_batch(specs, 7)
        self.assertEqual(self.merges, [], "the segments were merged across the failed one")
        self.assertFalse((self.out / "a_depth.mp4").exists())
        self.assertTrue((self.out / "a_seg").is_dir(), "the finished segments must stay for Resume")

    def test_merge_refuses_a_gap(self):
        # Re-Merge on an old partial run: the merge itself says what is missing
        import importlib
        saved = sys.modules.pop("depthcrafter.merge_depth_segments", None)
        try:
            M = importlib.import_module("depthcrafter.merge_depth_segments")
        finally:
            if saved is not None:
                sys.modules["depthcrafter.merge_depth_segments"] = saved
        meta = self.root / "gap_master_meta.json"
        jobs = [{"segment_id": i, "status": "success", "output_segment_filename": f"s{i}.npz"} for i in (0, 2)]
        jobs.append({"segment_id": 1, "status": "failure_fake"})
        meta.write_text(json.dumps({"global_processing_settings": {"processed_as_segments": True,
                                                                   "segment_definition_output_overlap_frames": 4},
                                    "total_expected_jobs": 3, "jobs_info": jobs}))
        with self.assertRaises(ValueError) as cm:
            M._load_and_validate_metadata(str(meta))
        self.assertIn("2", str(cm.exception))   # segment 2 of 3 (1-based) is the hole

    # audit 2026-09-28, blocker: a failed merge (or a failed segment) must never
    # cost the finished segments, even with Keep NPZ off (the default)
    def test_failed_merge_keeps_the_segments(self):
        def failing_merge(**kw):
            raise MemoryError("merge ran out of memory")
        R._merge_module = lambda: types.SimpleNamespace(merge_depth_segments=failing_merge)
        self.clip("a.mp4")
        specs, _ = R.discover_sources(str(self.inp))
        R.DepthCrafterRunner(self.seg_opts(), RecordingHooks(), threading.Event()).run_batch(specs, 7)
        seg = self.out / "a_seg"
        self.assertTrue(seg.is_dir(), "the segment folder was deleted after a failed merge")
        self.assertTrue((seg / "a_master_meta.json").exists())
        self.assertEqual(len(list(seg.glob("*.npz"))), 3)
        self.assertFalse((self.out / "a_dm.mp4").exists())

    def test_failed_segment_keeps_the_segments_without_keep_npz(self):
        FakeDemo.fail_segments = {1}
        self.clip("a.mp4")
        specs, _ = R.discover_sources(str(self.inp))
        R.DepthCrafterRunner(self.seg_opts(), RecordingHooks(), threading.Event()).run_batch(specs, 7)
        seg = self.out / "a_seg"
        self.assertTrue(seg.is_dir(), "the segment folder was deleted after a failed segment")
        self.assertEqual(json.loads((seg / "a_master_meta.json").read_text())["overall_status"], "partial_success")

    def test_incomplete_folder_resume_runs_only_missing(self):
        self.clip("a.mp4")
        specs, _ = R.discover_sources(str(self.inp))
        # First run keeps its segments, then pretend segment 3 never finished.
        R.DepthCrafterRunner(self.seg_opts(keep_intermediate_npz_var=True), RecordingHooks(),
                             threading.Event(), move_originals=False).run_batch(specs, 7)
        seg = self.out / "a_seg"
        (seg / "a_master_meta.json").unlink()
        (seg / "a_depth_3of3.npz").unlink()
        # The master meta deleted the per-segment JSONs; put back the two finished ones.
        for i in (1, 2):
            (seg / f"a_depth_{i}of3.json").write_text(json.dumps({"status": "success", "segment_id": i - 1,
                                                                  "output_segment_filename": f"a_depth_{i}of3.npz"}))
        FakeDemo.instances.clear()
        hooks = RecordingHooks(answers=[True])
        R.DepthCrafterRunner(self.seg_opts(keep_intermediate_npz_var=True), hooks,
                             threading.Event(), move_originals=False).run_batch(specs, 7)
        self.assertEqual(hooks.asked, ["Resume Incomplete Segments?"])
        runs = FakeDemo.instances[0].runs
        self.assertEqual([r["segment_job_info_param"]["segment_id"] for r in runs], [2])
        meta = json.loads((seg / "a_master_meta.json").read_text())
        self.assertEqual(meta["completed_successful_jobs"], 3)

    # bug sweep 2026-10-09 #9: "Yes, resume" with an unreadable master deleted every segment
    def _finished_then_damaged(self, keep_jsons):
        self.clip("a.mp4")
        specs, _ = R.discover_sources(str(self.inp))
        R.DepthCrafterRunner(self.seg_opts(keep_intermediate_npz_var=True), RecordingHooks(),
                             threading.Event(), move_originals=False).run_batch(specs, 7)
        seg = self.out / "a_seg"
        (seg / "a_master_meta.json").write_text('{"jobs_info": [{"stat')     # a write cut short
        (seg / "a_depth_3of3.npz").unlink()
        if keep_jsons:   # the master save failed, so the segments' own JSONs were kept
            for i in (1, 2):
                (seg / f"a_depth_{i}of3.json").write_text(json.dumps(
                    {"status": "success", "segment_id": i - 1, "output_segment_filename": f"a_depth_{i}of3.npz"}))
        FakeDemo.instances.clear()
        return specs, seg

    def test_unreadable_master_resumes_from_the_segment_files(self):
        specs, seg = self._finished_then_damaged(keep_jsons=True)
        hooks = RecordingHooks(answers=[True])
        R.DepthCrafterRunner(self.seg_opts(keep_intermediate_npz_var=True), hooks,
                             threading.Event(), move_originals=False).run_batch(specs, 7)
        self.assertEqual(hooks.asked, ["Resume or Overwrite Finalized Segments?"])
        runs = FakeDemo.instances[0].runs
        self.assertEqual([r["segment_job_info_param"]["segment_id"] for r in runs], [2])
        self.assertEqual(json.loads((seg / "a_master_meta.json").read_text())["completed_successful_jobs"], 3)
        self.assertEqual(len(list(seg.glob("a_master_meta.json.damaged_*"))), 1)

    def test_unreadable_master_with_nothing_reusable_asks_before_deleting(self):
        specs, seg = self._finished_then_damaged(keep_jsons=False)
        hooks = RecordingHooks(answers=[True, False])
        R.DepthCrafterRunner(self.seg_opts(keep_intermediate_npz_var=True), hooks,
                             threading.Event(), move_originals=False).run_batch(specs, 7)
        self.assertEqual(hooks.asked, ["Resume or Overwrite Finalized Segments?", "Delete the Segments?"])
        self.assertEqual([r for d in FakeDemo.instances for r in d.runs], [])   # skipped: nothing ran
        self.assertEqual(sorted(p.name for p in seg.glob("*.npz")), ["a_depth_1of3.npz", "a_depth_2of3.npz"])

    def test_cancel_at_resume_prompt_skips_source(self):
        self.clip("a.mp4")
        (self.out / "a_seg").mkdir(parents=True)
        hooks = RecordingHooks(answers=[None])
        specs, _ = R.discover_sources(str(self.inp))
        R.DepthCrafterRunner(self.seg_opts(), hooks, threading.Event()).run_batch(specs, 7)
        self.assertEqual(FakeDemo.instances[0].runs, [])
        self.assertTrue((self.inp / "a.mp4").exists())  # not moved: never finalized
        self.assertEqual(hooks.progress_calls[-1], (1, 1))

    def test_remerge_uses_snapshot_suffix(self):
        meta = self.root / "x_master_meta.json"
        meta.write_text("{}")
        (self.root / "outdir").mkdir()
        path = R.DepthCrafterRunner(self.seg_opts(), RecordingHooks(), threading.Event()).run_remerge(
            str(meta), str(self.root / "outdir"))
        self.assertTrue(path.endswith("x_dm.mp4"))


class OutcomeTests(RunnerTestBase):
    """audit 2026-09-28 #3: the batch counts its sources, so a run where
    every clip failed is not reported (and handed on) as a success."""

    def seg_opts(self, **kw):
        return self.opts(process_as_segments_var=True, merge_alignment_method_var="Linear Blend",
                         merge_output_suffix_var="_dm", **kw)

    def run_on(self, opts, hooks=None):
        specs, _ = R.discover_sources(str(self.inp))
        runner = R.DepthCrafterRunner(opts, hooks or RecordingHooks(), threading.Event())
        runner.run_batch(specs, 1)
        return runner.outcome

    def test_all_done(self):
        self.clip("a.mp4"); self.clip("b.mp4")
        o = self.run_on(self.opts())
        self.assertEqual((o.done, o.failed, o.skipped, o.error), (2, 0, 0, ""))

    def test_failed_segment_and_failed_merge_count_as_failed(self):
        FakeDemo.fail_segments = {1}
        self.clip("a.mp4")
        self.assertEqual((self.run_on(self.seg_opts()).failed, len(self.merges)), (1, 0))  # #23: no merge
        FakeDemo.fail_segments = set()
        R._merge_module = lambda: types.SimpleNamespace(
            merge_depth_segments=lambda **kw: (_ for _ in ()).throw(MemoryError("x")))
        self.clip("b.mp4")
        o = self.run_on(self.seg_opts())
        self.assertEqual(o.done, 0)
        self.assertGreaterEqual(o.failed, 1)

    def test_metadata_failure_counts_as_failed(self):
        def broken(**kw):
            raise OSError("unreadable")
        self.utils.define_video_segments = lambda *a, **kw: broken()
        self.clip("a.mp4")
        o = self.run_on(self.opts())
        self.assertEqual((o.done, o.failed), (0, 1))

    def test_declined_overwrite_counts_as_skipped(self):
        self.clip("a.mp4")
        self.out.mkdir()
        (self.out / "a_depth.mp4").write_bytes(b"old")
        o = self.run_on(self.opts(), RecordingHooks(answers=[False]))
        self.assertEqual((o.done, o.failed, o.skipped), (0, 0, 1))

    # audit 2026-09-28 (minor): Cancel on the latent-checkpoint prompt means
    # "skip this video" -- it was counted as a failed job, so the source was
    # moved to failed/ and the run said "1 failed".
    def test_cancel_at_the_checkpoint_prompt_skips_the_clip_and_leaves_it(self):
        self.clip("a.mp4")
        self.out.mkdir(exist_ok=True)
        Path(FakeDemo.get_latent_checkpoint_path(str(self.out), "a")).write_bytes(b"latents")
        hooks = RecordingHooks(answers=[None])
        o = self.run_on(self.opts(latent_checkpoint_var=True), hooks)
        self.assertEqual(hooks.asked, ["Resume From Latent Checkpoint?"])
        self.assertEqual((o.done, o.failed, o.skipped), (0, 0, 1))
        self.assertTrue((self.inp / "a.mp4").exists(), "the skipped source was moved")
        self.assertFalse((self.inp / "failed").exists())
        self.assertEqual(FakeDemo.instances[0].runs, [])

    # audit 2026-09-28 (minor): the model sets torch.backends.cudnn.benchmark
    # for the whole process, and it stayed on for every stage after
    def test_cudnn_benchmark_is_put_back_after_the_run(self):
        import torch

        class Bench(FakeDemo):
            def __init__(self, **kw):
                torch.backends.cudnn.benchmark = bool(kw.get("use_cudnn_benchmark"))
                super().__init__(**kw)

        class BenchFails(FakeDemo):
            def __init__(self, **kw):
                torch.backends.cudnn.benchmark = True
                raise OSError("no weights")
        was = torch.backends.cudnn.benchmark
        self.addCleanup(setattr, torch.backends.cudnn, "benchmark", was)
        for demo in (Bench, BenchFails):
            torch.backends.cudnn.benchmark = False
            sys.modules["depthcrafter.depthcrafter_logic"].DepthCrafterDemo = demo
            self.clip("a.mp4")
            self.run_on(self.opts(use_cudnn_benchmark=True))
            self.assertFalse(torch.backends.cudnn.benchmark, f"{demo.__name__}: left on for the other stages")

    def test_model_load_failure_is_the_error(self):
        class NoModel(FakeDemo):
            def __init__(self, **kw):
                raise OSError("no weights")
        sys.modules["depthcrafter.depthcrafter_logic"].DepthCrafterDemo = NoModel
        self.clip("a.mp4")
        o = self.run_on(self.opts())
        self.assertIn("could not be loaded", o.error)


class VerdictTests(unittest.TestCase):
    """studio/outcome.verdict: the toast and the handoff for each ending."""

    def test_rules(self):
        from studio.outcome import Outcome, verdict
        cases = [
            (Outcome(done=2), False, ("success", True)),
            (Outcome(done=1, failed=1), False, ("warning", True)),
            (Outcome(failed=2), False, ("error", False)),
            (Outcome(), False, ("warning", False)),
            (Outcome(skipped=3), False, ("warning", False)),
            (Outcome(error="model"), False, ("error", False)),
            (Outcome(done=1, error="crash"), False, ("error", True)),
            (Outcome(done=2), True, ("warning", False)),
            (Outcome(done=1, halted=True), False, ("success", True)),       # Stop After Clip
            (Outcome(done=1, failed=1, halted=True), False, ("warning", True)),
        ]
        for o, stopped, want in cases:
            v = verdict("X", o, stopped)
            self.assertEqual((v.kind, v.handoff), want, (o, stopped))
        self.assertIn("1 done, 1 failed", verdict("X", Outcome(done=1, failed=1)).text)
        self.assertEqual(verdict("X", Outcome(done=1, halted=True)).title, "X stopped after the clip")


class SettingsTests(unittest.TestCase):
    def test_coerce_and_snapshot(self):
        s = S.snapshot({"inference_steps": "12.0", "seed": "-1", "output_crf_var": "18",
                        "stabilize_ref_mode_var": "manual", "stabilize_ref_index_var": "-4",
                        "process_as_segments_var": "true", "bogus": 1})
        self.assertEqual(s["inference_steps"], 12)
        self.assertEqual(s["seed"], -1)
        self.assertEqual(s["output_crf"], 18)
        self.assertEqual(s["stabilize_ref_index"], 0)
        self.assertTrue(s["process_as_segments_var"])
        self.assertNotIn("bogus", s)
        self.assertIsNone(S.snapshot({"output_crf_var": "auto"})["output_crf"])
        self.assertIsNone(S.snapshot({"output_crf_var": "abc"})["output_crf"])

    def test_bad_number_falls_back_to_default(self):
        self.assertEqual(S.coerce("window_size", "-"), S.DEFAULTS["window_size"])

    def test_v1_config_seeds_every_known_key(self):
        v1_path = REPO / "config_depthcrafter.json"
        if not v1_path.exists():
            self.skipTest("no v1 config on this machine")
        v1 = json.loads(v1_path.read_text())
        seeded = S.normalise(S.seed_from_v1(v1))
        for key in S.DEFAULTS:
            if key in v1:
                self.assertEqual(seeded[key], S.coerce(key, v1[key]), key)

    def test_restore_moves_files_back(self):
        with tempfile.TemporaryDirectory() as d:
            fin = Path(d) / "finished"
            fin.mkdir()
            (fin / "a.mp4").write_bytes(b"x")
            restored, errors, _ = R.restore_input_files(d, "finished")
            self.assertEqual((restored, errors), (1, 0))
            self.assertTrue((Path(d) / "a.mp4").exists())
            self.assertFalse(fin.exists())


class LoadFramesFailureTest(unittest.TestCase):
    """audit 2026-09-28 (minor): _load_frames' failure branches returned 2
    values where its caller unpacks 4 -- a ValueError in place of the job's
    own failure status."""

    def test_no_input_and_an_unknown_source_return_four_values(self):
        from depthcrafter.depthcrafter_logic import DepthCrafterDemo
        demo = DepthCrafterDemo.__new__(DepthCrafterDemo)
        for src, status in ((None, "failure_no_input_source"),
                            ({"source_type": "nonsense"}, "failure_unknown_source_type_in_dict")):
            meta = {}
            out = demo._load_frames(src, None, -1, 256, 256, None, meta)
            self.assertEqual(len(out), 4, f"{status}: {out}")
            self.assertIsNone(out[0])
            self.assertEqual(meta.get("status"), status)


class VramTests(RunnerTestBase):
    """DepthCrafter left the desktop no VRAM (user, 2026-10-01): its UNet's
    feed-forward runs in slices, and the run is capped short of the card,
    retrying a job once uncapped if the cap is what it ran out of."""

    def test_sliced_feed_forward_is_the_same_maths(self):
        import torch
        from diffusers.models.attention import FeedForward
        from depthcrafter.unet import chunk_feed_forward
        torch.manual_seed(0)
        model = torch.nn.Sequential(FeedForward(16, activation_fn="geglu"), FeedForward(16, activation_fn="geglu"))
        x = torch.randn(50, 3, 16)
        with torch.no_grad():
            plain = model(x)
            self.assertEqual(chunk_feed_forward(model, budget=96), 2)     # 2 rows per slice, 25 slices
            sliced = model(x)
            self.assertTrue(torch.allclose(plain, sliced, atol=1e-6), float((plain - sliced).abs().max()))
            self.assertEqual(model(x[:1]).shape, (1, 3, 16))                # under budget: one plain call

    def _runner(self):
        self.clip("a.mp4")
        return R.DepthCrafterRunner(self.opts(process_as_segments_var=False), RecordingHooks(),
                                    threading.Event(), move_originals=False)

    def test_the_run_is_capped_and_the_cap_lifted_after(self):
        calls = []
        saved = R.DepthCrafterRunner._cap_vram
        R.DepthCrafterRunner._cap_vram = classmethod(lambda cls, on: calls.append(on) or on)
        try:
            runner = self._runner()
            specs, _single = R.discover_sources(str(self.inp))
            runner.run_batch(specs, 1)
        finally:
            R.DepthCrafterRunner._cap_vram = saved
        self.assertEqual(calls, [True, False])

    def test_out_of_memory_under_the_cap_runs_once_more_uncapped(self):
        calls = []
        saved = R.DepthCrafterRunner._cap_vram
        R.DepthCrafterRunner._cap_vram = classmethod(lambda cls, on: calls.append(on) or on)
        OOM = type("OutOfMemoryError", (RuntimeError,), {})
        tries = []

        class Demo:
            def run(self, **kw):
                tries.append(kw)
                if len(tries) == 1:
                    raise OOM("CUDA out of memory")
                return "out.mp4", {"status": "success"}
        try:
            runner = self._runner()
            runner._capped = True
            self.assertEqual(runner._run_demo(Demo(), x=1), ("out.mp4", {"status": "success"}))
            self.assertEqual((len(tries), calls, runner._capped), (2, [False], False))
            # uncapped already: an out-of-memory is the job's own failure, not retried
            tries.clear()
            with self.assertRaises(OOM):
                runner._run_demo(Demo(), x=1)
            self.assertEqual(len(tries), 1)
        finally:
            R.DepthCrafterRunner._cap_vram = saved


class ResolutionPresetTests(unittest.TestCase):
    """Resolution presets: none built in (user, 2026-10-11 -- the built-in
    "16:9 · 1.85:1" column doubled the sizes ticked in Settings); each machine
    makes its own in Settings > DepthCrafter, in multiples of 64."""

    def test_nothing_is_built_in(self):
        self.assertEqual(S.RESOLUTION_PRESETS, [])
        self.assertEqual(S.BUILTIN_COVERS, {})

    def test_auto_size_defaults_are_multiples_of_64_nearest_the_ratio(self):
        self.assertEqual([S.default_auto_size(r) for r in ("2.39:1", "16:9", "4:3")], [(768, 320), (768, 448), (768, 576)])
        for w in (768, 960, 1152):
            h = S.preset_height(w, 16 / 9)
            self.assertEqual(h % 64, 0)
            for other in (h - 64, h + 64):
                self.assertLessEqual(abs(w / h - 16 / 9), abs(w / other - 16 / 9))

    def test_custom_sizes_are_multiples_of_64_near_the_ratio(self):
        # user, 2026-10-05: each width in steps of 64, the heights just under and
        # just over the true one, up to ~1080p, with how far each is from the ratio
        sizes = S.sizes_for(4 / 3)
        self.assertTrue(all(w % 64 == 0 and h % 64 == 0 and w <= 1920 and h <= 1088 for w, h, _d in sizes))
        self.assertIn((640, 448, "7.1% wider"), sizes)
        self.assertIn((640, 512, "6.2% taller"), sizes)
        self.assertIn((768, 576, "exact"), sizes)
        self.assertTrue(all(abs((w / h) / (4 / 3) - 1) <= S.CUSTOM_MAX_DISPARITY for w, h, _d in sizes))
        self.assertEqual(S.ratio_value("2.39:1"), 2.39)
        self.assertIsNone(S.ratio_value("Mine"))

    def test_saving_custom_presets_keeps_other_columns(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, S.LOCAL_PRESETS_FILE), "w", encoding="utf-8") as f:
                f.write('{"presets": [{"category": "Mine", "sizes": [[800, 448]]}, {"category": "2.2:1", "sizes": [[960, 448]]}]}')
            S.save_local_presets(d, {"4:3": [(768, 576), (640, 448)], "2.2:1": []})
            self.assertEqual(S.local_presets(d), [("4:3", [(640, 448, None), (768, 576, None)]),
                                                  ("Mine", [(800, 448, None)])])   # 2.2:1 unticked: gone; Mine kept

    def test_local_presets_are_this_machines_own(self):
        # config/depthcrafter_presets.json (git-ignored) adds columns;
        # none or a broken one adds nothing (user, 2026-10-01)
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(S.local_presets(d), [])
            path = os.path.join(d, S.LOCAL_PRESETS_FILE)
            with open(path, "w", encoding="utf-8") as f:
                f.write('{"presets": [{"category": "2.2:1", "sizes": [[960, 448]]}]}')
            self.assertEqual(S.local_presets(d), [("2.2:1", [(960, 448, None)])])
            with open(path, "w", encoding="utf-8") as f:
                f.write("{not json")
            with self.assertLogs("studio.settings_store", "WARNING"):
                self.assertEqual(S.local_presets(d), [])


class StreamInfoReturnTest(unittest.TestCase):
    """audit 2026-09-28 (tidy-up): get_video_stream_info's last return read
    `return info, raw if info else (None, raw)` -- with nothing usable found
    it gave ({}, (None, raw)), a tuple where the caller expects the text."""

    def test_nothing_usable_is_none_and_the_text(self):
        import json as _json
        from unittest import mock
        from depthcrafter import utils as U
        raw = _json.dumps({"streams": [{"codec_name": "N/A"}]})
        done = mock.Mock(stdout=raw, stderr="", returncode=0)
        with mock.patch.object(U.subprocess, "run", return_value=done):
            self.assertEqual(U.get_video_stream_info("x.mp4"), (None, raw))

    def test_a_usable_stream_is_the_info_and_the_text(self):
        import json as _json
        from unittest import mock
        from depthcrafter import utils as U
        raw = _json.dumps({"streams": [{"codec_name": "h264", "width": 64, "height": 32, "nb_frames": "10",
                                        "r_frame_rate": "24/1"}]})
        done = mock.Mock(stdout=raw, stderr="", returncode=0)
        with mock.patch.object(U.subprocess, "run", return_value=done):
            info, text = U.get_video_stream_info("x.mp4")
        self.assertEqual((info["codec_name"], text), ("h264", raw))



class HeadroomTests(unittest.TestCase):
    """DepthCrafter's VRAM cap is this PC's choice (user, 2026-10-02): Studio
    ships with none; config/local.json's depthcrafter_vram_headroom_gb opts in.
    The settings folder redirected to a temp one."""

    def setUp(self):
        import tempfile
        import studio.settings_store as ss
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        saved = ss.CONFIG_DIR
        ss.CONFIG_DIR = Path(tmp.name)
        self.addCleanup(lambda: setattr(ss, "CONFIG_DIR", saved))
        self.dir = Path(tmp.name)

    def local(self, value):
        import json
        (self.dir / "local.json").write_text(json.dumps({R.DepthCrafterRunner.LOCAL_KEY: value}))

    def test_no_cap_unless_opted_in(self):
        self.assertEqual(R.DepthCrafterRunner.vram_headroom_gb(), 0.0)
        self.assertFalse(R.DepthCrafterRunner._cap_vram(True))      # nothing set, whatever the card

    def test_this_pcs_headroom(self):
        self.local(3)
        self.assertEqual(R.DepthCrafterRunner.vram_headroom_gb(), 3.0)

    def test_a_bad_value_is_no_cap(self):
        self.local("lots")
        self.assertEqual(R.DepthCrafterRunner.vram_headroom_gb(), 0.0)
        self.local(-2)
        self.assertEqual(R.DepthCrafterRunner.vram_headroom_gb(), 0.0)


@unittest.skipUnless(__import__("torch").cuda.is_available(), "needs CUDA")
class SageAttentionFallbackTest(unittest.TestCase):
    """2026-10-13: a SageAttention that raises (its Triton kernel on an RTX 20, a GPU newer than the
    package) must not end the job: SDPA from then on, the same answer."""

    def test_a_failing_sage_falls_back_to_sdpa(self):
        import torch
        import torch.nn.functional as F
        from depthcrafter import attention as A
        calls = []

        def broken(*a, **k):
            calls.append(1)
            raise ValueError("Unsupported CUDA architecture: sm999")

        q, k, v = (torch.randn(1, 2, 600, 64, device="cuda", dtype=torch.float16) for _ in range(3))
        saved = A.sageattn
        A.sageattn = broken
        try:
            out = A._attention(q, k, v, None)
            A._attention(q, k, v, None)
        finally:
            A.sageattn = saved
        torch.testing.assert_close(out, F.scaled_dot_product_attention(q, k, v))
        self.assertEqual(len(calls), 1, "Sage retried after it failed")

    def test_running_out_of_memory_once_keeps_sage(self):
        """Bug sweep 2026-10-14: one out-of-memory turned Sage off until Studio restarted. That call
        goes to SDPA; the next one tries Sage again."""
        import torch
        import torch.nn.functional as F
        from depthcrafter import attention as A
        calls = []

        def short_of_memory(*a, **k):
            calls.append(1)
            raise torch.OutOfMemoryError("CUDA out of memory (stand-in)")

        q, k, v = (torch.randn(1, 2, 600, 64, device="cuda", dtype=torch.float16) for _ in range(3))
        saved = A.sageattn
        A.sageattn = short_of_memory
        A._oom_said = False
        try:
            with self.assertLogs(A._logger, "WARNING") as logs:
                out = A._attention(q, k, v, None)
                A._attention(q, k, v, None)
                A._attention(q, k, v, None)
            still = A.sageattn is short_of_memory
        finally:
            A.sageattn = saved
        # said once, not per layer per step (bug sweep 2026-10-14b)
        self.assertEqual(len([m for m in logs.output if "out of memory" in m]), 1, logs.output)
        torch.testing.assert_close(out, F.scaled_dot_product_attention(q, k, v))
        self.assertTrue(still, "Sage was turned off by an out-of-memory")
        self.assertEqual(len(calls), 3, "Sage tried again on the next calls")


if __name__ == "__main__":
    unittest.main()
