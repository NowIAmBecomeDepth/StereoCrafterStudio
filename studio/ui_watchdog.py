"""ui_watchdog.py -- when Studio's window stops responding, the log says where it hung.

A tester (2026-11-17): partway through a DVD batch of ~6400 clips the window froze ("Python is not
responding"; Wait never brought it back) while the job ran on to the end. Nothing in the log said why, and
it can't be made to happen on demand. This makes the next one tell us:

* a QTimer on the UI thread re-arms ``faulthandler.dump_traceback_later`` once a second. faulthandler's
  timer is a C thread that needs neither the GIL nor the event loop, so if the UI thread stops coming back
  for :data:`DUMP_AFTER_S` it writes EVERY thread's Python stack -- the UI thread's shows where it is stuck
  (a Python call, or ``exec()`` when it is inside Qt itself: a render-thread sync, a driver call) -- to the
  launcher's log (studio_last_run.log), or to ``studio_ui_stall.log`` beside it when there is no stream;
* the same tick notices a shorter stall after the fact and logs how long the window was gone, so a run that
  slows down bit by bit before it stops shows that too.

Costs nothing a job would notice: one small Python call a second, no redraw (the Theme.calm rule).
"""

from __future__ import annotations

import faulthandler
import logging
import os
import sys
import time
from typing import Optional

from PySide6.QtCore import QObject, QTimer

logger = logging.getLogger("studio.ui_watchdog")

#: The UI thread is looked at this often (ms).
TICK_MS = 1000
#: A stall at least this long is logged when the UI thread comes back (Windows calls a window "not
#: responding" after 5 s without its messages).
REPORT_AFTER_S = 5.0
#: ...and one this long has every thread's stack written while it lasts.
DUMP_AFTER_S = 20.0

STALL_LOG = "studio_ui_stall.log"


def _stack_file(folder: str):
    """Where faulthandler writes: the real stderr (the launcher's log) when it is a file it can use,
    else a file of our own. faulthandler writes to the descriptor, so it must stay open."""
    for stream in (sys.__stderr__, sys.__stdout__):
        try:
            if stream is not None and stream.fileno() >= 0:
                return stream
        except Exception:
            pass
    try:
        return open(os.path.join(folder, STALL_LOG), "a", encoding="utf-8")
    except OSError:
        return None


class UiWatchdog(QObject):
    """Made and started on the UI thread; :meth:`stop` at shutdown (a slow close is the exit watchdog's)."""

    def __init__(self, folder: str = ".", parent=None, tick_ms: int = TICK_MS,
                 report_after: float = REPORT_AFTER_S, dump_after: float = DUMP_AFTER_S, file=None):
        super().__init__(parent)
        self.report_after, self.dump_after = float(report_after), float(dump_after)
        self.file = file if file is not None else _stack_file(folder)
        self.longest = 0.0          # the longest stall seen, in seconds
        self.stalls = 0             # stalls of report_after or more
        self._last: Optional[float] = None
        self._timer = QTimer(self)
        self._timer.setInterval(int(tick_ms))
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        if self.file is None:
            logger.debug("No file for the UI watchdog's stacks; stalls are only logged")
        self._last = time.monotonic()
        self._arm()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        if self.file is not None:
            try:
                faulthandler.cancel_dump_traceback_later()
            except Exception:
                pass

    def _arm(self) -> None:
        if self.file is None:
            return
        try:        # a new call replaces the last one and starts its clock again
            faulthandler.dump_traceback_later(self.dump_after, repeat=False, file=self.file, exit=False)
        except Exception:
            logger.debug("Could not arm the UI watchdog", exc_info=True)
            self.file = None

    def _tick(self) -> None:
        now = time.monotonic()
        gap = now - (self._last if self._last is not None else now) - self._timer.interval() / 1000.0
        self._last = now
        self._arm()
        if gap >= self.report_after:
            self.stalls += 1
            self.longest = max(self.longest, gap)
            dumped = self.file is not None and gap >= self.dump_after
            where = getattr(self.file, "name", "") if dumped else ""
            where = (f" ({STALL_LOG} has every thread's stack from it)" if str(where).endswith(STALL_LOG)
                     else " (every thread's stack from it is above, after \"Timeout\")" if dumped else "")
            logger.warning("The window stopped responding for %.1f s%s.", gap, where)
