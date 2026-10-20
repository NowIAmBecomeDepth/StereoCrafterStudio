"""Inpainting V1 reads its clip as uint8 (bug sweep 2026-10-09, A11): it read
float32 0-1 frames -- two full float32 copies -- and turned them straight back
into uint8. The frames are the very same (values and layout); on a 1080p
300-frame clip the load went from 4.7 s and ~8.9 GB to 1.2 s and ~1.8 GB.

    python -m unittest studio.tests.test_inpainting_load
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data


class Uint8LoadTest(unittest.TestCase):
    def test_the_same_frames_read_as_uint8(self):
        import torch
        from core.common.video_io import read_video_frames_decord
        from studio.legacy import install
        install()
        from studio.stages.inpainting import engine as E
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        clip = str(Path(d.name) / "c.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=24",
                        "-frames:v", "12", "-pix_fmt", "yuv420p", clip], check=True)
        a, fps0, *_ = read_video_frames_decord(video_path=clip)
        old = (torch.from_numpy(a).permute(0, 3, 1, 2).float() * 255).to(torch.uint8)
        new, fps1, _info = E._read_video_frames_uint8(clip)
        self.assertEqual(new.dtype, torch.uint8)
        self.assertTrue(torch.equal(old, new))
        self.assertEqual(old.stride(), new.stride())
        self.assertEqual(fps0, fps1)
        f32, *_ = read_video_frames_decord(video_path=clip)            # the float path is unchanged
        self.assertEqual(str(f32.dtype), "float32")
        self.assertTrue(abs(float(f32.max()) - float(a.max())) == 0)


if __name__ == "__main__":
    unittest.main()
