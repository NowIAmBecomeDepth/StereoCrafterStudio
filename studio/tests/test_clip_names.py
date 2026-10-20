"""A clip's own files, not a longer clip's (bug sweep 2026-10-14c): "<clip>_*" searches took clip
"shot_02"'s splat, depth or hi-res for clip "shot" -- Move to Finished moved the other clip's
splat, Play played it, Inpainting read its hi-res, Send to took its depth map."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.common.clip_names import is_own_file  # noqa: E402
from studio import clip_queue as Q, send_on  # noqa: E402


def touch(folder, name):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, name), "wb") as f:
        f.write(b"x")
    return os.path.join(folder, name)


class ClipNamesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_own_files_and_a_longer_clips(self):
        for name in ("shot.mp4", "shot_depth.mp4", "shot_1920_splatted2.mp4", "shot_1920_splatted4F.mov",
                     "shot_1920_inpainted_right_eye.mp4", "shot_1920_merged_full_sbs.mov", "SHOT_1920_Splatted2.mp4"):
            self.assertTrue(is_own_file("shot", name), name)
        for name in ("shot_02_1920_splatted2.mp4", "shot_02_depth.mp4", "shot_b_1920_splatted2.mp4",
                     "shot2_1920_splatted2.mp4", "shotgun.mp4", "other_shot_depth.mp4"):
            self.assertFalse(is_own_file("shot", name), name)

    def test_send_on_takes_the_clips_own_depth_map(self):
        d = os.path.join(self.tmp, "depth")
        touch(d, "shot_02_depth.mp4")
        self.assertEqual(send_on.made_from(d, "shot", "depth"), "", "not shot_02's")
        own = touch(d, "shot_depth.mp4")
        self.assertEqual(os.path.normcase(send_on.made_from(d, "shot", "depth")), os.path.normcase(own))

    def test_inpaintings_hires_is_the_clips_own(self):
        hi = os.path.join(self.tmp, "hires")
        touch(hi, "shot_02_1920_splatted4.mp4")
        low = touch(os.path.join(self.tmp, "lowres"), "shot_960_splatted4.mp4")
        self.assertEqual(Q.inpaint_hires_of(low, hi), "", "not shot_02's hi-res")
        own = touch(hi, "shot_1920_splatted4.mp4")
        self.assertEqual(os.path.normcase(Q.inpaint_hires_of(low, hi)), os.path.normcase(own))

    def test_merging_play_splat_is_the_clips_own(self):
        masks = os.path.join(self.tmp, "masks")
        touch(masks, "shot_02_1920_splatted2.mp4")
        inp = touch(os.path.join(self.tmp, "inpainted"), "shot_1920_inpainted_right_eye.mp4")
        plays = Q.merge_related({"mask_folder": masks, "original_folder": ""}, inp)
        self.assertFalse([p for t, p in plays if t == "Play Splat"], plays)

    def test_the_inpainting_engines_hires_match_is_the_clips_own(self):
        """The generated engine's own match (gen_inpainting_engine PATCHES)."""
        from studio.stages.inpainting import engine as IE
        hi = os.path.join(self.tmp, "hires")
        other = touch(hi, "shot_02_1920_splatted4.mp4")
        low = touch(os.path.join(self.tmp, "lowres"), "shot_960_splatted4.mp4")

        class Reader:                          # widths by name, no video read
            def __init__(self, path, ctx=None):
                self.w = 1920 if "_1920_" in os.path.basename(path) else 960

            def get_batch(self, idx):
                return SimpleNamespace(shape=(1, 1, self.w, 3))
        fake = SimpleNamespace(_run_path=lambda which: {"input": os.path.dirname(low), "hires": hi}[which])
        with mock.patch.object(IE, "VideoReader", Reader):
            got = IE.InpaintingEngine._find_high_res_match(fake, low) if hasattr(IE, "InpaintingEngine") else \
                next(c for c in vars(IE).values() if isinstance(c, type) and hasattr(c, "_find_high_res_match"))._find_high_res_match(fake, low)
        self.assertNotEqual(os.path.normcase(str(got)), os.path.normcase(other), "not shot_02's hi-res")


if __name__ == "__main__":
    unittest.main()
