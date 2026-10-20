"""M2S on the GPU (user, 2026-09-29): dependency_R/m2s_backend/gpu.py against
the numpy path it replaces (renderer._build_m2s_numpy_outputs), on random
frames and depth -- the same pixels win, the same holes, float32 rounding at
most. And the fixer's component filter (a lookup, not a pass per component)
keeps the same pixels."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import numpy as np  # noqa: E402
import torch  # noqa: E402

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from dependency_R.m2s_backend import gpu as G  # noqa: E402
from dependency_R.m2s_backend import renderer as R  # noqa: E402

DEV = "cuda" if torch.cuda.is_available() else "cpu"


def _inputs(seed, b=2, h=37, w=91):
    rng = np.random.default_rng(seed)
    left = rng.random((b, h, w, 3), dtype=np.float32)
    # smooth-ish depth with steps, so pixels collide and holes open
    depth = np.clip(rng.random((b, 1, h, w), dtype=np.float32) * 0.2
                    + np.repeat(np.linspace(0, 1, w, dtype=np.float32)[None, None, None], h, axis=2)
                    + (np.arange(w) > w // 2).astype(np.float32) * 0.3, 0, 1.2).astype(np.float32)
    return left, depth


class M2SGpuTest(unittest.TestCase):
    CASES = [(0.5, 6.0), (0.0, 10.0), (1.0, 3.3), (0.37, 12.7), (0.5, 0.0)]

    def test_the_gpu_warp_is_the_numpy_warp(self):
        for seed, (conv, disp) in enumerate(self.CASES):
            left, depth = _inputs(seed)
            ref_w, ref_m = R._build_m2s_numpy_outputs(left, depth, conv, disp)
            got_w, got_m = G.build_m2s_outputs_torch(torch.from_numpy(left).permute(0, 3, 1, 2).to(DEV),
                                                     torch.from_numpy(depth).to(DEV), conv, disp)
            got_w = got_w.permute(0, 2, 3, 1).cpu().numpy()
            got_m = got_m.cpu().numpy()
            with self.subTest(conv=conv, disp=disp):
                np.testing.assert_array_equal(got_m, ref_m)
                self.assertLessEqual(float(np.abs(got_w - ref_w).max()), 1e-6)

    def test_the_component_filter_keeps_the_same_pixels(self):
        rng = np.random.default_rng(9)
        cand = (rng.random((120, 200)) > 0.93).astype(np.uint8)
        cand[10:14, 20:90] = 1                     # a long thin one
        cand[50:80, 50:80] = 1                     # a solid block
        new = R._keep_thin_components(cand, 1024, 3, 0.55)
        old = R._keep_thin_components_loop(cand, 1024, 3, 0.55)
        np.testing.assert_array_equal(new, old)


class DepthPastOneTest(unittest.TestCase):
    """bug sweep 2026-10-09: M2S clamped depth to 1, so Boost, Pop-Out and Particles
    in front (depth past 1 on purpose) all landed on one plane. SC never clamped."""

    @staticmethod
    def _landing(d, gpu):
        w, conv, disp = 64, 0.0, 6.0                      # scale 12 px per unit of depth
        left = np.zeros((1, 4, w, 3), np.float32)
        left[:, :, 40] = 1.0
        depth = np.zeros((1, 1, 4, w), np.float32)
        depth[:, :, :, 40] = d
        if gpu:
            out, _ = G.build_m2s_outputs_torch(torch.from_numpy(left).permute(0, 3, 1, 2).to(DEV),
                                               torch.from_numpy(depth).to(DEV), conv, disp)
            row = out.permute(0, 2, 3, 1).cpu().numpy()[0, 0, :, 0]
        else:
            out, _ = R._build_m2s_numpy_outputs(left, depth, conv, disp)
            row = out[0, 0, :, 0]
        return int(np.argmax(row))

    def test_depth_past_one_lands_further(self):
        for gpu in (False, True):
            with self.subTest(gpu=gpu):
                self.assertEqual(self._landing(1.0, gpu), 40 - 12)
                self.assertEqual(self._landing(1.5, gpu), 40 - 18)

    def test_negative_depth_is_still_floored(self):
        self.assertEqual(self._landing(-0.5, False), 40)


class DualEyeM2STest(unittest.TestCase):
    """audit 2026-09-28 (minor): dual-eye ignored the M2S backend and always
    warped with SC. Each eye now warps with M2S: the right as the single-eye
    path does, the left mirrored (flip, warp, flip back) with its share."""

    def _warp(self, backend, **kw):
        from core.splatting.forward_warp import ForwardWarpStereo, execute_forward_warp
        g = torch.Generator().manual_seed(3)
        src = torch.rand(2, 3, 48, 96, generator=g).to(DEV)
        dep = torch.nn.functional.interpolate(torch.rand(2, 1, 6, 12, generator=g), size=(48, 96),
                                              mode="bilinear").to(DEV)
        proj = ForwardWarpStereo(occlu_map=True).to(DEV)
        return src, execute_forward_warp(proj, src, dep, target_width=96, max_disp=30.0,
                                         zero_disparity_anchor_val=0.5, splat_backend=backend, **kw)

    def test_all_the_disparity_on_the_right_eye_is_the_single_eye_m2s_warp(self):
        _src, (right, _m) = self._warp("M2S")
        _src, (left, _lm, dual_right, _rm) = self._warp("M2S", dual_eye=True, dual_eye_bias=1.0)
        torch.testing.assert_close(dual_right, right, rtol=0, atol=1e-6)

    def test_the_left_eye_is_the_mirror_of_a_right_warp(self):
        from core.splatting.forward_warp import ForwardWarpStereo, execute_forward_warp
        g = torch.Generator().manual_seed(4)
        src = torch.rand(2, 3, 48, 96, generator=g).to(DEV)
        dep = torch.nn.functional.interpolate(torch.rand(2, 1, 6, 12, generator=g), size=(48, 96),
                                              mode="bilinear").to(DEV)
        proj = ForwardWarpStereo(occlu_map=True).to(DEV)
        kw = dict(target_width=96, max_disp=30.0, zero_disparity_anchor_val=0.5, splat_backend="M2S",
                  debug_task_name="Preview")           # no edge fill: compare the warps themselves
        left, left_mask, _r, _rm = execute_forward_warp(proj, src, dep, dual_eye=True, dual_eye_bias=0.0, **kw)
        flip = lambda t: torch.flip(t, dims=[-1])  # noqa: E731
        mirrored, mirrored_mask = execute_forward_warp(proj, flip(src), flip(dep), **kw)
        torch.testing.assert_close(left, flip(mirrored), rtol=0, atol=1e-6)
        torch.testing.assert_close(left_mask, flip(mirrored_mask), rtol=0, atol=0)


if __name__ == "__main__":
    unittest.main()
