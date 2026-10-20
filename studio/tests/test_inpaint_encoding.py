"""Inpainting's Encoding Settings reach the files (user, 2026-10-11): 10-bit (Main10)
writes HEVC Main 10 on NVENC and x265 alike, in V1 and V2 -- V2 always wrote 8-bit
H.264 before; Enable DNxHR, which did nothing in Inpainting, writes DNxHR in a .mov,
and is off by default -- the presets no longer switch it on, and a setting saved
before it worked is set off once. Real encodes of a few frames into a temp folder."""

from __future__ import annotations

import json
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
from core.common.encoding_utils import nvenc_available, video_args_from_settings  # noqa: E402

TEN = {"codec": "H.265", "bit_depth": "10-bit (Main10)", "pix_fmt": "Auto", "encoding_quality": "Fast",
       "output_crf": "23"}
DNX = {"dnxhr_fullres_split": True, "dnxhr_profile": "HQX (10-bit 4:2:2)"}


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=codec_name,profile,pix_fmt", "-of", "json", path], capture_output=True, text=True)
    return (json.loads(out.stdout or "{}").get("streams") or [{}])[0]


class ArgsTest(unittest.TestCase):
    def test_ten_bit_is_main10_on_both_encoders(self):
        a, ext = video_args_from_settings({**TEN, "encoding_encoder": "Force CPU"})
        self.assertEqual(ext, ".mp4")
        self.assertIn("libx265", a)
        self.assertEqual(a[a.index("-pix_fmt") + 1], "yuv420p10le")
        if nvenc_available():
            a, _ = video_args_from_settings({**TEN, "encoding_encoder": "NVENC"})
            self.assertIn("hevc_nvenc", a)
            self.assertEqual((a[a.index("-pix_fmt") + 1], a[a.index("-profile:v") + 1]), ("p010le", "main10"))

    def test_dnxhr_only_when_asked(self):
        a, ext = video_args_from_settings({**TEN, **DNX})
        self.assertEqual((ext, a[1], a[3], a[5]), (".mov", "dnxhd", "dnxhr_hqx", "yuv422p10le"))
        _a, ext = video_args_from_settings({**TEN, "dnxhr_fullres_split": False})
        self.assertEqual(ext, ".mp4")


class EncodeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def v1(self, name, options):
        import numpy as np
        import cv2
        from core.common.video_io import encode_frames_to_mp4
        d = tempfile.mkdtemp(dir=self.tmp)
        ramp = (np.linspace(0, 65535, 320)[None, :, None] * np.ones((176, 1, 3))).astype(np.uint16)
        for i in range(4):
            cv2.imwrite(os.path.join(d, f"{i:05d}.png"), ramp)
        out = os.path.join(self.tmp, name)
        ok = encode_frames_to_mp4(d, out, 24, 4, {"pix_fmt": "yuv420p", "codec_name": "h264"}, user_output_crf=23,
                                  encoding_options=options)
        self.assertTrue(ok, name)
        return probe(out)

    def v2(self, name, settings):
        import numpy as np
        from studio.stages.inpainting_v2 import engine as E
        E.set_output_encoding(settings)
        try:
            out = os.path.join(self.tmp, name + E._V2_VIDEO["ext"])
            w = E.StreamingVideoWriter(out, 24, 320, 176)
            w.append(np.full((4, 176, 320, 3), 128, np.uint8))
            w.close()
        finally:
            E.set_output_encoding(None)
        return out, probe(out)

    def test_v1_ten_bit_x265_and_nvenc(self):
        p = self.v1("x265.mp4", {**TEN, "encoder": "Force CPU"})
        self.assertEqual((p.get("codec_name"), p.get("profile"), p.get("pix_fmt")), ("hevc", "Main 10", "yuv420p10le"))
        if nvenc_available():
            p = self.v1("nvenc.mp4", {**TEN, "encoder": "NVENC"})
            self.assertEqual((p.get("codec_name"), p.get("profile")), ("hevc", "Main 10"))

    def test_v1_dnxhr(self):
        p = self.v1("dnx.mov", {**TEN, "encoder": "NVENC", "dnxhr": True, "dnxhr_profile": "HQX (10-bit 4:2:2)"})
        self.assertEqual((p.get("codec_name"), p.get("pix_fmt")), ("dnxhd", "yuv422p10le"))

    def test_v2_follows_the_settings(self):
        out, p = self.v2("v2_x265", {**TEN, "encoding_encoder": "Force CPU"})
        self.assertTrue(out.endswith(".mp4"))
        self.assertEqual((p.get("codec_name"), p.get("profile"), p.get("pix_fmt")), ("hevc", "Main 10", "yuv420p10le"))
        if nvenc_available():
            _out, p = self.v2("v2_nvenc", {**TEN, "encoding_encoder": "NVENC"})
            self.assertEqual((p.get("codec_name"), p.get("profile")), ("hevc", "Main 10"))
        out, p = self.v2("v2_dnx", {**TEN, **DNX})
        self.assertTrue(out.endswith(".mov"))
        self.assertEqual(p.get("codec_name"), "dnxhd")
        out, p = self.v2("v2_none", None)                 # no settings: the H.264 it always wrote
        self.assertEqual(p.get("codec_name"), "h264")


class DefaultOffTest(unittest.TestCase):
    def test_a_setting_saved_before_it_worked_goes_off_once(self):
        from studio.stages.inpainting import settings as S
        self.assertFalse(S.DEFAULTS["dnxhr_fullres_split"])
        old = S.normalise({"dnxhr_fullres_split": True})                     # saved before 2026-10-11
        self.assertFalse(old["dnxhr_fullres_split"])
        chosen = S.normalise({"dnxhr_fullres_split": True, "inpaint_dnxhr_reviewed": True})
        self.assertTrue(chosen["dnxhr_fullres_split"], "turned on since: kept")

    def test_the_presets_leave_dnxhr_alone(self):
        from studio.stages.inpainting.controller import _encoding_presets
        for name, preset in _encoding_presets().items():
            self.assertFalse([k for k in preset if k.startswith("dnxhr")], name)


if __name__ == "__main__":
    unittest.main()
