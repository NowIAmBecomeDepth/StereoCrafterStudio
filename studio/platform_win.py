"""platform_win.py -- Windows 11 system backdrop (Mica / Acrylic).

Why this works in v2 when v1 needed a chroma key
------------------------------------------------
``core/ui/win_backdrop.py`` explains the Tk constraint: Tk paints through GDI
with alpha 0, so extending the DWM frame into the client area made the whole
window vanish, and the blur could only be shown by punching colour-keyed holes
-- which are click-through.

Qt Quick renders every pixel with real alpha (the window has an alpha buffer),
so the route v1 had to reject is the right one here:

* ``DwmExtendFrameIntoClientArea(-1)`` hands the whole client area to DWM;
* the accent policy's acrylic (v1's, which stays frosted when the window is
  inactive) -- or ``DWMWA_SYSTEMBACKDROP_TYPE`` for Mica -- goes under it;
* QML paints a translucent tint of the palette on top.

This is a plain framed window, not a layered one, so Windows hit-tests its
whole rectangle: no click-through, and no backplate window. Verified on the
real window with WindowFromPoint over bare glass (2026-09-25).

Needs Windows 11 22H2 (build 22621); elsewhere ``supported`` is False.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

from PySide6.QtCore import (Property, QAbstractNativeEventFilter, QCoreApplication, QObject, QPointF,
                            Qt, Signal, Slot)
from PySide6.QtGui import QColor

logger = logging.getLogger("studio.platform_win")


class _MARGINS(ctypes.Structure):
    _fields_ = [("cxLeftWidth", ctypes.c_int), ("cxRightWidth", ctypes.c_int),
                ("cyTopHeight", ctypes.c_int), ("cyBottomHeight", ctypes.c_int)]


class _ACCENT_POLICY(ctypes.Structure):
    _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_int)]


class _WINCOMPATTRDATA(ctypes.Structure):
    _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.POINTER(_ACCENT_POLICY)),
                ("SizeOfData", ctypes.c_size_t)]


class Backdrop(QObject):
    changed = Signal()

    _DWMWA_USE_IMMERSIVE_DARK_MODE = 20
    _DWMWA_SYSTEMBACKDROP_TYPE = 38
    # Windows 11 (22000+). v1: core/ui/theme_manager.apply_windows_caption_colors.
    _DWMWA_BORDER_COLOR = 34
    _DWMWA_CAPTION_COLOR = 35
    _DWMWA_TEXT_COLOR = 36
    _DWMWA_COLOR_DEFAULT = 0xFFFFFFFF
    MODES = {"none": 1, "mica": 2, "acrylic": 3}  # DWMSBT_NONE / MAINWINDOW / TRANSIENTWINDOW
    _ACCENT_DISABLED, _ACCENT_ACRYLIC = 0, 4          # ACCENT_ENABLE_ACRYLICBLURBEHIND

    def __init__(self, parent=None):
        super().__init__(parent)
        self._window = None
        self._mode = "none"
        build = sys.getwindowsversion().build if sys.platform == "win32" else 0
        self._supported = build >= 22621
        self._caption_ok = build >= 22000

    @Slot(QObject)
    def bind(self, window) -> None:
        """The window this backdrop dresses (app.py: Main; ConsoleWindow.qml: the console)."""
        self._window = window
        self._clear_redirection()

    def _clear_redirection(self) -> None:
        """Keep the window's GDI redirection surface black.

        Under the Qt Quick content sits the surface Windows keeps for GDI
        painting. With per-pixel alpha honoured (Qt's blur-behind trick, or
        WindowFrame's), DWM composites it premultiplied: black is clear, but
        anything else ADDS its colour over the glass. Windows filled it once
        with the class brush (a system grey) at the window's size back then,
        so the glass read as flat grey -- except over any strip the window
        grew into afterwards, which had never been painted and showed clean
        glass (user, 2026-09-25, a narrower-than-window grey slab on the 1080p
        screen). So: a black class brush, black on every WM_ERASEBKGND, and
        one full repaint now to wipe what is already there."""
        if self._window is None or sys.platform != "win32":
            return
        hwnd_value = int(self._window.winId())
        if not hwnd_value:
            return
        user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
        gdi32.GetStockObject.restype = wintypes.HGDIOBJ
        black = gdi32.GetStockObject(4)                      # BLACK_BRUSH
        user32.SetClassLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        user32.SetClassLongPtrW(wintypes.HWND(hwnd_value), -10, black)   # GCLP_HBRBACKGROUND
        if getattr(self, "_erase_filter", None) is None:
            self._erase_filter = _EraseFilter(hwnd_value, black)
            QCoreApplication.instance().installNativeEventFilter(self._erase_filter)
        else:
            # the same window under a new HWND: QWindow.close() destroys the
            # native window and the next show() makes another (the console)
            self._erase_filter._hwnd = hwnd_value
        # RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN | RDW_UPDATENOW
        user32.RedrawWindow(wintypes.HWND(hwnd_value), None, None, 0x1 | 0x4 | 0x80 | 0x100)

    def _hwnd(self):
        if self._window is None or not self._supported:
            return None
        return wintypes.HWND(int(self._window.winId()))

    def _set_attr(self, hwnd, attr: int, value: int) -> int:
        v = ctypes.c_int(value)
        return ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))

    @Slot(str, result=bool)
    def apply(self, mode: str) -> bool:
        hwnd = self._hwnd()
        if hwnd is None or mode not in self.MODES:
            return False
        # Glass off keeps one pixel of frame: a caption-less window (WindowFrame)
        # with no frame in its client area at all loses the DWM shadow.
        margins = _MARGINS(-1, -1, -1, -1) if mode != "none" else _MARGINS(0, 0, 1, 0)
        # Acrylic is the accent policy (v1 core/ui/win_backdrop.apply), not
        # DWMSBT_TRANSIENTWINDOW: the system backdrop drops to a flat solid
        # colour whenever another app has the focus -- the glass looked "not
        # transparent at all" in every screenshot taken from another window
        # (user, 2026-09-26; reproduced with a second process holding focus).
        # The accent keeps its blur while inactive. Mica stays the system
        # backdrop (it is meant to go solid when inactive).
        accent = mode == "acrylic"
        system = self.MODES["none"] if accent else self.MODES[mode]
        ok = (ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins)) == 0
              and self._set_attr(hwnd, self._DWMWA_SYSTEMBACKDROP_TYPE, system) == 0
              and self._set_accent(hwnd, self._ACCENT_ACRYLIC if accent else self._ACCENT_DISABLED))
        self._mode = mode if ok else "none"
        self._clear_redirection()
        self.changed.emit()
        return ok

    def _set_accent(self, hwnd, state: int) -> bool:
        """SetWindowCompositionAttribute(WCA_ACCENT_POLICY): undocumented, but
        stable since Windows 10 1803 and what v1 has always used. QML paints
        the palette's tint over it (Theme.ground), which is what View > Glass
        Opacity sets."""
        user32 = ctypes.windll.user32
        if not hasattr(user32, "SetWindowCompositionAttribute"):
            return state == self._ACCENT_DISABLED
        # Flags 0: the material's own tint. Flags 2 (draw the gradient,
        # v1's) left this per-pixel-alpha window flat black, measured.
        policy = _ACCENT_POLICY(state, 0, 0, 0)          # AABBGGRR: black, alpha 1
        data = _WINCOMPATTRDATA(19, ctypes.pointer(policy), ctypes.sizeof(policy))   # WCA_ACCENT_POLICY
        user32.SetWindowCompositionAttribute.argtypes = [wintypes.HWND, ctypes.POINTER(_WINCOMPATTRDATA)]
        user32.SetWindowCompositionAttribute.restype = wintypes.BOOL
        return bool(user32.SetWindowCompositionAttribute(hwnd, ctypes.byref(data)))

    @Slot()
    def refresh(self) -> None:
        """Re-seat the acrylic at the window's current size.

        The accent's blurred snapshot of what is behind the window is sized
        when the accent is SET: grown past that (the stage fit after launch,
        a resize, maximise), everything beyond showed the last column of
        pixels stretched (user, 2026-09-26). Setting the same accent again is
        a no-op, so it goes off and straight back on -- both inside one
        frame, so nothing flickers. Main calls this once a resize settles."""
        if self._mode != "acrylic":
            return
        hwnd = self._hwnd()
        if hwnd is None:
            return
        self._set_accent(hwnd, self._ACCENT_DISABLED)
        self._set_accent(hwnd, self._ACCENT_ACRYLIC)
        self.refreshes = getattr(self, "refreshes", 0) + 1

    @Slot(bool)
    def setDark(self, dark: bool) -> None:
        """DWM draws the caption bar and the material light or dark, not us."""
        hwnd = self._hwnd()
        if hwnd is not None:
            self._set_attr(hwnd, self._DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if dark else 0)

    @Slot(QColor, QColor, QColor, bool, result=bool)
    def setCaptionColors(self, caption: QColor, text: QColor, border: QColor, system: bool) -> bool:
        """Paint the OS caption from the palette, as v1 does: without it
        Windows 11 tints the caption from the wallpaper behind the window.
        ``system`` hands caption and text back to Windows -- under glass the
        caption is part of the material, and a solid band would cut it off."""
        if self._window is None or not self._caption_ok:
            return False
        hwnd = wintypes.HWND(int(self._window.winId()))

        def ref(c: QColor) -> int:   # COLORREF is 0x00BBGGRR
            return (c.blue() << 16) | (c.green() << 8) | c.red()

        default = self._DWMWA_COLOR_DEFAULT
        ok = True
        for attr, value in ((self._DWMWA_CAPTION_COLOR, default if system else ref(caption)),
                            (self._DWMWA_TEXT_COLOR, default if system else ref(text)),
                            (self._DWMWA_BORDER_COLOR, ref(border))):
            v = wintypes.DWORD(value)
            ok &= ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v)) == 0
        return ok

    supported = Property(bool, lambda self: self._supported, constant=True)
    mode = Property(str, lambda self: self._mode, notify=changed)


# ---- custom title bar ---------------------------------------------------------

_WM_NCCALCSIZE = 0x0083
_WM_NCHITTEST = 0x0084
_WM_NCMOUSEMOVE = 0x00A0
_WM_NCLBUTTONDOWN = 0x00A1
_WM_NCLBUTTONUP = 0x00A2
_WM_NCLBUTTONDBLCLK = 0x00A3
_WM_NCRBUTTONUP = 0x00A5
_WM_SYSCOMMAND = 0x0112
_WM_MOUSEMOVE = 0x0200
_WM_NCMOUSELEAVE = 0x02A2
_HTCLIENT, _HTCAPTION, _HTMAXBUTTON = 1, 2, 9
_HTLEFT, _HTRIGHT, _HTTOP, _HTTOPLEFT, _HTTOPRIGHT = 10, 11, 12, 13, 14
_HTBOTTOM, _HTBOTTOMLEFT, _HTBOTTOMRIGHT = 15, 16, 17
_GWL_STYLE, _GWL_EXSTYLE = -16, -20
_WS_EX_LAYERED = 0x00080000
_TPM_RETURNCMD, _TPM_RIGHTBUTTON = 0x0100, 0x0002
_SC_SIZE, _SC_MOVE, _SC_MINIMIZE, _SC_MAXIMIZE, _SC_CLOSE, _SC_RESTORE = (
    0xF000, 0xF010, 0xF020, 0xF030, 0xF060, 0xF120)
_MF_ENABLED, _MF_GRAYED = 0x0000, 0x0001
_WS_CAPTION, _WS_SYSMENU, _WS_THICKFRAME = 0x00C00000, 0x00080000, 0x00040000
_WS_MINIMIZEBOX, _WS_MAXIMIZEBOX = 0x00020000, 0x00010000
_SWP_FRAMECHANGED = 0x0020 | 0x0001 | 0x0002 | 0x0004 | 0x0010  # + NOSIZE|NOMOVE|NOZORDER|NOACTIVATE
_SM_CXSIZEFRAME, _SM_CYSIZEFRAME, _SM_CXPADDEDBORDER = 32, 33, 92
_TME_LEAVE, _TME_NONCLIENT = 0x02, 0x10


def _gdi_signatures() -> None:
    """Declare the GDI/USER calls that take handles. Undeclared, ctypes passes
    a Python int as a 32-bit C int, and a handle above 2**31 raised
    OverflowError -- at random, by handle value. In WindowFrame.attach that
    skipped the frame refresh and the erase after it, leaving the native
    caption and the grey surface on some launches only."""
    if sys.platform != "win32":
        return
    gdi32, user32 = ctypes.windll.gdi32, ctypes.windll.user32
    gdi32.CreateRectRgn.restype = wintypes.HRGN
    gdi32.CreateRectRgn.argtypes = [ctypes.c_int] * 4
    gdi32.DeleteObject.restype = wintypes.BOOL
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.GetStockObject.restype = wintypes.HGDIOBJ
    gdi32.GetStockObject.argtypes = [ctypes.c_int]
    user32.FillRect.restype = ctypes.c_int
    user32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), wintypes.HBRUSH]
    user32.RedrawWindow.restype = wintypes.BOOL
    user32.RedrawWindow.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.HRGN, wintypes.UINT]


_gdi_signatures()


class _MSG(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT), ("wParam", wintypes.WPARAM),
                ("lParam", wintypes.LPARAM), ("time", wintypes.DWORD), ("pt", wintypes.POINT)]


class _NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]


class _DWM_BLURBEHIND(ctypes.Structure):
    _fields_ = [("dwFlags", wintypes.DWORD), ("fEnable", wintypes.BOOL),
                ("hRgnBlur", wintypes.HRGN), ("fTransitionOnMaximized", wintypes.BOOL)]


class _TRACKMOUSEEVENT(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("hwndTrack", wintypes.HWND), ("dwHoverTime", wintypes.DWORD)]


class _EraseFilter(QAbstractNativeEventFilter):
    """WM_ERASEBKGND for one window: fill black (clear under per-pixel
    alpha) rather than the class brush. See Backdrop._clear_redirection."""

    def __init__(self, hwnd_value: int, brush):
        super().__init__()
        self._hwnd = hwnd_value
        self._brush = brush

    def nativeEventFilter(self, eventType, message):
        try:
            msg = _MSG.from_address(int(message))
            if msg.message == 0x0014 and msg.hwnd == self._hwnd:    # WM_ERASEBKGND
                rect = wintypes.RECT()
                ctypes.windll.user32.GetClientRect(msg.hwnd, ctypes.byref(rect))
                ctypes.windll.user32.FillRect(wintypes.HDC(msg.wParam), ctypes.byref(rect), self._brush)
                return True, 1
        except Exception:
            pass
        return False, 0


class _Filter(QAbstractNativeEventFilter):
    def __init__(self, frame: "WindowFrame"):
        super().__init__()
        self._frame = frame

    def nativeEventFilter(self, eventType, message):
        try:
            return self._frame._on_message(_MSG.from_address(int(message)))
        except Exception:   # a hit-test bug must never take the window down
            return False, 0


class WindowFrame(QObject):
    """The window's caption drawn by QML, keeping everything Windows does for a
    real one -- the approach Windows Terminal takes.

    The window is Qt-frameless (so Qt's geometry has no frame in it) but gets
    WS_CAPTION | WS_THICKFRAME back, so DWM still gives it the shadow, rounded
    corners, snapping and Aero drag. WM_NCCALCSIZE makes the whole window
    client area, and WM_NCHITTEST hands the pieces back:

    Two styles are taken OFF, both found on the real window:

    * WS_EX_LAYERED -- Qt makes a frameless window with an alpha buffer
      layered, and a layered window never shows the system backdrop: glass
      went solid. Taking it off is not enough on its own: for a NON-layered
      alpha window Qt also calls DwmEnableBlurBehindWindow with an empty
      region (qwindowswindow.cpp applyBlurBehindWindow), which is what makes
      DWM honour the window's per-pixel alpha -- and it skipped that for the
      layered window it created. Without it the surface stays opaque over the
      backdrop (glass read as flat grey), so it is done here.
    * WS_SYSMENU -- with the frame extended over the whole window (glass),
      DWM draws its OWN caption buttons over the client area, beside
      TitleBar's. Without the style it draws none; the system menu is opened
      here instead, on a right-click over the bar.

    * the edges resize (inside the window: there is no outer frame left);
    * the QML title bar, outside its buttons, is HTCAPTION -- Windows drags,
      double-click maximises, right-click opens the system menu;
    * the maximise button is HTMAXBUTTON, which is what makes Windows 11 show
      its snap layouts on hover. Being non-client, it never sees Qt's mouse
      events, so its hover/press/click are reported here (maxHovered,
      maxPressed, maximizeClicked) for QML to draw and act on.

    QML registers its items with :meth:`attach`. Maximised, Windows sizes the
    window past the monitor by the frame thickness; the client is inset by
    that much so nothing is drawn off-screen.
    """

    changed = Signal()
    maximizeClicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._window = None
        self._title = None
        self._max = None
        self._no_drag = []
        self._hover = False
        self._pressed = False
        self._filter = None
        self._on = sys.platform == "win32"
        self._resize_border = 6
        self._hwnd_value = 0

    # -- QML ---------------------------------------------------------------
    @Slot(QObject, QObject, QObject, "QVariantList")
    def attach(self, window, title_bar, max_button, no_drag) -> None:
        """Take over the caption of ``window``. ``title_bar`` is the drag area,
        ``max_button`` the maximise button, ``no_drag`` the other controls in
        the bar (they stay ordinary client area)."""
        try:
            self._attach(window, title_bar, max_button, no_drag)
        except Exception:
            # Intermittent on some launches; the log is how the next one gets found.
            logger.exception("Custom title bar: attach failed")
            raise

    def _attach(self, window, title_bar, max_button, no_drag) -> None:
        self._title, self._max = title_bar, max_button
        self._no_drag = [i for i in (no_drag or []) if i is not None]
        if not self._on:
            return
        # The handle is taken ONCE, here, before the filter exists. winId()
        # creates the native window if there is none yet, and Windows sends
        # messages synchronously from inside CreateWindowEx: a filter that
        # called winId() per message re-entered creation, which failed
        # ("The parameter is incorrect") and was retried thousands of times.
        hwnd_value = int(window.winId())
        if not hwnd_value:
            return
        self._window, self._hwnd_value = window, hwnd_value
        # Frameless only now, on an existing window: Qt keeps the HWND and
        # zeroes its frame margins, but the window was CREATED plain -- the
        # kind whose transparency (and so glass) works. Qt makes it layered
        # again here; that is undone below.
        window.setFlags(window.flags() | Qt.WindowType.FramelessWindowHint)
        if self._filter is None:
            self._filter = _Filter(self)
            QCoreApplication.instance().installNativeEventFilter(self._filter)
        hwnd = self._hwnd()
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(hwnd, _GWL_STYLE)
        style = (style | _WS_CAPTION | _WS_THICKFRAME | _WS_MINIMIZEBOX | _WS_MAXIMIZEBOX) & ~_WS_SYSMENU
        user32.SetWindowLongW(hwnd, _GWL_STYLE, style)
        ex = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, ex & ~_WS_EX_LAYERED)
        # Blur-behind ON is what makes Windows honour the window's per-pixel
        # alpha once it is frameless. Switched off (2026-10-01, to match a
        # test window that looked clearer without it) Studio lost its glass
        # entirely: the tint showed over black. Keep it.
        gdi32 = ctypes.windll.gdi32
        region = gdi32.CreateRectRgn(0, 0, -1, -1)
        bb = _DWM_BLURBEHIND(0x1 | 0x2, True, region, False)   # DWM_BB_ENABLE | DWM_BB_BLURREGION
        self.blur_behind_hr = ctypes.windll.dwmapi.DwmEnableBlurBehindWindow(hwnd, ctypes.byref(bb))
        gdi32.DeleteObject(region)
        user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, _SWP_FRAMECHANGED)
        # Un-layering gives the window a fresh GDI surface; erase it (black,
        # via Backdrop's class brush / erase filter) before it shows grey.
        user32.RedrawWindow(hwnd, None, None, 0x1 | 0x4 | 0x80 | 0x100)

    def _system_menu(self, hwnd, x: int, y: int) -> None:
        """The window menu a caption's right-click opens (Restore, Move, Size,
        Minimise, Maximise, Close), items enabled for the window's state."""
        user32 = ctypes.windll.user32
        user32.GetSystemMenu.restype = wintypes.HMENU
        menu = user32.GetSystemMenu(hwnd, False)
        if not menu:
            return
        zoomed = bool(user32.IsZoomed(hwnd))
        for cmd, on in ((_SC_RESTORE, zoomed), (_SC_MOVE, not zoomed), (_SC_SIZE, not zoomed),
                        (_SC_MINIMIZE, True), (_SC_MAXIMIZE, not zoomed), (_SC_CLOSE, True)):
            user32.EnableMenuItem(menu, cmd, _MF_ENABLED if on else _MF_GRAYED)
        cmd = user32.TrackPopupMenu(menu, _TPM_RETURNCMD | _TPM_RIGHTBUTTON, x, y, 0, hwnd, None)
        if cmd:
            user32.PostMessageW(hwnd, _WM_SYSCOMMAND, cmd, 0)

    active = Property(bool, lambda self: self._on, constant=True)
    maxHovered = Property(bool, lambda self: self._hover, notify=changed)
    maxPressed = Property(bool, lambda self: self._pressed, notify=changed)

    # -- native ------------------------------------------------------------
    def _hwnd(self):
        return wintypes.HWND(self._hwnd_value)

    def _set(self, hover=None, pressed=None) -> None:
        h = self._hover if hover is None else hover
        p = self._pressed if pressed is None else pressed
        if (h, p) != (self._hover, self._pressed):
            self._hover, self._pressed = h, p
            self.changed.emit()

    @staticmethod
    def _inside(item, x: float, y: float) -> bool:
        if item is None or not item.isVisible():
            return False
        # Both corners through the scene: the page zoom (Main.qml) scales
        # the whole UI, so an item's own width is not its width on screen.
        tl = item.mapToScene(QPointF(0, 0))
        br = item.mapToScene(QPointF(item.width(), item.height()))
        return tl.x() <= x < br.x() and tl.y() <= y < br.y()

    def hit_test(self, x: float, y: float, zoomed: bool) -> int:
        """What Windows should treat logical client point (x, y) as."""
        w, h = self._window.width(), self._window.height()
        if not zoomed:
            b = self._resize_border
            left, right, top, bottom = x < b, x >= w - b, y < b, y >= h - b
            if top and left:
                return _HTTOPLEFT
            if top and right:
                return _HTTOPRIGHT
            if bottom and left:
                return _HTBOTTOMLEFT
            if bottom and right:
                return _HTBOTTOMRIGHT
            if left:
                return _HTLEFT
            if right:
                return _HTRIGHT
            if top:
                return _HTTOP
            if bottom:
                return _HTBOTTOM
        if self._inside(self._max, x, y):
            return _HTMAXBUTTON
        if any(self._inside(i, x, y) for i in self._no_drag):
            return _HTCLIENT
        if self._inside(self._title, x, y):
            return _HTCAPTION
        return _HTCLIENT

    def _on_message(self, msg):
        if self._window is None or not msg.hwnd or msg.hwnd != self._hwnd_value:
            return False, 0
        user32 = ctypes.windll.user32
        m = msg.message
        if m == _WM_NCCALCSIZE:
            if msg.wParam and user32.IsZoomed(msg.hwnd):
                # Maximised: Windows oversizes the window by the frame; keep
                # the client on the monitor.
                dpi = user32.GetDpiForWindow(msg.hwnd)
                pad = user32.GetSystemMetricsForDpi(_SM_CXPADDEDBORDER, dpi)
                fx = user32.GetSystemMetricsForDpi(_SM_CXSIZEFRAME, dpi) + pad
                fy = user32.GetSystemMetricsForDpi(_SM_CYSIZEFRAME, dpi) + pad
                params = _NCCALCSIZE_PARAMS.from_address(msg.lParam)
                r = params.rgrc[0]
                r.left += fx
                r.right -= fx
                r.top += fy
                r.bottom -= fy
                params.rgrc[0] = r
            return True, 0
        if m == _WM_NCHITTEST:
            pt = wintypes.POINT(ctypes.c_short(msg.lParam & 0xFFFF).value,
                                ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value)
            user32.ScreenToClient(msg.hwnd, ctypes.byref(pt))
            dpr = self._window.devicePixelRatio() or 1.0
            return True, self.hit_test(pt.x / dpr, pt.y / dpr, bool(user32.IsZoomed(msg.hwnd)))
        if m == _WM_NCMOUSEMOVE:
            on_max = msg.wParam == _HTMAXBUTTON
            self._set(hover=on_max, pressed=self._pressed and on_max)
            if on_max:
                tme = _TRACKMOUSEEVENT(ctypes.sizeof(_TRACKMOUSEEVENT), _TME_LEAVE | _TME_NONCLIENT,
                                       msg.hwnd, 0)
                user32.TrackMouseEvent(ctypes.byref(tme))
            return False, 0
        if m in (_WM_NCMOUSELEAVE, _WM_MOUSEMOVE):
            self._set(hover=False, pressed=False)
            return False, 0
        if m in (_WM_NCLBUTTONDOWN, _WM_NCLBUTTONDBLCLK) and msg.wParam == _HTMAXBUTTON:
            self._set(hover=True, pressed=True)
            return True, 0          # not DefWindowProc: it would draw a classic button
        if m == _WM_NCRBUTTONUP and msg.wParam == _HTCAPTION:
            self._system_menu(msg.hwnd, ctypes.c_short(msg.lParam & 0xFFFF).value,
                              ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value)
            return True, 0
        if m == _WM_NCLBUTTONUP and msg.wParam == _HTMAXBUTTON:
            was = self._pressed
            self._set(pressed=False)
            if was:
                self.maximizeClicked.emit()
            return True, 0
        return False, 0
