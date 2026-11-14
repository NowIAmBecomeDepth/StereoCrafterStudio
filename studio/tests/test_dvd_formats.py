"""test_dvd_formats.py -- DVD's Output Format (core.vda.formats; user, 2026-11-13): each format writes a map that
reads back as the codes it was given, at its own bit depth, and the setting reaches the encode pass."""

import os
import subprocess
import sys
import tempfile
import unittest

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.vda import formats as F  # noqa: E402
from studio.stages.dvd import settings as S  # noqa: E402

W, H, N = 256, 144, 3        # NVENC refuses tiny frames (64x32 failed to open the encoder)


def _codes():
    """(N, H, W) float 10-bit codes: a ramp over the map's range, a different one each frame."""
    ramp = np.linspace(F.CODE_LO, F.CODE_HI, W, dtype=np.float32)
    return torch.from_numpy(np.stack([np.tile(np.roll(ramp, 7 * i), (H, 1)) for i in range(N)]))


def _probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=codec_name,pix_fmt,nb_frames", "-of", "default=nw=1", path],
                         capture_output=True, text=True, check=True).stdout
    return dict(line.split("=", 1) for line in out.split() if "=" in line)


def _luma(path, bits):
    # the Y plane as stored: decoding to gray would stretch tv range to full
    fmt, dt = ("yuv420p10le", "<u2") if bits == 10 else ("yuv420p", np.uint8)
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", fmt, "-"],
                         capture_output=True, check=True).stdout
    frame = H * W + 2 * (H // 2) * (W // 2)
    planes = np.frombuffer(raw, dtype=dt).reshape(-1, frame)
    return planes[:, :H * W].reshape(-1, H, W).astype(np.float32)


def _write(fmt, d):
    final = F.final_path(d, "clip", "_depth", fmt)
    w = F.open_writer(fmt, final, W, H, "24", metadata={"comment": "test"})
    w.write(_codes())
    part = w.close()
    if F.is_png(fmt):
        F.publish_pngs(part, final)
    else:
        os.replace(part, final)
    return final


class Formats(unittest.TestCase):
    def test_menu_and_default(self):
        self.assertEqual(S.DEFAULTS["output_format"], F.LOSSLESS)
        self.assertEqual(S.choices()["output_format"], F.FORMATS)
        self.assertEqual(S.coerce("output_format", "PNG 16-bit"), F.PNG16)
        self.assertEqual(S.coerce("output_format", "nonsense"), F.LOSSLESS)

    def test_lossless_is_exact(self):
        with tempfile.TemporaryDirectory() as d:
            path = _write(F.LOSSLESS, d)
            self.assertTrue(path.endswith("clip_depth.mp4"))
            self.assertEqual(_probe(path)["codec_name"], "hevc")
            got = _luma(path, 10)
            self.assertEqual(len(got), N)
            np.testing.assert_array_equal(got, _codes().round().numpy())

    def test_video_formats(self):
        # (format, codec, bits, max error in that format's codes)
        cases = [(F.HEVC10, "hevc", 10, 4.0), (F.HEVC8, "hevc", 8, 2.0), (F.H264, "h264", 8, 2.0)]
        for fmt, codec, bits, tol in cases:
            with self.subTest(fmt=fmt), tempfile.TemporaryDirectory() as d:
                path = _write(fmt, d)
                p = _probe(path)
                self.assertEqual(p["codec_name"], codec)
                self.assertIn("10" if bits == 10 else "yuv420p", p["pix_fmt"])
                want = _codes().numpy() if bits == 10 else _codes().numpy() / 4.0
                got = _luma(path, bits)
                self.assertEqual(len(got), N)
                self.assertLessEqual(float(np.abs(got - want).max()), tol)

    def test_png_formats(self):
        import cv2
        for fmt, top, dt in ((F.PNG16, 65535, np.uint16), (F.PNG8, 255, np.uint8)):
            with self.subTest(fmt=fmt), tempfile.TemporaryDirectory() as d:
                folder = _write(fmt, d)
                self.assertEqual(folder, os.path.join(d, "clip_depth"))
                files = sorted(os.listdir(folder))
                self.assertEqual(files, [f"clip_depth_{i:06d}.png" for i in range(N)])
                img = cv2.imread(os.path.join(folder, files[0]), cv2.IMREAD_UNCHANGED)
                self.assertEqual((img.dtype, img.shape), (np.dtype(dt), (H, W)))
                self.assertEqual((int(img.min()), int(img.max())), (0, top))     # full range: far 0, near max
                # a second run moves the first folder aside, never deletes it
                _write(fmt, d)
                self.assertEqual(len([n for n in os.listdir(d) if n.startswith("clip_depth")]), 2)

    def test_abort_leaves_nothing(self):
        for fmt in (F.HEVC8, F.PNG16):
            with self.subTest(fmt=fmt), tempfile.TemporaryDirectory() as d:
                w = F.open_writer(fmt, F.final_path(d, "clip", "_depth", fmt), W, H, "24")
                w.write(_codes())
                w.abort()
                self.assertEqual(os.listdir(d), [])

    def test_encode_pass_takes_the_setting(self):
        from studio.stages.dvd.runner import DvdRunner
        from studio.stages.vda.runner import Hooks

        class Info:
            fps = "24"

        with tempfile.TemporaryDirectory() as d:
            opts = dict(S.DEFAULTS, output_dir=d, output_format=F.PNG8)
            r = DvdRunner.__new__(DvdRunner)
            r.opts, r.hooks = opts, Hooks()
            import threading
            r.stop_now = threading.Event()
            raw = np.stack([np.tile(np.linspace(0, 1, W, dtype=np.float16), (H, 1))] * N)
            final, moved = r.encode(raw, [0.0] * N, [1.0] * N, N, (H, W), Info(), "clip.mp4", "clip",
                                    torch.device("cpu"))
            self.assertEqual(final, os.path.join(d, "clip_depth"))
            self.assertEqual(len(os.listdir(final)), N)


if __name__ == "__main__":
    unittest.main()
