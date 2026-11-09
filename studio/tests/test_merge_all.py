"""Merging's Merge All to One Video (user, 2026-10-11; studio/merge_all.py): the
scenes found chapter by chapter in movie order, their merged outputs (an L / R
pair, a Full SBS), what a missing one still needs, and the movie built both
ways -- concat for one intra-frame format, pipe for anything else -- with a
black clip of the missing scene's length when overridden. A fake project in a
temp folder; nothing of the user's is read."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first
from studio import merge_all as M  # noqa: E402

EW, EH, FPS = 256, 144, "24"      # DNxHR wants 256 wide or more


def _clip(path: str, frames: int, w: int = EW, h: int = EH, codec=("-c:v", "libx264", "-pix_fmt", "yuv420p"),
          colour: str = "") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    src = f"color={colour}:s={w}x{h}:r={FPS}" if colour else f"testsrc2=s={w}x{h}:r={FPS}"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", src,
                    "-frames:v", str(frames)] + list(codec) + [path], check=True)


DNX = ("-c:v", "dnxhd", "-profile:v", "dnxhr_hq", "-pix_fmt", "yuv422p")


class MergeAllTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        p = cls.p = os.path.join(cls.tmp, "Movie")
        src, dep, spl, inp, mer = (os.path.join(p, n) for n in
                                   ("M Source Clips", "M DepthMaps", "M Splats", "M Inpainted", "M Converted LR Clips"))
        # chapter 2 sorts after chapter 10 by name: the number decides
        c1, c2, c10 = "Chapter 1 - Start", "Chapter 2 - Middle", "Chapter 10 - End"
        _clip(os.path.join(src, c1, "finished", "finished", "scene00000500.mp4"), 6)
        _clip(os.path.join(src, c1, "finished", "Part 2", "scene00000100.mp4"), 5)
        _clip(os.path.join(src, c2, "scene00001000.mp4"), 4)                     # missing: needs Splatting
        _clip(os.path.join(src, c2, "scene00001200.mp4"), 3)                     # missing: needs DepthCrafter
        _clip(os.path.join(src, c10, "scene00002000.mp4"), 4)
        _clip(os.path.join(src, c1, "edit_history", "scene00000500.original.mp4"), 2)    # never a scene
        _clip(os.path.join(src, c1, "finished", "finished", "scene00000500_submask.mp4"), 2)   # Detect Text's mask
        _clip(os.path.join(src, c10, "scene00002000_depth.mp4"), 2)                             # a map filed there
        for stem, n in (("scene00000100", 5), ("scene00000500", 6), ("scene00002000", 4)):
            ch = c10 if stem.endswith("2000") else c1
            sub = "New folder" if stem.endswith("500") else ""
            for eye, colour in (("L", "red"), ("R", "blue")):
                _clip(os.path.join(mer, ch, sub, f"{stem}_{EW}_merged_full_sbs_{eye}.mov"), n, codec=DNX, colour=colour)
        # what scene00001000 has been through: a depth map only
        _clip(os.path.join(dep, c2, "scene00001000_depth.mp4"), 4)
        os.makedirs(os.path.join(spl, c2, "lowres"), exist_ok=True)
        os.makedirs(os.path.join(inp, c2), exist_ok=True)
        cls.info = M.scan(p)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_scenes_in_movie_order(self):
        info = self.info
        self.assertEqual([s["stem"] for s in info["scenes"]],
                         ["scene00000100", "scene00000500", "scene00001000", "scene00001200", "scene00002000"])
        self.assertEqual([c["num"] for c in info["chapters"]], [1, 2, 10])
        self.assertEqual(info["total"], 5)
        first = info["scenes"][0]
        self.assertEqual(first["kind"], "lr")
        self.assertTrue(first["files"][0].endswith("_L.mov") and first["files"][1].endswith("_R.mov"))
        self.assertEqual(info["scenes"][1]["kind"], "lr", "an output in a subfolder of the chapter is found")

    def test_missing_scenes_say_which_stage_they_need(self):
        miss = {s["stem"]: s for s in self.info["missing"]}
        self.assertEqual(set(miss), {"scene00001000", "scene00001200"})
        self.assertEqual(miss["scene00001000"]["stage"], "splatting")
        self.assertEqual(miss["scene00001200"]["stage"], "depthcrafter")
        self.assertEqual([c["missing"] for c in self.info["chapters"]], [0, 2, 0])

    def test_send_plans(self):
        miss = {s["stem"]: s for s in self.info["missing"]}
        sp = M.send_plan(miss["scene00001000"])
        self.assertEqual((sp["stage"], sp["index"]), ("splatting", 1))
        self.assertTrue(sp["fields"]["input_depth_maps"].endswith("scene00001000_depth.mp4"))
        self.assertTrue(sp["fields"]["input_source_clips"].endswith("scene00001000.mp4"))
        self.assertIn("M Splats", sp["fields"]["output_splatted"])
        dc = M.send_plan(miss["scene00001200"])
        self.assertEqual((dc["stage"], dc["index"]), ("depthcrafter", 0))
        self.assertTrue(dc["source"].endswith("scene00001200.mp4"))
        self.assertIn("M DepthMaps", dc["output"])

    def probes(self, info, override=True):
        paths = [f for s in info["scenes"] for f in s["files"]]
        if override:
            paths += [s["source"] for s in info["scenes"] if not s["kind"]]
        return M.probe_all(paths)

    def build(self, info, override=True, **opts):
        plan = M.plan_build(info, self.probes(info, override), override)
        self.assertNotIn("error", plan, plan.get("error"))
        out = tempfile.mkdtemp(dir=self.tmp)
        o = {**M.options_from({}), "codec": "H.264", "encoder": "Force CPU", "quality": "Fastest", "crf": "30",
             "output_folder": out, **opts}
        res = M.Build(info, plan, o).run()
        self.assertNotIn("error", res, res.get("error"))
        self.assertEqual(sorted(os.listdir(out)), sorted(os.path.basename(p) for p in res["outputs"]),
                         "only the finished file(s): no .part, no temp folder")
        return plan, res

    def test_missing_without_override_is_refused(self):
        plan = M.plan_build(self.info, self.probes(self.info, False), False)
        self.assertIn("error", plan)

    def test_concat_way_full_sbs_with_black_for_missing(self):
        plan, res = self.build(self.info)
        self.assertEqual(plan["way"], "concat")                  # one DNxHR format throughout
        self.assertEqual(plan["frames"], 5 + 6 + 4 + 3 + 4)
        info = M.probe(res["outputs"][0])
        self.assertEqual((info["w"], info["h"]), (2 * EW, EH))
        self.assertEqual(info["frames"], 22)
        self.assertTrue(res["outputs"][0].endswith("Movie - 3D Full SBS.mkv"))
        # frame 13 (0-based) is inside the black clip for scene00001000 (frames 11-14); frame 0 is the red | blue pair
        frames = self.frames_at(res["outputs"][0], (0, 13))
        self.assertGreater(frames[0][:, : EW, 0].mean(), 150)      # left eye red
        self.assertGreater(frames[0][:, EW:, 2].mean(), 150)       # right eye blue
        self.assertLess(frames[1].mean(), 25)                      # black

    def test_pipe_way_for_long_gop_and_mixed_kinds(self):
        info = M.scan(self.p)
        # one chapter's scene as a Full SBS in H.264 instead: mixed kinds and codecs -> pipe
        s = next(x for x in info["scenes"] if x["stem"] == "scene00002000")
        sbs = os.path.join(self.tmp, "sbs", "scene00002000_256_merged_full_sbs.mp4")
        _clip(sbs, 4, w=2 * EW, h=EH)
        s.update({"kind": "sbs", "files": [sbs.replace("\\", "/")]})
        plan, res = self.build(info, layout="L and R files")
        self.assertEqual(plan["way"], "pipe")
        self.assertEqual(len(res["outputs"]), 2)
        for path in res["outputs"]:
            i = M.probe(path)
            self.assertEqual((i["w"], i["h"], i["frames"]), (EW, EH, 22))
        left = self.frames_at(res["outputs"][0], (0,))[0]
        self.assertGreater(left[..., 0].mean(), 150)               # the L file is the left eye (red)

    def test_a_scene_with_no_frame_count_is_refused(self):
        """Bug sweep 2026-10-14c: it was dropped silently (the sound drifted after it)."""
        probes = self.probes(self.info)
        first = next(s for s in self.info["scenes"] if s["kind"])
        probes[first["files"][0]] = dict(probes[first["files"][0]], frames=0)
        plan = M.plan_build(self.info, probes, True)
        self.assertIn("Can't tell how many frames", plan.get("error", ""))

    def test_a_scene_that_decodes_short_is_said(self):
        """Bug sweep 2026-10-14c: black-filled with only a log line; the toast said all was well."""
        info = M.scan(self.p)
        s = next(x for x in info["scenes"] if x["stem"] == "scene00002000")
        sbs = os.path.join(self.tmp, "sbs2", "scene00002000_256_merged_full_sbs.mp4")
        _clip(sbs, 4, w=2 * EW, h=EH)
        s.update({"kind": "sbs", "files": [sbs.replace("\\", "/")]})          # mixed: the pipe way
        plan = M.plan_build(info, self.probes(info), True)
        piece = next(p for p in plan["pieces"] if p["stem"] == "scene00002000")
        piece["frames"] += 2                                                 # it says more than it has
        plan["frames"] += 2
        out = tempfile.mkdtemp(dir=self.tmp)
        o = {**M.options_from({}), "codec": "H.264", "encoder": "Force CPU", "quality": "Fastest", "crf": "30",
             "output_folder": out}
        res = M.Build(info, plan, o).run()
        self.assertEqual(res.get("short"), ["scene00002000 (2 of 6 frames)"], res)

    def test_a_quits_leftovers_are_swept_once_old(self):
        """Bug sweep 2026-10-14c: a quit mid-run left the .part and the black-clip folder (GBs)."""
        import time
        out = tempfile.mkdtemp(dir=self.tmp)
        old = time.time() - M.SWEEP_AGE_S - 60
        left = os.path.join(out, "_mergeall_abc")
        os.makedirs(left)
        part = os.path.join(out, "Movie - 3D Full SBS.mkv.part")
        open(part, "wb").close()
        fresh = os.path.join(out, "_mergeall_live")
        os.makedirs(fresh)
        for p in (left, part):
            os.utime(p, (old, old))
        M._sweep(out)
        self.assertFalse(os.path.exists(left) or os.path.exists(part))
        self.assertTrue(os.path.exists(fresh), "a fresh one (maybe a live run's) stays")

    def test_cross_eye_layout(self):
        """Tester 2026-11-09: a cross-eye Merging came out parallel -- Merge All had no cross-eye layout."""
        _plan, res = self.build(self.info, layout="Full SBS Cross-eye")
        self.assertTrue(res["outputs"][0].endswith("Movie - 3D Full SBS Cross-eye.mkv"))
        i = M.probe(res["outputs"][0])
        self.assertEqual((i["w"], i["h"]), (2 * EW, EH))
        f = self.frames_at(res["outputs"][0], (0,))[0]
        self.assertGreater(f[:, : EW, 2].mean(), 150)              # the right eye (blue) on the left
        self.assertGreater(f[:, EW:, 0].mean(), 150)               # the left eye (red) on the right

    def test_layout_follows_merging_output_format(self):
        """User 2026-11-09: Merge All joins in Merging's Output Format, no layout of its own."""
        for fmt, layout in (("Full SBS (Left-Right)", "Full SBS"), ("Full SBS Cross-eye (Right-Left)", "Full SBS Cross-eye"),
                            ("Double SBS", "Double SBS"), ("Half SBS (Left-Right)", "Half SBS"),
                            ("Split L/R Output", "L and R files"), ("Anaglyph (Red/Cyan)", "Anaglyph"),
                            ("Anaglyph Half-Color", "Anaglyph Half-Color"), ("Right-Eye Only", "Right Eye")):
            self.assertEqual(M.options_from({"output_format": fmt, "mergeall_layout": "Half SBS"})["layout"], layout, fmt)
        self.assertEqual(M.options_from({})["layout"], "Full SBS")
        from studio.stages.merging import settings as S
        self.assertEqual(set(M.LAYOUT_OF), set(S.OUTPUT_FORMATS), "every Output Format has a layout")

    def test_double_anaglyph_and_right_eye_layouts(self):
        _plan, res = self.build(self.info, layout="Double SBS")
        i = M.probe(res["outputs"][0])
        self.assertEqual((i["w"], i["h"]), (2 * EW, 2 * EH))
        _plan, res = self.build(self.info, layout="Right Eye")
        i = M.probe(res["outputs"][0])
        self.assertEqual((i["w"], i["h"]), (EW, EH))
        f = self.frames_at(res["outputs"][0], (0,))[0]
        self.assertGreater(f[..., 2].mean(), 150)                  # the right eye (blue)
        _plan, res = self.build(self.info, layout="Anaglyph")
        self.assertTrue(res["outputs"][0].endswith("Movie - 3D Anaglyph.mkv"))
        i = M.probe(res["outputs"][0])
        self.assertEqual((i["w"], i["h"]), (EW, EH))
        f = self.frames_at(res["outputs"][0], (0,))[0]
        self.assertGreater(f[..., 0].mean(), 150)                  # red from the left eye (red)
        self.assertGreater(f[..., 2].mean(), 150)                  # blue from the right eye (blue)

    def test_half_sbs_layout(self):
        _plan, res = self.build(self.info, layout="Half SBS")
        i = M.probe(res["outputs"][0])
        self.assertEqual((i["w"], i["h"]), (EW, EH))

    def test_stop_discards_the_unfinished_movie(self):
        import threading
        import time
        for way in ("concat", "pipe"):
            plan = M.plan_build(self.info, self.probes(self.info), True)
            plan["way"] = way
            for piece in plan["pieces"]:
                if piece["kind"] == "black":
                    piece["frames"] = 20000            # long enough to stop part-way
            plan["frames"] = sum(p["frames"] for p in plan["pieces"])
            out = tempfile.mkdtemp(dir=self.tmp)
            o = {**M.options_from({}), "codec": "H.264", "encoder": "Force CPU", "quality": "Fastest",
                 "output_folder": out}
            build = M.Build(self.info, plan, o)
            got = {}
            t = threading.Thread(target=lambda: got.update(build.run()))
            t.start()
            time.sleep(1.5)
            build.stop()
            t.join(60)
            self.assertFalse(t.is_alive(), way)
            self.assertTrue(got.get("stopped"), (way, got))
            self.assertEqual(os.listdir(out), [], f"{way}: nothing left -- no .part, no temp folder")

    @staticmethod
    def frames_at(path, idxs):
        import numpy as np
        i = M.probe(path)
        w, h = i["w"], i["h"]
        raw = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", path, "-f", "rawvideo",
                              "-pix_fmt", "rgb24", "pipe:1"], capture_output=True, check=True).stdout
        all_ = np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3)
        return [all_[k] for k in idxs]



class PlainFolderTests(unittest.TestCase):
    """No project open (a tester, 2026-11-03: Merge All asked for a Source Clips folder, and
    one made by hand didn't help): the merged clips in Merging's Output folder, in scene-number
    order, built into one movie; a scene with only one eye's file is left out and named."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        f = cls.final = os.path.join(cls.tmp, "final")
        _clip(os.path.join(f, "film-Scene-0010_256_merged_full_sbs.mp4"), 3, w=2 * EW)
        _clip(os.path.join(f, "film-Scene-0002_256_merged_full_sbs.mp4"), 4, w=2 * EW, colour="green")
        for eye, colour in (("L", "red"), ("R", "blue")):
            _clip(os.path.join(f, f"film-Scene-0001_256_merged_full_sbs_{eye}.mp4"), 5, colour=colour)
        _clip(os.path.join(f, "film-Scene-0005_256_merged_full_sbs_L.mp4"), 2)          # one eye only
        _clip(os.path.join(f, "film-Scene-0003.mp4"), 2)                                  # not a merged clip

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_the_folders_merged_clips_in_scene_order(self):
        info = M.scan_folder(self.final)
        self.assertNotIn("error", info, info.get("error"))
        self.assertTrue(info["plain"])
        self.assertEqual([s["stem"] for s in info["scenes"]], ["film-Scene-0001", "film-Scene-0002", "film-Scene-0010"])
        self.assertEqual([s["kind"] for s in info["scenes"]], ["lr", "sbs", "sbs"])
        self.assertEqual((info["missing"], info["total"], info["name"]), ([], 3, "final"))
        self.assertEqual(len(info["skipped"]), 1)
        self.assertIn("film-Scene-0005", info["skipped"][0])

    def test_built_into_one_movie(self):
        info = M.scan_folder(self.final)
        plan = M.plan_build(info, M.probe_all([f for s in info["scenes"] for f in s["files"]]), False)
        self.assertNotIn("error", plan, plan.get("error"))
        out = tempfile.mkdtemp(dir=self.tmp)
        o = {**M.options_from({}), "codec": "H.264", "encoder": "Force CPU", "quality": "Fastest", "crf": "30",
             "output_folder": out}
        res = M.Build(info, plan, o).run()
        self.assertNotIn("error", res, res.get("error"))
        self.assertTrue(res["outputs"][0].endswith("final - 3D Full SBS.mkv"), res["outputs"])
        got = M.probe(res["outputs"][0])
        self.assertEqual((got["w"], got["h"], got["frames"]), (2 * EW, EH, 5 + 4 + 3))
        first = MergeAllTests.frames_at(res["outputs"][0], (0,))[0]
        self.assertGreater(first[:, : EW, 0].mean(), 150)        # scene 1 first: its left eye red
        self.assertGreater(first[:, EW:, 2].mean(), 150)

    def test_an_empty_or_missing_folder_says_so(self):
        empty = tempfile.mkdtemp(dir=self.tmp)
        self.assertIn("there are none", M.scan_folder(empty)["error"])
        self.assertIn("isn't there", M.scan_folder(os.path.join(self.tmp, "nope"))["error"])

    def test_the_button_scans_the_output_folder_without_a_project(self):
        import threading
        from types import SimpleNamespace
        from studio.stages.merging.controller import MergingController
        got, done = [], threading.Event()

        def emit(info):
            got.append(info)
            done.set()
        for project, base in (("", ""), ("C:/Projects/Movie", "")):
            got.clear(); done.clear()
            fake = SimpleNamespace(_busy=False, is_busy=lambda: False, toast=SimpleNamespace(emit=lambda *a: None),
                                   _merge_base=lambda: base, _shell=SimpleNamespace(projectFolder=project),
                                   _values=lambda: {"output_folder": self.final},
                                   mergeAllScanned=SimpleNamespace(emit=emit), _sigMergeScanned=SimpleNamespace(emit=emit))
            MergingController.mergeAllScan(fake)
            self.assertTrue(done.wait(30))
            if project:          # a show with no chapter picked: still "which episode?"
                self.assertIn("Which episode", got[0].get("error", ""))
            else:
                self.assertTrue(got[0].get("plain"), got[0])
                self.assertEqual(got[0]["total"], 3)


if __name__ == "__main__":
    unittest.main()
