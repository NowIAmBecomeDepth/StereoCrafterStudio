"""child_procs.py -- the ffmpeg / ffprobe processes Studio started, found and ended.

A forced Stop or Quit (audit 2026-09-28 #13) must not leave an encoder running
on after Studio: a job thread that is cut off never reaps the ffmpeg it piped
frames into. Only descendants of this process with those names are touched --
never a player, Explorer or anything else Studio opened for the user.
Windows only (Toolhelp32 via ctypes, no dependency); elsewhere a no-op.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from ctypes import wintypes
from typing import Dict, Iterable, List, Tuple

logger = logging.getLogger("studio.child_procs")

#: Lower-case image names that are Studio's encoders / probes.
NAMES = ("ffmpeg.exe", "ffprobe.exe")

_TH32CS_SNAPPROCESS = 0x00000002
_PROCESS_TERMINATE = 0x0001
_INVALID_HANDLE = ctypes.c_void_p(-1).value


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def _snapshot() -> List[Tuple[int, int, str]]:
    """(pid, parent pid, lower-case exe name) of every process."""
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snap = k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snap or snap == _INVALID_HANDLE:
        return []
    out = []
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            out.append((int(entry.th32ProcessID), int(entry.th32ParentProcessID), entry.szExeFile.lower()))
            ok = k32.Process32NextW(snap, ctypes.byref(entry))
    finally:
        k32.CloseHandle(snap)
    return out


def descendants(root: int, table: Iterable[Tuple[int, int, str]]) -> List[Tuple[int, str]]:
    """(pid, name) of every process below `root` in `table`."""
    kids: Dict[int, List[Tuple[int, str]]] = {}
    for pid, ppid, name in table:
        if pid != ppid:
            kids.setdefault(ppid, []).append((pid, name))
    found, todo, seen = [], [root], {root}
    while todo:
        for pid, name in kids.get(todo.pop(), []):
            if pid not in seen:
                seen.add(pid)
                found.append((pid, name))
                todo.append(pid)
    return found


def kill_encoders(names: Iterable[str] = NAMES) -> List[int]:
    """End every ffmpeg / ffprobe this process started (at any depth).
    Returns the pids ended."""
    if not sys.platform.startswith("win"):
        return []
    wanted = {n.lower() for n in names}
    try:
        targets = [pid for pid, name in descendants(os.getpid(), _snapshot()) if name in wanted]
    except Exception:
        logger.exception("Could not list Studio's child processes")
        return []
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.restype = wintypes.HANDLE
    ended = []
    for pid in targets:
        h = k32.OpenProcess(_PROCESS_TERMINATE, False, pid)
        if not h:
            continue
        try:
            if k32.TerminateProcess(h, 1):
                ended.append(pid)
        finally:
            k32.CloseHandle(h)
    if ended:
        logger.info("Ended %d encoder process(es) left by a stopped job: %s", len(ended), ended)
    return ended
