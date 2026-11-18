"""The UI-stall watchdog (a tester, 2026-11-17: the window froze partway through a 6400-clip DVD batch
and nothing said where): a UI thread that stops coming back gets every thread's stack written, with
the blocking call in it, and the stall is logged when it comes back.

    python -m unittest studio.tests.test_ui_watchdog
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
import unittest

from PySide6.QtCore import QCoreApplication, QEventLoop

import studio.tests  # noqa: F401  -- the data redirect first: tests never touch the user's data
from studio.tests._qt import app as _qt_app
_app = _qt_app()


def pump(seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 10)
        time.sleep(0.005)


def stuck_in_a_slot_for_the_test(seconds: float) -> None:
    time.sleep(seconds)          # the UI thread blocked, as the tester's froze


class UiWatchdogTest(unittest.TestCase):
    def setUp(self):
        from studio.ui_watchdog import UiWatchdog
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "stall.log")
        self.file = open(self.path, "w", encoding="utf-8")
        self.dog = UiWatchdog(self.tmp, tick_ms=50, report_after=0.5, dump_after=0.8, file=self.file)

    def written(self) -> str:
        with open(self.path, encoding="utf-8") as f:
            return f.read()

    def tearDown(self):
        self.dog.stop()
        self.file.close()

    def test_a_hang_writes_every_stack_and_is_logged(self):
        self.dog.start()
        pump(0.3)
        with self.assertLogs("studio.ui_watchdog", logging.WARNING) as logs:
            stuck_in_a_slot_for_the_test(1.5)
            pump(0.3)
        self.file.flush()
        text = self.written()
        self.assertIn("stuck_in_a_slot_for_the_test", text)      # the UI thread's stack, where it hung
        self.assertIn("Timeout", text)
        self.assertEqual(self.dog.stalls, 1)
        self.assertGreaterEqual(self.dog.longest, 1.0)
        self.assertIn("stopped responding for", logs.output[0])

    def test_a_responsive_window_writes_nothing(self):
        self.dog.start()
        pump(1.5)                                 # past dump_after, but the timer keeps re-arming it
        self.dog.stop()
        self.file.flush()
        self.assertEqual(self.written(), "")
        self.assertEqual(self.dog.stalls, 0)

    def test_stop_cancels_the_dump(self):
        self.dog.start()
        pump(0.2)
        self.dog.stop()
        time.sleep(1.2)                           # no event loop, but stopped: nothing written
        self.file.flush()
        self.assertEqual(self.written(), "")


if __name__ == "__main__":
    unittest.main()
