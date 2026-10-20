"""Rain's own Pop and Guard (bug sweep 2026-10-09, A4 / A5, low confidence,
settled 2026-10-10):

* Pop was full at the batch's nearest drop, so it moved with where the drops
  of each batch happened to fall -- ~10% at a short last batch of sparse rain.
  It's full at RainExtractor.pop_peak(): a nearest-plane drop over the scene's
  near part, the same from batch to batch.
* Guard dimmed the rain where the scene is near; the preview also cleared its
  layer there, the render kept it (Pop's peak could come from removed rain).
  One helper does both now.

    python -m unittest studio.tests.test_rain_pop_guard
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
import core.splatting.rain as R  # noqa: E402

H, W = 135, 240


def scene():
    return np.tile(np.linspace(0.15, 0.85, W, dtype=np.float32), (H, 1))


def sparse_band(rng, n):
    band = np.zeros((H, W), np.int32)
    for _ in range(n):
        x, y = rng.integers(0, W), rng.integers(0, H - 10)
        band[y:y + 10, x] = rng.integers(1, 4)
    return band


class PopPeakTests(unittest.TestCase):
    def ext(self, **kw):
        s = R.RainSettings.from_values(mode="static", layers=3, depth=kw.get("depth", 0.6),
                                       placement=kw.get("placement", "front"))
        return R.RainExtractor(s, W)

    def test_steady_across_batches(self):
        rng = np.random.default_rng(0)
        ext, d = self.ext(), scene()
        peaks, maxes = [], []
        for b in range(8):
            n = 3 if b == 7 else 10                              # a short last batch
            rd = np.stack([ext.depth_of(sparse_band(rng, 4), d) for _ in range(n)])
            peaks.append(ext.pop_peak())
            maxes.append(float(rd.max()))
        self.assertLess(max(peaks) - min(peaks), 1e-6, peaks)
        self.assertGreater(max(maxes) - min(maxes), 0.01, "the batch max did move (the old peak)")

    def test_the_peak_is_a_nearest_drop_over_the_near_scene(self):
        rng = np.random.default_rng(1)
        for placement in ("front", "place"):
            ext, d = self.ext(placement=placement), scene()
            dense = np.stack([ext.depth_of(sparse_band(rng, 400), d) for _ in range(10)])
            peak = ext.pop_peak()
            self.assertLess(abs(peak - float(dense.max())) / float(dense.max()), 0.08, (placement, peak, dense.max()))

    def test_pop_follows_the_given_peak(self):
        import torch
        rd = torch.zeros(1, 1, 4, 4)
        rd[0, 0, 0, 0] = 0.7
        a = R.rain_pop_extra(rd, 10.0, 0.5, 20.0, peak=1.0)
        rd2 = rd.clone()
        rd2[0, 0, 1, 1] = 0.95                                   # a nearer drop in this batch only
        b = R.rain_pop_extra(rd2, 10.0, 0.5, 20.0, peak=1.0)
        self.assertAlmostEqual(float(a[0, 0, 0, 0]), float(b[0, 0, 0, 0]), places=6)
        self.assertIsNone(R.rain_pop_extra(rd, 0.0, 0.5, 20.0, peak=1.0))


class GuardTests(unittest.TestCase):
    def test_removed_rain_keeps_no_layer(self):
        d = scene()
        rain = np.ones((H, W, 3), np.float32)
        band = np.full((H, W), 2, np.int32)
        r2, b2 = R.apply_guard(rain, band, d, 1.0, W / 1920.0)
        keep = R.keep_far(d, 1.0, W / 1920.0)
        self.assertTrue(np.array_equal(b2 > 0, keep > 0.5))
        self.assertTrue(np.allclose(r2, rain * keep[..., None]))
        r3, b3 = R.apply_guard(rain, band, d, 0.0)
        self.assertIs(r3, rain)
        self.assertIs(b3, band)

    def test_render_and_preview_use_it(self):
        src = (Path(__file__).resolve().parents[2] / "core/splatting/render_processor.py").read_text(encoding="utf-8")
        self.assertIn("apply_guard(r, b, _rd[k], _rain_settings.guard, width / 1920.0)", src)
        self.assertIn("peak=rain_pop_peak", src)
        rain_src = (Path(__file__).resolve().parents[2] / "core/splatting/rain.py").read_text(encoding="utf-8")
        self.assertIn("rain, band = apply_guard(rain, band, d, rs.guard, W / 1920.0)", rain_src)


if __name__ == "__main__":
    unittest.main()
