"""desktop_notify.py -- a Windows notification when a job finishes while
Studio's window is minimized (user, 2026-10-05).

Every stage's finish passes through outcome.announce (verdict() calls it,
Scene Cut calls it with its own message), on whatever thread the job ended
on; JobNotifier takes it to the UI thread and, only if the main window is
minimized right then, shows it through a tray icon -- Windows turns that into
its own notification (Action Center keeps it). Clicking it brings Studio back.
The tray icon is there only while a notification may need it: it goes when
the window is restored. No extra package: QSystemTrayIcon ships with PySide6
and works under Studio's QGuiApplication -- as long as showMessage is given a
QIcon. Its MessageIcon form looks the icon up in the widget style, which a
QGuiApplication doesn't have: a null style, an access violation in
Qt6Widgets.dll (user, 2026-10-07: Studio gone at the end of an inpaint).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtGui import QIcon, QWindow

logger = logging.getLogger(__name__)


def is_minimized(window) -> bool:
    if window is None:
        return False
    try:
        return bool(window.windowStates() & Qt.WindowState.WindowMinimized) \
            or window.visibility() == QWindow.Visibility.Minimized
    except Exception:
        return False


class JobNotifier(QObject):
    _sig = Signal(str, str, str)

    def __init__(self, window, icon_path: Optional[Path] = None, parent=None,
                 show: Optional[Callable[[str, str, str], None]] = None) -> None:
        super().__init__(parent)
        self._window = window
        self._icon_path = Path(icon_path) if icon_path else None
        self._tray = None
        self._show = show or self._show_tray       # a check stands its own in
        self._sig.connect(self._on_finished, Qt.ConnectionType.QueuedConnection)
        try:
            window.windowStateChanged.connect(self._on_window_state)
        except Exception:
            pass

    def set_icon(self, icon_path: Optional[Path]) -> None:
        """The app icon changed (Shell.appIcon): the next notification shows the new one."""
        self._icon_path = Path(icon_path) if icon_path else None
        if self._tray is not None:
            self._tray.setIcon(QIcon(str(self._icon_path)) if self._icon_path and self._icon_path.exists() else QIcon())

    # any thread
    def job_finished(self, kind: str, title: str, text: str) -> None:
        self._sig.emit(str(kind), str(title), str(text))

    # UI thread
    @Slot(str, str, str)
    def _on_finished(self, kind: str, title: str, text: str) -> None:
        if not is_minimized(self._window):
            return                       # the window is up: the in-app toast says it
        try:
            self._show(kind, title, text)
        except Exception:
            logger.exception("Could not show the Windows notification")

    def _show_tray(self, kind: str, title: str, text: str) -> None:
        from PySide6.QtWidgets import QSystemTrayIcon
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        if self._tray is None:
            icon = QIcon(str(self._icon_path)) if self._icon_path and self._icon_path.exists() else QIcon()
            self._tray = QSystemTrayIcon(icon, self)
            self._tray.setToolTip("StereoCrafter Studio")
            self._tray.messageClicked.connect(self._bring_back)
            self._tray.activated.connect(lambda _reason: self._bring_back())
        self._tray.show()
        # the QIcon form, never the MessageIcon one (see the top): Studio's own icon
        self._tray.showMessage(title, text, self._tray.icon(), 15000)

    @Slot()
    def _bring_back(self) -> None:
        w = self._window
        if w is None:
            return
        w.setWindowStates(w.windowStates() & ~Qt.WindowState.WindowMinimized)
        w.show()
        w.raise_()
        w.requestActivate()

    def _on_window_state(self, *_a) -> None:
        if self._tray is not None and not is_minimized(self._window):
            self._tray.hide()            # back up: no icon left in the tray
