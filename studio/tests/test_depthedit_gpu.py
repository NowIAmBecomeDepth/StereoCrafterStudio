"""core.depthedit.edit_gpu: the Depth Map Editor's edit on the GPU (user, 2026-10-31: the render back
to Splatting felt slow; ~10x on a 1920x1038 frame).

* the same frame as edit_ops's CPU code, setting by setting -- Alter (Edges whole and fractional, Grow /
  Shrink, Smooth, Contrast, Flatten, Brightness, gradients), Add, the global levels, and the steps it
  hands back to the CPU (Erase, Despeck, Fill holes, Close gaps): at the map's 10-bit codes, at most
  one code apart and on almost no pixels;
* edit_ops.apply_all uses it for a big frame, keeps a small one (a thumbnail) on the CPU, and falls
  back to the CPU when it fails.

Needs a CUDA card (skipped without one).

    python -m unittest studio.tests.test_depthedit_gpu
"""

from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
import cv2  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402

from core.depthedit import edit_ops as E  # noqa: E402

W, H = 1920, 1038


def _blob(i: int) -> np.ndarray:
    m = np.zeros((H, W), np.uint8)
    cv2.ellipse(m, (int(W * (0.25 + 0.17 * i)), int(H * (0.45 + 0.05 * i))), (int(W * 0.1), int(H * 0.28)),
                15 * i, 0, 360, 1, -1)
    m[int(H * 0.3):int(H * 0.32), :] |= (i % 2)        # a thin strip, for Edge snap to move
    return m.astype(bool)


def _frame() -> np.ndarray:
    rng = np.random.default_rng(1)
    return np.clip(cv2.GaussianBlur(rng.random((H, W), np.float32), (0, 0), 6) * 3 - 1, 0, 1).astype(np.float32)


def _cpu(v, g, layers, f=0):
    with mock.patch.object(E, "GPU_EDIT_MIN_PIXELS", 10 ** 12):
        return E.apply_all(v, g, layers, f)


GRAD = E.Gradient(enabled=True, shape="Radial", p0=(0.5, 0.44), p1=(1.68, 0.67), follow_object=True,
                  near=0.52, far=0.47, curve=1.0, keep_detail=0.5, blend="Replace")
ALTER = E.LayerSettings(edge_top=1.0, edge_bottom=7.0, edge_left=1.0, grow=5.5, edge_snap=9, feather=2.0)


@unittest.skipUnless(torch.cuda.is_available(), "needs a CUDA card")
class GpuEditMatchesCpu(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from core.depthedit import edit_gpu
        cls.G = edit_gpu
        cls.v = _frame()
        cls.masks = [_blob(i) for i in range(4)]
        cls.g0 = E.GlobalSettings()

    def same(self, layers, g=None, f=0):
        g = g or self.g0
        a = _cpu(self.v, g, layers, f)
        b = self.G.apply_all(self.v, g, layers, f)
        ca, cb = np.round(a * 1023), np.round(b * 1023)
        off = np.abs(ca - cb)
        self.assertLessEqual(off.max(), 1, "more than one 10-bit code apart")
        self.assertLess((off > 0).mean(), 1e-4, f"{(off > 0).mean() * 100:.4f}% of the pixels differ")

    def test_alter_settings(self):
        cases = {
            "as the user had it": ALTER,
            "edges, fractions both ways": replace(ALTER, edge_top=-2.5, edge_bottom=3.25, edge_left=1.5, edge_right=-4.0),
            "smooth / contrast / flatten / brightness": replace(ALTER, smooth=3.0, contrast=1.4, flatten=0.3,
                                                               brightness=0.05),
            "shrink": replace(ALTER, grow=-3.5),
            "no snap, no feather": replace(ALTER, edge_snap=0, feather=0.0),
            "half opacity": replace(ALTER, opacity=0.5),
            "radial gradient": replace(ALTER, gradient=GRAD),
            "linear gradient, only nearer": replace(ALTER, gradient=replace(GRAD, shape="Linear", blend="Only nearer")),
            "gradient added, curved": replace(ALTER, gradient=replace(GRAD, blend="Add", curve=1.7)),
        }
        for name, s in cases.items():
            with self.subTest(name):
                self.same([(s, self.masks[1])])

    def test_add_layer(self):
        for name, s in {"plain": replace(ALTER, mode="Add", gradient=GRAD),
                        "smooth / contrast / an edge": replace(ALTER, mode="Add", smooth=2.0, contrast=0.6,
                                                               edge_left=2.5)}.items():
            with self.subTest(name):
                self.same([(s, self.masks[0])])

    def test_cpu_steps_inside(self):
        for name, s in {"Erase": replace(ALTER, mode="Erase"), "Despeck": replace(ALTER, despeck_on=True),
                        "Fill holes + Close gaps": replace(ALTER, fill_holes=True, close_gaps=3.0)}.items():
            with self.subTest(name):
                self.same([(s, self.masks[1])])

    def test_four_layers_and_the_global(self):
        layers = [(replace(ALTER, mode="Add", gradient=GRAD, edge_right=-2.5, grow=1.5, edge_snap=12), self.masks[0]),
                  (ALTER, self.masks[1]), (replace(ALTER, edge_left=3.5, grow=3.0), self.masks[2]),
                  (replace(ALTER, start_frame=5), self.masks[3])]      # not on frame 0
        self.same(layers)
        self.same(layers, E.GlobalSettings(invert=True, in_black=0.1, in_white=0.9, gamma=1.3, contrast=1.1,
                                           brightness=0.02))

    def test_apply_all_uses_it_for_a_big_frame(self):
        with mock.patch.object(self.G, "apply_all", wraps=self.G.apply_all) as spy:
            E.apply_all(self.v, self.g0, [(ALTER, self.masks[1])], 0)
            self.assertEqual(spy.call_count, 1)
            small = cv2.resize(self.v, (320, 180))
            E.apply_all(small, self.g0, [(ALTER, cv2.resize(self.masks[1].astype(np.uint8), (320, 180)) > 0)], 0)
            self.assertEqual(spy.call_count, 1, "a small frame stays on the CPU")

    def test_a_failure_falls_back_to_the_cpu(self):
        want = _cpu(self.v, self.g0, [(ALTER, self.masks[1])])
        try:
            with mock.patch.object(self.G, "_apply_all", side_effect=RuntimeError("CUDA out of memory")):
                with self.assertLogs(E.logger, "WARNING"):
                    got = E.apply_all(self.v, self.g0, [(ALTER, self.masks[1])], 0)
            np.testing.assert_array_equal(got, want)
        finally:
            E._gpu_edit_failed = False


if __name__ == "__main__":
    unittest.main()
