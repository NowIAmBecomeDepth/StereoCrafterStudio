"""Theme management for StereoCrafter GUI applications.

Provides dark/light theme support with color palettes and styling utilities
for tkinter-based applications.
"""

import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Palette registry
# ---------------------------------------------------------------------------
# Each palette defines the same color roles so any GUI reads them uniformly.
# "theme" is the underlying ttk base theme ("black"/"equilux"-style dark bases
# use "black"; light uses "clam"). "is_dark" drives the legacy dark-mode flag
# and accent-dependent choices in the GUIs (e.g. selected-text color).

THEMES: Dict[str, Dict[str, Any]] = {
    # --- Original two, preserved exactly -----------------------------------
    "Graphite": {
        "is_dark": True, "theme": "black",
        "bg": "#2b2b2b", "fg": "white", "entry_bg": "#3c3c3c",
        "menu_bg": "#3c3c3c", "menu_fg": "white",
        "active_bg": "#555555", "active_fg": "white",
        "tooltip_bg": "#4a4a4a", "tooltip_fg": "white",
        "panel_bg": "#3a4047", "panel_trough": "#262b31", "panel_bar": "#ffffff",
        "slider_trough": "#4a4a4a", "slider_handle": "#8a8a8a",
    },
    "Light": {
        "is_dark": False, "theme": "clam",
        "bg": "#d9d9d9", "fg": "black", "entry_bg": "#ffffff",
        "menu_bg": "#f0f0f0", "menu_fg": "black",
        "active_bg": "#dddddd", "active_fg": "black",
        "tooltip_bg": "#ffffe0", "tooltip_fg": "black",
        "panel_bg": "#e6ebf2", "panel_trough": "#bcc6d3", "panel_bar": "#4f83cc",
        "slider_trough": "#b8b8b8", "slider_handle": "#6a6a6a",
        # The console keeps its black pane under this palette (see
        # ConsoleWindow._log_pane_colors), and the blue accent that reads well
        # on the light UI is muddy against it. Plain white instead.
        "console_bar": "#ffffff",
    },
    # --- New palettes -------------------------------------------------------
    "True Black": {  # OLED black, amber accent
        "is_dark": True, "theme": "black",
        "bg": "#0a0a0a", "fg": "#eaeaea", "entry_bg": "#161616",
        "menu_bg": "#111111", "menu_fg": "#eaeaea",
        "active_bg": "#262626", "active_fg": "#ffffff",
        "tooltip_bg": "#1c1c1c", "tooltip_fg": "#eaeaea",
        # Dark red from the "Dark Red and black" palette, replacing the amber.
        # It measures 1.64:1 against this palette's #0a0a0a, which is fine for
        # the solid shapes it fills -- progress bars and the selected preset
        # button -- and far too dim for text.
        "panel_bg": "#141414", "panel_trough": "#050505", "panel_bar": "#661b1c",
        # So the selected stage tab's LABEL gets a lighter red from the same
        # hue family: 5.18:1 on the tab background, past the 4.5:1 text floor,
        # while still reading as red rather than salmon.
        "tab_accent": "#e05252",
        # Outline only: the accent stays on the tab text and progress bar, but
        # a mid grey close to the entry outline reads better for the section
        # rectangles against pure black.
        "section_border": "#4a4a4a",
        "slider_trough": "#333333", "slider_handle": "#8f8f8f",
        # 8-bit / 10-bit preset buttons: no accent outline. Against pure black
        # the accent rectangles read as a second, louder frame around the pair.
        "preset_toolbutton_outline": False,
        # CustomTkinter stages: this palette's whole point is black, so the
        # section cards are black too and the grey outline is what tells them
        # apart. Dropdowns stay grey; the red is left to the stage tab and the
        # one primary action.
        "ctk_panel": "#0a0a0a",
        "ctk_control": "#4a4a4a",
        # Grey sliders. The red accent is right for Start and for a ticked
        # box, but a column of eight red sliders reads as a warning rather
        # than as settings. Same value as this palette's ttk slider_handle,
        # so the converted stages match the ones still on ttk.
        "ctk_slider": "#8f8f8f",
    },
    "Ink": {  # GitHub-dark navy-black, cool blue accent
        "is_dark": True, "theme": "black",
        "bg": "#0d1117", "fg": "#c9d1d9", "entry_bg": "#161b22",
        "menu_bg": "#161b22", "menu_fg": "#c9d1d9",
        "active_bg": "#21262d", "active_fg": "#f0f6fc",
        "tooltip_bg": "#1c2128", "tooltip_fg": "#c9d1d9",
        "panel_bg": "#161b22", "panel_trough": "#010409", "panel_bar": "#58a6ff",
        "slider_trough": "#21262d", "slider_handle": "#7d8590",
    },
    "Nord": {  # desaturated slate-blue, muted frost accent
        "is_dark": True, "theme": "black",
        "bg": "#2e3440", "fg": "#e5e9f0", "entry_bg": "#3b4252",
        "menu_bg": "#3b4252", "menu_fg": "#e5e9f0",
        "active_bg": "#434c5e", "active_fg": "#eceff4",
        "tooltip_bg": "#434c5e", "tooltip_fg": "#e5e9f0",
        "panel_bg": "#3b4252", "panel_trough": "#272c36", "panel_bar": "#88c0d0",
        "slider_trough": "#434c5e", "slider_handle": "#8891a4",
    },
    "Warm Carbon": {  # near-black warm tint, amber accent
        "is_dark": True, "theme": "black",
        "bg": "#1a1715", "fg": "#e8e0d8", "entry_bg": "#26211d",
        "menu_bg": "#26211d", "menu_fg": "#e8e0d8",
        "active_bg": "#3a322c", "active_fg": "#fff4e8",
        "tooltip_bg": "#2c2621", "tooltip_fg": "#e8e0d8",
        "panel_bg": "#231e1a", "panel_trough": "#120f0d", "panel_bar": "#b8763a",
        "slider_trough": "#3a322c", "slider_handle": "#9a8d7e",
    },
    "Plum": {  # deep aubergine, magenta-pink accent
        "is_dark": True, "theme": "black",
        "bg": "#1e1522", "fg": "#ece4f0", "entry_bg": "#2a1f30",
        "menu_bg": "#2a1f30", "menu_fg": "#ece4f0",
        "active_bg": "#3d2d45", "active_fg": "#f8f0fc",
        "tooltip_bg": "#302438", "tooltip_fg": "#ece4f0",
        "panel_bg": "#281d2e", "panel_trough": "#140e17", "panel_bar": "#c678dd",
        "slider_trough": "#3d2d45", "slider_handle": "#9b7fa6",
    },
    "Teal Slate": {  # dark blue-green, greyed turquoise accent
        "is_dark": True, "theme": "black",
        "bg": "#12201f", "fg": "#dceae8", "entry_bg": "#1b2e2c",
        "menu_bg": "#1b2e2c", "menu_fg": "#dceae8",
        "active_bg": "#2a4340", "active_fg": "#effffd",
        "tooltip_bg": "#213734", "tooltip_fg": "#dceae8",
        "panel_bg": "#193029", "panel_trough": "#0a1413", "panel_bar": "#7aaea6",
        "slider_trough": "#2a4340", "slider_handle": "#6fa39c",
    },
    "Paper": {  # warm off-white light theme, soft orange accent
        "is_dark": False, "theme": "clam",
        "bg": "#ebe7df", "fg": "#2b2b2b", "entry_bg": "#ffffff",
        "menu_bg": "#f2efe9", "menu_fg": "#2b2b2b",
        "active_bg": "#dcd7cc", "active_fg": "#1a1a1a",
        "tooltip_bg": "#fffdf5", "tooltip_fg": "#2b2b2b",
        "panel_bg": "#f2efe9", "panel_trough": "#cfc9bc", "panel_bar": "#e08a4e",
        "slider_trough": "#c9c2b4", "slider_handle": "#7a7266",
        # Light palettes normally get a black console pane, which fights this
        # one's whole point. Paper's own paper-white with its near-black text
        # instead; the orange accent still marks the progress bars.
        "console_bg": "#ebe7df", "console_fg": "#2b2b2b",
    },
    # --- Dual palettes ------------------------------------------------------
    # Each of these is the other half of a pair: one hue read on both a dark
    # and a light ground, so switching changes the room's light rather than the
    # app's identity. Four give an existing dark palette the light twin it
    # never had; Moss/Fern is a new family, both halves.
    #
    # "tab_accent" appears wherever the accent is a good progress bar but too
    # dim to be the selected tab's LABEL, which is text and owes 4.5:1 against
    # "bg". Where it is absent the bar colour already clears that on its own.
    #
    # "entry_bg" is a tint of the palette, never plain #ffffff. A field is one
    # step off "bg" in the palette's own hue -- the way the dark palettes do it
    # -- so it reads as an inset surface. Pure white on a tinted ground reads as
    # a hole cut through to another theme.
    "Frost": {  # light twin of Nord: snow storm ground, frost blue accent
        "is_dark": False, "theme": "clam",
        "bg": "#e5e9f0", "fg": "#2e3440", "entry_bg": "#f3f6fb",
        "menu_bg": "#eceff4", "menu_fg": "#2e3440",
        "active_bg": "#d8dee9", "active_fg": "#2e3440",
        "tooltip_bg": "#f7f9fc", "tooltip_fg": "#2e3440",
        "panel_bg": "#eceff4", "panel_trough": "#c7d0de", "panel_bar": "#5e81ac",
        "tab_accent": "#44628c",
        "slider_trough": "#c7d0de", "slider_handle": "#6b7a91",
    },
    "Orchid": {  # light twin of Plum: lilac ground, deepened grape accent
        "is_dark": False, "theme": "clam",
        "bg": "#f0eaf2", "fg": "#2b1f30", "entry_bg": "#fbf7fd",
        "menu_bg": "#f6f1f8", "menu_fg": "#2b1f30",
        "active_bg": "#e0d5e6", "active_fg": "#1a1020",
        "tooltip_bg": "#fdfaff", "tooltip_fg": "#2b1f30",
        "panel_bg": "#f6f1f8", "panel_trough": "#d3c4db", "panel_bar": "#8b4bab",
        "slider_trough": "#cfc0d7", "slider_handle": "#7a6683",
    },
    "Sea Glass": {  # light twin of Teal Slate: pale green-grey, pine accent
        "is_dark": False, "theme": "clam",
        "bg": "#e4ece9", "fg": "#17302c", "entry_bg": "#f5faf8",
        "menu_bg": "#eef3f1", "menu_fg": "#17302c",
        "active_bg": "#d2ddd9", "active_fg": "#0d211e",
        "tooltip_bg": "#f6faf9", "tooltip_fg": "#17302c",
        "panel_bg": "#eef3f1", "panel_trough": "#c2d1cc", "panel_bar": "#2f7d72",
        "tab_accent": "#266a60",
        "slider_trough": "#c2d1cc", "slider_handle": "#5f7d77",
    },
    "Bone": {  # light twin of True Black: bone white, the same dark red
        "is_dark": False, "theme": "clam",
        "bg": "#f4f2ef", "fg": "#1c1a19", "entry_bg": "#fdfcfa",
        "menu_bg": "#faf9f7", "menu_fg": "#1c1a19",
        "active_bg": "#e4e0da", "active_fg": "#0f0e0d",
        "tooltip_bg": "#fffefc", "tooltip_fg": "#1c1a19",
        "panel_bg": "#faf9f7", "panel_trough": "#d6d1c9", "panel_bar": "#a32f2f",
        "tab_accent": "#8f2727",
        "slider_trough": "#cfc9c0", "slider_handle": "#6f6a63",
    },
    "Moss": {  # dark forest floor, leaf green accent
        "is_dark": True, "theme": "black",
        "bg": "#161c15", "fg": "#dfe7db", "entry_bg": "#212a1f",
        "menu_bg": "#212a1f", "menu_fg": "#dfe7db",
        "active_bg": "#33402f", "active_fg": "#f0f7ec",
        "tooltip_bg": "#28321f", "tooltip_fg": "#dfe7db",
        "panel_bg": "#1d251b", "panel_trough": "#0c110b", "panel_bar": "#7cb26a",
        "slider_trough": "#33402f", "slider_handle": "#86967e",
    },
    "Fern": {  # light twin of Moss: pale sage ground, deeper green accent
        "is_dark": False, "theme": "clam",
        "bg": "#e9eee3", "fg": "#1f2a1b", "entry_bg": "#f8fbf3",
        "menu_bg": "#f2f5ee", "menu_fg": "#1f2a1b",
        "active_bg": "#d8e0cf", "active_fg": "#121a0f",
        "tooltip_bg": "#fbfdf8", "tooltip_fg": "#1f2a1b",
        "panel_bg": "#f2f5ee", "panel_trough": "#c6d1ba", "panel_bar": "#4a7c3f",
        "tab_accent": "#3f6b35",
        "slider_trough": "#c6d1ba", "slider_handle": "#6b7a62",
    },
}

# Legacy default name for each mode (used when only the boolean is known).
_DEFAULT_DARK = "Graphite"
_DEFAULT_LIGHT = "Light"

# Backwards-compat aliases (old code referenced these dicts directly).
DARK_COLORS = dict(THEMES[_DEFAULT_DARK], theme="black")
LIGHT_COLORS = dict(THEMES[_DEFAULT_LIGHT])


# ---------------------------------------------------------------------------
# Windows dark title bar
# ---------------------------------------------------------------------------
# Tk has no control over the OS-drawn title bar, so a dark app still gets a
# white caption on Windows. The DWM exposes an immersive-dark-mode attribute
# that fixes it. No-op on non-Windows and on Windows 10 builds older than 17763,
# where the attribute does not exist.

_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1 = 19
# Windows 11 (build 22000+) only. Without these the caption is left to the
# system backdrop, which on Windows 11 tints it with whatever is on the desktop
# behind the window -- a wallpaper with a red swirl in it gives a dark red
# title bar, strongest on the window you are NOT using. Naming a colour makes
# the caption solid and takes the wallpaper out of it.
_DWMWA_BORDER_COLOR = 34
_DWMWA_CAPTION_COLOR = 35
_DWMWA_TEXT_COLOR = 36

_titlebar_warned = False


def _colorref(color: Any) -> Optional[int]:
    """A Tk colour as a Win32 COLORREF (0x00BBGGRR), or None if unparseable."""
    named = {"white": "#ffffff", "black": "#000000"}
    value = named.get(str(color).strip().lower(), color)
    try:
        h = str(value).lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if len(h) != 6:
            return None
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        return (b << 16) | (g << 8) | r
    except Exception:
        return None


def apply_windows_caption_colors(window: Any, colors: Dict[str, Any]) -> bool:
    """Paint the OS caption of `window` from the palette. Windows 11 only.

    The caption takes the menu bar's colour so the two read as one band, and
    the border follows. Older Windows rejects the attributes and keeps the
    caption it draws today; every platform else is a no-op.
    """
    import sys

    if not sys.platform.startswith("win") or not colors:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id()) or window.winfo_id()
        if not hwnd:
            return False

        caption = _colorref(colors.get("menu_bg") or colors.get("bg"))
        text = _colorref(colors.get("menu_fg") or colors.get("fg"))
        border = _colorref(colors.get("section_border") or colors.get("menu_bg") or colors.get("bg"))

        set_attr = ctypes.windll.dwmapi.DwmSetWindowAttribute
        applied = False
        for attr, value in (
            (_DWMWA_CAPTION_COLOR, caption),
            (_DWMWA_TEXT_COLOR, text),
            (_DWMWA_BORDER_COLOR, border),
        ):
            if value is None:
                continue
            ref = wintypes.DWORD(value)
            if set_attr(wintypes.HWND(hwnd), wintypes.DWORD(attr), ctypes.byref(ref), ctypes.sizeof(ref)) == 0:
                applied = True
        return applied
    except Exception:
        logger.debug("Windows caption colouring unavailable", exc_info=True)
        return False


def apply_windows_titlebar_theme(window: Any, dark: bool) -> bool:
    """Set the OS title bar of `window` to dark or light on Windows.

    Returns True if the attribute was applied, False otherwise (non-Windows,
    unsupported build, or the window not yet having a native handle).

    Safe to call repeatedly and from any platform; failures are swallowed and
    logged once, since a wrong-coloured title bar must never break a GUI.
    """
    global _titlebar_warned

    import sys
    if not sys.platform.startswith("win"):
        return False

    try:
        import ctypes
        from ctypes import wintypes

        # The window must be mapped before it has an HWND to talk about.
        window.update_idletasks()

        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        if not hwnd:
            hwnd = window.winfo_id()
        if not hwnd:
            return False

        value = ctypes.c_int(1 if dark else 0)
        set_attr = ctypes.windll.dwmapi.DwmSetWindowAttribute

        # Build 18985+ uses attribute 20; 17763-18984 used 19. Try the modern
        # one first and fall back, since querying the build adds no certainty
        # (the call simply fails harmlessly on the wrong one).
        for attr in (_DWMWA_USE_IMMERSIVE_DARK_MODE,
                     _DWMWA_USE_IMMERSIVE_DARK_MODE_BEFORE_20H1):
            res = set_attr(wintypes.HWND(hwnd), wintypes.DWORD(attr),
                           ctypes.byref(value), ctypes.sizeof(value))
            if res == 0:
                # Nudge the frame so the change is visible immediately rather
                # than on the next user-triggered redraw.
                #
                # SetWindowPos with SWP_FRAMECHANGED and no move, size, zorder
                # or activation redraws the NON-CLIENT area only, which is
                # exactly what changed. This used to be withdraw() followed by
                # deiconify(), and deiconify() restores a maximised window:
                # picking a theme dropped a full-screen Studio back into a
                # window and left it there, on every palette change. It also
                # flashed the whole window to do it.
                try:
                    _SWP_NOSIZE = 0x0001
                    _SWP_NOMOVE = 0x0002
                    _SWP_NOZORDER = 0x0004
                    _SWP_NOACTIVATE = 0x0010
                    _SWP_FRAMECHANGED = 0x0020
                    ctypes.windll.user32.SetWindowPos(
                        wintypes.HWND(hwnd), None, 0, 0, 0, 0,
                        _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER
                        | _SWP_NOACTIVATE | _SWP_FRAMECHANGED,
                    )
                except Exception:
                    logger.debug("Could not redraw the title bar frame",
                                 exc_info=True)
                return True

        return False

    except Exception as e:
        if not _titlebar_warned:
            logger.debug(f"Windows title bar theming unavailable: {e}")
            _titlebar_warned = True
        return False


def get_available_palette_names() -> List[str]:
    """Ordered list of selectable palette names for menus."""
    return list(THEMES.keys())


def get_palette_names_by_mode() -> Dict[str, List[str]]:
    """Palette names split into ``{"dark": [...], "light": [...]}``.

    Registry order is preserved inside each group. The registry itself is not
    grouped -- Light sits second, between two dark palettes -- because its
    order is historical and the legacy accessors depend on the first two
    entries.
    """
    grouped: Dict[str, List[str]] = {"dark": [], "light": []}
    for name, palette in THEMES.items():
        grouped["dark" if palette.get("is_dark") else "light"].append(name)
    return grouped


#: Menu rows and the Font object are stashed on the menu widget under these.
_HEADING_ROWS_ATTR = "_sc_theme_heading_rows"
_HEADING_FONT_ATTR = "_sc_theme_heading_font"
#: Headings sit slightly right of the palette names below them.
_HEADING_INDENT = "   "


def _heading_font(menu: Any) -> Any:
    """A copy of the menu font, one step smaller, for the group headings."""
    from tkinter import font as tkfont

    try:
        font = tkfont.Font(font=tkfont.nametofont("TkMenuFont"))
        size = int(font.cget("size") or 9)
        # Tk sizes are points when positive and pixels when negative, so
        # "smaller" moves toward zero from whichever side it starts on.
        font.configure(size=(size + 1) if size < 0 else max(6, size - 1))
        return font
    except Exception:
        return None


def _restyle_theme_headings(menu: Any, colors: Dict[str, Any]) -> None:
    """Recolour a theme menu's group headings for the palette now in force.

    The headings are ordinary entries, so without this they would take the
    menu's full-strength text colour and highlight under the pointer like a
    real command. Dimming them and giving them a hover state identical to
    their resting state makes them read as labels.
    """
    rows = getattr(menu, _HEADING_ROWS_ATTR, None)
    if not rows:
        return
    menu_bg = colors.get("menu_bg", colors.get("bg", "#000000"))
    menu_fg = colors.get("menu_fg", colors.get("fg", "#ffffff"))
    dim = ThemeManager._blend(menu_fg, menu_bg, 0.4)
    for row in rows:
        try:
            menu.entryconfig(
                row,
                foreground=dim,
                background=menu_bg,
                activeforeground=dim,
                activebackground=menu_bg,
            )
        except Exception:
            pass


def populate_theme_menu(menu: Any, variable: Any, on_select: Any) -> None:
    """Fill a ``tk.Menu`` with every palette, grouped under Dark and Light.

    One flat list of fifteen names gave no hint which half of the day each one
    was for, and the two groups were interleaved. This lays them out under
    disabled heading rows instead, so picking a palette starts with picking a
    mode.

    ``on_select`` is called with the chosen palette's name. Callers that drive
    everything off ``variable`` can ignore the argument.
    """
    grouped = get_palette_names_by_mode()
    font = _heading_font(menu)
    rows: List[int] = []
    first = True
    # "Dark"/"Light" alone would put a heading called Light directly above the
    # palette called Light.
    for mode, heading in (("dark", "Dark themes"), ("light", "Light themes")):
        names = grouped.get(mode) or []
        if not names:
            continue
        if not first:
            menu.add_separator()
        first = False
        # An ordinary entry with no command, not a disabled one: Windows draws
        # a disabled menu entry embossed -- grey text with a pale shadow behind
        # it -- and no colour option turns that off. This stays enabled and is
        # made inert by its colours instead (see _restyle_theme_headings): it
        # never highlights, and clicking it does nothing.
        menu.add_command(label=_HEADING_INDENT + heading, font=font)
        rows.append(menu.index("end"))
        for name in names:
            menu.add_radiobutton(
                label=name,
                value=name,
                variable=variable,
                command=lambda n=name: on_select(n),
            )

    # apply_theme_to_menus needs to find these again on every theme change; the
    # Font has to outlive this call or Tk loses the size.
    setattr(menu, _HEADING_ROWS_ATTR, rows)
    setattr(menu, _HEADING_FONT_ATTR, font)
    _restyle_theme_headings(menu, get_palette(str(variable.get() or _DEFAULT_DARK)))


def get_palette(name: str) -> Dict[str, Any]:
    """Return a palette by name, falling back to Graphite."""
    return THEMES.get(name, THEMES[_DEFAULT_DARK])


def get_theme_colors(is_dark: bool) -> Dict[str, str]:
    """Legacy accessor: colors by dark/light boolean (returns default of mode)."""
    return get_palette(_DEFAULT_DARK if is_dark else _DEFAULT_LIGHT)


def is_dark_mode(config: Dict[str, Any]) -> bool:
    """Check if dark mode is enabled in configuration.

    Args:
        config: Configuration dictionary

    Returns:
        True if dark mode is enabled
    """
    return bool(config.get("dark_mode_enabled", False))


class ThemeManager:
    """Manages application theming for dark/light mode switching.

    Provides utilities for applying themes to tkinter widgets and
    maintaining consistent styling across the application.

    Args:
        dark_mode_var: Optional tkinter BooleanVar tracking theme state
        config: Optional configuration dictionary
    """

    def __init__(
        self,
        dark_mode_var: Optional["tk.BooleanVar"] = None,
        config: Optional[Dict[str, Any]] = None,
        theme_name_var: Optional["tk.StringVar"] = None,
    ):
        """Initialize the theme manager.

        Args:
            dark_mode_var: Legacy BooleanVar tracking dark mode state
            config: Configuration dictionary
            theme_name_var: Optional StringVar naming an entry in THEMES. When
                set to a known palette, it takes priority over dark_mode_var.
        """
        self.dark_mode_var = dark_mode_var
        self.theme_name_var = theme_name_var
        self.config = config or {}

    def current_palette_name(self) -> str:
        """Resolve the active palette name from the name var, config, or the
        legacy boolean (defaulting to the mode's default palette)."""
        if self.theme_name_var is not None:
            name = self.theme_name_var.get()
            if name in THEMES:
                return name
        cfg_name = self.config.get("theme_name")
        if cfg_name in THEMES:
            return cfg_name
        return _DEFAULT_DARK if self.is_dark_mode() else _DEFAULT_LIGHT

    def is_dark_mode(self) -> bool:
        """Check if the active theme is dark.

        Prefers the named palette's is_dark flag when a name var is set, so the
        legacy boolean and accent-dependent GUI code stay correct across all
        palettes.

        Returns:
            True if the active theme is dark
        """
        if self.theme_name_var is not None:
            name = self.theme_name_var.get()
            if name in THEMES:
                return bool(THEMES[name].get("is_dark", True))
        if self.dark_mode_var:
            return bool(self.dark_mode_var.get())
        return is_dark_mode(self.config)

    #: Draw the 1px rectangle around each section. The unified shell flips this
    #: from View > Show Section Outlines and re-applies the theme; standalone
    #: stages leave it at the default.
    show_section_outlines: bool = True

    def get_colors(self) -> Dict[str, str]:
        """Get the current theme colors.

        Returns:
            Dictionary containing current color palette
        """
        return get_palette(self.current_palette_name())

    @staticmethod
    def _blend(hex_a: str, hex_b: str, amount: float) -> str:
        """Mix two hex colours. amount=0 returns a, amount=1 returns b.

        Unlike _tint, which pushes a colour toward white or black, this mixes
        toward another palette colour -- used for the disabled foreground,
        which has to fade toward the surface it sits on rather than toward a
        fixed grey that would be invisible on half the palettes.
        """
        # The two oldest palettes spell their foreground "white" / "black"
        # rather than in hex, and those are exactly the values this is asked to
        # fade, so a hex-only parse would fall through to the except and return
        # full-strength text on Dark and Light.
        _named = {"white": "#ffffff", "black": "#000000"}
        hex_a = _named.get(str(hex_a).strip().lower(), hex_a)
        hex_b = _named.get(str(hex_b).strip().lower(), hex_b)
        try:
            ha, hb = hex_a.lstrip("#"), hex_b.lstrip("#")
            ra, ga, ba = (int(ha[i:i + 2], 16) for i in (0, 2, 4))
            rb, gb, bb = (int(hb[i:i + 2], 16) for i in (0, 2, 4))
            t = max(0.0, min(1.0, amount))
            r, g, b = (int(round(ca + (cb - ca) * t)) for ca, cb in ((ra, rb), (ga, gb), (ba, bb)))
            return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            return hex_a

    @staticmethod
    def _tint(hex_color: str, amount: float) -> str:
        """Lighten (amount > 0) or darken (amount < 0) a hex colour.

        Used to derive a button's bevel from its own fill colour (panel_bg)
        rather than introducing a separate fixed highlight/shadow colour per
        palette -- keeps the bevel visually related to the surface it sits on
        regardless of how light or dark that surface is.
        """
        try:
            h = hex_color.lstrip("#")
            r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
            if amount >= 0:
                r, g, b = (int(c + (255 - c) * amount) for c in (r, g, b))
            else:
                r, g, b = (int(c * (1 + amount)) for c in (r, g, b))
            return f"#{max(0, min(255, r)):02x}{max(0, min(255, g)):02x}{max(0, min(255, b)):02x}"
        except Exception:
            return hex_color

    def apply_theme_to_style(self, style: "ttk.Style", root_window: Optional["tk.Tk"] = None) -> None:
        """Apply the current theme to ttk styles.

        Args:
            style: ttk.Style object to configure
            root_window: Optional root window for bg configuration
        """
        colors = self.get_colors()

        # Apply theme to root window if provided
        if root_window:
            root_window.configure(bg=colors["bg"])
            # Use set_theme if it's a ThemedTk instance, otherwise theme_use
            if hasattr(root_window, "set_theme"):
                try:
                    root_window.set_theme(colors["theme"])
                except Exception as e:
                    logger.warning(f"Failed to set theme via set_theme: {e}")
                    style.theme_use(colors["theme"])
            else:
                try:
                    style.theme_use(colors["theme"])
                except Exception as e:
                    logger.warning(f"Failed to set theme via theme_use: {e}")

            # Crucial: let the theme switch take effect before configuring styles
            root_window.update_idletasks()

            # Match the OS title bar to the palette (Windows only, no-op elsewhere).
            apply_windows_titlebar_theme(root_window, self.is_dark_mode())
            apply_windows_caption_colors(root_window, colors)

        # Configure basic styles for the current theme
        # We apply to common prefixes to be thorough
        bg = colors["bg"]
        fg = colors["fg"]
        entry_bg = colors["entry_bg"]
        # Shared by the section-outline rectangles and the button styling
        # below: section_border lets a palette (True Black) outline its boxes
        # in a colour independent of its accent; everything else falls back to
        # the accent itself.
        accent = colors.get("section_border") or colors.get("panel_bar", fg)

        for style_name in ["TFrame", "TLabelframe", "TLabel", "TCheckbutton", "TRadiobutton"]:
            style.configure(style_name, background=bg, foreground=fg)

        # --- Buttons ---------------------------------------------------------
        # Previously untouched: TButton was left at whatever the base ttk theme
        # ("black" or "clam") ships, and "black" hardcodes a flat mid-grey
        # (#424242) with a near-black bevel regardless of what palette is
        # active. Light and Paper happened to look better only because clam's
        # own default button colours (#dcdad5, warm bevel) sit close to their
        # own light backgrounds -- not because anything was actually themed.
        #
        # Measured what a real clam button draws before copying it: a 1px
        # bordercolor ring, then a 1px bevel inside it (lighter on top-left,
        # darker on bottom-right), then the fill. That 2px structure -- not a
        # flat 1px line -- is what reads as "thicker" and gives the slightly
        # rounded look. Reproduced here with palette colours: fill from
        # panel_bg (the surface colour already used for raised UI elements),
        # bordercolor from the same accent the section outlines use, and the
        # bevel as a lightened/darkened tint of panel_bg rather than a second
        # fixed colour, so it stays close in tone to the button itself instead
        # of introducing a third arbitrary shade.
        panel_bg = colors.get("panel_bg", bg)
        btn_light = self._tint(panel_bg, 0.35)
        btn_dark = self._tint(panel_bg, -0.55)

        # Disabled text. This used to be entry_bg, which on most palettes sits
        # within a few percent of panel_bg -- so a disabled button drew its
        # caption in almost exactly its own fill colour and read as blank.
        # Plenty of buttons start disabled and stay that way until a clip is
        # loaded (all three in the Subtitle Fixer, for one), so "blank" was the
        # first thing you saw and there was no way to tell what they were for.
        #
        # Faded toward the surface rather than set to a fixed grey: the
        # palettes run from near-black to near-white, and any one grey would be
        # invisible on one end or indistinguishable from live text on the
        # other. 0.45 keeps it clearly dimmer than enabled text while staying
        # legible on every palette. A palette may override it outright.
        disabled_fg = colors.get("disabled_fg") or self._blend(fg, panel_bg, 0.45)
        # The border fades further, so a disabled button still reads as a
        # button but does not compete with the live ones around it.
        disabled_border = self._blend(fg, panel_bg, 0.75)
        style.configure(
            "TButton",
            background=panel_bg,
            foreground=fg,
            bordercolor=accent,
            lightcolor=btn_light,
            darkcolor=btn_dark,
            relief="raised",
            borderwidth=1,
        )
        # Button size: every palette uses clam's. Paper and Light looked
        # roomier only because their base theme, clam, ships a larger default
        # TButton padding and minimum width than "black" does. The values are
        # read from clam itself rather than copied in as numbers, so they stay
        # exactly what the light palettes already draw.
        self._apply_clam_button_metrics(style)
        # Menubutton is a separate widget class with its own style, so the
        # TButton settings above do not reach it -- which is why the resolution
        # preset dropdowns stayed unthemed while everything around them
        # changed. Same border/bevel treatment, plus an arrow colour, since
        # Menubutton draws an indicator TButton does not have.
        style.configure(
            "TMenubutton",
            background=panel_bg,
            foreground=fg,
            bordercolor=accent,
            lightcolor=btn_light,
            darkcolor=btn_dark,
            relief="raised",
            borderwidth=1,
            arrowcolor=fg,
        )
        style.map(
            "TMenubutton",
            background=[
                ("disabled", panel_bg),
                ("pressed", btn_dark),
                ("active", colors.get("active_bg", btn_light)),
            ],
            foreground=[("disabled", disabled_fg)],
            bordercolor=[("disabled", disabled_border)],
            arrowcolor=[("disabled", disabled_fg)],
        )

        # A map is not optional polish here: clam's own hover/press colours
        # are baked into the theme and stay active under a plain configure(),
        # so without this every button flashes the same fixed grey on hover
        # regardless of palette -- confirmed by testing before adding it.
        style.map(
            "TButton",
            background=[
                ("disabled", panel_bg),
                ("pressed", btn_dark),
                ("active", colors.get("active_bg", btn_light)),
            ],
            foreground=[("disabled", disabled_fg)],
            bordercolor=[("disabled", disabled_border)],
            relief=[("pressed", "sunken")],
        )

        # --- Section boxes -------------------------------------------------
        # Draw a visible rectangle around each LabelFrame in the palette's own
        # accent colour, so the sections read as grouped panels rather than
        # floating labels.
        #
        # Both ttk themes in use ("black" and "clam") share clam's
        # Labelframe.border element, which exposes bordercolor/lightcolor/
        # darkcolor. relief="solid" nominally draws every side in bordercolor,
        # but the light/dark colours are what get used for some edges depending
        # on relief, so all three are set to the same value -- that guarantees a
        # uniform rectangle instead of a two-tone bevel.
        # relief="groove" with the bevel colours set to the background, NOT
        # relief="solid". clam's solid border paints bordercolor AND an inner
        # bevel line from light/darkcolor, which measures 2px on screen no
        # matter how low borderwidth goes. groove draws the single bordercolor
        # line, and neutralising light/darkcolor removes the bevel -- measured
        # 1px on all four sides. 1px is the floor; ttk has no sub-pixel border.
        if getattr(self, "show_section_outlines", True):
            style.configure(
                "TLabelframe",
                background=bg,
                bordercolor=accent,
                lightcolor=bg,
                darkcolor=bg,
                relief="groove",
                borderwidth=1,
                # clam sets labeloutside=true with a 4px bottom margin, which
                # parks the title fully ABOVE the rectangle and leaves the line
                # unbroken -- that is why the light palettes (Light, Paper, both
                # clam-based) looked different from the dark ones, which use the
                # "black" theme and leave this unset. Forcing it false puts the
                # title back on the line with the break around it, so all nine
                # palettes match.
                labeloutside=False,
                labelmargins=(8, 0, 0, 0),
            )
        else:
            # Outlines switched off: same geometry, no visible line. The
            # headings still separate the sections on their own.
            style.configure(
                "TLabelframe",
                background=bg,
                borderwidth=0,
                relief="flat",
                labeloutside=False,
                labelmargins=(8, 0, 0, 0),
            )

        # Borderless variant for sections that need no box -- the input/output
        # path block at the top of every stage, where a rectangle just traces
        # the window edge instead of grouping anything. Stages opt in with
        # style="Plain.TLabelframe"; the heading still gets the bold treatment.
        style.configure(
            "Plain.TLabelframe",
            background=bg,
            borderwidth=0,
            relief="flat",
            labeloutside=False,
            labelmargins=(0, 0, 0, 0),
        )

        # Right-anchored title variant, for sub-sections whose labelanchor is
        # "ne" instead of the default "nw" (e.g. Special FX's inner boxes).
        #
        # This sets every option explicitly rather than only labelmargins and
        # relying on ttk's dot-name fallback to inherit the rest from
        # TLabelframe. style.lookup() reported labeloutside correctly
        # inherited as False, but the actual rendering still showed the title
        # parked outside the border with the line running unbroken beneath it
        # -- labelanchor/labeloutside/labelmargins are layout-affecting
        # options Tk's Labelframe resolves at a different point than paint
        # options like bordercolor, and evidently do not compose the same way
        # through style inheritance. Setting the full style explicitly removes
        # any dependence on that inheritance working, for either branch of
        # show_section_outlines.
        if getattr(self, "show_section_outlines", True):
            style.configure(
                "RightTitle.TLabelframe",
                background=bg,
                bordercolor=accent,
                lightcolor=bg,
                darkcolor=bg,
                relief="groove",
                borderwidth=1,
                labeloutside=False,
                labelmargins=(0, 0, 8, 0),
            )
        else:
            style.configure(
                "RightTitle.TLabelframe",
                background=bg,
                borderwidth=0,
                relief="flat",
                labeloutside=False,
                labelmargins=(0, 0, 8, 0),
            )

        # Section titles: same family as the rest of the UI, one point up and
        # bold, so headings read as headings without introducing a second
        # typeface. The family is read from the live default font rather than
        # hardcoded, so this follows the user's Windows font settings and DPI
        # scaling instead of fighting them.
        try:
            import tkinter.font as _tkfont

            _base = _tkfont.nametofont("TkDefaultFont")
            _family = _base.cget("family")
            _size = int(_base.cget("size"))
            # Tk font sizes are negative for pixels, positive for points; step
            # away from zero either way so both conventions get larger.
            _heading_size = _size + 1 if _size > 0 else _size - 1
            for _lbl in ("TLabelframe.Label", "Plain.TLabelframe.Label", "RightTitle.TLabelframe.Label"):
                style.configure(
                    _lbl,
                    background=bg,
                    foreground=fg,
                    font=(_family, _heading_size, "bold"),
                )
        except Exception:
            logger.debug("Could not derive heading font; using default", exc_info=True)
            for _lbl in ("TLabelframe.Label", "Plain.TLabelframe.Label", "RightTitle.TLabelframe.Label"):
                style.configure(_lbl, background=bg, foreground=fg)

        # Configure style maps for interactive widgets.
        #
        # The disabled entries use the same faded-toward-the-surface colour as
        # the buttons above, so a stage greyed out mid-render stays readable
        # instead of emptying out. These sit on bg rather than panel_bg, so
        # they fade toward bg.
        disabled_on_bg = colors.get("disabled_fg") or self._blend(fg, bg, 0.45)
        disabled_on_entry = colors.get("disabled_fg") or self._blend(fg, entry_bg, 0.45)
        style.map(
            "TCheckbutton",
            foreground=[("disabled", disabled_on_bg), ("active", fg), ("!disabled", fg)],
            background=[("active", bg), ("!disabled", bg)],
        )
        style.map(
            "TRadiobutton",
            foreground=[("disabled", disabled_on_bg), ("active", fg), ("!disabled", fg)],
            background=[("active", bg), ("!disabled", bg)],
        )
        style.map("TLabel", foreground=[("disabled", disabled_on_bg)])

        # Configure Entry and Combobox
        # Some themes require explicit state mappings to override native looks
        #
        # "disabled" needs its own field colour. Without one the lookup falls
        # through to the base ttk theme, and clam's default field is white --
        # so on every light palette a greyed-out box stayed a white rectangle
        # while its neighbours took the palette's tint. It fades toward bg, the
        # same direction the disabled text does.
        disabled_field = colors.get("disabled_entry_bg") or self._blend(entry_bg, bg, 0.55)
        style.map(
            "TEntry",
            fieldbackground=[("disabled", disabled_field), ("!disabled", entry_bg), ("focus", entry_bg)],
            foreground=[("disabled", disabled_on_entry), ("!disabled", fg), ("focus", fg)],
        )
        style.configure("TEntry", fieldbackground=entry_bg, insertcolor=fg)

        # Slider (TScale) styling — the default ttk trough/handle are near-black
        # and vanish on dark palettes, so drive them explicitly from the palette.
        slider_trough = colors.get("slider_trough", colors.get("panel_trough", entry_bg))
        slider_handle = colors.get("slider_handle", colors.get("panel_bar", fg))
        try:
            for _sname in ("Horizontal.TScale", "Vertical.TScale", "TScale"):
                style.configure(
                    _sname,
                    background=bg,          # area around the widget
                    troughcolor=slider_trough,
                    bordercolor=slider_trough,
                    lightcolor=slider_handle,
                    darkcolor=slider_handle,
                )
            # The handle ('slider' element) color; brighten slightly when active.
            _handle_bright = colors.get("panel_bar", slider_handle)
            style.map(
                "Horizontal.TScale",
                background=[("active", bg), ("!disabled", bg)],
                troughcolor=[("!disabled", slider_trough)],
            )
        except Exception as e:
            logger.debug(f"TScale styling skipped: {e}")

        # "disabled" is listed before "readonly": most of these comboboxes are
        # state="readonly" all the time, and a disabled one is in BOTH states,
        # so whichever comes first wins. Readonly-first would keep full-strength
        # text on a greyed-out dropdown.
        style.map(
            "TCombobox",
            fieldbackground=[
                ("disabled", disabled_field),
                ("readonly", entry_bg),
                ("!disabled", entry_bg),
                ("focus", entry_bg),
            ],
            foreground=[("disabled", disabled_on_entry), ("readonly", fg), ("!disabled", fg), ("focus", fg)],
            arrowcolor=[("disabled", disabled_on_entry)],
            selectbackground=[("readonly", entry_bg)],
            selectforeground=[("readonly", fg)],
        )
        style.configure("TCombobox", fieldbackground=entry_bg, insertcolor=fg)
        # Spinboxes had no field colour at all -- only their text and arrows
        # were themed -- so every spinbox in the app drew clam's white field.
        style.configure("TSpinbox", fieldbackground=entry_bg, foreground=fg, insertcolor=fg)
        style.map(
            "TSpinbox",
            fieldbackground=[("disabled", disabled_field), ("readonly", entry_bg), ("!disabled", entry_bg)],
            foreground=[("disabled", disabled_on_entry), ("!disabled", fg)],
            arrowcolor=[("disabled", disabled_on_entry)],
        )

        # Tooltips and the help panes are plain tk widgets in their own
        # Toplevels, with no style to read. They pick the palette up from here.
        try:
            from core.ui.widgets import set_active_palette

            set_active_palette(colors)
        except Exception:
            logger.debug("Could not publish palette to widgets module", exc_info=True)

        if root_window:
            self._apply_theme_to_combobox_popdowns(root_window, colors)
            # Force a final update to catch any delayed renders
            root_window.update_idletasks()

    def _apply_theme_to_combobox_popdowns(self, root_window: Any, colors: Dict[str, str]) -> None:
        """Colour the dropdown LIST that opens under a combobox.

        The list is not part of the combobox's ttk style: ttk builds a plain tk
        Listbox in a Toplevel of its own the first time a box is opened, and it
        reads the option database, not the theme. Nothing here ever set those
        options, so every dropdown opened as a white list -- the field above it
        themed, the list below it not.

        Both halves are needed. ``option_add`` covers lists not built yet; the
        walk fixes the ones that already exist, which after a theme switch is
        most of them.
        """
        from tkinter import ttk as _ttk

        entry_bg = colors["entry_bg"]
        fg = colors["fg"]
        sel_bg = colors.get("active_bg", entry_bg)
        sel_fg = colors.get("active_fg", fg)

        for option, value in (
            ("*TCombobox*Listbox.background", entry_bg),
            ("*TCombobox*Listbox.foreground", fg),
            ("*TCombobox*Listbox.selectBackground", sel_bg),
            ("*TCombobox*Listbox.selectForeground", sel_fg),
        ):
            try:
                root_window.option_add(option, value)
            except Exception:
                pass

        stack = [root_window]
        while stack:
            widget = stack.pop()
            try:
                stack.extend(widget.winfo_children())
            except Exception:
                pass
            if not isinstance(widget, _ttk.Combobox):
                continue
            try:
                # Builds the popdown if this box has never been opened, which
                # is what we want: it then exists, withdrawn, already themed.
                listbox = f"{widget.tk.call('ttk::combobox::PopdownWindow', widget)}.f.l"
                widget.tk.call(
                    listbox, "configure",
                    "-background", entry_bg,
                    "-foreground", fg,
                    "-selectbackground", sel_bg,
                    "-selectforeground", sel_fg,
                )
            except Exception:
                pass

    # Cached clam TButton metrics: {"padding": (...), "width": int}. Read once;
    # a ttk base theme's shipped defaults do not change at runtime.
    _clam_button_metrics: Optional[Dict[str, Any]] = None

    def _apply_clam_button_metrics(self, style: "ttk.Style") -> None:
        metrics = ThemeManager._clam_button_metrics
        if metrics is None:
            metrics = self._read_clam_button_metrics(style)
            if metrics is None:
                return  # clam unavailable: leave the base theme's own size
            ThemeManager._clam_button_metrics = metrics
        try:
            style.configure("TButton", **metrics)
        except Exception:
            logger.debug("Could not apply clam button metrics", exc_info=True)

    @staticmethod
    def _read_clam_button_metrics(style: "ttk.Style") -> Optional[Dict[str, Any]]:
        """Look up TButton padding and width as the clam theme defines them.

        ``ttk::style theme settings`` makes clam the current theme for the
        duration of its script, so a lookup inside it reads clam's values even
        while a dark palette (base theme "black") is active.
        """
        tk_app = style.tk
        try:
            # ttkthemes loads a theme's package on first use; a session that
            # started on a dark palette may not have loaded clam yet.
            if "clam" not in style.theme_names():
                tk_app.call("package", "require", "ttk::theme::clam")
            tk_app.eval(
                "ttk::style theme settings clam {"
                " set ::_sc_clam_btn_padding [ttk::style lookup TButton -padding];"
                " set ::_sc_clam_btn_width [ttk::style lookup TButton -width] }"
            )
            padding = str(tk_app.getvar("_sc_clam_btn_padding")).strip()
            width = str(tk_app.getvar("_sc_clam_btn_width")).strip()
        except Exception:
            logger.debug("clam button metrics unavailable", exc_info=True)
            return None
        metrics: Dict[str, Any] = {}
        if padding:
            try:
                metrics["padding"] = tuple(int(float(p)) for p in padding.split())
            except ValueError:
                pass
        if width:
            try:
                metrics["width"] = int(float(width))
            except ValueError:
                pass
        return metrics or None

    def apply_theme_to_menus(self, menus: list, menubar: Optional["tk.Menu"] = None) -> None:
        """Apply theme colors to menu widgets.

        Args:
            menus: List of tk.Menu widgets to configure
            menubar: Optional menubar widget
        """
        colors = self.get_colors()
        try:
            from core.ui.themed_menubar import menu_check_color, set_menu_check_color

            check = menu_check_color(self.is_dark_mode())
        except Exception:
            check = None

        all_menus = list(menus)
        if menubar:
            all_menus.append(menubar)

        for menu in all_menus:
            try:
                menu.config(
                    bg=colors["menu_bg"],
                    fg=colors["menu_fg"],
                    activebackground=colors["active_bg"],
                    activeforeground=colors["active_fg"],
                    # Greyed items -- and the Theme menu's Dark/Light headings,
                    # which are disabled entries -- otherwise draw in Tk's own
                    # grey, which on several palettes is close to the menu
                    # background. Fade toward it from the menu's own text.
                    disabledforeground=self._blend(
                        colors["menu_fg"], colors["menu_bg"], 0.45
                    ),
                )
            except Exception:
                pass
            # White check marks on dark menus, black on light (submenus too).
            if check is not None:
                set_menu_check_color(menu, check)
            # A theme menu's Dark/Light headings are ordinary entries with
            # hand-set colours, so the config above does not reach them.
            _restyle_theme_headings(menu, colors)

    def apply_theme_to_labels(self, labels: list, bg: Optional[str] = None, fg: Optional[str] = None) -> None:
        """Apply theme colors to label widgets.

        Args:
            labels: List of tk.Label widgets to configure
            bg: Optional background color override
            fg: Optional foreground color override
        """
        colors = self.get_colors()

        for label in labels:
            try:
                if bg:
                    label.config(bg=bg)
                else:
                    label.config(bg=colors["bg"])
                if fg:
                    label.config(fg=fg)
                else:
                    label.config(fg=colors["fg"])
            except Exception:
                pass

    def apply_theme_to_canvas(self, canvas: "tk.Canvas") -> None:
        """Apply theme background color to a canvas widget.

        Args:
            canvas: tk.Canvas widget to configure
        """
        colors = self.get_colors()
        try:
            canvas.config(bg=colors["bg"], highlightthickness=0)
        except Exception:
            pass

    def get_style_config(self) -> Dict[str, str]:
        """Get the current style configuration for persistence.

        Returns:
            Dictionary containing style-relevant configuration
        """
        colors = self.get_colors()
        return {
            "dark_mode_enabled": self.is_dark_mode(),
            "theme_bg": colors["bg"],
            "theme_fg": colors["fg"],
            "theme_entry_bg": colors["entry_bg"],
        }

    @staticmethod
    def get_available_themes() -> list:
        """Get list of available theme names.

        Returns:
            List of theme names
        """
        return ["default", "black", "clam", "alt", "classic"]

    def configure_compact_styles(self, style: "ttk.Style") -> None:
        """Configure compact button and entry styles for space-efficient UIs.

        Args:
            style: ttk.Style object to configure
        """
        colors = self.get_colors()
        panel_bg = colors.get("panel_bg", colors["bg"])

        try:
            style.configure("SmallTool.TButton", padding=(0, 0), anchor="center")
            style.configure("CompactAction.TButton", padding=(4, 0), anchor="center")
            style.configure("Loading.TButton", padding=(4, 0), anchor="center")
            style.configure("TEntry", padding=(1, 0))
            style.configure("TCombobox", padding=(1, 0))
            style.configure("TSpinbox", padding=(1, 0))
        except Exception:
            pass
        # Arrow and scrollbar sizes are pixels, so they follow the monitor's
        # DPI in Sharp mode (and are exactly what they were otherwise).
        from core.ui import dpi

        dpi.apply_ttk_sizes(getattr(style, "master", None))

    def configure_progressbar_style(
        self, style: "ttk.Style", trough_color: Optional[str] = None, bar_color: Optional[str] = None
    ) -> None:
        """Configure progress bar styles with custom colors.

        Args:
            style: ttk.Style object to configure
            trough_color: Optional color for the progress bar trough
            bar_color: Optional color for the progress bar fill
        """
        colors = self.get_colors()
        trough = trough_color or colors.get("panel_trough", colors["bg"])
        bar = bar_color or colors.get("panel_bar", "#5d96e0")

        try:
            style.configure(
                "Custom.Horizontal.TProgressbar",
                troughcolor=trough,
                background=bar,
                lightcolor=bar,
                darkcolor=bar,
                bordercolor=trough,
                thickness=12,
            )
            style.configure(
                "ProgressPanel.TLabel", background=colors.get("panel_bg", colors["bg"]), foreground=colors["fg"]
            )
        except Exception:
            pass

    def apply_theme_to_labelframes(self, labelframes: list) -> None:
        """Apply theme colors to a list of labelframe widgets.

        Args:
            labelframes: List of tk.LabelFrame widgets to configure
        """
        colors = self.get_colors()

        accent = colors.get("panel_bar", colors["fg"])

        for lf in labelframes:
            if lf is not None:
                try:
                    lf.configure(bg=colors["bg"], fg=colors["fg"])
                except Exception:
                    pass
                # Match the 1px accent rectangle the ttk LabelFrames get. A
                # tk.LabelFrame has no bordercolor, so the coloured line comes
                # from the highlight ring instead, with the real border zeroed
                # so the two don't stack into a thick double edge.
                #
                # Widgets tagged _plain_border opt out -- the input/output path
                # block at the top of a stage, where a box only traces the
                # window edge.
                try:
                    if getattr(lf, "_plain_border", False):
                        lf.configure(highlightthickness=0, bd=0, relief="flat")
                    else:
                        lf.configure(
                            highlightthickness=1,
                            highlightbackground=accent,
                            highlightcolor=accent,
                            bd=0,
                            relief="flat",
                        )
                except Exception:
                    pass

    def apply_theme_to_widgets_by_names(
        self, widget_dict: Dict[str, Any], bg: Optional[str] = None, fg: Optional[str] = None
    ) -> None:
        """Apply theme colors to widgets referenced by name dictionary.

        Args:
            widget_dict: Dictionary mapping widget names to widget objects
            bg: Optional background color override
            fg: Optional foreground color override
        """
        colors = self.get_colors()
        panel_bg = colors.get("panel_bg", colors["bg"])

        for w_name, w in widget_dict.items():
            if w is not None:
                try:
                    if fg and hasattr(w, "configure"):
                        w.configure(bg=bg or panel_bg, fg=fg)
                    else:
                        w.configure(bg=bg or panel_bg)
                except Exception:
                    try:
                        w.configure(bg=bg or panel_bg)
                    except Exception:
                        pass
