"""Encoding Settings' Container (user, 2026-10-25: "a dropdown to choose mp4 or mkv when dnxhr isnt
selected"): the outputs take it, DNxHR still writes .mov, and the next stage's lookups find an .mkv. Also
"no where at all ... any dnxhr defaults on": the 8-bit / 10-bit presets leave Enable DNxHR off.

    python -m unittest studio.tests.test_output_container
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.common import encoding_utils as EU  # noqa: E402


def _format_of(path: str) -> str:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=format_name", "-of", "json", path],
                         capture_output=True, text=True)
    return (json.loads(out.stdout or "{}").get("format") or {}).get("format_name", "")


class ContainerTest(unittest.TestCase):
    def test_the_extension_follows_the_container_dnxhr_wins(self):
        self.assertEqual(EU.output_ext({}), ".mp4")
        self.assertEqual(EU.output_ext({"output_container": "MKV"}), ".mkv")
        self.assertEqual(EU.output_ext({"output_container": "mkv"}), ".mkv")
        self.assertEqual(EU.output_ext({"output_container": "MP4"}), ".mp4")
        self.assertEqual(EU.output_ext({"output_container": "MKV", "dnxhr_fullres_split": True}), ".mov")
        self.assertEqual(EU.video_args_from_settings({"output_container": "MKV"})[1], ".mkv")
        self.assertEqual(EU.OUTPUT_CONTAINERS, ("MP4", "MKV"))

    def test_every_stage_offers_it_mp4_by_default(self):
        from studio.stages.inpainting import settings as IS
        from studio.stages.merging import settings as MS
        from studio.stages.splatting import settings as SS
        for mod in (IS, MS, SS):
            self.assertEqual(mod.DEFAULTS.get("output_container"), "MP4", mod.__name__)
            # kept as chosen through the stage's own clean-up (Merging's filed it as a yes/no once)
            self.assertEqual(mod.normalise(dict(mod.DEFAULTS, output_container="MKV"))["output_container"], "MKV",
                             mod.__name__)
        self.assertIn("output_container", IS.ENCODING_KEYS, "Inpainting V2 takes V1's encoding")


class NoDnxhrByDefaultTest(unittest.TestCase):
    def test_the_presets_leave_dnxhr_off(self):
        from core.ui.encoding_settings import ENCODING_PRESETS
        for name, preset in ENCODING_PRESETS.items():
            self.assertIs(preset.get("dnxhr_fullres_split"), False, name)

    def test_no_stage_defaults_to_dnxhr(self):
        from core.splatting.config_manager import SPLATTER_DEFAULT_CONFIG
        from studio.stages.inpainting import settings as IS
        from studio.stages.merging import settings as MS
        from studio.stages.splatting import settings as SS
        for d in (IS.DEFAULTS, MS.DEFAULTS, SS.DEFAULTS, SPLATTER_DEFAULT_CONFIG):
            self.assertIs(d.get("dnxhr_fullres_split"), False)
        self.assertEqual(MS.DEFAULTS.get("mergeall_codec"), "H.265")


class MkvInputsFoundTest(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_inpainting_lists_and_matches_mkv_splats(self):
        from studio import clip_queue as Q
        lo, hi = self.d / "lo", self.d / "hi"
        lo.mkdir(); hi.mkdir()
        (lo / "shot_640_splatted2.mkv").write_bytes(b"x")
        (lo / "other_640_splatted2.mp4").write_bytes(b"x")
        (hi / "shot_1920_splatted2.mkv").write_bytes(b"x")
        listed = [os.path.basename(p) for p in Q.inpaint_clips({"input_folder": str(lo)})]
        self.assertEqual(sorted(listed), ["other_640_splatted2.mp4", "shot_640_splatted2.mkv"])
        self.assertEqual(os.path.basename(Q.inpaint_hires_of(str(lo / "shot_640_splatted2.mkv"), str(hi))),
                         "shot_1920_splatted2.mkv")

    def test_merging_preflight_sees_an_mkv_splat(self):
        from core.common import merge_preflight as MP
        (self.d / "clip_320_splatted2.mkv").write_bytes(b"x")
        self.assertTrue(MP.needs_original("clip_320_inpainted_right_eye.mkv", "clip", str(self.d)))
        (self.d / "quad_320_splatted4.mkv").write_bytes(b"x")
        self.assertFalse(MP.needs_original("quad_320_inpainted_sbs.mkv", "quad", str(self.d)))


@unittest.skipUnless(shutil.which("ffprobe"), "needs ffprobe")
class V2WritesMkvTest(unittest.TestCase):
    """Inpainting V2's streaming writer: the Container's .mkv is a Matroska file (its muxer)."""

    def test_an_mkv_output_is_matroska(self):
        os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")
        from studio.stages.inpainting_v2 import engine as V2
        d = Path(tempfile.mkdtemp())
        try:
            V2.set_output_encoding({"codec": "H.264", "encoding_encoder": "Force CPU", "output_crf": 23,
                                    "output_container": "MKV"})
            path = str(d / f"clip_sbs{V2._V2_VIDEO['ext']}")
            self.assertTrue(path.endswith(".mkv"), path)
            w = V2.StreamingVideoWriter(path, 24, 64, 48)
            w.append(np.zeros((3, 48, 64, 3), np.uint8))
            w.close()
            self.assertIn("matroska", _format_of(path))
        finally:
            V2.set_output_encoding(None)
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
