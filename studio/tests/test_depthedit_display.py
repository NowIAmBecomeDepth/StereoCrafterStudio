"""The Depth Map Editor's picture of a depth frame shows the map's 10 bits
(user, 2026-10-05: banding in the 8-bit viewer; core.depthedit.display).
Arrays only.

    python -m unittest studio.tests.test_depthedit_display
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.depthedit import depth_io  # noqa: E402
from core.depthedit import display as D  # noqa: E402

INFO = depth_io.ClipInfo("x", 64, 64, 1, "24/1", "yuv420p10le", False)     # TV range, 64..940


def unit_of_codes(codes):
    return depth_io.to_unit(np.asarray(codes, np.uint16), INFO)


class GreyTests(unittest.TestCase):
    def test_off_is_the_old_8_bit_picture(self):
        u = np.random.default_rng(0).random((40, 56), dtype=np.float32) * 1.2 - 0.1
        old = np.repeat((np.clip(u, 0, 1) * 255.0 + 0.5).astype(np.uint8)[:, :, None], 3, axis=2)
        self.assertTrue(np.array_equal(D.depth_view(u, False, dither=False), old))

    def test_a_flat_shade_between_8_bit_levels_averages_to_it(self):
        for level in (100.25, 100.5, 100.75, 37.125):
            u = np.full((64, 64), level / 255.0, np.float32)
            img = D.depth_view(u, False, dither=True)
            self.assertLess(abs(img[..., 0].mean() - level), 1 / 64 + 1e-4, level)
            self.assertTrue(set(np.unique(img)) <= {int(level), int(level) + 1})

    def test_every_10_bit_level_of_a_slope_shows(self):
        # a gentle wall: 60 consecutive 10-bit codes across the frame
        codes = np.tile(np.repeat(np.arange(400, 460), 8), (64, 1))      # 64 x 480
        u = unit_of_codes(codes)
        plain = D.depth_view(u, False, dither=False)[..., 0]
        dith = D.depth_view(u, False, dither=True)[..., 0]
        # each code's 8 x 64 block, averaged
        plain_levels = {round(float(plain[:, i * 8:(i + 1) * 8].mean()), 3) for i in range(60)}
        dith_levels = {round(float(dith[:, i * 8:(i + 1) * 8].mean()), 3) for i in range(60)}
        self.assertLess(len(plain_levels), 20, "8 bits: about one shade per 3.4 codes")
        self.assertEqual(len(dith_levels), 60, "dithered: every code its own shade")

    def test_the_pattern_stays_put(self):
        u = np.full((24, 32), 0.4321, np.float32)
        self.assertTrue(np.array_equal(D.depth_view(u, False), D.depth_view(u.copy(), False)))

    def test_out_of_range_is_clipped(self):
        u = np.array([[-0.5, 1.7]], np.float32)
        img = D.depth_view(u, False, dither=True)
        self.assertEqual(img.dtype, np.uint8)
        self.assertEqual((int(img[0, 0, 0]), int(img[0, 1, 0])), (0, 255))


class ColourTests(unittest.TestCase):
    def test_at_8_bit_levels_it_is_turbo(self):
        u = (np.arange(256, dtype=np.float32) / 255.0)[None, :]
        want = cv2.applyColorMap(np.arange(256, dtype=np.uint8)[None, :], cv2.COLORMAP_TURBO)[..., ::-1]
        got = D.depth_view(u, True, dither=False)
        self.assertLessEqual(int(np.abs(got.astype(int) - want.astype(int)).max()), 1)

    def test_one_10_bit_step_changes_the_colour(self):
        codes = np.tile(np.repeat(np.arange(400, 460), 8), (64, 1))
        img = D.depth_view(unit_of_codes(codes), True, dither=True).astype(np.float64)
        means = [tuple(np.round(img[:, i * 8:(i + 1) * 8].reshape(-1, 3).mean(axis=0), 2)) for i in range(60)]
        self.assertEqual(len(set(means)), 60)

    def test_shape_and_type(self):
        img = D.depth_view(np.zeros((7, 9), np.float32), True)
        self.assertEqual((img.shape, img.dtype), ((7, 9, 3), np.uint8))


if __name__ == "__main__":
    unittest.main()
