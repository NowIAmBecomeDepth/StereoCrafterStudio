"""Download V2 Weights (user, 2026-10-18; studio/stages/inpainting_v2/weights.py).

Nothing is downloaded: the child process's fetch script is swapped for one that writes the
files (or fails, or hangs), so the bookkeeping around it -- what's missing, the sizes, the
progress, Stop, a failure -- runs for real.

    .venv\\Scripts\\python.exe -m unittest studio.tests.test_inpainting_v2_weights -v
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data

from studio.stages.inpainting_v2 import weights as W  # noqa: E402

#: writes each file at the size its name ends with (".../name@123"), slowly enough to be seen
_WRITE = (
    "import sys, os, time\n"
    "repo, rev, folder, *files = sys.argv[1:]\n"
    "for f in files:\n"
    "    path = os.path.join(folder, f)\n"
    "    os.makedirs(os.path.dirname(path) or folder, exist_ok=True)\n"
    "    part = os.path.join(folder, '.cache', 'huggingface', 'download', 'x.' + str(abs(hash(f))) + '.incomplete')\n"
    "    os.makedirs(os.path.dirname(part), exist_ok=True)\n"
    "    with open(part, 'wb') as h:\n"
    "        h.write(b'x' * 4000); h.flush(); time.sleep(0.3)\n"
    "    os.replace(part, path)\n"
)
_FAIL = "import sys\nprint('401 Client Error: Unauthorized for url', flush=True)\nsys.exit(3)\n"
_HANG = "import time\ntime.sleep(60)\n"


class ListTest(unittest.TestCase):
    def test_sizes_are_the_files_v2_loads(self):
        self.assertEqual(W.gb(W.BASE.size + W.MODEL.size), "46.6 GB")
        self.assertEqual(W.gb(W.BASE.size), "11.9 GB")
        self.assertEqual(W.gb(W.MODEL.size), "34.7 GB")
        self.assertEqual(W.gb(W.FP8.size), "17.4 GB")
        # what engine.load_models reads from the VACE folder -- and never its 59 GB transformer
        tops = {p.split("/")[0] for p, _n in W.BASE.files}
        self.assertTrue({"tokenizer", "text_encoder", "vae"} <= tops)
        self.assertNotIn("transformer", tops)
        self.assertIn(("diffusion_pytorch_model.safetensors", 34675325472), W.MODEL.files)
        # the folders are the engine's (PRE_TRAINED_PATH, TRANSFORMER_PATH, + "-FP8")
        self.assertEqual((W.BASE.folder, W.MODEL.folder, W.FP8.folder),
                         ("Wan2.1-VACE-14B-diffusers", "StereoCrafter2", "StereoCrafter2-FP8"))

    def test_missing_until_every_file_is_there(self):
        root = Path(tempfile.mkdtemp())
        try:
            self.assertFalse(W.installed(root=root))
            self.assertEqual(W.to_download(root=root), W.BASE.size + W.MODEL.size)
            for part in W.REQUIRED:
                for path, _n in part.files:
                    f = root / part.folder / path
                    f.parent.mkdir(parents=True, exist_ok=True)
                    f.write_bytes(b"1")
            self.assertTrue(W.installed(root=root))
            (root / W.MODEL.folder / "diffusion_pytorch_model.safetensors").write_bytes(b"")   # empty: not there
            self.assertEqual([f for _p, f, _n in W.missing(root=root)], ["diffusion_pytorch_model.safetensors"])
            self.assertEqual(W.to_download(root=root), 34675325472)
        finally:
            shutil.rmtree(root, ignore_errors=True)


class DownloadTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.part = W.Part("Test model", "nobody/test", "0" * 40, "TestModel",
                           (("config.json", 4000), ("sub/model.bin", 4000), ("LICENSE", 4000)))
        self._fetch = W._FETCH

    def tearDown(self):
        W._FETCH = self._fetch
        shutil.rmtree(self.root, ignore_errors=True)

    def test_fetches_only_whats_missing_with_progress(self):
        (self.root / "TestModel").mkdir()
        (self.root / "TestModel" / "LICENSE").write_text("already here")
        W._FETCH = _WRITE
        seen = []
        W.download([self.part], lambda f, t: seen.append((f, t)), threading.Event(), root=self.root, poll_s=0.1)
        self.assertTrue(W.installed([self.part], root=self.root))
        self.assertEqual((self.root / "TestModel" / "LICENSE").read_text(), "already here")
        self.assertEqual(seen[-1], (1.0, "Downloaded."))
        self.assertTrue(any("Test model:" in t and " of " in t for _f, t in seen), seen)
        fr = [f for f, _t in seen]
        self.assertEqual(fr, sorted(fr), "progress only goes up")

    def test_stop_ends_the_child_at_once(self):
        W._FETCH = _HANG
        stop = threading.Event()
        procs = []
        threading.Timer(0.5, stop.set).start()
        t0 = time.monotonic()
        with self.assertRaises(W.Stopped):
            W.download([self.part], lambda f, t: None, stop, root=self.root, poll_s=0.1, procs=procs)
        self.assertLess(time.monotonic() - t0, 10)
        self.assertEqual(procs, [], "the child is gone")

    def test_a_failure_says_why(self):
        W._FETCH = _FAIL
        with self.assertRaises(RuntimeError) as cm:
            W.download([self.part], lambda f, t: None, threading.Event(), root=self.root, poll_s=0.1)
        self.assertIn("Test model", str(cm.exception))
        self.assertIn("401", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
