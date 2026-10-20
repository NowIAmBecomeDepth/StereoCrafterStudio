"""Detect Text on the GPU (user, 2026-10-01): core.splatting.submask_gpu's mask
against the CPU's compute_mask, on synthetic frames with drawn text -- every
colour mode, Refine on and off, radii, Hard and Soft, the rectangle, invert.
Hard masks must match exactly; Soft within two grey levels (OpenCV blurs
8-bit in fixed point) and a tiny average difference. Needs CUDA (skips
without it); small frames, a few MB of VRAM.

    python -m unittest studio.tests.test_submask_gpu
"""

from __future__ import annotations

import itertools
import sys
import unittest
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import torch  # noqa: E402

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.splatting import submask_gpu  # noqa: E402
from core.ui.subtitle_mask_editor import DEFAULTS, compute_mask  # noqa: E402


def _frame(seed: int, h=270, w=480) -> np.ndarray:
    rng = np.random.default_rng(seed)
    f = (rng.random((h, w, 3)) * 140).astype(np.uint8)                   # a dim, noisy picture
    f = cv2.GaussianBlur(f, (0, 0), 3)
    cv2.circle(f, (60, 60), 40, (250, 250, 250), -1)                      # a bright highlight, no text
    cv2.rectangle(f, (300, 20), (470, 60), (40, 200, 230), -1)           # a yellow-ish block
    for i, (txt, col) in enumerate((("Subtitle text here", (255, 255, 255)),
                                    ("Yellow caption", (30, 230, 240)),
                                    ("grey-ish words", (190, 190, 190)))):
        cv2.putText(f, txt, (20 + 7 * i, 180 + 30 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.9, col, 2, cv2.LINE_AA)
    return f


@unittest.skipUnless(submask_gpu.available(), "needs CUDA")
class GpuMatchesCpu(unittest.TestCase):
    def check(self, frame, **kw):
        s = dict(DEFAULTS)
        s.update(kw)
        cpu = compute_mask(frame, s)
        gpu = submask_gpu.compute_mask_gpu(frame, s)
        self.assertEqual(gpu.shape, cpu.shape)
        self.assertEqual(gpu.dtype, np.uint8)
        diff = np.abs(gpu.astype(int) - cpu.astype(int))
        if s["edge_mode"] == "Soft":
            self.assertLessEqual(int(diff.max()), 2, kw)
            self.assertLess(float(diff.mean()), 0.05, kw)           # rounding in the blur, not a different mask
        else:
            self.assertEqual(int(diff.max()), 0, (kw, int((diff > 0).sum())))

    def test_every_mode(self):
        for seed in (0, 1):
            f = _frame(seed)
            for color, refine, r, edge_mode in itertools.product(
                    ("Any", "White", "Yellow"), (True, False), (0, 2, 5), ("Hard", "Soft")):
                with self.subTest(seed=seed, color=color, refine=refine, r=r, edge_mode=edge_mode):
                    self.check(f, color=color, refine=refine, r=r, edge_mode=edge_mode)

    def test_thresholds_rect_invert(self):
        f = _frame(2)
        for core, edge in ((200, 165), (230, 120), (150, 200), (255, 0)):
            with self.subTest(core=core, edge=edge):
                self.check(f, core=core, edge=edge)
        self.check(f, rect=(10, 150, 400, 260))
        self.check(f, rect=(10, 150, 400, 260), invert=True, edge_mode="Soft", r=3)

    def test_a_long_winding_component(self):
        # the hysteresis must reach along a long thin edge path from one core pixel
        f = np.zeros((200, 300, 3), np.uint8)
        pts = np.array([[10, 10], [290, 10], [290, 190], [10, 190], [10, 40], [260, 40], [260, 160], [40, 160]])
        cv2.polylines(f, [pts.reshape(-1, 1, 2)], False, (180, 180, 180), 1)
        f[160, 40] = (255, 255, 255)                                          # the core pixel at the far end
        self.check(f, core=200, edge=165, r=0)
        self.check(f, core=200, edge=165, r=0, color="White")

    def test_hd_frame(self):
        f = cv2.resize(_frame(3), (1920, 1080), interpolation=cv2.INTER_NEAREST)
        self.check(f)
        self.check(f, edge_mode="Soft", r=4)


class EncoderArgs(unittest.TestCase):
    def test_cpu_fallback(self):
        self.assertEqual(submask_gpu.encoder_args(False)[:2], ["-c:v", "libx264"])


if __name__ == "__main__":
    unittest.main()
