"""Shared plumbing for stages built from CustomTkinter instead of ttk.

A CTk widget ignores ttk.Style completely. The shell restyles every ttk widget
in the process, and a CTk-built stage sees none of it, so it has to repaint
itself: each widget is registered when it is built and recoloured by hand
whenever the palette changes. That machinery is identical from one converted
stage to the next, so it lives here rather than in each of them.

Three things this module knows that are easy to get wrong:

  * **bg_color is what rounded corners composite against**, and CTk detects it
    ONCE, from the master, at construction. A CTkFrame parent pushes its colour
    down to CTk children when it changes; a ttk parent has no such mechanism,
    so a CTk widget parented to a ttk frame keeps the palette it was born under
    unless something repaints it. Register everything, including containers
    that paint nothing.
  * **The accent is not guaranteed to be readable.** A palette may name a
    near-white accent -- Graphite's is literally ``#ffffff`` -- so what goes on
    top of an accent fill is chosen by contrast against it, never taken from
    the palette's foreground.
  * **There is no CTk LabelFrame.** :meth:`CtkThemedStage._section` builds the
    equivalent: a rounded frame with a header above a transparent body, so
    children keep the row and column they already used.
  * **There is no CTk spinbox either.** :class:`CtkSpin` is an entry between
    two step buttons, built by :meth:`CtkThemedStage._ctk_spin`. Its `width`
    is in PIXELS, where ttk.Spinbox counted characters.
  * **grid_remove() does not survive a scale change** unless the fix below
    is installed -- CTk replays the last grid() call on every widget when
    the scaling changes, and a hidden widget's record is still live. See
    :func:`_install_grid_remove_fix`.
  * **CTk's own default sizes are much bigger than the ttk ones they replace**
    -- an entry is 140x28 against roughly 75x21 -- so the helpers below
    default to the ttk sizes instead. A button's width is a floor it grows
    past for a long label; an entry's is the whole size; an option menu is
    measured against its widest value so it neither wastes room nor jumps
    when the selection changes.

A stage mixes in :class:`CtkThemedStage`, calls :meth:`_ctk_init` before
building, registers widgets through the ``_ctk_*`` helpers, and calls
:meth:`_apply_ctk_palette` from wherever it receives a palette.
"""

import logging
import tkinter as tk
import tkinter.font as tkfont
import weakref
from tkinter import ttk
from typing import Any, Dict, List, Optional, Tuple

import customtkinter as ctk

from core.ui import app_fonts, win_backdrop

logger = logging.getLogger(__name__)


def _install_grid_remove_fix() -> None:
    """Make CustomTkinter's ``grid_remove()`` behave like its ``grid_forget()``.

    CTk records the last ``.grid()``/``.pack()`` call made on every widget and
    REPLAYS it whenever the widget scaling changes, so that padx/pady follow
    the new scale -- ``CTkBaseClass._set_scaling``. It clears that record in
    ``grid_forget()`` and ``pack_forget()``, which it overrides. It never
    overrides ``grid_remove()``, which is therefore tkinter's own and leaves
    the record untouched.

    So a widget hidden with ``grid_remove()`` keeps a live record of the very
    ``grid()`` call that put it on screen, and the next scale change re-runs
    it: the widget comes back, with nothing in our code having asked for it.

    That is what made dragging Studio between the two displays reveal every
    conditional box at once -- DepthCrafter's Merged Output Options, the
    beginner-mode panels, the preview's optional rows -- dismissable only by
    toggling each one off and on again. Both halves of a monitor change
    trigger it: our own :func:`apply_ui_scale`, and CTk's DPI follow
    (``ScalingTracker.check_dpi_scaling``) when the two screens differ in
    scaling, which they do here.

    ``grid_remove()`` is used across the stages rather than ``grid_forget()``
    precisely because Tk remembers the cell. Tk's memory is untouched by this:
    only CTk's replay record is dropped, so a later bare ``.grid()`` still
    restores the widget to its original row, column and sticky. The one thing
    given up is that such a widget's padding keeps the pixel values from the
    scale it was last explicitly gridded at, until something grids it with
    arguments again -- a pixel or two, against a box that should not be on
    screen at all.

    Idempotent, and never fatal: a CTk release that fixes this itself, or that
    moves the class, leaves the app running unpatched rather than failing to
    import.
    """
    try:
        from customtkinter.windows.widgets.core_widget_classes.ctk_base_class import (
            CTkBaseClass,
        )
    except Exception:
        logger.debug("Could not import CTkBaseClass to patch grid_remove", exc_info=True)
        return
    if getattr(CTkBaseClass, "_sc_grid_remove_patched", False):
        return
    inherited = CTkBaseClass.grid_remove

    def grid_remove(self):
        # Exactly what CTk's own grid_forget() does, minus unmapping the cell.
        self._last_geometry_manager_call = None
        return inherited(self)

    grid_remove.__doc__ = CTkBaseClass.grid_remove.__doc__
    CTkBaseClass.grid_remove = grid_remove
    CTkBaseClass._sc_grid_remove_patched = True


_install_grid_remove_fix()


def _install_exact_dimensions() -> None:
    """Stop CTk drawing controls up to 2px short at a fractional UI scale.

    On every <Configure>, CTkBaseClass converts the widget's real pixel size
    back to logical units with ``int(pixels / scaling)`` -- TRUNCATED -- and
    the draw then applies ``int(logical * scaling)`` and floors that to an
    even number. At 0.85 (the 1080p screen) a 20px entry comes out as
    int(23.5) = 23, int(19.55) = 19, even 18: its outline is drawn 2px short
    of its own canvas. On a text box the typing field, which is laid out
    from the real 20px, then covers the outline's bottom edge -- the "bottom
    cut off" boxes seen after dragging Studio onto the 1080p screen. At 1.0
    the arithmetic is exact, which is why the 4K screen never showed it.

    The logical size is kept as an exact float instead, nudged a hair up so
    that multiplying it back lands on the pixel count rather than a
    rounding error below it. Idempotent and never fatal, like the other
    CTk patches here.
    """
    try:
        from customtkinter.windows.widgets.core_widget_classes.ctk_base_class import (
            CTkBaseClass,
        )
    except Exception:
        logger.debug("Could not import CTkBaseClass to patch dimensions", exc_info=True)
        return
    if getattr(CTkBaseClass, "_sc_exact_dimensions", False):
        return

    def _update_dimensions_event(self, event):
        try:
            scaling = float(self._get_widget_scaling()) or 1.0
        except Exception:
            scaling = 1.0
        width = event.width / scaling + 1e-6
        height = event.height / scaling + 1e-6
        # Redraw only when the drawn pixel size would actually change.
        if (round(float(self._current_width) * scaling) != event.width
                or round(float(self._current_height) * scaling) != event.height):
            self._current_width = width
            self._current_height = height
            self._draw(no_color_updates=True)

    CTkBaseClass._update_dimensions_event = _update_dimensions_event
    CTkBaseClass._sc_exact_dimensions = True


_install_exact_dimensions()


#: Widgets some stage's palette painter owns: every one it has painted. The
#: painter sets each of their colours explicitly on every theme pass, so
#: CTk's own recolouring of them during that pass is thrown away -- see
#: _install_fast_repaint. Weak, so a destroyed widget simply drops out.
_PAINTED = weakref.WeakSet()
#: Tk paths of the widgets the painter call in progress is about to paint,
#: or None outside a paint.
_PAINTING: Optional[set] = None
#: True while the painter itself is switching CTk's light/dark mode.
_PAINTER_SWITCHING_MODE = False


def _install_fast_repaint() -> None:
    """Stop a theme change redrawing every control two or three times.

    A light<->dark switch took ~2.8 s with every stage built, a dark->dark
    one ~2.0 s. The painter (CtkThemedStage._apply_ctk_palette) configures
    each registered widget once, which is the redraw that is needed. Two
    things in CustomTkinter redrew them all again around it:

      * **The appearance-mode switch.** Every CTk widget in the app redraws
        itself for the new mode -- and flushes the WHOLE app's pending
        layout and redraws after doing so, once per widget. For a widget the
        painter is about to recolour anyway the redraw is thrown away, so it
        only records the new mode; and nothing needs the flush, since Tk
        draws at idle anyway.
      * **CTkFrame's colour cascade.** Changing a frame's fg_color
        reconfigures every CTk child's bg_color, and changing a transparent
        frame's bg_color makes every child re-detect its own -- recursively,
        container by container. During a paint that is skipped for the
        children the painter is about to paint itself; the rest (a section's
        body frame, a segmented button's halves) still get it.

    Outside a paint both behave exactly as CustomTkinter wrote them.
    """
    try:
        from customtkinter.windows.widgets.core_widget_classes.ctk_base_class import (
            CTkBaseClass,
        )
    except Exception:
        logger.debug("Could not import CTkBaseClass for the repaint patch", exc_info=True)
        return
    if getattr(CTkBaseClass, "_sc_fast_repaint", False):
        return

    def _set_appearance_mode(self, mode_string):
        super(CTkBaseClass, self)._set_appearance_mode(mode_string)
        if _PAINTER_SWITCHING_MODE and self in _PAINTED:
            return          # the painter recolours it next; that is its redraw
        self._draw()

    frame_configure = ctk.CTkFrame.configure

    def _frame_configure(self, require_redraw=False, **kwargs):
        painting = _PAINTING
        if painting is None:
            return frame_configure(self, require_redraw=require_redraw, **kwargs)

        def cascade(color):
            for child in self.winfo_children():
                if isinstance(child, CTkBaseClass) and str(child) not in painting:
                    child.configure(bg_color=color)

        # CTkFrame.configure, with the cascade limited as above.
        if "corner_radius" in kwargs:
            self._corner_radius = kwargs.pop("corner_radius")
            require_redraw = True
        if "border_width" in kwargs:
            self._border_width = kwargs.pop("border_width")
            require_redraw = True
        if "fg_color" in kwargs:
            self._fg_color = self._check_color_type(kwargs.pop("fg_color"), transparency=True)
            require_redraw = True
            cascade(self._fg_color)
        if "bg_color" in kwargs and self._fg_color == "transparent":
            cascade(self._fg_color)
        if "border_color" in kwargs:
            self._border_color = self._check_color_type(kwargs.pop("border_color"))
            require_redraw = True
        if "background_corner_colors" in kwargs:
            self._background_corner_colors = kwargs.pop("background_corner_colors")
            require_redraw = True
        CTkBaseClass.configure(self, require_redraw=require_redraw, **kwargs)

    CTkBaseClass._set_appearance_mode = _set_appearance_mode
    ctk.CTkFrame.configure = _frame_configure
    CTkBaseClass._sc_fast_repaint = True


_install_fast_repaint()


def seat_corner_toggle(outer, box, gap: int = 14) -> None:
    """Place a section's show/hide switch in its top-right corner.

    Centred on the section's HEADING, not pinned a fixed few pixels from the
    card's top edge: 3px looked right at full size, but at the 1080p scale it
    left the box a pixel off the card's border and it read as cut off. On
    the heading's centre line it keeps clear of the border at every scale,
    lines up with the title, and still sits centred when the section is
    collapsed to its header. `gap` is the logical distance from the card's
    right edge.
    """
    header = next((w for w in outer.pack_slaves() if w is not box), None)
    try:
        info = header.pack_info() if header is not None else {}
    except tk.TclError:
        info = {}
    if header is None or info.get("fill") not in ("x", "both"):
        box.place(relx=1.0, y=3, x=-gap, anchor="ne")
        return
    # Room for the box inside the heading's own padding. Placed rather than
    # packed, the box takes no space, so a card collapsed to its heading
    # shrank to the TEXT's width and the box sat on top of the last letters.
    # A centred heading is padded on both sides, so it stays centred; the
    # placement below reads this padding, so the box still lands `gap` from
    # the card's edge.
    try:
        scale = float(box._get_widget_scaling()) or 1.0
        # The box's own logical size, not its requested width: this runs
        # while the page is being built, before the box has been laid out,
        # and an unlaid-out widget reports next to nothing.
        # The margin covers the spacing column a CTkCheckBox keeps beside its
        # box even with no text, plus a little air before the title.
        reserve = float(getattr(box, "_checkbox_width", 18)) + 20.0
        if not getattr(header, "_sc_corner_reserved", False):
            base = info.get("padx", 0)
            left, right_pad = ((base[0], base[-1]) if isinstance(base, (tuple, list)) else (base, base))
            left, right_pad = float(left) / scale, float(right_pad) / scale
            centred = str(header.cget("anchor")) == "center"
            repack(header, padx=(round(left + (reserve if centred else 0.0)), round(right_pad + reserve)))
            header._sc_corner_reserved = True
            info = header.pack_info()
    except Exception:
        logger.debug("Could not reserve heading room for a corner switch", exc_info=True)
    # The header is inset by its own padx, so the offset is measured from
    # its right edge back to the card's.
    padx = info.get("padx", 0)
    right = padx[-1] if isinstance(padx, (tuple, list)) else padx
    try:
        right = float(right) / float(box._get_widget_scaling())
    except Exception:
        right = 10
    box.place(in_=header, relx=1.0, rely=0.5, x=right - gap, anchor="e")


def _install_button_centring() -> None:
    """Centre every CTkButton's CAPITALS rather than its line box.

    CTk centres a button's label by the font's whole line box. Button labels
    here are all caps, and in Cal Sans the capitals sit well below that box's
    middle, so every label read as slightly dropped. A bottom pad of twice the
    offset on the label lifts it by exactly the offset -- see
    :func:`app_fonts.cap_lift`, which returns 0 for any face it does not know.

    Patched into CTkButton rather than applied per button, because CTk lays
    its label out afresh from many places -- _create_grid runs on every
    configure of text, image, compound or anchor, and on a DPI change -- and a
    one-off adjustment would be undone by the first relabel. _update_font is
    hooked as well: changing a button's font does NOT go back through
    _create_grid, and the tab strip changes its buttons' fonts on every repaint.
    """
    # A widget may also set `_sc_text_nudge`: extra logical pixels to move
    # its text DOWN, on top of the centring. The stage tabs use it -- CTk puts
    # the label a little high in a 28px tab, which the font alone does not
    # explain, so no metric can correct it. It is honoured by CTkLabel too,
    # but only for a label that sets it: a tab's step number is a CTkLabel
    # and has to move with its title, while every other label is left alone.
    def _centre(self, label, always: bool) -> None:
        if label is None:
            return
        nudge = getattr(self, "_sc_text_nudge", None)
        if not always and nudge is None:
            return
        try:
            lift = app_fonts.cap_lift(label.cget("font"))
            down = self._apply_widget_scaling(float(nudge or 0.0)) - lift
            pad = max(0, int(round(2 * abs(down))))
            if nudge is not None and down > 0:
                # An opted-in drop stops at the room the widget has: past
                # it, the padding makes the widget TALLER instead of moving
                # the text. For the stage tabs that meant the current tab
                # grew a pixel on a 100% screen, and the whole strip jumped
                # on every stage switch. The text simply drops a little less.
                height = self._apply_widget_scaling(self._current_height)
                if isinstance(self, ctk.CTkButton):
                    height -= 2 * self._apply_widget_scaling(
                        max(self._border_width + 1, self._border_spacing))
                room = int(height - label.winfo_reqheight())
                pad = min(pad, max(0, room))
            # Padding on one side only. The cell grows by `pad` and stays
            # centred, so the text moves by pad/2 -- exactly `down`. Padding
            # both sides to keep the cell the same size was tried: it made
            # 24px buttons grow to 29 and put their text off by a pixel.
            label.grid_configure(pady=(pad, 0) if down > 0 else (0, pad))
        except (tk.TclError, ValueError):
            pass            # not gridded yet; the next _create_grid will do it

    for cls, attr, always in ((ctk.CTkButton, "_text_label", True),
                              (ctk.CTkLabel, "_label", False)):
        if getattr(cls, "_sc_centring", False):
            continue
        original_grid = cls._create_grid
        original_font = cls._update_font

        def _create_grid(self, _orig=original_grid, _attr=attr, _always=always):
            _orig(self)
            _centre(self, getattr(self, _attr, None), _always)

        def _update_font(self, _orig=original_font, _attr=attr, _always=always):
            _orig(self)
            _centre(self, getattr(self, _attr, None), _always)

        # And after a rescale. CTkButton._set_scaling re-grids its label
        # (running the hook above) BEFORE giving it the new scale's font, and
        # sets that font directly rather than through _update_font -- so the
        # padding was always worked out for the PREVIOUS scale's font. A
        # button came back from a trip between monitors a couple of pixels
        # off the height it started at.
        original_scaling = cls._set_scaling

        def _set_scaling(self, *args, _orig=original_scaling, _attr=attr, _always=always, **kwargs):
            _orig(self, *args, **kwargs)
            _centre(self, getattr(self, _attr, None), _always)

        cls._create_grid = _create_grid
        cls._update_font = _update_font
        cls._set_scaling = _set_scaling
        cls._sc_centring = True


_install_button_centring()

# --- frosted glass ----------------------------------------------------------
#
# The same effect the console uses, on the same terms -- see
# unified_gui.console_window and core.ui.win_backdrop. Windows blurs what is
# BEHIND a window; the blur shows only where the window is transparent, and Tk
# paints opaque, so every surface meant to be glass is painted in a colour key
# that Windows punches out.
#
# Only the PAGE is keyed. The sections stay solid on every palette, and the
# glass is spent on the gutters around them.
#
# Dark palettes used to key the cards as well, so a stage read as one sheet of
# glass. It looked better and it cost too much: a punched-out pixel is
# CLICK-THROUGH, so keying the cards meant most of a dark window was a hole --
# the inside of every section, not just the gutters -- and a click landing on
# one went to whatever was behind the app. On a dark page you could pick up a
# desktop icon through a section box. The measurement and the alternatives are
# in win_backdrop.warn_click_through; the short version is that the
# click-through is inherent to the colour key and the only lever is how much
# of the window is keyed. (Since then win_backdrop puts an all-but-invisible
# base layer under every glass window, which catches those clicks -- so a
# keyed card would no longer leak them. The cards stay solid anyway; this is
# the look the palettes were tuned for.)
#
# A palette can ask for the old look back with "ctk_glass_panels": true,
# which buys a continuous sheet and pays for it in click-through.
#
# The palette still supplies every other colour: fills and hovers are derived
# from the real surface colour, never from the key, or a button would come out
# near-black.
#
# Text on a punched-out background keeps the fringe it was antialiased with:
# white on a dark tint carries it and the page reads cleanly, black on a light
# tint does not -- it comes out rough and doubled. So a LIGHT palette plates
# the widgets that draw text straight on the page. That is about the text, not
# about the cards, which is why it keys off is_dark and not off the panel
# decision above.
#
#: The build-time switch. The USER's switch is
#: :func:`core.ui.win_backdrop.is_enabled` -- View > Turn On Frosted Glass --
#: and both have to say yes, so a build with this off has no live toggle to
#: contradict it.
GLASS_ENABLED = True
#: How much palette colour sits over the blur, 0-255. Matches the console.
GLASS_TINT_ALPHA = 255

# --- UI scale ---------------------------------------------------------------
#
# The pages were laid out on a 4K screen, and a 1080p one has to fit the same
# controls into a little under half the height. CustomTkinter can size every
# widget it owns by a single factor -- text, padding, entry heights, the lot --
# so the pages are scaled down on a small screen rather than redrawn for it.
#
# Measured on the Splatting page, which is the densest: at 1.0 its two rails
# ask for 994px of width and its left rail for 443px of height; at 0.85 that
# is 907 and 355. Those 87 pixels of width go straight to the preview, and the
# height is what lets the wide layout -- the one with the depth and projection
# boxes UNDER the preview -- fit on a 1080p screen at all.
#
# The thresholds are in LOGICAL pixels, i.e. after Windows' own display
# scaling, because that is what decides how much fits -- not the panel's spec
# sheet. A 4K screen at 150% has the room of a 1440p one at 100% and is
# treated as one; a 1080p screen at 125% has the room of a 864px one.
#
# 1440 logical and up is left at 1.00 deliberately: that is where these pages
# were laid out and what the wide layout was measured against, and it already
# fits with hundreds of pixels to spare. Shrinking it would be a change to the
# screen nobody complained about.
#
# ttk chrome -- the menu strip, the tab strip, the status bar -- is deliberately
# NOT scaled. Those fonts are the OS's, a stage never lays out against them,
# and shrinking global named fonts would reach every dialog in the process.
_UI_SCALE_STEPS = (
    # (minimum logical screen height, factor)
    (1300, 1.00),   # 1440p logical and up: the size everything was drawn at
    (1000, 0.85),   # 1080p: the case this exists for
    (0, 0.75),      # smaller again, or a heavily scaled display
)

#: Bounds for an override, so a bad config value cannot make the app unusable.
UI_SCALE_MIN, UI_SCALE_MAX = 0.6, 1.5

_ui_scale = 1.0


def ui_scale() -> float:
    """The factor CustomTkinter widgets are currently sized by."""
    return _ui_scale


def screen_ui_scale(window: Any) -> float:
    """The factor this screen calls for, before any override.

    Measured against the display the WINDOW is on, not the primary one: a
    1080p second monitor next to a 1440p main is exactly the case this is
    for, and winfo_screenheight() only ever answers for the primary.
    """
    height = None
    size = win_backdrop.monitor_size(window)
    if size:
        height = size[1]
    if not height:
        try:
            height = int(window.winfo_screenheight())
        except Exception:
            return 1.0
    dpi = 1.0
    try:
        # CTk multiplies its own factor by the window's display scaling, so
        # the question is how much fits, not how many pixels there are.
        from customtkinter.windows.widgets.scaling.scaling_tracker import ScalingTracker

        dpi = float(ScalingTracker.get_window_dpi_scaling(window)) or 1.0
    except Exception:
        logger.debug("Could not read the display scaling", exc_info=True)
    logical = height / max(dpi, 0.1)
    for minimum, factor in _UI_SCALE_STEPS:
        if logical >= minimum:
            return factor
    return 1.0


def prime_ui_scale(window: Any) -> float:
    """Set the UI scale for the window's screen WITHOUT rescaling anything.

    For CustomTkinter's own DPI change (Sharp mode), which is about to
    rescale every widget anyway: setting the factor first lets that one pass
    apply the new screen's DPI and its UI scale together. Changing it
    afterwards with apply_ui_scale meant a second full pass -- several
    seconds each with every stage built. Returns the factor.
    """
    global _ui_scale
    factor = screen_ui_scale(window)
    try:
        from customtkinter.windows.widgets.scaling.scaling_tracker import ScalingTracker

        ScalingTracker.widget_scaling = max(factor, 0.4)
        _ui_scale = factor
    except Exception:
        logger.debug("Could not prime the UI scale", exc_info=True)
    return _ui_scale


def apply_ui_scale(window: Any, override: Optional[float] = None) -> float:
    """Size every CustomTkinter widget for this screen. Returns the factor.

    Call it ONCE, on the first window, before the pages are built: the factor
    is process-wide, and a page measured at one scale and re-gridded at
    another is a fit the shell has to redo.
    """
    global _ui_scale
    factor = screen_ui_scale(window)
    if override:
        try:
            factor = min(UI_SCALE_MAX, max(UI_SCALE_MIN, float(override)))
        except (TypeError, ValueError):
            logger.warning("Ignoring an unusable ui_scale override: %r", override)
    try:
        ctk.set_widget_scaling(factor)
    except Exception:
        logger.debug("Could not set the CustomTkinter widget scaling", exc_info=True)
        return _ui_scale
    _ui_scale = factor
    logger.info("UI scale: %.2f", factor)
    return factor


#: The ttk style that keys a stage's own ground -- see
#: :meth:`CtkThemedStage._ctk_glass_ground`. One shared name is enough: the key
#: is a constant, so every stage on glass wants exactly the same style.
GLASS_GROUND_STYLE = "Glass.Stage.TFrame"

#: Corner radius for every converted control. Chosen against the frosted-glass
#: mockup in scripts/ctk_glass_mockup.py.
CTK_RADIUS = 10

#: A one-pixel edge, as a FLOAT on purpose.
#:
#: CustomTkinter scales an INT dimension with ``int(value * scaling)``, which
#: truncates. At a widget scaling of 0.85 on a 1.0-DPI display ``int(1 * 0.85)``
#: is 0, so the draw engine takes the else-branch of its ``border_width > 0``
#: test and DELETES the border parts: every section outline, button edge and
#: segmented strip loses its border, with cget() still reporting 1. A float is
#: scaled without truncation and rounded by the draw engine instead, so 1.0
#: survives every factor down to 0.6 -- the bottom of the apply_ui_scale clamp.
#:
#: The factor that matters is apply_ui_scale's TIMES the window's DPI scaling,
#: which is why this only ever showed on the 1080p display: at 150% the product
#: stays above 1.0 and the truncation never bites. Anything drawing a hairline
#: on a CTk widget must use this, not a bare 1.
CTK_HAIRLINE = 1.0
#: Text colour for regular buttons on a LIGHT palette. None keeps the
#: palette's own foreground (dark text). Set to white as a trial of how white
#: labels read on the light themes' pale button fills.
CTK_LIGHT_BUTTON_TEXT = "#ffffff"
#: How much contrast white button text gets on a light palette. The pale
#: fill a light palette gives its buttons is darkened, keeping its tint,
#: until white on it reaches this ratio. 4.5:1 is the usual floor for text
#: this size.
CTK_LIGHT_BUTTON_CONTRAST = 4.5
#: The primary button keeps white text down to this contrast with the accent.
#: Deliberately low: white is a choice there, kept on accents it only reads
#: modestly on (Nord's is 2.0:1). Below this it cannot be read at all --
#: Graphite's accent is white itself -- and the text turns dark grey.
ACCENT_WHITE_MIN_CONTRAST = 1.5

#: Slider knob: a small rounded rectangle rather than CTk's default round dot.
#: button_length and button_corner_radius go together -- at the default
#: length of 0 there is no width to round, and a radius on its own draws the
#: knob as a bare vertical line. A radius of 0 is worse: CustomTkinter 6.0.0
#: renders it as a bowtie at any length, so 2 is the lowest usable value.
CTK_SLIDER_HEIGHT = 16
CTK_SLIDER_KNOB_LENGTH = 6
CTK_SLIDER_KNOB_RADIUS = 3

_NAMED = {"white": "#ffffff", "black": "#000000"}


def _rgb(hex_color: Any):
    h = str(_NAMED.get(str(hex_color).lower(), hex_color)).lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return [int(h[i:i + 2], 16) for i in (0, 2, 4)]
    except (ValueError, IndexError):
        return None


def luma(hex_color: Any) -> float:
    """Rec. 601 luma, 0-255. Enough to compare two colours for legibility."""
    parts = _rgb(hex_color)
    if parts is None:
        return 0.0
    return 0.299 * parts[0] + 0.587 * parts[1] + 0.114 * parts[2]


def readable_on(hex_color: Any) -> str:
    """Black or white, whichever can actually be read on `hex_color`."""
    parts = _rgb(hex_color)
    if parts is None:
        return "#ffffff"
    return "#000000" if luma(hex_color) > 150 else "#ffffff"


def _contrast_with_white(hex_color: Any) -> float:
    """WCAG contrast ratio of white text on `hex_color`."""
    parts = _rgb(hex_color)
    if parts is None:
        return 21.0
    lin = [c / 255.0 for c in parts]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in lin]
    lum = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    return 1.05 / (lum + 0.05)


def darken_for_white(hex_color: Any, ratio: float) -> str:
    """`hex_color` darkened, in steps, until white on it reaches `ratio`."""
    amount = 0.0
    out = hex_color
    while _contrast_with_white(out) < ratio and amount < 0.95:
        amount += 0.02
        out = shade(hex_color, -amount)
    return out


def shade(hex_color: Any, amount: float) -> str:
    """`amount` of the way toward white (positive) or black (negative)."""
    parts = _rgb(hex_color)
    if parts is None:
        return hex_color
    target = 255 if amount > 0 else 0
    return "#%02x%02x%02x" % tuple(
        max(0, min(255, round(c + (target - c) * abs(amount)))) for c in parts
    )


def glass_dialog(window: Any, colors: Optional[Dict[str, Any]] = None,
                 is_dark: Optional[bool] = None) -> bool:
    """Put a plain ttk dialog on the acrylic backdrop. Returns whether it took.

    A converted stage keys its surfaces widget by widget, because CustomTkinter
    has no styles to key. A ttk dialog is the opposite case: it owns no palette
    at all -- it simply inherits the process-wide ttk styles -- so it is keyed
    by STYLE here, and the only thing it has to be told is which palette is in
    force. Left out, that is taken from the one the app last applied.

    Same policy as a stage, for the same reason (see the note above
    GLASS_ENABLED): the ground is always keyed, and on a DARK palette the text
    surfaces go with it so the dialog reads as one sheet of glass. On a LIGHT
    palette they keep the palette's colour, which is what makes them the solid
    plates the text sits on.

    Controls with a fill of their own -- entries, comboboxes, buttons -- are
    left alone either way: only their edges ever see the ground.

    Call it once the dialog is fully built; it walks what is there. A dialog
    is built fresh each time it is opened, so there is no "off" path here --
    the next one simply comes up opaque.
    """
    if colors is None:
        try:
            from core.ui.widgets import get_active_palette

            colors = get_active_palette()
        except Exception:
            colors = {}
    colors = colors or {}
    if is_dark is None:
        is_dark = bool(colors.get("is_dark", True))
    if not (GLASS_ENABLED and win_backdrop.is_enabled() and win_backdrop.is_supported()):
        return False

    if not win_backdrop.apply(window, "acrylic", tint=colors.get("bg", "#1e1e1e"),
                              alpha=GLASS_TINT_ALPHA):
        win_backdrop.clear(window)
        return False
    if not win_backdrop.set_chroma_key(window, True):
        win_backdrop.clear(window)
        return False
    try:
        window.configure(bg=win_backdrop.CHROMA_KEY)
    except Exception:
        logger.debug("Could not key the dialog background", exc_info=True)

    # DERIVED style names, never the bare ones: TFrame and TLabel belong to
    # every window in the process, and keying those would punch holes in the
    # stage this dialog is sitting on top of.
    key = win_backdrop.CHROMA_KEY
    try:
        style = ttk.Style(window)
        style.configure(GLASS_GROUND_STYLE, background=key)
        keyed = {"TFrame": GLASS_GROUND_STYLE}
        if is_dark:
            for cls in ("TLabel", "TCheckbutton", "TRadiobutton", "TLabelframe"):
                name = "Glass." + cls
                style.configure(name, background=key)
                keyed[cls] = name
            # A labelframe's title is a style of its own, and keeps the
            # frame's old background otherwise.
            style.configure("Glass.TLabelframe.Label", background=key)
    except Exception:
        logger.debug("Could not configure the dialog's glass styles", exc_info=True)
        return True

    def restyle(widget) -> None:
        for child in widget.winfo_children():
            cls = child.winfo_class()
            name = keyed.get(cls)
            if name is not None:
                try:
                    # A widget that was given a style on purpose keeps it: a
                    # Toolbutton or a Plain.TLabelframe is a deliberate look,
                    # and this is only meant to catch the defaults.
                    current = str(child.cget("style") or "")
                    if not current or current == cls:
                        child.configure(style=name)
                except Exception:
                    logger.debug("Could not key %r", child, exc_info=True)
            restyle(child)

    restyle(window)
    return True


class CtkProgressAdapter(ctk.CTkProgressBar):
    """A CTkProgressBar that still answers ttk.Progressbar's item protocol.

    The stages drive progress as ``bar["value"]`` and ``bar["maximum"]``, from
    many places and often from callbacks queued off worker threads. CTk works
    in 0..1 and offers no item access, so the two keys are kept and translated
    here rather than rewritten at every call site.

    Pass it to :meth:`CtkThemedStage._ctk_progress` as ``factory=``.
    """

    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self._pb_value = 0.0
        # ttk.Progressbar defaults to 100, and a stage that never sets
        # ["maximum"] is relying on exactly that -- Inpainting V1 does, and
        # with a 1.0 default every value above 1 pinned the bar to full.
        self._pb_maximum = 100.0
        self.set(0.0)

    def _refresh(self):
        try:
            self.set(max(0.0, min(1.0, self._pb_value / (self._pb_maximum or 1.0))))
        except (TypeError, ValueError, ZeroDivisionError):
            pass

    def __setitem__(self, key, value):
        if key == "value":
            self._pb_value = float(value)
        elif key == "maximum":
            self._pb_maximum = float(value) or 1.0
        else:
            return super().__setitem__(key, value)
        self._refresh()

    def __getitem__(self, key):
        if key == "value":
            return self._pb_value
        if key == "maximum":
            return self._pb_maximum
        return super().__getitem__(key)

    def configure(self, require_redraw=False, **kwargs):
        """Also takes ttk's ``configure(maximum=..., value=...)`` form.

        CTkProgressBar rejects both keys outright with a ValueError, and
        Splatting sets its maximum this way when a batch starts -- which
        raised on every run before the first clip.
        """
        touched = False
        if "maximum" in kwargs:
            self._pb_maximum = float(kwargs.pop("maximum")) or 1.0
            touched = True
        if "value" in kwargs:
            self._pb_value = float(kwargs.pop("value"))
            touched = True
        if kwargs or require_redraw:
            super().configure(require_redraw=require_redraw, **kwargs)
        if touched:
            self._refresh()

    config = configure

def _skip_flush() -> None:
    """Stands in for a canvas's update_idletasks -- see CtkArrowOptionMenu._draw."""


class CtkArrowOptionMenu(ctk.CTkOptionMenu):
    """A CTkOptionMenu whose arrow stays legible on a light button block.

    CustomTkinter draws the arrow in `text_color` -- the same colour as the
    value text. But the text sits on `fg_color` (the field) and the arrow sits
    on `button_color` (the block beside it), and those are two different
    surfaces. A palette whose accent is near-white paints a white arrow on a
    white block: Graphite's accent is literally "#ffffff", and its dropdowns
    had no visible arrow at all.

    Recoloured after every redraw rather than once, because CTkOptionMenu._draw
    resets the arrow to text_color each time -- and it redraws on hover, on
    state changes and on resize, so a one-shot fix would last until the first
    time the pointer crossed the widget.
    """

    def _draw(self, no_color_updates=False):
        # CTkOptionMenu._draw ends in self._canvas.update_idletasks(): a flush
        # of the WHOLE app's pending geometry and redraws, not this widget's,
        # on every draw of every dropdown. A monitor change rescales ~20 of
        # them in one pass, and each flush re-ran the layout work every other
        # widget had queued -- measured at about 0.2 s a dropdown, most of the
        # freeze. Tk draws at idle anyway, which is moments later, so the
        # flush is skipped here. Shadowed for the one call, not for good, so
        # anything else asking the canvas to flush still can.
        canvas = self._canvas
        canvas.update_idletasks = _skip_flush
        try:
            super()._draw(no_color_updates)
        finally:
            try:
                del canvas.update_idletasks
            except AttributeError:
                pass
        try:
            block = self._apply_appearance_mode(self._button_color)
            self._canvas.itemconfig("dropdown_arrow", fill=readable_on(block))
        except Exception:
            logger.debug("Could not recolour the dropdown arrow", exc_info=True)


class CtkAccentButton(ctk.CTkButton):
    """The primary action's button: the accent when enabled, and an ordinary
    disabled button -- grey fill and outline, like Cancel -- when it is not.

    CTkButton has one fill for every state and only greys the TEXT when
    disabled, so a disabled accent button stayed a bright accent block. The
    painter leaves both colour sets on the widget (``_sc_state_colors``) and
    they are swapped here whenever the state changes, from wherever it is
    changed -- a stage disabling Start for a run, or its input-state walk.
    """

    _sc_state_colors: Optional[Dict[str, Dict[str, Any]]] = None

    def configure(self, require_redraw=False, **kwargs):
        state_changed = "state" in kwargs
        super().configure(require_redraw=require_redraw, **kwargs)
        if state_changed:
            self.sc_apply_state_colors()

    def sc_apply_state_colors(self) -> None:
        colors = self._sc_state_colors
        if not colors:
            return
        key = "disabled" if str(self._state) == tk.DISABLED else "normal"
        super().configure(**colors[key])


class CtkSafeSlider(ctk.CTkSlider):
    """A CTkSlider that tolerates an empty range.

    CTkSlider positions its knob as ``(value - from_) / (to - from_)`` and so
    divides by zero whenever the two bounds are equal. That is not an exotic
    state: a frame scrubber is created ``from_=0, to=0`` before any clip is
    loaded, and it goes back there for a one-frame clip. ttk.Scale simply drew
    the knob at the start.

    Worse than the constructor raising, a variable write in that state raises
    from inside a Tk callback -- so it would not have surfaced as a crash at
    all, just a traceback on the console every time the scrubber variable was
    touched with no video loaded.
    """

    def set(self, output_value, from_variable_callback=False):
        if self._to == self._from_:
            self._output_value = self._round_to_step_size(self._from_)
            self._value = 0.0
            self._draw(no_color_updates=False)
            if self._variable is not None and not from_variable_callback:
                self._variable_callback_blocked = True
                self._variable.set(
                    round(self._output_value)
                    if isinstance(self._variable, tk.IntVar)
                    else self._output_value
                )
                self._variable_callback_blocked = False
            return
        return super().set(output_value, from_variable_callback)


class CtkSpin(ctk.CTkFrame):
    """An entry with two step buttons, standing in for ttk.Spinbox.

    CustomTkinter ships no spinbox at all, and the stages use them wherever a
    number has a small, bounded range -- a validation window of 2..10 frames,
    a CQ level of 16..30. An entry on its own loses the two things a spinbox
    was doing there: it stops advertising that the range is small, and it
    stops stopping you leaving a number outside it.

    Two shapes. The default is ``[-] [ entry ] [+]`` with 22px buttons, which
    are a far better click target than the 13px half-arrows ttk drew. Where
    the width matters more than the target -- a spin in the grid column that
    happens to set the whole page width, which is what Scene Cut's thumbnail
    options are -- ``compact=True`` stacks the two arrows in one narrow
    column on the right, the way ttk had them: 93px instead of 158.

    The compact arrows are CTkLabels with a click binding, not CTkButtons,
    because a CTkButton will not go below 25px tall whatever height it is
    given -- two of those stacked come to 53px and make the row TALLER than
    the wide shape it was meant to shrink. A label floors at 17px, so the
    pair fits inside the entry's own height. The wheel and the up/down keys
    work in either shape.

    Two things are deliberate and easy to undo by accident:

      * **The entry is never bound to the caller's variable.** CTkEntry traces
        its textvariable and calls ``.get()`` on it at every keystroke, so an
        IntVar raises TclError the moment the field is empty or holds a letter
        -- which is every field someone is part-way through clearing. The
        entry drives a StringVar of its own and the two are mirrored, so the
        caller's IntVar only ever sees a number.
      * **Only an in-range number reaches the caller's variable.** Half-typed
        text leaves the last good value in place, and :meth:`_commit` -- on
        Return and on leaving the field -- puts the entry back in step with
        it. ttk.Spinbox let a typed number sit outside the range indefinitely.
    """

    def __init__(self, master, from_=0, to=100, increment=1, textvariable=None,
                 width: int = 64, compact: bool = False, **kw):
        kw.setdefault("fg_color", "transparent")
        # See _ctk_container: an empty CTkFrame asks for 200x200.
        super().__init__(master, width=0, height=0, **kw)
        self._from = from_
        self._to = to
        self._step = increment
        # An integral range writes back ints, so an IntVar stays writable and
        # the entry never shows "3.0" where it showed "3".
        self._integral = all(
            float(v).is_integer() for v in (from_, to, increment)
        )
        self.variable = textvariable
        self._syncing = False
        # The compact arrows are labels, which have no disabled state of their
        # own, so the flag is what stops them stepping. See configure().
        self._enabled = True

        initial = self._coerce(self._var_value())
        # `is None`, not `or`: 0 is a legitimate value and several of
        # these ranges are centred on it.
        initial = self._clamp(from_ if initial is None else initial)
        self._text = tk.StringVar(value=str(initial))

        self.buttons = []
        self.compact = compact
        self.entry = ctk.CTkEntry(self, corner_radius=CTK_RADIUS, width=width,
                                  height=24, justify="center",
                                  textvariable=self._text)

        def _step_button(parent, text, delta, w, h, radius):
            b = ctk.CTkButton(parent, text=text, width=w, height=h,
                              corner_radius=radius,
                              font=ctk.CTkFont(size=9 if compact else 13),
                              command=lambda d=delta: self.step(d))
            self.buttons.append(b)
            return b

        self.arrows = []
        if compact:
            self.entry.pack(side="left")
            # Its own column so the two arrows stack; it paints nothing, but
            # it still owns a bg_color -- see _ctk_spin, which registers it.
            self.arrow_column = ctk.CTkFrame(self, fg_color="transparent",
                                             width=0, height=0)
            self.arrow_column.pack(side="left", padx=(2, 0))
            for glyph, delta, pady in (("\u25b2", +1, (0, 0)), ("\u25bc", -1, (1, 0))):
                a = ctk.CTkLabel(self.arrow_column, text=glyph, width=14,
                                 height=8, font=ctk.CTkFont(size=9),
                                 cursor="hand2")
                a.pack(pady=pady)
                a.bind("<Button-1>", lambda e, d=delta: self.step(d))
                self.arrows.append(a)
        else:
            self.arrow_column = None
            _step_button(self, "\u2212", -1, 22, 24, CTK_RADIUS).pack(side="left")
            self.entry.pack(side="left", padx=2)
            _step_button(self, "+", +1, 22, 24, CTK_RADIUS).pack(side="left")

        self._text.trace_add("write", self._on_text)
        if self.variable is not None:
            self.variable.trace_add("write", self._on_variable)

        self.entry.bind("<MouseWheel>", self._on_wheel)
        self.entry.bind("<Up>", lambda e: self.step(+1))
        self.entry.bind("<Down>", lambda e: self.step(-1))
        self.entry.bind("<Return>", lambda e: self._commit())
        self.entry.bind("<FocusOut>", lambda e: self._commit())

    # -- value -----------------------------------------------------------

    @staticmethod
    def _coerce(raw):
        try:
            return float(str(raw).strip())
        except (TypeError, ValueError):
            return None

    def _clamp(self, value):
        value = max(self._from, min(self._to, value))
        return int(round(value)) if self._integral else value

    def _var_value(self):
        """The caller's variable, or None -- it raises when the text is not a number."""
        if self.variable is None:
            return None
        try:
            return self.variable.get()
        except Exception:
            return None

    def get(self):
        """The current value, clamped. Falls back to the minimum on garbage."""
        value = self._coerce(self._text.get())
        if value is None:
            value = self._coerce(self._var_value())
        return self._clamp(self._from if value is None else value)

    def set(self, value) -> None:
        value = self._coerce(value)
        value = self._clamp(self._from if value is None else value)
        self._syncing = True
        try:
            self._text.set(str(value))
            if self.variable is not None:
                self.variable.set(value)
        finally:
            self._syncing = False

    def step(self, direction: int) -> str:
        """Move one increment. Garbage in the field falls back to the minimum."""
        if not self._enabled:
            return "break"
        current = self._coerce(self._text.get())
        if current is None:
            current = self._from
        else:
            current += self._step * direction
        self.set(current)
        return "break"

    def _commit(self) -> None:
        """Put the entry back in step with a clamped value, on Return or focus loss."""
        self.set(self.get())

    def _on_wheel(self, event) -> str:
        return self.step(1 if getattr(event, "delta", 0) > 0 else -1)

    # -- mirroring --------------------------------------------------------

    def _on_text(self, *_) -> None:
        if self._syncing or self.variable is None:
            return
        value = self._coerce(self._text.get())
        # Out of range is left alone rather than clamped mid-typing: someone
        # heading for 15 in a 10..80 field types "1" first, and snapping that
        # to 10 fights them for the field.
        if value is None or not (self._from <= value <= self._to):
            return
        self._syncing = True
        try:
            self.variable.set(int(round(value)) if self._integral else value)
        except Exception:
            logger.debug("Could not set the spin variable", exc_info=True)
        finally:
            self._syncing = False

    def _on_variable(self, *_) -> None:
        """Follow a variable set from elsewhere -- loading settings, mostly."""
        if self._syncing:
            return
        raw = self._var_value()
        if raw is None:
            return
        self._syncing = True
        try:
            self._text.set(str(raw))
        finally:
            self._syncing = False

    # -- widget protocol --------------------------------------------------

    def configure(self, require_redraw=False, **kwargs):
        """Pass `state` through to the parts; a CTkFrame has no state of its own."""
        if "state" in kwargs:
            state = kwargs.pop("state")
            self._enabled = str(state) != "disabled"
            for part in (self.entry, *self.buttons):
                try:
                    part.configure(state=state)
                except Exception:
                    logger.debug("Could not set the spin state", exc_info=True)
        if kwargs or require_redraw:
            super().configure(require_redraw=require_redraw, **kwargs)


#: (font, padding, scaling) per toplevel, for sizing option menus. Measured
#: once per window: the probe costs an idle cycle and the answer cannot change
#: while the display and the font stay put.
_OPTION_METRICS: Dict[str, Optional[Tuple[Any, int, float]]] = {}


def _option_metrics(master) -> Optional[Tuple[Any, int, float]]:
    """What it takes to predict a CTkOptionMenu's natural width.

    Measured against the widget's OWN rendered font, the padding around the
    text -- the arrow block and the margins -- is the same integer for every
    string, so one probe answers for all of them and the rest is arithmetic.
    Measuring with ``ctk.CTkFont()`` instead does not work: that is the
    unscaled font, and the difference then varies with the string.
    """
    try:
        key = str(master.winfo_toplevel())
    except Exception:
        return None
    if key in _OPTION_METRICS:
        return _OPTION_METRICS[key]

    result = None
    try:
        probe = ctk.CTkOptionMenu(master, values=["Mg"], width=1, height=24)
        probe.update_idletasks()
        font = tkfont.Font(font=probe._text_label.cget("font"))
        padding = probe.winfo_reqwidth() - font.measure("Mg")
        try:
            scaling = ctk.ScalingTracker.get_widget_scaling(master.winfo_toplevel())
        except Exception:
            scaling = 1.0
        probe.destroy()
        if padding >= 0 and scaling > 0:
            result = (font, padding, scaling)
    except Exception:
        # A CustomTkinter without _text_label, or no display: fall back to the
        # widget's own default width rather than guessing.
        logger.debug("Could not measure option-menu metrics", exc_info=True)

    _OPTION_METRICS[key] = result
    return result


def refresh_segmented(widget) -> None:
    """Recolour a segmented button's labels for whichever half is selected."""
    colors = getattr(widget, "_sc_seg_colors", None)
    if not colors:
        return
    on_accent, fg = colors
    try:
        selected = widget.get()
        for value, button in getattr(widget, "_buttons_dict", {}).items():
            button.configure(text_color=on_accent if value == selected else fg)
    except Exception:
        logger.debug("Could not recolour segmented labels", exc_info=True)


def option_width_for(master, values) -> Optional[int]:
    """The `width` an option menu needs so no value makes it resize.

    A CTkOptionMenu asks for the width of the value it is CURRENTLY showing
    and shrinks again for a shorter one, so anything narrower than the longest
    value makes the control -- and everything gridded beside it -- jump every
    time the selection changes. Sizing to the widest value keeps it tight and
    still. Returns None when it cannot be measured, so the caller leaves the
    width alone.
    """
    metrics = _option_metrics(master)
    if metrics is None or not values:
        return None
    font, padding, scaling = metrics
    try:
        widest = max(font.measure(str(v)) for v in values)
    except Exception:
        return None
    return int((widest + padding) / scaling) + 1


#: Registry buckets, by the role a widget plays in the palette.
_ROLES = (
    "containers",      # paint nothing, but still own a bg_color
    "frames",          # the rounded section cards
    "plates",          # a solid backing on glass, invisible without it
    "headers",         # a section's title
    "labels",
    "entries",
    "buttons",
    "accent_buttons",  # the one primary action per screen
    "checks",
    "options",         # CTkOptionMenu, standing in for a readonly combobox
    "sliders",
    "radios",
    "segmented",       # CTkSegmentedButton, for the ttk "Toolbutton" radio strips
    "progress",
)


def _remember_geometry(widget, kwargs: dict) -> None:
    """Fold `kwargs` into the geometry call CustomTkinter replays.

    CTk records the arguments of ``grid()``/``pack()``/``place()`` and REPLAYS
    them verbatim whenever the widget scaling changes -- which happens on a
    DPI change, such as dragging the window to a monitor at another scale.

    It does not record ``grid_configure()`` or ``pack_configure()``: those are
    plain Tk methods that CTk never wrapped. So a later tweak is invisible to
    the replay, and the next scaling change silently restores the ORIGINAL
    arguments. Two bugs came from exactly this, both reported as "it doesn't
    hold when I resize": centred preview controls jumping back to the left
    (their sticky="" reverted to "ew"), and the keyframe marker strip ending
    up on top of the frame slider (the slider's row reverted from 1 to 0,
    which is where the strip had been put).

    Padding is deliberately NOT remembered. CTk re-scales padx/pady from the
    stored values on every replay, so writing an already-scaled number back
    would compound the scaling on each DPI change.
    """
    call = getattr(widget, "_last_geometry_manager_call", None)
    if not isinstance(call, dict) or not isinstance(call.get("kwargs"), dict):
        return                      # a plain Tk widget, or never laid out yet
    call["kwargs"].update({k: v for k, v in kwargs.items()
                           if k not in ("padx", "pady", "ipadx", "ipady")})


#: How far a glass plate reaches past the control it backs, in logical px.
GLASS_PLATE_MARGIN = 2


def _rounded_rect_points(w: float, h: float, r: float, steps: int = 8) -> list:
    """Polygon points for a w x h rectangle with corners of radius r."""
    import math

    r = max(0.0, min(r, w / 2.0, h / 2.0))
    pts = []
    for cx, cy, start in ((w - r, r, -90), (w - r, h - r, 0), (r, h - r, 90), (r, r, 180)):
        for i in range(steps + 1):
            a = math.radians(start + 90.0 * i / steps)
            pts.extend((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def _sync_glass_plate(widget, color: Optional[str]) -> None:
    """Put a plate of `color` just behind `widget`, or take it away (None).

    A rounded CTk control softens its corners by blending them with what is
    under them. Standing on frosted glass that is the chroma key, which is
    nearly black, and Windows only cuts out the pixels that match it EXACTLY
    -- so the half-blended corner pixels stay, as a dark fringe that stands
    out on a light page. The same control on a solid card is smooth.

    The plate is the control's own shape, GLASS_PLATE_MARGIN bigger all
    round, in the page colour, drawn as plain Tk polygons -- which Tk does
    NOT antialias, so the plate's own edge is a hard step between page colour
    and key, with no fringe to leave. It is drawn in two halves:

      * OUTSIDE the control, a canvas placed `in_` it, a margin bigger all
        round and stacked just beneath it. `place` follows the control's
        size and position and unmaps with it when the control is hidden,
        without touching its own grid or pack.
      * INSIDE the control's own canvas, the same shape clipped to it, under
        the control's drawing. This is what gets the corners right: the
        control keeps the key as its bg_color, so its canvas corners are
        glass where the plate curves away, and its antialiasing lands on the
        polygon underneath. Giving the control a solid bg_color instead
        filled its square corners with page colour, which stuck out past the
        plate's curve and made every corner read as square.
    """
    plate = getattr(widget, "_sc_glass_plate", None)
    inner = getattr(widget, "_canvas", None)
    if color is None:
        if plate is not None:
            try:
                plate.place_forget()
            except tk.TclError:
                pass
        widget._sc_plate_color = None
        if inner is not None:
            try:
                inner.delete("sc_plate")
            except tk.TclError:
                pass
        return
    try:
        widget._sc_plate_color = color
        if plate is None or not plate.winfo_exists():
            plate = tk.Canvas(widget.master, highlightthickness=0, borderwidth=0,
                              bg=win_backdrop.CHROMA_KEY, takefocus=0)
            plate.bind("<Configure>", lambda _e, w=widget: _draw_glass_plate(w), add="+")
            widget.bind("<Destroy>", lambda e, c=plate, w=widget: (
                c.destroy() if e.widget is w and c.winfo_exists() else None), add="+")
            widget._sc_glass_plate = plate
            # CTk redraws its canvas on every resize, hover and recolour, and
            # may restack its own parts as it does; the inner half is put
            # back underneath after each of those.
            original_draw = widget._draw

            def _draw(*args, _orig=original_draw, _w=widget, **kwargs):
                result = _orig(*args, **kwargs)
                _draw_glass_plate(_w)
                return result

            widget._draw = _draw
        plate.configure(bg=win_backdrop.CHROMA_KEY)
        margin = int(round(widget._apply_widget_scaling(GLASS_PLATE_MARGIN)))
        plate.place(in_=widget, x=-margin, y=-margin, relwidth=1.0, relheight=1.0,
                    width=2 * margin, height=2 * margin)
        # Directly beneath its control, whatever order they were made in.
        widget.tk.call("lower", plate._w, widget._w)
        _draw_glass_plate(widget)
    except (tk.TclError, AttributeError):
        logger.debug("Could not set a glass plate", exc_info=True)


def _draw_glass_plate(widget) -> None:
    """Draw both halves of `widget`'s glass plate for its current size."""
    color = getattr(widget, "_sc_plate_color", None)
    plate = getattr(widget, "_sc_glass_plate", None)
    inner = getattr(widget, "_canvas", None)
    if not color or plate is None:
        return
    try:
        margin = widget._apply_widget_scaling(GLASS_PLATE_MARGIN)
        radius = widget._apply_widget_scaling(float(getattr(widget, "_corner_radius", 0) or 0))
        plate.delete("plate")
        width, height = plate.winfo_width(), plate.winfo_height()
        if width > 1 and height > 1:
            plate.create_polygon(_rounded_rect_points(width, height, radius + margin),
                                 fill=color, outline="", tags="plate")
        if inner is not None:
            inner.delete("sc_plate")
            w, h = inner.winfo_width(), inner.winfo_height()
            if w > 1 and h > 1:
                points = _rounded_rect_points(w + 2 * margin, h + 2 * margin, radius + margin)
                points = [v - margin for v in points]
                inner.create_polygon(points, fill=color, outline="", tags="sc_plate")
                inner.tag_lower("sc_plate")
                if inner.find_withtag("background_parts"):
                    inner.tag_raise("sc_plate", "background_parts")
    except (tk.TclError, AttributeError):
        pass


_PADDING = ("padx", "pady", "ipadx", "ipady")


def _relayout_scaled(widget, method: str, kwargs: dict) -> bool:
    """Apply a PADDING change through CTk's own grid()/pack(). True if done.

    Padding cannot go through grid_configure/pack_configure plus
    _remember_geometry: the value would be applied unscaled (7px where a 150%
    screen wants 10.5) and, not being remembered, lost on the next replay --
    which is how a collapsed section lost its bottom padding after a scaling
    change and drew its border through its own title. Calling CTk's method
    with the recorded arguments plus the change scales the padding and
    records the logical value, so a replay keeps it. Re-issuing grid()/pack()
    on a widget already managed keeps its place in the packing order.
    """
    if not any(k in kwargs for k in _PADDING):
        return False
    call = getattr(widget, "_last_geometry_manager_call", None)
    if (not isinstance(call, dict) or not isinstance(call.get("kwargs"), dict)
            or not hasattr(widget, "_apply_argument_scaling")):
        return False
    merged = dict(call["kwargs"])
    merged.update(kwargs)
    getattr(widget, method)(**merged)
    return True


def regrid(widget, **kwargs) -> None:
    """``grid_configure`` that survives a CustomTkinter scaling change."""
    if _relayout_scaled(widget, "grid", kwargs):
        return
    widget.grid_configure(**kwargs)
    _remember_geometry(widget, kwargs)


def repack(widget, **kwargs) -> None:
    """``pack_configure`` that survives a CustomTkinter scaling change."""
    if _relayout_scaled(widget, "pack", kwargs):
        return
    widget.pack_configure(**kwargs)
    _remember_geometry(widget, kwargs)


class CtkThemedStage:
    """Mixin: build CustomTkinter widgets that follow the app's palettes.

    The host class supplies ``shell`` (or None) and ``theme_manager``.
    """

    def _app_fonts_on(self) -> bool:
        """Whether the bundled faces are there to draw with.

        Every stage uses them. There was briefly a per-stage opt-in, so one
        stage could be judged against the others in a running app; it is
        gone now that the look is settled, because it keyed off a stage id
        that had to be kept in step with the shell's and would have failed
        silently -- and invisibly -- if one were ever mistyped.

        This asks whether ALL THREE faces took. A single missing file is
        still handled per role by :meth:`_app_font`, which falls back on its
        own; this is the question a caller wants when it is asking whether
        the app as a whole is in its own typography.
        """
        return all(app_fonts.family_for(role, self) != app_fonts.FALLBACK
                   for role in ("heading", "body", "button"))

    def _app_font(self, role: str, size: int, weight: str = "normal"):
        """A CTkFont in the bundled face for `role`, or None to leave it be.

        None rather than a default font: returning something would override
        whatever the call site already chose, and several of them pass a
        deliberate font of their own.
        """
        family = app_fonts.family_for(role, self)
        if family == app_fonts.FALLBACK:
            return None            # nothing bundled took; leave the defaults
        try:
            # The heading and button faces are single weights: the heading
            # is a named SemiBold instance and Cal Sans has only Regular.
            # Asking either for bold gets a synthesised copy, which smears.
            if family in (app_fonts.HEADING, app_fonts.BUTTON):
                weight = "normal"
            return ctk.CTkFont(family=family, size=size, weight=weight)
        except Exception:
            logger.debug("Could not build the %s font", role, exc_info=True)
            return None

    def _heading_parent(self, body):
        """The frame a widget must be created in to sit on a section's heading.

        CTk widgets cannot be re-parented, and the heading belongs to the
        section's outer frame, not to the body its controls live in -- so
        anything meant for the heading line has to be BORN in the outer frame.
        Falls back to `body` for a container that is not a _section.
        """
        return getattr(body, "_sc_section_outer", body)

    def _seat_beside_heading(self, body, widget, gap: int = 12) -> bool:
        """Put `widget` on the heading line, directly after the title text.

        The heading is re-packed to its own text width instead of the full
        card, so `relx=1.0` on it lands right where the words end. `place`
        rather than a pack or grid cell, because the heading line has no cell
        to spare and placing leaves the title and the body exactly as built.

        The heading is also given the widget's height, so the row is as tall
        as the button: a placed widget reserves no space, and a heading only
        as tall as its text would let the button hang into the first row of
        the body. Returns False if `body` is not a _section.
        """
        header = getattr(body, "_sc_section_header", None)
        if header is None:
            return False
        try:
            height = int(float(widget.cget("height")))
            if height > int(float(header.cget("height") or 0)):
                header.configure(height=height)
            repack(header, fill="none", anchor="w")
            # The chapter dropdown, when there is one, sits straight after the
            # title and everything else after it.
            menu = getattr(self, "_chapter_menu", None)
            if (menu is not None and widget is not menu
                    and menu.winfo_manager() == "place"):
                widget.place(in_=menu, relx=1.0, x=gap, rely=0.5, anchor="w")
            else:
                widget.place(in_=header, relx=1.0, x=gap, rely=0.5, anchor="w")
            widget.lift()
        except tk.TclError:
            logger.debug("Could not seat a widget beside a heading", exc_info=True)
            return False
        return True

    # -- imported inputs -------------------------------------------------
    #
    # After an import button fills a stage's input folders, those rows are
    # put away: the paths came from the previous stage's job, so there is
    # nothing to type, and a line of small text naming what is in them says
    # enough. The import button becomes "Change Inputs", which brings the
    # rows back and turns itself back into the import button. Session only:
    # a restart shows the rows again, with the paths still filled in.

    #: Folder names that say nothing on their own, so the display name keeps
    #: climbing to the parent: ".../Chapter 37/finished/finished" reads as
    #: "Chapter 37/finished/finished", not as "finished".
    _GENERIC_INPUT_DIRS = frozenset({"finished", "hires", "lowres", "output", "outputs",
                                     "input", "inputs", "done"})

    @classmethod
    def _input_display_name(cls, path) -> str:
        p = str(path or "").replace("\\", "/").rstrip("/")
        parts = [x for x in p.split("/") if x]
        if not parts:
            return "(empty)"
        keep = [parts.pop()]
        while parts and keep[0].lower() in cls._GENERIC_INPUT_DIRS:
            keep.insert(0, parts.pop())
        # Kept short, because a label asks for its full text width and a long
        # name would widen the whole page. Only the leading, meaningful
        # folder is shortened -- the generic tail is what tells two imports
        # of the same project apart.
        limit = 60
        tail = "/".join(keep[1:])
        head = keep[0]
        room = limit - (len(tail) + 1 if tail else 0)
        if len(head) > room:
            head = head[:max(12, room - 1)].rstrip() + "\u2026"
        return f"{head}/{tail}" if tail else head

    def _import_rows(self, body, variables):
        """(label text, variable, row index, widgets in that row) per variable."""
        def walk(w):
            yield w
            for c in w.winfo_children():
                yield from walk(c)

        rows = []
        for var in variables:
            hit = None
            for w in walk(body):
                try:
                    tv = w.cget("textvariable")
                except Exception:
                    continue
                if tv is not None and str(tv) == str(var):
                    hit = w
                    break
            if hit is None:
                continue
            node = hit
            while node is not None and node.master is not body:
                node = node.master
            if node is None or node.winfo_manager() != "grid":
                continue
            row = int(node.grid_info()["row"])
            widgets = list(body.grid_slaves(row=row))
            label = ""
            for w in widgets:
                if isinstance(w, ctk.CTkLabel):
                    label = str(w.cget("text")).strip().rstrip(":")
            rows.append((label, var, row, widgets))
        return rows

    def _import_inputs_collapsed(self) -> bool:
        return bool(getattr(self, "_import_collapse", None))

    def _hide_import_button(self, btn) -> None:
        """Take the import button off the heading -- unless it is standing in
        as "Change Inputs", which is then the only way to get the rows back."""
        if self._import_inputs_collapsed():
            return
        btn.place_forget()

    def _inputs_button(self):
        """The button that reads "Change Inputs" while rows are put away: the
        stage's import button, or -- on a stage without one -- the chapter
        picker's own, which is only on screen while it is needed."""
        return getattr(self, "import_prev_btn", None) or getattr(self, "_chapter_change_btn", None)

    def _collapse_imported_inputs(self, variables, outputs=()) -> None:
        """Put away the rows an import just filled; see the section comment.

        `outputs` are the variables among them that name an OUTPUT folder, so
        the summary can say "(new)" for one that does not exist yet rather
        than "(not found)".
        """
        btn = self._inputs_button()
        body = getattr(self, "_import_prev_body", None)
        if btn is None or body is None:
            return
        # A second import while collapsed: start from the rows shown.
        self._expand_imported_inputs(refresh_button=False)
        try:
            rows = self._import_rows(body, variables)
            if not rows:
                return
            saved = []
            for _label, _var, _row, widgets in rows:
                for w in widgets:
                    # The arguments CTk replays on a scale change, so the row
                    # comes back with its padding scaled for wherever the
                    # window is by then; plain Tk widgets restore from Tk.
                    call = getattr(w, "_last_geometry_manager_call", None)
                    kwargs = (dict(call["kwargs"])
                              if isinstance(call, dict) and call.get("kwargs") else None)
                    saved.append((w, kwargs))
                    w.grid_remove()

            summary = getattr(self, "_import_summary", None)
            if summary is None or not summary.winfo_exists():
                from core.ui.widgets import Tooltip

                summary = self._ctk_label(body, text="", anchor="w", justify="left")
                small = self._app_font("body", 11)
                if small is not None:
                    summary.configure(font=small)
                self._import_summary = summary
                self._import_summary_tip = Tooltip(summary, "")
            summary.grid(row=min(r[2] for r in rows), column=0,
                         columnspan=max(1, body.grid_size()[0]),
                         sticky="w", padx=5, pady=(2, 4))

            traces = []
            for _label, var, _row, _widgets in rows:
                traces.append((var, var.trace_add(
                    "write", lambda *_a: self._refresh_import_summary())))

            own = btn is not getattr(self, "import_prev_btn", None)
            self._import_collapse = {
                "rows": rows, "saved": saved, "traces": traces,
                "outputs": {str(v) for v in outputs}, "own_button": own,
                "text": btn.cget("text"), "command": btn.cget("command"),
            }
            btn.configure(text=app_fonts.button_text("Change Inputs"),
                          command=self._expand_imported_inputs)
            # On the heading even if the import button was hidden for want of
            # a job to import: it is now the way back to the rows.
            self._seat_beside_heading(body, btn)
            self._refresh_import_summary()
        except Exception:
            logger.debug("Could not put the imported input rows away", exc_info=True)

    def _refresh_import_summary(self) -> None:
        state = getattr(self, "_import_collapse", None)
        summary = getattr(self, "_import_summary", None)
        if not state or summary is None:
            return
        try:
            import os

            parts, full = [], []
            for label, var, _row, _widgets in state["rows"]:
                value = var.get()
                note = ""
                if not (value and os.path.exists(value)):
                    note = "  (new)" if str(var) in state["outputs"] else "  (not found)"
                parts.append(f"{label}: {self._input_display_name(value)}{note}")
                full.append(f"{label}:\n  {value}{note}")
            # One line per field: a single long line would widen the page.
            summary.configure(text="\n".join(parts))
            self._import_summary_tip.text = "\n".join(full)
            btn = self._inputs_button()
            if btn is not None:
                btn._sc_tooltip_override = (
                    "Show the input fields again, to check them or pick different "
                    "folders by hand.\n\n" + "\n".join(full))
        except Exception:
            logger.debug("Could not refresh the import summary", exc_info=True)

    def _expand_imported_inputs(self, refresh_button: bool = True) -> None:
        """Bring the input rows back and turn the button back into Import."""
        state = getattr(self, "_import_collapse", None)
        if not state:
            return
        self._import_collapse = None
        try:
            summary = getattr(self, "_import_summary", None)
            if summary is not None:
                summary.grid_remove()
            for w, kwargs in state["saved"]:
                if kwargs:
                    w.grid(**kwargs)
                else:
                    w.grid()
            for var, trace in state["traces"]:
                try:
                    var.trace_remove("write", trace)
                except tk.TclError:
                    pass
            btn = self._inputs_button()
            if btn is not None:
                btn._sc_tooltip_override = None
                btn.configure(text=state["text"], command=state["command"])
                if state.get("own_button"):
                    btn.place_forget()
                elif refresh_button and hasattr(self, "_refresh_import_button"):
                    # Back to the import button's own rule: hidden when there
                    # is no finished job to import.
                    self._refresh_import_button()
        except Exception:
            logger.debug("Could not bring the input rows back", exc_info=True)

    # -- chapter picker ----------------------------------------------------
    #
    # A dropdown on the Input/Output heading that points the stage at one
    # chapter of the movie: Studio holds the project folder, core.ui.chapters
    # works out every path this stage reads and writes for that chapter, and
    # the rows are then put away exactly as an import does it. The chapter is
    # remembered per stage. Editing any of those paths by hand -- or using
    # the import button -- drops the dropdown back to "Manual paths", so it
    # never names a chapter the fields do not match.

    _CHAPTER_MANUAL = "Manual paths"
    #: Chooses (or changes) the movie's project folder -- what turns the
    #: chapter list on.
    _CHAPTER_CHOOSE = "Project Mode\u2026"
    _CHAPTER_NEW = "New Chapter\u2026"
    #: Scene Cut only: the whole movie, filed into chapter folders by its
    #: chapter markers. Stored as chapter number -1.
    _CHAPTER_WHOLE = "Whole Movie (all chapters)"

    def _install_chapter_picker(self, stage_key: str, body, fields: Dict[str, Any]) -> None:
        """Put the chapter dropdown on `body`'s heading.

        `fields` maps each path role core.ui.chapters.stage_paths knows for
        this stage to the variable it fills. A no-op outside Studio, which
        owns the project folder.
        """
        shell = getattr(self, "shell", None)
        if shell is None or not hasattr(shell, "get_project_folder"):
            return
        try:
            self._chapter_stage = stage_key
            self._chapter_fields = dict(fields)
            self._chapter_expected: Dict[str, str] = {}
            self._chapter_applying = False
            self._chapter_applied_once = False
            self._chapter_names: Dict[str, Tuple[int, str]] = {}
            if getattr(self, "_import_prev_body", None) is None:
                self._import_prev_body = body
            parent = self._heading_parent(body)
            self._chapter_menu = self._ctk_option(
                parent, values=[self._CHAPTER_MANUAL], width=250,
                dynamic_resizing=False, command=self._on_chapter_picked)
            from core.ui.widgets import Tooltip

            Tooltip(self._chapter_menu,
                    "Point this stage at one chapter of the movie. Studio finds the\n"
                    "chapter's folder in each stage folder of the project (Source,\n"
                    "DepthMaps, Splats, Inpainted, Merged/LR) and fills every path\n"
                    "here from them, inputs and output.\n\n"
                    "\"Manual paths\" leaves the fields as you set them. Editing a path\n"
                    "by hand switches back to it on its own.")
            if getattr(self, "import_prev_btn", None) is None:
                self._chapter_change_btn = self._ctk_button(
                    parent, text="Change Inputs", command=self._expand_imported_inputs)
            self._seat_beside_heading(body, self._chapter_menu)
            btn = getattr(self, "import_prev_btn", None)
            if btn is not None and btn.winfo_manager() == "place":
                self._seat_beside_heading(body, btn)
            for var in self._chapter_fields.values():
                var.trace_add("write", lambda *_a, v=var: self._chapter_field_changed(v))
            shell.register_chapter_stage(self)
            # Not now: a stage may still be loading its saved paths when this
            # runs, and those writes would read as the user editing a field.
            self._chapter_menu.winfo_toplevel().after(250, self._refresh_chapter_picker)
        except Exception:
            logger.exception("Could not add the chapter picker for %s", stage_key)

    def _chapter_saved(self):
        shell = getattr(self, "shell", None)
        return shell.get_stage_chapter(self._chapter_stage) if shell is not None else None

    def _chapter_display_for(self, saved) -> Optional[str]:
        if not saved:
            return None
        for display, (num, _name) in self._chapter_names.items():
            if num == saved.get("num"):
                return display
        return None

    def _refresh_chapter_picker(self) -> None:
        """Re-list the chapters and re-resolve this stage's paths.

        Run on every tab change as well: files move into finished/ folders
        as the stages run, and the paths should follow them.
        """
        menu = getattr(self, "_chapter_menu", None)
        if menu is None:
            return
        try:
            import os

            from core.ui import chapters

            project = self.shell.get_project_folder()
            has_project = bool(project) and os.path.isdir(project)
            saved = self._chapter_saved()
            values = [self._CHAPTER_MANUAL]
            self._chapter_names = {}
            if has_project:
                for num, name in chapters.list_chapters(project):
                    self._chapter_names[name] = (num, name)
                    values.append(name)
                # A New Chapter that is not on disk yet stays on the list.
                if saved and self._chapter_display_for(saved) is None and saved.get("name"):
                    self._chapter_names[saved["name"]] = (saved["num"], saved["name"])
                    values.append(saved["name"])
                if self._chapter_stage == "scenecut":
                    values.insert(1, self._CHAPTER_WHOLE)
                    self._chapter_names[self._CHAPTER_WHOLE] = (-1, self._CHAPTER_WHOLE)
                    values.append(self._CHAPTER_NEW)
            values.append(self._CHAPTER_CHOOSE)
            menu.configure(values=values)

            display = self._chapter_display_for(saved) if has_project else None
            if display is None:
                menu.set(self._CHAPTER_MANUAL)
                self._chapter_expected = {}
                return
            menu.set(display)
            num, name = self._chapter_names[display]
            first = not self._chapter_applied_once
            self._apply_chapter(num, name, collapse=first or self._import_inputs_collapsed())
        except Exception:
            logger.debug("Could not refresh the chapter picker", exc_info=True)

    def _apply_chapter(self, num: int, name: str, collapse: bool = True) -> None:
        from core.ui import chapters

        paths = chapters.stage_paths(self._chapter_stage, self.shell.get_project_folder(),
                                     num, name)
        fields = self._chapter_fields
        self._chapter_applying = True
        try:
            for role, var in fields.items():
                if role in paths and var.get() != paths[role]:
                    var.set(paths[role])
        finally:
            self._chapter_applying = False
        self._chapter_applied_once = True
        self._chapter_expected = {str(var): var.get() for role, var in fields.items()
                                  if role in paths}
        if collapse:
            self._collapse_imported_inputs(
                [var for role, var in fields.items() if role in paths],
                outputs=[fields["output"]] if "output" in fields and "output" in paths else ())
        else:
            self._refresh_import_summary()

    def _chapter_field_changed(self, var) -> None:
        """A path changed by something other than the chapter: go Manual."""
        if self._chapter_applying or not self._chapter_expected:
            return
        expected = self._chapter_expected.get(str(var))
        if expected is None or var.get() == expected:
            return
        self._chapter_expected = {}
        try:
            self._chapter_menu.set(self._CHAPTER_MANUAL)
            self.shell.set_stage_chapter(self._chapter_stage, None)
        except Exception:
            logger.debug("Could not drop the chapter to manual", exc_info=True)

    def _restore_chapter_display(self) -> None:
        self._chapter_menu.set(self._chapter_display_for(self._chapter_saved())
                               or self._CHAPTER_MANUAL)

    def _on_chapter_picked(self, value: str) -> None:
        shell = self.shell
        parent = self._chapter_menu.winfo_toplevel()
        try:
            if value == self._CHAPTER_CHOOSE:
                self._restore_chapter_display()
                from tkinter import filedialog

                path = filedialog.askdirectory(
                    parent=parent, title="Project Mode: choose the movie's project folder",
                    initialdir=shell.get_project_folder() or None)
                if path:
                    shell.set_project_folder(path)
                return
            if value == self._CHAPTER_NEW:
                self._restore_chapter_display()
                self._new_chapter(parent)
                return
            if value == self._CHAPTER_MANUAL:
                shell.set_stage_chapter(self._chapter_stage, None)
                self._chapter_expected = {}
                self._expand_imported_inputs()
                return
            num, name = self._chapter_names[value]
            shell.set_stage_chapter(self._chapter_stage, {"num": num, "name": name})
            self._apply_chapter(num, name, collapse=True)
            # A stage may follow the pick with settings of its own -- Scene Cut
            # turns its chapter-folder split on for the whole movie, off for one
            # chapter. Only on a real pick, never on a refresh.
            hook = getattr(self, "_on_chapter_chosen", None)
            if hook is not None:
                hook(num)
        except Exception:
            logger.exception("Could not switch chapter")

    def _new_chapter(self, parent) -> None:
        """Scene Cut: name a chapter that has no folders yet."""
        from tkinter import messagebox, simpledialog

        from core.ui import chapters

        name = simpledialog.askstring(
            "New Chapter",
            "Folder name for the new chapter, starting with its number:\n"
            "(for example  Chapter 38 - The Final Battle)",
            parent=parent)
        if not name or not name.strip():
            return
        name = name.strip()
        num = chapters.chapter_number(name)
        if num is None:
            messagebox.showerror(
                "New Chapter",
                "The name has to start with the chapter number, like\n"
                "\"Chapter 38 - The Final Battle\" or \"C38\".", parent=parent)
            return
        existing = next((n for n, (k, _v) in self._chapter_names.items() if k == num), None)
        self.shell.set_stage_chapter(self._chapter_stage,
                                     {"num": num, "name": existing or name})
        self._chapter_applied_once = False
        self._refresh_chapter_picker()

    def _ctk_init(self) -> None:
        """Start a fresh registry. Call before building any widget."""
        self._ctk: Dict[str, List[Tuple[Any, str]]] = {role: [] for role in _ROLES}

    # -- construction ----------------------------------------------------

    def _ctk_popup(self, win, size: Optional[Tuple[int, int]] = None,
                   minsize: Optional[Tuple[int, int]] = None,
                   screen_fraction: float = 0.85, glass: bool = True) -> None:
        """Finish a pop-up window built from this stage's _ctk_* widgets.

        Call once the window is fully built. It:

          * sizes it -- `size` and `minsize` are LOGICAL pixels, turned into
            real ones for the monitor (Sharp mode; unchanged otherwise), and
            capped at `screen_fraction` of the screen;
          * puts it on frosted glass when the stage is (and `glass` allows
            it), else on the page colour, so the widgets' rounded corners
            have the right ground;
          * paints just its own widgets in the current palette -- it is built
            after the stage's last theme pass, and repainting the whole stage
            to reach a dozen new widgets would be wasted work;
          * drops them from the palette registry when the window closes, or
            every later repaint would walk destroyed widgets.
        """
        from core.ui import dpi

        try:
            if size:
                w, h = dpi.px(win, size[0]), dpi.px(win, size[1])
                sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
                w, h = min(w, int(sw * screen_fraction)), min(h, int(sh * screen_fraction))
                win.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{dpi.px(win, 40)}")
            if minsize:
                win.minsize(dpi.px(win, minsize[0]), dpi.px(win, minsize[1]))
        except tk.TclError:
            logger.debug("Could not size the pop-up", exc_info=True)

        last = getattr(self, "_ctk_last_palette", None)
        if last is None:
            return
        colors, is_dark = last
        on_glass = glass and self._ctk_glass_active() and glass_dialog(win, colors, is_dark)
        self._ctk_paint_window(win, colors, is_dark, glass=bool(on_glass))
        # The OS title bar too: left alone, every pop-up opened with a white
        # one on a dark palette.
        try:
            from core.ui.theme_manager import apply_windows_titlebar_theme

            apply_windows_titlebar_theme(win, bool(is_dark))
        except Exception:
            logger.debug("Could not theme the pop-up title bar", exc_info=True)

        def _gone(event, _win=win):
            if event.widget is _win:
                self._ctk_forget([_win])

        win.bind("<Destroy>", _gone, add="+")

    def _ctk_paint_window(self, win, colors: Dict[str, Any], is_dark: bool,
                          glass: bool = False) -> None:
        """Paint just `win`'s registered widgets in `colors`.

        For a pop-up: _ctk_popup's first paint, and a window that repaints
        itself later -- widgets it adds after opening, or a palette of its
        own (the Grain Match Preview's Dim UI). The stage's palette, glass
        state and the app-wide CTk appearance mode are all left as they were:
        switching that mode redraws every CTk widget in the app, which one
        window's colours are no reason to do.
        """
        try:
            win.configure(bg=win_backdrop.CHROMA_KEY if glass else colors.get("bg", "#1e1e1e"))
        except tk.TclError:
            pass
        full = self._ctk
        last = getattr(self, "_ctk_last_palette", None)
        stage_glass = self._ctk_glass_active()
        prefix = str(win) + "."
        self._ctk = {role: [(w, on) for w, on in items if str(w).startswith(prefix)]
                     for role, items in full.items()}
        self._ctk_glass = bool(glass)
        self._ctk_subset_paint = True
        try:
            self._apply_ctk_palette(colors, is_dark)
        except Exception:
            logger.debug("Could not paint the pop-up", exc_info=True)
        finally:
            self._ctk = full
            self._ctk_glass = stage_glass
            self._ctk_subset_paint = False
            if last is not None:
                self._ctk_last_palette = last

    def _ctk_text_window(self, title: str, text: str, size: Tuple[int, int] = (600, 700),
                         button: str = "Close", wait: bool = False):
        """A read-only text window -- user guide, help -- in the app's style.

        Replaces three hand-built copies that each chose their own colours
        (two hard-coded #2b2b2b/white or #fdfdfd/black whatever the palette)
        and used ttk buttons. The text is a tk.Text for its wrapping and
        scrolling, in the palette's field colours; the rest is CTk, so it
        scales with the monitor, and _ctk_popup sizes it and puts it on glass.
        Modal, as all three were. `wait` blocks until it closes.
        """
        parent = self._ctk_window() or getattr(self, "root", None) or self
        colors = (getattr(self, "_ctk_last_palette", None) or ({}, True))[0]
        field = colors.get("entry_bg", "#2b2b2b")
        fg = colors.get("fg", "#e0e0e0")

        win = tk.Toplevel(parent)
        win.title(title)
        try:
            win.transient(parent)
        except tk.TclError:
            pass
        try:
            from core.ui.theme_manager import apply_windows_titlebar_theme

            apply_windows_titlebar_theme(win, bool(colors.get("is_dark", True)))
        except Exception:
            pass

        body = self._ctk_container(win)
        body.pack(expand=True, fill="both", padx=10, pady=(10, 4))
        text_widget = tk.Text(
            body, wrap=tk.WORD, relief="flat", borderwidth=0, padx=6, pady=6,
            font=("Segoe UI", 9), bg=field, fg=fg, insertbackground=fg,
            selectbackground=colors.get("active_bg", field),
            selectforeground=colors.get("active_fg", fg),
        )
        text_widget.insert(tk.END, text)
        text_widget.configure(state=tk.DISABLED)
        scrollbar = ttk.Scrollbar(body, orient=tk.VERTICAL, command=text_widget.yview)
        text_widget["yscrollcommand"] = scrollbar.set
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        text_widget.pack(side=tk.LEFT, expand=True, fill="both")

        row = self._ctk_container(win)
        row.pack(pady=(4, 10))
        close = self._ctk_button(row, text=button, command=win.destroy)
        close.pack()

        self._ctk_popup(win, size=size, minsize=(400, 200))
        try:
            win.grab_set()
            close.focus_set()
        except tk.TclError:
            pass
        if wait:
            win.wait_window()
        return win

    def _ctk_forget(self, widgets) -> None:
        """Drop `widgets`, and anything inside them, from the palette registry.

        Needed wherever a stage destroys and rebuilds part of its page --
        Scene Cut's detector parameters change shape with the detector.
        Without it the registry keeps a reference to every widget ever built,
        so each repaint walks a list that grows by a row per switch and spends
        its time configuring windows Tk has already destroyed.
        """
        registry = getattr(self, "_ctk", None)
        if not registry:
            return
        names = {str(w) for w in widgets}
        # Tk widget paths are dot-separated, so a child's path is its parent's
        # plus a suffix; that is what finds the contents of a container here.
        prefixes = tuple(f"{name}." for name in names)

        def stale(widget) -> bool:
            path = str(widget)
            return path in names or path.startswith(prefixes)

        for role, entries in registry.items():
            registry[role] = [e for e in entries if not stale(e[0])]


    def _section(self, parent, title: str, anchor: str = "w"):
        """A rounded frame with a header, standing in for ttk.LabelFrame.

        Returns ``(outer, body)``. Children grid into `body` at whatever row
        and column they used before, so only the container changes.

        `anchor` is ttk.LabelFrame's `labelanchor` in the cases that matter
        here: "w" for a normal heading, "center" for the narrow stacked boxes
        that centre theirs (Splatting's Special FX group).
        """
        outer = ctk.CTkFrame(parent, corner_radius=CTK_RADIUS)
        # height=0 so the header is as tall as its text. A CTkLabel reserves
        # 28 where the text needs about 16, and a section pays that on every
        # card: a ttk.LabelFrame drew its title INTO the border and cost
        # nothing, so six sections of default-height headers were most of why
        # a converted page came out taller than the ttk one.
        heading_font = self._app_font("heading", app_fonts.HEADING_SIZE)
        header = ctk.CTkLabel(
            outer,
            text=app_fonts.heading_text(title) if heading_font else title,
            anchor=anchor, height=0,
            font=heading_font or ctk.CTkFont(size=15, weight="bold"),
        )
        header.pack(fill="x", padx=10, pady=(7, 0))
        body = ctk.CTkFrame(outer, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=5, pady=(3, 7))
        # A ttk.LabelFrame was one widget that was the box, the title AND the
        # thing children were parented to. A section is three, so anything
        # holding one of them has to be able to find the other two:
        #
        #   * beginner mode hides whole sections -- it must hide `outer`, or
        #     the card stays on screen as an empty rounded header;
        #   * it also renames one ("Depth Map Pre-processing" -> "Edge
        #     Alignment"), which means configuring `header`, not the box.
        #
        # Stages hold the body (that is what they parent children to), so the
        # body is what carries the links.
        header._sc_section = outer
        body._sc_section_outer = outer
        body._sc_section_header = header
        self._ctk["frames"].append((outer, None))
        self._ctk["headers"].append((header, None))
        return outer, body

    def _ctk_container(self, parent, on: str = None, **kw):
        """A frame that paints nothing but still needs its bg_color kept up.

        width/height default to 0 because CTkFrame does not size to its content
        the way ttk.Frame does: an empty CTkFrame is 200x200, an empty
        ttk.Frame is 1x1. Rows built around a container whose controls are
        hidden until some checkbox is ticked therefore reserved a 200px block
        in both directions -- which is where the dead gaps between controls
        came from after the conversion.
        """
        kw.setdefault("width", 0)
        kw.setdefault("height", 0)
        w = ctk.CTkFrame(parent, fg_color="transparent", **kw)
        self._ctk["containers"].append((w, on))
        return w

    def _ctk_plate(self, parent, on: str = None, **kw):
        """A container that turns SOLID on frosted glass, in the card colour.

        For a strip of controls that lives on the page rather than in a
        section -- the preview's frame slider -- and is hard to read with the
        desktop showing through behind it. Off glass it paints the page
        colour, so it looks exactly like a plain container. Widgets inside it
        count as on a card while glass is on, so their rounded corners are
        composited against the plate rather than punched through it.
        """
        kw.setdefault("width", 0)
        kw.setdefault("height", 0)
        # A smaller radius than a card: a plate is one control tall, and at
        # the card radius it would be a pill that the controls poke out of.
        w = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=6, **kw)
        self._ctk["plates"].append((w, on))
        return w

    def _ctk_label(self, parent, on: str = None, muted: bool = False, **kw):
        """height defaults to 0 so the label is as tall as its text.

        `muted` paints it a step toward the background, for hints and status
        lines that should not compete with the settings they sit beside.

        CTkLabel reserves 28px by default where a ttk.Label asks for 19 and the
        text itself needs 15. Nine dead pixels a row is invisible on one label
        and obvious down a column of them, which is what spread the Current
        Clip Info rows apart after the conversion. Callers that want a fixed
        height still pass one.
        """
        kw.setdefault("height", 0)
        font = self._app_font("body", 13)
        if font is not None:
            kw.setdefault("font", font)
        w = ctk.CTkLabel(parent, **kw)
        w._sc_muted = muted
        self._ctk["labels"].append((w, on))
        return w

    def _ctk_entry(self, parent, on: str = None, **kw):
        """width/height default to the ttk.Entry these replaced, not to CTk's.

        CTkEntry defaults to 140x28 where the ``ttk.Entry(width=10)`` it
        stands in for asks for about 75x21. Taken at face value that one
        default is most of why a converted page came out ~40% wider than the
        ttk one it replaced -- on a stage with a dozen fields it is hundreds
        of pixels of nothing. An entry has no natural size to grow to, so the
        number here is the whole story and callers still pass their own.
        """
        kw.setdefault("width", 75)
        kw.setdefault("height", 24)
        w = ctk.CTkEntry(parent, corner_radius=CTK_RADIUS, **kw)
        self._ctk["entries"].append((w, on))
        return w

    def _ctk_button(self, parent, primary: bool = False, width: int = 70,
                    on: str = None, **kw):
        """`width` is a FLOOR, not a size: CTkButton grows to fit its label.

        Measured: at width=70 a "Browse..." button asks for 113px and a
        "Restore Finished Files" one asks for 216px -- nothing is clipped or
        compressed. So the default is a minimum for short labels, the way
        ttk.Button's padding is, rather than a width every button is padded
        out to. It used to be 110, which made every button on a page at least
        half again as wide as its text needed.

        height is likewise a default, not fixed: a dense stage needs to trade
        button height for rows, and passing one used to raise "multiple values
        for keyword argument".
        """
        kw.setdefault("height", 24)
        # Cal Sans has one weight, and a heavy one: it is not asked for bold,
        # which would only get a synthesised thickening of it.
        font = self._app_font("button", app_fonts.BUTTON_SIZE)
        if font is not None:
            kw.setdefault("font", font)
            # Caps at the point of use, not in the source: the strings stay
            # readable where they are written and searchable when something
            # goes wrong. Only a plain string is touched -- a textvariable
            # belongs to the caller and is left alone.
            if isinstance(kw.get("text"), str):
                kw["text"] = app_fonts.button_text(kw["text"])
        cls = CtkAccentButton if primary else ctk.CTkButton
        w = cls(parent, corner_radius=CTK_RADIUS, width=width, **kw)
        self._ctk["accent_buttons" if primary else "buttons"].append((w, on))
        return w

    def _ctk_check(self, parent, on: str = None, **kw):
        font = self._app_font("body", 13)
        if font is not None:
            kw.setdefault("font", font)
        w = ctk.CTkCheckBox(parent, corner_radius=4, checkbox_width=18,
                            checkbox_height=18, onvalue=True, offvalue=False, **kw)
        self._ctk["checks"].append((w, on))
        return w

    def _ctk_spin(self, parent, on: str = None, **kw):
        """A CtkSpin whose parts are each registered in their own role."""
        w = CtkSpin(parent, **kw)
        self._ctk["containers"].append((w, on))
        if getattr(w, "arrow_column", None) is not None:
            self._ctk["containers"].append((w.arrow_column, on))
        self._ctk["entries"].append((w.entry, on))
        for button in w.buttons:
            self._ctk["buttons"].append((button, on))
        # Compact arrows are labels, so they take the foreground colour and
        # read as the glyphs ttk drew rather than as two tiny buttons.
        for arrow in getattr(w, "arrows", ()):
            self._ctk["labels"].append((arrow, on))
        return w

    def _ctk_option(self, parent, on: str = None, **kw):
        """Sized to its widest value unless the caller says otherwise.

        See :func:`option_width_for` for why it is the widest value and not
        the current one.
        """
        kw.setdefault("height", 24)
        if "width" not in kw and kw.get("values"):
            width = option_width_for(parent, kw["values"])
            if width:
                kw["width"] = width
        w = CtkArrowOptionMenu(parent, corner_radius=CTK_RADIUS, **kw)
        self._ctk["options"].append((w, on))
        return w

    def _ctk_slider(self, parent, on: str = None, progress: bool = True, **kw):
        """A slider in the app's shape. `number_of_steps` makes it discrete.

        Two things to know before using it in place of a ttk.Scale:

          * **set() snaps as well as dragging.** With `number_of_steps` set,
            set(7) on a 0..100 ten-step slider lands on 10, so a value
            restored from a config can move on its way in. Drive it from a
            notch index, as core.ui.widgets does, if that matters.
          * **Bind with add="+", and bind the widget only.** CTkSlider.bind
            forwards to its internal canvas, so binding the frame and its
            children lands two callbacks on the same window and fires the
            handler twice per event.
        """
        kw.setdefault("height", CTK_SLIDER_HEIGHT)
        kw.setdefault("button_length", CTK_SLIDER_KNOB_LENGTH)
        kw.setdefault("button_corner_radius", CTK_SLIDER_KNOB_RADIUS)
        kw.setdefault("corner_radius", CTK_SLIDER_HEIGHT // 2)
        # `progress=False` leaves the track one colour and lets the knob alone
        # carry the value. Useful where a row of sliders is read as positions
        # rather than as amounts -- a filled bar implies "more is more", which
        # is wrong for a setting like a bias that is centred on zero.
        # Recorded on the widget, not applied here, because the palette owns
        # every colour and would overwrite anything set at construction.
        w = CtkSafeSlider(parent, **kw)
        w._sc_no_progress = not progress
        self._ctk["sliders"].append((w, on))
        return w

    def _ctk_radio(self, parent, on: str = None, **kw):
        """One option of a genuine radio group.

        Not the same thing as :meth:`_ctk_segmented`. A segmented button is
        one control with two or three halves, for a short fixed set shown side
        by side -- the encoding presets. This is for a list whose length is
        not known until runtime and which reads as a column of choices:
        Splatting's depth-map picker, which has one row per subfolder found.
        """
        kw.setdefault("radiobutton_width", 18)
        kw.setdefault("radiobutton_height", 18)
        kw.setdefault("border_width_unchecked", 2)
        kw.setdefault("border_width_checked", 5)
        w = ctk.CTkRadioButton(parent, **kw)
        self._ctk["radios"].append((w, on))
        return w

    def _ctk_segmented(self, parent, on: str = None, **kw):
        """A segmented toggle, for the ttk "Toolbutton" radio strips.

        The encoding-preset strip reads as one control with two halves rather
        than as two radio buttons, which is what the Toolbutton style was
        already doing. CTkSegmentedButton is that control, and it takes
        `values` plus a `variable` instead of one widget per option.
        """
        kw.setdefault("corner_radius", CTK_RADIUS)
        kw.setdefault("height", 24)
        kw.setdefault("border_width", CTK_HAIRLINE)
        w = ctk.CTkSegmentedButton(parent, **kw)
        # The selected half is filled with the accent, so only its label needs
        # the contrasting colour -- and which half that is changes on every
        # click, not only when the palette does. The painter leaves the two
        # colours on the widget and this trace re-applies them in between; a
        # variable value that is in no segment (the encoding row's "custom"
        # state) simply leaves them all unselected.
        variable = kw.get("variable")
        if variable is not None:
            variable.trace_add("write", lambda *_a, _w=w: refresh_segmented(_w))
        self._ctk["segmented"].append((w, on))
        return w

    def _ctk_progress(self, parent, on: str = None, factory=None, **kw):
        """`factory` lets a stage supply its own CTkProgressBar subclass."""
        cls = factory or ctk.CTkProgressBar
        w = cls(parent, corner_radius=CTK_RADIUS, height=14, **kw)
        w.set(0.0)
        self._ctk["progress"].append((w, on))
        return w

    # -- painting --------------------------------------------------------

    def _resolve_surface(self, widget, section_ids) -> str:
        """Which surface `widget` is drawn on: "panel" inside a section, else "page".

        Derived from the widget's place in the tree rather than declared at
        every call site. A widget carries bg_color to composite its rounded
        corners against, and getting it wrong leaves a plate of the wrong
        colour behind the control -- which is what a "panel" default did to
        every button and label that lives outside a section.
        """
        node = widget
        for _ in range(12):
            node = getattr(node, "master", None)
            if node is None:
                return "page"
            if str(node) in section_ids:
                return "panel"
        return "page"



    # -- frosted glass ----------------------------------------------------

    def _ctk_window(self):
        """The toplevel this stage's glass belongs to, or None.

        Most stages ARE a widget and answer winfo_toplevel(). DepthCrafter is
        not: it is a plain class that packs into a container and keeps the
        window as `self.root`.
        """
        getter = getattr(self, "winfo_toplevel", None)
        if callable(getter):
            try:
                return getter()
            except Exception:
                pass
        return getattr(self, "root", None)

    def _ctk_glass_active(self) -> bool:
        """Whether this stage is currently painting its surfaces as glass."""
        return bool(getattr(self, "_ctk_glass", False))

    def _ctk_set_glass(self, colors: Dict[str, Any], is_dark: bool) -> bool:
        """Put this stage's window on (or off) the acrylic backdrop.

        Returns whether glass is on afterwards. Call this BEFORE
        :meth:`_apply_ctk_palette`, because it decides which surfaces that
        paints: a dark palette keys the sections too, a light one keys the
        page and leaves the cards solid. Nothing is keyed unless Windows
        actually took the backdrop -- punching holes in a window with no blur
        behind it would leave the page hanging over the bare desktop.
        """
        window = self._ctk_window()
        want = bool(window is not None and GLASS_ENABLED
                    and win_backdrop.is_enabled()
                    and win_backdrop.is_supported())

        def give_up() -> bool:
            """Off, and back to a window the palette would recognise."""
            was_on = bool(getattr(self, "_ctk_glass", False))
            self._ctk_glass = False
            self._ctk_glass_ground(False)
            if window is None:
                return False
            win_backdrop.clear(window)
            if was_on:
                # The key went onto the window's own background when glass
                # went on, and clearing the backdrop does not take it off
                # again -- that would leave a near-black window behind a
                # palette that asked for nothing of the sort.
                try:
                    window.configure(bg=colors.get("bg", "#1e1e1e"))
                except Exception:
                    logger.debug("Could not restore the window background",
                                 exc_info=True)
            return False

        if not want:
            return give_up()

        tint = colors.get("bg", "#1e1e1e")
        if not win_backdrop.apply(window, "acrylic", tint=tint, alpha=GLASS_TINT_ALPHA):
            return give_up()
        if not win_backdrop.set_chroma_key(window, True):
            return give_up()

        self._ctk_glass = True
        self._ctk_glass_ground(True)
        #: Whether the SECTION fill is keyed as well as the page -- see the
        #: note above GLASS_ENABLED. Off by default on every palette now,
        #: because a keyed card is a click-through card; a palette can opt
        #: back in and accept that.
        self._ctk_glass_panels = bool(colors.get("ctk_glass_panels", False))
        # The window's own background shows between the widgets, so it has to
        # be punched out too or the page sits on a solid rectangle.
        try:
            window.configure(bg=win_backdrop.CHROMA_KEY)
        except Exception:
            logger.debug("Could not key the window background", exc_info=True)
        return True

    def _ctk_glass_ground(self, glass: bool) -> None:
        """Key the ttk frame the cards are packed into, or give it back.

        The cards are CustomTkinter and take the key from
        :meth:`_apply_ctk_palette`, but the frame BEHIND them is a ttk widget
        that only ttk.Style reaches, and it is what shows in the padding
        between one card and the next. Left alone it drew the page's own
        colour there: solid bands across a page that was otherwise glass, on
        every stage whose cards are packed straight into the stage frame
        rather than into a CTk container that covers it.

        The original style name is remembered per widget, so turning glass off
        puts the palette's ground back rather than leaving it keyed.
        """
        grounds = [w for w in (self, getattr(self, "container", None))
                   if isinstance(w, ttk.Frame)]
        if not grounds:
            return
        try:
            style = ttk.Style(grounds[0])
            if glass:
                # Re-configured on every pass, not once: set_theme() resets
                # every style, and a theme change is exactly when this runs.
                style.configure(GLASS_GROUND_STYLE, background=win_backdrop.CHROMA_KEY)
        except Exception:
            logger.debug("Could not configure the glass ground style", exc_info=True)
            return
        for w in grounds:
            try:
                if glass:
                    if not hasattr(w, "_sc_ground_style"):
                        w._sc_ground_style = str(w.cget("style") or "")
                    w.configure(style=GLASS_GROUND_STYLE)
                elif hasattr(w, "_sc_ground_style"):
                    w.configure(style=w._sc_ground_style)
                    del w._sc_ground_style
            except Exception:
                logger.debug("Could not restyle the stage ground", exc_info=True)

    def _outlines_enabled(self) -> bool:
        """Whether View > Show Section Outlines is on.

        The flag lives on the ThemeManager because that is what configures
        TLabelframe for the ttk stages. In the shell the authoritative one
        belongs to the theme bus; standalone the stage has its own.
        """
        bus = getattr(getattr(self, "shell", None), "theme_bus", None)
        manager = getattr(bus, "manager", None) or getattr(self, "theme_manager", None)
        return bool(getattr(manager, "show_section_outlines", True))

    def _apply_ctk_palette(self, colors: Dict[str, Any], is_dark: bool) -> None:
        """Repaint every registered CustomTkinter widget for `colors`.

        Tells _install_fast_repaint's patches which widgets this pass is about
        to paint, for the length of it, so CustomTkinter does not redraw them
        on its own first.
        """
        global _PAINTING
        registry = getattr(self, "_ctk", None)
        if not registry:
            return
        widgets = [w for items in registry.values() for w, _on in items]
        _PAINTED.update(widgets)
        previous = _PAINTING
        _PAINTING = {str(w) for w in widgets}
        try:
            self._paint_ctk_palette(registry, colors, is_dark)
        finally:
            _PAINTING = previous

    def _paint_ctk_palette(self, registry, colors: Dict[str, Any], is_dark: bool) -> None:
        """The painter itself; see _apply_ctk_palette."""
        global _PAINTER_SWITCHING_MODE
        # For a pop-up built later, which has to be painted to match without
        # waiting for the next theme pass -- see _ctk_popup.
        self._ctk_last_palette = (colors, is_dark)

        bg = colors.get("bg", "#1e1e1e")
        fg = colors.get("fg", "#e0e0e0")
        if fg in ("white", "black"):
            fg = _NAMED[fg]
        # The section fill is lifted off the page background rather than taken
        # from panel_bg: several palettes give panel_bg a hue of its own
        # (Graphite's #3a4047 reads blue against its own neutral window), and a
        # rounded card covering most of a page is a far larger surface than the
        # progress panels that colour was picked for.
        # "ctk_panel" lets a palette name its own section fill. True Black
        # wants the boxes to BE black and be told apart by their outline alone;
        # a derived lift gives it a grey card, which is the one thing that
        # palette exists to avoid.
        panel = colors.get("ctk_panel") or shade(bg, 0.09 if is_dark else -0.05)
        entry_bg = colors.get("entry_bg", bg)
        accent = colors.get("tab_accent") or colors.get("panel_bar", fg)
        border = colors.get("section_border") or colors.get("slider_trough", panel)
        # Hover moves AWAY from the surface, which is opposite directions on a
        # dark and a light palette.
        lift = 0.14 if is_dark else -0.10
        surfaces = {"page": bg, "panel": panel}
        # On glass a surface is PAINTED with the key but still DERIVED from
        # the palette: `surface_of` keeps answering with the real colour so a
        # button fill stays a lifted grey, while `bg_of` answers with the key
        # so the pixels are punched out. A light palette keys the page only,
        # so its sections keep the solid card they were always drawn on.
        glass = self._ctk_glass_active()
        glass_panels = glass and bool(getattr(self, "_ctk_glass_panels", True))
        # Text drawn straight onto the key keeps the fringe it was antialiased
        # with: white on a dark tint carries it, black on a light tint comes
        # out rough and doubled. A light palette therefore gives the widgets
        # that draw TEXT on the page -- the preview toolbar, mostly, since
        # everything else lives on a card that is already solid -- a plate of
        # the page's own colour to sit on, and the glass keeps the gutters
        # around them. Controls with a fill of their own (buttons, entries,
        # dropdowns) need nothing: only their rounded corners see the key.
        #
        # Keyed off is_dark, NOT off glass_panels. The two used to coincide,
        # and when the cards stopped being keyed on dark palettes a derived
        # flag would have started plating dark text as well -- solid patches
        # behind labels that were reading perfectly well on the glass.
        plate_text = glass and not is_dark
        paint = {
            "page": win_backdrop.CHROMA_KEY if glass else surfaces["page"],
            "panel": win_backdrop.CHROMA_KEY if glass_panels else surfaces["panel"],
        }
        on_accent = readable_on(accent)
        # Section outlines use the colour the ttk pages give TLabelframe, and
        # obey the same View > Show Section Outlines switch.
        # "ctk_outline" darkens the section edges for a palette whose accent is
        # chosen as a fill and is too light to draw a line with.
        outline = (colors.get("ctk_outline") or colors.get("section_border")
                   or colors.get("panel_bar", fg))
        outlines_on = self._outlines_enabled()
        # Section titles are text, so they read as text. The accent earns its
        # place on the edges and the primary action instead of colouring every
        # heading -- which also means a palette whose accent is too light to
        # read (Paper's is 1.92:1 on its own card) has nothing to go wrong.
        # "ctk_heading" is still honoured for a palette that wants otherwise.
        heading = colors.get("ctk_heading") or fg
        # Dropdowns take the accent on their arrow block by default; a palette
        # can ask for something quieter without giving up its accent elsewhere.
        control = colors.get("ctk_control") or accent
        # Sliders, same escape hatch. The accent marks the one primary action
        # on a screen, and a panel of eight sliders is not eight primary
        # actions -- on a palette whose accent is a strong colour, a column of
        # them reads as an alarm. "ctk_slider" lets such a palette put its own
        # colour on the knob and the filled part of the track while keeping
        # the accent on buttons and checkboxes. Every palette already names a
        # muted `slider_handle` for the ttk pages; this is deliberately NOT
        # read from that, so converting a stage cannot quietly restyle a
        # palette nobody asked to change.
        slider_color = colors.get("ctk_slider") or accent

        if not getattr(self, "_ctk_subset_paint", False):
            # Every widget any painter owns skips its own redraw for the new
            # mode: this pass, and the other stages' in the same theme change,
            # recolour them straight after. See _install_fast_repaint.
            _PAINTER_SWITCHING_MODE = True
            try:
                ctk.set_appearance_mode("dark" if is_dark else "light")
            except Exception:
                logger.debug("Could not set the CustomTkinter appearance mode", exc_info=True)
            finally:
                _PAINTER_SWITCHING_MODE = False

        # A section's own frame is the boundary between the two surfaces.
        section_ids = {str(f) for f, _ in registry["frames"]}
        # A plate is a card only while it is solid, which is only on glass.
        plates = registry.get("plates", [])
        if glass:
            section_ids |= {str(f) for f, _ in plates}

        def surface_of(widget, declared):
            """The colour behind `widget`, for deriving fills and hovers."""
            return surfaces[declared or self._resolve_surface(widget, section_ids)]

        def bg_of(widget, declared):
            """What to PAINT behind it -- the key when this stage is glass."""
            return paint[declared or self._resolve_surface(widget, section_ids)]

        def text_bg_of(widget, declared):
            """Behind a run of TEXT: solid wherever the glass would fringe it."""
            name = declared or self._resolve_surface(widget, section_ids)
            return surfaces[name] if plate_text else paint[name]

        def apply(widget, **kw):
            try:
                widget.configure(**kw)
            except Exception:
                logger.debug("CTk repaint failed for %r", widget, exc_info=True)

        for w, on in registry["containers"]:
            apply(w, bg_color=bg_of(w, on))
        for w, on in plates:
            apply(w, fg_color=paint["panel"] if glass else bg_of(w, on),
                  bg_color=bg_of(w, on))
        for w, on in registry["frames"]:
            apply(w, fg_color=paint["panel"], bg_color=bg_of(w, on),
                  border_width=CTK_HAIRLINE if outlines_on else 0,
                  border_color=outline)
        for w, on in registry["headers"]:
            apply(w, text_color=heading, bg_color=text_bg_of(w, on))
        # A muted label (a hint, a status line) sits between the text and
        # the page, the way ttk pages give theirs a fixed grey.
        muted = shade(fg, -0.4 if is_dark else 0.4)
        for w, on in registry["labels"]:
            apply(w, text_color=muted if getattr(w, "_sc_muted", False) else fg,
                  bg_color=text_bg_of(w, on))
        for w, on in registry["entries"]:
            apply(w, fg_color=entry_bg, bg_color=bg_of(w, on), text_color=fg,
                  border_color=border,
                  placeholder_text_color=shade(fg, -0.4 if is_dark else 0.4))
            # Drag-and-drop highlights CTk entries by border colour, since they
            # have no style to swap -- see core.ui.dnd_support.
            w._sc_drop_colors = (border, accent)
        def button_colors(w, on):
            """A regular button's look: (fill, hover, text)."""
            fill = shade(surface_of(w, on), lift)
            hover = shade(surface_of(w, on), lift * 1.9)
            text = fg
            if not is_dark and CTK_LIGHT_BUTTON_TEXT:
                # White labels on a light palette need a fill dark enough to
                # carry them; hover goes a step darker again.
                text = CTK_LIGHT_BUTTON_TEXT
                fill = darken_for_white(fill, CTK_LIGHT_BUTTON_CONTRAST)
                hover = shade(fill, -0.12)
            return fill, hover, text

        for w, on in registry["buttons"]:
            # A lifted surface with an accent edge: it ties the buttons to the
            # section outlines and gives a flat grey button a defined shape.
            # They share "outline" with the sections, so a palette that quiets
            # its edges -- True Black's grey -- quiets these too.
            fill, hover, text = button_colors(w, on)
            apply(w, fg_color=fill, bg_color=bg_of(w, on), hover_color=hover,
                  text_color=text, border_width=CTK_HAIRLINE, border_color=outline)
        # White on the accent, by choice rather than by contrast rule:
        # readable_on picks black for the lighter accents, and the primary
        # action reads better in white. Except where white cannot be read at
        # all -- Graphite's accent IS white, which left a blank white button
        # -- and there it is dark grey: the page's own, on a dark palette.
        accent_text = ("#ffffff" if _contrast_with_white(accent) >= ACCENT_WHITE_MIN_CONTRAST
                       else (bg if is_dark else "#2b2b2b"))
        for w, on in registry["accent_buttons"]:
            fill, hover, _text = button_colors(w, on)
            apply(w, bg_color=bg_of(w, on))
            # Enabled it is the accent; disabled -- Start while a job runs --
            # it is an ordinary disabled button, like Cancel beside it, rather
            # than an accent block that only its paler text says is off.
            # CtkAccentButton swaps between the two as its state changes.
            colors = {
                "normal": dict(fg_color=accent, border_width=0, text_color=accent_text,
                               hover_color=shade(accent, -0.15 if on_accent == "#000000" else 0.18)),
                "disabled": dict(fg_color=fill, hover_color=hover,
                                 border_width=CTK_HAIRLINE, border_color=outline),
            }
            w._sc_state_colors = colors
            if isinstance(w, CtkAccentButton):
                w.sc_apply_state_colors()
            else:
                apply(w, **colors["normal"])
        for w, on in registry["checks"]:
            # bg_color, not fg_color: on a check the fill IS the box, and the
            # label beside it is drawn on the background.
            apply(w, fg_color=accent, bg_color=text_bg_of(w, on),
                  hover_color=shade(accent, 0.18), text_color=fg,
                  border_color=border, checkmark_color=on_accent)
        for w, on in registry["options"]:
            apply(w, fg_color=entry_bg, bg_color=bg_of(w, on), text_color=fg,
                  button_color=control, button_hover_color=shade(control, 0.18),
                  text_color_disabled=shade(fg, -0.4 if is_dark else 0.4),
                  dropdown_fg_color=colors.get("menu_bg", panel),
                  dropdown_text_color=colors.get("menu_fg", fg),
                  dropdown_hover_color=colors.get("active_bg", panel))
        for w, on in registry["sliders"]:
            # The trough takes the colour the ttk pages give a Scale, so a
            # converted slider sits at the same weight as one that has not
            # been converted yet -- Splatting still draws ttk ones.
            trough = colors.get("slider_trough", border)
            fill = colors.get("ctk_slider") or colors.get("panel_bar", accent)
            apply(w, fg_color=trough,
                  bg_color=bg_of(w, on),
                  # Filling with the trough colour is what "no progress fill"
                  # means: CTkSlider always draws the left-hand segment, so it
                  # is hidden by matching it to the track rather than removed.
                  progress_color=trough if getattr(w, "_sc_no_progress", False) else fill,
                  button_color=slider_color,
                  button_hover_color=shade(
                      slider_color,
                      -0.18 if readable_on(slider_color) == "#000000" else 0.18,
                  ))
        for w, on in registry["radios"]:
            apply(w, fg_color=accent, bg_color=text_bg_of(w, on), text_color=fg,
                  border_color=border, hover_color=shade(accent, 0.18))
        for w, on in registry["segmented"]:
            apply(w, fg_color=entry_bg, bg_color=bg_of(w, on),
                  selected_color=accent, selected_hover_color=shade(accent, -0.15),
                  unselected_color=entry_bg, unselected_hover_color=shade(entry_bg, lift),
                  text_color=fg, text_color_disabled=shade(fg, -0.4 if is_dark else 0.4),
                  border_width=CTK_HAIRLINE)
            w._sc_seg_colors = (on_accent, fg)
            refresh_segmented(w)
        for w, on in registry["progress"]:
            apply(w, fg_color=colors.get("panel_trough", border),
                  bg_color=bg_of(w, on),
                  progress_color=colors.get("panel_bar", accent))

        # Rounded controls standing straight on the glass, on a LIGHT
        # palette: each gets a plate of the page colour just behind it. See
        # _sync_glass_plate for why. Everything else loses any plate it had.
        for role in ("buttons", "accent_buttons", "options", "entries"):
            for w, on in registry[role]:
                wants = (plate_text and not getattr(w, "_sc_no_glass_plate", False)
                         and bg_of(w, on) == win_backdrop.CHROMA_KEY)
                _sync_glass_plate(w, surfaces["page"] if wants else None)
