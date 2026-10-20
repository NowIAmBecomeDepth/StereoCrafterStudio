"""Splatting's Rain: Pop, extra Pop-Out for the rain alone (user, 2026-10-07).
Pop-Out's ramp topped at the rain's own nearest plane: the nearest rain gets
all of it, farther planes less, rain behind the screen and no-rain pixels
none; the setting reaches the render (a clip's sidecar over it) and the
preview's frame-buffer key. Pop-Out's own dial now reaches 60. CPU only.

    python -m unittest studio.tests.test_rain_pop
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.splatting.rain import MAX_POP, RainSettings, rain_pop_extra  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


class Pop(unittest.TestCase):

    def depth(self):
        d = torch.zeros(1, 1, 4, 4)
        d[0, 0, 0, :4] = torch.tensor([0.0, 0.4, 0.6, 0.8])      # none, behind the screen, mid, nearest
        return d

    def test_ramp_topped_at_the_nearest_rain(self):
        e = rain_pop_extra(self.depth(), 10.0, 0.5, 20.0)
        row = e[0, 0, 0].tolist()
        self.assertEqual(row[0], 0.0, "no rain, no pop")
        self.assertEqual(row[1], 0.0, "rain behind the screen stays")
        self.assertAlmostEqual(row[3], 10.0 / 20.0, places=6, msg="the nearest rain gets all of it")
        self.assertAlmostEqual(row[2], (1 / 3) ** 2 * 0.5, places=6, msg="farther rain less (u**2)")

    def test_off(self):
        self.assertIsNone(rain_pop_extra(self.depth(), 0.0, 0.5, 20.0))
        d = self.depth() * 0.5                                      # all rain behind the screen
        self.assertIsNone(rain_pop_extra(d, 10.0, 0.5, 20.0))

    def test_settings(self):
        self.assertEqual(RainSettings.from_values().pop, 0.0)
        self.assertEqual(RainSettings.from_values(pop="12.5").pop, 12.5)
        self.assertEqual(RainSettings.from_values(pop=999).pop, MAX_POP)
        self.assertEqual(RainSettings.from_values(pop=-3).pop, 0.0)
        self.assertEqual(RainSettings.from_values(pop="x").pop, 0.0)


class Plumbing(unittest.TestCase):

    def test_setting_reaches_the_render(self):
        from studio.legacy import install
        install()
        from studio.stages.splatting import settings as S
        from core.splatting.batch_processing import ProcessingSettings, _saved_or
        vals = dict(S.DEFAULTS)
        vals["rain_pop"] = 7.5
        ps = ProcessingSettings.from_config(S.config_for_run(vals))
        self.assertEqual(float(ps.rain_pop), 7.5)
        self.assertEqual(_saved_or({"rain_pop": 2.0}, "rain_pop", 7.5), 2.0)
        self.assertEqual(_saved_or({"rain_pop": None}, "rain_pop", 7.5), 7.5)

    def test_in_the_frame_buffer_key(self):
        from core.ui.preview_buffer import PreviewFrameBuffer
        buf = PreviewFrameBuffer(max_frames=2, max_memory_mb=1)
        self.assertNotEqual(buf._generate_signature_from_params({"rain_pop": 0.0}, "x.mp4"),
                            buf._generate_signature_from_params({"rain_pop": 5.0}, "x.mp4"))

    def test_dials(self):
        qml = (REPO / "studio/qml/Studio/SplattingPage.qml").read_text(encoding="utf-8")
        pop = re.search(r'key: "pop_amount";[^}]*?to: (\d+)', qml)
        self.assertEqual(pop.group(1), "60", "Pop-Out reaches 60")
        rain = re.search(r'key: "rain_pop";[^}]*?to: (\d+)', qml)
        self.assertEqual(float(rain.group(1)), MAX_POP)


if __name__ == "__main__":
    unittest.main()
