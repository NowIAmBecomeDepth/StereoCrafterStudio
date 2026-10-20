"""The update check and a stalled fetch (bug sweep 2026-10-09, A1): on a timeout,
subprocess.run killed only git and then read its pipes with no limit; git's own
helper still held them, so "Looking for updates..." never ended. A stand-in that
leaves a child holding the pipes, as git-remote-https does."""
from __future__ import annotations

import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from studio import updater as U  # noqa: E402

_STALL = ("import subprocess, sys, time; "
          "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
          "time.sleep(60)")


class TimeoutTests(unittest.TestCase):
    def test_a_stalled_command_ends_at_its_timeout(self):
        t0 = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            U._run([sys.executable, "-c", _STALL], timeout=1.0)
        self.assertLess(time.monotonic() - t0, 15, "the read waited on the child still holding the pipes")

    def test_a_quick_command_is_read_as_before(self):
        r = U._run([sys.executable, "-c", "print('hi'); import sys; sys.exit(3)"], timeout=20)
        self.assertEqual((r.returncode, r.stdout.strip()), (3, "hi"))


class InstallDuringAJobTests(unittest.TestCase):
    """bug sweep 2026-10-09 (A1): Install during a job started the update helper, the
    close was refused for the job, and the update went in at a later close."""

    def test_install_refuses_while_a_job_runs_and_starts_nothing(self):
        from unittest import mock
        up = U.Updater(run_check=lambda *a: {})
        up._result = {"state": "available", "sha": "abc123", "commits": [{}], "dirty": False, "deps": False}
        up.job_running = lambda: "DepthCrafter"
        with mock.patch.object(U.subprocess, "Popen") as popen:
            why = up.install()
        self.assertIn("DepthCrafter is running a job", why)
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
