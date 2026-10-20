"""taskbar_win (user, 2026-10-09): one taskbar button for a pinned Studio -- the
window carries the AppUserModelID and the relaunch command a pin will use."""
from __future__ import annotations

import ctypes
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import studio.tests  # noqa: E402,F401  -- the data redirect first: tests never touch the user's data
from studio import taskbar_win as T  # noqa: E402

@unittest.skipUnless(sys.platform == "win32", "Windows only")
class TaskbarTests(unittest.TestCase):
    def read(self, hwnd):
        store = ctypes.c_void_p()
        ctypes.windll.shell32.SHGetPropertyStoreForWindow(ctypes.c_void_p(hwnd), ctypes.byref(T._IID_IPropertyStore),
                                                          ctypes.byref(store))
        vt = ctypes.cast(ctypes.cast(store, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
        get = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(T._PROPERTYKEY),
                                 ctypes.POINTER(T._PROPVARIANT))(vt[5])
        out = {}
        for pid in (T._PKEY_ID, T._PKEY_RELAUNCH_COMMAND, T._PKEY_RELAUNCH_NAME, T._PKEY_RELAUNCH_ICON):
            pv = T._PROPVARIANT()
            get(store, ctypes.byref(T._PROPERTYKEY(T._GUID.of(T._AUMID_FMT), pid)), ctypes.byref(pv))
            out[pid] = pv.pwszVal
        ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vt[2])(store)
        return out

    def test_the_window_carries_the_id_and_the_relaunch_command(self):
        # a real hidden Win32 window: the suite's Qt runs offscreen (no HWNDs)
        user32 = ctypes.windll.user32
        user32.CreateWindowExW.restype = ctypes.c_void_p
        user32.CreateWindowExW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32,
                                           ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                           ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        user32.DestroyWindow.argtypes = [ctypes.c_void_p]
        hwnd = user32.CreateWindowExW(0, "STATIC", "taskbar test", 0, 0, 0, 10, 10, None, None, None, None)
        self.assertTrue(hwnd)
        self.addCleanup(user32.DestroyWindow, hwnd)
        icon = T._REPO / "assets" / "StereoCrafter.ico"
        self.assertTrue(T.set_window_properties(hwnd, icon, "StereoCrafter.Studio.Test"))
        got = self.read(hwnd)
        self.assertEqual(got[T._PKEY_ID], "StereoCrafter.Studio.Test")
        self.assertEqual(got[T._PKEY_RELAUNCH_COMMAND], T.relaunch_command())
        self.assertEqual(got[T._PKEY_RELAUNCH_NAME], "StereoCrafter Studio")
        self.assertEqual(got[T._PKEY_RELAUNCH_ICON], str(icon))

    def test_a_pin_starts_the_launcher(self):
        self.assertIn("StereoCrafter Studio.", T.relaunch_command())
        self.assertTrue(T.relaunch_command().startswith('"') and T.relaunch_command().endswith('"'))

    def _make_link(self, path, target, icon):
        ole32 = ctypes.windll.ole32
        ole32.CoInitializeEx(None, 2)
        psl, ppf = ctypes.c_void_p(), ctypes.c_void_p()
        ole32.CoCreateInstance.argtypes = [ctypes.POINTER(T._GUID), ctypes.c_void_p, ctypes.c_uint32,
                                           ctypes.POINTER(T._GUID), ctypes.POINTER(ctypes.c_void_p)]
        self.assertEqual(ole32.CoCreateInstance(ctypes.byref(T._CLSID_ShellLink), None, 1,
                                                ctypes.byref(T._IID_IShellLinkW), ctypes.byref(psl)), 0)
        T._method(psl, 0, ctypes.POINTER(T._GUID), ctypes.POINTER(ctypes.c_void_p))(
            psl, ctypes.byref(T._IID_IPersistFile), ctypes.byref(ppf))
        T._method(psl, 20, ctypes.c_wchar_p)(psl, str(target))                          # SetPath
        T._method(psl, 17, ctypes.c_wchar_p, ctypes.c_int)(psl, str(icon), 0)          # SetIconLocation
        self.assertEqual(T._method(ppf, 6, ctypes.c_wchar_p, ctypes.c_int)(ppf, str(path), 1), 0)
        for obj in (ppf, psl):
            T._method(obj, 2)(obj)

    def _icon_of(self, path):
        import subprocess
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              f"(New-Object -ComObject WScript.Shell).CreateShortcut('{path}').IconLocation"],
                             capture_output=True, text=True).stdout.strip()
        return out

    def test_the_picker_re_icons_only_our_pins(self):
        # temp shortcuts only -- never the user's real pins
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        ours, other = tmp / "ours.exe", tmp / "other.exe"
        for f in (ours, other):
            f.write_bytes(b"")
        old_icon, new_icon = tmp / "old.ico", tmp / "new.ico"
        (tmp / "pins" / "sub").mkdir(parents=True)
        self._make_link(tmp / "pins" / "sub" / "Studio.lnk", ours, old_icon)
        self._make_link(tmp / "pins" / "Other.lnk", other, old_icon)
        self.assertEqual(T.update_pinned_icon(new_icon, folders=[tmp / "pins"], target=str(ours), desktops=[]), 1)
        self.assertEqual(self._icon_of(tmp / "pins" / "sub" / "Studio.lnk"), f"{new_icon},0")
        self.assertEqual(self._icon_of(tmp / "pins" / "Other.lnk"), f"{old_icon},0", "someone else's pin changed")
        self.assertEqual(T.update_pinned_icon(new_icon, folders=[tmp / "pins"], target=str(ours), desktops=[]), 0,
                         "an unchanged icon is not written again")

    def test_desktop_shortcuts_follow_but_not_the_folders_on_it(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        ours = tmp / "ours.exe"
        ours.write_bytes(b"")
        (tmp / "desk" / "a folder").mkdir(parents=True)
        self._make_link(tmp / "desk" / "Studio.lnk", ours, "")
        self._make_link(tmp / "desk" / "a folder" / "Studio copy.lnk", ours, "")
        n = T.update_pinned_icon(tmp / "new.ico", folders=[], target=str(ours), desktops=[tmp / "desk"])
        self.assertEqual(n, 1)
        self.assertEqual(self._icon_of(tmp / "desk" / "Studio.lnk"), f"{tmp / 'new.ico'},0")
        self.assertEqual(self._icon_of(tmp / "desk" / "a folder" / "Studio copy.lnk"), ",0",
                         "a shortcut inside a folder on the desktop was changed")

    def test_icon_changed_waits_for_the_real_window(self):
        from unittest import mock
        with mock.patch.object(T, "_window_hwnd", 0), mock.patch.object(T, "update_pinned_icon") as up:
            T.icon_changed(Path("x.ico"))
        up.assert_not_called()

    def test_no_window_is_a_quiet_no(self):
        self.assertFalse(T.set_window_properties(0, Path("x.ico")))


if __name__ == "__main__":
    unittest.main()
