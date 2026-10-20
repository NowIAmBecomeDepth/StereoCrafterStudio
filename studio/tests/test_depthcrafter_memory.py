"""DepthCrafter's full-video mode, lighter on memory (bug sweep 2026-10-09,
A3): the load, the normalisation and the save each held two or three full-clip
float32 copies at once, and the pipeline made a second full-clip copy on the
GPU. Each now works in place (or a chunk of frames at a time) -- and gives the
very same values.

    python -m unittest studio.tests.test_depthcrafter_memory
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data

REPO = Path(__file__).resolve().parents[2]


class LoadTest(unittest.TestCase):
    def test_the_load_gives_the_same_frames(self):
        import torch  # noqa: F401  -- before decord (it segfaults on import otherwise here)
        from decord import VideoReader, cpu
        from depthcrafter.utils import read_video_frames
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        clip = str(Path(d.name) / "c.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=128x72:rate=12",
                        "-frames:v", "8", "-pix_fmt", "yuv420p", clip], check=True)
        frames = read_video_frames(clip, target_height=64, target_width=128)[0]
        want = VideoReader(clip, ctx=cpu(0), width=128, height=64).get_batch(list(range(8))).asnumpy().astype("float32") / 255.0
        self.assertEqual(frames.dtype, np.float32)
        self.assertTrue(np.array_equal(frames, want))


class InPlaceTest(unittest.TestCase):
    def test_the_steps_are_in_place_or_chunked(self):
        utils = (REPO / "depthcrafter/utils.py").read_text(encoding="utf-8")
        self.assertIn("frames_numpy /= 255.0", utils)
        self.assertIn("np.copyto(_out[_i:_i + _n], _buf[:_n], casting=\"unsafe\")", utils)
        logic = (REPO / "depthcrafter/depthcrafter_logic.py").read_text(encoding="utf-8")
        self.assertIn("np.clip(res_normalized_for_mp4, 0, 1, out=res_normalized_for_mp4)", logic)
        ppl = (REPO / "depthcrafter/depth_crafter_ppl.py").read_text(encoding="utf-8")
        self.assertIn("video.mul_(2.0).sub_(1.0)", ppl)
        self.assertNotIn("            video = video * 2.0 - 1.0  # [0,1] -> [-1,1]", ppl)

    def test_the_same_values(self):
        rng = np.random.default_rng(0)
        res = rng.random((70, 9, 11), dtype=np.float32) * 3 + 0.2
        mn, mx = res.min(), res.max()
        old = np.clip((res - mn) / (mx - mn), 0, 1)
        new = res - mn
        new /= (mx - mn)
        np.clip(new, 0, 1, out=new)
        self.assertTrue(np.array_equal(old, new))
        out = np.empty(new.shape, np.uint16)
        buf = np.empty((16,) + new.shape[1:], np.float32)
        for i in range(0, len(new), 16):
            n = min(16, len(new) - i)
            np.multiply(new[i:i + n], 65535.0, out=buf[:n])
            np.copyto(out[i:i + n], buf[:n], casting="unsafe")
        self.assertTrue(np.array_equal(out, (new * 65535.0).astype(np.uint16)))
        for f8 in (new.astype(np.float64),):          # a float64 clip, and 8-bit output
            out8 = np.empty(f8.shape, np.uint8)
            buf8 = np.empty((16,) + f8.shape[1:], f8.dtype)
            for i in range(0, len(f8), 16):
                n = min(16, len(f8) - i)
                np.multiply(f8[i:i + n], 255.0, out=buf8[:n])
                np.copyto(out8[i:i + n], buf8[:n], casting="unsafe")
            self.assertTrue(np.array_equal(out8, (f8 * 255.0).astype(np.uint8)))
        import torch
        v = torch.rand(5, 3, 8, 8).half()
        w = v.clone()
        w.mul_(2.0).sub_(1.0)
        self.assertTrue(torch.equal(w, v * 2.0 - 1.0))


if __name__ == "__main__":
    unittest.main()
