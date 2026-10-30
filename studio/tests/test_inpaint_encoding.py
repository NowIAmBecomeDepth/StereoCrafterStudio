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


class RawFrameEncoderTest(unittest.TestCase):
    """Inpainting V1's Hi-Res frames straight to ffmpeg (a user's patch, 2026-10-29): not written as 16-bit
    PNGs and read back. The same command but its input, and the same video out."""

    @classmethod
    def setUpClass(cls):
        import numpy as np
        cls.tmp = tempfile.mkdtemp()
        rng = np.random.default_rng(2)
        ramp = np.linspace(0, 65535, 320)[None, :, None] * np.ones((176, 1, 3))
        cls.frames = [np.clip(ramp + rng.normal(0, 4000, ramp.shape) + i * 900, 0, 65535).astype(np.uint16)
                      for i in range(8)]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def decoded(self, path):
        out = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "rawvideo", "-"], capture_output=True)
        self.assertEqual(out.returncode, 0)
        return out.stdout

    def both(self, name, options):
        import cv2
        from core.common.video_io import RawFrameEncoder, encode_frames_to_mp4
        info = {"pix_fmt": "yuv420p", "codec_name": "h264"}
        d = tempfile.mkdtemp(dir=self.tmp)
        for i, f in enumerate(self.frames):
            cv2.imwrite(os.path.join(d, f"{i:05d}.png"), cv2.cvtColor(f, cv2.COLOR_RGB2BGR))
        png = os.path.join(self.tmp, "png_" + name)
        self.assertTrue(encode_frames_to_mp4(d, png, 24, len(self.frames), info, user_output_crf=23,
                                             encoding_options=options))
        raw = os.path.join(self.tmp, "raw_" + name)
        enc = RawFrameEncoder(320, 176, raw, 24, info, 23, options)
        for f in self.frames:
            enc.write(f)
        self.assertTrue(enc.finish())
        self.assertFalse(os.path.exists(enc.encode_path), "the .part was left")
        return png, raw

    def test_the_same_video_as_the_png_route(self):
        cases = [("x264.mp4", {"codec": "H.264", "encoder": "Force CPU", "encoding_quality": "Fast"}),
                 ("x265_10.mp4", {**TEN, "encoder": "Force CPU"})]
        if nvenc_available():
            cases.append(("nvenc.mp4", {"codec": "H.264", "encoder": "NVENC", "encoding_quality": "Fast"}))
        for name, options in cases:
            png, raw = self.both(name, options)
            self.assertEqual(probe(png), probe(raw), name)
            self.assertEqual(self.decoded(png), self.decoded(raw), f"{name}: the frames differ")

    def test_the_same_command_but_the_input(self):
        from core.common.video_io import build_encode_cmd
        opts = {**TEN, "encoder": "Force CPU"}
        png, _ = build_encode_cmd(["-framerate", "24", "-i", "x/%05d.png"], "out.mp4", None, 23, opts)
        raw, _ = build_encode_cmd(["-f", "rawvideo", "-pix_fmt", "rgb48le", "-s", "320x176", "-framerate", "24",
                                   "-i", "pipe:0"], "out.mp4", None, 23, opts)
        self.assertEqual(png[:5], ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"])
        self.assertEqual(png[png.index("x/%05d.png") + 1:], raw[raw.index("pipe:0") + 1:])

    def test_abort_leaves_nothing(self):
        from core.common.video_io import RawFrameEncoder
        out = os.path.join(self.tmp, "stopped.mp4")
        enc = RawFrameEncoder(320, 176, out, 24, None, 23, {"codec": "H.264", "encoder": "Force CPU"})
        enc.write(self.frames[0])
        enc.abort()
        self.assertFalse(os.path.exists(out) or os.path.exists(enc.encode_path))
        enc = RawFrameEncoder(320, 176, out, 24)
        try:
            with self.assertRaises(ValueError):
                enc.write(self.frames[0][:, :100])            # a frame of the wrong size
        finally:
            enc.abort()


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
