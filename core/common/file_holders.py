"""file_holders.py -- which programs have a file open (Windows' Restart Manager), and a move that waits for them.

A tester, 2026-11-17: Rerun in Splatting said "LC_V2-0411_depth.mp4 -- couldn't be moved", and the same Rerun
worked ten seconds later. Something held the depth map for a few seconds -- Windows Defender scanning it, Explorer
making a thumbnail, a player -- and the move had given up after two. So a move waits longer for a file that is
in use, and when it still can't go, says who has it open.

No Qt here. Off Windows (or when the Restart Manager can't answer), holders() is [].
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from typing import Callable, List, Optional

#: how long a move waits for a file someone else has open (the tester's lock was gone in ~10 s)
WAIT_FOR_FREE = 15.0
#: between tries
RETRY_EVERY = 0.5


def holders(path: str) -> List[str]:
    """The programs that have `path` open, as "Name (pid N)" -- this very program as "Studio itself". [] when
    none, off Windows, or the Restart Manager couldn't say."""
    if sys.platform != "win32" or not path:
        return []
    try:
        return _rm_holders(os.path.abspath(path))
    except Exception:
        return []


def _rm_holders(path: str) -> List[str]:
    import ctypes
    from ctypes import wintypes

    rm = ctypes.WinDLL("rstrtmgr")

    class RM_UNIQUE_PROCESS(ctypes.Structure):
        _fields_ = [("dwProcessId", wintypes.DWORD), ("ProcessStartTime", wintypes.FILETIME)]

    class RM_PROCESS_INFO(ctypes.Structure):
        _fields_ = [("Process", RM_UNIQUE_PROCESS), ("strAppName", wintypes.WCHAR * 256),
                    ("strServiceShortName", wintypes.WCHAR * 64), ("ApplicationType", ctypes.c_int),
                    ("AppStatus", wintypes.ULONG), ("TSSessionId", wintypes.DWORD), ("bRestartable", wintypes.BOOL)]

    session = wintypes.DWORD()
    key = ctypes.create_unicode_buffer(64)
    if rm.RmStartSession(ctypes.byref(session), 0, key) != 0:
        return []
    try:
        files = (wintypes.LPCWSTR * 1)(path)
        if rm.RmRegisterResources(session, 1, files, 0, None, 0, None) != 0:
            return []
        needed, count, reasons = wintypes.UINT(0), wintypes.UINT(0), wintypes.DWORD(0)
        err = rm.RmGetList(session, ctypes.byref(needed), ctypes.byref(count), None, ctypes.byref(reasons))
        if err not in (0, 234) or needed.value == 0:          # 234: ERROR_MORE_DATA -- there are some
            return []
        info = (RM_PROCESS_INFO * needed.value)()
        count = wintypes.UINT(needed.value)
        if rm.RmGetList(session, ctypes.byref(needed), ctypes.byref(count), info, ctypes.byref(reasons)) != 0:
            return []
        out = []
        me = os.getpid()
        for i in range(count.value):
            pid = int(info[i].Process.dwProcessId)
            name = "Studio itself" if pid == me else (info[i].strAppName or "a program")
            label = name if pid == me else f"{name} (pid {pid})"
            if label not in out:
                out.append(label)
        return out
    finally:
        rm.RmEndSession(session)


def move_when_free(src: str, dest: str, wait: float = WAIT_FOR_FREE, every: float = RETRY_EVERY,
                   mover: Callable[[str, str], object] = shutil.move) -> Optional[str]:
    """Move `src` to `dest`, trying again while the file is in use for up to `wait` seconds. None when moved,
    else why not -- naming who has it open when Windows can say."""
    deadline = time.monotonic() + max(0.0, wait)
    while True:
        try:
            mover(src, dest)
            return None
        except OSError as e:
            if time.monotonic() >= deadline:
                who = holders(src)
                return f"{e}" + (f" -- open in {', '.join(who)}" if who else "")
            time.sleep(every)
