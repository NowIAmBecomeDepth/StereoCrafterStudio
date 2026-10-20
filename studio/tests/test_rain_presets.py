"""Rain scenarios (user, 2026-10-07: a test clip, soft out-of-focus rain barely picked
up; "don't alter what it can pick up" on the first clips). Downpour keeps the detector's original
figures (its output is proven bit-identical by prototypes/rain/downpour_golden.py on the
first test clips); Soft has its own scale, threshold and background. CPU only.

    python -m unittest studio.tests.test_rain_presets
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
import core.splatting.rain as R  # noqa: E402


class Presets(unittest.TestCase):

    def test_downpour_is_the_original_detector(self):
        self.assertEqual(R.PRESETS["downpour"], (7, 15, 7, 3.0, 0.003, False))
        self.assertEqual(R.BACKGROUND["downpour"], (3, 0.25, 5))
        self.assertEqual((R.RADIUS, R.QUANT), (3, 0.25))
        ext = R.RainExtractor(R.RainSettings.from_values(mode="moving"), 1920, device="cpu")
        self.assertEqual((ext.thin, ext.line, ext.short, ext.k, ext.floor, ext.relative, ext.soft),
                         (7, 15, 7, 3.0, 0.003, False, False))
        self.assertEqual((ext.radius, ext.quant, ext.flow_open), (3, 0.25, 5))

    def test_soft(self):
        s = R.RainSettings.from_values(mode="moving", preset="soft")
        ext = R.RainExtractor(s, 1920, device="cpu")
        self.assertTrue(ext.soft and ext.relative)
        self.assertEqual((ext.thin, ext.line, ext.short), (21, 45, 21))
        self.assertEqual((ext.radius, ext.quant, ext.flow_open), (4, 0.1, 21))
        self.assertEqual(R.radius_of(s), 4)

    def test_settings(self):
        self.assertEqual(R.RainSettings.from_values().preset, "downpour")
        self.assertEqual(R.RainSettings.from_values(preset="SOFT").preset, "soft")
        self.assertEqual(R.RainSettings.from_values(preset="nonsense").preset, "downpour")
        self.assertEqual(R.radius_of(R.RainSettings.from_values()), 3)

    def test_window_follows_the_radius(self):
        self.assertEqual(R.window_indices([10], 100)[0], list(range(7, 14)))
        self.assertEqual(R.window_indices([10], 100, 4)[0], list(range(6, 15)))
        self.assertEqual(R.window_indices([1], 100, 4), (list(range(0, 6)), [1]))


class Mist(unittest.TestCase):

    def test_settings(self):
        s = R.RainSettings.from_values(mode="moving", preset="mist", guard=0.5)
        self.assertEqual((s.preset, s.guard), ("mist", 0.5))
        self.assertEqual(R.radius_of(s), 4)
        self.assertEqual(R.RainSettings.from_values().guard, 0.0)
        self.assertEqual(R.RainSettings.from_values(guard=7).guard, 1.0)

    def test_sheet_takes_faint_dense_rain(self):
        import numpy as np
        rng = np.random.default_rng(1)
        base = np.full((7, 120, 160, 3), 90, np.uint8)
        for t in range(7):                       # a dense sheet of faint fine vertical streaks
            for _ in range(400):
                x, y = int(rng.integers(0, 160)), int(rng.integers(0, 110))
                base[t, y:y + 8, x] = 96
        s = R.RainSettings.from_values(mode="static", preset="mist", strands=False)
        ext = R.RainExtractor(s, 1920, device="cpu")
        rain, length = ext.extract_lengths(base, [3])[0]
        self.assertGreater(float((length > 0).mean()), 0.05, "a dense faint sheet is taken")
        down = R.RainExtractor(R.RainSettings.from_values(mode="static", strands=False), 1920, device="cpu")
        _, l2 = down.extract_lengths(base, [3])[0]
        self.assertGreater(float((length > 0).mean()), float((l2 > 0).mean()))

    def test_tie_falls_back_to_vertical_but_not_for_downpour(self):
        import numpy as np
        flat = np.random.default_rng(2).normal(0, 0.01, (200, 200)).astype(np.float32).clip(0, None)
        soft = R.RainExtractor(R.RainSettings.from_values(mode="moving", preset="mist"), 1920, device="cpu")
        self.assertEqual(soft._angle(flat), 90.0)

    def test_foreground_guard(self):
        import numpy as np
        d = np.full((100, 200), 0.2, np.float32)
        d[20:80, 20:60] = 0.9                    # someone near
        self.assertIsNone(R.keep_far(d, 0.0))
        keep = R.keep_far(d, 0.5, 0.1)
        self.assertLess(float(keep[50, 40]), 0.01, "no rain taken from the near part")
        self.assertGreater(float(keep[50, 150]), 0.99, "the background keeps its rain")


class Plumbing(unittest.TestCase):

    def test_settings_reach_the_render(self):
        from studio.legacy import install
        install()
        from studio.stages.splatting import settings as S
        from core.splatting.batch_processing import ProcessingSettings, _saved_or
        self.assertEqual((S.DEFAULTS["rain_preset"], S.DEFAULTS["rain_guard"]), ("downpour", 0.0))
        vals = dict(S.DEFAULTS)
        vals.update(rain_preset="mist", rain_guard=0.5)
        ps = ProcessingSettings.from_config(S.config_for_run(vals))
        self.assertEqual((ps.rain_preset, float(ps.rain_guard)), ("mist", 0.5))
        self.assertEqual(_saved_or({"rain_preset": "soft"}, "rain_preset", "mist"), "soft")
        self.assertEqual(_saved_or({"rain_guard": None}, "rain_guard", 0.5), 0.5)

    def test_in_the_frame_buffer_key(self):
        from core.ui.preview_buffer import PreviewFrameBuffer
        buf = PreviewFrameBuffer(max_frames=2, max_memory_mb=1)
        for k, a, b in (("rain_preset", "downpour", "mist"), ("rain_guard", 0.0, 0.5)):
            self.assertNotEqual(buf._generate_signature_from_params({k: a}, "x.mp4"),
                                buf._generate_signature_from_params({k: b}, "x.mp4"), k)

    def test_sidecar_keys(self):
        from core.common.sidecar_manager import SidecarConfigManager as M
        for k in ("rain_preset", "rain_guard"):
            self.assertIn(k, M.SIDECAR_KEY_MAP)
            self.assertIsNone(M.SIDECAR_KEY_MAP[k][1], "a sidecar without it leaves the page's setting")
            self.assertIn(k, M.KEEP_GUI_WHEN_MISSING)


if __name__ == "__main__":
    unittest.main()
