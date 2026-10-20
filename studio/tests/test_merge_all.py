"""Merging's Merge All SBS to 1 Video (user, 2026-10-11; studio/merge_all.py): the
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


if __name__ == "__main__":
    unittest.main()
