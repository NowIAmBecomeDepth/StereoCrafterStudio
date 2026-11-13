"""core.common.flat_sbs (user, 2026-11-08: end credits with no depth as a 2D Full SBS):
the frame in both eyes, Merging's name and encoder, 10-bit kept, a stop leaves nothing."""
import os, subprocess, sys, tempfile, unittest
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from core.common import flat_sbs  # noqa: E402

CPU = {"codec": "H.264", "encoding_encoder": "Force CPU", "encoding_quality": "Fast", "output_crf": 1,
       "bit_depth": "Auto", "pix_fmt": "Auto", "output_container": "MP4"}


def _clip(path, pix="yuv420p", codec="libx264", frames=12):
    extra = ["-x265-params", "log-level=error"] if codec == "libx265" else []
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size=160x90:rate=24",
                    "-frames:v", str(frames), "-c:v", codec, "-pix_fmt", pix, *extra, str(path)], check=True)


def _frame(path, i=5):
    info = flat_sbs.probe(str(path))
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"select=eq(n\,{i})", "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(info["h"], info["w"], 3)


class FlatSbsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.src = self.tmp / "credits.mp4"
        _clip(self.src)
        self.addCleanup(__import__("shutil").rmtree, self.tmp, True)

    def test_both_eyes_the_source_under_merging_name(self):
        seen = []
        out = flat_sbs.render(str(self.src), str(self.tmp / "Merged"), CPU, progress=lambda d, t: seen.append((d, t)))
        self.assertEqual(Path(out), self.tmp / "Merged" / "credits_160_merged_full_sbs.mp4")
        info = flat_sbs.probe(out)
        self.assertEqual((info["w"], info["h"], info["frames"]), (320, 90, 12))
        f, s = _frame(out).astype(int), _frame(self.src).astype(int)
        self.assertLess(np.abs(f[:, :160] - f[:, 160:]).mean(), 1.0)      # left eye == right eye
        self.assertLess(np.abs(f[:, :160] - s).mean(), 2.0)               # == the source
        self.assertEqual(seen[-1], (12, 12))
        self.assertFalse([p for p in os.listdir(self.tmp / "Merged") if "partial" in p])

    def test_ten_bit_stays_ten_bit_and_mkv(self):
        src = self.tmp / "credits10.mkv"
        _clip(src, "yuv420p10le", "libx265")
        out = flat_sbs.render(str(src), str(self.tmp / "M"), dict(CPU, codec="H.265", output_container="MKV"))
        self.assertTrue(out.endswith("credits10_160_merged_full_sbs.mkv"), out)
        self.assertIn("10", flat_sbs.probe(out)["pix_fmt"])

    def test_a_stop_leaves_nothing(self):
        with self.assertRaises(InterruptedError):
            flat_sbs.render(str(self.src), str(self.tmp / "S"), CPU, stop=lambda: True)
        self.assertEqual(os.listdir(self.tmp / "S"), [])


if __name__ == "__main__":
    unittest.main()
