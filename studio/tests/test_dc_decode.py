"""DepthCrafter's decode, out of GPU memory (bug sweep 2026-10-14c): 8 frames a decode chunk
(2026-10-11) doubled the decode's peak against the 4 before -- too much for a smaller card ended
the clip. A chunk that runs out of memory is decoded in halves instead. No GPU: a stand-in VAE
that "runs out" above 2 frames."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
import torch  # noqa: E402
from depthcrafter.depth_crafter_ppl import DepthCrafterPipeline  # noqa: E402


class _SmallVae:
    """Decodes a latent frame to a 3-channel picture of its own value; more than `most` frames at
    once is out of memory."""

    def __init__(self, most):
        self.most = most
        self.config = SimpleNamespace(scaling_factor=1.0)
        self.sizes = []

    def forward(self, sample, num_frames=None):
        return sample

    def decode(self, chunk, num_frames=None):
        self.sizes.append(chunk.shape[0])
        if chunk.shape[0] > self.most:
            raise torch.OutOfMemoryError("stand-in")
        v = chunk[:, :1].mean(dim=(2, 3), keepdim=True)              # one value per frame
        return SimpleNamespace(sample=(v * 2 - 1).expand(-1, 3, 4, 4))


class DecodeFallbackTest(unittest.TestCase):
    def test_an_out_of_memory_chunk_is_decoded_in_halves(self):
        vae = _SmallVae(most=2)
        pipe = SimpleNamespace(vae=vae)
        pipe._decode_chunk = lambda chunk, a: DepthCrafterPipeline._decode_chunk(pipe, chunk, a)
        values = torch.linspace(0.1, 0.9, 8)
        latents = values.view(1, 8, 1, 1, 1).expand(1, 8, 4, 2, 2).clone()
        out = DepthCrafterPipeline.decode_latents_to_gray_cpu(pipe, latents, 8, decode_chunk_size=8)
        self.assertEqual(out.shape[0], 8)
        self.assertTrue(torch.allclose(torch.from_numpy(out[:, 0, 0]), values, atol=1e-5), out[:, 0, 0])
        self.assertIn(8, vae.sizes, "the whole chunk was tried first")
        self.assertTrue(all(n <= 2 for n in vae.sizes if n != 8 and n != 4), vae.sizes)

    def test_a_single_frame_out_of_memory_still_raises(self):
        vae = _SmallVae(most=0)
        pipe = SimpleNamespace(vae=vae)
        pipe._decode_chunk = lambda chunk, a: DepthCrafterPipeline._decode_chunk(pipe, chunk, a)
        latents = torch.zeros(1, 2, 4, 2, 2)
        with self.assertRaises(torch.OutOfMemoryError):
            DepthCrafterPipeline.decode_latents_to_gray_cpu(pipe, latents, 2, decode_chunk_size=2)


if __name__ == "__main__":
    unittest.main()
