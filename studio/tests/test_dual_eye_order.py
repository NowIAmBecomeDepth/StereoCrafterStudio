"""Inpainting V1's dual-eye clips (_splatted2D) are stacked [L0..Ln, Rn..R0]
(bug sweep 2026-10-09, A7, settled 2026-10-10 with the real model): stacked
[L0..Ln, R0..Rn], the chunk across the seam -- and the next, seeded with its last
frames -- inpainted the right eye's first frames beside the left eye's last ones,
the clip's two ends (a red-left / blue-right clip: ~7% of the left's colour in
the right eye's first 5 frames' holes, over 3 runs against a control). Reversed,
the seam joins the two views of the same moment. The right eye comes out in its
own order (checked with a frame-number strip through the real model); a
dual-eye checkpoint from the old order doesn't resume, and other clips'
signatures are unchanged.

    python -m unittest studio.tests.test_dual_eye_order
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data

ENGINE = Path(__file__).resolve().parents[1] / "stages/inpainting/engine.py"


class DualEyeOrderTest(unittest.TestCase):
    def test_stacked_reversed_and_split_back(self):
        src = ENGINE.read_text(encoding="utf-8")
        self.assertIn("torch.cat([left_warp_q, torch.flip(right_warp_q, dims=[0])], dim=0)", src)
        self.assertIn("torch.cat([left_mask_q, torch.flip(right_mask_q, dims=[0])], dim=0)", src)
        self.assertIn("right_eye_out = torch.flip(final_right_or_dual_frames[per_eye:], dims=[0])", src)

    def test_the_round_trip_keeps_the_order(self):
        left, right = torch.arange(5).float().view(5, 1, 1, 1), torch.arange(10, 15).float().view(5, 1, 1, 1)
        stacked = torch.cat([left, torch.flip(right, dims=[0])], dim=0)
        self.assertEqual(stacked.view(-1).tolist(), [0, 1, 2, 3, 4, 14, 13, 12, 11, 10])   # Ln beside Rn
        per_eye = stacked.shape[0] // 2
        self.assertTrue(torch.equal(stacked[:per_eye], left))
        self.assertTrue(torch.equal(torch.flip(stacked[per_eye:], dims=[0]), right))

    def test_only_dual_eye_signatures_change(self):
        src = ENGINE.read_text(encoding="utf-8")
        self.assertIn('**({"dual_eye_order": "right reversed"} if', src)
        self.assertIn('.endswith(("_splatted2D", "_splatted2DF")) else {})', src)


if __name__ == "__main__":
    unittest.main()
