"""recycle_bin.py -- send a file to the Windows Recycle Bin.

Through the shell's own SHFileOperationW, so there is no dependency to add
and the file lands in the Recycle Bin exactly as if deleted from Explorer --
restorable from there.

On a drive that has no Recycle Bin (some removable and network drives),
Windows would otherwise delete the file PERMANENTLY and silently. So the drive
is checked first and, without a bin, the file is KEPT (False) with no prompt:
both callers run on a job thread, where Windows' "delete permanently?" box has
no owner window, can sit behind Studio, and stalls the batch until answered
(bug sweep 2026-10-09, A11). A drive whose bin is switched off ("don't move
files to the Recycle Bin") counts as having none.

Something too big for its drive's bin would be deleted for good too. Windows'
own warning for that (FOF_WANTNUKEWARNING) came up from a job thread, ownerless,
and stalled the batch -- so the size is checked here against the bin's own limit,
and Studio asks instead (user, 2026-10-14: "a pop up warning the same way Windows
does, and that deleting will remove it permanently"): ASK_PERMANENT, set by the
app, shows the question and waits for the answer. Kept when it says no, or when
nothing can ask (a test, no app).
"""

from __future__ import annotations

import ctypes
import logging
import os
import shutil
import sys
from ctypes import wintypes
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_FO_DELETE = 0x0003
_FOF_SILENT = 0x0004            # no progress dialog
_FOF_NOCONFIRMATION = 0x0010    # no "are you sure" for an ordinary recycle
_FOF_ALLOWUNDO = 0x0040         # to the Recycle Bin, not gone for good
_FOF_NOERRORUI = 0x0400         # errors come back as a result, not a dialog

#: Asks the user whether to delete `path` (`size` bytes, too big for the Recycle Bin) for good:
#: True to delete it. Set by the app (studio/app.py); None -- a test, no app -- keeps the file.
ASK_PERMANENT: Optional[Callable[[str, int], bool]] = None
#: Why the last send_to_recycle_bin kept its file: "no_bin", "too_big" (asked, kept), "failed", "".
last_kept = ""

_BITBUCKET = r"Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume"


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


class _SHQUERYRBINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("i64Size", ctypes.c_longlong), ("i64NumItems", ctypes.c_longlong)]


_DRIVE_REMOTE = 4


def _bin_settings(path: str) -> dict:
    """The drive's Recycle Bin settings, as Explorer keeps them: {"MaxCapacity": MB,
    "NukeOnDelete": 0/1} ({} when they can't be read)."""
    try:
        import winreg
        root = os.path.splitdrive(os.path.abspath(path))[0] + "\\"
        buf = ctypes.create_unicode_buffer(64)
        if not ctypes.windll.kernel32.GetVolumeNameForVolumeMountPointW(ctypes.c_wchar_p(root), buf, 64):
            return {}
        vol = buf.value
        guid = vol[vol.find("{"):vol.find("}") + 1]
        if not guid:
            return {}
        out = {}
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _BITBUCKET + "\\" + guid) as key:
            for name in ("MaxCapacity", "NukeOnDelete"):
                try:
                    out[name] = int(winreg.QueryValueEx(key, name)[0])
                except OSError:
                    pass
        return out
    except Exception:
        return {}


def bin_capacity(path: str) -> Optional[int]:
    """How much the drive's Recycle Bin takes, in bytes (None: not known)."""
    mb = _bin_settings(path).get("MaxCapacity")
    return int(mb) * 1024 * 1024 if mb and mb > 0 else None


def size_of(path: str) -> int:
    """A file's size, or everything in a folder."""
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(dirpath, f))
            except OSError:
                pass
    return total


def has_recycle_bin(path: str) -> bool:
    """Whether the drive `path` is on has a Recycle Bin: not a network share,
    the shell can query a bin for its root, and the bin isn't switched off."""
    if not sys.platform.startswith("win"):
        return False
    root = os.path.splitdrive(os.path.abspath(path))[0]
    if not root or root.startswith(("\\\\", "//")):
        return False                              # a UNC path: a share, no bin
    root += "\\"
    try:
        if ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root)) == _DRIVE_REMOTE:
            return False
        info = _SHQUERYRBINFO()
        info.cbSize = ctypes.sizeof(info)
        if ctypes.windll.shell32.SHQueryRecycleBinW(ctypes.c_wchar_p(root), ctypes.byref(info)) != 0:
            return False
        # "Don't move files to the Recycle Bin" on this drive: it would delete for good
        return not _bin_settings(root).get("NukeOnDelete")
    except Exception:
        return False


def send_to_recycle_bin(path: str) -> bool:
    """Move `path` to the Recycle Bin. True if it is gone from its folder.
    False, and the file kept, when its drive has no bin (see the module doc)."""
    if not sys.platform.startswith("win"):
        logger.warning("Recycle Bin is Windows-only; leaving %s in place", path)
        return False
    global last_kept
    last_kept = ""
    full = os.path.abspath(path)
    if not os.path.exists(full):
        return False
    if not has_recycle_bin(full):
        logger.warning("Kept %s: its drive has no Recycle Bin, and deleting it there would be for good.", full)
        last_kept = "no_bin"
        return False
    cap = bin_capacity(full)
    size = size_of(full) if cap is not None else 0
    if cap is not None and size > cap:
        # too big for the bin: Windows would delete it for good -- asked first, in Studio
        ask = ASK_PERMANENT
        yes = False
        if ask is not None:
            try:
                yes = bool(ask(full, size))
            except Exception:
                logger.exception("Couldn't ask about deleting %s for good", full)
        if not yes:
            logger.warning("Kept %s: it's too big for the Recycle Bin (%.1f GB, the bin holds %.1f GB).",
                           full, size / 2 ** 30, cap / 2 ** 30)
            last_kept = "too_big"
            return False
        try:
            if os.path.isdir(full):
                shutil.rmtree(full)
            else:
                os.remove(full)
        except OSError as e:
            logger.warning("Couldn't delete %s: %s", full, e)
        gone = not os.path.exists(full)
        if gone:
            logger.info("Deleted %s for good (too big for the Recycle Bin; asked).", full)
        return gone
    op = _SHFILEOPSTRUCTW()
    op.wFunc = _FO_DELETE
    # A list of paths, each NUL-terminated, the whole list ending in a second
    # NUL -- ctypes adds one, this is the other.
    op.pFrom = full + "\0"
    # no FOF_WANTNUKEWARNING: its box came up from job threads and stalled them; the size is
    # checked above instead (and Studio asks)
    op.fFlags = (_FOF_ALLOWUNDO | _FOF_NOCONFIRMATION | _FOF_SILENT | _FOF_NOERRORUI)
    try:
        result = ctypes.WinDLL("shell32").SHFileOperationW(ctypes.byref(op))
    except Exception:
        logger.exception("Could not send %s to the Recycle Bin", full)
        return False
    if result != 0 or op.fAnyOperationsAborted:
        logger.warning("Recycle Bin refused %s (code %s)", full, result)
        return False
    return not os.path.exists(full)
