"""dpi.py -- Sharp mode: Studio rendered at each monitor's real resolution.

Without it Studio is DPI-UNAWARE ("classic" mode). Windows draws it at 96
DPI and stretches the finished bitmap to the monitor's scaling, which on a
4K screen at 150% makes every edge and letter slightly soft -- but it also
means Tk never sees a DPI change, so the layout only ever has one set of
pixel sizes to be right about.

Sharp mode, the default, makes the process per-monitor DPI aware, so
nothing is stretched.
The price is that Studio then has to follow a monitor change itself, which
Tk 8.6 does not do on its own:

  * CustomTkinter rescales its widgets (its ScalingTracker polls each
    window's monitor DPI), but Studio's window is not a CTk window, and the
    tracker calls two methods only CTk windows have. The shell supplies them
    -- see StereoCrafterShell.block_update_dimensions_event.
  * Tk's own ``tk scaling`` -- which sizes every point-sized font, so every
    ttk and plain Tk widget -- is read once from the PRIMARY monitor at
    startup and never changes. :func:`follow` updates it and re-applies the
    named fonts.
  * The window keeps its pixel size, which on a lower-DPI monitor is larger
    than the screen; it is resized by the ratio of the two DPIs.

On unless the user turns it off (View > Sharp Mode, which applies on the
next start). The environment variable ``STEREOCRAFTER_SHARP`` overrides the
setting either way, 1 or 0 -- for testing one mode without touching the
other's saved setting. It must be switched on before the first Tk window
exists.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import tkinter.font as tkfont
from typing import Any

logger = logging.getLogger(__name__)

ENV_VAR = "STEREOCRAFTER_SHARP"

_sharp = False

#: Tk's points-to-pixels factor at 100% (96 DPI / 72 points per inch).
_TK_SCALING_AT_100 = 96.0 / 72.0


def requested(default: bool = True) -> bool:
    """Whether to run sharp: the environment variable if it is set, else
    `default` -- the saved View > Sharp Mode setting."""
    raw = str(os.environ.get(ENV_VAR, "")).strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def is_sharp() -> bool:
    return _sharp


def enable() -> bool:
    """Make the process per-monitor DPI aware. True if it now is.

    Per-Monitor V2 where Windows has it (10 1703+): Windows then also scales
    the title bar, menus and common dialogs as the window crosses monitors.
    V1 otherwise, which is what CustomTkinter itself asks for.
    """
    global _sharp
    if not sys.platform.startswith("win"):
        return False
    try:
        user32 = ctypes.WinDLL("user32")
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 == (HANDLE)-4
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            _sharp = True
            _install_tolerant_tracker()
            return True
    except (AttributeError, OSError):
        pass
    try:
        if ctypes.WinDLL("shcore").SetProcessDpiAwareness(2) == 0:
            _sharp = True
            _install_tolerant_tracker()
            return True
    except (AttributeError, OSError):
        pass
    logger.warning("Sharp mode: could not make Studio DPI aware; running as usual")
    return False


def _install_tolerant_tracker() -> None:
    """Let CustomTkinter's DPI tracker handle windows that are not CTk windows.

    ScalingTracker.check_dpi_scaling calls block/unblock_update_dimensions_event
    on every window holding CTk widgets. Only CTk's own windows have them, and
    Studio's are plain Tk: the shell, the console, and every pop-up (Hole
    Shift, the subtitle editor, the preview window...). On the first of those
    to cross monitors the call raised -- after the tracker had dimmed the
    window to 15% opacity and before it restored it -- which left that window
    nearly invisible and stopped the tracker for the session.

    This is the same loop, with the two calls made only where they exist, the
    opacity restored whatever happens, and a plain window resized by the DPI
    ratio (Windows leaves that to the application). The shell defines both
    calls itself; see StereoCrafterShell.block_update_dimensions_event.
    """
    try:
        from customtkinter.windows.widgets.scaling.scaling_tracker import ScalingTracker
    except Exception:
        return
    if getattr(ScalingTracker, "_sc_tolerant", False):
        return

    def check_dpi_scaling(cls):
        new_scaling_detected = False
        # Not while a window is being dragged. A rescale mid-drag resized the
        # window under the pointer, several seconds a time, with the window
        # still straddling the two monitors -- see _resize_by for how that
        # became a loop. The change is simply noticed on the next poll after
        # the button is let go, and handled once, where the window landed.
        windows = [] if _pointer_held() else list(cls.window_widgets_dict)
        for window in windows:
            try:
                if not window.winfo_exists() or window.state() == "iconic":
                    continue
                current = cls.get_window_dpi_scaling(window)
                old = cls.window_dpi_scaling_dict.get(window, current)
                if current == old:
                    continue
                cls.window_dpi_scaling_dict[window] = current
                new_scaling_detected = True
                window.attributes("-alpha", 0.15)
                try:
                    block = getattr(window, "block_update_dimensions_event", None)
                    if block is not None:
                        block()
                    cls.update_scaling_callbacks_for_window(window)
                    unblock = getattr(window, "unblock_update_dimensions_event", None)
                    if unblock is not None:
                        unblock()
                    else:
                        _resize_by(window, current / old if old else 1.0)
                finally:
                    window.attributes("-alpha", 1)
            except Exception:
                logger.debug("Sharp mode: DPI rescale of a window failed", exc_info=True)
        for app in list(cls.window_widgets_dict):
            try:
                app.after(cls.loop_pause_after_new_scaling if new_scaling_detected
                          else cls.update_loop_interval, cls.check_dpi_scaling)
                return
            except Exception:
                continue
        cls.update_loop_running = False

    ScalingTracker.check_dpi_scaling = classmethod(check_dpi_scaling)
    ScalingTracker._sc_tolerant = True


def _pointer_held() -> bool:
    """Whether a mouse button is down right now -- a window drag in progress.

    GetAsyncKeyState reads the physical buttons, so both are checked rather
    than working out which one is primary on a swapped mouse.
    """
    if not sys.platform.startswith("win"):
        return False
    try:
        user32 = ctypes.WinDLL("user32")
        return any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in (0x01, 0x02))
    except Exception:
        return False


def _resize_by(window: Any, ratio: float, scale_minsize: bool = True) -> None:
    """Scale a window's size by `ratio` about its CENTRE, on its monitor.

    Kept centred, and then kept inside the work area of the monitor that
    centre is on. Scaled from the top-left corner instead, as it used to be,
    a window straddling two monitors rescaled itself in a loop: the DPI
    change is decided by which monitor holds the window's centre, shrinking
    for the smaller monitor pulled the centre back over the edge onto the
    larger one, which grew the window and pushed it back again -- every two
    seconds, a full rescale each time, until a click during one hung Studio.
    Unless it is maximised, which Windows sizes itself.
    """
    if abs(ratio - 1.0) < 0.01:
        return
    try:
        if str(window.state()) == "zoomed":
            return      # Windows sizes a maximised window to its new monitor
        # The window's own size is current without a flush; flushing first
        # laid the whole app out at the OLD size, only for the geometry call
        # below to lay it out again at the new one -- seconds, on a monitor
        # change. Only a window not yet sized (1x1) needs the flush.
        w, h = window.winfo_width(), window.winfo_height()
        if w <= 1 or h <= 1:
            window.update_idletasks()
            w, h = window.winfo_width(), window.winfo_height()
        # A pop-up's minimum was set for the monitor it opened on, so it moves
        # with the size. A window that works its minimum out itself -- the
        # shell, whose tab strip claims the width of its row -- says so: scaled
        # here it was never scaled back, and Studio could no longer be shrunk
        # to its real minimum after a move (300 px of height became 450).
        if scale_minsize:
            try:
                mw, mh = window.minsize()
                window.minsize(int(mw * ratio), int(mh * ratio))
            except Exception:
                pass
        new_w, new_h = max(1, int(w * ratio)), max(1, int(h * ratio))
        # The frame's position and the client's: the two differ by the title
        # bar and borders, which a geometry position is given in terms of.
        x, y = window.winfo_x(), window.winfo_y()
        cx, cy = window.winfo_rootx() + w // 2, window.winfo_rooty() + h // 2
        x, y = x + (w - new_w) // 2, y + (h - new_h) // 2
        from core.ui import win_backdrop

        area = win_backdrop.work_area_at(cx, cy)
        if area:
            left, top, right, bottom = area
            chrome_w = window.winfo_rootx() - window.winfo_x()
            chrome_h = window.winfo_rooty() - window.winfo_y()
            new_w = min(new_w, max(1, right - left - 2 * chrome_w))
            new_h = min(new_h, max(1, bottom - top - chrome_h - chrome_w))
            x = max(left, min(x, right - new_w - 2 * chrome_w))
            y = max(top, min(y, bottom - new_h - chrome_h - chrome_w))
        window.geometry(f"{new_w}x{new_h}+{x}+{y}")
    except Exception:
        logger.debug("Sharp mode: could not resize a window", exc_info=True)


def factor_for(widget: Any = None) -> float:
    """The DPI factor of the monitor `widget`'s window is on. 1.0 unless Sharp.

    For sizes Studio sets in raw pixels on plain Tk/ttk widgets -- which,
    unlike CTk's, nothing scales. In normal mode Windows stretches the whole
    window instead, so this is always 1.0 there and those sizes stay exactly
    as they were written.
    """
    if not _sharp:
        return 1.0
    try:
        import tkinter

        from customtkinter.windows.widgets.scaling.scaling_tracker import ScalingTracker

        if widget is None:
            widget = tkinter._default_root
        top = widget.winfo_toplevel() if widget is not None else None
        value = ScalingTracker.window_dpi_scaling_dict.get(top) if top is not None else None
        return float(value or (monitor_dpi_factor(top) if top is not None else 1.0)) or 1.0
    except Exception:
        return 1.0


def px(widget: Any, value: float, factor: float = None) -> int:
    """`value` logical pixels in real ones on `widget`'s monitor."""
    return max(1, int(round(value * (factor or factor_for(widget)))))


#: ttk element sizes Studio sets in pixels, scaled per monitor in Sharp mode.
#: (style, option) -> logical pixels. 14 is ttk's own scrollbar default.
_TTK_SIZES = {
    ("TScrollbar", "arrowsize"): 14,
    ("TCombobox", "arrowsize"): 10,
    ("TSpinbox", "arrowsize"): 10,
}


def apply_ttk_sizes(widget: Any, factor: float = None) -> None:
    """Configure the pixel-sized ttk elements for `widget`'s monitor.

    Run from every theme pass -- theme_use resets the style database -- and
    on every monitor change. The scrollbars in particular: ttk draws them
    14 px thick whatever the DPI, a hairline on a 4K screen.
    """
    try:
        from tkinter import ttk

        style = ttk.Style(widget)
        for (name, option), value in _TTK_SIZES.items():
            style.configure(name, **{option: px(widget, value, factor)})
    except Exception:
        logger.debug("Could not size the ttk elements", exc_info=True)


def monitor_dpi_factor(window: Any) -> float:
    """The DPI of the monitor `window` is on, as a factor of 96 (1.5 = 150%)."""
    try:
        from customtkinter.windows.widgets.scaling.scaling_tracker import ScalingTracker

        return float(ScalingTracker.get_window_dpi_scaling(window)) or 1.0
    except Exception:
        return 1.0


def set_tk_scaling(window: Any, factor: float) -> None:
    """Tk's points-to-pixels factor for a monitor at `factor` (1.5 = 150%).

    Called BEFORE CustomTkinter rescales on a DPI change, not after: the
    widgets' fonts are re-made during that pass, and Tk records a font's
    point size with the scaling in force when it is made. Set late, every
    font re-made in the pass was recorded at the previous monitor's scale.
    """
    if not _sharp or not factor:
        return
    try:
        window.tk.call("tk", "scaling", factor * _TK_SCALING_AT_100)
    except Exception:
        logger.debug("Sharp mode: could not set Tk scaling", exc_info=True)


def retarget_menus(window: Any) -> None:
    """Point every tk.Menu under `window` at the NAMED font TkMenuFont.

    On Windows a tk.Menu's default font is not TkMenuFont but a literal
    description of the system menu font, "{Segoe UI} 9". Tk turns a literal
    description into pixels once, at the `tk scaling` in force when it is
    first used, and caches that -- so a menu built on the 4K screen (9 pt at
    150% = 18 px) kept its 18 px text on the 1080p one, where it opened huge.
    A named font is re-measured when it is re-applied (follow does that), so
    a menu on TkMenuFont follows the monitor. CustomTkinter's own dropdown
    lists carry fonts of their own and are left alone; so is any menu already
    on a named font.
    """
    try:
        import tkinter as tk

        named = set(tkfont.names(window))
    except Exception:
        return
    stack = [window]
    while stack:
        widget = stack.pop()
        try:
            stack.extend(widget.winfo_children())
        except Exception:
            continue
        if not isinstance(widget, tk.Menu) or type(widget).__name__ == "DropdownMenu":
            continue
        try:
            if str(widget.cget("font")) not in named:
                widget.configure(font="TkMenuFont")
        except Exception:
            continue


def follow(window: Any, old: float, new: float, resize: bool = True,
           scale_minsize: bool = True) -> None:
    """Bring Tk's own scaling, the fonts and the window size to a new DPI.

    `old` and `new` are DPI factors (1.0 = 100%). CustomTkinter's widgets are
    handled by CustomTkinter; this is everything else. `scale_minsize` False
    leaves the window's minimum alone, for a window that sets its own.
    """
    if not _sharp or not new or abs(new - old) < 0.01:
        return
    try:
        window.tk.call("tk", "scaling", new * _TK_SCALING_AT_100)
        # Point sizes are turned into pixels when a font is configured, so a
        # new `tk scaling` reaches a widget only once its font is configured
        # again. Every named font -- TkDefaultFont and friends, which ttk and
        # plain Tk widgets use -- is re-applied at its own size. Negative
        # (pixel) sizes are CustomTkinter's, and it rescales those itself.
        for name in tkfont.names(window):
            try:
                font = tkfont.nametofont(name, root=window)
                size = int(font.cget("size"))
                if size > 0:
                    font.configure(size=size)
            except Exception:
                continue
    except Exception:
        logger.debug("Sharp mode: could not update Tk scaling", exc_info=True)
    apply_ttk_sizes(window, new)
    retarget_menus(window)
    if not resize:
        return
    _resize_by(window, new / old, scale_minsize=scale_minsize)
