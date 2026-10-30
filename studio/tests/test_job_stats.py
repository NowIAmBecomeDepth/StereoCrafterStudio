"""JobStats (audit 2026-09-28, minor): the ring read 100% at the start of
every Inpainting clip -- chunk 0 of N ("none started") counted as landed --
and the stats kept reading other stages' progress bars after a job ended."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from PySide6.QtCore import QCoreApplication  # noqa: E402

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from studio.job_stats import JobStats  # noqa: E402

from studio.tests._qt import app as _qt_app
_app = _qt_app()   # a GUI app: the whole suite shares it (tests/_qt.py)


class RingTests(unittest.TestCase):
    def test_a_new_clip_starts_the_ring_at_zero(self):
        s = JobStats()
        s.begin("inpainting")
        s._on_chunk("inpainting", 5, 5, True)          # the last clip's last chunk landed
        self.assertEqual(s.property("part"), 1.0)
        s._on_chunk("inpainting", 0, 4, False)         # the next clip: nothing run yet
        self.assertEqual(s.property("part"), 0.0, "the ring read full before the clip's first step")
        self.assertEqual(s.property("fraction"), 0.0)

    def test_a_landed_chunk_is_still_full(self):
        s = JobStats()
        s.begin("inpainting")
        s._on_chunk("inpainting", 2, 4, True)
        self.assertEqual(s.property("part"), 1.0)

    def test_a_second_one_window_clip_starts_again(self):
        # user, 2026-10-05: clip 40 after clip 39, both "Window 1/1" -- the
        # bar and ring sat at 100% while clip 40 ran
        s = JobStats()
        s.begin("depthcrafter")
        s._on_bar("Window 1/1: 100%|####| 15/15 [00:43<00:00,  2.88s/it]")
        self.assertEqual(s.property("fraction"), 1.0)
        s._on_bar("Window 1/1:   0%|    | 0/15 [00:00<?, ?it/s]")
        self.assertEqual(s.property("fraction"), 0.0, "the next clip's window read as the last one's")
        s._on_bar("Window 1/1:  73%|### | 11/15 [00:53<00:20,  5.04s/it]")
        self.assertAlmostEqual(s.property("fraction"), 11 / 15)
        self.assertAlmostEqual(s.property("part"), 11 / 15)
        s._on_bar("Window 1/1:  73%|### | 11/15 [00:53<00:20,  5.04s/it]")    # a redraw changes nothing
        self.assertAlmostEqual(s.property("fraction"), 11 / 15)

    def test_the_next_clip_loading_is_not_the_last_ones_full_bar(self):
        """user, 2026-10-15: the clip list's bar for the next clip read 100% -- the clip before's --
        while it loaded, until its first inference step."""
        from types import SimpleNamespace
        from studio.stages.inpainting.controller import InpaintingController
        s = JobStats()
        s.begin("inpainting")
        s._on_chunk("inpainting", 4, 4, True)                    # the clip before: done
        self.assertEqual(s.property("fraction"), 1.0)
        ctl = SimpleNamespace(_clip=["a.mp4"] + ["N/A"] * 4, job_stats=s, infoChanged=SimpleNamespace(emit=lambda: None))
        InpaintingController._on_clip(ctl, ["a.mp4", "1", "2", "3", "4"])     # the same clip's details
        self.assertEqual(s.property("fraction"), 1.0)
        InpaintingController._on_clip(ctl, ["b.mp4", "N/A", "N/A", "N/A", "N/A"])   # the next clip, loading
        self.assertEqual(s.property("fraction"), 0.0)

    def test_depthcrafters_next_clip_too(self):
        s = JobStats()
        s.begin("depthcrafter")
        s._on_bar("Window 3/3: 100%|####| 15/15 [00:43<00:00,  2.88s/it]")
        self.assertEqual(s.property("fraction"), 1.0)
        s._on_dc("clip", None)
        self.assertEqual(s.property("fraction"), 0.0)


class EtaTests(unittest.TestCase):
    """Inpainting's ETA (user, 2026-10-09): the running chunk's steps left x
    their mean s/it, the previous chunk's start-to-start time for the chunks
    after it, and the previous clip's render + encode per frame."""

    def setUp(self):
        self.now = [0.0]
        self.s = JobStats()
        self.s._clock = lambda: self.now[0]
        self.s.begin("inpainting")

    def clip(self, frames, chunks, at):
        self.now[0] = at
        s = self.s
        s._on_frames(frames, 0, -1, -1)
        s._on_phase("inpaint", 0, 0)
        s._on_per_chunk(4, 2)                   # 2 tiles x 2 steps
        s._on_chunk("inpainting", 0, chunks, False)

    def steps(self, rates):
        for r in rates:                         # tiles of two steps: 1/2, 2/2, 1/2, ...
            self.k = getattr(self, "k", 0) + 1
            self.s._on_bar(f"{(self.k - 1) % 2 + 1}/2 [00:01<00:01, {r:.2f}s/it]")

    def eta(self):
        return self.s.property("eta"), self.s.property("etaPartial")

    def test_two_clips(self):
        s = self.s
        self.clip(50, 3, at=0)
        self.assertEqual(self.eta(), (-1.0, True), "no step yet: unknown")
        s._on_chunk("inpainting", 1, 3, False)
        self.steps([2.0])
        self.assertEqual(self.eta(), (-1.0, True), "a pass's first step carries its setup: still N/A")
        self.steps([2.0])
        self.assertAlmostEqual(self.eta()[0], 2 * 2 + 2 * 4 * 2)      # untimed: chunks from steps alone
        self.steps([2.0, 2.0])
        s._on_chunk("inpainting", 1, 3, True)
        self.now[0] = 10                                              # chunk 2 starts 10 s after chunk 1
        s._on_chunk("inpainting", 2, 3, False)
        self.assertAlmostEqual(self.eta()[0], (4 * 2 + 2) + 1 * 10, msg="steps + overhead (10 - 8) + a 10 s chunk")
        self.steps([9.0, 2.0])                                        # the first (9 s) is no sample
        self.assertAlmostEqual(self.eta()[0], 2 * 2 + 2 + 10)
        self.steps([2.0, 2.0])
        self.now[0] = 20
        s._on_chunk("inpainting", 3, 3, False)                        # the last: its decode is in the tail
        self.steps([2.0])
        self.assertAlmostEqual(self.eta()[0], 3 * 2)
        self.assertTrue(self.eta()[1], "first clip: no render time known yet")
        self.steps([2.0, 2.0, 2.0])
        self.now[0] = 28
        s._on_chunk("inpainting", 3, 3, True)
        s._on_phase("render", 0, 50)
        self.assertEqual(self.eta(), (-1.0, True))

        self.clip(100, 2, at=48)
        s._on_tail(0.44)                        # InpaintingChunks: this size's blend + encode, a frame
        self.now[0] = 50
        s._on_chunk("inpainting", 1, 2, False)
        self.steps([2.0])
        tail = 0.44 * 100
        self.assertAlmostEqual(self.eta()[0], 3 * 2 + 2 + 1 * 10 + tail)
        self.assertFalse(self.eta()[1])
        self.steps([2.0, 2.0, 2.0])
        self.now[0] = 60
        s._on_chunk("inpainting", 2, 2, False)
        self.steps([2.0, 2.0, 2.0, 2.0])
        self.now[0] = 70
        s._on_chunk("inpainting", 2, 2, True)
        self.now[0] = 80
        s._on_phase("render", 10, 100)
        self.assertAlmostEqual(self.eta()[0], tail - 10, msg="the render counts down from the last chunk")

    def test_cached_chunks_are_not_timed(self):
        s = self.s
        self.clip(50, 3, at=0)
        s._on_chunk("inpainting", 1, 3, False)                        # resumed: landed at once
        s._on_chunk("inpainting", 1, 3, True)
        self.now[0] = 1
        s._on_chunk("inpainting", 2, 3, False)
        self.steps([2.0, 2.0])
        self.assertAlmostEqual(self.eta()[0], 2 * 2 + 1 * 4 * 2, msg="a 1 s cached chunk is not a chunk time")

    def test_a_loading_bar_is_not_a_step(self):
        # user, 2026-10-09: "ETA < 1 min" and 130 it/s while the model loaded
        s = self.s
        s._on_bar("Loading pipeline components...: 100%|#####| 5/5 [00:00<00:00, 130.66it/s]")
        self.assertEqual((s.property("rate"), s.property("step")), (0.0, 0), "read before the clip")
        self.clip(50, 3, at=0)
        s._on_chunk("inpainting", 1, 3, False)
        s._on_bar("Loading checkpoint shards: 100%|#####| 5/5 [00:00<00:00, 130.66it/s]")
        self.assertEqual(self.eta(), (-1.0, True))
        self.assertEqual(s.property("rate"), 0.0, "a loading bar's rate shown as the steps'")

    def test_a_warm_up_chunk_is_not_a_chunk_time(self):
        # Optimise Model: the first step compiles for minutes, and so does the first decode
        s = self.s
        self.clip(50, 3, at=0)
        s._on_chunk("inpainting", 1, 3, False)
        self.steps([300.0, 2.0, 2.0, 2.0])
        s._on_chunk("inpainting", 1, 3, True)
        self.now[0] = 900
        s._on_chunk("inpainting", 2, 3, False)
        self.assertAlmostEqual(self.eta()[0], 4 * 2 + 1 * 4 * 2, msg="the 900 s warm-up chunk counted")

    def test_a_known_tail_counts_from_the_first_clip(self):
        s = self.s
        self.clip(50, 2, at=0)
        s._on_tail(2.0)
        s._on_chunk("inpainting", 1, 2, False)
        self.steps([2.0, 2.0])
        self.assertAlmostEqual(self.eta()[0], 2 * 2 + 1 * 4 * 2 + 2.0 * 50)
        self.assertFalse(self.eta()[1], "a known size's render left out")


class PushedStepTests(unittest.TestCase):
    """step_event (2026-10-24): an engine reporting its steps itself -- Inpainting's M2S engine, whose
    tiles are its steps -- gives the same numbers a printed bar does, and once it has, its other
    printed bars (its chunks, its decode) are no steps even when their counts match."""

    def setUp(self):
        self.now = [0.0]
        self.s = JobStats()
        self.s._clock = lambda: self.now[0]
        self.s.begin("inpainting")
        self.s._on_frames(30, 0, -1, -1)
        self.s._on_per_chunk(6, 6)                  # 6 tiles a chunk, one step each
        self.s._on_tail(1e-6)
        self.s._on_chunk("inpainting", 1, 2, False)

    def test_a_pushed_step_sets_the_step_the_rate_and_the_eta(self):
        s = self.s
        s._on_step("inpainting", 0, 6, 0.0)
        self.assertEqual((s.property("step"), s.property("steps")), (0, 6))
        for n in range(1, 4):
            s._on_step("inpainting", n, 6, 2.0)
        self.assertEqual((s.property("step"), s.property("steps"), s.property("rate")), (3, 6, 2.0))
        self.assertAlmostEqual(s.property("part"), 0.5)
        # 3 tiles left x 2 s, then chunk 2's 6 tiles x 2 s (untimed: from its steps)
        self.assertAlmostEqual(s.property("eta"), 3 * 2.0 + 6 * 2.0, places=3)
        self.assertFalse(s.property("etaPartial"))

    def test_printed_bars_are_no_steps_once_steps_are_pushed(self):
        s = self.s
        s._on_step("inpainting", 0, 6, 0.0)
        s._on_step("inpainting", 1, 6, 2.0)
        s._on_bar("Temporal Chunks:  50%|#####| 3/6 [00:39<00:39, 39.90s/it]")    # a count that matches
        self.assertEqual((s.property("step"), s.property("rate")), (1, 2.0))

    def test_another_stages_steps_are_not_taken(self):
        self.s._on_step("depthcrafter", 2, 6, 9.0)
        self.assertEqual((self.s.property("step"), self.s.property("rate")), (0, 0.0))


def _fake_v2_engine(seen, stats):
    """Inpainting V2's engine as InpaintingV2Chunks sees it: run_inpaint's chunk loop (its progress_cb
    per chunk, its module's STEP_HOOK per denoise step) and plan_next_chunk (fixed chunks, overlapping)."""
    import types
    mod = types.SimpleNamespace(STEP_HOOK=None)

    def plan_next_chunk(done, total, chunk, overlap, vsft):
        start = max(0, done - overlap) if done else 0
        return start, min(chunk, total - start)

    def run_inpaint(engine, video, save_dir, frames_chunk=20, frames_overlap=5, tile_num=1,
                    inference_steps=3, progress_cb=None, **_):
        total, done = 50, 0
        while done < total:
            start, length = plan_next_chunk(done, total, frames_chunk, frames_overlap, 4)
            progress_cb("inpaint", done, total, "")
            for n in range(1, inference_steps + 1):
                mod.STEP_HOOK(n, inference_steps)
                if n == 2:
                    s = stats
                    seen.append((s.property("chunk"), s.property("chunks"), s.property("framesDone"),
                                 (s.property("rangeFrom"), s.property("rangeTo")), s.property("step")))
            done = start + length
        progress_cb("export", 0, 1, "")
        progress_cb("done", total, total, "")
        return {}

    mod.plan_next_chunk, mod.run_inpaint = plan_next_chunk, run_inpaint
    return mod


class InpaintingV2ChunksTests(unittest.TestCase):
    """V1's numbers on V2's page (user, 2026-10-24): V2's chunks, frames and steps into JobStats, and
    each Inpainting page's job its own (begin's owner)."""

    def test_v2s_chunks_frames_and_steps(self):
        from studio.job_stats import InpaintingV2Chunks
        s, seen, told = JobStats(), [], []
        s.begin("inpainting", "v2")
        mod = _fake_v2_engine(seen, s)
        original = mod.run_inpaint
        chunks = InpaintingV2Chunks(mod, s)
        try:
            mod.run_inpaint(object(), "D:/in/x_splatted.mp4", "D:/out", frames_chunk=20, frames_overlap=5,
                            tile_num=2, inference_steps=3, progress_cb=lambda *a: told.append(a[0]))
        finally:
            chunks.restore()
        self.assertEqual(s.property("owner"), "v2")
        # 50 frames, 20 a chunk, 5 overlap: 0-20, 15-35, 30-50; seen at each chunk's step 2
        self.assertEqual(seen, [(1, 3, 0, (0, 20), 2), (2, 3, 20, (15, 35), 2), (3, 3, 35, (30, 50), 2)])
        self.assertEqual((s.property("done"), s.property("framesDone"), s.property("frames")), (3, 50, 50))
        self.assertIn("inpaint", told, "the page's own progress still told")
        self.assertIs(mod.run_inpaint, original, "restored")
        self.assertIsNone(mod.STEP_HOOK, "restored: no hook after the run")

    def test_each_page_has_its_own_job(self):
        s = JobStats()
        s.begin("inpainting", "v1")
        self.assertEqual((s.property("stage"), s.property("owner")), ("inpainting", "v1"))
        s.begin("inpainting")
        self.assertEqual(s.property("owner"), "", "no owner: the stage's")


class TailTimingTests(unittest.TestCase):
    """InpaintingChunks times the hi-res blend + encode per frame and keeps it
    per resolution (config/inpainting_eta.json -- a temp config here)."""

    def test_timed_kept_and_used_for_the_next_clip(self):
        import json
        import logging
        import time as _t
        import types
        from unittest import mock
        from studio import job_stats as J
        from studio import settings_store as ss

        log = logging.getLogger("test.inpaint.tail")
        log.setLevel(logging.INFO)
        clock = [100.0]
        rates = []
        fake = types.SimpleNamespace(logger=log)
        fake.spatial_tiled_process = lambda *a, **k: "latents"
        fake.encode_frames_to_mp4 = lambda *a, **k: True

        class FakeEngine:
            def process_single_video(self, **kw):
                log.info("Hi-Res blending enabled. Target resolution: 1920x1038")
                fake.spatial_tiled_process()            # one 25-frame chunk
                clock[0] += 30                          # the blend + encode: 30 s
                fake.encode_frames_to_mp4()
                return True, None

        fake.InpaintingEngine = FakeEngine
        stats = JobStats()
        stats.tail_rate = rates.append
        path = ss.CONFIG_DIR / "inpainting_eta.json"
        path.unlink(missing_ok=True)
        with mock.patch.object(J.InpaintingChunks, "_count_frames", staticmethod(lambda p, l: 25)), \
                mock.patch.object(J.time, "monotonic", lambda: clock[0]):
            hook = J.InpaintingChunks(fake, stats)
            try:
                FakeEngine().process_single_video(input_video_path="x.mp4", frames_chunk=25, overlap=3)
                self.assertEqual(rates[:2], [0.0, 0.0], "nothing known at first: lowres, then 1920x1038")
                self.assertAlmostEqual(rates[-1], 30 / 25)
                kept = json.loads(path.read_text(encoding="utf-8"))["tail_seconds_per_frame"]
                self.assertAlmostEqual(kept["1920x1038"], 1.2)
                clock[0] += 5
                FakeEngine().process_single_video(input_video_path="y.mp4", frames_chunk=25, overlap=3)
                self.assertAlmostEqual(rates[-2], 1.2, msg="the next clip of that size starts with it")
            finally:
                hook.restore()
        hook2 = J.InpaintingChunks(fake, JobStats())
        self.assertAlmostEqual(hook2._tail_for("1920x1038"), 1.2, msg="kept for the next run")
        hook2.restore()

    def test_a_streamed_hires_encode_ends_the_tail_too(self):
        """Since 2026-10-29 the Hi-Res frames are encoded as they're made: the clip's encode ends in
        finish_streamed_encode, not encode_frames_to_mp4 -- timed the same."""
        import logging
        import types
        from unittest import mock
        from studio import job_stats as J

        log = logging.getLogger("test.inpaint.tail.streamed")
        log.setLevel(logging.INFO)
        clock = [100.0]
        rates = []
        fake = types.SimpleNamespace(logger=log)
        fake.spatial_tiled_process = lambda *a, **k: "latents"
        fake.encode_frames_to_mp4 = lambda *a, **k: True
        finish = lambda *a, **k: True          # noqa: E731
        fake.finish_streamed_encode = finish

        class FakeEngine:
            def process_single_video(self, **kw):
                log.info("Hi-Res blending enabled. Target resolution: 2560x1080")
                fake.spatial_tiled_process()
                clock[0] += 50
                fake.finish_streamed_encode()
                return True, None

        fake.InpaintingEngine = FakeEngine
        stats = JobStats()
        stats.tail_rate = rates.append
        with mock.patch.object(J.InpaintingChunks, "_count_frames", staticmethod(lambda p, l: 25)), \
                mock.patch.object(J.time, "monotonic", lambda: clock[0]):
            hook = J.InpaintingChunks(fake, stats)
            try:
                FakeEngine().process_single_video(input_video_path="x.mp4", frames_chunk=25, overlap=3)
            finally:
                hook.restore()
        self.assertTrue(rates)
        self.assertAlmostEqual(rates[-1], 50 / 25, msg="the streamed encode didn't end the clip's tail")
        self.assertIs(fake.finish_streamed_encode, finish, "not put back")

    def test_an_uncounted_clip_never_runs_backwards(self):
        # bug sweep 2026-10-09 (A2): cv2 (and ffprobe) can't count the frames: the
        # total grew one chunk at a time, so the bar read 100% after each chunk
        # and dropped when the next began. Now: progress unknown, no fraction.
        import logging
        import types
        from unittest import mock
        from studio import job_stats as J

        fake = types.SimpleNamespace(logger=logging.getLogger("test.inpaint.uncounted"))
        fake.spatial_tiled_process = lambda *a, **k: "latents"

        class FakeEngine:
            def process_single_video(self, **kw):
                for _ in range(3):
                    fake.spatial_tiled_process()
                return True, None

        fake.InpaintingEngine = FakeEngine
        stats = JobStats()
        stats.begin("inpainting")
        seen = []
        stats.changed.connect(lambda: seen.append((stats.property("fraction"), stats.property("chunks"),
                                                   stats.property("frames"))))
        with mock.patch.object(J.InpaintingChunks, "_count_frames", staticmethod(lambda p, l: 0)):
            hook = J.InpaintingChunks(fake, stats)
            try:
                FakeEngine().process_single_video(input_video_path="x.mkv", frames_chunk=25, overlap=5)
            finally:
                hook.restore()
        self.assertTrue(seen)
        self.assertEqual({(f, c, n) for f, c, n in seen}, {(0.0, 0, 0)}, "a fraction shown for an uncounted clip")

    def test_ffprobe_counts_what_cv2_cannot(self):
        from unittest import mock
        from studio import job_stats as J
        from studio.tests.checks import _fixtures
        clip = _fixtures.path("small") / "src" / "clipA.mp4"           # 48 frames
        with mock.patch("cv2.VideoCapture", side_effect=RuntimeError("no count")):
            self.assertEqual(J.InpaintingChunks._count_frames(str(clip), -1), 48)
        self.assertEqual(J.InpaintingChunks._probe_frames(str(clip.with_name("missing.mp4"))), 0)

    def test_depthcrafter_has_none(self):
        s = JobStats()
        s.begin("depthcrafter")
        s._on_bar("Window 1/3:  40%|##  | 2/5 [00:05<00:07,  2.50s/it]")
        self.assertEqual(s.property("eta"), -1.0)


class EndTests(unittest.TestCase):
    def _pump(self):
        for _ in range(5):
            _app.processEvents()

    def test_bars_after_the_job_ended_are_not_read(self):
        s = JobStats()
        s.begin("depthcrafter")
        s.feed_bar("Window 1/3  4/10 [00:01<00:01,  2.00it/s]")
        self._pump()
        self.assertEqual(s.property("step"), 4)
        s.end("depthcrafter")
        s.feed_bar("Splatting  7/48 [00:03<00:10,  5.00it/s]")      # another stage's bar, later
        self._pump()
        self.assertEqual(s.property("step"), 4, "a later bar of another stage was read as this job's")
        self.assertEqual(s.property("rate"), 0.5)

    def test_a_merge_bar_after_merging_ended_is_not_read(self):
        s = JobStats()
        s.begin("merging")
        s.end("merging")
        s.feed_bar("Encoding clip: |####      | 40.0% clip.mp4")
        self._pump()
        self.assertEqual(s.property("mergePercent"), 0.0)

    def test_ending_another_stage_leaves_this_one(self):
        s = JobStats()
        s.begin("inpainting")
        s.end("depthcrafter")
        self.assertEqual(s.property("stage"), "inpainting")


class DcEtaTests(unittest.TestCase):
    """DepthCrafter's ETA (user, 2026-10-12): head + r x frames per window still to
    run + tail, r live from the bar, head / tail / r kept per size. The user's
    A test clip: 257 frames, window 120 / overlap 100, 10 steps, 768x448 --
    8 windows at ~6.17 s/it, 583 s in all (head 22.7 s, tail ~52 s)."""

    RATES = {"head": {"768x448": 0.088}, "tail": {"768x448": 0.203}, "step": {"768x448": 0.0514}}

    def setUp(self):
        from studio import settings_store as ss
        self.ss = ss
        self.ss.save("depthcrafter_eta", {})
        self.now = [0.0]
        self.s = JobStats()
        self.s._clock = lambda: self.now[0]

    def window(self, a, b, rates, steps=10):
        for n, r in enumerate(rates, 1):
            self.s._on_bar(f"Window {a}/{b}:  {10 * n}%|#  | {n}/{steps} [00:10<00:30, {r:.2f}s/it]")

    def test_the_windows_as_the_pipeline_steps_them(self):
        from studio.job_stats import dc_windows
        self.assertEqual(dc_windows(257, 120, 100), [120] * 7 + [117])     # the user's clip: 8 windows
        self.assertEqual(dc_windows(160, 120, 100), [120] * 3)              # the speed study's clip
        self.assertEqual(dc_windows(100, 120, 100), [100])                  # shorter than a window
        self.assertEqual(dc_windows(300, 110, 25), [110, 110, 110, 45])
        self.assertEqual(dc_windows(0, 120, 100), [])

    def test_the_users_clip_from_known_rates(self):
        from studio.job_stats import DcEta
        e = DcEta(self.RATES)
        e.clip_started(0.0)
        e.plan(257, 10, 120, 100, "768x448")
        eta, partial = e.eta(0.0)
        self.assertFalse(partial)
        self.assertAlmostEqual(eta, 0.088 * 257 + 10 * 0.0514 * 957 + 0.203 * 257, places=3)
        self.assertLess(abs(eta - 583.2) / 583.2, 0.03)             # within 3% of what it took

    def test_live_through_a_clip(self):
        s = self.s
        s.begin("depthcrafter")
        s._dc.rates = {k: dict(v) for k, v in self.RATES.items()}
        s._on_dc("clip", None)
        s._on_dc("plan", {"frames": 257, "steps": 10, "window": 120, "overlap": 100, "key": "768x448"})
        self.now[0] = 22.0
        self.window(1, 8, [10.55] + [6.17] * 9)                       # window 1: its first step is the warm-up
        self.assertEqual(s._dc.samples[0], 6.17 / 120)                # ...and no sample
        self.now[0] = 86.0
        self.window(2, 8, [6.4] + [6.17] * 4)                         # window 2, 5 steps done
        r = 6.17 / 120
        overhead = 86.0 - 22.0 - 10 * r * 120                         # window 1's start-to-start, less its steps
        want = 5 * r * 120 + 10 * r * (5 * 120 + 117) + 6 * overhead + 0.203 * 257
        self.assertAlmostEqual(s.property("eta"), want, places=3)
        self.assertFalse(s.property("etaPartial"))
        for w in range(3, 9):                                         # the rest, then the tail
            self.now[0] += 62.0
            self.window(w, 8, [6.4] + [6.17] * 9)
        self.assertAlmostEqual(s.property("eta"), 0.203 * 257, places=3)
        self.now[0] += 20.0
        s._on_bar("Window 8/8: 100%|##| 10/10 [01:00<00:00,  6.07s/it]")   # a redraw: nothing new
        self.assertAlmostEqual(s.property("eta"), 0.203 * 257 - 20.0, places=3)

    def test_learned_and_kept_for_the_next_run(self):
        s = self.s
        s.begin("depthcrafter")                                      # nothing kept yet
        s._on_dc("clip", None)
        s._on_dc("plan", {"frames": 100, "steps": 2, "window": 120, "overlap": 100, "key": "640x384"})
        self.assertEqual(s.property("eta"), -1.0)                    # no rate at all yet: N/A
        self.now[0] = 10.0                                           # head: 10 s for 100 frames
        self.window(1, 1, [9.0, 5.0], steps=2)
        self.assertTrue(s.property("etaPartial"))                    # tail unknown: "+ encode/decode"
        self.now[0] = 40.0
        s._on_dc("done", None)                                       # tail: 30 s
        kept = self.ss.load("depthcrafter_eta")
        self.assertAlmostEqual(kept["head_seconds_per_frame"]["640x384"], 0.1, places=6)
        self.assertAlmostEqual(kept["tail_seconds_per_frame"]["640x384"], 0.3, places=6)
        self.assertAlmostEqual(kept["step_seconds_per_frame"]["640x384"], 0.05, places=6)
        t = JobStats()                                               # the next run, a size not timed:
        t._clock = lambda: 0.0
        t.begin("depthcrafter")
        t._on_dc("clip", None)
        t._on_dc("plan", {"frames": 100, "steps": 2, "window": 120, "overlap": 100, "key": "1280x768"})
        self.assertAlmostEqual(t.property("eta"), 4 * (0.1 * 100 + 2 * 0.05 * 100 + 0.3 * 100), places=3)
        self.assertFalse(t.property("etaPartial"))                   # scaled by pixels from 640x384

    def test_without_a_plan_there_is_none(self):
        s = JobStats()
        s.begin("depthcrafter")
        s._on_bar("Window 1/3:  40%|##  | 2/5 [00:05<00:07,  2.50s/it]")
        self.assertEqual(s.property("eta"), -1.0)


class DcSegmentEtaTests(unittest.TestCase):
    """Process as Segments (user, 2026-10-21): a clip runs as one job per segment, and its ETA
    ran out at the end of each one -- now the segments after the running one are in it."""

    RATES = DcEtaTests.RATES
    PER = 0.088 + 10 * 0.0514 + 0.203          # head + steps x r + tail, a frame at 768x448

    def setUp(self):
        from studio import settings_store as ss
        ss.save("depthcrafter_eta", {})
        self.now = [0.0]
        self.s = JobStats()
        self.s._clock = lambda: self.now[0]
        self.s.begin("depthcrafter")
        self.s._dc.rates = {k: dict(v) for k, v in self.RATES.items()}

    def segment(self, frames):
        self.s._on_dc("clip", None)
        self.s._on_dc("plan", {"frames": frames, "steps": 10, "window": frames, "overlap": 0, "key": "768x448"})

    def test_the_clips_later_segments_are_in_its_eta(self):
        s = self.s
        s._on_dc("segments", [100, 100, 57])
        self.segment(100)
        self.assertAlmostEqual(s.property("eta"), 100 * self.PER + 157 * self.PER, places=3)
        self.assertFalse(s.property("etaPartial"))
        self.now[0] = 60.0
        self.segment(100)                            # the second: only the last after it
        self.assertAlmostEqual(s.property("eta"), 100 * self.PER + 57 * self.PER, places=3)
        self.now[0] = 120.0
        self.segment(57)                             # the last: its own alone
        self.assertAlmostEqual(s.property("eta"), 57 * self.PER, places=3)

    def test_a_frame_rate_change_scales_the_later_ones(self):
        s = self.s
        s._on_dc("segments", [200, 200])             # source frames; the plan loads half (fps halved)
        self.segment(100)
        self.assertAlmostEqual(s.property("eta"), 100 * self.PER + 100 * self.PER, places=3)

    def test_a_whole_clip_after_a_segmented_one_has_none(self):
        s = self.s
        s._on_dc("segments", [100])
        self.segment(100)
        self.segment(80)                             # the next clip, not segmented: the queue is empty
        self.assertAlmostEqual(s.property("eta"), 80 * self.PER, places=3)

    def test_the_batch_counts_a_segment_by_its_own_frames(self):
        s = self.s
        s._on_batch("depthcrafter", [("A.mp4", 200), ("B.mp4", 100)])
        s._on_batch_clip("depthcrafter", "A")
        s._on_dc("segments", [100, 100])
        self.segment(100)
        eta = s.property("eta")                      # both of A's segments
        self.assertAlmostEqual(s.property("batchEta"), eta + eta / 200 * 100, places=3)
        self.now[0] = 50.0                           # segment 1 took 50 s: 50 s for its 100 frames
        self.segment(100)
        self.assertEqual((s._batch_spent, s._batch_spent_frames), (50.0, 100))
        eta = s.property("eta")
        self.assertAlmostEqual(s.property("batchEta"), eta + (50.0 + eta) / 200 * 100, places=3)


class BatchEtaTests(unittest.TestCase):
    """The batch's ETA (user, 2026-10-21): the clip's ETA + the frames of the clips not reached
    yet x seconds a frame -- the finished clips as they took, the running one as projected."""

    RATES = DcEtaTests.RATES

    def setUp(self):
        from studio import settings_store as ss
        ss.save("depthcrafter_eta", {})
        self.now = [0.0]
        self.s = JobStats()
        self.s._clock = lambda: self.now[0]

    def dc_clip(self, name, frames):
        s = self.s
        s._on_batch_clip("depthcrafter", name)
        s._on_dc("clip", None)
        s._on_dc("plan", {"frames": frames, "steps": 10, "window": 120, "overlap": 100, "key": "768x448"})

    def test_depthcrafter_three_clips(self):
        s = self.s
        s.begin("depthcrafter")
        s._dc.rates = {k: dict(v) for k, v in self.RATES.items()}
        s._on_batch("depthcrafter", [("D:/in/A.mp4", 257), ("D:/in/B.mp4", 100), ("D:/in/C.mov", 50)])
        self.assertEqual(s.property("batchClips"), 3)
        self.assertEqual(s.property("batchEta"), -1.0, "no clip running yet")
        self.dc_clip("A", 257)                                  # the runner names it without its extension
        self.assertEqual(s.property("batchClips"), 2)
        eta = s.property("eta")
        self.assertGreater(eta, 0)
        self.assertAlmostEqual(s.property("batchEta"), eta + eta / 257 * 150, places=3)
        self.now[0] = 100.0                                     # 100 s in: the clip's projection holds
        s._eta_update()
        eta = s.property("eta")
        self.assertAlmostEqual(s.property("batchEta"), eta + (100.0 + eta) / 257 * 150, places=3)
        self.now[0] = 600.0                                     # A took 600 s; B starts
        self.dc_clip("B", 100)
        eta_b = s.property("eta")
        self.assertAlmostEqual(s.property("batchEta"), eta_b + (600.0 + eta_b) / 357 * 50, places=3)
        self.now[0] = 900.0                                     # C: the last -- no batch line
        self.dc_clip("C", 50)
        self.assertEqual(s.property("batchClips"), 0)
        self.assertEqual(s.property("batchEta"), -1.0)

    def test_inpainting_names_with_extensions_and_an_unknown_clip_eta(self):
        s = self.s
        s.begin("inpainting")
        s._on_batch("inpainting", [("D:/in/x_splatted.mp4", 100), ("D:/in/y_splatted.mp4", 200)])
        s._on_frames(100, 0, -1, -1)
        s._on_chunk("inpainting", 0, 4, False)                  # x starts
        s._on_batch_clip("inpainting", "x_splatted.mp4")
        self.assertEqual(s.property("batchClips"), 1)
        self.assertEqual(s.property("batchEta"), -1.0, "the clip's ETA unknown: the batch's too")
        s._eta, s._eta_partial = 50.0, False                    # as _eta_update_clip would set it
        self.now[0] = 30.0
        s._batch_update()
        self.assertAlmostEqual(s.property("batchEta"), 50.0 + (30.0 + 50.0) / 100 * 200)
        self.now[0] = 90.0                                      # y: frames first, then its start
        s._on_frames(200, 0, -1, -1)
        s._on_chunk("inpainting", 0, 8, False)
        self.assertEqual((s._batch_spent, s._batch_spent_frames), (90.0, 100), "x timed with x's frames")

    def inpaint_clip(self, name, frames, chunks):
        s = self.s
        s._on_frames(frames, 0, -1, -1)
        s._on_phase("inpaint", 0, 0)
        s._on_per_chunk(4, 2)                                   # 2 tiles x 2 steps
        s._on_chunk("inpainting", 0, chunks, False)
        s._on_batch_clip("inpainting", name)

    def steps(self, rates):
        for i, r in enumerate(rates):
            self.s._on_bar(f"{i % 2 + 1}/2 [00:01<00:01, {r:.2f}s/it]")

    def test_a_compile_warm_up_is_no_frames_time(self):
        # user, 2026-10-24: the first clip's ~10 min compile read as every frame's -- the Batch ETA
        # promised an hour and a half too much, and only caught up over the batch
        s = self.s
        s.begin("inpainting")
        s._on_batch("inpainting", [("x.mp4", 100), ("y.mp4", 100)])
        self.inpaint_clip("x.mp4", 100, 3)
        s._on_chunk("inpainting", 1, 3, False)
        self.steps([300.0, 2.0, 2.0, 2.0])                      # the first step compiles
        s._on_chunk("inpainting", 1, 3, True)
        self.now[0] = 900.0                                     # the warm-up chunk: 900 s
        s._on_chunk("inpainting", 2, 3, False)
        self.steps([2.0, 2.0, 2.0, 2.0])
        self.now[0] = 910.0                                     # a normal chunk: 10 s
        s._on_chunk("inpainting", 3, 3, False)
        self.steps([2.0])
        eta = s.property("eta")
        warm = 900.0 - 10.0                                     # beyond a normal chunk
        self.assertAlmostEqual(s.property("batchEta"), eta + (910.0 - warm + eta) / 100 * 100, places=3)

    def test_a_one_chunk_warm_up_clip_is_left_out_too(self):
        s = self.s
        s.begin("inpainting")
        s._on_batch("inpainting", [("x.mp4", 100), ("y.mp4", 100), ("z.mp4", 100)])
        self.inpaint_clip("x.mp4", 100, 1)
        s._on_chunk("inpainting", 1, 1, False)
        self.steps([300.0, 2.0, 2.0, 2.0])
        s._on_chunk("inpainting", 1, 1, True)
        self.now[0] = 320.0                                     # y starts: x took 320 s, 298 of them compile
        self.inpaint_clip("y.mp4", 100, 2)
        s._on_chunk("inpainting", 1, 2, False)
        self.steps([2.0, 2.0])
        eta = s.property("eta")
        self.assertGreater(eta, 0)
        self.assertAlmostEqual(s.property("batchEta"), eta + (320.0 - 298.0 + eta) / 200 * 100, places=3)

    def test_a_new_job_forgets_the_batch(self):
        s = self.s
        s.begin("inpainting")
        s._on_batch("inpainting", [("a.mp4", 10)])
        s.begin("inpainting")
        self.assertEqual(s.property("batchClips"), 0)
        s._on_batch("depthcrafter", [("a.mp4", 10)])            # another stage's: not this job's
        self.assertEqual(s.property("batchClips"), 0)


class DepthCrafterTimelineTests(unittest.TestCase):
    """The run's clips reach JobStats from DepthCrafterDemo's own calls, put back after."""

    def make(self):
        import numpy as np

        class Demo:
            def _internal_infer(self, path, frames, steps):
                return self._perform_inference(frames, 1.0, steps, 120, 100, None, 448, 768)

            def _perform_inference(self, actual_frames_to_process, guidance_scale, num_denoising_steps,
                                   pipe_call_window_size, pipe_call_overlap, segment_job_info,
                                   actual_processed_height, actual_processed_width, **kw):
                return "depth"

        self.Demo = Demo
        self.events = []
        stats = type("S", (), {"dc_event": lambda _s, k, d=None: self.events.append((k, d))})()
        from types import SimpleNamespace
        from studio.job_stats import DepthCrafterTimeline
        self.originals = (Demo._internal_infer, Demo._perform_inference)
        return DepthCrafterTimeline(SimpleNamespace(DepthCrafterDemo=Demo), stats), np.zeros((257, 4, 4, 3))

    def test_a_clip_planned_and_restored(self):
        tl, frames = self.make()
        self.assertEqual(self.Demo()._internal_infer("clip.mp4", frames, 10), "depth")
        self.assertEqual(self.events, [("clip", None),
                                       ("plan", {"frames": 257, "steps": 10, "window": 120, "overlap": 100,
                                                 "key": "768x448"}),
                                       ("done", None)])
        tl.restore()
        self.assertEqual((self.Demo._internal_infer, self.Demo._perform_inference), self.originals)

    def test_a_segment_is_one_window(self):
        tl, frames = self.make()
        self.Demo()._perform_inference(frames[:60], 1.0, 5, 120, 100, {"segment_id": 0}, 448, 768)
        tl.restore()
        self.assertEqual(self.events[0][1]["window"], 60)
        self.assertEqual(self.events[0][1]["overlap"], 0)


if __name__ == "__main__":
    unittest.main()
