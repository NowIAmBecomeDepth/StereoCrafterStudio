"""core.common.gpu_ops against the OpenCV it replaces (user, 2026-10-04:
colour transfer, Text Grow / Soften / Clean and the Depth Map Editor's
source-size render on the GPU). The ops run on any device: on the GPU when
there is one, else on the CPU -- the same maths either way.

    python -m unittest studio.tests.test_gpu_ops
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import cv2
import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.common import gpu_ops as G  # noqa: E402
from core.common.image_processing import apply_color_transfer  # noqa: E402

DEV = G.device()


def t4(a):
    return torch.from_numpy(np.ascontiguousarray(a, dtype=np.float32)).to(DEV)[None, None]


def back(x):
    return x[0, 0].cpu().numpy()


class MorphologyTests(unittest.TestCase):
    def test_dilate_and_erode_match_opencv_exactly(self):
        rng = np.random.default_rng(1)
        m = (rng.random((90, 130)) > 0.99).astype(np.uint8) * 255
        m[30:50, 40:90] = 255
        for r in (1, 4, 9):
            k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
            self.assertTrue(np.array_equal(back(G.dilate_ellipse(t4(m), r)).astype(np.uint8), cv2.dilate(m, k)), r)
            self.assertTrue(np.array_equal(back(G.erode_ellipse(t4(m), r)).astype(np.uint8), cv2.erode(m, k)), r)


class FilterTests(unittest.TestCase):
    def test_gaussian_matches_opencv(self):
        f = np.random.default_rng(2).random((80, 120)).astype(np.float32)
        for s in (0.7, 2.5, 9.0):
            self.assertLess(np.abs(back(G.gaussian_blur(t4(f), s)) - cv2.GaussianBlur(f, (0, 0), s)).max(), 1e-4, s)

    def test_a_very_wide_blur_stays_close(self):
        f = cv2.GaussianBlur(np.random.default_rng(3).random((200, 300)).astype(np.float32), (0, 0), 4)
        self.assertLess(np.abs(back(G.gaussian_blur(t4(f), 40)) - cv2.GaussianBlur(f, (0, 0), 40)).max(), 0.005)

    def test_guided_filter_matches_ximgproc(self):
        rng = np.random.default_rng(4)
        g = cv2.GaussianBlur(rng.random((120, 160)).astype(np.float32), (0, 0), 3)
        m = np.zeros((120, 160), np.float32)
        m[30:90, 40:120] = 1
        for r in (2, 6):
            ref = cv2.ximgproc.guidedFilter(guide=g, src=m, radius=r, eps=1e-4)
            out = back(G.guided_filter(t4(g), t4(m), r, 1e-4))
            self.assertLess(np.abs(out - ref).max(), 1e-3, r)
            self.assertTrue(np.array_equal(out >= 0.5, ref >= 0.5), r)


class ColourTransferTests(unittest.TestCase):
    def test_close_to_the_opencv_version(self):
        rng = np.random.default_rng(5)
        img = cv2.resize((rng.random((9, 16, 3)) * 255).astype(np.uint8), (160, 90), interpolation=cv2.INTER_CUBIC)
        ref = torch.from_numpy(img).permute(2, 0, 1).float() / 255
        tgt = (ref * 0.7 + torch.tensor([0.1, 0.05, 0.2]).view(3, 1, 1)).clamp(0, 1)
        out = G.color_transfer(ref.to(DEV), tgt.to(DEV)).cpu()
        # the frame takes the reference's colours back (as the OpenCV one did)
        self.assertLess(float((out - ref).abs().mean()), float((tgt - ref).abs().mean()) / 3)
        # Lab of the result has the reference's mean, to within a level
        lab_o, lab_r = G.rgb_to_lab(out[None]), G.rgb_to_lab(ref[None])
        self.assertLess(float((lab_o.mean((2, 3)) - lab_r.mean((2, 3))).abs().max()), 1.5)

    def test_a_smooth_gradient_keeps_its_levels(self):
        # OpenCV's 8-bit round trip bands it; the float one does not
        g = torch.linspace(0.2, 0.3, 1920).view(1, 1, -1).expand(3, 16, -1).clone()
        r = g * 0.9
        levels = lambda x: len(torch.unique((x[1, 0] * 1023).round()))  # noqa: E731
        gpu = G.color_transfer(r.to(DEV), g.to(DEV)).cpu()
        import core.common.image_processing as IP
        was, IP._ct_on_gpu = IP._ct_on_gpu, (lambda: False)          # the OpenCV path, as it was
        try:
            cv = apply_color_transfer(r, g)
        finally:
            IP._ct_on_gpu = was
        self.assertGreater(levels(gpu), 3 * levels(cv), f"{levels(gpu)} vs {levels(cv)}")

    def test_batches_and_shapes(self):
        refs, tgts = torch.rand(5, 3, 20, 30), torch.rand(5, 3, 20, 30)
        out = G.color_transfer(refs.to(DEV), tgts.to(DEV), chunk=2)
        self.assertEqual(tuple(out.shape), (5, 3, 20, 30))
        one = G.color_transfer(refs[3].to(DEV), tgts[3].to(DEV))
        self.assertTrue(torch.allclose(out[3], one, atol=1e-5))


if __name__ == "__main__":
    unittest.main()
