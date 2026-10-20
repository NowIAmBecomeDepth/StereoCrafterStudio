"""The forward warp's occlusion weights past a ~255 px disparity span (bug
sweep 2026-10-09 #15): 1.414 ** span overflowed float32 and the foreground
came out NaN -- a 3840-wide render at Disparity 67 or more. A wider span is
now compressed; up to 240 px the result is exactly what it was.

Needs CUDA (the warp kernel); tiny tensors.

    .venv\\Scripts\\python.exe -m unittest studio.tests.test_warp_span -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data

try:
    import torch  # noqa: E402
    CUDA = torch.cuda.is_available()
except Exception:  # pragma: no cover
    CUDA = False

W, H = 800, 8
BG, FG = 0.2, 0.9


@unittest.skipUnless(CUDA, "needs CUDA")
class WideSpanTests(unittest.TestCase):
    def warp(self, fg_disp):
        """A grey background at disparity 0, a bright stripe at 500..520 with
        `fg_disp`: it lands on the background at 500 - fg_disp."""
        from core.splatting.forward_warp import ForwardWarpStereo
        im = torch.full((1, 3, H, W), BG, device="cuda")
        im[..., 500:520] = FG
        disp = torch.zeros((1, 1, H, W), device="cuda")
        disp[..., 500:520] = float(fg_disp)
        res, _occ = ForwardWarpStereo(occlu_map=True).cuda()(im, disp)
        return res, 500 - fg_disp

    def test_a_300_px_span_is_finite_and_the_nearest_wins(self):
        res, at = self.warp(300)
        self.assertFalse(torch.isnan(res).any().item(), "NaN in the warp")
        landed = res[0, :, :H - 1, at + 2:at + 18]     # (the kernel leaves the last row empty, always)
        self.assertGreater(float(landed.min()), FG - 0.01, "the background showed through the foreground")

    def test_up_to_the_cap_nothing_changes(self):
        from core.splatting import forward_warp as FW
        capped, _ = self.warp(200)
        real = FW._WEIGHT_SPAN
        FW._WEIGHT_SPAN = 1e9                     # no compression: the weights as they always were
        try:
            uncapped, _ = self.warp(200)
        finally:
            FW._WEIGHT_SPAN = real
        self.assertTrue(torch.equal(capped, uncapped))


if __name__ == "__main__":
    unittest.main()
