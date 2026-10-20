"""Reusable tkinter widgets and UI helper functions for StereoCrafter."""

import logging
import tkinter as tk
from tkinter import Toplevel, Label, ttk
from typing import Optional, Tuple, Callable

logger = logging.getLogger(__name__)

DEFAULT_TICK_RELY = 0.6
DEFAULT_TICK_RELHEIGHT = 0.6
DEFAULT_TICK_WIDTH = 2
DEFAULT_TICK_COLOR = "#6b7280"
DEFAULT_TICK_X_OFFSET_PX = 5

# Optional hook that swaps a tooltip's text at the moment it is shown. Studio's
# beginner mode installs one to show plain-language help; standalone GUIs never
# set it, so their tooltips behave exactly as before. Resolved at hover time
# rather than at creation, so switching modes takes effect on the next hover
# with no widget rebuilt.
_tooltip_text_resolver: Optional[Callable[[str], str]] = None


def set_tooltip_text_resolver(resolver: Optional[Callable[[str], str]]) -> None:
    """Install (or clear, with None) the hover-time tooltip text hook."""
    global _tooltip_text_resolver
    _tooltip_text_resolver = resolver


def _is_ctk_stage(GUI_self) -> bool:
    """True when this host builds CustomTkinter and has a live palette registry.

    The slider helper below is shared by Merging, which is converted, and
    Splatting, which is not. Flipping it wholesale would put CTk sliders on a
    ttk page, where nothing repaints them -- a CTk widget is reached only
    through its stage's registry, and a ttk stage has none. So the toolkit is
    chosen per host, and Splatting keeps the ttk path untouched until its turn.
    """
    try:
        from core.ui.ctk_theme import CtkThemedStage

        return isinstance(GUI_self, CtkThemedStage) and bool(getattr(GUI_self, "_ctk", None))
    except Exception:
        return False


def _set_text(widget, text: str) -> None:
    """Set a label's text, whichever toolkit drew it.

    CustomTkinter raises on .config() by design, to stop exactly the kind of
    half-ported call site this function exists to avoid.
    """
    try:
        widget.configure(text=text)
    except Exception:
        widget.config(text=text)


def resolve_tooltip_text(text: str) -> str:
    """The text a tooltip should show right now. Never raises."""
    if not text or _tooltip_text_resolver is None:
        return text
    try:
        return _tooltip_text_resolver(text) or text
    except Exception:
        return text


# The palette in force right now, pushed here by ThemeManager on every apply.
#
# Most of the UI is ttk and gets its colours from styles. The pieces that are
# not -- a tooltip, a help pane -- are plain tk widgets in Toplevels of their
# own, built long before (and after) any theme change and holding no reference
# to a theme manager. They read this at display time instead, so a tooltip
# created at startup still comes up in whatever palette is current when it is
# finally hovered.
_active_palette: dict = {}


def set_active_palette(colors: Optional[dict]) -> None:
    """Record the palette now in force. Called by ThemeManager; never raises."""
    global _active_palette
    _active_palette = dict(colors) if colors else {}


def get_active_palette() -> dict:
    """A copy of the palette in force, or {} before any theme has been applied."""
    return dict(_active_palette)


def tooltip_colors() -> Tuple[str, str, str]:
    """(background, foreground, border) for a tooltip, in the current palette.

    Falls back to Tk's classic pale yellow, which is what every tooltip in this
    app used to be hardcoded to -- so a GUI that never applies a palette looks
    exactly as it did.
    """
    p = _active_palette
    bg = p.get("tooltip_bg") or "#ffffe0"
    fg = p.get("tooltip_fg") or p.get("fg") or "#000000"
    # The border is the one part a palette doesn't name. A mid-tone between the
    # two reads as an edge on both light and dark tooltips.
    border = p.get("section_border") or _blend_hex(fg, bg, 0.55) or "#808080"
    return bg, fg, border


def _blend_hex(a: str, b: str, amount: float) -> Optional[str]:
    """`amount` of the way from `a` to `b`, or None if either isn't a hex colour."""
    try:
        pa, pb = a.lstrip("#"), b.lstrip("#")
        if len(pa) != 6 or len(pb) != 6:
            return None
        out = []
        for i in (0, 2, 4):
            ca, cb = int(pa[i:i + 2], 16), int(pb[i:i + 2], 16)
            out.append(max(0, min(255, round(ca + (cb - ca) * amount))))
        return "#%02x%02x%02x" % tuple(out)
    except Exception:
        return None


def place_popup_on_screen(popup, anchor, x: int, y: int, gap: int = 20) -> None:
    """Move an override-redirect popup to (x, y), kept inside the screen.

    (x, y) is where it goes by preference: below `anchor`. Near the bottom of
    a maximised window that runs under the taskbar or off the monitor, so
    the popup flips to ABOVE the anchor instead, and it is pulled left if it
    would cross the monitor's right edge. Measured against the work area of
    the monitor the anchor is on, so it holds on either display.
    """
    try:
        popup.withdraw()            # measured unmapped, so it never flashes at the old spot
        popup.update_idletasks()
        w, h = popup.winfo_reqwidth(), popup.winfo_reqheight()
        from core.ui import win_backdrop

        area = win_backdrop.work_area_at(anchor.winfo_rootx(), anchor.winfo_rooty())
        if area is None:
            area = (0, 0, popup.winfo_screenwidth(), popup.winfo_screenheight())
        left, top, right, bottom = area
        if y + h > bottom:
            y = anchor.winfo_rooty() - h - max(4, gap // 4)
        x = max(left, min(x, right - w))
        y = max(top, min(y, bottom - h))
        popup.wm_geometry(f"+{int(x)}+{int(y)}")
    except Exception:
        pass
    try:
        popup.deiconify()
    except Exception:
        pass


def _scaled(widget, value: int) -> int:
    """`value` pixels for `widget`'s monitor -- see core.ui.dpi.px."""
    from core.ui import dpi

    return dpi.px(widget, value)


class Tooltip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tooltip_window = None
        self.show_delay = 600
        self.hide_delay = 100
        self.enter_id = None
        self.leave_id = None
        self._bind_tree(self.widget)

    def _bind_tree(self, widget, depth: int = 0) -> None:
        """Bind the hover handlers on `widget` and everything inside it.

        A ttk widget is one window and one binding is enough. A CustomTkinter
        widget is a frame wrapping a canvas and a label, and Tk delivers an
        event to the deepest window under the pointer without bubbling it up to
        the parent -- so binding only the outer frame meant the press that
        opens a dropdown never reached this class, and the tooltip stayed up
        over the open menu until the pointer left the whole widget.
        """
        try:
            widget.bind("<Enter>", self.show_tooltip, add="+")
            widget.bind("<Leave>", self.hide_tooltip, add="+")
            widget.bind("<ButtonPress>", self.hide_tooltip, add="+")
        except Exception:
            return
        # Composite widgets are two levels deep at most; the bound is there so
        # a tooltip on a container can never walk a whole page of widgets.
        if depth >= 2:
            return
        try:
            children = widget.winfo_children()
        except Exception:
            return
        for child in children:
            self._bind_tree(child, depth + 1)

    def _display_tooltip(self):
        self.enter_id = None
        text = resolve_tooltip_text(self.text)
        # A widget can stand in text of its own for a while, over whatever
        # tooltips were attached to it -- the import button does, while it
        # reads "Change Inputs" and the attached text describes importing.
        override = getattr(self.widget, "_sc_tooltip_override", None)
        if override:
            text = override
        if self.tooltip_window or not text:
            return
        if not self._pointer_is_inside():
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 20
        bg, fg, border = tooltip_colors()
        self.tooltip_window = Toplevel(self.widget)
        self.tooltip_window.wm_overrideredirect(True)
        self.tooltip_window.wm_geometry(f"+{x}+{y}")
        # The border is the Toplevel showing through a 1px margin. "solid"
        # relief draws its edge in a colour Tk picks, which on a dark palette
        # is a black line around a dark tooltip -- invisible.
        self.tooltip_window.configure(background=border)

        label = Label(
            self.tooltip_window,
            text=text,
            background=bg,
            foreground=fg,
            relief="flat",
            borderwidth=0,
            justify="left",
            # Pixels, so scaled for the monitor in Sharp mode: at 250 on a 4K
            # screen every tooltip wrapped into a tall, narrow column.
            wraplength=_scaled(self.widget, 250),
        )
        label.pack(ipadx=2, ipady=1, padx=1, pady=1)
        place_popup_on_screen(self.tooltip_window, self.widget, x, y)

    def hide_tooltip(self, event=None):
        if self.enter_id:
            try:
                self.widget.after_cancel(self.enter_id)
            except Exception:
                pass
            self.enter_id = None
        if self.tooltip_window:
            try:
                self.tooltip_window.destroy()
            except Exception:
                pass
        self.tooltip_window = None

    def show_tooltip(self, event=None):
        if self.leave_id:
            try:
                self.widget.after_cancel(self.leave_id)
            except Exception:
                pass
            self.leave_id = None
        # Cancel any pending show before scheduling another. One graze over a
        # composite widget fires <Enter> once per window inside it -- frame,
        # canvas, label -- and overwriting enter_id without cancelling left
        # those earlier timers pending. They fired after the pointer had long
        # since moved on, and hide_tooltip could only ever cancel the last id,
        # so tooltips popped up unbidden and then never went away.
        if self.enter_id:
            try:
                self.widget.after_cancel(self.enter_id)
            except Exception:
                pass
            self.enter_id = None
        self.enter_id = self.widget.after(self.show_delay, self._display_tooltip)

    def _pointer_is_inside(self) -> bool:
        """True when the pointer really is over this widget (or a child of it).

        A timer that survives the pointer leaving is the failure this guards:
        the belt to cancellation's braces, and the thing that keeps a tooltip
        from appearing over a window the user has already moved away from.
        """
        try:
            under = self.widget.winfo_containing(
                self.widget.winfo_pointerx(), self.widget.winfo_pointery()
            )
        except Exception:
            return True
        if under is None:
            return False
        target = str(self.widget)
        return str(under) == target or str(under).startswith(target + ".")


def keep_entry_end_visible(entry, var: Optional[tk.Variable] = None) -> None:
    """Scroll an Entry to show the END of its text whenever it isn't being edited.

    A path longer than its box normally shows its beginning, which for a file
    path is the drive and top folders -- the part you least need. This keeps
    the last folder or file name in view instead: after the value changes
    (Browse, Import, a loaded config), after the box is resized, and when you
    click away from it. While the box has focus it is left alone, so typing
    and cursor movement behave normally.
    """

    def _scroll_to_end() -> None:
        try:
            if entry.winfo_exists():
                entry.xview_moveto(1.0)
        except tk.TclError:
            pass

    def _holds_focus() -> bool:
        """True when the caret is in this box -- or in a child of it.

        A ttk.Entry IS the focused widget; a CustomTkinter entry is a frame
        wrapping a real tk.Entry, and focus lands on that inner widget. An
        identity test therefore never matched on CTk, so the box scrolled to
        the end on every keystroke while you were typing in it.
        """
        try:
            focused = entry.focus_get()
        except (KeyError, tk.TclError):
            # focus_get raises when focus sits in a widget Tk can't name
            # (a combobox popdown, say) -- which also means not this entry.
            return False
        if focused is None:
            return False
        path = str(entry)
        return str(focused) == path or str(focused).startswith(path + ".")

    def _request(*_args) -> None:
        if _holds_focus():
            return
        try:
            # Idle, not immediate: after a value change or resize the entry
            # has not re-measured yet, and scrolling first would use the old
            # text width.
            entry.after_idle(_scroll_to_end)
        except tk.TclError:
            pass

    entry.bind("<Configure>", _request, add="+")
    entry.bind("<FocusOut>", _request, add="+")
    if var is not None:
        var.trace_add("write", _request)
    _request()


def _bind_slider(slider, sequence: str, handler) -> None:
    """Bind a mouse sequence on a slider, ttk or CustomTkinter.

    A CTkSlider is a frame wrapping a canvas, and Tk delivers a button event
    to the deepest window under the pointer without bubbling it up -- so the
    binding has to reach the canvas. CTkSlider.bind() already forwards there,
    which is easy to miss: binding the frame AND its children, as the obvious
    defensive version does, lands two callbacks on the same canvas and fires
    on_slider_release twice for every drag -- two full preview re-renders.
    Measured, not guessed. One bind on the widget is both necessary and
    sufficient.

    add="+" throughout: CTkSlider drives its own knob from internal bindings
    and rejects anything that would replace them.
    """
    try:
        slider.bind(sequence, handler, add="+")
    except Exception:
        pass


def _knob_offset(slider, use_ctk: bool) -> int:
    """Half the knob, in pixels: where the knob's travel starts and ends.

    The tick that marks a slider's default value has to sit under the knob
    when the slider IS at that value, so it has to know how far in the knob's
    centre stops. 15 was measured off ttk.Scale's knob. A CTkSlider's is
    narrower and scales with the display, so it is derived rather than fixed.
    """
    if not use_ctk:
        return 15
    try:
        import customtkinter as ctk

        scaling = ctk.ScalingTracker.get_widget_scaling(slider.winfo_toplevel())
    except Exception:
        scaling = 1.0
    try:
        radius = float(slider.cget("corner_radius") or 0)
        length = float(slider.cget("button_length") or 0)
    except Exception:
        radius, length = 8.0, 6.0
    return max(1, int((radius + length / 2.0) * scaling))


def create_single_slider_with_label_updater(
    GUI_self,
    parent: ttk.Frame,
    text: str,
    var: tk.Variable,
    from_: float,
    to: float,
    row: int,
    decimals: int = 0,
    tooltip_key: Optional[str] = None,
    trough_increment: float = -1.0,
    display_next_odd_integer: bool = False,
    custom_label_formula: Optional[Callable] = None,
    step_size: Optional[float] = None,
    default_value: Optional[float] = None,
    progress_fill: bool = True,
    on_drag: Optional[Callable[[float], None]] = None,
) -> None:
    """A labelled slider row. `on_drag`, when given, is called with the new
    value on every step of a drag (and a wheel notch or a click on the track)
    -- for a preview that can follow the slider live, where on_slider_release
    only fires once it is let go."""
    # Wide enough for the digits actually shown. At decimals=4 a value like
    # "0.0010" is six characters and a fixed width of 5 clipped the last one,
    # which read as a rendering glitch rather than a too-narrow label.
    VALUE_LABEL_FIXED_WIDTH = max(5, decimals + 3)

    use_ctk = _is_ctk_stage(GUI_self)

    if use_ctk:
        label = GUI_self._ctk_label(parent, text=text, anchor="e")
    else:
        label = ttk.Label(parent, text=text, anchor="e")
    label.grid(row=row, column=0, sticky="ew", padx=0, pady=2)

    if tooltip_key and hasattr(GUI_self, "_create_hover_tooltip"):
        GUI_self._create_hover_tooltip(label, tooltip_key)

    actual_step = step_size if step_size is not None else (0.5 if decimals > 0 else 1.0)
    total_steps = int((to - from_) / actual_step)
    internal_int_var = tk.IntVar(value=int((float(var.get()) - from_) / actual_step))

    def update_label_only(value_float: float) -> None:
        try:
            if custom_label_formula:
                _set_text(value_label, custom_label_formula(value_float))
                return
            display_value = value_float
            if display_next_odd_integer:
                k_int = int(round(value_float))
                if k_int > 0 and k_int % 2 == 0:
                    display_value = k_int + 1
                elif k_int > 0:
                    display_value = k_int
                elif k_int == 0:
                    display_value = 0
            _set_text(value_label, f"{display_value:.{decimals}f}")
        except Exception:
            pass

    def on_slider_move(val):
        notch = int(float(val))
        actual_val = from_ + (notch * actual_step)
        actual_val = max(from_, min(to, actual_val))
        var.set(actual_val)
        update_label_only(actual_val)
        _update_marker_position()
        if on_drag is not None:
            try:
                on_drag(actual_val)
            except Exception:
                logger.debug("Slider drag callback failed", exc_info=True)

    if use_ctk:
        # number_of_steps matches the notch count the internal IntVar already
        # counts in, so the knob lands on the same positions the ttk one did.
        # scroll_step=1 is one notch per wheel click -- a capability
        # ttk.Scale never had.
        slider = GUI_self._ctk_slider(
            parent, from_=0, to=total_steps, variable=internal_int_var,
            number_of_steps=max(1, total_steps), scroll_step=1,
            orientation="horizontal", command=on_slider_move,
            # width is a FLOOR here, not a size: the slider is gridded
            # sticky="ew" and takes the column. CTkSlider defaults to 200,
            # and a panel of nine of them therefore demanded 200px of width
            # before anything else on the page had asked for any -- where a
            # ttk.Scale has no minimum at all.
            #
            # A host can raise it. The shell sizes each stage to what its page
            # asks for, so for a panel of sliders the floor IS the drag room
            # the window opens with -- leaving it at the bare minimum gives a
            # stage whose main control is a slider the least usable slider it
            # can legally draw.
            width=getattr(GUI_self, "slider_min_width", 110),
            # Off leaves the track one colour; see CtkThemedStage._ctk_slider.
            # Ignored on the ttk path, which has no filled segment anyway.
            progress=progress_fill,
        )
    else:
        slider = ttk.Scale(
            parent, from_=0, to=total_steps, variable=internal_int_var, orient="horizontal", command=on_slider_move
        )
    slider.grid(row=row, column=1, sticky="ew", padx=2)

    if use_ctk:
        # ttk counted the label's width in characters; CTk counts pixels.
        value_label = GUI_self._ctk_label(
            parent, text="", width=VALUE_LABEL_FIXED_WIDTH * 9, anchor="w"
        )
    else:
        value_label = ttk.Label(parent, text="", width=VALUE_LABEL_FIXED_WIDTH)
    value_label.grid(row=row, column=2, sticky="w", padx=0)
    parent.grid_columnconfigure(1, weight=1)

    if hasattr(GUI_self, "on_slider_release"):
        _bind_slider(slider, "<ButtonRelease-1>", GUI_self.on_slider_release)

    default_marker_label = None
    default_ratio = None
    if default_value is not None and to != from_:
        default_ratio = max(0.0, min(1.0, (default_value - from_) / (to - from_)))

    def _travel():
        """(start, length) of the knob centre's travel, in the slider's pixels.

        For a CTkSlider, from the very numbers it draws its knob with:
        centre = corner_radius + button_length/2 + (width - 2*corner_radius
        - button_length) * value, all scaled. The marker has to sit where the
        knob sits at the default, so anything cruder than that drifts as the
        slider changes width.
        """
        if use_ctk:
            try:
                width = slider._apply_widget_scaling(slider._current_width)
                radius = slider._apply_widget_scaling(slider._corner_radius)
                length = slider._apply_widget_scaling(slider._button_length)
                return radius + length / 2.0, max(0.0, width - 2.0 * radius - length)
            except Exception:
                pass
        width = slider.winfo_width()
        offset = _knob_offset(slider, use_ctk)
        return float(offset), float(max(0, width - 2 * offset))

    def _draw_default_marker():
        nonlocal default_marker_label
        if default_ratio is None:
            return
        if default_marker_label is not None:
            try:
                default_marker_label.destroy()
            except Exception:
                pass
            default_marker_label = None

        def _handle_marker_interaction(event):
            """A press on the marker is a press on the slider at that point."""
            try:
                start, length = _travel()
                if length <= 0:
                    return
                # event.x is within the marker, whose centre is its place x.
                x = float(marker_canvas.place_info().get("x", 0)) + event.x - marker_canvas.winfo_width() / 2.0
                rel_x = max(0.0, min(1.0, (x - start) / length))
                new_notch = int(round(rel_x * total_steps))
                internal_int_var.set(new_notch)
                on_slider_move(new_notch)
                if event.type == "5" and hasattr(GUI_self, "on_slider_release"):  # ButtonRelease
                    GUI_self.on_slider_release(event)
            except Exception:
                pass

        # Not until the slider has a size: the <Configure> that gives it one
        # calls this again. (No update_idletasks to force one -- a flush of
        # the whole app per slider, on a page with dozens of them.)
        if slider.winfo_width() <= 1:
            return
        try:
            marker_canvas = tk.Canvas(slider, highlightthickness=0, bg=DEFAULT_TICK_COLOR)
            # Where along the travel it belongs, for anything checking it.
            marker_canvas._sc_default_ratio = default_ratio
            marker_canvas.bind("<Button-1>", _handle_marker_interaction)
            marker_canvas.bind("<B1-Motion>", _handle_marker_interaction)
            marker_canvas.bind("<ButtonRelease-1>", _handle_marker_interaction)
            default_marker_label = marker_canvas
            _update_marker_position()
        except Exception as e:
            print(f"Marker error: {e}")

    def _update_marker_position():
        """Put the marker under where the knob sits at the default.

        Called on every resize of the slider, not just the first: the page
        re-flows -- window size, the wide/compact layout, a DPI change -- and
        a marker placed once stayed at its first-drawn x, up to a few hundred
        pixels off, until moving the slider happened to re-place it.
        """
        if default_marker_label is None or default_ratio is None:
            return
        try:
            if slider.winfo_width() <= 1:
                return
            start, length = _travel()
            x_pos = int(round(start + length * default_ratio))
            # Pixel sizes on a plain Tk canvas, so scaled by the monitor's DPI
            # for Sharp mode (dpi.px is 1.0 otherwise).
            from core.ui import dpi

            default_marker_label.configure(width=dpi.px(slider, 4), height=dpi.px(slider, 10))
            default_marker_label.place_configure(x=x_pos, y=slider.winfo_height() - dpi.px(slider, 2),
                                                 anchor="s")
        except Exception:
            pass

    def _on_right_click(event):
        if default_value is not None:
            var.set(default_value)
            sync_external_change()
            _update_marker_position()
            if hasattr(GUI_self, "on_slider_release"):
                GUI_self.on_slider_release(event)
        return "break"

    def _on_middle_click(event):
        try:
            slider_width = slider.winfo_width()
            if slider_width <= 1:
                return "break"
            click_x = event.x
            rel_x = click_x / slider_width
            rel_x = max(0.0, min(1.0, rel_x))
            clicked_notch = int(rel_x * total_steps)
            clicked_notch = max(0, min(total_steps, clicked_notch))
            clicked_val = from_ + (clicked_notch * actual_step)
            clicked_val = max(from_, min(to, clicked_val))
            var.set(clicked_val)
            sync_external_change()
            if hasattr(GUI_self, "on_slider_release"):
                GUI_self.on_slider_release(event)
        except Exception:
            pass
        return "break"

    if default_value is not None:
        for sequence, handler in (
            ("<Button-3>", _on_right_click),
            ("<ButtonRelease-3>", _on_right_click),
            ("<Button-2>", _on_middle_click),
            ("<ButtonRelease-2>", _on_middle_click),
        ):
            _bind_slider(slider, sequence, handler)
        GUI_self.after(50, _draw_default_marker)

    def _on_configure(event):
        if default_marker_label is None:
            _draw_default_marker()
        else:
            _update_marker_position()

    slider.bind("<Configure>", _on_configure)

    def sync_external_change():
        try:
            current_f = float(var.get())
            new_notch = int((current_f - from_) / actual_step)
            internal_int_var.set(new_notch)
            update_label_only(current_f)
            _update_marker_position()
        except Exception:
            pass

    sync_external_change()

    if hasattr(GUI_self, "slider_label_updaters"):
        GUI_self.slider_label_updaters.append(sync_external_change)
    if hasattr(GUI_self, "widgets_to_disable"):
        GUI_self.widgets_to_disable.append(slider)

    return lambda val: (var.set(val), sync_external_change())


def create_dual_slider_layout(
    GUI_self,
    parent: ttk.Frame,
    text_x: str,
    text_y: str,
    var_x: tk.Variable,
    var_y: tk.Variable,
    from_: float,
    to: float,
    row: int,
    decimals: int = 0,
    is_integer: bool = True,
    tooltip_key_x: Optional[str] = None,
    tooltip_key_y: Optional[str] = None,
    trough_increment: float = -1,
    display_next_odd_integer: bool = False,
    custom_label_formula: Optional[Callable] = None,
    default_x: Optional[float] = None,
    default_y: Optional[float] = None,
    from_y: Optional[float] = None,
    to_y: Optional[float] = None,
    decimals_y: Optional[int] = None,
    step_size_x: Optional[float] = None,
    step_size_y: Optional[float] = None,
    progress_fill_x: bool = True,
    progress_fill_y: bool = True,
) -> Tuple[ttk.Frame, Tuple[Callable, Callable], Tuple[ttk.Frame, ttk.Frame]]:
    # Three plain containers; the sliders inside them dispatch on their own.
    # They still have to follow the host, because a ttk.Frame inside a CTk
    # section keeps the ttk background and draws a rectangle of the wrong
    # colour behind the pair.
    _frame = (GUI_self._ctk_container if _is_ctk_stage(GUI_self)
              else (lambda master: ttk.Frame(master)))

    xy_frame = _frame(parent)
    xy_frame.grid(row=row, column=0, columnspan=2, sticky="ew", padx=5, pady=0)
    xy_frame.grid_columnconfigure(0, weight=1)
    xy_frame.grid_columnconfigure(1, weight=1)

    f_x, t_x, d_x = from_, to, decimals
    f_y = from_y if from_y is not None else from_
    t_y = to_y if to_y is not None else to
    d_y = decimals_y if decimals_y is not None else decimals

    x_frame = _frame(xy_frame)
    x_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
    x_frame.grid_columnconfigure(1, weight=1)
    set_x = create_single_slider_with_label_updater(
        GUI_self,
        x_frame,
        text_x,
        var_x,
        f_x,
        t_x,
        0,
        decimals=d_x,
        tooltip_key=tooltip_key_x,
        step_size=step_size_x,
        default_value=default_x,
        progress_fill=progress_fill_x,
    )

    y_frame = _frame(xy_frame)
    y_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
    y_frame.grid_columnconfigure(1, weight=1)
    set_y = create_single_slider_with_label_updater(
        GUI_self,
        y_frame,
        text_y,
        var_y,
        f_y,
        t_y,
        0,
        decimals=d_y,
        tooltip_key=tooltip_key_y,
        step_size=step_size_y,
        default_value=default_y,
        progress_fill=progress_fill_y,
    )
    return xy_frame, (set_x, set_y), (x_frame, y_frame)
