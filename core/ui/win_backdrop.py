"""Frosted-glass window backdrops on Windows.

This is a DWM effect, not a widget-toolkit feature: no Tk (or CustomTkinter)
widget can produce it, and none is needed to. Windows blurs what sits BEHIND a
window; the blur is then visible only wherever the window itself is
transparent. Tk paints its client area opaque, so on its own the blur is hidden
completely.

The way through is a chroma key. One colour is nominated with
``wm attributes -transparentcolor`` and every pixel Tk paints in exactly that
colour is punched out of the window, revealing the blur behind it. Two
consequences follow, and neither can be engineered away:

  * Only what you do NOT draw is frosted. A widget with a normal background is
    an opaque rectangle sitting on the glass.
  * Text drawn against the key colour fringes at the glyph edges, because it
    was antialiased against a colour that is no longer there.
  * Punched-out pixels are click-through: Windows hands a click on one to
    whatever is behind the window. That one IS worked around, by the base
    layer at the bottom of this module -- an all-but-invisible window kept
    directly underneath, which catches those clicks instead.

Everything here is a no-op off Windows and on any build that refuses the call,
so callers never need to guard.
"""

import ctypes
import logging
import sys
from ctypes import wintypes
from typing import Any

logger = logging.getLogger(__name__)

#: The colour punched out of a window. Nothing in any palette may equal it, or
#: that widget would go transparent by accident -- hence a near-black no
#: designer would reach for.
CHROMA_KEY = "#010203"

# SetWindowCompositionAttribute is undocumented but has been stable since
# Windows 10 1803, and is the only way to ask for acrylic on a window whose
# composition you do not own.
_WCA_ACCENT_POLICY = 19
_ACCENT_DISABLED = 0
_ACCENT_ENABLE_BLURBEHIND = 3
_ACCENT_ENABLE_ACRYLICBLURBEHIND = 4

# Windows 11 only.
_DWMWA_SYSTEMBACKDROP_TYPE = 38
_DWMSBT_NONE = 1
_DWMSBT_MAINWINDOW = 2  # Mica

#: Accepted by :func:`apply`.
MODES = ("none", "acrylic", "blur", "mica")

#: The user's preference, process-wide: View > Turn On Frosted Glass.
#:
#: Deliberately not folded into :func:`is_supported`, which answers a
#: different question -- whether Windows CAN do it. Both readers consult this
#: on every theme pass (the stages in core.ui.ctk_theme, the console in
#: unified_gui.console_window), so setting it and re-applying the theme is all
#: it takes to put every window onto the backdrop or take them all off.
_enabled = True


def is_enabled() -> bool:
    """Whether the user wants frosted glass at all."""
    return _enabled


def set_enabled(on: bool) -> None:
    """Record the preference.

    Windows already on glass are NOT changed here: each one settles itself on
    its next theme pass, which is also when it knows the palette it would be
    tinted with. Callers re-apply the theme after this.
    """
    global _enabled
    _enabled = bool(on)


#: Whether the click-through warning has been shown yet in this process.
_warned = False


def warn_click_through(window: Any = None) -> bool:
    """Tell the user once per run what turning the glass on costs them.

    Only what is NOT drawn is frosted, and those punched-out pixels are
    click-through: a click landing on a gap between cards goes to whatever is
    behind the window -- the desktop, another app -- and this process never
    sees it. That is inherent to the colour key, not a rough edge waiting to
    be filed off. The alternative was measured and rejected:
    DwmExtendFrameIntoClientArea keeps the clicks, but it switches the client
    area to per-pixel alpha compositing, and everything Tk paints through GDI
    carries alpha 0 -- so the whole window, cards and text and controls alike,
    composites away to nothing.

    This lives here rather than in the menus because the cost belongs to the
    mechanism, and there are five separate switches for it: Studio's View menu
    and the Options menu of each standalone stage.

    Called from those menu handlers only, never from :func:`set_enabled`, so
    restoring a saved preference at startup stays silent -- someone who left
    it on last time has already been told. Returns whether a dialog was shown.

    Silent, too, while the base layer is doing its job (see _Backplate): the
    holes still exist, but a click through one now lands on this app's own
    layer instead of the desktop, so there is nothing to warn about. Only a
    window whose layer could not be made still has the old problem.
    """
    global _warned
    if _warned or not is_supported() or not _backplate_failed:
        return False
    _warned = True
    try:
        from tkinter import messagebox

        messagebox.showwarning(
            "Frosted Glass",
            "Frosted glass works by punching transparent holes in the window. "
            "That is the only way to show the Windows blur behind it.\n\n"
            "Those transparent areas are CLICK-THROUGH: a click that lands on "
            "an empty gap goes to whatever is behind this window, not to this "
            "app. You can pick up a desktop icon through a gap.\n\n"
            "Dark themes are affected most -- they make the section boxes "
            "transparent as well as the page. Light themes make only the page "
            "transparent, so only the margins are affected.\n\n"
            "Turn it off again from the same menu item if it gets in the way.",
            parent=window,
        )
        return True
    except Exception:
        logger.debug("Could not show the frosted-glass warning", exc_info=True)
        return False


class _ACCENT_POLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", ctypes.c_uint),   # AABBGGRR
        ("AnimationId", ctypes.c_int),
    ]


class _COMPOSITION_ATTRIB_DATA(ctypes.Structure):
    _fields_ = [
        ("Attribute", ctypes.c_int),
        ("Data", ctypes.POINTER(_ACCENT_POLICY)),
        ("SizeOfData", ctypes.c_size_t),
    ]


def is_supported() -> bool:
    """True when this platform can do any of it."""
    if not sys.platform.startswith("win"):
        return False
    try:
        return hasattr(ctypes.windll.user32, "SetWindowCompositionAttribute")
    except Exception:
        return False


def _frame_hwnd(window: Any) -> int:
    """The FRAME handle. winfo_id() returns Tk's client window, a child of it."""
    # Tk gives a window its native frame when it is first mapped, so only a
    # window not yet on screen needs the flush. A mapped one asking -- as
    # monitor_size does after every move and resize -- paid for a flush of
    # the whole app's pending layout just to read a handle it already had.
    if not window.winfo_ismapped():
        window.update_idletasks()
    client = window.winfo_id()
    return ctypes.windll.user32.GetParent(client) or client


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _RECT),
                ("rcWork", _RECT), ("dwFlags", ctypes.c_ulong)]


_MONITOR_DEFAULTTONEAREST = 2


def monitor_size(window: Any):
    """(width, height) of the display `window` is on, or None if unknown.

    Not the same question as winfo_screenwidth/height, which only ever answer
    for the PRIMARY display: a window opened on a second monitor of a
    different size would otherwise be measured against a screen it is not on.
    """
    if not sys.platform.startswith("win"):
        return None
    try:
        hwnd = _frame_hwnd(window)
        if not hwnd:
            return None
        handle = ctypes.windll.user32.MonitorFromWindow(
            wintypes.HWND(hwnd), _MONITOR_DEFAULTTONEAREST
        )
        if not handle:
            return None
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if not ctypes.windll.user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            return None
        rect = info.rcMonitor
        return (rect.right - rect.left, rect.bottom - rect.top)
    except Exception:
        logger.debug("Could not identify the window's monitor", exc_info=True)
        return None


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def work_area_at(x: int, y: int):
    """(left, top, right, bottom) of the usable area of the monitor at (x, y).

    The WORK area: the monitor less the taskbar and anything docked. None
    off Windows or on failure. Screen coordinates, the same ones Tk's
    winfo_rootx/rooty report (Studio's process is DPI-aware).
    """
    if not sys.platform.startswith("win"):
        return None
    try:
        user32 = ctypes.WinDLL("user32")
        user32.MonitorFromPoint.argtypes = [_POINT, wintypes.DWORD]
        user32.MonitorFromPoint.restype = wintypes.HMONITOR
        handle = user32.MonitorFromPoint(_POINT(int(x), int(y)), _MONITOR_DEFAULTTONEAREST)
        if not handle:
            return None
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if not user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            return None
        rect = info.rcWork
        return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        logger.debug("Could not read the monitor work area", exc_info=True)
        return None


def _gradient(hex_color: str, alpha: int) -> int:
    """A tint colour as DWM wants it: AABBGGRR."""
    named = {"white": "#ffffff", "black": "#000000"}
    h = str(named.get(str(hex_color).strip().lower(), hex_color)).lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        r = g = b = 0
    return ((alpha & 0xFF) << 24) | (b << 16) | (g << 8) | r


def set_chroma_key(window: Any, enabled: bool, key: str = CHROMA_KEY) -> bool:
    """Turn the colour key on or off for `window`.

    Without this the blur is applied but nothing shows it, because Tk has
    painted over every pixel of the client area.
    """
    try:
        window.attributes("-transparentcolor", key if enabled else "")
    except Exception:
        logger.debug("transparentcolor not available on this window", exc_info=True)
        return False
    if enabled:
        _attach_backplate(window)
    else:
        _detach_backplate(window)
    return True


def apply(window: Any, mode: str, tint: str = "#000000", alpha: int = 150) -> bool:
    """Put `mode` on `window`; "none" clears it. Returns True if Windows took it.

    `tint` and `alpha` colour the acrylic. A heavier alpha buries the wallpaper
    and keeps text readable; a lighter one shows more of the desktop.
    """
    if not sys.platform.startswith("win"):
        return False
    mode = (mode or "none").lower()
    try:
        hwnd = _frame_hwnd(window)
        if not hwnd:
            return False

        # Mica and the accent policy are separate APIs. Clear whichever is not
        # wanted, so switching modes at runtime cannot stack them.
        backdrop = ctypes.c_int(_DWMSBT_MAINWINDOW if mode == "mica" else _DWMSBT_NONE)
        try:
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(hwnd), wintypes.DWORD(_DWMWA_SYSTEMBACKDROP_TYPE),
                ctypes.byref(backdrop), ctypes.sizeof(backdrop),
            )
        except Exception:
            pass  # Windows 10: no such attribute, and nothing is lost by it.

        state = {
            "acrylic": _ACCENT_ENABLE_ACRYLICBLURBEHIND,
            "blur": _ACCENT_ENABLE_BLURBEHIND,
        }.get(mode, _ACCENT_DISABLED)
        policy = _ACCENT_POLICY(state, 2, _gradient(tint, alpha), 0)
        data = _COMPOSITION_ATTRIB_DATA(
            _WCA_ACCENT_POLICY, ctypes.pointer(policy), ctypes.sizeof(policy)
        )
        set_attr = ctypes.windll.user32.SetWindowCompositionAttribute
        return bool(set_attr(wintypes.HWND(hwnd), ctypes.byref(data)))
    except Exception:
        logger.debug("Window backdrop unavailable", exc_info=True)
        return False


def clear(window: Any) -> None:
    """Back to an ordinary opaque window: no blur, no colour key."""
    apply(window, "none")
    set_chroma_key(window, False)


# --- the base layer ---------------------------------------------------------
#
# A keyed pixel is a hole as far as Windows' hit-testing is concerned, so a
# click on the glass used to land on whatever was behind the app -- the
# desktop, another program. The base layer is a window of its own, kept
# directly beneath a glass window and covering it exactly, at 1/255 opacity:
# nothing to the eye, and not a hole, so it is what those clicks land on
# instead. A click on it brings its window forward, as a click on a solid
# part of the window would.
#
# It comes and goes with the colour key (set_chroma_key), so it exists only
# while a window is actually on glass.
#
# It is deliberately NOT owned by the glass window: Windows always stacks an
# owned window ABOVE its owner, which is the wrong side. It is re-stacked
# beneath it instead, on a short poll that also catches the window being moved,
# resized, brought to the front, minimised or hidden. During a title-bar drag
# it can trail by a frame, which costs nothing -- the mouse is busy dragging.

_BACKPLATE_POLL_MS = 16
#: Set if any window's layer could not be made; warn_click_through reads it.
_backplate_failed = False

_GWL_EXSTYLE = -20
_GWLP_HWNDPARENT = -8
_GW_HWNDNEXT = 2
_GA_ROOT = 2
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_APPWINDOW = 0x00040000
_WS_EX_LAYERED = 0x00080000
_WS_EX_NOACTIVATE = 0x08000000
_LWA_ALPHA = 0x2
_SWP_NOACTIVATE = 0x0010
_SWP_SHOWWINDOW = 0x0040
_SW_HIDE = 0
_DWMWA_TRANSITIONS_FORCEDISABLED = 3

_api = None


def _user32():
    """user32 with the signatures the layer needs.

    Its own WinDLL instance, so these argtypes do not leak onto
    ctypes.windll.user32, which the rest of the app calls with plain ints.
    """
    global _api
    if _api is None:
        u = ctypes.WinDLL("user32")
        long_ptr = ctypes.c_ssize_t
        u.GetWindowLongPtrW.restype = long_ptr
        u.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        u.SetWindowLongPtrW.restype = long_ptr
        u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, long_ptr]
        u.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD,
                                                 ctypes.c_ubyte, wintypes.DWORD]
        u.GetAncestor.restype = wintypes.HWND
        u.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
        u.GetWindow.restype = wintypes.HWND
        u.GetWindow.argtypes = [wintypes.HWND, ctypes.c_uint]
        u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_RECT)]
        u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        for name in ("IsWindow", "IsWindowVisible", "IsIconic"):
            getattr(u, name).argtypes = [wintypes.HWND]
        u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        _api = u
    return _api


class _Backplate:
    """The base layer under one glass window."""

    def __init__(self, window: Any):
        import tkinter as tk

        self.window = window
        self._rect = None
        self._job = None
        # A child of the glass window in Tk's tree, so Tk destroys it along
        # with the window -- but not a child or an owned window in Windows'
        # sense (see above).
        self.top = tk.Toplevel(window)
        self.top._sc_is_backplate = True
        self.top.overrideredirect(True)
        self.top.configure(bg="#000000")
        self.top.geometry("1x1+-32000+-32000")
        self.hwnd = _frame_hwnd(self.top)

        u = _user32()
        ex = u.GetWindowLongPtrW(self.hwnd, _GWL_EXSTYLE)
        ex |= _WS_EX_LAYERED | _WS_EX_TOOLWINDOW | _WS_EX_NOACTIVATE
        ex &= ~(_WS_EX_APPWINDOW | _WS_EX_TRANSPARENT)
        u.SetWindowLongPtrW(self.hwnd, _GWL_EXSTYLE, ex)
        u.SetWindowLongPtrW(self.hwnd, _GWLP_HWNDPARENT, 0)
        # 1 of 255: invisible, but not zero -- a fully transparent layered
        # pixel is a hole again.
        if not u.SetLayeredWindowAttributes(self.hwnd, 0, 1, _LWA_ALPHA):
            self.top.destroy()
            raise OSError("SetLayeredWindowAttributes refused the base layer")
        off = ctypes.c_int(1)
        try:
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(self.hwnd), wintypes.DWORD(_DWMWA_TRANSITIONS_FORCEDISABLED),
                ctypes.byref(off), ctypes.sizeof(off))
        except Exception:
            pass
        self.top.bind("<ButtonPress>", self._on_click)
        self.top.bind("<Destroy>", self._on_destroyed)
        self.sync()
        self._job = self.top.after(_BACKPLATE_POLL_MS, self._poll)

    def alive(self) -> bool:
        return self.top is not None

    def _glass_hwnd(self) -> int:
        # Resolved every time rather than kept: Tk gives a window its frame
        # only when it is first mapped, and glass can go on before that.
        return _user32().GetAncestor(self.window.winfo_id(), _GA_ROOT) or 0

    def sync(self) -> None:
        if self.top is None:
            return
        u = _user32()
        try:
            glass = self._glass_hwnd()
        except Exception:        # the window is gone
            self.destroy()
            return
        if not glass or not u.IsWindowVisible(glass) or u.IsIconic(glass):
            if u.IsWindowVisible(self.hwnd):
                u.ShowWindow(self.hwnd, _SW_HIDE)
            self._rect = None
            return
        r = _RECT()
        u.GetWindowRect(glass, ctypes.byref(r))
        rect = (r.left, r.top, r.right - r.left, r.bottom - r.top)
        if rect != self._rect or u.GetWindow(glass, _GW_HWNDNEXT) != self.hwnd:
            # hWndInsertAfter=glass: directly beneath it.
            u.SetWindowPos(self.hwnd, glass, *rect, _SWP_NOACTIVATE | _SWP_SHOWWINDOW)
            self._rect = rect

    def _poll(self) -> None:
        self._job = None
        try:
            self.sync()
        except Exception:
            logger.debug("Glass base layer: sync failed", exc_info=True)
        if self.top is not None:
            self._job = self.top.after(_BACKPLATE_POLL_MS, self._poll)

    def _on_click(self, _event=None) -> None:
        try:
            self.window.lift()
            self.window.focus_force()
        except Exception:
            logger.debug("Glass base layer: could not raise its window", exc_info=True)

    def _on_destroyed(self, event) -> None:
        if event.widget is self.top:
            self._cancel()
            self.top = None

    def _cancel(self) -> None:
        if self._job is not None and self.top is not None:
            try:
                self.top.after_cancel(self._job)
            except Exception:
                pass
        self._job = None

    def destroy(self) -> None:
        self._cancel()
        top, self.top = self.top, None
        if top is not None:
            try:
                top.destroy()
            except Exception:
                pass


def _attach_backplate(window: Any) -> None:
    """Give a window that just went onto glass its base layer.

    Idempotent: every theme pass re-keys the window, and that must not stack
    layers.
    """
    global _backplate_failed
    if not sys.platform.startswith("win") or getattr(window, "_sc_is_backplate", False):
        return
    plate = getattr(window, "_sc_backplate", None)
    if plate is not None and plate.alive():
        plate.sync()
        return
    try:
        window._sc_backplate = _Backplate(window)
    except Exception:
        _backplate_failed = True
        logger.debug("Glass base layer unavailable; clicks on the glass fall through",
                     exc_info=True)


def _detach_backplate(window: Any) -> None:
    plate = getattr(window, "_sc_backplate", None)
    if plate is not None:
        plate.destroy()
        window._sc_backplate = None
