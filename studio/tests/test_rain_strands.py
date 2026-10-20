"""Splatting's Rain: Keep webs & wires out (user, 2026-10-07: a web-like strand
read as rain). A long thin bright line is kept out of the rain; short faint
streaks and round glitter aren't; the setting (on by default) reaches the
render, a clip's sidecar over it, and the preview's frame-buffer key. CPU only.

    python -m unittest studio.tests.test_rain_strands
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.splatting.rain import RainExtractor, RainSettings, strands  # noqa: E402


def _scene(seed=5):
    rng = np.random.default_rng(seed)
    f = np.full((540, 960, 3), 30, np.uint8)
    cv2.line(f, (300, 20), (420, 520), (200, 200, 200), 3, cv2.LINE_AA)          # the web: long, bright
    for _ in range(300):                                                            # rain: short, faint
        x, y = int(rng.integers(500, 940)), int(rng.integers(0, 500))
        cv2.line(f, (x, y), (x + 2, y + int(rng.integers(10, 25))), (55, 55, 55), 1, cv2.LINE_AA)
    for _ in range(600):                                                            # wet-ground glitter
        x, y = int(rng.integers(0, 280)), int(rng.integers(300, 540))
        cv2.circle(f, (x, y), int(rng.integers(1, 3)), (120, 120, 120), -1)
    return f


class Strands(unittest.TestCase):

    def test_the_line_and_only_the_line(self):
        f = _scene()
        m = strands(f, 0.5)
        line = np.zeros(m.shape, np.uint8)
        cv2.line(line, (300, 20), (420, 520), 1, 3)
        self.assertGreater(m[line > 0].mean(), 0.9, "the web's length kept out")
        self.assertLess(m[:, 500:].mean(), 0.002, "short faint streaks stay rain")
        self.assertLess(m[300:, :280].mean(), 0.01, "round glitter stays")

    def test_removed_from_the_rain(self):
        frames = np.stack([_scene(s) for s in range(7)])
        on = RainExtractor(RainSettings.from_values(mode="static", strands=True, lights=False), 960, device="cpu")
        off = RainExtractor(RainSettings.from_values(mode="static", strands=False, lights=False), 960, device="cpu")
        line = np.zeros((540, 960), np.uint8)
        cv2.line(line, (300, 20), (420, 520), 1, 3)
        frames[3] = frames[3].copy()
        cv2.line(frames[3], (310, 20), (430, 520), (220, 220, 220), 3, cv2.LINE_AA)   # moved: residual
        # (Keep Lights Out takes a line this bright too: off here, so this is Strands alone)
        ron, _ = on.extract_lengths(frames, [3])[0]
        roff, _ = off.extract_lengths(frames, [3])[0]
        near = cv2.dilate(line, np.ones((25, 25), np.uint8)) > 0
        self.assertGreater(roff.sum(-1)[near].sum(), 1.0, "off: the moved web reads as rain")
        self.assertLess(ron.sum(-1)[near].sum(), 0.2 * roff.sum(-1)[near].sum(), "on: kept out")

    def test_defaults(self):
        self.assertTrue(RainSettings.from_values().strands)
        self.assertTrue(RainSettings.from_values(strands=None).strands)
        self.assertFalse(RainSettings.from_values(strands=False).strands)
        self.assertFalse(RainSettings.from_values(strands="False").strands)


class Plumbing(unittest.TestCase):

    def test_setting_reaches_the_render(self):
        from studio.legacy import install
        install()
        from studio.stages.splatting import settings as S
        from core.splatting.batch_processing import ProcessingSettings, _saved_or
        self.assertIs(S.DEFAULTS["rain_strands"], True)
        vals = dict(S.DEFAULTS)
        vals["rain_strands"] = False
        ps = ProcessingSettings.from_config(S.config_for_run(vals))
        self.assertIs(bool(ps.rain_strands), False)
        self.assertEqual(_saved_or({"rain_strands": True}, "rain_strands", False), True)
        self.assertEqual(_saved_or({"rain_strands": None}, "rain_strands", False), False)

    def test_in_the_frame_buffer_key(self):
        from core.ui.preview_buffer import PreviewFrameBuffer
        buf = PreviewFrameBuffer(max_frames=2, max_memory_mb=1)
        a = buf._generate_signature_from_params({"rain_strands": True}, "x.mp4")
        b = buf._generate_signature_from_params({"rain_strands": False}, "x.mp4")
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()
