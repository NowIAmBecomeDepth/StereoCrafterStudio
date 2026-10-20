"""Splatting's Rain layers (user, 2026-10-07: "add the layers dial", 1..10):
the rain split into that many depth planes by streak length, an equal share
on each, the planes spread evenly from mid-scene to Rain Depth's reach; the
setting reaching the render (Studio setting -> processing settings; a
clip's sidecar over it, a sidecar without it leaving the setting) and the
preview's frame-buffer key. CPU only.

    python -m unittest studio.tests.test_rain_layers
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.splatting.rain import MAX_LAYERS, RainExtractor, RainSettings  # noqa: E402


def _lengths(n_pixels=6000, seed=3):
    rng = np.random.default_rng(seed)
    length = np.zeros((120, 100), np.float32)
    idx = rng.choice(length.size, n_pixels, replace=False)
    length.flat[idx] = rng.uniform(3, 60, n_pixels)
    return length


class Layers(unittest.TestCase):

    def ext(self, layers, depth=0.5):
        return RainExtractor(RainSettings.from_values(mode="static", layers=layers, depth=depth), 1920, device="cpu")

    def test_equal_shares(self):
        length = _lengths()
        for n in (1, 3, 5, 10):
            band = self.ext(n).bands_of(length)
            counts = np.bincount(band[length > 0], minlength=n + 1)[1:]
            self.assertEqual(len(counts), n)
            self.assertTrue((band[length == 0] == 0).all(), "no rain, no layer")
            self.assertLess(counts.max() - counts.min(), 0.02 * counts.sum() + 2, f"{n} layers: {counts}")

    def test_longer_streaks_nearer(self):
        length = _lengths()
        band = self.ext(4).bands_of(length)
        means = [float(length[band == k].mean()) for k in range(1, 5)]
        self.assertEqual(means, sorted(means), "layer 1 the shortest (farthest) .. the last the longest (nearest)")

    def test_planes_spread_evenly(self):
        scene = np.linspace(0.2, 0.6, 120 * 100, dtype=np.float32).reshape(120, 100)
        for n in (1, 2, 6, 10):
            ext = self.ext(n, depth=1.0)            # Depth 1: the planes themselves
            band = np.zeros((120, 100), np.uint8)
            band.flat[:n] = np.arange(1, n + 1)
            d = ext.depth_of(band, scene)
            planes = d.flat[:n]
            self.assertTrue((d.flat[n:] == 0).all(), "no rain: behind everything")
            mid, near = float(np.percentile(scene, 50)), float(np.percentile(scene, 98))
            far_level, near_level = mid + 0.5 * (near - mid), near + max(near - mid, 1 - near, 0.1)
            if n == 1:
                self.assertAlmostEqual(float(planes[0]), 0.5 * (far_level + near_level), places=5)
            else:
                self.assertAlmostEqual(float(planes[0]), far_level, places=5)
                self.assertAlmostEqual(float(planes[-1]), near_level, places=5)
                gaps = np.diff(planes)
                self.assertTrue(np.allclose(gaps, gaps[0], atol=1e-5), f"even: {planes}")

    def test_clamped(self):
        self.assertEqual(RainSettings.from_values(layers=0).layers, 1)
        self.assertEqual(RainSettings.from_values(layers=99).layers, MAX_LAYERS)
        self.assertEqual(RainSettings.from_values(layers="6.0").layers, 6)
        self.assertEqual(RainSettings.from_values(layers=None).layers, 3)


class Plumbing(unittest.TestCase):

    def test_setting_reaches_the_render(self):
        from studio.legacy import install
        install()
        from studio.stages.splatting import settings as S
        from core.splatting.batch_processing import ProcessingSettings, _saved_or
        vals = dict(S.DEFAULTS)
        vals["rain_layers"] = 7
        ps = ProcessingSettings.from_config(S.config_for_run(vals))
        self.assertEqual(int(ps.rain_layers), 7)
        self.assertEqual(_saved_or({"rain_layers": 2}, "rain_layers", 7), 2, "a clip's sidecar wins")
        self.assertEqual(_saved_or({"rain_layers": None}, "rain_layers", 7), 7, "a sidecar without it leaves the setting")

    def test_in_the_frame_buffer_key(self):
        from core.ui.preview_buffer import PreviewFrameBuffer
        buf = PreviewFrameBuffer(max_frames=2, max_memory_mb=1)
        a = buf._generate_signature_from_params({"rain_layers": 3}, "x.mp4")
        b = buf._generate_signature_from_params({"rain_layers": 7}, "x.mp4")
        self.assertNotEqual(a, b, "a Layers change must clear the frames the preview kept")


if __name__ == "__main__":
    unittest.main()
