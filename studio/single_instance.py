"""single_instance.py -- one StereoCrafter Studio at a time (bug sweep 2026-10-09, A1).

A second Studio shared config/*.json with the first (the last to save won) and
competed for the GPU, and a second start from the .bat copied the live log over
studio_last_run.prev.log and then emptied it. Now:

* ``claim()`` (studio.app, before anything else): a named mutex held for the
  life of the process. When another Studio holds it, the running one's window
  is brought forward and this one exits quietly.
* ``python -m studio.single_instance`` (the .bat and the .exe launcher, before
  they touch the log): exit code 3 -- after bringing the running window
  forward -- when Studio is already running, else 0. Imports nothing heavy.
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes

MUTEX_NAME = "Local\\StereoCrafterStudio"
WINDOW_TITLE = "StereoCrafter Studio"
ALREADY_RUNNING = 3

_ERROR_ALREADY_EXISTS = 183
_SYNCHRONIZE = 0x00100000
_held = None                      # the mutex handle, kept for the life of the process


def claim(name: str = MUTEX_NAME) -> bool:
    """True: this is the only Studio (the mutex is now ours). False: another holds it."""
    global _held
    if sys.platform != "win32":
        return True
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateMutexW.restype = wintypes.HANDLE
    k32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    handle = k32.CreateMutexW(None, False, name)
    if not handle:
        return True                                   # can't tell: never block a start on it
    if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
        k32.CloseHandle(wintypes.HANDLE(handle))
        return False
    _held = handle
    return True


def running(name: str = MUTEX_NAME) -> bool:
    """Whether a Studio holds the mutex (without taking it)."""
    if sys.platform != "win32":
        return False
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenMutexW.restype = wintypes.HANDLE
    k32.OpenMutexW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    handle = k32.OpenMutexW(_SYNCHRONIZE, False, name)
    if not handle:
        return False
    k32.CloseHandle(wintypes.HANDLE(handle))
    return True


def bring_forward(title: str = WINDOW_TITLE) -> bool:
    """Restore and raise another process's visible window titled `title`(...)."""
    if sys.platform != "win32":
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    me, found = os.getpid(), []
    proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def each(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        if not (buf.value == title or buf.value.startswith(title + " (")):   # the main window, not Full Preview
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value != me:
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(proto(each), 0)
    if not found:
        return False
    hwnd = found[0]
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)                    # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    return True


def main() -> int:
    if running():
        bring_forward()
        return ALREADY_RUNNING
    return 0


if __name__ == "__main__":
    sys.exit(main())
