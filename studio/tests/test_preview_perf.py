"""The previews' performance fixes (bug sweep 2026-10-09, batch 9):

* the frame buffer is capped by bytes as well as frames (A6 / A8);
* Detect Text's frame cache goes when it's suspended for a job (A6);
* Loop Playback and the debug switches don't drop the play cache (A6);
* the Depth Map Editor's 3D frames don't refresh the clip list's badges (A6);
* rain is separated only for views that show the warp (A6);
* Merging's colour transfer stays on the device it runs on (A8).

    python -m unittest studio.tests.test_preview_perf
"""

from __future__ import annotations

import inspect
import re
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data

REPO = Path(__file__).resolve().parents[2]


class FrameBufferTests(unittest.TestCase):
    def test_capped_by_bytes(self):
        from PIL import Image
        from core.ui.preview_buffer import PreviewFrameBuffer
        buf = PreviewFrameBuffer(max_frames=500, max_memory_mb=1)       # 1 MB
        frame = Image.new("RGB", (256, 256))                            # 192 KB
        for i in range(20):
            buf.cache_frame(i, frame)
        held = len(buf._main_buffer)
        self.assertLessEqual(held * 256 * 256 * 3, 2 ** 20)
        self.assertEqual(held, 5)
        self.assertEqual(sorted(buf._main_buffer), list(range(15, 20)), "the lowest frames went first")
        buf.cache_frame(19, frame)                                      # a frame cached again
        self.assertEqual(buf._bytes, held * 256 * 256 * 3)
        buf.clear()
        self.assertEqual(buf._bytes, 0)

    def test_still_capped_by_frames(self):
        from PIL import Image
        from core.ui.preview_buffer import PreviewFrameBuffer
        buf = PreviewFrameBuffer(max_frames=3, max_memory_mb=2048)
        for i in range(6):
            buf.cache_frame(i, Image.new("RGB", (8, 8)))
        self.assertEqual(sorted(buf._main_buffer), [3, 4, 5])


class DetectTextSuspendTest(unittest.TestCase):
    def test_suspend_lets_the_cache_go(self):
        from types import SimpleNamespace
        from studio.legacy import install
        install()
        from studio.stages.splatting.submask import SubtitleMask
        m = SubtitleMask.__new__(SubtitleMask)
        import threading
        m._cache, m._cache_bytes, m._cache_lock = {1: np.zeros(10, np.uint8)}, 10, threading.Lock()
        released = []
        m._release = lambda: released.append(1)
        try:
            m.suspend()
        except AttributeError as e:                          # a helper attribute this stand-in lacks
            self.fail(f"suspend needs {e}")
        self.assertEqual(released, [1])
        self.assertEqual((m._cache, m._cache_bytes), ({}, 0))


class PreviewQuietTests(unittest.TestCase):
    def test_loop_and_debug_dont_re_render(self):
        from studio.legacy import install
        install()
        from studio.stages.splatting import preview as P
        for key in ("loop_playback", "debug_logging", "debug_mode"):
            self.assertIn(key, P.NO_RENDER)

    def test_3d_frames_dont_refresh_the_badges(self):
        src = (REPO / "studio/stages/splatting/preview.py").read_text(encoding="utf-8")
        line = next(ln for ln in src.splitlines() if 'result["kind"] not in' in ln and '"render"' in ln)
        self.assertIn('"ext_depth"', line)


class RainViewsTest(unittest.TestCase):
    def test_the_rain_views_are_the_warp_views(self):
        src = (REPO / "studio/stages/splatting/preview_engine.py").read_text(encoding="utf-8")
        rain = re.search(r"_rain_views = \((.*?)\)\n", src, re.S).group(1)
        warp = re.search(r"needs_forward_warp = preview_source in \((.*?)\)\n", src, re.S).group(1)
        names = lambda s: re.findall(r'"([^"]+)"', s)  # noqa: E731
        self.assertEqual(sorted(names(rain)), sorted(names(warp)))
        self.assertIn("if preview_source in _rain_views:", src)


class ColourTransferTest(unittest.TestCase):
    def test_no_round_trip_through_the_cpu(self):
        src = (REPO / "studio/stages/merging/preview_engine.py").read_text(encoding="utf-8")
        self.assertNotIn("apply_color_transfer(original_left.cpu(), inpainted.cpu())", src)
        self.assertIn("apply_color_transfer(original_left, inpainted)", src)

    def test_the_same_result_from_device_tensors(self):
        import torch
        from core.common.image_processing import apply_color_transfer
        g = torch.Generator().manual_seed(0)
        left, right = torch.rand(1, 3, 32, 48, generator=g), torch.rand(1, 3, 32, 48, generator=g)
        dev = "cuda" if torch.cuda.is_available() else "cpu"
        old = apply_color_transfer(left.to(dev).cpu(), right.to(dev).cpu()).unsqueeze(0).to(dev)
        new = apply_color_transfer(left.to(dev), right.to(dev)).unsqueeze(0).to(dev)
        self.assertTrue(torch.equal(old, new))
        self.assertEqual(new.device.type, dev)


if __name__ == "__main__":
    unittest.main()
