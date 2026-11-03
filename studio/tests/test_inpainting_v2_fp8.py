"""Inpainting V2's FP8 Math (user, 2026-10-31; studio/stages/inpainting_v2/fp8_matmul.py):

  * switched on, a quanto int8 linear (what MMGP streams) multiplies in FP8: close to the normal
    result (FP8's own rounding, a few % on random data) and counted; a plain bf16 weight, a small
    batch and anything switched off go the normal way, unchanged;
  * the setting is off by default and a bool;
  * the chunk checkpoints' signature carries it only while it's on (earlier checkpoints still match).

The maths need an RTX 40 / 50 card (FP8 matmuls) and skip elsewhere.

    .venv\\Scripts\\python.exe -m unittest studio.tests.test_inpainting_v2_fp8 -v
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data

import torch  # noqa: E402
import torch.nn.functional as F  # noqa: E402

from studio.stages.inpainting_v2 import fp8_matmul as M  # noqa: E402
from studio.stages.inpainting_v2 import resume as R  # noqa: E402
from studio.stages.inpainting_v2 import settings as S  # noqa: E402


def _fp8_card() -> bool:
    return torch.cuda.is_available() and torch.cuda.get_device_capability() >= (8, 9)


@unittest.skipUnless(_fp8_card(), "needs an RTX 40 / 50 series card")
class Fp8LinearTest(unittest.TestCase):
    def setUp(self):
        from optimum.quanto import freeze, qint8, quantize
        torch.manual_seed(0)
        lin = torch.nn.Linear(512, 768).cuda().to(torch.bfloat16)
        self.w = lin.weight.detach().clone()
        self.x = torch.randn(2, 64, 512, device="cuda", dtype=torch.bfloat16)
        with torch.no_grad():
            self.ref = F.linear(self.x, self.w, lin.bias).float()
            m = torch.nn.Sequential(lin)
            quantize(m, weights=qint8)
            freeze(m)
        self.q = m[0]

    def tearDown(self):
        M.uninstall()

    def err(self, y):
        return ((y.float() - self.ref).norm() / self.ref.norm()).item()

    def test_on_an_int8_linear_runs_fp8(self):
        M.set_enabled(True)
        n = M.calls
        with torch.no_grad():
            y = F.linear(self.x, self.q.qweight, self.q.bias)
        self.assertEqual(M.calls, n + 1)
        self.assertEqual(y.shape, (2, 64, 768))
        self.assertEqual(y.dtype, torch.bfloat16)
        self.assertLess(self.err(y), 0.06)

    def test_off_the_normal_way(self):
        M.set_enabled(True)
        M.set_enabled(False)
        n = M.calls
        with torch.no_grad():
            y = F.linear(self.x, self.q.qweight, self.q.bias)
            want = self.q.qweight.dequantize()
        self.assertEqual(M.calls, n)
        self.assertLess(self.err(y), 0.02)                  # int8's own error only
        self.assertIsNotNone(want)

    def test_plain_weights_and_small_batches_untouched(self):
        M.set_enabled(True)
        n = M.calls
        with torch.no_grad():
            plain = F.linear(self.x, self.w)
            small = F.linear(self.x[:, :3], self.q.qweight)     # 6 rows: not a multiple of 16
        self.assertEqual(M.calls, n)
        self.assertTrue(torch.equal(plain, torch.matmul(self.x, self.w.t())) or self.err(plain) < 1e-2)
        self.assertEqual(small.shape, (2, 3, 768))

    def test_uninstall_restores_linear(self):
        orig = F.linear
        M.set_enabled(True)
        self.assertIsNot(torch.nn.functional.linear, orig)
        M.uninstall()
        self.assertIs(torch.nn.functional.linear, orig)
        self.assertFalse(M.enabled())


class SettingTest(unittest.TestCase):
    def test_off_by_default_and_a_bool(self):
        self.assertIs(S.DEFAULTS["use_fp8_matmul"], False)
        self.assertIs(S.normalise({"use_fp8_matmul": "true"})["use_fp8_matmul"], True)
        self.assertIs(S.normalise({})["use_fp8_matmul"], False)

    def test_refused_without_an_fp8_card(self):
        saved = M.supported
        M.supported = lambda: False
        try:
            self.assertFalse(M.set_enabled(True))
            self.assertFalse(M.enabled())
        finally:
            M.supported = saved


class SignatureTest(unittest.TestCase):
    def sig(self):
        return R.signature(__file__, frames_chunk=81, frames_overlap=10, tile_num=2, tile_overlap=64,
                           inference_steps=5, seed=0, prompt="", is_dual_input=True, total_frames=81, model={})

    def tearDown(self):
        M._enabled = False

    def test_marked_only_while_on(self):
        M._enabled = False
        off = self.sig()
        self.assertNotIn("fp8_matmul", off)
        M._enabled = True
        on = self.sig()
        self.assertTrue(on.get("fp8_matmul"))
        self.assertNotEqual(on, off)


if __name__ == "__main__":
    unittest.main()
