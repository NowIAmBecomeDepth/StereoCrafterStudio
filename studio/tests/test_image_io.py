"""OpenCV's imwrite / imread for any path (a user, 2026-10-24): Inpainting of "Противостояние (2001)_1360_
splatted4.mp4" ran three hours, then "Failed to write PNG frame 0" into its temp folder -- named after the
clip -- and the clip was thrown away. On Windows OpenCV can't open a path outside the ANSI code page.

Proved in a fresh process: plain cv2 can't write or read a PNG in a Cyrillic folder (the old code); after
core.common.image_io.install() -- as Studio does at start -- it writes, reads it back the same, and ASCII
paths work as before.

    python -m unittest studio.tests.test_image_io
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data

CHILD = r"""
import json, os, sys
sys.path.insert(0, sys.argv[1])
import cv2, numpy as np
if sys.argv[3] == "installed":
    from core.common import image_io
    image_io.install()
img = (np.arange(16 * 24 * 3) % 251).astype(np.uint8).reshape(16, 24, 3)
out = {}
for name in ("Противостояние (2001)_1920_6688", "plain_ascii"):
    d = os.path.join(sys.argv[2], name)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "00000.png")
    ok = bool(cv2.imwrite(p, img))
    back = cv2.imread(p)
    gray = cv2.imread(p, cv2.IMREAD_GRAYSCALE)
    out[name] = {"wrote": ok, "exists": os.path.exists(p), "same": back is not None and bool((back == img).all()),
                 "gray": None if gray is None else list(gray.shape)}
print("RESULT" + json.dumps(out, ensure_ascii=True))
"""


class AnyPathTest(unittest.TestCase):
    def run_child(self, mode: str) -> dict:
        with tempfile.TemporaryDirectory() as d:
            script = Path(d) / "child.py"
            script.write_text(CHILD, encoding="utf-8")
            out = subprocess.run([sys.executable, str(script), str(REPO), d, mode],
                                 capture_output=True, text=True, timeout=120, encoding="utf-8")
            line = [x for x in out.stdout.splitlines() if x.startswith("RESULT")]
            self.assertTrue(line, out.stderr[-2000:])
            return json.loads(line[-1][len("RESULT"):])

    @unittest.skipUnless(sys.platform == "win32", "OpenCV's code-page paths are Windows'")
    def test_plain_opencv_cannot_write_a_cyrillic_folder(self):
        # the old code: the user's failure, so the test below proves something
        r = self.run_child("plain")
        self.assertFalse(r["Противостояние (2001)_1920_6688"]["wrote"])
        self.assertTrue(r["plain_ascii"]["same"])

    def test_installed_it_writes_and_reads_any_path(self):
        r = self.run_child("installed")
        for name, got in r.items():
            self.assertEqual(got, {"wrote": True, "exists": True, "same": True, "gray": [16, 24]}, name)


if __name__ == "__main__":
    unittest.main()
