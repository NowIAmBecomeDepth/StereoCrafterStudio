"""taskbar_win.py -- one taskbar button for Studio, pinned or running (user, 2026-10-09).

Windows groups taskbar buttons by AppUserModelID. Without one, Studio's window
belongs to pythonw.exe while the pin belongs to "StereoCrafter Studio.exe", so
a pinned Studio showed two buttons once it was open. So:

* the process takes an explicit ID before any window exists;
* the main window carries that ID plus the relaunch properties -- the command
  (the launcher .exe beside the repo, or the .bat), its name and its icon --
  so "Pin to taskbar" on the running window makes a pin that starts the
  launcher and groups with the window.

A pin made from the .exe before this has no ID: unpin it and pin the running
window once. Plain ctypes COM (IPropertyStore); nothing to install. Every call
steps aside on failure: a taskbar nicety never stops Studio.

A grouped button shows the PIN's icon, not the window's, so the App Icon
picker also re-icons Studio's own pinned shortcuts (those that start our
launcher) and tells Explorer to refresh -- only once the real main window has
been set up here (``icon_changed``), so the checks never touch the user's pins.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from ctypes import wintypes
from pathlib import Path

logger = logging.getLogger("studio.taskbar_win")

APP_ID = "StereoCrafter.Studio"
_REPO = Path(__file__).resolve().parents[1]
_window_hwnd = 0            # the main window, once set_window_properties has dressed it


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def of(cls, text: str) -> "_GUID":
        h = text.strip("{}").replace("-", "")
        return cls(int(h[0:8], 16), int(h[8:12], 16), int(h[12:16], 16),
                   (ctypes.c_ubyte * 8)(*bytes.fromhex(h[16:32])))


class _PROPERTYKEY(ctypes.Structure):
    _fields_ = [("fmtid", _GUID), ("pid", wintypes.DWORD)]


class _PROPVARIANT(ctypes.Structure):
    # vt + three reserved words, then the value; VT_LPWSTR holds a pointer
    _fields_ = [("vt", ctypes.c_ushort), ("r1", ctypes.c_ushort), ("r2", ctypes.c_ushort),
                ("r3", ctypes.c_ushort), ("pwszVal", ctypes.c_wchar_p), ("pad", ctypes.c_void_p)]


_VT_LPWSTR = 31
_IID_IPropertyStore = _GUID.of("{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}")
_AUMID_FMT = "{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}"
_PKEY_ID = 5
_PKEY_RELAUNCH_COMMAND = 2
_PKEY_RELAUNCH_ICON = 3
_PKEY_RELAUNCH_NAME = 4


def set_process_app_id(app_id: str = APP_ID) -> bool:
    """Before the first window: every window of this process groups under app_id."""
    if sys.platform != "win32":
        return False
    try:
        return ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(ctypes.c_wchar_p(app_id)) == 0
    except Exception:
        logger.debug("Could not set the AppUserModelID", exc_info=True)
        return False


def relaunch_command() -> str:
    """What a pin of the window starts: the launcher .exe, else the .bat."""
    exe = _REPO / "StereoCrafter Studio.exe"
    if exe.exists():
        return f'"{exe}"'
    return f'"{_REPO / "StereoCrafter Studio.bat"}"'


def set_window_properties(hwnd: int, icon: Path, app_id: str = APP_ID) -> bool:
    """The window's ID and relaunch command, name and icon (for "Pin to taskbar")."""
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        store = ctypes.c_void_p()
        get = ctypes.windll.shell32.SHGetPropertyStoreForWindow
        get.argtypes = [wintypes.HWND, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
        if get(wintypes.HWND(hwnd), ctypes.byref(_IID_IPropertyStore), ctypes.byref(store)) != 0 or not store:
            return False
        vtbl = ctypes.cast(ctypes.cast(store, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
        release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtbl[2])
        set_value = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(_PROPERTYKEY),
                                       ctypes.POINTER(_PROPVARIANT))(vtbl[6])
        try:
            fmt = _GUID.of(_AUMID_FMT)
            values = [(_PKEY_RELAUNCH_COMMAND, relaunch_command()),
                      (_PKEY_RELAUNCH_NAME, "StereoCrafter Studio"),
                      (_PKEY_RELAUNCH_ICON, str(icon)),
                      (_PKEY_ID, app_id)]                  # the ID last, as the docs ask
            ok = True
            for pid, text in values:
                pv = _PROPVARIANT(vt=_VT_LPWSTR, pwszVal=text)   # SetValue copies it
                ok = set_value(store, ctypes.byref(_PROPERTYKEY(fmt, pid)), ctypes.byref(pv)) == 0 and ok
            if ok and app_id == APP_ID:
                global _window_hwnd
                _window_hwnd = hwnd
            return ok
        finally:
            release(store)
    except Exception:
        logger.debug("Could not set the window's taskbar properties", exc_info=True)
        return False


# ---- the pinned shortcut's icon -----------------------------------------------------
_CLSID_ShellLink = _GUID.of("{00021401-0000-0000-C000-000000000046}")
_IID_IShellLinkW = _GUID.of("{000214F9-0000-0000-C000-000000000046}")
_IID_IPersistFile = _GUID.of("{0000010B-0000-0000-C000-000000000046}")


def _method(obj, index, *argtypes):
    vtbl = ctypes.cast(ctypes.cast(obj, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
    return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtbl[index])


def pinned_folders() -> list:
    """Where the taskbar keeps its pins: .lnk files, the window-made ones under ImplicitAppShortcuts."""
    base = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Internet Explorer" / "Quick Launch" / "User Pinned"
    return [base / "TaskBar", base / "ImplicitAppShortcuts"]


def desktop_folders() -> list:
    """The user's desktop (wherever OneDrive moved it) and the public one -- read
    at their top level only, never the folders on them."""
    out = []
    for csidl in (0x10, 0x19):                  # CSIDL_DESKTOPDIRECTORY, CSIDL_COMMON_DESKTOPDIRECTORY
        buf = ctypes.create_unicode_buffer(260)
        if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf) == 0 and buf.value:
            out.append(Path(buf.value))
    return out


def update_pinned_icon(icon: Path, folders=None, target: str = "", desktops=None) -> int:
    """Re-icon every pinned shortcut -- and every shortcut on the desktop -- that
    starts `target` (default: our launcher). Returns how many changed; shortcuts
    already showing `icon` are left alone. A public-desktop one needs admin
    rights to change and is skipped quietly without them."""
    if sys.platform != "win32":
        return 0
    target = os.path.normcase(os.path.abspath(target or relaunch_command().strip('"')))
    links = []
    for folder in (pinned_folders() if folders is None else folders):
        try:
            links += [p for p in Path(folder).rglob("*.lnk")]
        except OSError:
            pass
    for folder in (desktop_folders() if desktops is None else desktops):
        try:
            links += [p for p in Path(folder).glob("*.lnk")]
        except OSError:
            pass
    if not links:
        return 0
    ole32 = ctypes.windll.ole32
    ole32.CoCreateInstance.argtypes = [ctypes.POINTER(_GUID), ctypes.c_void_p, ctypes.c_uint32,
                                       ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p)]
    inited = ole32.CoInitializeEx(None, 2) == 0           # S_OK: ours to undo; S_FALSE / changed mode: not
    changed = 0
    try:
        for link in links:
            psl, ppf = ctypes.c_void_p(), ctypes.c_void_p()
            try:
                if ole32.CoCreateInstance(ctypes.byref(_CLSID_ShellLink), None, 1, ctypes.byref(_IID_IShellLinkW),
                                          ctypes.byref(psl)) != 0:
                    continue
                if _method(psl, 0, ctypes.POINTER(_GUID), ctypes.POINTER(ctypes.c_void_p))(
                        psl, ctypes.byref(_IID_IPersistFile), ctypes.byref(ppf)) != 0:
                    continue
                if _method(ppf, 5, ctypes.c_wchar_p, ctypes.c_uint32)(ppf, str(link), 2) != 0:   # Load, read/write
                    continue
                buf = ctypes.create_unicode_buffer(1024)
                _method(psl, 3, ctypes.c_wchar_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32)(psl, buf, 1024, None, 0)
                if not buf.value or os.path.normcase(os.path.abspath(buf.value)) != target:
                    continue
                cur, idx = ctypes.create_unicode_buffer(1024), ctypes.c_int(0)
                _method(psl, 16, ctypes.c_wchar_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int))(psl, cur, 1024, ctypes.byref(idx))
                if os.path.normcase(cur.value) == os.path.normcase(str(icon)) and idx.value == 0:
                    continue
                _method(psl, 17, ctypes.c_wchar_p, ctypes.c_int)(psl, str(icon), 0)               # SetIconLocation
                if _method(ppf, 6, ctypes.c_wchar_p, ctypes.c_int)(ppf, None, 1) == 0:            # Save
                    changed += 1
            except Exception:
                logger.debug("Could not re-icon %s", link, exc_info=True)
            finally:
                for obj in (ppf, psl):
                    if obj:
                        _method(obj, 2)(obj)
    finally:
        if inited:
            ole32.CoUninitialize()
    if changed:
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)                           # SHCNE_ASSOCCHANGED
    return changed


def icon_changed(icon: Path) -> None:
    """The App Icon picker chose `icon`: the window's relaunch icon and Studio's pins follow.
    Nothing before the real main window is set up (the checks build windows of their own)."""
    if not _window_hwnd:
        return
    try:
        set_window_properties(_window_hwnd, icon)
        n = update_pinned_icon(icon)
        if n:
            logger.info("Taskbar pin re-iconed (%d shortcut%s)", n, "" if n == 1 else "s")
    except Exception:
        logger.debug("Could not follow the icon on the taskbar", exc_info=True)
