"""DepthCrafter's affine stabilisation with something passing right past the lens (user, 2026-10-12,
chap2_00097213: a person crossing the camera at frames 61-74 of a locked-off shot, and the static scene
2.7x brighter after frame ~140 -- the windows without the pass renegotiated the scale). The old solve
picked the pass as its reference (it has the widest spread), so nothing fitted and the ramp stayed;
with a sound reference its fits during the pass locked onto the person (gain 0.03) and flattened it
into the background. Now the reference is an ordinary frame, and a fit on an unusual frame that jumps
from its neighbour's keeps the neighbour's transform. A synthetic clip reproduces both."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first
import numpy as np  # noqa: E402

from depthcrafter import depth_stabilize as DS  # noqa: E402

T, H, W = 100, 48, 96
PASS = range(30, 43)            # something close covers half the frame here
DRIFT_FROM, DRIFT_TO = 60, 80   # ...and from here the static scene brightens 1.5x


def clip():
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    scene = 0.08 + 0.25 * xx / W + 0.1 * yy / H
    scene[12:40, 20:45] = 0.45                              # a figure in the shot, static
    d = np.repeat(scene[None], T, axis=0).copy()
    for t in range(T):
        g = 1.0 + 0.5 * np.clip((t - DRIFT_FROM) / (DRIFT_TO - DRIFT_FROM), 0, 1)
        d[t] = scene * g
    for t in PASS:
        d[t][:, 48:] = 0.55 + 0.4 * (xx[:, 48:] - 48) / 48         # close: bright, blurred across
    return d, scene


class StabiliseTest(unittest.TestCase):
    def setUp(self):
        self.d, self.scene = clip()

    def test_the_reference_is_an_ordinary_frame(self):
        self.assertNotIn(DS._auto_reference_index(self.d, 4), PASS)

    def test_the_static_scene_is_steady_after_the_drift(self):
        out, st = DS.stabilize_depth_affine(self.d.copy(), smooth_window=0)
        static = out[:, :, :48]                                   # never covered by the pass
        ratio = static[T - 1].mean() / static[0].mean()
        self.assertAlmostEqual(ratio, 1.0, delta=0.03, msg=f"start vs end of the static scene: x{ratio:.2f}")

    def test_the_pass_is_not_flattened_into_the_background(self):
        out, st = DS.stabilize_depth_affine(self.d.copy(), ref_index=0, smooth_window=0)
        for t in PASS:
            near, scene = out[t][:, 56:].mean(), out[t][:, :20].mean()
            self.assertGreater(near, 2 * scene, f"frame {t}: the close thing {near:.3f} vs the scene {scene:.3f}")
        self.assertEqual(set(st["held_frame_indices"]), set(PASS))

    def test_a_clip_without_a_pass_still_follows_the_drift(self):
        d = np.repeat(self.scene[None], T, axis=0) * np.linspace(1.0, 1.8, T)[:, None, None]
        out, st = DS.stabilize_depth_affine(d.astype(np.float32), ref_index=0, smooth_window=0)
        self.assertEqual(st["frames_held"], 0)
        self.assertLess(float(np.abs(out - out[0]).max()), 0.01)


class AutoTopTest(unittest.TestCase):
    """Percentile Output Norm's Auto Top (user, 2026-10-12): the range from the usual frames only, so
    the brief close pass clips to white and the scene keeps the range; without a pass, unchanged."""

    def setUp(self):
        self.d, self.scene = clip()
        self.s, _ = DS.stabilize_depth_affine(self.d.copy(), smooth_window=0)

    def test_the_pass_clips_and_the_scene_gets_the_range(self):
        plain, _ = DS.normalize_with_percentiles(self.s, 0.1, 99.9)
        auto, st = DS.normalize_with_percentiles(self.s, 0.1, 99.9, auto_top=True)
        self.assertEqual(st["auto_top_unusual_frames"], list(PASS))
        usual = [t for t in range(T) if t not in PASS]
        self.assertGreater(np.percentile(auto[usual], 99), 0.95)               # the scene: the whole range
        self.assertLess(np.percentile(plain[usual], 99), 0.8)                  # ...where the pass squeezed it
        self.assertGreater((auto[list(PASS)][:, :, 64:] >= 0.999).mean(), 0.5)  # the pass: white

    def test_without_a_pass_it_is_the_same(self):
        d = np.repeat(self.scene[None], T, axis=0) * np.linspace(1.0, 1.2, T)[:, None, None].astype(np.float32)
        plain, _ = DS.normalize_with_percentiles(d, 0.1, 99.9)
        auto, st = DS.normalize_with_percentiles(d, 0.1, 99.9, auto_top=True)
        self.assertEqual(st["auto_top_unusual_frames"], [])
        self.assertTrue(np.array_equal(plain, auto))

    def test_off_reports_nothing_new(self):
        _, st = DS.normalize_with_percentiles(self.s, 0.1, 99.9)
        self.assertNotIn("auto_top_unusual_frames", st)


if __name__ == "__main__":
    unittest.main()
