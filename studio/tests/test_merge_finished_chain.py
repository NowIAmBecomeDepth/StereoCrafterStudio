"""Merging finds a clip's files at any level of its "finished" chain, and takes each clip's newest inpaint
(tester carlobs, 2026-11-18).

1. "After inpainting the Source clip still not moved into finished folder ... so not appear in Merge Tab": a
   project gives Merging <Source>/finished/finished for originals and <Splats>/hires/finished for splats -- but
   only if those folders exist when the stage is opened (resolve_subfolder), and a clip rendered on its own
   (Splatting's single clip, Inpainting without its hi-res field) stays a level up. Every lookup was in exactly
   the one folder, so such a clip had "no original" or wasn't listed at all. Now each looks through the chain:
   the folder, the levels above it, then the levels below.
2. "After Inpainting with Hi-Res Blend, if the inpainting folder contain a Clip without Hi-Res Blend, are founded
   2 inpainted Clips (hi and low) in the Merge Tab, instead only the last created": both inpaints of one clip
   (their names differ only by width) were listed and merged; now only the newest is.

    python -m unittest studio.tests.test_merge_finished_chain
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first


def touch(path: Path, age: float = 0.0) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    if age:
        t = time.time() - age
        os.utime(path, (t, t))
    return str(path)


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp(prefix="sc_chain_"))
        self.addCleanup(shutil.rmtree, self.d, True)
        self.src = self.d / "Source" / "finished" / "finished"      # Merging's Original folder in a project
        self.hires = self.d / "Splats" / "hires"
        self.inp = self.d / "Inpainted"


class OriginalTest(_Tmp):
    def test_an_original_a_level_up_or_down_is_found(self):
        from core.common.sidecar_manager import find_video_by_core_name
        self.src.mkdir(parents=True)
        up = touch(self.d / "Source" / "finished" / "LC_V2-0412.mp4")      # Splatting's single clip: not moved
        self.assertEqual(os.path.normcase(find_video_by_core_name(str(self.src), "LC_V2-0412") or ""), os.path.normcase(up))
        down = touch(self.src / "finished" / "LC_V2-0413.mp4")            # merged once already
        self.assertEqual(os.path.normcase(find_video_by_core_name(str(self.src), "LC_V2-0413") or ""), os.path.normcase(down))
        here = touch(self.src / "LC_V2-0412.mp4")                         # the folder's own comes first
        self.assertEqual(os.path.normcase(find_video_by_core_name(str(self.src), "LC_V2-0412") or ""), os.path.normcase(here))
        self.assertIsNone(find_video_by_core_name(str(self.src), "LC_V2-0499"))

    def test_the_preflight_finds_it_too(self):
        from core.common import merge_preflight as M
        touch(self.d / "Source" / "finished" / "LC_V2-0412.mp4")
        self.src.mkdir(parents=True)
        touch(self.hires / "finished" / "LC_V2-0412_1920_splatted2.mp4")
        inp = touch(self.inp / "LC_V2-0412_1920_inpainted_right_eye.mp4")
        self.assertEqual(M.missing_originals([inp], str(self.hires / "finished"), str(self.src)), [])

    def test_the_splat_a_level_up_still_needs_its_original(self):
        from core.common import merge_preflight as M
        touch(self.hires / "LC_V2-0412_1920_splatted2.mp4")               # left in hires/, Merging reads hires/finished
        (self.hires / "finished").mkdir()
        self.assertTrue(M.needs_original("LC_V2-0412_1920_inpainted_right_eye.mp4", "LC_V2-0412",
                                         str(self.hires / "finished")))


class MergeListTest(_Tmp):
    def values(self, mask):
        return {"inpainted_folder": str(self.inp), "mask_folder": str(mask), "original_folder": str(self.src)}

    def test_listed_with_its_splat_a_level_up_or_down(self):
        from studio.clip_queue import merge_clips
        a = touch(self.inp / "LC_V2-0412_1920_inpainted_right_eye.mp4")
        b = touch(self.inp / "LC_V2-0413_1920_inpainted_right_eye.mp4")
        touch(self.hires / "LC_V2-0412_1920_splatted2.mp4")              # not moved (no hi-res field)
        touch(self.hires / "finished" / "LC_V2-0413_1920_splatted2.mp4")  # moved, Merging set before it existed
        for mask in (self.hires, self.hires / "finished"):
            self.assertEqual(merge_clips(self.values(mask)), [a, b], str(mask))

    def test_only_each_clips_newest_inpaint(self):
        from studio.clip_queue import merge_clips
        touch(self.hires / "finished" / "LC_V2-0412_1920_splatted2.mp4")
        touch(self.hires / "finished" / "LC_V2-0412_640_splatted2.mp4")
        old = touch(self.inp / "LC_V2-0412_640_inpainted_right_eye.mp4", age=3600)    # low-res only, before
        new = touch(self.inp / "LC_V2-0412_1920_inpainted_right_eye.mp4")             # with Hi-Res Blend, now
        other = touch(self.inp / "LC_V2-0414_640_inpainted_right_eye.mp4")
        touch(self.hires / "finished" / "LC_V2-0414_640_splatted2.mp4")
        self.assertEqual(merge_clips(self.values(self.hires / "finished")), [new, other])
        os.utime(old, None)                                               # the low-res one made again, later
        t = time.time() + 5
        os.utime(old, (t, t))
        self.assertEqual(merge_clips(self.values(self.hires / "finished")), [old, other])


class EngineTest(unittest.TestCase):
    def test_the_merge_takes_the_same_clips_and_splats(self):
        import inspect
        from studio.legacy import install
        install()
        from studio.stages.merging.engine import MergingEngine
        src = inspect.getsource(MergingEngine.run_batch_process)
        self.assertIn("newest_per_core(inpainted_videos", src, "each clip's newest inpaint only")
        self.assertIn("dir_holding(", src, "the splat wherever its chain has it")


if __name__ == "__main__":
    unittest.main()
