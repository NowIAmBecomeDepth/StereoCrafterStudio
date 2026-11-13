"""The PyTorch forward warp's memory (tester, 2026-11-12): its flat
[B*C*H*W] int64 indices made a 4K Full-Resolution batch of 10 ask for ~22 GB,
and a 12 GB card ran out mid-render. It now warps a few frames at a time with
one index per pixel; the result is the same.

Needs CUDA for the memory test; small tensors otherwise.

    .venv\Scripts\python.exe -m unittest studio.tests.test_warp_memory -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data

import torch  # noqa: E402

from dependency.forward_warp_pytorch import forward_warp_pytorch as FWP  # noqa: E402

CUDA = torch.cuda.is_available()


def reference(im0, flow):
    """The bilinear warp written out pixel by pixel, as the CUDA kernel does it."""
    B, C, H, W = im0.shape
    out = torch.zeros_like(im0)
    for b in range(B):
        for y in range(H):
            for x in range(W):
                xd = x + float(flow[b, y, x, 0])
                yd = y + float(flow[b, y, x, 1])
                xf, yf = int(torch.floor(torch.tensor(xd))), int(torch.floor(torch.tensor(yd)))
                if xf < 0 or xf + 1 >= W or yf < 0 or yf + 1 >= H:
                    continue
                ax, bx = (xf + 1) - xd, xd - xf
                ay, by = (yf + 1) - yd, yd - yf
                for yy, xx, w in ((yf, xf, ax * ay), (yf, xf + 1, bx * ay),
                                  (yf + 1, xf, ax * by), (yf + 1, xf + 1, bx * by)):
                    out[b, :, yy, xx] += im0[b, :, y, x] * w
    return out


class WarpResultTests(unittest.TestCase):
    def test_chunked_warp_matches_the_kernel(self):
        torch.manual_seed(1)
        B, C, H, W = 3, 3, 9, 14
        im = torch.rand(B, C, H, W, dtype=torch.float64)
        flow = torch.zeros(B, H, W, 2, dtype=torch.float64)
        flow[..., 0] = (torch.rand(B, H, W, dtype=torch.float64) - 0.5) * 8
        flow[0, ..., 1] = (torch.rand(H, W, dtype=torch.float64) - 0.5) * 3
        real = FWP._CHUNK_PIXELS
        FWP._CHUNK_PIXELS = H * W                    # one frame per chunk
        try:
            got = FWP.forward_warp()(im, flow.contiguous())
        finally:
            FWP._CHUNK_PIXELS = real
        self.assertTrue(torch.allclose(got, reference(im, flow), atol=1e-12))


@unittest.skipUnless(CUDA, "needs CUDA")
class WarpMemoryTests(unittest.TestCase):
    def test_a_batch_of_1080p_frames_stays_small(self):
        B = 8
        im = torch.rand(B, 3, 1080, 1920, device="cuda")
        flow = torch.zeros(B, 1080, 1920, 2, device="cuda")
        flow[..., 0] = -20.5
        torch.cuda.synchronize()
        torch.cuda.reset_peak_memory_stats()
        base = torch.cuda.memory_allocated()
        with torch.no_grad():
            out = FWP.forward_warp()(im, flow)
        torch.cuda.synchronize()
        extra = torch.cuda.max_memory_allocated() - base
        input_bytes = im.numel() * im.element_size()
        del im, flow, out
        torch.cuda.empty_cache()
        # the old warp took ~23x its input on top; this one ~7x
        self.assertLess(extra, 10 * input_bytes, f"{extra / 2**30:.2f} GiB on top of a "
                        f"{input_bytes / 2**30:.2f} GiB batch")


if __name__ == "__main__":
    unittest.main()
