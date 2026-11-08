"""Frame Break reaches the merge (user, 2026-11-03: goblinbridgeattack00149667 -- bars 0.226 saved in its
depth map's sidecar -- merged without them).

Three faults, each a test here: Merging read Frame Break only from the Inpainted folder's sidecar (which
held grain_amount alone, and whose loader drops Frame Break's keys anyway); the hi-res splat was moved to
finished/ and its _framebreak.mkv mask left behind; Inpainting's .mkv glob took the low-res mask for a
splat and inpainted it. Temp folders only."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first

from core.common import sidecar_manager as SM  # noqa: E402

CLIP = "goblinbridgeattack00149667"


def _write(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb" if isinstance(data, bytes) else "w", **({} if isinstance(data, bytes) else {"encoding": "utf-8"})) as f:
        f.write(data)


class DepthSidecarTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.depth = os.path.join(self.tmp, "SM DepthMaps", "Chapter 35")
        self.inpainted = os.path.join(self.tmp, "SM Inpainted", "Chapter 35")
        # as on the user's drive: the map retired twice, the sidecar with it
        _write(os.path.join(self.depth, "finished", "finished", CLIP + "_depth.fssidecar"),
               json.dumps({"frame_break_bars": 0.226, "frame_break_thresh": 0.8, "convergence_plane": 0.5}))
        # Merging's own grain match wrote this one, seconds before the merge
        _write(os.path.join(self.inpainted, CLIP + ".fssidecar"), json.dumps({"grain_amount": 0.4}))
        self.sm = SM.SidecarConfigManager()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_merging_takes_frame_break_from_the_depth_sidecar(self):
        d = SM.read_clip_sidecar(self.sm, os.path.join(self.inpainted, CLIP + "_1920_inpainted_right_eye.mp4"), CLIP,
                                 [self.inpainted], depth_folders=[self.depth])
        self.assertAlmostEqual(d.get("frame_break_bars", 0.0), 0.226)
        self.assertAlmostEqual(d.get("frame_break_thresh", 1.0), 0.8)
        self.assertAlmostEqual(d.get("grain_amount"), 0.4, msg="the clip's own sidecar still counts")

    def test_the_clips_own_frame_break_wins_and_isnt_dropped(self):
        _write(os.path.join(self.inpainted, CLIP + ".fssidecar"), json.dumps({"frame_break_bars": 0.1}))
        d = SM.read_clip_sidecar(self.sm, "x.mp4", CLIP, [self.inpainted], depth_folders=[self.depth])
        self.assertAlmostEqual(d.get("frame_break_bars", 0.0), 0.1)

    def test_no_depth_folder_no_frame_break(self):
        d = SM.read_clip_sidecar(self.sm, "x.mp4", CLIP, [self.inpainted])
        self.assertFalse(d.get("frame_break_bars"))

    def test_the_engine_hands_its_depth_folders_on(self):
        """the generated Merging engine's _read_clip_sidecar passes depth_sidecar_folders (gen PATCHES)"""
        from studio.stages.merging import engine as ME

        class Var:
            def __init__(self, v):
                self.v = v

            def get(self):
                return self.v

        class Fake:
            inpainted_folder_var, original_folder_var = Var(self.inpainted), Var("")
            sidecar_manager = self.sm
            depth_sidecar_folders = [self.depth]
        d = ME.MergingEngine._read_clip_sidecar(Fake(), "x.mp4", CLIP)
        self.assertAlmostEqual(d.get("frame_break_bars", 0.0), 0.226)


class MaskTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.hires = os.path.join(self.tmp, "hires")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_merging_finds_the_mask_left_above_finished(self):
        from core.common.video_io import find_frame_break_mask
        splat = os.path.join(self.hires, "finished", CLIP + "_1920_splatted2.mov")
        mask = os.path.join(self.hires, CLIP + "_1920_splatted2_framebreak.mkv")
        _write(splat)
        _write(mask)
        self.assertEqual(os.path.normcase(find_frame_break_mask(splat)), os.path.normcase(mask))

    def test_the_mask_moves_with_its_splat(self):
        from core.common.file_organizer import move_single_file
        splat = os.path.join(self.hires, CLIP + "_1920_splatted2.mov")
        mask = os.path.join(self.hires, CLIP + "_1920_splatted2_framebreak.mkv")
        _write(splat)
        _write(mask)
        ok, _err = move_single_file(splat, self.hires)
        self.assertTrue(ok)
        self.assertTrue(os.path.isfile(os.path.join(self.hires, "finished", os.path.basename(mask))))
        self.assertFalse(os.path.exists(mask))

    def test_inpainting_lists_no_mask(self):
        from studio import clip_queue as Q
        low = os.path.join(self.tmp, "lowres")
        _write(os.path.join(low, CLIP + "_1408_splatted2.mkv"))
        _write(os.path.join(low, CLIP + "_1408_splatted2_framebreak.mkv"))
        for fn in (Q.inpaint_clips, Q.inpaint_v2_clips):
            got = [os.path.basename(p) for p in fn({"input_folder": low})]
            self.assertEqual(got, [CLIP + "_1408_splatted2.mkv"], fn.__name__)
        from core.common.video_io import is_frame_break_mask
        self.assertTrue(is_frame_break_mask(CLIP + "_1408_splatted2_framebreak.mkv"))
        self.assertFalse(is_frame_break_mask(CLIP + "_1408_splatted2.mkv"))


if __name__ == "__main__":
    unittest.main()
