"""Splatting's Rain on a lit-up frame (user, 2026-10-29 -- ch.28 00115209: "pretty sensitive when
lighting hits picking up false positives even with guard all the way up"). The light flickers and
the camera shakes, so one frame is brighter, bluer and sharper than its neighbours: everything it
has over their low quantile counted, and a lit wall's texture read as rain. Relight matches each
neighbour's light to the frame's; the scene's own texture -- in every neighbour too -- isn't
rain. Synthetic frames, no footage."""
import unittest

import cv2
import numpy as np

import studio.tests  # noqa: F401  -- the data redirect first: tests never touch the user's data
import core.splatting.rain as R

H, W = 540, 960
WALL = W // 3


def _wall():
    """A lit wall's fine texture, the left third."""
    rng = np.random.default_rng(7)
    t = cv2.GaussianBlur(rng.normal(0, 1, (H, WALL)).astype(np.float32), (0, 0), 1.0)
    return np.clip(110 + 40 * t / t.std(), 0, 255)


def _frame(seed, lit=False):
    """The wall (shaken soft, or lit up and sharp) beside a dark street with new rain streaks."""
    rng = np.random.default_rng(seed)
    f = np.full((H, W, 3), 20.0, np.float32)
    wall = _wall()
    if lit:
        f[:, :WALL] = wall[..., None] * np.array([1.3, 1.3, 1.55])
    else:
        f[:, :WALL] = cv2.GaussianBlur(wall, (0, 0), 1.2)[..., None]
    for _ in range(400):
        x, y = int(rng.integers(WALL + 20, W - 20)), int(rng.integers(0, H - 30))
        cv2.line(f, (x, y), (x, y + int(rng.integers(12, 25))), (75, 75, 75), 1, cv2.LINE_AA)
    return np.clip(f, 0, 255).astype(np.uint8)


def _rain(on: bool):
    frames = np.stack([_frame(s, lit=(s == 3)) for s in range(7)])
    old = R.RELIGHT, R.TEXTURE
    R.RELIGHT = R.TEXTURE = on
    try:
        ex = R.RainExtractor(R.RainSettings.from_values(mode="static", lights=False, strands=False), W, device="cpu")
        rain, _ = ex.extract_lengths(frames, [3])[0]
    finally:
        R.RELIGHT, R.TEXTURE = old
    return rain.sum(-1)


class LitFrame(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.old, cls.new = _rain(False), _rain(True)
        cls.wall = np.zeros((H, W), bool)
        cls.wall[20:-20, 20:WALL - 20] = True
        cls.street = np.zeros((H, W), bool)
        cls.street[:, WALL + 20:] = True

    def test_old_took_the_wall(self):
        self.assertGreater(self.old[self.wall].sum(), self.old[self.street].sum(),
                           "before: the lit wall reads as more rain than the rain")
        self.assertGreater(self.old[self.street].sum(), 0, "...and the rain is found")

    def test_the_wall_is_not_rain(self):
        self.assertLess(self.new[self.wall].sum(), 0.05 * self.old[self.wall].sum())

    def test_the_rain_stays(self):
        self.assertGreater(self.new[self.street].sum(), 0.9 * self.old[self.street].sum())


class Off(unittest.TestCase):

    def test_same_light_needs_no_relight(self):
        f = _frame(1)
        self.assertIsNone(R.relight_gain(f, f, np.ones((H, W), bool), W / 1920))

    def test_lit_up_is_matched(self):
        g = R.relight_gain(_frame(3, lit=True), _frame(1), np.ones((H, W), bool), W / 1920)
        self.assertIsNotNone(g)
        wall = g[20:-20, 20:WALL // 2 - 20]                  # half size
        red, blue = float(np.median(wall[..., 0])), float(np.median(wall[..., 2]))
        self.assertAlmostEqual(blue, 1.5, delta=0.05, msg="lit 1.55x (less the dark floor): matched")
        self.assertTrue(1.0 < red < 1.2, f"lit 1.3x: in the ramp, partly ({red:.3f})")


if __name__ == "__main__":
    unittest.main()
