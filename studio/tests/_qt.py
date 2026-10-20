"""The one Qt application a unit-test run shares (2026-10-02).

Unit tests run in one process when discovered together, and Qt allows one
application object per process. Some modules made a plain QCoreApplication as
they were imported; a later test that built the Studio shell then had no GUI
application to load fonts or watch window focus with, and crashed the whole
run. Everything uses this instead: an offscreen QGuiApplication, which does
all a QCoreApplication does.
"""

from __future__ import annotations


def app():
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtGui import QGuiApplication
    existing = QCoreApplication.instance()
    if existing is not None:
        return existing
    return QGuiApplication(["studio-tests", "-platform", "offscreen"])
