"""Auto-Border on a 10-bit TV-range depth map (bug sweep 2026-10-09, A4,
low confidence, settled 2026-10-10): its TV-range compensation never ran -- it
looked for a `_file_path` decord's reader doesn't have -- and it must not:
decord already expands the 64-940 window to 0-255, so the render's factor
(for raw codes) would overshoot the border by ~17%. The branch is gone; this
keeps the reason true -- it fails if decord stops expanding TV range.

    python -m unittest studio.tests.test_border_tv_range
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data


class TvRangeTest(unittest.TestCase):
    def test_decord_expands_tv_range(self):
        import torch  # noqa: F401  -- before decord (it segfaults on import otherwise here)
        from decord import VideoReader, cpu
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        clip = str(Path(d.name) / "tv10.mp4")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "nullsrc=size=256x64:rate=24:duration=0.25,format=yuv420p10le,"
                        "geq=lum='if(lt(X,128),940,64)':cb=512:cr=512",
                        "-c:v", "libx265", "-x265-params", "lossless=1:log-level=error", "-pix_fmt", "yuv420p10le",
                        "-color_range", "tv", clip], check=True)
        f = VideoReader(clip, ctx=cpu(0))[0].asnumpy().mean(axis=2)
        self.assertGreater(f[:, 8:120].mean(), 245, "TV white no longer reads as full white")
        self.assertLess(f[:, 136:248].mean(), 8, "TV black no longer reads as black")

    def test_auto_border_applies_no_factor(self):
        src = (Path(__file__).resolve().parents[2] / "core/splatting/border_scanning.py").read_text(encoding="utf-8")
        self.assertIn("tv_disp_comp = 1.0", src)
        self.assertNotIn("_get_tv_compensation", src)


if __name__ == "__main__":
    unittest.main()
