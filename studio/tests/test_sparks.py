"""Particles: Sparks (user, 2026-10-07: "use our rain math to detect sparks ... a drop down
to switch to a spark detection mode"). Small bright things that MOVE are taken; a lamp that
stays put and a fire's dense flicker are not; the setting reaches the render, the sidecar
and the preview's frame-buffer key. CPU only (the GPU path: test_rain_gpu).

    python -m unittest studio.tests.test_sparks
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


def clip(n=5, seed=3):
    """A dim textured set; a lamp that stays put; sparks somewhere new every frame; a patch
    of 'fire' -- broad bright tongues that change every frame."""
    rng = np.random.default_rng(seed)
    base = (rng.random((270, 480, 3)) * 30 + 40).astype(np.uint8)
    frames, sparks = [], []
    for t in range(n):
        f = base.copy()
        cv2.circle(f, (60, 60), 4, (255, 240, 200), -1)                    # the lamp
        s = np.zeros(f.shape[:2], bool)
        for _ in range(25):                                                 # sparks: new places
            x, y = int(rng.integers(150, 330)), int(rng.integers(20, 250))
            cv2.line(f, (x, y), (x + 6, y + 3), (255, 170, 60), 2)
            cv2.line(s.view(np.uint8), (x, y), (x + 6, y + 3), 1, 2)
        # fire: broad flickering tongues -- blurred noise, bright where it peaks, new every frame
        noise = cv2.GaussianBlur(rng.random((110, 110)).astype(np.float32), (0, 0), 4)
        lit = noise > np.quantile(noise, 0.5)
        f[150:260, 360:470][lit] = (255, 150, 40)
        frames.append(f)
        sparks.append(s)
    return np.stack(frames), sparks


class Sparks(unittest.TestCase):

    def test_takes_the_sparks_not_the_lamp_or_the_fire(self):
        win, truth = clip()
        s = R.RainSettings.from_values(mode="static", particle="sparks")
        ext = R.RainExtractor(s, 480, device="cpu")
        self.assertTrue(ext.sparks)
        light, length = ext.extract_lengths(win, [2])[0]
        got = length > 0
        hit = (got & truth[2]).sum() / truth[2].sum()
        self.assertGreater(hit, 0.8, "the sparks are taken")
        self.assertFalse(got[50:71, 50:71].any(), "the lamp stays put: not a spark")
        self.assertLess(got[150:260, 360:470].mean(), 0.05, "the fire's flicker is texture")
        self.assertGreater(float(light[truth[2]].mean()), 0.2, "their light")

    def test_settings(self):
        s = R.RainSettings.from_values(particle="SPARKS")
        self.assertEqual(s.particle, "sparks")
        self.assertEqual(R.radius_of(s), 1, "the frame before and after")
        self.assertEqual(R.RainSettings.from_values().particle, "rain")
        self.assertEqual(R.RainSettings.from_values(particle="smoke").particle, "rain")


def lens_clip(n=9, seed=5):
    """A dim set; a 'lens' -- a small bright reflection that jitters a pixel and flickers every
    frame but stays put (the fire in the Goblin's eye lenses); sparks somewhere new each frame."""
    rng = np.random.default_rng(seed)
    base = (rng.random((270, 480, 3)) * 30 + 40).astype(np.uint8)
    frames, sparks, lens = [], [], np.zeros((270, 480), bool)
    cv2.circle(lens.view(np.uint8), (80, 80), 10, 1, -1)
    for t in range(n):
        f = base.copy()
        jx, jy = int(rng.integers(-1, 2)), int(rng.integers(-1, 2))
        lit = (130, 100, 60) if t in (3, 5) else (255, 200, 120)          # a flicker: frame 4 a peak
        cv2.ellipse(f, (80 + jx, 80 + jy), (4 + t % 2, 3), 15 * t, 0, 360, lit, -1)
        s = np.zeros(f.shape[:2], bool)
        for _ in range(20):
            x, y = int(rng.integers(160, 440)), int(rng.integers(20, 250))
            cv2.line(f, (x, y), (x + 6, y + 3), (255, 170, 60), 2)
            cv2.line(s.view(np.uint8), (x, y), (x + 6, y + 3), 1, 2)
        frames.append(f)
        sparks.append(s)
    return np.stack(frames), sparks, lens


class Reflections(unittest.TestCase):
    """Keep reflections out (user, 2026-10-08: the Goblin's mask "being picked up too much no
    matter what i try")."""

    def run_(self, on):
        win, truth, lens = lens_clip()
        s = R.RainSettings.from_values(mode="static", particle="sparks", reflections=on)
        ext = R.RainExtractor(s, 480, device="cpu")
        wi, c = R.window_indices([4], len(win), R.radius_of(s))
        _, length = ext.extract_lengths(win[wi], c)[0]
        got = length > 0
        return got[lens].any(), (got & truth[4]).sum() / truth[4].sum()

    def test_off_by_default_and_unchanged(self):
        self.assertFalse(R.RainSettings.from_values().reflections)
        self.assertEqual(R.radius_of(R.RainSettings.from_values(particle="sparks")), 1)
        took_lens, _ = self.run_(False)
        self.assertTrue(took_lens, "off: the flickering lens is taken for a spark (the bug)")

    def test_on_keeps_the_lens_and_the_sparks(self):
        self.assertEqual(R.radius_of(R.RainSettings.from_values(particle="sparks", reflections=True)), 3)
        took_lens, hit = self.run_(True)
        self.assertFalse(took_lens, "on: the lens stays with the scene")
        self.assertGreater(hit, 0.8, "...and the sparks are still taken")

    def test_plumbing(self):
        from studio.legacy import install
        install()
        from studio.stages.splatting import settings as S
        from core.splatting.batch_processing import ProcessingSettings
        from core.common.sidecar_manager import SidecarConfigManager as M
        from core.ui.preview_buffer import PreviewFrameBuffer
        vals = dict(S.DEFAULTS)
        self.assertIs(vals["rain_reflections"], False)
        vals["rain_reflections"] = True
        self.assertTrue(ProcessingSettings.from_config(S.config_for_run(vals)).rain_reflections)
        self.assertIn("rain_reflections", M.SIDECAR_KEY_MAP)
        self.assertIn("rain_reflections", M.KEEP_GUI_WHEN_MISSING)
        buf = PreviewFrameBuffer(max_frames=2, max_memory_mb=1)
        self.assertNotEqual(buf._generate_signature_from_params({"rain_reflections": False}, "x.mp4"),
                            buf._generate_signature_from_params({"rain_reflections": True}, "x.mp4"))
        qml = (Path(__file__).resolve().parents[1] / "qml/Studio/SplattingPage.qml").read_text(encoding="utf-8")
        self.assertIn('key: "rain_reflections"', qml)


class Plumbing(unittest.TestCase):

    def test_reaches_the_render(self):
        from studio.legacy import install
        install()
        from studio.stages.splatting import settings as S
        from core.splatting.batch_processing import ProcessingSettings, _saved_or
        self.assertEqual(S.DEFAULTS["rain_particle"], "rain")
        vals = dict(S.DEFAULTS)
        vals["rain_particle"] = "sparks"
        self.assertEqual(ProcessingSettings.from_config(S.config_for_run(vals)).rain_particle, "sparks")
        self.assertEqual(_saved_or({"rain_particle": None}, "rain_particle", "sparks"), "sparks")

    def test_frame_buffer_and_sidecar(self):
        from core.ui.preview_buffer import PreviewFrameBuffer
        from core.common.sidecar_manager import SidecarConfigManager as M
        buf = PreviewFrameBuffer(max_frames=2, max_memory_mb=1)
        self.assertNotEqual(buf._generate_signature_from_params({"rain_particle": "rain"}, "x.mp4"),
                            buf._generate_signature_from_params({"rain_particle": "sparks"}, "x.mp4"))
        self.assertIsNone(M.SIDECAR_KEY_MAP["rain_particle"][1])
        self.assertIn("rain_particle", M.KEEP_GUI_WHEN_MISSING)


if __name__ == "__main__":
    unittest.main()
