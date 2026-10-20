"""The Depth Map Editor (Extra tab): the generated engine is today's v1, and
its slider tables are v1's. The real path is checks/check_depthedit.py."""

from __future__ import annotations

import sys
import os
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
try:                    # the Prototype's generators don't ship (release, 2026-10-17)
    from studio.tools import gen_depthedit_engine as gen  # noqa: E402
except ImportError:
    gen = None


@unittest.skipUnless(studio.tests.PROTOTYPE, studio.tests.NO_PROTOTYPE)
class SyncTest(unittest.TestCase):
    def test_engine_matches_v1(self):
        """engine.py is exactly what the generator makes from today's v1.

        Fails when depth_editor_gui.py has changed since engine.py was
        generated: re-run studio.tools.gen_depthedit_engine."""
        fresh = gen.build().replace("\n", "\r\n").encode("utf-8")
        self.assertEqual(fresh, gen.TARGET.read_bytes(),
                         "engine.py is stale; re-run: python -m studio.tools.gen_depthedit_engine")

    def test_slider_tables_are_v1s(self):
        """The four tables the page draws from, lifted from v1's builders."""
        src = gen.build()
        ns: dict = {}
        start = src.index("# ---- the edit controls, lifted from v1's page builders ----")
        end = src.index("# DepthEditEngine -- v1 DepthEditorGUI without its widgets")
        helpers = "".join(f"def {n}(v): return v\n" for n in ("_pct", "_pct0", "_times", "_px", "_px_signed", "_frames"))
        exec(helpers + src[start:end].rsplit("# ====", 1)[0], ns)
        self.assertEqual([s[0] for s in ns["LAYER_SLIDERS"]],
                         [("brightness",), ("contrast",), ("flatten",), ("smooth",), ("opacity",), ("edge_top",),
                          ("edge_bottom",), ("edge_left",), ("edge_right",)])
        self.assertEqual([s[0] for s in ns["EDGE_SLIDERS"]],
                         [("edge_snap",), ("grow",), ("close_gaps",), ("feather",), ("steady",)])
        self.assertEqual([s[0] for s in ns["GRADIENT_SLIDERS"]],
                         [("gradient", "near"), ("gradient", "far"), ("gradient", "curve"), ("gradient", "keep_detail")])
        self.assertEqual([s[0] for s in ns["GLOBAL_SLIDERS"]],
                         ["in_black", "in_white", "gamma", "contrast", "brightness"])



class EffectiveMaskCacheTests(unittest.TestCase):
    """audit 2026-09-28 O6: every redraw worked out each layer's edited mask
    (edge snap -- a guided filter -- then fill / grow) twice or three times,
    ~48 ms a layer at 1080p: 625 ms a redraw with four layers. It is worked out
    once for a (mask, settings, depth frame) and reused; any change to those
    works it out again, and the result is the same array the uncached call
    gives."""

    def setUp(self):
        import numpy as np
        from core.depthedit import edit_ops as E
        self.E, self.np = E, np
        E.clear_mask_cache()
        self.calls = []
        real = E.snap_mask
        def counting(mask, guide, radius):
            self.calls.append(radius)
            return real(mask, guide, radius)
        E.snap_mask = counting
        self.addCleanup(setattr, E, "snap_mask", real)
        rng = np.random.default_rng(0)
        self.guide = rng.random((90, 160), dtype=np.float32)
        self.guide.flags.writeable = False           # as the editor's cached depth frames are
        self.mask = np.zeros((90, 160), bool); self.mask[20:60, 40:120] = True
        self.s = E.LayerSettings(edge_snap=4, grow=2)

    def test_worked_out_once_and_the_same(self):
        E = self.E
        first = E.effective_mask(self.mask, self.s, self.guide)
        again = E.effective_mask(self.mask.copy(), self.s, self.guide)   # a fresh unpack, same content
        self.assertEqual(len(self.calls), 1, "the snap was worked out twice for the same inputs")
        self.assertTrue(self.np.array_equal(first, again))
        E.clear_mask_cache()
        self.assertTrue(self.np.array_equal(first, E.effective_mask(self.mask, self.s, self.guide)))

    def test_a_change_works_it_out_again(self):
        E, np = self.E, self.np
        E.effective_mask(self.mask, self.s, self.guide)
        other = self.mask.copy(); other[10, 10] = True
        E.effective_mask(other, self.s, self.guide)                             # the mask
        E.effective_mask(self.mask, E.LayerSettings(edge_snap=4, grow=5), self.guide)  # a setting
        g2 = self.guide.copy(); g2.flags.writeable = False
        E.effective_mask(self.mask, self.s, g2)                                  # another depth frame
        self.assertEqual(len(self.calls), 4)

    def test_settings_that_do_not_shape_the_mask_do_not_miss(self):
        E = self.E
        E.effective_mask(self.mask, self.s, self.guide)
        E.effective_mask(self.mask, E.LayerSettings(edge_snap=4, grow=2, brightness=0.3, feather=9), self.guide)
        self.assertEqual(len(self.calls), 1, "brightness / feather made it work the mask out again")

    def test_the_cached_mask_cannot_be_changed_by_a_caller(self):
        m = self.E.effective_mask(self.mask, self.s, self.guide)
        with self.assertRaises(ValueError):
            m[0, 0] = 1



class UseInSplattingTests(unittest.TestCase):
    """audit 2026-09-28 (minor): Use in Splatting kept the first backup of a
    depth map for ever and the editor preferred it -- a map regenerated in
    DepthCrafter afterwards was never opened or backed up; and the live map
    was overwritten in place (a crash mid-copy left a broken map).
    user 2026-09-29: going back and forth nested edited/edited/originals/...
    without end. Now ONE edit_history folder beside the maps holds the
    original and every edit it replaced."""

    def setUp(self):
        import tempfile
        from studio.stages.depthedit import controller as C
        self.C = C
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.live = self.dir / "clip_depth.mp4"
        self.live.write_bytes(b"original map")
        self.hist = self.dir / "edit_history"

    def render(self, data):
        """What the editor opened from Splatting writes: its render, in the
        folder openFromSplatting gives it."""
        out = Path(self.C.history_dir(str(self.live)))
        out.mkdir(exist_ok=True)
        written = out / "clip_depth.mp4"
        written.write_bytes(data)
        return written

    def use(self, data=b"the edited map"):
        return self.C.use_edited_map(str(self.render(data)), str(self.live))

    def names(self):
        return sorted(p.relative_to(self.dir).as_posix() for p in self.dir.rglob("*")
                      if not p.name.endswith(".edit.json"))

    def original(self):
        return Path(self.C.DepthEditController.backup_path(str(self.live)))

    def test_first_use_puts_the_original_in_edit_history(self):
        self.use()
        self.assertEqual(self.live.read_bytes(), b"the edited map")
        self.assertEqual(self.original(), self.hist / "clip_depth.original.mp4")
        self.assertEqual(self.original().read_bytes(), b"original map")
        self.assertEqual(self.C.map_to_edit(str(self.live)), str(self.original()).replace("\\", "/"))
        self.assertEqual(self.names(), ["clip_depth.mp4", "edit_history", "edit_history/clip_depth.original.mp4"])

    def test_back_and_forth_keeps_one_folder_and_numbers_the_old_edits(self):
        for i in range(1, 5):
            self.use(f"edit {i}".encode())
            self.assertEqual(self.C.map_to_edit(str(self.live)), str(self.original()).replace("\\", "/"))
        self.assertEqual(self.live.read_bytes(), b"edit 4")
        self.assertEqual(self.original().read_bytes(), b"original map")
        self.assertEqual(self.names(), ["clip_depth.mp4", "edit_history",
                                        "edit_history/clip_depth.edit-01.mp4", "edit_history/clip_depth.edit-02.mp4",
                                        "edit_history/clip_depth.edit-03.mp4", "edit_history/clip_depth.original.mp4"])
        self.assertEqual((self.hist / "clip_depth.edit-03.mp4").read_bytes(), b"edit 3")

    def test_a_regenerated_map_becomes_the_original_and_the_old_one_is_kept(self):
        import time
        self.use()
        time.sleep(0.02)
        self.live.write_bytes(b"a new DepthCrafter map!")        # regenerated after the edit
        os.utime(self.live, None)
        self.assertEqual(self.C.map_to_edit(str(self.live)), str(self.live).replace("\\", "/"),
                         "the stale original was opened instead of the regenerated map")
        self.use(b"an edit of the new map")
        self.assertEqual(self.original().read_bytes(), b"a new DepthCrafter map!")
        self.assertEqual((self.hist / "clip_depth.original-01.mp4").read_bytes(), b"original map")
        self.assertEqual(self.live.read_bytes(), b"an edit of the new map")

    def test_the_old_edited_originals_layout_is_adopted(self):
        from core.depthedit.project import project_path_for
        old = self.dir / "edited" / "originals"
        old.mkdir(parents=True)
        (old / "clip_depth.mp4").write_bytes(b"original map")
        self.live.write_bytes(b"an old edit")
        (self.dir / "edited" / "clip_depthedit.json").write_text("{}")
        self.assertEqual(self.C.map_to_edit(str(self.live)), str(self.original()).replace("\\", "/"))
        self.assertEqual(self.original().read_bytes(), b"original map")
        self.assertTrue(os.path.isfile(project_path_for(str(self.hist), str(self.original()))),
                        "the clip's saved layers were left behind")
        self.assertFalse((self.dir / "edited").exists(), "the emptied edited/ folder was left")

    def test_the_kept_original_has_its_clips_project(self):
        from core.depthedit.project import depth_stem, project_path_for
        self.assertEqual(depth_stem("x/edit_history/clip_depth.original.mp4"), "clip")
        self.assertEqual(project_path_for("h", "x/edit_history/clip_depth.original.mp4"),
                         project_path_for("h", str(self.live)))

    def test_a_render_elsewhere_is_copied_not_moved(self):
        elsewhere = self.dir / "mine" / "clip_depth.mp4"
        elsewhere.parent.mkdir()
        elsewhere.write_bytes(b"my own render")
        self.C.use_edited_map(str(elsewhere), str(self.live))
        self.assertEqual(self.live.read_bytes(), b"my own render")
        self.assertTrue(elsewhere.is_file())

    def test_no_temp_file_is_left(self):
        self.use()
        self.use(b"again")
        leftovers = [p.name for p in self.dir.rglob("*") if p.suffix in (".part", ".tmp")]
        self.assertEqual(leftovers, [])


class PackedResultTests(unittest.TestCase):
    """audit 2026-09-28 (PERF): the editor kept every tracked frame's SAM 3
    masks as full-size bool arrays (1080p: 2 MB an object a frame -- 4 GB for
    two objects over 1000 frames). They are kept bit-packed; what reads them
    (which object is under a click, the ids) answers the same."""

    def test_packed_results_answer_as_the_masks_did(self):
        import numpy as np
        from core.depthedit import sam3_backend as sam
        rng = np.random.default_rng(5)
        masks = rng.random((3, 1, 90, 157)) > 0.6          # an odd width
        result = ([4, 7, 9], masks)
        packed = sam.pack_result(result)
        self.assertEqual(packed[0], [4, 7, 9])
        for _ in range(300):
            x, y = rng.random(), rng.random()
            self.assertEqual(sam.object_at(packed, x, y), sam.object_at(result, x, y))
        self.assertLess(sam.result_nbytes(packed), masks.nbytes / 6)

    def test_an_empty_result_packs(self):
        from core.depthedit import sam3_backend as sam
        import numpy as np
        packed = sam.pack_result(([], np.zeros((0, 1, 1), bool)))
        self.assertIsNone(sam.object_at(packed, 0.5, 0.5))

    def test_the_editor_keeps_them_packed(self):
        from studio.stages.depthedit import engine
        src = Path(engine.__file__).read_text(encoding="utf-8")
        lines = [ln.strip() for ln in src.splitlines()]
        self.assertEqual(lines.count("self._results[(layer.uid, frame)] = result"), 0, "a result kept unpacked")
        self.assertEqual(src.count("= sam.pack_result(result)"), 2)


class RenderSaveTests(unittest.TestCase):
    """audit 2026-09-28 (PERF): Render saved the project -- every tracked
    mask, compressed -- on the UI thread first (2.5 s for two layers over
    1000 1080p frames). The job saves a snapshot taken when Render was
    pressed; later edits cannot reach it."""

    def test_a_snapshot_is_what_was_on_screen(self):
        import numpy as np
        from core.depthedit.project import Project
        p = Project()
        ly = p.new_layer(4, 6)
        ly.masks.set(0, np.ones((4, 6), bool))
        ly.settings.brightness = 0.25
        snap = p.snapshot()
        ly.settings.brightness = 0.9
        ly.masks.set(1, np.ones((4, 6), bool))
        ly.name = "renamed"
        p.global_settings.contrast = 3.0
        s0 = snap.layers[0]
        self.assertEqual((s0.settings.brightness, s0.masks.frames(), s0.name), (0.25, [0], "Layer 1"))
        self.assertNotEqual(snap.global_settings.contrast, 3.0)

    def test_render_saves_off_the_ui_thread(self):
        import inspect
        from studio.stages.depthedit.engine import DepthEditEngine
        src = inspect.getsource(DepthEditEngine._render)
        before_job = src.split("def job()")[0]
        self.assertNotIn("_save_project(", before_job, "Render still saves the project on the UI thread")
        self.assertIn(".save(", src.split("def job()")[1].split("self._submit")[0])


class StopWhileIdleTests(unittest.TestCase):
    """audit 2026-09-28 (minor): Stop with nothing running set the flag, and
    the next job (a render, a track) started already stopped. A late click
    as a job ended did the same."""

    def _fake(self, pending):
        import threading
        from types import SimpleNamespace
        cancels = []
        return SimpleNamespace(_stop=threading.Event(), _job_pending=lambda: pending,
                               engine=SimpleNamespace(cancel=lambda: cancels.append(1))), cancels

    def test_stop_with_nothing_running_does_nothing(self):
        from studio.stages.depthedit.engine import DepthEditEngine
        fake, cancels = self._fake(pending=False)
        DepthEditEngine._request_stop(fake)
        self.assertFalse(fake._stop.is_set(), "the next job would start stopped")
        self.assertEqual(cancels, [])

    def test_stop_during_a_job_stops_it(self):
        from studio.stages.depthedit.engine import DepthEditEngine
        fake, cancels = self._fake(pending=True)
        DepthEditEngine._request_stop(fake)
        self.assertTrue(fake._stop.is_set())
        self.assertEqual(cancels, [1])


class OverlayRangeTests(unittest.TestCase):
    """audit 2026-09-28 (minor): the overlay tinted a layer's mask on frames
    outside its Start / End range, where the edit does nothing. Out of range
    it is drawn as a disabled layer is: dimmed."""

    def _tint(self, frame, **range_):
        import numpy as np
        from types import SimpleNamespace
        from core.depthedit.project import Project
        from studio.stages.depthedit.engine import DepthEditEngine
        p = Project()
        ly = p.new_layer(8, 8)
        for f in range(10):
            ly.masks.set(f, np.ones((8, 8), bool))
        for k, v in range_.items():
            setattr(ly.settings, k, v)
        fake = SimpleNamespace(project=p, selected=None, _unit=lambda idx: np.zeros((8, 8), np.float32))
        img = np.zeros((8, 8, 3), np.uint8)
        return int(DepthEditEngine._tint_masks(fake, img, frame).max())

    def test_out_of_range_frames_are_dimmed(self):
        inside = self._tint(3, start_frame=2, end_frame=5)
        outside = self._tint(8, start_frame=2, end_frame=5)
        self.assertGreater(inside, 0)
        self.assertLess(outside, inside, "a frame the layer does not edit was tinted as if it did")
        self.assertEqual(self._tint(8), inside, "no range: every frame is in it")


class FractionalPixelTests(unittest.TestCase):
    """Finer edits (user, 2026-10-01): Top / Bottom edge and Grow take
    fractional depth pixels -- the blend of the two nearest whole-pixel
    results -- and whole values render exactly as they always did."""

    def setUp(self):
        import numpy as np
        from core.depthedit import edit_ops
        self.np, self.E = np, edit_ops
        rng = np.random.default_rng(3)
        self.v = (rng.random((90, 140)) * 0.3 + 0.2).astype(np.float32)
        y, x = np.mgrid[0:90, 0:140]
        self.mask = ((x - 70) ** 2 / 40 ** 2 + (y - 45) ** 2 / 25 ** 2) < 1
        self.v[self.mask] += 0.4
        edit_ops.clear_mask_cache()

    def run_layer(self, **kw):
        # a small push, so Grow has a change to spread
        s = self.E.LayerSettings(edge_snap=0, feather=0, brightness=0.1, **kw)
        return self.E.apply_layer(self.v, self.mask, s, guide=self.v)

    def test_a_fraction_lands_between_its_whole_neighbours(self):
        np = self.np
        for key, lo, mid, hi in (("edge_top", -2, -2.5, -3), ("grow", 2, 2.5, 3), ("grow", -1, -1.5, -2),
                                 ("edge_bottom", 1, 1.4, 2)):
            a, b, c = self.run_layer(**{key: lo}), self.run_layer(**{key: mid}), self.run_layer(**{key: hi})
            self.assertTrue(np.all((b >= np.minimum(a, c) - 1e-6) & (b <= np.maximum(a, c) + 1e-6)), (key, mid))
            self.assertFalse(np.allclose(b, a) or np.allclose(b, c), (key, mid))

    def test_whole_values_are_unchanged_by_the_fraction_path(self):
        # a whole value takes no blend: 3.0 and 3 are the same edit, and edge_shift
        # of a whole number is the single morphology step it always was
        np = self.np
        self.assertTrue(np.array_equal(self.run_layer(grow=3.0), self.run_layer(grow=3)))
        self.assertTrue(np.array_equal(self.E.edge_shift(self.v, -2.0, top=True),
                                       self.E._edge_shift_whole(self.v, -2, top=True)))

    def test_an_old_project_s_whole_numbers_load_as_floats(self):
        s = self.E.layer_settings_from_dict({"edge_top": -3, "grow": 2, "feather": 4, "smooth": 1})
        self.assertEqual([type(getattr(s, k)) for k in ("edge_top", "grow", "feather", "smooth")], [float] * 4)
        self.assertEqual((s.edge_top, s.grow), (-3.0, 2.0))


def _engine_cls():
    from studio.legacy import install
    install()
    from studio.stages.depthedit import engine as E
    return E.DepthEditEngine


class NoOpStepTests(unittest.TestCase):
    """bug sweep 2026-10-09 (A9): a Track or Find that did nothing (no prompts,
    SAM 3 not ready, no text) still made an Undo step and cleared Redo."""

    def edit(self, action):
        import queue
        from types import SimpleNamespace
        from core.depthedit import history as H
        from core.depthedit.project import Project
        from studio.stages.depthedit.controller import DepthEditController
        eng = SimpleNamespace(project=Project(rgb_path="a.mp4", depth_path="a_depth.mp4"), _results={},
                              selected=None, _jobs=queue.Queue(), _set_busy_ui=lambda: None)
        eng._submit = lambda name, fn: _engine_cls()._submit(eng, name, fn)
        hist = H.History()
        hist._redo.append(H.State("Undone thing", eng.project.snapshot(), None, {}))
        fake = SimpleNamespace(_engine=eng, _history=hist, _history_live=lambda: True, _act=lambda fn: fn())
        fake._capture = lambda label: H.State(label, eng.project.snapshot(), None, {})
        DepthEditController._edit(fake, "Tracking", lambda: action(eng), keep=True)
        return hist

    def test_nothing_sent_no_step(self):
        hist = self.edit(lambda eng: None)            # _track's early return
        self.assertFalse(hist.can_undo)
        self.assertTrue(hist.can_redo, "Redo was cleared")

    def test_a_job_sent_is_a_step(self):
        hist = self.edit(lambda eng: eng._submit("Tracking", lambda: None))
        self.assertEqual(hist.undo_label, "Tracking")


class UnloadWordTests(unittest.TestCase):
    """bug sweep 2026-10-09 (A9): Unload said "VRAM released" when the unload
    was only queued."""

    def test_released_is_said_by_the_worker_after_the_unload(self):
        import inspect
        from types import SimpleNamespace
        cls = _engine_cls()
        said, jobs = [], []
        fake = SimpleNamespace(engine=SimpleNamespace(loaded=True, version=None),
                               release_gpu=lambda: jobs.append("unload"),
                               _status=said.append, _submit=lambda name, fn: jobs.append(fn),
                               _ui=lambda fn, *a: fn(*a))
        cls._toggle_model(fake)
        self.assertFalse(any("released" in s for s in said), said)
        self.assertEqual(jobs[0], "unload")
        jobs[1]()                                     # the worker, after the unload
        self.assertIn("released", said[-1])
        src = inspect.getsource(cls.release_gpu)
        self.assertNotIn("idle", src)
        self.assertNotIn("this waits for the unload", src)


class LoadClipSaveTests(unittest.TestCase):
    """bug sweep 2026-10-09 (A9): Load Clip compressed and wrote the open
    project on the UI thread. It's queued on the worker, before the load,
    and the load doesn't count that save as a job to wait for."""

    def test_load_clip_queues_the_save(self):
        from types import SimpleNamespace
        from studio.stages.depthedit.controller import DepthEditController
        kept, ran = [], []
        fake = SimpleNamespace(_engine=SimpleNamespace(_job_pending=lambda: False),
                               EDIT_OPS=DepthEditController.EDIT_OPS,
                               _keep_edits=lambda later=False: kept.append(later), _run=ran.append)
        DepthEditController.run(fake, "_load_clip")
        self.assertEqual(kept, [True])
        self.assertEqual(ran, ["_load_clip"])

    def test_a_queued_save_alone_doesnt_hold_the_load(self):
        import queue
        from types import SimpleNamespace
        cls = _engine_cls()
        eng = SimpleNamespace(_busy_job=None, _jobs=queue.Queue())
        eng._jobs.put(("Saving project", None))
        self.assertTrue(cls._only_saving(eng))
        eng._busy_job = "Saving project"
        self.assertTrue(cls._only_saving(eng))
        eng._jobs.put(("Tracking", None))
        self.assertFalse(cls._only_saving(eng))
        eng._jobs = queue.Queue()
        eng._busy_job = "Rendering"
        self.assertFalse(cls._only_saving(eng))


class SampleOffTheUiThreadTests(unittest.TestCase):
    """bug sweep 2026-10-09 (A9): Sample read every frame on the UI thread."""

    def test_sample_is_a_worker_job(self):
        from types import SimpleNamespace
        from unittest import mock
        from studio.stages.depthedit.controller import DepthEditController
        jobs, read = [], []
        eng = SimpleNamespace(project=SimpleNamespace(rgb_path="clip.mp4"), codes=object(),
                              _num_frames=lambda: 3, _status=lambda t: None,
                              _submit=lambda name, fn: jobs.append(fn), _unit=lambda i: read.append(i))
        fake = SimpleNamespace(_engine=eng, _splat_settings=lambda: (0.5, 20.0, ""),
                               _erase_layers=lambda e: [], _dirty=lambda: None)
        with mock.patch("core.removal.putback.load_settings", return_value={"factor": 1.0}):
            DepthEditController.samplePutBack(fake)
        self.assertEqual(len(jobs), 1, "not queued on the worker")
        self.assertEqual(read, [], "frames read on the UI thread")

    def test_the_gaps_take_the_nearest_frame(self):
        import numpy as np
        from types import SimpleNamespace
        from studio.stages.depthedit.controller import DepthEditController
        on = {2: 0.8, 7: 0.2}
        m = np.ones((4, 4), bool)
        layer = SimpleNamespace(masks=SimpleNamespace(get=lambda i: m if i in on else None))
        eng = SimpleNamespace(_unit=lambda i: np.full((4, 4), on[i], np.float32))
        track = DepthEditController._sample_track(eng, [layer], 10, 1.0, 0.5, 20.0)
        a, b = track[2], track[7]
        self.assertEqual(track, [a, a, a, a, a, b, b, b, b, b])


class SteadyTests(unittest.TestCase):
    """Steady (user, 2026-10-11): a tracked mask voted over the frames around it,
    so an outline SAM 3 redraws differently each frame (a lamp post's foot) holds."""

    def _store(self):
        import numpy as np
        from core.depthedit.project import MaskStore
        s = MaskStore(40, 20)
        rng = np.random.default_rng(1)
        for f in range(30):
            m = np.zeros((40, 20), bool)
            m[5:30, 8:12] = True                       # the post
            m[30:30 + int(rng.integers(0, 6)), 8:12] = True   # its foot, a different length every frame
            s.set(f, m)
        return s

    def test_off_is_the_tracked_mask(self):
        import numpy as np
        s = self._store()
        for f in (0, 13, 29):
            self.assertTrue(np.array_equal(s.get(f), s.get(f, 0)))

    def test_the_foot_holds_still(self):
        import numpy as np
        s = self._store()
        raw = [int(s.get(f).sum()) for f in range(5, 25)]
        held = [int(s.get(f, 6).sum()) for f in range(5, 25)]
        self.assertGreater(np.std(raw), 3 * max(np.std(held), 0.1), (raw, held))
        # the post itself is untouched
        self.assertTrue(s.get(15, 6)[5:30, 8:12].all())

    def test_a_frame_without_a_mask_stays_without(self):
        import numpy as np
        s = self._store()
        s.set(15, None)
        self.assertIsNone(s.get(15, 4))
        # ...and doesn't wear its neighbours away: only frames with a mask vote
        self.assertTrue(s.get(14, 4)[5:30, 8:12].all())

    def test_a_change_is_seen(self):
        import numpy as np
        s = self._store()
        before = s.get(10, 3)
        for f in range(7, 14):
            s.set(f, np.ones((40, 20), bool))
        self.assertFalse(np.array_equal(before, s.get(10, 3)))
        self.assertTrue(s.get(10, 3).all())

    @unittest.skipUnless(studio.tests.PROTOTYPE, studio.tests.NO_PROTOTYPE)
    def test_the_render_and_overlay_read_it(self):
        """The layer stack (preview), the overlay and the render read the steadied mask."""
        src = gen.build()
        self.assertIn("layer.masks.get(idx, layer.settings.steady)", src)
        self.assertIn("store.get(idx, s.steady)", src)
        self.assertIn("hi.get(idx, s.steady)", src)
        ctl = (REPO / "studio" / "stages" / "depthedit" / "controller.py").read_text(encoding="utf-8")
        self.assertIn("layer.masks_hi.get(idx, layer.settings.steady)", ctl)

    def test_saved_with_the_layer(self):
        from core.depthedit.edit_ops import LayerSettings, layer_settings_from_dict, settings_to_dict
        self.assertEqual(layer_settings_from_dict(settings_to_dict(LayerSettings(steady=5))).steady, 5)
        self.assertEqual(layer_settings_from_dict({}).steady, 0)     # a project from before



class NoFrameCountForSizeTests(unittest.TestCase):
    """2026-10-11 (user: the editor's first open froze Studio): asking for the
    source's size counted its frames by decoding the whole clip when the
    container has no count (MKV) -- 15 s on a 17 s HEVC 10-bit shot, on the UI
    thread. Size-only callers no longer decode; the load job counts packets and
    decodes only to confirm a mismatch."""

    @classmethod
    def setUpClass(cls):
        import subprocess
        import tempfile
        cls.tmp = tempfile.mkdtemp()
        cls.mkv = os.path.join(cls.tmp, "clip.mkv").replace("\\", "/")
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "testsrc2=size=320x176:rate=24:duration=1", "-pix_fmt", "yuv420p", cls.mkv], check=True)

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def recorded(self, fn):
        """fn()'s result and every ffprobe it ran, as "frames" / "packets" / "probe"."""
        from unittest import mock
        from core.depthedit import depth_io
        real, ran = depth_io.subprocess.run, []

        def run(cmd, *a, **k):
            ran.append("frames" if "-count_frames" in cmd else "packets" if "-count_packets" in cmd else "probe")
            return real(cmd, *a, **k)
        with mock.patch.object(depth_io.subprocess, "run", run):
            return fn(), ran

    def test_the_size_alone_decodes_nothing(self):
        from core.depthedit import depth_io, hires
        info, ran = self.recorded(lambda: depth_io.probe(self.mkv, count=False))
        self.assertEqual((info.width, info.height), (320, 176))
        self.assertNotIn("frames", ran)
        hires._probe_size.cache_clear()
        size, ran = self.recorded(lambda: hires.source_size(self.mkv))
        self.assertEqual(size, (320, 176))
        self.assertNotIn("frames", ran, "hires.source_size decoded the clip")

    def test_the_page_s_pixel_scale_decodes_nothing(self):
        from types import SimpleNamespace
        from studio.stages.depthedit.controller import DepthEditController
        eng = SimpleNamespace(info=SimpleNamespace(width=160, height=88), project=SimpleNamespace(rgb_path=self.mkv))
        fake = SimpleNamespace(_engine=eng)
        k, ran = self.recorded(lambda: DepthEditController._px_scale(fake, "y"))
        self.assertEqual(k, 2.0)
        self.assertNotIn("frames", ran, "the UI thread decoded the clip for its size")

    def test_count_frames_counts_packets_and_confirms_a_mismatch(self):
        from core.depthedit import depth_io
        n, ran = self.recorded(lambda: depth_io.count_frames(self.mkv, 24))
        self.assertEqual(n, 24)
        self.assertNotIn("frames", ran)
        n, ran = self.recorded(lambda: depth_io.count_frames(self.mkv, 30))
        self.assertEqual(n, 24)
        self.assertIn("frames", ran, "a mismatch is confirmed by decoding")
        self.assertEqual(depth_io.probe(self.mkv).num_frames, 24)      # the exact count is unchanged



class GlobalSettingsLoadTests(unittest.TestCase):
    """bug sweep 2026-10-14c: a project's Global Despeck / Local Match values were taken as they
    were -- a null failed every redraw (logged only), and Render."""

    def test_bad_values_fall_back_to_the_defaults(self):
        from core.depthedit import edit_ops as E
        g = E.global_settings_from_dict({"despeck_on": True, "despeck_strength": None, "match_frame": "x",
                                         "match_on": "yes", "despeck_left": "0.25", "gamma": 1.5})
        self.assertEqual((g.despeck_strength, g.match_frame, g.match_on), (1.0, 0, False))
        self.assertEqual((g.despeck_left, g.gamma, g.despeck_on), (0.25, 1.5, True))
        self.assertFalse(g.is_identity())            # runs, not raises
        self.assertIsInstance(g.despeck_zone(), tuple)


class AddTakesAlterControlsTests(unittest.TestCase):
    """User, 2026-10-11: Add gets Alter's controls. The edge moves move the
    shape's own edges; Smooth / Contrast / Flatten shape its depth (a
    gradient's); at their defaults an Add layer renders as it always did."""

    def setUp(self):
        import numpy as np
        from core.depthedit import edit_ops
        self.np, self.E = np, edit_ops
        edit_ops.clear_mask_cache()
        self.v = np.full((60, 80), 0.2, np.float32)
        self.m = np.zeros((60, 80), bool)
        self.m[20:40, 30:50] = True

    def add(self, **kw):
        kw.setdefault("feather", 0.0)
        kw.setdefault("edge_snap", 0)
        return self.E.apply_layer(self.v, self.m, self.E.LayerSettings(mode="Add", add_depth=0.8, **kw), guide=self.v)

    def rows(self, out):
        on = self.np.where((out > 0.5).any(axis=1))[0]
        return int(on[0]), int(on[-1])

    def cols(self, out):
        on = self.np.where((out > 0.5).any(axis=0))[0]
        return int(on[0]), int(on[-1])

    def test_defaults_are_the_shape_as_before(self):
        np = self.np
        want = np.where(self.m, np.float32(0.8), self.v)
        self.assertTrue(np.allclose(self.add(), want, atol=1e-6))

    def test_each_edge_move_moves_only_its_own_edge(self):
        self.assertEqual(self.rows(self.add(edge_top=3)), (17, 39))
        self.assertEqual(self.rows(self.add(edge_bottom=-4)), (20, 35))
        self.assertEqual(self.cols(self.add(edge_left=-2)), (32, 49))
        self.assertEqual(self.cols(self.add(edge_right=5)), (30, 54))
        self.assertEqual(self.cols(self.add(edge_top=3)), (30, 49), "a top move left the sides alone")

    def test_a_fraction_ends_the_edge_part_way(self):
        out = self.add(edge_top=1.5)
        self.assertAlmostEqual(float(out[19, 40]), 0.8, places=5)          # one whole row out
        self.assertAlmostEqual(float(out[18, 40]), 0.2 + 0.6 * 0.5, places=5)  # and half the next
        self.assertAlmostEqual(float(out[17, 40]), 0.2, places=5)

    def test_contrast_flatten_and_smooth_shape_a_gradient(self):
        np = self.np
        g = self.E.Gradient(enabled=True, near=0.8, far=0.6)     # nowhere near clipping at 2x
        base = self.add(gradient=g)[self.m]
        spread = lambda o: float(o[self.m].max() - o[self.m].min())  # noqa: E731
        self.assertGreater(spread(self.add(gradient=g, contrast=2.0)), spread(self.add(gradient=g)) * 1.5)
        self.assertLess(spread(self.add(gradient=g, flatten=1.0)), 1e-5)
        self.assertLess(float(np.std(self.add(gradient=g, smooth=6)[self.m])), float(np.std(base)))
        # flat, they have nothing to change
        self.assertTrue(np.allclose(self.add(contrast=2.5, flatten=0.5, smooth=4), self.add(), atol=1e-6))

    def test_contrast_pivots_on_the_shapes_own_median(self):
        """Bug sweep 2026-10-14c: the mask (0/1 uint8) indexed rows, so the pivot came from the
        wrong values -- and at 1080p an (H, W, W) temporary of ~16 GB."""
        np = self.np
        g = self.E.Gradient(enabled=True, near=0.8, far=0.6)
        before = self.add(gradient=g)[self.m]
        after = self.add(gradient=g, contrast=2.0)[self.m]
        self.assertAlmostEqual(float(np.median(after)), float(np.median(before)), places=3)


if __name__ == "__main__":
    unittest.main()
