import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk
from typing import Optional, Callable
import logging

logger = logging.getLogger(__name__)


class PreviewCanvasWindow(tk.Toplevel):
    """A Toplevel window that hosts the preview canvas, scrollbars, and image display.

    Serves as both the main preview viewport and the SBS viewer. Supports zoom,
    drag/pan, fullscreen (F11), cross-eye toggle (X key), and image centering.
    Hides on close (X button) rather than being destroyed, and can be re-shown
    via ``show()``.
    """

    def __init__(
        self,
        parent,
        title: str = "Preview",
        zoom_callback: Optional[Callable] = None,
        sbs_cross_eye_var: Optional[tk.BooleanVar] = None,
        on_close_callback: Optional[Callable] = None,
        owner=None,
        is_active_callback: Optional[Callable] = None,
    ):
        super().__init__(parent)
        self.parent = parent
        # `parent` is the Tk master (a toplevel). `owner` is the GUI object that
        # owns this previewer, which used to be the same thing: each stage GUI
        # WAS its window. In the unified shell the toplevel is shared by four
        # stages, so reaching through `parent` for `.previewer` or
        # `.on_slider_release` finds the shell and silently does nothing.
        self.owner = owner if owner is not None else parent
        # Root-level key bindings land on a toplevel shared with the other
        # stages. This says whether this previewer's stage is the visible one;
        # standalone there is nothing to arbitrate and it is always True.
        self._is_active_callback = is_active_callback
        self.title(title)
        self.configure(bg="black")

        self._zoom_callback = zoom_callback
        self.sbs_cross_eye_var = sbs_cross_eye_var
        self._on_close_callback = on_close_callback

        # State
        self._is_dragging = False
        self._is_fullscreen = False
        self._photo: Optional[ImageTk.PhotoImage] = None
        self._pil_image: Optional[Image.Image] = None
        # Multiplier on top of fit-to-window: 1.0 IS fit. See _handle_zoom.
        self._view_zoom = 1.0

        # Geometry. 1280x720 is logical, made real for the monitor in Sharp
        # mode (unchanged otherwise) -- on 4K it opened at two-thirds size.
        try:
            from core.ui import dpi

            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            init_w = min(dpi.px(self, 1280), int(sw * 0.8))
            init_h = min(dpi.px(self, 720), int(sh * 0.7))
            self.geometry(f"{init_w}x{init_h}")
        except Exception:
            self.geometry("1280x720")

        self.resizable(True, True)

        # --- Canvas + Scrollbars ---
        self.preview_canvas = tk.Canvas(self, bg="black", highlightthickness=0)

        self.v_scrollbar = ttk.Scrollbar(self, orient="vertical", command=self._on_vscroll)
        self.h_scrollbar = ttk.Scrollbar(self, orient="horizontal", command=self._on_hscroll)
        self.preview_canvas.configure(yscrollcommand=self.v_scrollbar.set, xscrollcommand=self.h_scrollbar.set)

        self.preview_canvas.grid(row=0, column=0, sticky="nsew")
        self.v_scrollbar.grid(row=0, column=1, sticky="ns")
        self.h_scrollbar.grid(row=1, column=0, sticky="ew")
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Inner frame + label for displaying the image
        self._inner_frame = ttk.Frame(self.preview_canvas)
        self._canvas_window_id = self.preview_canvas.create_window((0, 0), window=self._inner_frame, anchor="nw")
        self.preview_label = ttk.Label(self._inner_frame, text="Load a video list to see preview", anchor="center")
        self.preview_label.pack(fill="both", expand=True)

        # Re-scale to the new size, not just re-centre: this window's size is
        # what decides how big the picture is now.
        self.preview_canvas.bind("<Configure>", lambda e: self._on_canvas_resized())

        # --- Zoom bindings ---
        for w in [self.preview_canvas, self.preview_label]:
            w.bind("<MouseWheel>", self._handle_zoom)
            w.bind("<Button-4>", self._handle_zoom)
            w.bind("<Button-5>", self._handle_zoom)

        # --- Drag/pan bindings ---
        self.preview_canvas.bind("<Button-1>", lambda e: self.preview_canvas.focus_set(), add="+")
        for b in ["<ButtonPress-1>", "<ButtonPress-2>"]:
            self.preview_label.bind(b, self._start_drag_scroll)
        for b in ["<B1-Motion>", "<B2-Motion>"]:
            self.preview_label.bind(b, self._drag_scroll)
        for b in ["<ButtonRelease-1>", "<ButtonRelease-2>"]:
            self.preview_label.bind(b, self._end_drag_scroll)

        # --- Key bindings ---
        # Bind on the root window for reliable key capture regardless of focus.
        # The canvas child may hold focus, preventing Toplevel-level bindings from firing.
        # Each handler checks _is_active() first: with four stages sharing one
        # toplevel, an ungated binding means one X keypress toggles cross-eye in
        # every mounted stage at once.
        root = self.parent.winfo_toplevel()

        def _gated(action):
            def _handler(event):
                if not self._is_active():
                    return None
                action()
                return "break"

            return _handler

        self._bind_f11 = root.bind("<F11>", _gated(self.toggle_fullscreen), add="+")
        self._bind_escape = root.bind("<Escape>", _gated(self._on_escape), add="+")
        self._bind_x_lower = root.bind("<x>", _gated(self._toggle_cross_eye), add="+")
        self._bind_x_upper = root.bind("<X>", _gated(self._toggle_cross_eye), add="+")

        # Unified key relay — pass keys to parent's top-level window
        self._relaying_lock = False
        self.bind("<KeyPress>", self._relay_key_event)

        # Double-click toggles fullscreen
        self.preview_label.bind("<Double-Button-1>", lambda e: self.toggle_fullscreen())

        # Close handler — hide instead of destroy
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Suppress initial geometry manager adjustments
        self.withdraw()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _is_active(self) -> bool:
        """True when this previewer's stage is the one the user is looking at."""
        if self._is_active_callback is None:
            return True
        try:
            return bool(self._is_active_callback())
        except Exception:
            return True

    def show(self):
        """Show (or re-show) the canvas window."""
        self.deiconify()
        # The title bar follows the palette in force NOW, which may have
        # changed since the window was built and hidden.
        try:
            from core.ui.theme_manager import apply_windows_titlebar_theme
            from core.ui.widgets import get_active_palette

            apply_windows_titlebar_theme(self, bool((get_active_palette() or {}).get("is_dark", True)))
        except Exception:
            logger.debug("Could not theme the preview window title bar", exc_info=True)
        self.lift()
        self.focus_set()

    def hide(self):
        """Hide the canvas window without destroying it."""
        self.withdraw()

    def destroy_window(self):
        """Fully destroy the Toplevel. After this the window cannot be re-shown."""
        # Remove root-level key bindings (targeted, using stored callback IDs)
        try:
            root = self.parent.winfo_toplevel()
            for seq, attr in (
                ("<F11>", "_bind_f11"),
                ("<Escape>", "_bind_escape"),
                ("<x>", "_bind_x_lower"),
                ("<X>", "_bind_x_upper"),
            ):
                cb_id = getattr(self, attr, None)
                if cb_id:
                    try:
                        root.unbind(seq, cb_id)
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass

    def exists(self) -> bool:
        return self.winfo_exists()

    def update_frame(self, pil_image: Image.Image):
        """Display a PIL image, scaled to this window rather than to the page.

        The frame arriving here was rendered for the inline panel, at the main
        window's Preview Scale. Showing it 1:1 made this window a pixel-for-pixel
        echo: dragging it larger just added background, because the picture was
        already a fixed size. It now fills whatever room THIS window has, so its
        size is its own.

        Note what that does and does not buy: enlarging here stretches the frame
        that was rendered, it does not re-render at a higher scale. Raising
        Preview Scale (Ctrl+wheel, or the dropdown) is still what adds real
        detail -- this, and the wheel's view zoom, add size.
        """
        if not self.winfo_exists():
            return
        try:
            self._pil_image = pil_image
            self._render_current_frame()
        except Exception as e:
            logger.debug(f"PreviewCanvasWindow.update_frame error: {e}")

    def _render_current_frame(self):
        """(Re)draw the held frame at the size this window can show."""
        if self._pil_image is None:
            return
        display = self._fit_to_canvas(self._pil_image)
        self._photo = ImageTk.PhotoImage(display)
        self.preview_label.config(image=self._photo, text="")
        self.preview_label.image = self._photo
        self._update_layout()

    def _fit_scale(self, image: Image.Image) -> Optional[float]:
        """The scale that makes ``image`` fill this window, or None if unknown."""
        try:
            cw = self.preview_canvas.winfo_width()
            ch = self.preview_canvas.winfo_height()
        except Exception:
            return None
        if cw <= 1 or ch <= 1 or image.width <= 0 or image.height <= 0:
            return None
        return min(cw / image.width, ch / image.height)

    def _fit_to_canvas(self, image: Image.Image) -> Image.Image:
        """The frame scaled to fill this window, aspect preserved, times the view zoom.

        Scales up as well as down -- a 30% preview in a maximised window is
        exactly the case this exists for. Returns the original when the canvas
        has not been laid out yet, or when the difference is not worth a resample.
        """
        fit = self._fit_scale(image)
        if fit is None:
            return image
        scale = fit * self._view_zoom
        if abs(scale - 1.0) < 0.02:
            return image
        size = (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
        resample = Image.LANCZOS if scale < 1.0 else Image.BICUBIC
        try:
            return image.resize(size, resample)
        except Exception:
            return image

    def _on_canvas_resized(self):
        """Debounced re-fit after a drag; a resize is a burst of events."""
        existing = getattr(self, "_refit_id", None)
        if existing is not None:
            try:
                self.after_cancel(existing)
            except Exception:
                pass
        self._refit_id = self.after(80, self._refit_now)

    def _refit_now(self):
        self._refit_id = None
        if self._pil_image is None:
            self._update_layout()
            return
        self._render_current_frame()

    def clear(self):
        """Clear the displayed image and show placeholder text."""
        dummy = ImageTk.PhotoImage(Image.new("RGBA", (1, 1), (0, 0, 0, 0)))
        self._photo = dummy
        self._pil_image = None
        try:
            self.preview_label.config(image=dummy, text="Load a video list to see preview")
            self.preview_label.image = dummy
        except Exception:
            pass

    def apply_theme(self, colors: dict):
        """Apply theme colors to the canvas."""
        try:
            bg = colors.get("bg", "#000000")
            self.preview_canvas.config(bg=bg, highlightthickness=0)
            self.configure(bg=bg)
        except Exception:
            pass

    def toggle_fullscreen(self, force: Optional[bool] = None):
        if force is None:
            self._is_fullscreen = not self._is_fullscreen
        else:
            self._is_fullscreen = bool(force)
        self.attributes("-fullscreen", self._is_fullscreen)

    @property
    def is_cross_eye(self) -> bool:
        if self.sbs_cross_eye_var is not None:
            return bool(self.sbs_cross_eye_var.get())
        return False

    # ------------------------------------------------------------------
    # Internal: layout
    # ------------------------------------------------------------------

    def _update_layout(self):
        """Center the image if smaller than canvas; show/hide scrollbars."""
        if self._pil_image is None or self._photo is None:
            self.v_scrollbar.grid_remove()
            self.h_scrollbar.grid_remove()
            return

        canvas_w = self.preview_canvas.winfo_width()
        canvas_h = self.preview_canvas.winfo_height()
        img_w = self._photo.width()
        img_h = self._photo.height()

        if canvas_w <= 1 or canvas_h <= 1:
            return

        v_needed = img_h > canvas_h
        h_needed = img_w > canvas_w

        if v_needed:
            self.v_scrollbar.grid()
        else:
            self.v_scrollbar.grid_remove()
        if h_needed:
            self.h_scrollbar.grid()
        else:
            self.h_scrollbar.grid_remove()

        x = max(0, (canvas_w - img_w) // 2)
        y = max(0, (canvas_h - img_h) // 2)

        self.preview_canvas.coords(self._canvas_window_id, x, y)
        self._inner_frame.update_idletasks()
        self.preview_canvas.config(scrollregion=(0, 0, max(canvas_w, img_w), max(canvas_h, img_h)))

    # ------------------------------------------------------------------
    # Internal: scroll
    # ------------------------------------------------------------------

    def _on_vscroll(self, *args):
        self.preview_canvas.yview(*args)

    def _on_hscroll(self, *args):
        self.preview_canvas.xview(*args)

    # ------------------------------------------------------------------
    # Internal: zoom
    # ------------------------------------------------------------------

    #: Per-notch view zoom factor, and the range the wheel may reach. The
    #: ceiling is 4 canvases wide because the whole image is rasterised even
    #: though only a canvas-sized window of it is on screen -- an unbounded zoom
    #: on a 4K side-by-side would build a several-hundred-megabyte PhotoImage.
    _VIEW_ZOOM_STEP = 1.1
    _VIEW_ZOOM_MIN = 0.1
    _VIEW_ZOOM_MAX = 4.0

    def _handle_zoom(self, event):
        """Wheel: make the picture bigger or smaller in this window.

        The wheel used to drive Preview Scale, the render resolution -- which in
        THIS window changes nothing you can see as size, because every frame is
        refitted to the window on arrival (see update_frame). The only visible
        effect was the picture getting sharper or blockier while staying exactly
        as big, which is not what a scroll wheel over an image means.

        So the wheel now moves a view zoom that multiplies the fit: 1.0 is
        fit-to-window, above that the image overflows and the scrollbars and
        drag-pan take over. Ctrl+wheel still drives Preview Scale for anyone who
        wants more real detail rather than more size; right-click resets both.
        """
        # X11 sends button 4/5; Windows and macOS send a signed delta, 120 per
        # notch, and a high-resolution wheel can report several notches at once.
        if getattr(event, "num", None) in (4, 5):
            steps = 1 if event.num == 4 else -1
        else:
            delta = int(getattr(event, "delta", 0) or 0)
            if delta == 0:
                return
            steps = max(1, abs(delta) // 120) * (1 if delta > 0 else -1)

        # Ctrl+wheel keeps the old meaning: re-render at a different scale.
        if int(getattr(event, "state", 0) or 0) & 0x0004:
            if self._zoom_callback:
                self._zoom_callback(event)
            return "break"

        old = self._view_zoom
        new = max(self._VIEW_ZOOM_MIN, min(self._VIEW_ZOOM_MAX, old * (self._VIEW_ZOOM_STEP**steps)))
        if abs(new - old) < 1e-6:
            return "break"
        self._view_zoom = new
        self._rezoom_around_pointer(event, old, new)
        return "break"

    def _rezoom_around_pointer(self, event, old: float, new: float):
        """Re-render at the new zoom, holding the point under the cursor still."""
        anchor = None
        try:
            if self._photo is not None:
                # Where the cursor sits inside the image, as a 0..1 fraction.
                cx = self.preview_canvas.canvasx(event.x_root - self.preview_canvas.winfo_rootx())
                cy = self.preview_canvas.canvasy(event.y_root - self.preview_canvas.winfo_rooty())
                x0, y0 = self.preview_canvas.coords(self._canvas_window_id)
                iw, ih = self._photo.width(), self._photo.height()
                if iw > 0 and ih > 0:
                    anchor = (
                        min(1.0, max(0.0, (cx - x0) / iw)),
                        min(1.0, max(0.0, (cy - y0) / ih)),
                        event.x_root - self.preview_canvas.winfo_rootx(),
                        event.y_root - self.preview_canvas.winfo_rooty(),
                    )
        except Exception:
            anchor = None

        self._render_current_frame()

        if anchor is None:
            return
        fx, fy, px, py = anchor
        try:
            x0, y0 = self.preview_canvas.coords(self._canvas_window_id)
            iw, ih = self._photo.width(), self._photo.height()
            region = self.preview_canvas.cget("scrollregion").split()
            rw, rh = float(region[2]), float(region[3])
            if rw > 0:
                self.preview_canvas.xview_moveto(max(0.0, (x0 + fx * iw - px) / rw))
            if rh > 0:
                self.preview_canvas.yview_moveto(max(0.0, (y0 + fy * ih - py) / rh))
        except Exception:
            pass

    def reset_view_zoom(self):
        """Back to fit-to-window, and back to the top-left of the scroll region."""
        if abs(self._view_zoom - 1.0) < 1e-6:
            return
        self._view_zoom = 1.0
        self._render_current_frame()
        try:
            self.preview_canvas.xview_moveto(0.0)
            self.preview_canvas.yview_moveto(0.0)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Internal: drag / pan
    # ------------------------------------------------------------------

    def _start_drag_scroll(self, event):
        if self.v_scrollbar.winfo_ismapped() or self.h_scrollbar.winfo_ismapped():
            self._is_dragging = True
            self.preview_canvas.config(cursor="fleur")
            self.preview_canvas.scan_mark(int(event.x), int(event.y))

    def _end_drag_scroll(self, event):
        self._is_dragging = False
        self.preview_canvas.config(cursor="")

    def _drag_scroll(self, event):
        if self._is_dragging:
            self.preview_canvas.scan_dragto(int(event.x), int(event.y), gain=1)

    # ------------------------------------------------------------------
    # Internal: cross-eye
    # ------------------------------------------------------------------

    def _toggle_cross_eye(self):
        if self.sbs_cross_eye_var is not None:
            self.sbs_cross_eye_var.set(not bool(self.sbs_cross_eye_var.get()))

        # Clear the display buffer so the next preview re-renders with new L/R order
        if hasattr(self.owner, "previewer") and hasattr(self.owner.previewer, "clear_display_buffer"):
            self.owner.previewer.clear_display_buffer()

        # Request preview refresh via the owning GUI if available
        if hasattr(self.owner, "on_slider_release"):
            self.owner.on_slider_release(None)
        elif hasattr(self.owner, "update_preview"):
            self.owner.update_preview()

    def _on_escape(self):
        """Handle Escape key: exit fullscreen if active."""
        if self._is_fullscreen:
            self.toggle_fullscreen(force=False)

    # ------------------------------------------------------------------
    # Internal: key relay
    # ------------------------------------------------------------------

    def _relay_key_event(self, event):
        """Pass key presses to the parent's top-level window."""
        if self._relaying_lock:
            return
        if not self._is_active():
            return

        # X, F11, Escape are handled by root-window bindings (see _create_widgets).
        # Relay everything else to the parent's top-level window.
        root = self.parent.winfo_toplevel()
        kwargs = {
            "serial": event.serial,
            "time": event.time,
            "x": event.x,
            "y": event.y,
            "rootx": event.x_root,
            "rooty": event.y_root,
            "state": event.state,
            "keycode": event.keycode,
            "keysym": event.keysym,
        }
        try:
            self._relaying_lock = True
            if len(event.keysym) <= 1 or event.keysym in ("Left", "Right", "Up", "Down", "space", "Prior", "Next"):
                root.event_generate(f"<{event.keysym}>", **kwargs)
            else:
                root.event_generate("<KeyPress>", **kwargs)
        except Exception:
            pass
        finally:
            self._relaying_lock = False

        return "break"

    # ------------------------------------------------------------------
    # Internal: close
    # ------------------------------------------------------------------

    def _on_close(self):
        if self._on_close_callback:
            self._on_close_callback()
        self.hide()
