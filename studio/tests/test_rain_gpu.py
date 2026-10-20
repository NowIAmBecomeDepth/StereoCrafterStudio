"""Rain on the GPU (core.splatting.rain_gpu; user, 2026-10-07: "port it to the gpu"): each
image operation equals OpenCV's, and a whole extraction matches the CPU code's (the same
rain, a few borderline pixels either way). Skipped without CUDA.

    python -m unittest studio.tests.test_rain_gpu
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
import core.splatting.rain as R  # noqa: E402

try:
    import torch
    CUDA = torch.cuda.is_available()
except Exception:
    CUDA = False


@unittest.skipUnless(CUDA, "no CUDA")
class Ops(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        from core.splatting import rain_gpu as G
        cls.G = G
        cls.ops = G.Ops("cuda")
        rng = np.random.default_rng(0)
        cls.x = rng.random((131, 197)).astype(np.float32)
        cls.xt = torch.from_numpy(cls.x).cuda()

    def same(self, a, b, tol=1e-5):
        self.assertLessEqual(float(np.abs(a.cpu().numpy() - b).max()), tol)

    def test_line_filters(self):
        for a, L in ((0, 9), (37, 15), (90, 31), (172.5, 7)):
            self.same(self.ops.along(self.xt, a, L), R.along(self.x, a, L))

    def test_morphology(self):
        for k in (3, 7, 21):
            se = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
            self.same(self.ops.erode(self.xt, k), cv2.erode(self.x, se), 0)
            self.same(self.ops.dilate(self.xt, k), cv2.dilate(self.x, se), 0)
            self.same(self.ops.tophat(self.xt, k), cv2.morphologyEx(self.x, cv2.MORPH_TOPHAT, se), 1e-6)

    def test_blur_resize_grey(self):
        for s in (4.0, 6.0, 40.0):                       # 40: the pad is bigger than the image
            self.same(self.ops.gauss(self.xt, s), cv2.GaussianBlur(self.x, (0, 0), s))
        self.same(self.ops.half(self.xt[:130, :196]),
                  cv2.resize(self.x[:130, :196], None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA))
        self.same(self.ops.resize(self.xt, (262, 394)), cv2.resize(self.x, (394, 262), interpolation=cv2.INTER_LINEAR))
        u8 = np.random.default_rng(1).integers(0, 256, (40, 50, 3), dtype=np.uint8)
        self.same(self.G.grey_of(torch.from_numpy(u8).cuda()),
                  cv2.cvtColor(u8, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255, 1e-6)


@unittest.skipUnless(CUDA, "no CUDA")
class Whole(unittest.TestCase):

    def clip(self, seed=4):
        rng = np.random.default_rng(seed)
        base = (rng.random((1, 270, 480, 3)) * 60 + 60).astype(np.uint8).repeat(7, 0)
        for t in range(7):
            for _ in range(250):
                x, y = int(rng.integers(0, 476)), int(rng.integers(0, 250))
                cv2.line(base[t], (x, y), (x + 2, y + int(rng.integers(8, 18))), (200, 200, 200), 1)
        return base

    def test_matches_the_cpu(self):
        win = self.clip()
        for preset in ("downpour", "soft", "mist"):
            s = R.RainSettings.from_values(mode="static", preset=preset)
            rc, lc = R.RainExtractor(s, 480, device="cpu").extract_lengths(win, [3])[0]
            ext = R.RainExtractor(s, 480, device="cuda")
            self.assertTrue(ext.gpu)
            rg, lg = ext.extract_lengths(win, [3])[0]
            mc, mg = lc > 0, lg > 0
            iou = (mc & mg).sum() / max((mc | mg).sum(), 1)
            self.assertGreater(iou, 0.95, preset)
            self.assertAlmostEqual(float(rg.sum() / max(rc.sum(), 1e-9)), 1.0, delta=0.03, msg=preset)

    def test_sparks_match_the_cpu(self):
        from studio.tests.test_sparks import clip
        win, _ = clip()
        s = R.RainSettings.from_values(mode="static", particle="sparks")
        rc, lc = R.RainExtractor(s, 480, device="cpu").extract_lengths(win, [2])[0]
        rg, lg = R.RainExtractor(s, 480, device="cuda").extract_lengths(win, [2])[0]
        mc, mg = lc > 0, lg > 0
        self.assertGreater((mc & mg).sum() / max((mc | mg).sum(), 1), 0.95)
        self.assertAlmostEqual(float(rg.sum() / max(rc.sum(), 1e-9)), 1.0, delta=0.03)

    def test_cpu_still_the_cpu(self):
        self.assertFalse(R.RainExtractor(R.RainSettings.from_values(mode="static"), 480, device="cpu").gpu)


if __name__ == "__main__":
    unittest.main()
