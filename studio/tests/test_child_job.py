"""GPU workers end with Studio (user, 2026-10-15: "everything releasing as it should especially when studio
closes"; core/common/child_job.py). A parent process starts a child that would run for minutes (as ROSE's
worker does), binds it, then ends the way Studio does -- os._exit -- or is killed: the child goes with it.
Unbound, it's left running (what happened before)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data

PARENT = r'''
import os, subprocess, sys, time
sys.path.insert(0, sys.argv[1])
from core.common.child_job import bind
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
if sys.argv[3] == "bind":
    assert bind(child)
open(sys.argv[2], "w").write(str(child.pid))
if sys.argv[4] == "exit":
    os._exit(0)                       # as Studio closes
time.sleep(120)                       # waits to be killed
'''


def _alive(pid: int) -> bool:
    import psutil
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


@unittest.skipUnless(sys.platform.startswith("win"), "Windows job objects")
class ChildEndsWithStudioTest(unittest.TestCase):
    def run_parent(self, how: str, ending: str) -> int:
        d = tempfile.mkdtemp()
        script, pid_file = os.path.join(d, "parent.py"), os.path.join(d, "child.pid")
        with open(script, "w", encoding="utf-8") as f:
            f.write(PARENT)
        parent = subprocess.Popen([sys.executable, script, str(REPO), pid_file, how, ending])
        end = time.monotonic() + 30
        while not os.path.exists(pid_file) and time.monotonic() < end:
            time.sleep(0.05)
        time.sleep(0.3)
        pid = int(open(pid_file).read())
        if ending == "kill":
            parent.kill()                 # a crash, or Task Manager
        parent.wait(30)
        self.addCleanup(lambda: _alive(pid) and __import__("psutil").Process(pid).kill())
        time.sleep(1.0)
        return pid

    def test_a_bound_child_ends_when_studio_closes(self):
        self.assertFalse(_alive(self.run_parent("bind", "exit")), "the worker outlived Studio's close")

    def test_a_bound_child_ends_when_studio_is_killed(self):
        self.assertFalse(_alive(self.run_parent("bind", "kill")), "the worker outlived Studio being killed")

    def test_unbound_it_was_left_running(self):
        self.assertTrue(_alive(self.run_parent("none", "exit")), "the test shows nothing: unbound ends too")


if __name__ == "__main__":
    unittest.main()
