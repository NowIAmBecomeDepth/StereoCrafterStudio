"""ROSE Erase's worker and Stop / Quit (bug sweep 2026-10-09, A1): the erase thread
saw Stop only when the worker printed its next line, so during a model load or a
long step Quit's 10 s join ran out and the worker went on holding its VRAM. A
stand-in worker that prints nothing: Stop ends it at once."""
from __future__ import annotations

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data


class RoseStopTests(unittest.TestCase):
    def test_stop_kills_a_silent_worker_at_once(self):
        import psutil
        from core.removal import erase as E
        tmp = Path(tempfile.mkdtemp())
        pid_file = tmp / "pid"
        worker = tmp / "silent_worker.py"
        worker.write_text("import os, sys, time\n"
                          "open(sys.argv[1] + '.pid', 'w').write(str(os.getpid()))\n"
                          "time.sleep(120)\n")                       # "loading": no output at all
        job = str(tmp / "job")
        stop, result = threading.Event(), {}

        def run():
            try:
                E._run_worker(job, 1, 10, lambda f, m: None, stop, 0.0, 1.0)
            except BaseException as e:                                  # Stopped is expected
                result["exc"] = e

        with mock.patch.object(E.rose_env, "python", return_value=Path(sys.executable)), \
                mock.patch.object(E.rose_env, "WORKER", worker):
            t = threading.Thread(target=run, daemon=True)
            t.start()
            pid_path = Path(job + ".pid")
            end = time.monotonic() + 30
            while not pid_path.exists() and time.monotonic() < end:
                time.sleep(0.05)
            pid = int(pid_path.read_text())
            t0 = time.monotonic()
            stop.set()
            t.join(10)
        self.assertFalse(t.is_alive(), "the erase thread still waits on the silent worker")
        self.assertLess(time.monotonic() - t0, 5)
        self.assertIsInstance(result.get("exc"), E.Stopped, repr(result.get("exc")))
        time.sleep(0.3)
        self.assertFalse(psutil.pid_exists(pid) and psutil.Process(pid).status() != psutil.STATUS_ZOMBIE,
                         "the worker is still running")


class RoseStepBarTests(unittest.TestCase):
    """Fill Edges' console (user, 2026-10-14): each ROSE window's steps as a bar, like
    DepthCrafter's and Inpainting's, with s/it; Erase (no `bars`) prints none."""

    def run_worker(self, bars):
        import io
        from contextlib import redirect_stdout
        from core.removal import erase as E
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, tmp, True)      # no temp folders left (sweep 2026-10-14b)
        worker = tmp / "steps_worker.py"
        worker.write_text("import time\n"
                          "for c in (1, 2):\n"
                          "    print('CHUNK', c, 2, flush=True)\n"
                          "    for s in (1, 2, 3):\n"
                          "        time.sleep(0.1)\n"
                          "        print('STEP', s, 3, flush=True)\n"
                          "    print('DONE', c, '0.3s', flush=True)\n")
        out = io.StringIO()
        with mock.patch.object(E.rose_env, "python", return_value=Path(sys.executable)), \
                mock.patch.object(E.rose_env, "WORKER", worker), redirect_stdout(out):
            E._run_worker(str(tmp / "job"), 2, 3, lambda f, m: None, threading.Event(), 0.0, 1.0, bars=bars)
        return out.getvalue()

    def test_a_bar_per_window_with_seconds_per_step(self):
        from studio.log_model import parse_bar
        text = self.run_worker(True)
        finals = [ln.rsplit("\r", 1)[-1] for ln in text.split("\n") if ln.strip()]
        self.assertEqual(len(finals), 2, repr(text))                 # one line per window, each ended
        for c, ln in enumerate(finals, 1):
            self.assertTrue(ln.startswith(f"  ROSE window {c}/2:"), ln)
            _left, frac, right = parse_bar(ln)
            self.assertEqual(frac, 1.0, ln)
            self.assertRegex(right, r"3/3 \[0\.\d\ds/it\]", "the rate stays at 100%")
            per = float(right.split("[")[1].split("s/it")[0])
            self.assertGreater(per, 0.05)
            self.assertLess(per, 0.5)

    def test_erase_prints_no_bars(self):
        self.assertEqual(self.run_worker(False), "")

    def test_a_windows_first_step_shows_no_rate_yet(self):
        """Bug sweep 2026-10-14: the first step's s/it counted the window's set-up (window 1: the
        model load) -- "?s/it" until a second step."""
        text = self.run_worker(True)
        firsts = [seg for seg in text.replace("\n", "\r").split("\r") if " 1/3 [" in seg]
        self.assertTrue(firsts, repr(text))
        self.assertTrue(all("?s/it" in seg for seg in firsts), firsts)

    def test_the_workers_pipes_are_closed(self):
        """Bug sweep 2026-10-14: the worker's stdout and stderr pipes were left to the garbage
        collector (ResourceWarning: unclosed file) -- two handles per ROSE run."""
        import gc
        import warnings
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            self.run_worker(False)
            gc.collect()
        unclosed = [str(w.message) for w in caught if issubclass(w.category, ResourceWarning)
                    and "TextIOWrapper" in str(w.message)]
        self.assertEqual(unclosed, [])



class EraseLeavesNoWorkFolderTests(unittest.TestCase):
    """The user's erase (2026-10-15) left .erase_*/w0_clean.npy (~200 MB) beside the clip: ROSE's output
    was still open, memory-mapped, when the work folder was removed (errors ignored), and Windows won't
    delete an open file. A stand-in worker writes the output; nothing may be left."""

    def test_no_work_folder_left(self):
        import json
        import os
        import shutil
        import subprocess
        import numpy as np
        from core.removal import erase as E
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        clip = tmp / "shot.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=96x64:rate=24:duration=1",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)], check=True)
        worker = tmp / "copy_worker.py"
        worker.write_text("import json, sys, numpy as np\n"
                          "job = json.load(open(sys.argv[1]))\n"
                          "for k, c in enumerate(job['chunks']):\n"
                          "    print('CHUNK', k + 1, len(job['chunks']), flush=True)\n"
                          "    np.save(c['out'], np.load(c['frames']))\n"
                          "    print('DONE', k + 1, '0s', flush=True)\n")
        square = np.zeros((64, 96), bool)
        square[20:40, 30:60] = True
        with mock.patch.object(E.rose_env, "ready", return_value=True),                 mock.patch.object(E.rose_env, "python", return_value=Path(sys.executable)),                 mock.patch.object(E.rose_env, "WORKER", worker),                 mock.patch.object(E, "frame_mask", lambda layers, i, size: square):
            E.erase(str(clip), [], lambda f, m: None, threading.Event(), steps=2)
        left = [n for n in os.listdir(tmp) if n.startswith(".erase_")]
        self.assertEqual(left, [], "the erase's work folder was left beside the clip")
        self.assertTrue(os.path.isfile(E.record_path(str(clip))))


if __name__ == "__main__":
    unittest.main()
