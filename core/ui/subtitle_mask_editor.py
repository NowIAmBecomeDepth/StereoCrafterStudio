"""Subtitle Mask editor popup for the Stereocrafter splatting GUI.

Opens the current previewer clip, auto-detects burned-in (white/yellow)
subtitle text via a two-threshold hysteresis key, and renders a grayscale
protection-mask video (<clip>_submask.mp4) next to the source clip. The
render stage (render_processor step 3.6) pins masked pixels to the
convergence anchor so subtitles sit at screen depth with zero ghosting.

Settings persist per-clip in the .fssidecar via the ``on_save`` callback
(submask_* keys, see sidecar_manager.SIDECAR_KEY_MAP).

Controls (mirrors the reference tool):
  Core          high luminance threshold -> solid text body
  Refine/Edge   lower threshold for the antialiased fringe, kept only where
                connected to Core pixels (hysteresis)
  R             grow radius (dilate) after hysteresis
  Hard/Soft     binary vs gaussian-feathered mask edge
  Any/White/Yellow  channel mode (White = min-channel, rejects bright colors)
  Mask          preview mask-only (white on black)
  Invert        invert final mask
  drag on image draw the green detection rect; Zoom Rect / Fit / Clear
"""

import os
import json
import shutil
import threading
import subprocess
import tkinter as tk
from tkinter import messagebox
from typing import Callable, Optional

import cv2
import numpy as np
from PIL import Image, ImageTk

DEBOUNCE_MS = 60
RECT_COLOR = "#4cc36a"

# sidecar keys <-> internal settings names
SIDECAR_PREFIX = "submask_"
SETTINGS_KEYS = ("core", "edge", "refine", "r", "edge_mode", "color", "invert", "rect")

DEFAULTS = {
    "core": 200,
    "edge": 165,
    "refine": True,
    "r": 2,
    "edge_mode": "Hard",
    "color": "Any",
    "invert": False,
    "rect": None,
}


def submask_output_path(source_video_path: str) -> str:
    """Mask video path convention shared with render_processor.resolve_subtitle_mask."""
    return os.path.splitext(source_video_path)[0] + "_submask.mp4"


def settings_from_sidecar(sidecar_data: Optional[dict]) -> dict:
    """Extract submask_* keys from a loaded sidecar dict into editor settings."""
    s = dict(DEFAULTS)
    if not sidecar_data:
        return s
    for k in SETTINGS_KEYS:
        val = sidecar_data.get(SIDECAR_PREFIX + k)
        if val is not None:
            s[k] = val
    rect = s.get("rect")
    if rect is not None:
        try:
            s["rect"] = tuple(int(v) for v in rect)
        except (TypeError, ValueError):
            s["rect"] = None
    return s


def settings_to_sidecar(settings: dict) -> dict:
    """Convert editor settings into submask_* sidecar keys."""
    out = {}
    for k in SETTINGS_KEYS:
        v = settings.get(k, DEFAULTS[k])
        if k == "rect":
            v = list(v) if v else None
        out[SIDECAR_PREFIX + k] = v
    return out


def compute_mask(frame_bgr: np.ndarray, settings: dict) -> np.ndarray:
    """Return uint8 mask (0-255), same HxW as the frame."""
    b, g, r = cv2.split(frame_bgr)
    mode = settings["color"]
    if mode == "White":
        # white text = ALL channels bright; min-channel rejects saturated colors
        v = cv2.min(cv2.min(b, g), r)
    elif mode == "Yellow":
        v = cv2.min(g, r)
        v = cv2.subtract(v, (b // 2).astype(np.uint8))
    else:  # Any
        v = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

    core = (v >= settings["core"]).astype(np.uint8)

    if settings["refine"]:
        edge_t = min(settings["edge"], settings["core"])
        edge = (v >= edge_t).astype(np.uint8)
        # hysteresis: keep edge components containing at least one core pixel
        n, labels = cv2.connectedComponents(edge, connectivity=8)
        if n > 1:
            hit = np.zeros(n, dtype=bool)
            lbl_core = labels[core > 0]
            if lbl_core.size:
                hit[np.unique(lbl_core)] = True
            hit[0] = False
            mask = hit[labels].astype(np.uint8)
        else:
            mask = core
    else:
        mask = core

    rad = int(settings["r"])
    if rad > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * rad + 1, 2 * rad + 1))
        mask = cv2.dilate(mask, k)

    mask = mask * 255

    if settings["edge_mode"] == "Soft":
        mask = cv2.GaussianBlur(mask, (0, 0), max(0.8, rad * 0.8))

    rect = settings.get("rect")
    if rect:
        x0, y0, x1, y1 = rect
        limited = np.zeros_like(mask)
        limited[y0:y1, x0:x1] = mask[y0:y1, x0:x1]
        mask = limited

    if settings["invert"]:
        mask = 255 - mask

    return mask


class SubtitleMaskEditor(tk.Toplevel):
    """Modal-ish popup bound to one clip.

    `parent` is the stage that opens it (Splatting): the controls are built
    from its _ctk_* factories and the window is finished by its _ctk_popup,
    so the editor is in the app's palette and scales with the monitor like
    the stage does. The video itself is a raw tk.Canvas.
    """

    def __init__(
        self,
        parent,
        video_path: str,
        start_frame: int = 0,
        initial_settings: Optional[dict] = None,
        on_save: Optional[Callable[[dict, str], None]] = None,
    ):
        super().__init__(parent)
        self.video_path = video_path
        self.on_save = on_save

        self.cap = cv2.VideoCapture(video_path)
        if not self.cap.isOpened():
            messagebox.showerror("Subtitle Mask", f"Could not open:\n{video_path}", parent=parent)
            self.destroy()
            return
        self.n_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.fps = self.cap.get(cv2.CAP_PROP_FPS) or 24.0
        self.vw = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.vh = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        self.frame_idx = 0
        self.frame_bgr = None
        self.mask = None
        self.rect = None
        self.zoomed = False
        self.drag_start = None
        self.display_scale = 1.0
        self.display_off = (0, 0)
        self.view_origin = (0, 0)
        self._after_id = None
        self._render_thread = None
        self._stop_render = False
        self._photo = None

        self.transient(parent)
        self.ui = parent
        self._build_ui()
        # Sizes are logical pixels, made real for the monitor in Sharp mode.
        parent._ctk_popup(self, size=(1180, 820), minsize=(860, 560))
        self._apply_settings(settings_from_sidecar(initial_settings) if initial_settings else dict(DEFAULTS))

        start = max(0, min(self.n_frames - 1, int(start_frame)))
        self.scale_frame.set(start)
        self.seek(start)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Left>", lambda e: self.step_frame(-1))
        self.bind("<Right>", lambda e: self.step_frame(1))
        self.bind("<Shift-Left>", lambda e: self.step_frame(-10))
        self.bind("<Shift-Right>", lambda e: self.step_frame(10))

    # ------------------------------------------------------------------ UI --
    def _build_ui(self):
        ui = self.ui
        self.canvas = tk.Canvas(self, bg="black", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=4, pady=(4, 0))
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Configure>", lambda e: self.redraw())

        # Label widths are logical pixels (CTk), standing in for ttk's
        # characters. Sliders step in whole units, as the IntVars they drive.
        nav = ui._ctk_container(self)
        nav.pack(fill="x", padx=6, pady=(4, 0))
        self.lbl_frame = ui._ctk_label(nav, text="Frame: - / -", width=130, anchor="w")
        self.lbl_frame.pack(side="left")
        self.scale_frame = ui._ctk_slider(nav, from_=0, to=max(0, self.n_frames - 1),
                                          number_of_steps=max(1, self.n_frames - 1),
                                          command=self.on_frame_scrub)
        self.scale_frame.pack(side="left", fill="x", expand=True, padx=6)

        row1 = ui._ctk_container(self)
        row1.pack(fill="x", padx=6, pady=(6, 0))
        ui._ctk_label(row1, text="Core").pack(side="left")
        self.var_core = tk.IntVar(value=DEFAULTS["core"])
        self.s_core = ui._ctk_slider(row1, from_=0, to=255, number_of_steps=255,
                                     command=lambda v: self._slider_int(self.var_core, v))
        self.s_core.pack(side="left", fill="x", expand=True, padx=6)
        self.lbl_core = ui._ctk_label(row1, text=str(DEFAULTS["core"]), width=36, anchor="e")
        self.lbl_core.pack(side="left")

        self.var_maskonly = tk.BooleanVar(value=False)
        ui._ctk_check(row1, text="Mask", variable=self.var_maskonly,
                      command=self.schedule_update).pack(side="left", padx=(10, 0))
        self.var_invert = tk.BooleanVar(value=False)
        ui._ctk_check(row1, text="Invert", variable=self.var_invert,
                      command=self.schedule_update).pack(side="left", padx=(6, 0))

        self.var_edgemode = tk.StringVar(value=DEFAULTS["edge_mode"])
        ui._ctk_option(row1, variable=self.var_edgemode, values=["Hard", "Soft"],
                       command=lambda _v: self.schedule_update()).pack(side="left", padx=(8, 0))

        self.var_color = tk.StringVar(value=DEFAULTS["color"])
        ui._ctk_option(row1, variable=self.var_color, values=["Any", "White", "Yellow"],
                       command=lambda _v: self.schedule_update()).pack(side="left", padx=(6, 0))

        row2 = ui._ctk_container(self)
        row2.pack(fill="x", padx=6, pady=(4, 0))
        self.var_refine = tk.BooleanVar(value=DEFAULTS["refine"])
        ui._ctk_check(row2, text="Refine  Edge", variable=self.var_refine,
                      command=self.schedule_update).pack(side="left")
        self.var_edge = tk.IntVar(value=DEFAULTS["edge"])
        self.s_edge = ui._ctk_slider(row2, from_=0, to=255, number_of_steps=255,
                                     command=lambda v: self._slider_int(self.var_edge, v))
        self.s_edge.pack(side="left", fill="x", expand=True, padx=6)
        self.lbl_edge = ui._ctk_label(row2, text=str(DEFAULTS["edge"]), width=36, anchor="e")
        self.lbl_edge.pack(side="left")

        ui._ctk_label(row2, text="R").pack(side="left", padx=(8, 4))
        self.var_r = tk.IntVar(value=DEFAULTS["r"])
        ui._ctk_spin(row2, from_=0, to=20, textvariable=self.var_r, width=44,
                     compact=True).pack(side="left")
        # The spin only writes an in-range number to var_r, so its trace is
        # the one place to listen -- arrows, wheel and typing alike.
        self.var_r.trace_add("write", lambda *_a: self.schedule_update())

        ui._ctk_button(row2, text="Zoom Rect", command=self.zoom_rect).pack(side="left", padx=(10, 0))
        ui._ctk_button(row2, text="Fit", command=self.fit_view).pack(side="left", padx=(4, 0))
        ui._ctk_button(row2, text="Clear", command=self.clear_rect).pack(side="left", padx=(4, 0))

        row3 = ui._ctk_container(self)
        row3.pack(fill="x", padx=6, pady=(6, 8))
        ui._ctk_label(row3, text="From:").pack(side="left")
        self.ent_from = ui._ctk_entry(row3, width=60)
        self.ent_from.pack(side="left", padx=(4, 8))
        self.ent_from.insert(0, "0")
        ui._ctk_label(row3, text="To:").pack(side="left")
        self.ent_to = ui._ctk_entry(row3, width=60)
        self.ent_to.pack(side="left", padx=(4, 8))
        self.ent_to.insert(0, str(max(0, self.n_frames - 1)))

        from core.ui.ctk_theme import CtkProgressAdapter

        # Answers ttk's configure(maximum=, value=), which the render uses.
        self.progress = ui._ctk_progress(row3, factory=CtkProgressAdapter)
        self.progress.pack(side="left", fill="x", expand=True, padx=6)
        self.lbl_status = ui._ctk_label(row3, text="", width=230, anchor="w")
        self.lbl_status.pack(side="left")

        ui._ctk_button(row3, text="Cancel", command=self._on_close).pack(side="right", padx=(4, 0))
        ui._ctk_button(row3, text="Save", primary=True, command=self.save_mask).pack(side="right")

    def _slider_int(self, var, v):
        var.set(int(float(v)))
        self.lbl_core.configure(text=str(self.var_core.get()))
        self.lbl_edge.configure(text=str(self.var_edge.get()))
        self.schedule_update()

    # ------------------------------------------------------------ settings --
    def current_settings(self) -> dict:
        return {
            "core": self.var_core.get(),
            "edge": self.var_edge.get(),
            "refine": bool(self.var_refine.get()),
            "r": max(0, int(self.var_r.get() or 0)),
            "edge_mode": self.var_edgemode.get(),
            "color": self.var_color.get(),
            "invert": bool(self.var_invert.get()),
            "rect": self.rect,
        }

    def _apply_settings(self, s: dict):
        self.var_core.set(int(s.get("core", DEFAULTS["core"])))
        self.s_core.set(self.var_core.get())
        self.var_edge.set(int(s.get("edge", DEFAULTS["edge"])))
        self.s_edge.set(self.var_edge.get())
        self.var_refine.set(bool(s.get("refine", DEFAULTS["refine"])))
        self.var_r.set(int(s.get("r", DEFAULTS["r"])))
        self.var_edgemode.set(s.get("edge_mode", DEFAULTS["edge_mode"]))
        self.var_color.set(s.get("color", DEFAULTS["color"]))
        self.var_invert.set(bool(s.get("invert", DEFAULTS["invert"])))
        rect = s.get("rect")
        self.rect = tuple(rect) if rect else None
        self.lbl_core.configure(text=str(self.var_core.get()))
        self.lbl_edge.configure(text=str(self.var_edge.get()))

    # ------------------------------------------------------------ playback --
    def seek(self, idx: int):
        idx = max(0, min(self.n_frames - 1, idx))
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = self.cap.read()
        if not ok:
            return
        self.frame_idx = idx
        self.frame_bgr = frame
        self.lbl_frame.configure(text=f"Frame: {idx} / {self.n_frames - 1}")
        self.title(f"Subtitle Mask — {os.path.basename(self.video_path)} — Frame {idx}")
        self.update_mask()

    def on_frame_scrub(self, v):
        idx = int(float(v))
        if idx != self.frame_idx:
            self.seek(idx)

    def step_frame(self, d):
        self.seek(self.frame_idx + d)
        self.scale_frame.set(self.frame_idx)

    # ------------------------------------------------------------- preview --
    def schedule_update(self):
        if self._after_id:
            self.after_cancel(self._after_id)
        self._after_id = self.after(DEBOUNCE_MS, self.update_mask)

    def update_mask(self):
        self._after_id = None
        if self.frame_bgr is None:
            return
        self.mask = compute_mask(self.frame_bgr, self.current_settings())
        self.redraw()

    def redraw(self):
        if self.frame_bgr is None:
            return
        cw = self.canvas.winfo_width()
        ch = self.canvas.winfo_height()
        if cw < 10 or ch < 10:
            return

        if self.var_maskonly.get() and self.mask is not None:
            img = cv2.cvtColor(self.mask, cv2.COLOR_GRAY2RGB)
        else:
            img = cv2.cvtColor(self.frame_bgr, cv2.COLOR_BGR2RGB)
            if self.mask is not None:
                m = self.mask.astype(np.float32)[..., None] / 255.0
                img = (img.astype(np.float32) * (1 - m) + 255.0 * m).astype(np.uint8)

        if self.zoomed and self.rect:
            x0, y0, x1, y1 = self.rect
            pad = 24
            x0 = max(0, x0 - pad)
            y0 = max(0, y0 - pad)
            x1 = min(self.vw, x1 + pad)
            y1 = min(self.vh, y1 + pad)
            view = img[y0:y1, x0:x1]
            self.view_origin = (x0, y0)
        else:
            view = img
            self.view_origin = (0, 0)

        vh, vw = view.shape[:2]
        scale = min(cw / vw, ch / vh)
        dw, dh = max(1, int(vw * scale)), max(1, int(vh * scale))
        self.display_scale = scale
        self.display_off = ((cw - dw) // 2, (ch - dh) // 2)

        disp = cv2.resize(view, (dw, dh), interpolation=cv2.INTER_AREA)
        self._photo = ImageTk.PhotoImage(Image.fromarray(disp))
        self.canvas.delete("all")
        self.canvas.create_image(self.display_off[0], self.display_off[1],
                                 anchor="nw", image=self._photo)

        if self.rect:
            cx0, cy0 = self.vid_to_canvas(self.rect[0], self.rect[1])
            cx1, cy1 = self.vid_to_canvas(self.rect[2], self.rect[3])
            self.canvas.create_rectangle(cx0, cy0, cx1, cy1,
                                         outline=RECT_COLOR, width=2)

    def vid_to_canvas(self, x, y):
        ox, oy = self.view_origin
        return (self.display_off[0] + (x - ox) * self.display_scale,
                self.display_off[1] + (y - oy) * self.display_scale)

    def canvas_to_vid(self, cx, cy):
        ox, oy = self.view_origin
        x = ox + (cx - self.display_off[0]) / self.display_scale
        y = oy + (cy - self.display_off[1]) / self.display_scale
        return (int(max(0, min(self.vw, x))), int(max(0, min(self.vh, y))))

    # ---------------------------------------------------------------- rect --
    def on_press(self, e):
        if self.frame_bgr is not None:
            self.drag_start = self.canvas_to_vid(e.x, e.y)

    def on_drag(self, e):
        if not self.drag_start:
            return
        x0, y0 = self.drag_start
        x1, y1 = self.canvas_to_vid(e.x, e.y)
        self.rect = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        self.redraw()

    def on_release(self, e):
        if not self.drag_start:
            return
        self.on_drag(e)
        self.drag_start = None
        if self.rect and (self.rect[2] - self.rect[0] < 4 or self.rect[3] - self.rect[1] < 4):
            self.rect = None
        self.schedule_update()

    def zoom_rect(self):
        if self.rect:
            self.zoomed = True
            self.redraw()

    def fit_view(self):
        self.zoomed = False
        self.redraw()

    def clear_rect(self):
        self.rect = None
        self.zoomed = False
        self.schedule_update()

    # ---------------------------------------------------------------- save --
    def save_mask(self):
        if self._render_thread:
            return
        try:
            f0 = int(self.ent_from.get())
            f1 = int(self.ent_to.get())
        except ValueError:
            messagebox.showerror("Subtitle Mask", "Bad From/To frame numbers.", parent=self)
            return
        f0 = max(0, f0)
        f1 = min(self.n_frames - 1, f1)
        if f1 < f0:
            messagebox.showerror("Subtitle Mask", "To must be >= From.", parent=self)
            return

        out_path = submask_output_path(self.video_path)
        settings = self.current_settings()

        self._stop_render = False
        self.progress.configure(maximum=f1 - f0 + 1, value=0)
        self.lbl_status.configure(text="Rendering…")
        self._render_thread = threading.Thread(
            target=self._render_worker, args=(f0, f1, out_path, settings), daemon=True)
        self._render_thread.start()

    def _render_worker(self, f0, f1, out_path, settings):
        cap = cv2.VideoCapture(self.video_path)
        cap.set(cv2.CAP_PROP_POS_FRAMES, f0)

        use_ffmpeg = shutil.which("ffmpeg") is not None
        proc = writer = None
        if use_ffmpeg:
            cmd = ["ffmpeg", "-y", "-loglevel", "error",
                   "-f", "rawvideo", "-pix_fmt", "gray",
                   "-s", f"{self.vw}x{self.vh}", "-r", f"{self.fps:.6f}",
                   "-i", "-",
                   "-c:v", "libx264", "-crf", "10", "-preset", "fast",
                   "-pix_fmt", "yuv420p", out_path]
            proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE,
                creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
        else:
            writer = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(*"mp4v"),
                                     self.fps, (self.vw, self.vh), False)

        n_done = 0
        error = None
        try:
            # Frame alignment: the renderer indexes this mask video by ABSOLUTE
            # clip frame number, so a partial range must be padded. Frames
            # before From are written as black (mask inactive -> unpinned);
            # frames after To are simply absent (the renderer leaves frames
            # past the end of the mask unpinned). From/To therefore acts as a
            # temporal gate for when the subtitle pin is active.
            if f0 > 0:
                black = np.zeros((self.vh, self.vw), np.uint8)
                black_bytes = black.tobytes()
                for _ in range(f0):
                    if self._stop_render:
                        break
                    if proc:
                        proc.stdin.write(black_bytes)
                    else:
                        writer.write(black)
            for idx in range(f0, f1 + 1):
                if self._stop_render:
                    break
                ok, frame = cap.read()
                if not ok:
                    break
                mask = compute_mask(frame, settings)
                if proc:
                    proc.stdin.write(mask.tobytes())
                else:
                    writer.write(mask)
                n_done += 1
                if n_done % 5 == 0 or idx == f1:
                    self.after(0, lambda n=n_done: self.progress.configure(value=n))
        except Exception as e:  # pragma: no cover - defensive
            error = str(e)
        finally:
            cap.release()
            if proc:
                try:
                    proc.stdin.close()
                    proc.wait()
                except Exception:
                    pass
            if writer:
                writer.release()

        self.after(0, self._render_done, out_path, settings, n_done,
                   self._stop_render, error)

    def _render_done(self, out_path, settings, n, cancelled, error):
        self._render_thread = None
        self.progress.configure(value=0)
        if error:
            self.lbl_status.configure(text="Render failed.")
            messagebox.showerror("Subtitle Mask", f"Mask render failed:\n{error}", parent=self)
            return
        if cancelled:
            self.lbl_status.configure(text="Cancelled.")
            return
        self.lbl_status.configure(text=f"Saved {n} frames → {os.path.basename(out_path)}")
        if self.on_save:
            try:
                self.on_save(settings_to_sidecar(settings), out_path)
            except Exception:
                pass
        self.destroy_safe()

    # --------------------------------------------------------------- close --
    def _on_close(self):
        if self._render_thread:
            self._stop_render = True
            return
        self.destroy_safe()

    def destroy_safe(self):
        try:
            if self.cap:
                self.cap.release()
        except Exception:
            pass
        try:
            self.destroy()
        except tk.TclError:
            pass
