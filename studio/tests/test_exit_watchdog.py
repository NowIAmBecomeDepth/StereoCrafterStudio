"""Studio ends its process when its window closes (user, 2026-10-02): after a
Stop After Clip, closing the window left Studio running hidden. The exit
watchdog: with a thread that never ends and the watchdog at 1 s, a child
process exits by itself and its log holds that thread's stack.

    python -m unittest studio.tests.test_exit_watchdog
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
# (the child below inherits it through the environment)

CHILD = r"""
import sys, threading, time
sys.path.insert(0, %r)
from studio.app import _start_exit_watchdog

def stuck_forever():
    while True:
        time.sleep(0.05)

threading.Thread(target=stuck_forever, name="stuck-worker").start()     # not a daemon
_start_exit_watchdog(1.0)
"""


class ExitWatchdogTest(unittest.TestCase):
    def test_a_stuck_thread_cannot_keep_studio_alive(self):
        env = dict(os.environ, CUDA_VISIBLE_DEVICES="-1", QT_QPA_PLATFORM="offscreen")
        t0 = time.time()
        r = subprocess.run([sys.executable, "-c", CHILD % str(REPO)], capture_output=True, text=True,
                           timeout=120, env=env, cwd=str(REPO))
        took = time.time() - t0
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        self.assertIn("stuck_forever", r.stderr)                 # what was stuck, in the log
        self.assertIn("still running", r.stderr + r.stdout)
        self.assertLess(took, 90)


if __name__ == "__main__":
    unittest.main()
