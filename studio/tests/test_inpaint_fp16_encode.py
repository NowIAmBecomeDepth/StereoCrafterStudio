"""Inpainting V1 encodes in the VAE's own fp16 (user, 2026-10-12): SVD's force_upcast made every
encode switch the VAE to fp32 -- 13.5 s of a 112 s chunk, 7.7 s in fp16 with the picture moving 0.12
of a level. Latents that overflow are encoded again in fp32, and the VAE always ends back in fp16.
A stand-in VAE records the dtype it encoded in; no weights, no GPU."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first
import torch  # noqa: E402

from pipelines import stereo_video_inpainting as P  # noqa: E402


class FakeVAE(torch.nn.Module):
    """dtype follows its one weight, like the real VAE; overflow=True makes fp16 latents inf."""

    def __init__(self, overflow=False, fail_fp32=False):
        super().__init__()
        self.w = torch.nn.Parameter(torch.ones(1, dtype=torch.float16), requires_grad=False)
        self.config = SimpleNamespace(force_upcast=True)
        self.overflow, self.fail_fp32 = overflow, fail_fp32
        self.encoded_in = []          # (frames dtype, VAE dtype) per encode

    @property
    def dtype(self):
        return self.w.dtype

    def encode(self, frames):
        self.encoded_in.append((frames.dtype, self.dtype))
        if self.dtype == torch.float32 and self.fail_fp32:
            raise RuntimeError("out of memory")
        if self.dtype == torch.float16 and self.overflow:
            return torch.full((1, 4), float("inf"), dtype=frames.dtype)
        return torch.zeros(1, 4, dtype=frames.dtype)


FRAMES = torch.rand(3, 3, 8, 8)        # float32, as the pipeline's preprocess hands them over


class Fp16EncodeTest(unittest.TestCase):
    def test_encodes_in_fp16_without_switching_the_vae(self):
        vae = FakeVAE()
        out = P.encode_frames_upcast_on_overflow(vae, vae.encode, FRAMES)
        self.assertEqual(vae.encoded_in, [(torch.float16, torch.float16)])   # one encode, both fp16
        self.assertEqual(out.dtype, torch.float16)
        self.assertEqual(vae.dtype, torch.float16)

    def test_overflow_encodes_again_in_fp32_and_ends_in_fp16(self):
        vae = FakeVAE(overflow=True)
        out = P.encode_frames_upcast_on_overflow(vae, vae.encode, FRAMES)
        self.assertEqual(vae.encoded_in, [(torch.float16, torch.float16), (torch.float32, torch.float32)])
        self.assertTrue(bool(torch.isfinite(out).all()))
        self.assertEqual(vae.dtype, torch.float16)

    def test_a_failing_fp32_encode_still_leaves_the_vae_in_fp16(self):
        vae = FakeVAE(overflow=True, fail_fp32=True)
        with self.assertRaises(RuntimeError):
            P.encode_frames_upcast_on_overflow(vae, vae.encode, FRAMES)
        self.assertEqual(vae.dtype, torch.float16)

    def test_a_vae_without_force_upcast_keeps_its_latents(self):
        vae = FakeVAE(overflow=True)
        vae.config.force_upcast = False
        out = P.encode_frames_upcast_on_overflow(vae, vae.encode, FRAMES)
        self.assertEqual(len(vae.encoded_in), 1)
        self.assertFalse(bool(torch.isfinite(out).all()))

    def test_the_pipeline_encodes_through_it(self):
        src = Path(P.__file__).read_text(encoding="utf-8")
        call = src[src.index("    def __call__("):src.index("def _resize_with_antialiasing")]
        self.assertIn("encode_frames_upcast_on_overflow(", call)
        self.assertNotIn("self.vae.to(dtype=torch.float32)", call)    # no fp32 switch for every encode


if __name__ == "__main__":
    unittest.main()
