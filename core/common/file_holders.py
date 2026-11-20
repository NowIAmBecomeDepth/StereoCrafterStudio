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


#: video readers that don't keep their file's name, listed by owner whatever file they read
_NAMELESS_READERS = ("VideoReader",)


def own_holders(path: str, limit: int = 12) -> List[str]:
    """What inside this process may still have `path` open, as "Owner.attribute -> Reader" lines (tester carlobs,
    2026-11-20: "open in Studio itself" twice, and no repro here). Every live object that names the file in an
    attribute (a reader's path, an open file's name), and every decord VideoReader alive (it keeps no name), each
    with the objects holding it. Slow (it walks every object): only after a move has failed."""
    import gc
    import io

    want = os.path.normcase(os.path.abspath(path)) if path else ""
    base = os.path.basename(want)

    def names_it(v) -> bool:
        if not isinstance(v, str) or base not in v.lower():
            return False
        try:
            return os.path.normcase(os.path.abspath(v)) == want or want in os.path.normcase(v)
        except (TypeError, ValueError):
            return False

    hits = []
    for obj in gc.get_objects():
        try:
            kind = type(obj).__name__
            if isinstance(obj, io.IOBase):
                if not getattr(obj, "closed", True) and names_it(getattr(obj, "name", None)):
                    hits.append(obj)
                continue
            d = getattr(obj, "__dict__", None) if not isinstance(obj, type) else None
            if not isinstance(d, dict):
                continue
            if kind in _NAMELESS_READERS or any(names_it(v) for v in d.values()):
                hits.append(obj)
        except Exception:
            continue
        if len(hits) >= limit:
            break

    def owners(obj) -> List[str]:
        out = []
        for ref in gc.get_referrers(obj):
            if ref is hits or isinstance(ref, type(sys._getframe())):
                continue
            if isinstance(ref, dict):
                attr = next((k for k, v in ref.items() if v is obj), "?")
                owner = next((o for o in gc.get_referrers(ref) if getattr(o, "__dict__", None) is ref), None)
                out.append(f"{type(owner).__name__}.{attr}" if owner is not None else f"a dict['{attr}']")
            elif isinstance(ref, (list, tuple, set)):
                out.append(f"a {type(ref).__name__}")
            elif getattr(ref, "__dict__", None) is not None and not isinstance(ref, type):
                out.append(type(ref).__name__)
        return sorted(set(out))[:6]

    raws = {id(getattr(h, "raw", None)) for h in hits if isinstance(h, io.IOBase)}
    lines = []
    for obj in hits:
        if id(obj) in raws:
            continue                             # an open file's raw handle: its buffer is listed already
        what = type(obj).__module__ + "." + type(obj).__name__
        held = owners(obj)
        lines.append(f"{what} held by {', '.join(held) if held else 'no object (a running function, or garbage)'}")
    hits.clear()
    return lines


def _log_own_holders(path: str) -> None:
    import logging
    log = logging.getLogger(__name__)
    try:
        lines = own_holders(path)
    except Exception:
        log.debug("Couldn't list what in Studio has %s open", path, exc_info=True)
        return
    log.warning("%s is open in Studio itself; what in Studio may hold it:%s", os.path.basename(path),
                "".join(f"\n    {s}" for s in lines) if lines else " nothing found by name (a native reader?)")


def move_when_free(src: str, dest: str, wait: float = WAIT_FOR_FREE, every: float = RETRY_EVERY,
                   mover: Callable[[str, str], object] = shutil.move) -> Optional[str]:
    """Move `src` to `dest`, trying again while the file is in use for up to `wait` seconds. None when moved,
    else why not -- naming who has it open when Windows can say."""
    deadline = time.monotonic() + max(0.0, wait)
    collected = False
    while True:
        try:
            mover(src, dest)
            return None
        except OSError as e:
            if not collected:
                # a reader Studio has dropped but not yet collected (a reference cycle) keeps its file open until
                # the garbage collector runs (tester carlobs, 2026-11-18: "open in Studio itself" after the
                # previews had let go): once, then the move again at once
                collected = True
                import gc
                gc.collect()
                continue
            if time.monotonic() >= deadline:
                who = holders(src)
                if any(w == "Studio itself" for w in who):
                    _log_own_holders(src)
                return f"{e}" + (f" -- open in {', '.join(who)}" if who else "")
            time.sleep(every)
