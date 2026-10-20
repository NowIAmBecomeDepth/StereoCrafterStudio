"""Splatting's Rain: Keep lights out (user, 2026-10-10: a rainy night street -- lamps, lit
windows and the lamp over a sign taken as rain "even with guard at 1.0"). A light that lands a
few px off in the lined-up neighbours leaves a bright left-over the detector reads as a streak;
a light is bright in the frame, stands out from around it and is wider than a drop. Synthetic
frames, no footage."""
import unittest

import cv2
import numpy as np

import studio.tests  # noqa: F401  -- the data redirect first: tests never touch the user's data
from core.splatting.rain import RainExtractor, RainSettings, lights


def _scene(seed, lamp_x=400):
    """A dark street with a lit lamp post (along the rain's slant) and faint rain streaks to its right."""
    rng = np.random.default_rng(seed)
    f = np.full((540, 960, 3), 30, np.uint8)
    cv2.line(f, (lamp_x, 110), (lamp_x + 4, 190), (235, 225, 200), 8, cv2.LINE_AA)
    for _ in range(300):
        x, y = int(rng.integers(500, 940)), int(rng.integers(0, 500))
        cv2.line(f, (x, y), (x + 2, y + int(rng.integers(10, 25))), (55, 55, 55), 1, cv2.LINE_AA)
    return f


class Lights(unittest.TestCase):

    def test_found(self):
        f = _scene(0)
        m = lights(f, 0.5)
        self.assertTrue(m[150, 402], "the lamp")
        self.assertFalse(m[:, 480:].any(), "no rain streak is a light")

    def test_a_bright_sheet_is_not_a_light(self):
        """A lit rain sheet or a wet street is bright but doesn't stand out from around it."""
        f = np.full((540, 960, 3), 200, np.uint8)
        self.assertFalse(lights(f, 0.5).any())

    def test_kept_out_of_the_rain(self):
        """The lamp moved by 2 px in one frame (a camera move the flow didn't quite follow)."""
        frames = np.stack([_scene(s) for s in range(7)])
        frames[3] = _scene(3, lamp_x=402)
        near = np.zeros((540, 960), bool)
        near[100:200, 385:425] = True
        got = {}
        for on in (True, False):
            ex = RainExtractor(RainSettings.from_values(mode="static", lights=on), 960, device="cpu")
            rain, _ = ex.extract_lengths(frames, [3])[0]
            got[on] = rain.sum(-1)
        self.assertGreater(got[False][near].sum(), 0.5, "off: the lamp's left-over reads as rain")
        self.assertEqual(got[True][near].sum(), 0.0, "on: none taken at the lamp")
        self.assertGreater(got[True][~near].sum(), 0.9 * got[False][~near].sum(), "...and the rain elsewhere stays")

    def test_defaults(self):
        self.assertTrue(RainSettings.from_values().lights)
        self.assertFalse(RainSettings.from_values(lights=False).lights)
        self.assertFalse(RainSettings.from_values(lights="False").lights)


class Plumbing(unittest.TestCase):

    def test_setting_reaches_the_render(self):
        from studio.legacy import install
        install()
        from studio.stages.splatting import settings as S
        from core.splatting.batch_processing import ProcessingSettings
        from core.common.sidecar_manager import SidecarConfigManager
        self.assertIs(S.DEFAULTS["rain_lights"], True)
        self.assertIs(ProcessingSettings.rain_lights, True)
        self.assertIn("rain_lights", SidecarConfigManager.KEEP_GUI_WHEN_MISSING)


if __name__ == "__main__":
    unittest.main()
