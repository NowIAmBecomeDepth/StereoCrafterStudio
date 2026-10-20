"""child_job.py -- GPU worker processes that end when Studio does, however it ends.

A worker started in its own process (ROSE's, ~14 GB of the card) isn't ended by Windows when the process
that started it goes: a close, a crash or a kill left it running on, holding the GPU, with nothing left
to stop it (user, 2026-10-15: "tight gpu management ... everything releasing as it should especially
when studio closes"). bind() puts a child in this process's job object, made with KILL_ON_JOB_CLOSE:
the job's one handle is this process's, so when this process ends -- any way at all -- Windows closes
it and ends every child in it.

Only the children bound here: a player or a folder Studio opens for the user is never bound, so it
stays open. Elsewhere than Windows, a no-op.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from ctypes import wintypes

logger = logging.getLogger(__name__)

_JOB = None
_LOCK = threading.Lock()

_JobObjectExtendedLimitInformation = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                  "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _BASIC_LIMIT(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD)]


class _EXTENDED_LIMIT(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BASIC_LIMIT), ("IoInfo", _IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]


def _job():
    """This process's kill-on-close job object (made once), or None."""
    global _JOB
    with _LOCK:
        if _JOB is not None:
            return _JOB
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        job = k32.CreateJobObjectW(None, None)
        if not job:
            raise OSError(ctypes.get_last_error(), "CreateJobObject failed")
        info = _EXTENDED_LIMIT()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not k32.SetInformationJobObject(job, _JobObjectExtendedLimitInformation, ctypes.byref(info),
                                           ctypes.sizeof(info)):
            k32.CloseHandle(job)
            raise OSError(ctypes.get_last_error(), "SetInformationJobObject failed")
        _JOB = job                         # never closed: it closes when this process ends
        return _JOB


def bind(proc) -> bool:
    """Put `proc` (a subprocess.Popen) in the job: it ends when this process does. True when bound; a
    failure is logged, never raised -- the worker still runs, as before."""
    if not sys.platform.startswith("win"):
        return False
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        h = k32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, int(proc.pid))
        if not h:
            raise OSError(ctypes.get_last_error(), "OpenProcess failed")
        try:
            if not k32.AssignProcessToJobObject(_job(), h):
                raise OSError(ctypes.get_last_error(), "AssignProcessToJobObject failed")
        finally:
            k32.CloseHandle(h)
        return True
    except Exception:
        logger.warning("Could not tie process %s to Studio's lifetime; it may outlive a crash.",
                       getattr(proc, "pid", "?"), exc_info=True)
        return False
