"""Splatting's particles: Placement (user, 2026-10-07: "the bight sparks behind the goblin
bomb appear in front of it"). In front -- the default, unchanged: planes from the scene's
middle toward the camera, the same all over the frame. In place: each particle at the
scene's own depth where it is (smoothed), lifted by its layer -- sparks over a near object
sit on it, sparks over the far wall stay back. iw3's GPU version matches. The setting
reaches the render (a clip's sidecar over it), the preview's frame-buffer key and the page.
CPU only.

    python -m unittest studio.tests.test_rain_placement
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.splatting.rain import RainExtractor, RainSettings, in_place_depth, particle_depth  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
H, W = 270, 480


def bomb():
    """A near object (0.7) on a far wall (0.2); a spark over each, on the nearest layer."""
    d = np.full((H, W), 0.2, np.float32)
    d[100:200, 150:300] = 0.7
    band = np.zeros((H, W), np.uint8)
    band[150, 225] = 3
    band[40, 50] = 3
    return d, band


class Placement(unittest.TestCase):

    def test_default_is_in_front(self):
        self.assertEqual(RainSettings.from_values().placement, "front")
        self.assertEqual(RainSettings.from_values(placement="nonsense").placement, "front")
        d, band = bomb()
        ext = RainExtractor(RainSettings.from_values(mode="moving", depth=1.0), W)
        rd = ext.depth_of(band, d)
        self.assertAlmostEqual(float(rd[150, 225]), float(rd[40, 50]), 6)   # Depth 1: one plane for the frame
        self.assertGreater(float(rd[150, 225]), 0.75)                       # in front of the object

    def test_depth_0_is_no_effect(self):
        """user, 2026-10-07: "the slider being at 0 to show the preview with 0 particle effects"
        -- each particle at the scene's own depth right where it is, in either placement."""
        d, band = bomb()
        for placement in ("front", "place"):
            rd = particle_depth(band, d, 3, 0.0, placement, 0.2, 0.7)
            self.assertTrue(np.array_equal(rd[band > 0], d[band > 0]), placement)
            self.assertEqual(float(rd[band == 0].max()), 0.0)

    def test_depth_moves_it_all_the_way(self):
        """...and "so miniscule in how much it adjusts": on a map stretched to 0..1 (iw3's),
        Depth 1 brings the nearest particles a whole spread in front of the scene's nearest."""
        d = np.linspace(0.0, 1.0, H * W, dtype=np.float32).reshape(H, W)
        band = np.zeros((H, W), np.uint8)
        band[H // 2, W // 2] = 3
        mid, near = float(np.percentile(d, 50)), float(np.percentile(d, 98))
        lo = particle_depth(band, d, 3, 0.0, "front", mid, near)[H // 2, W // 2]
        hi = particle_depth(band, d, 3, 1.0, "front", mid, near)[H // 2, W // 2]
        self.assertGreater(hi - lo, 0.9, (lo, hi))
        self.assertAlmostEqual(float(hi), near + (near - mid), 4)

    def test_in_place_stays_with_the_scene(self):
        d, band = bomb()
        ext = RainExtractor(RainSettings.from_values(mode="moving", placement="place", depth=1.0), W)
        rd = ext.depth_of(band, d)
        self.assertTrue(0.7 <= rd[150, 225] < 0.85, rd[150, 225])    # on the object, a little lift
        self.assertTrue(0.2 <= rd[40, 50] < 0.35, rd[40, 50])        # back on the wall
        self.assertEqual(float(rd[band == 0].max()), 0.0)
        flat = in_place_depth(band, d, 3, 0.0)                         # Depth 0: on the surface
        self.assertAlmostEqual(float(flat[150, 225]), 0.7, 5)

    def test_layers_lift_in_order(self):
        d = np.full((H, W), 0.4, np.float32)
        band = np.zeros((H, W), np.uint8)
        band[10, 10], band[10, 20], band[10, 30] = 1, 2, 3
        rd = in_place_depth(band, d, 3, 0.5)
        self.assertLess(rd[10, 10], rd[10, 20])
        self.assertLess(rd[10, 20], rd[10, 30])

    def test_iw3_gpu_version_matches(self):
        try:
            import torch
            sys.path.insert(0, r"A:\iw3\nunif")
            from iw3 import rain as I
        except Exception as e:                                         # iw3 isn't installed here
            self.skipTest(str(e))
        rng = np.random.default_rng(0)
        d = rng.random((H, W)).astype(np.float32)
        band = rng.integers(0, 4, (H, W)).astype(np.uint8)
        a = in_place_depth(band, d, 3, 0.5)
        mid, near = float(np.percentile(d, 50)), float(np.percentile(d, 98))
        bt, dt = torch.from_numpy(band.astype(np.int64)), torch.from_numpy(d)
        for placement in ("front", "place"):
            a = particle_depth(band, d, 3, 0.5, placement, mid, near)
            b = I.particle_depth_torch(bt, dt[None], 3, 0.5, placement, mid, near)
            self.assertLess(float(np.abs(a - b.numpy()).max()), 1e-5, placement)
        b2 = I.depth_torch(bt, dt, 3, 0.5, "place")                    # its own percentiles (torch's)
        self.assertLess(float(np.abs(in_place_depth(band, d, 3, 0.5) - b2.numpy()).max()), 1e-3)



class LayerControls(unittest.TestCase):
    """Spread, Spacing and the particles' own Convergence (user, 2026-10-08: "can the depth
    controls be more manipulated like distance between layers and such", "oh and convergence
    for particles")."""

    def planes(self, **kw):
        from core.splatting.rain import full_levels
        return full_levels(0.3, 0.8, 4, **kw)[1:]

    def test_defaults_unchanged(self):
        from core.splatting.rain import spread_of
        mid, near = 0.3, 0.8
        far, nl = mid + 0.5 * (near - mid), near + spread_of(mid, near)
        self.assertTrue(np.allclose(self.planes(), np.linspace(far, nl, 4)))
        s = RainSettings.from_values()
        self.assertEqual((s.spread, s.spacing, s.conv), (1.0, 1.0, 0.0))

    def test_spread(self):
        base, tight, wide = self.planes(), self.planes(spread=0.0), self.planes(spread=2.0)
        self.assertTrue(np.allclose(tight, base[-1]), "0: every layer on the nearest plane")
        self.assertAlmostEqual(float(wide[-1]), float(base[-1]), 6)
        self.assertAlmostEqual(float(wide[-1] - wide[0]), 2 * float(base[-1] - base[0]), 5)

    def test_spacing(self):
        even, near, far = self.planes(), self.planes(spacing=3.0), self.planes(spacing=0.4)
        self.assertAlmostEqual(float(near[0]), float(even[0]), 6)
        self.assertAlmostEqual(float(near[-1]), float(even[-1]), 6)
        self.assertGreater(float(near[1]), float(even[1]), "above 1: crowded toward the nearest")
        self.assertLess(float(far[1]), float(even[1]), "below 1: toward the farthest")

    def test_convergence(self):
        d, band = bomb()
        a = particle_depth(band, d, 3, 0.5, "front", 0.2, 0.7)
        b = particle_depth(band, d, 3, 0.5, "front", 0.2, 0.7, conv=0.1)
        on = band > 0
        self.assertTrue(np.allclose(a[on] - b[on], 0.1, atol=1e-6), "+ moves them back by that much")
        self.assertEqual(float(b[~on].max()), 0.0)
        self.assertEqual(RainSettings.from_values(conv=5).conv, 1.0)

    def test_iw3_matches(self):
        try:
            import torch
            sys.path.insert(0, r"A:\iw3\nunif")
            from iw3 import rain as I
        except Exception as e:
            self.skipTest(str(e))
        rng = np.random.default_rng(1)
        d = rng.random((H, W)).astype(np.float32)
        band = rng.integers(0, 4, (H, W)).astype(np.uint8)
        for placement in ("front", "place"):
            a = particle_depth(band, d, 3, 0.7, placement, 0.4, 0.9, 1.6, 2.5, -0.2)
            b = I.particle_depth_torch(torch.from_numpy(band.astype(np.int64)), torch.from_numpy(d), 3, 0.7,
                                       placement, 0.4, 0.9, 1.6, 2.5, -0.2)
            self.assertLess(float(np.abs(a - b.numpy()).max()), 1e-5, placement)

    def test_plumbing(self):
        from studio.legacy import install
        install()
        from studio.stages.splatting import settings as S
        from core.splatting.batch_processing import ProcessingSettings
        from core.common.sidecar_manager import SidecarConfigManager as M
        from core.ui.preview_buffer import PreviewFrameBuffer
        vals = dict(S.DEFAULTS)
        vals.update(rain_spread=0.5, rain_spacing=2.0, rain_conv=-0.3)
        ps = ProcessingSettings.from_config(S.config_for_run(vals))
        self.assertEqual((ps.rain_spread, ps.rain_spacing, ps.rain_conv), (0.5, 2.0, -0.3))
        buf = PreviewFrameBuffer(max_frames=2, max_memory_mb=1)
        for k in ("rain_spread", "rain_spacing", "rain_conv"):
            self.assertIn(k, M.SIDECAR_KEY_MAP)
            self.assertIn(k, M.KEEP_GUI_WHEN_MISSING)
            self.assertNotEqual(buf._generate_signature_from_params({k: 0.5}, "x.mp4"),
                                buf._generate_signature_from_params({k: 0.75}, "x.mp4"))
        qml = (REPO / "studio/qml/Studio/SplattingPage.qml").read_text(encoding="utf-8")
        for k in ("rain_spread", "rain_spacing", "rain_conv"):
            self.assertIn('key: "%s"' % k, qml)


class Plumbing(unittest.TestCase):

    def test_setting_reaches_the_render(self):
        from studio.legacy import install
        install()
        from studio.stages.splatting import settings as S
        from core.splatting.batch_processing import ProcessingSettings, _saved_or
        vals = dict(S.DEFAULTS)
        self.assertEqual(vals["rain_placement"], "front")
        vals["rain_placement"] = "place"
        ps = ProcessingSettings.from_config(S.config_for_run(vals))
        self.assertEqual(ps.rain_placement, "place")
        self.assertEqual(_saved_or({"rain_placement": "front"}, "rain_placement", "place"), "front")
        self.assertEqual(_saved_or({"rain_placement": None}, "rain_placement", "place"), "place")

    def test_in_the_sidecar(self):
        from core.common.sidecar_manager import SidecarConfigManager
        self.assertIn("rain_placement", SidecarConfigManager.SIDECAR_KEY_MAP)
        self.assertIn("rain_placement", SidecarConfigManager.KEEP_GUI_WHEN_MISSING)

    def test_in_the_frame_buffer_key(self):
        from core.ui.preview_buffer import PreviewFrameBuffer
        buf = PreviewFrameBuffer(max_frames=2, max_memory_mb=1)
        self.assertNotEqual(buf._generate_signature_from_params({"rain_placement": "front"}, "x.mp4"),
                            buf._generate_signature_from_params({"rain_placement": "place"}, "x.mp4"))

    def test_on_the_page(self):
        qml = (REPO / "studio/qml/Studio/SplattingPage.qml").read_text(encoding="utf-8")
        self.assertIn('objectName: "rainPlacement"', qml)
        self.assertIn("page.st.rain_placement", qml)


if __name__ == "__main__":
    unittest.main()
