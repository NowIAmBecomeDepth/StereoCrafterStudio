"""ClipQueue while a job runs (audit 2026-09-28 #28). The list is held so the
job's clips keep their rows -- but frame counts for those same clips were
dropped too (Splatting's H / L bars divide by them: they never filled), and a
rescan asked for mid-job (a folder or chapter changed) was lost for good.
The scan itself is stood in for; no files are read."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from PySide6.QtCore import QCoreApplication, QEventLoop  # noqa: E402

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from studio import clip_queue as Q  # noqa: E402

from studio.tests._qt import app as _qt_app
app = _qt_app()   # a GUI app: the whole suite shares it (tests/_qt.py)


def pump(until, sec=5.0):
    end = time.time() + sec
    while not until() and time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 20)
        time.sleep(0.01)
    QCoreApplication.processEvents(QEventLoop.AllEvents, 20)
    return until()


class ClipRatioTests(unittest.TestCase):
    """Each clip's aspect-ratio class beside it in the list (user, 2026-10-11)."""

    def test_a_fixture_clip_and_an_unreadable_one(self):
        sys.path.insert(0, str(Path(__file__).with_name("checks")))
        import _fixtures
        clip = str(_fixtures.path("small") / "src" / "clipA.mp4")        # 640x360
        cache = {}
        self.assertEqual(Q.clip_ratio(clip, cache), "16:9")
        self.assertEqual(len(cache), 1)
        self.assertEqual(Q.clip_ratio(clip, cache), "16:9")             # cached
        self.assertEqual(Q.clip_ratio("no_such_clip.mp4", {}), "")


class HeldQueueTests(unittest.TestCase):
    def setUp(self):
        self.folder = ["a.mp4", "b.mp4"]
        self.counts = {"a.mp4": 10, "b.mp4": 20, "c.mp4": 30}
        self.frames_gate = None
        saved = (Q.merge_clips, Q.merge_fx, Q.frame_count)
        self.addCleanup(lambda: (setattr(Q, "merge_clips", saved[0]), setattr(Q, "merge_fx", saved[1]),
                                 setattr(Q, "frame_count", saved[2])))
        Q.merge_clips = lambda values: list(self.folder)
        Q.merge_fx = lambda values, paths: [[False] for _ in paths]

        def frame_count(path, cache):
            if self.frames_gate is not None:
                self.frames_gate()
            return self.counts[path]
        Q.frame_count = frame_count
        self.q = Q.ClipQueue("merging", lambda: {})

    def scanned(self):
        self.q.rescan()
        self.assertTrue(pump(lambda: self.q.property("clipFrames") == [self.counts[p] for p in self.folder]))

    def test_frame_counts_for_the_listed_clips_land_while_held(self):
        import threading
        release = threading.Event()
        self.frames_gate = lambda: release.wait(5)
        self.q.rescan()
        self.assertTrue(pump(lambda: self.q.property("clipPaths") == self.folder))
        self.q.hold(True)                   # the job starts before the counts are in
        release.set()
        self.assertTrue(pump(lambda: self.q.property("clipFrames") == [10, 20], 3),
                        f"frame counts dropped while held: {self.q.property('clipFrames')}")

    def test_a_rescan_asked_for_mid_job_happens_after_it(self):
        self.scanned()
        self.q.hold(True)
        self.folder = ["c.mp4"]             # a folder / chapter change during the run
        self.q.rescan()
        pump(lambda: False, 0.6)
        self.assertEqual(self.q.property("clipPaths"), ["a.mp4", "b.mp4"], "the held list changed mid-job")
        self.q.hold(False)
        self.assertTrue(pump(lambda: self.q.property("clipPaths") == ["c.mp4"], 3),
                        f"the change was lost: {self.q.property('clipPaths')}")

    def test_no_rescan_after_a_job_nothing_asked_for(self):
        self.scanned()
        self.q.hold(True)
        self.folder = []                    # the batch moved its clips to finished/
        self.q.hold(False)
        pump(lambda: False, 0.6)
        self.assertEqual(self.q.property("clipPaths"), ["a.mp4", "b.mp4"],
                         "the finished job's rows should stay until something asks for a rescan")


    def test_a_badge_refresh_mid_job_keeps_the_rows(self):
        self.scanned()
        self.q.hold(True)
        self.folder = []                    # clips moved to finished/ by the batch
        Q.merge_fx = lambda values, paths: [[True] for _ in paths]   # a sidecar saved mid-job
        self.q.refreshFx()
        self.assertEqual(self.q.property("clipFx"), [[False], [False]], "badges changed mid-job")
        self.q.hold(False)
        pump(lambda: False, 0.6)
        self.assertEqual(self.q.property("clipPaths"), ["a.mp4", "b.mp4"], "the rows went: a full rescan ran")
        self.assertEqual(self.q.property("clipFx"), [[True], [True]], "the badges were not re-read")

class MultiMapFoldersTests(unittest.TestCase):
    """user 2026-09-29: the Depth Map Editor's edit_history (the maps Use in
    Splatting replaced, and its pending render) is never a map folder."""

    def test_edit_history_is_not_a_map(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "src").mkdir()
            (d / "src" / "clip.mp4").write_bytes(b"v")
            for sub in ("edit_history", "maps"):
                (d / "depth" / sub).mkdir(parents=True)
            (d / "depth" / "edit_history" / "clip_depth.mp4").write_bytes(b"a pending render")
            (d / "depth" / "maps" / "clip_depth.mp4").write_bytes(b"the map")
            pairs = Q.splat_pairs({"input_source_clips": str(d / "src"), "input_depth_maps": str(d / "depth"),
                                   "multi_map": True})
            self.assertEqual([Path(p).parent.name for _v, p in pairs], ["maps"])


class SplatFxTests(unittest.TestCase):
    """audit 2026-09-28 (PERF): the badges come from one listing of the
    sidecar folder; they still follow a sidecar that is saved again or gone."""

    def test_badges_follow_the_sidecars(self):
        import json
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            depths = [str(d / "a_depth.mp4"), str(d / "b_depth.mp4")]
            side = d / "a_depth.fssidecar"
            side.write_text(json.dumps({"keyframe_track": True}))
            cache = {}
            vals = {"input_depth_maps": str(d)}
            self.assertEqual(Q.splat_fx(vals, depths, cache), [[True, False, False, True, False, False, False, False, False], [False] * 9])
            side.write_text(json.dumps({"boost_amount": 0.5}))
            os.utime(side, (1, 2_000_000_000))
            self.assertEqual(Q.splat_fx(vals, depths, cache)[0], [False, False, True, True, False, False, False, False, False])
            # Particles on (user, 2026-10-08): the seventh flag
            side.write_text(json.dumps({"rain_mode": "moving"}))
            os.utime(side, (1, 2_000_000_100))
            self.assertEqual(Q.splat_fx(vals, depths, cache)[0], [False, False, False, True, False, False, True, False, False])
            side.write_text(json.dumps({"rain_mode": "off"}))
            os.utime(side, (1, 2_000_000_200))
            self.assertFalse(Q.splat_fx(vals, depths, cache)[0][6])
            side.unlink()
            self.assertEqual(Q.splat_fx(vals, depths, cache)[0], [False] * 9)

    def test_rain_badge_only_from_a_saved_sidecar(self):
        """user, 2026-10-10: Rain turned on in the page lit the badge on every clip (bug sweep
        A2 made it follow the page where the sidecar said nothing). Like the other effects, only a
        sidecar that saved Rain on shows it."""
        import json
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            depths = [str(d / "a_depth.mp4"), str(d / "b_depth.mp4")]
            side = d / "a_depth.fssidecar"
            side.write_text(json.dumps({"convergence_plane": 0.5}))     # from before Particles
            cache = {}
            moving = {"input_depth_maps": str(d), "rain_mode": "moving"}
            off = {"input_depth_maps": str(d), "rain_mode": "off"}
            self.assertEqual([row[6] for row in Q.splat_fx(moving, depths, cache)], [False, False],
                             "the page's Rain alone lights no badge")
            self.assertEqual([row[6] for row in Q.splat_fx(off, depths, cache)], [False, False])
            side.write_text(json.dumps({"rain_mode": "moving"}))
            os.utime(side, (1, 2_000_000_000))
            self.assertEqual([row[6] for row in Q.splat_fx(off, depths, cache)], [True, False],
                             "a sidecar that saved Rain on shows it, whatever the page says")


class ErasedBadgeTests(unittest.TestCase):
    """user, 2026-10-16: a clip Erase from Clip took an object out of gets an "Object removed" badge (the
    eighth flag) -- in Splatting's list (the clip itself) and Merging's (its original, by core name)."""

    def setUp(self):
        import json
        import tempfile
        from core.removal import erase as E
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.src = self.d / "src"
        hist = self.src / "edit_history"
        hist.mkdir(parents=True)
        self.erased, self.plain = self.src / "shotA.mp4", self.src / "shotB.mp4"
        for p in (self.erased, self.plain):
            p.write_bytes(b"clip")
        orig = hist / "shotA.original.mp4"
        orig.write_bytes(b"the original")
        json.dump({"live": E._file_id(str(self.erased)), "original": str(orig)},
                  open(E.record_path(str(self.erased)), "w", encoding="utf-8"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_splatting(self):
        depths = [str(self.d / "shotA_depth.mp4"), str(self.d / "shotB_depth.mp4")]
        rows = Q.splat_fx({"input_depth_maps": str(self.d)}, depths, {}, [str(self.erased), str(self.plain)])
        self.assertEqual([r[7] for r in rows], [True, False])

    def test_a_clip_changed_since_isnt_an_erase(self):
        import os
        os.utime(self.erased, (1, 2_000_000_000))       # replaced: the record no longer fits it
        rows = Q.splat_fx({"input_depth_maps": str(self.d)}, [str(self.d / "x_depth.mp4")], {}, [str(self.erased)])
        self.assertFalse(rows[0][7])

    def test_a_vda_map(self):
        # user, 2026-10-25: a badge on the clips whose depth map Video Depth Anything made (its tag)
        import subprocess
        from core.vda.output import TAG
        maps = [self.d / "shotA_depth.mp4", self.d / "shotB_depth.mp4"]
        for p, tag in zip(maps, (["-metadata", f"comment={TAG}"], [])):
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=64x32:d=0.2",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", *tag, str(p)], check=True)
        cache = {}
        rows = Q.splat_fx({"input_depth_maps": str(self.d)}, [str(m) for m in maps], cache)
        self.assertEqual([r[8] for r in rows], [True, False])
        self.assertEqual(Q.splat_fx({"input_depth_maps": str(self.d)}, [str(maps[0])], cache)[0][8], True)  # cached
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=64x32:d=0.3",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(maps[0])], check=True)   # DepthCrafter's again
        self.assertFalse(Q.splat_fx({"input_depth_maps": str(self.d)}, [str(maps[0])], cache)[0][8])

    def test_merging(self):
        inp = self.d / "inpainted"
        inp.mkdir()
        paths = [str(inp / "shotA_1920_inpainted_right_eye.mp4"), str(inp / "shotB_1920_inpainted_right_eye.mp4")]
        rows = Q.merge_fx({"original_folder": str(self.src), "inpainted_folder": str(inp)}, paths)
        self.assertEqual([r[7] for r in rows], [True, False])
        self.assertEqual(len(rows[0]), 8)


class DepthEditedBadgeTests(unittest.TestCase):
    """user 2026-09-30: a clip whose depth map Splatting reads is an edit from
    the Depth Map Editor (Use in Splatting) gets a badge -- the last flag."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.live = self.d / "clip_depth.mp4"
        self.live.write_bytes(b"the edited map")
        (self.d / "other_depth.mp4").write_bytes(b"untouched")

    def tearDown(self):
        self.tmp.cleanup()

    def edited(self):
        depths = [str(self.live), str(self.d / "other_depth.mp4")]
        return [f[4] for f in Q.splat_fx({"input_depth_maps": str(self.d)}, depths, {})]

    def keep_original(self, folder, record=True):
        import json
        from core.depthedit.project import file_id
        folder.mkdir(parents=True, exist_ok=True)
        backup = folder / "clip_depth.mp4" if folder.name == "originals" else folder / "clip_depth.original.mp4"
        backup.write_bytes(b"the original map")
        if record:
            Path(str(backup) + ".edit.json").write_text(json.dumps({"live": file_id(str(self.live))}))

    def test_no_edit_no_badge(self):
        self.assertEqual(self.edited(), [False, False])

    def test_the_edit_in_use_has_the_badge(self):
        self.keep_original(self.d / "edit_history")
        self.assertEqual(self.edited(), [True, False])

    def test_a_map_regenerated_since_is_not_an_edit(self):
        self.keep_original(self.d / "edit_history")
        self.live.write_bytes(b"DepthCrafter made this one again, longer")
        self.assertEqual(self.edited(), [False, False])

    def test_an_original_kept_before_the_record_existed_counts(self):
        self.keep_original(self.d / "edit_history", record=False)
        self.assertEqual(self.edited(), [True, False])

    def test_the_old_edited_originals_layout_counts(self):
        self.keep_original(self.d / "edited" / "originals")
        self.assertEqual(self.edited(), [True, False])


class TextMaskBadgeTests(unittest.TestCase):
    """user 2026-10-03: a clip Detect Text saved a mask for (<clip>_submask.mp4
    beside it, which a render uses) gets a badge -- the sixth flag."""

    def test_the_mask_beside_the_clip(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            clips = [str(d / "src" / "a.mp4"), str(d / "src" / "b.mp4")]
            (d / "src").mkdir()
            depths = [str(d / "a_depth.mp4"), str(d / "b_depth.mp4")]
            vals = {"input_depth_maps": str(d)}
            self.assertEqual([f[5] for f in Q.splat_fx(vals, depths, {}, clips)], [False, False])
            (d / "src" / "A_submask.mp4").write_bytes(b"mask")       # Windows: any case
            self.assertEqual([f[5] for f in Q.splat_fx(vals, depths, {}, clips)], [True, False])
            self.assertEqual(Q.splat_fx(vals, depths, {})[0][5], False)  # no clips given: no text mask


class TicksTests(unittest.TestCase):
    """user 2026-09-30: tick clips for Process Selected / Move to Finished."""

    def queue(self, paths):
        q = Q.ClipQueue("splatting", lambda: {})
        q._on_done((list(paths), [p.replace(".mp4", "_depth.mp4") for p in paths]))
        return q

    def test_tick_all_none_and_order(self):
        q = self.queue(["C:/a/one.mp4", "C:/a/two.mp4", "C:/a/three.mp4"])
        self.assertEqual(q.property("checked"), [False, False, False])
        q.setChecked("C:/a/three.mp4", True)
        q.setChecked("C:/a/one.mp4", True)
        self.assertEqual(q.property("checkedCount"), 2)
        self.assertEqual(q.checked_paths(), ["C:/a/one.mp4", "C:/a/three.mp4"])      # the list's order
        q.checkAll()
        self.assertEqual(q.property("checked"), [True, True, True])
        q.checkNone()
        self.assertEqual(q.property("checkedCount"), 0)

    def test_a_clip_that_leaves_the_list_is_no_longer_ticked(self):
        q = self.queue(["C:/a/one.mp4", "C:/a/two.mp4"])
        q.checkAll()
        q._on_done((["C:/a/two.mp4"], ["C:/a/two_depth.mp4"]))
        self.assertEqual(q.checked_paths(), ["C:/a/two.mp4"])
        q._on_done((["C:/a/one.mp4", "C:/a/two.mp4"], ["", ""]))      # back again: not re-ticked
        self.assertEqual(q.checked_paths(), ["C:/a/two.mp4"])


class MoveToFinishedTests(unittest.TestCase):
    """Move Selected to Finished moves what a batch moves for each clip, and
    the moved clips leave the list (user, 2026-09-30)."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def touch(self, *parts):
        p = self.d.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
        return str(p)

    def test_splat_moves_the_clip_its_depth_and_sidecars(self):
        v = self.touch("src", "shot.mp4")
        dep = self.touch("depth", "shot_depth.mp4")
        sc = self.touch("depth", "shot_depth.fssidecar")
        self.touch("depth", "other_depth.fssidecar")
        moves = Q.splat_finished_moves({"input_depth_maps": str(self.d / "depth")}, [(v, dep)])
        self.assertEqual(moves, [(v, str(self.d / "src")), (dep, str(self.d / "depth")), (sc, str(self.d / "depth"))])

    def test_merge_moves_the_inpainted_its_splat_and_original(self):
        inp = self.touch("inp", "shot_1408_inpainted_right_eye.mp4")
        inp_sc = self.touch("inp", "shot_1408_inpainted_right_eye.fssidecar")
        grid = self.touch("mask", "shot_1408_splatted4.mp4")
        self.touch("mask", "shotB_1408_splatted4.mp4")                     # another clip: stays
        orig = self.touch("orig", "shot.mp4")
        orig_sc = self.touch("orig", "shot.fssidecar")
        vals = {"inpainted_folder": str(self.d / "inp"), "mask_folder": str(self.d / "mask"),
                "original_folder": str(self.d / "orig")}
        got = Q.merge_finished_moves(vals, [inp])
        self.assertEqual(got, [(inp, str(self.d / "inp")), (inp_sc, str(self.d / "inp")),
                               (grid, str(self.d / "mask")), (orig, str(self.d / "orig")),
                               (orig_sc, str(self.d / "orig"))])

    def test_moved_clips_leave_the_list(self):
        for n in ("one", "two"):
            self.touch("src", f"{n}.mp4")
            self.touch("depth", f"{n}_depth.mp4")
        vals = {"input_source_clips": str(self.d / "src"), "input_depth_maps": str(self.d / "depth")}
        q = Q.ClipQueue("splatting", lambda: vals)
        q.rescan()
        self.assertTrue(pump(lambda: len(q.property("clips")) == 2))
        q.setChecked(str(self.d / "src" / "one.mp4"), True)
        moved = []
        q.movedToFinished.connect(lambda n, left: moved.append((n, list(left))))
        q.moveCheckedToFinished()
        self.assertTrue(pump(lambda: moved and q.property("clips") == ["two.mp4"], 10))
        self.assertEqual(moved, [(1, [])])
        self.assertTrue((self.d / "src" / "finished" / "one.mp4").exists())
        self.assertTrue((self.d / "depth" / "finished" / "one_depth.mp4").exists())
        self.assertEqual(q.property("checkedCount"), 0)

    def test_never_while_a_job_holds_the_list(self):
        v = self.touch("src", "one.mp4")
        q = Q.ClipQueue("splatting", lambda: {})
        q._on_done(([v], [""]))
        q.checkAll()
        q.hold(True)
        q.moveCheckedToFinished()
        self.assertFalse(q.property("moving"))
        self.assertTrue(os.path.exists(v))


class OtherStagesTests(unittest.TestCase):
    """DepthCrafter's and Inpainting's lists, and the rows' "Play ..." files
    (user, 2026-10-10), over empty stand-in files in a temp folder."""

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())

    def touch(self, *parts):
        p = self.tmp.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")
        return str(p)

    def test_inpainting_lists_the_lo_res_splats_and_finds_each_hi_res(self):
        a = self.touch("lo", "shot-0006_640_splatted2.mp4")
        b = self.touch("lo", "shot-0007_640_splatted4.mov")
        self.touch("lo", "notes.txt")
        hi = self.touch("hi", "shot-0006_1920_splatted2.mp4")
        self.touch("hi", "shot-0006_1920_splatted4.mp4")        # another layout: not its match
        self.assertEqual(Q.inpaint_clips({"input_folder": str(self.tmp / "lo")}), [a, b])
        self.assertEqual(Q.inpaint_hires_of(a, str(self.tmp / "hi")), hi)
        self.assertEqual(Q.inpaint_hires_of(b, str(self.tmp / "hi")), "")
        self.assertEqual(Q.inpaint_hires_of(a, str(self.tmp / "lo")), "")      # the same folder: none

    def test_inpainting_moves_the_splat_its_sidecar_and_its_hi_res(self):
        a = self.touch("lo", "shot-0006_640_splatted2.mp4")
        sc = self.touch("lo", "shot-0006_640_splatted2.spsidecar")
        hi = self.touch("hi", "shot-0006_1920_splatted2.mp4")
        moves = Q.inpaint_finished_moves({"hires_blend_folder": str(self.tmp / "hi")}, [a])
        self.assertEqual(moves, [(a, str(self.tmp / "lo")), (sc, str(self.tmp / "lo")), (hi, str(self.tmp / "hi"))])

    def test_depthcrafter_moves_each_source_beside_itself(self):
        a = self.touch("src", "a.mp4")
        self.assertEqual(Q.dc_finished_moves([a]), [(a, str(self.tmp / "src"))])

    def test_merging_plays_the_original_and_the_splat(self):
        inp = self.touch("inp", "mA_320_inpainted_right_eye.mp4")
        orig = self.touch("orig", "mA.mp4")
        splat = self.touch("mask", "mA_1920_splatted2.mp4")
        got = Q.merge_related({"original_folder": str(self.tmp / "orig"), "mask_folder": str(self.tmp / "mask")}, inp)
        self.assertEqual(got, [("Play Original", orig), ("Play Splat", splat)])


class SendOnTests(unittest.TestCase):
    """Upcoming Jobs' "Send to <next stage>" (user, 2026-10-11): studio.send_on.plan."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def touch(self, *parts):
        p = self.tmp.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
        return str(p)

    def test_depthcrafter_to_splatting(self):
        from studio import send_on
        src = self.touch("src", "finished", "clip1.mp4")              # moved on by the batch
        self.touch("depth", "clip10_depth.mp4")                        # not clip1's
        depth = self.touch("depth", "clip1_depth.mp4")
        p = send_on.plan("depthcrafter", str(self.tmp / "src" / "clip1.mp4"),
                         {"output_dir": str(self.tmp / "depth")}, {"output": "X:/ch/splats"})
        self.assertEqual(p["stage"], "splatting")
        self.assertEqual(p["fields"], {"input_source_clips": src, "input_depth_maps": depth,
                                       "output_splatted": "X:/ch/splats"})

    def test_splatting_to_inpainting(self):
        from studio import send_on
        low = self.touch("out", "lowres", "clip1_640_splatted2.mp4")
        hi = self.touch("out", "hires", "clip1_1920_splatted2.mp4")
        p = send_on.plan("splatting", "S:/src/clip1.mp4", {"output_splatted": str(self.tmp / "out")}, {})
        self.assertEqual(p["fields"], {"input_folder": low, "hires_blend_folder": os.path.dirname(hi)})
        self.assertNotIn("note", p)

    def test_only_a_hires_splat_is_said(self):
        """bug sweep 2026-10-14c: the Hi-Res splat went in as the Lo-Res input without a word."""
        from studio import send_on
        hi = self.touch("out", "hires", "clip1_1920_splatted2.mp4")
        p = send_on.plan("splatting", "S:/src/clip1.mp4", {"output_splatted": str(self.tmp / "out")}, {})
        self.assertEqual(p["fields"]["input_folder"], hi)
        self.assertIn("Hi-Res splat", p.get("note", ""))

    def test_inpainting_to_merging_with_a_chapter(self):
        from studio import send_on
        self.touch("inp", "clip2_640_inpainted_right_eye.mp4")
        mine = self.touch("inp", "clip1_640_inpainted_right_eye.mp4")
        ch = {"inpainted": "I", "original": "O", "mask": "M", "output": "OUT"}
        p = send_on.plan("inpainting", str(self.tmp / "lowres" / "clip1_640_splatted2.mp4"),
                         {"output_folder": str(self.tmp / "inp")}, ch)
        self.assertEqual(p["fields"], {"inpainted_folder": mine, "original_folder": "O", "mask_folder": "M",
                                       "output_folder": "OUT"})

    def test_nothing_made_yet(self):
        from studio import send_on
        (self.tmp / "depth").mkdir()
        self.touch("src", "clip1.mp4")
        p = send_on.plan("depthcrafter", str(self.tmp / "src" / "clip1.mp4"), {"output_dir": str(self.tmp / "depth")})
        self.assertIn("error", p)
        self.assertIn("error", send_on.plan("merging", "x.mp4", {}))




class SplatShapeTests(unittest.TestCase):
    """user, 2026-10-14: Inpainting's list showed the splat's shape (its panels together), not
    the video's."""

    def test_a_splats_shape_is_one_panels(self):
        import cv2
        import numpy as np
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        for name, (w, h) in (("shot_640_splatted2.mp4", (1280, 360)), ("shot_640_splatted4.mp4", (1280, 720)),
                             ("shot.mp4", (640, 360))):
            out = cv2.VideoWriter(os.path.join(d, name), cv2.VideoWriter_fourcc(*"mp4v"), 24, (w, h))
            for _ in range(2):
                out.write(np.zeros((h, w, 3), np.uint8))
            out.release()
        want = Q.clip_ratio(os.path.join(d, "shot.mp4"), {})
        self.assertEqual(want, "16:9")
        for name in ("shot_640_splatted2.mp4", "shot_640_splatted4.mp4"):
            self.assertEqual(Q.clip_ratio(os.path.join(d, name), {}), want, name)

class SweepCQueueTests(unittest.TestCase):
    """Bug sweep 2026-10-14c."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_play_offers_videos_and_folders_only(self):
        """A .npz depth map's Play went to whatever Windows opens that with."""
        clip = os.path.join(self.tmp, "shot.mp4")
        npz = os.path.join(self.tmp, "shot_depth.npz")
        for f in (clip, npz):
            open(f, "wb").close()
        q = Q.ClipQueue("splatting", lambda: {})
        q._paths, q._depths = [clip], [npz]
        self.assertEqual([i["text"] for i in q.playables(clip)], ["Play Source Clip"])

    def test_a_start_waits_for_a_move_still_running(self):
        """A Start while Move to Finished was still moving read the clips it was taking."""
        said = []

        class Toast:
            def emit(self, *a):
                said.append(a)
        q = Q.ClipQueue("merging", lambda: {})
        self.assertFalse(Q.move_pending(q, Toast()))
        q._moving = True
        self.assertTrue(Q.move_pending(q, Toast()))
        self.assertEqual(said[0][1], "Moving clips")


class OpenInExplorerTests(unittest.TestCase):
    """A row's "Open ... in Explorer" (user, 2026-10-18): one item per kind of file that goes with the
    clip at that stage -- any file, a .npz depth map too -- selected in Explorer; missing ones left out."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def touch(self, *parts):
        p = self.tmp.joinpath(*parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"")
        return str(p)

    def items(self, q, path):
        return [(i["text"], i["path"]) for i in q.explorables(path)]

    def test_depthcrafter_the_source(self):
        clip = self.touch("src", "a.mp4")
        q = Q.ClipQueue("depthcrafter", lambda: {})
        self.assertEqual(self.items(q, clip), [("Open Source Clip in Explorer", clip)])

    def test_splatting_the_source_and_its_depth_map_even_a_npz(self):
        clip = self.touch("src", "shot.mp4")
        npz = self.touch("depth", "shot_depth.npz")
        q = Q.ClipQueue("splatting", lambda: {})
        q._paths, q._depths = [clip], [npz]
        self.assertEqual(self.items(q, clip), [("Open Source Clip in Explorer", clip), ("Open Depth Map in Explorer", npz)])
        self.assertEqual([i["text"] for i in q.playables(clip)], ["Play Source Clip"])     # Play stays videos only

    def test_inpainting_the_lo_res_and_hi_res_splats(self):
        lo = self.touch("lo", "shot-0006_640_splatted2.mp4")
        hi = self.touch("hi", "shot-0006_1920_splatted2.mp4")
        q = Q.ClipQueue("inpainting", lambda: {"hires_blend_folder": str(self.tmp / "hi")})
        self.assertEqual(self.items(q, lo), [("Open Lo-Res Splat in Explorer", lo), ("Open Hi-Res Splat in Explorer", hi)])

    def test_merging_the_inpainted_clip_its_original_and_its_splat(self):
        inp = self.touch("inp", "mA_320_inpainted_right_eye.mp4")
        orig = self.touch("orig", "mA.mp4")
        splat = self.touch("mask", "mA_1920_splatted2.mp4")
        q = Q.ClipQueue("merging", lambda: {"original_folder": str(self.tmp / "orig"), "mask_folder": str(self.tmp / "mask")})
        self.assertEqual(self.items(q, inp), [("Open Inpainted Clip in Explorer", inp), ("Open Original in Explorer", orig),
                                              ("Open Splat in Explorer", splat)])

    def test_a_file_not_there_is_left_out(self):
        clip = self.touch("src", "shot.mp4")
        q = Q.ClipQueue("splatting", lambda: {})
        q._paths, q._depths = [clip], [str(self.tmp / "depth" / "shot_depth.mp4")]
        self.assertEqual(self.items(q, clip), [("Open Source Clip in Explorer", clip)])

    def test_reveal_selects_the_file_in_explorer(self):
        from core.ui import folder_open as FO
        clip = self.touch("my clips", "a b.mp4")
        with mock.patch.object(FO.sys, "platform", "win32"), mock.patch.object(FO.subprocess, "Popen") as popen:
            ok, _ = FO.reveal_file(clip)
        self.assertTrue(ok)
        self.assertEqual(popen.call_args[0][0], f'explorer /select,"{os.path.normpath(clip)}"')

    def test_reveal_of_a_missing_file_or_a_folder_opens_the_folder(self):
        from core.ui import folder_open as FO
        with mock.patch.object(FO, "open_folder", return_value=(True, "x")) as of:
            FO.reveal_file(str(self.tmp / "gone.mp4"))
            FO.reveal_file(str(self.tmp))
        self.assertEqual([c[0][0] for c in of.call_args_list], [str(self.tmp / "gone.mp4"), str(self.tmp)])


if __name__ == "__main__":
    unittest.main()
