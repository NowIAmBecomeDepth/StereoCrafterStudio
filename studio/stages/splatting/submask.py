"""submask.py -- Detect Text, v1's Subtitle Mask editor without the Tk.

v1: core/ui/subtitle_mask_editor.SubtitleMaskEditor (a Tk Toplevel) opened by
splatting_gui.open_subtitle_mask_editor. The detection itself (compute_mask),
the sidecar key mapping and the mask-video path are toolkit-neutral functions
in that same module and are imported, not copied; what is here is the
window's logic -- its capture, seek, the drawn rectangle, zoom, and the
mask-video render -- rule for rule. The window is SubtitleMaskWindow.qml.

The GPU (user, 2026-10-01; measured 2026-10-02): only the mask video's
encode uses it (NVENC). Decoding and the mask itself stay on the CPU -- on a
1080p clip OpenCV's decode was faster than hardware decoding (4 vs 11 ms a
frame in order, 34 vs 49 ms a seek: the frame comes back from the card) and
compute_mask faster than its CUDA twin (21 vs 87 ms: connectedComponents beats
label propagation), so the CUDA path made scrubbing slower.
core.splatting.submask_gpu keeps the CUDA mask (bit-identical, tested). While
a stage's job runs the encode is libx264 -- nothing may slow a running job.

Exact frames (user, 2026-10-02: "the frame number is inaccurate", the
picture jumping at frame 39): OpenCV's seek (CAP_PROP_POS_FRAMES / _MSEC) is
wrong on long-GOP HEVC with B-frames -- on the user's clip frames 31-39 came
back as frames from elsewhere and every one from 40 on as the frame after,
keyframes included; Save's render seeked too, so its mask was a frame out of
step. Reading from frame 0 in order is exact. So nothing here seeks any more:
the editor's own capture only reads forward, opened again from the start to
go back; a background pass (its own capture) decodes the clip in order into a
frame cache (CACHE_BYTES, ~330 1080p frames) when the editor opens, after
which scrubbing is a lookup; Save's render reads from frame 0. (decord and
ffmpeg -ss were exact too but 350-600 ms a frame on that clip.)

The rectangle limits the work (user, 2026-10-02): with one drawn, the mask is
made from that area alone (plus the R / Soft reach around it), not the whole
frame -- a text component reaching in from outside it no longer counts.

Selections (user, 2026-10-02): text often comes in several frame ranges with
gaps between, each needing its own Core / Edge / rectangle. The editor holds a
list of selections -- each its own settings plus a From / To range -- and the
controls edit the current one. Save renders ONE mask video: each frame the
union of the masks of every selection covering it, black where none does. The
list goes in the sidecar as submask_segments; the old single-selection
submask_* keys are written too (from the first), and a sidecar with only those
opens as one selection over the whole clip.

No selections (user, 2026-10-02: the last one could not be removed): Remove
takes the last one too; Save then takes the clip's mask off -- the mask video
deleted, submask_enabled off in the sidecar.

Freeze (user, 2026-10-02): fading text loses the detection as it dims. A
selection can be FROZEN on a frame where the text is caught well -- that
frame's mask is then used, at full strength, on every frame of the selection.
(Fades -- the mask ramping in / out with the text -- were tried and removed the
same day: the user found a fading mask let DepthCrafter's warped depth back
onto letters still on screen; holding the text needs the full mask until it
is gone. Fade keys in older sidecars are ignored.)

Flatten Layers (user, 2026-10-02: opening credits need many masks): the
selections made so far are rendered and saved, then set aside as FLATTENED --
kept with their exact settings, ranges and freezes, no longer listed or
edited -- and the list starts empty for the next ones. Every Save renders the
flattened ones too (each frame: the union of every flattened and every
current selection covering it), and the picture shows them. They go in the
sidecar as submask_flat; Reset All clears them with the rest.

Threads: every method but the render runs on the preview's worker (the save
touches the engine's sidecar and previewer, as v1's on_save did on the Tk
thread). The render runs on its own thread, as v1's did, reporting through
the callbacks it is given.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
from typing import Any, Callable, Dict, List, Optional

import cv2
import numpy as np
from PIL import Image

from core.common.partial_output import discard, ffmpeg_output_args, part_path, publish, reap
from core.splatting import submask_gpu
from core.ui.subtitle_mask_editor import (
    DEFAULTS,
    compute_mask,
    settings_from_sidecar,
    settings_to_sidecar,
    submask_output_path,
)

logger = logging.getLogger("splatting.v2.preview")

#: v1 redraw: context kept around the rectangle when zoomed to it.
ZOOM_PAD = 24

#: The decoded-frame cache's budget (see "Exact frames" above).
CACHE_BYTES = 2 * 1024 ** 3

#: A selection's own settings (compute_mask's), beside its range.
SEG_KEYS = ("core", "edge", "refine", "r", "edge_mode", "color", "invert", "rect")
#: ...and its freeze frame (None: off)
SEG_FRAME_KEYS = ("hold",)


def _clean_segment(seg: dict, n_frames: int) -> Dict[str, Any]:
    out = dict(DEFAULTS)
    for k in SEG_KEYS:
        if k in seg and seg[k] is not None:
            out[k] = seg[k]
    rect = out.get("rect")
    try:
        out["rect"] = tuple(int(v) for v in rect) if rect else None
    except (TypeError, ValueError):
        out["rect"] = None
    last = max(0, n_frames - 1)
    try:
        f0, f1 = int(seg.get("from", 0)), int(seg.get("to", last))
    except (TypeError, ValueError):
        f0, f1 = 0, last
    out["from"], out["to"] = max(0, min(last, f0)), max(0, min(last, f1))
    for k in SEG_FRAME_KEYS:
        v = seg.get(k)
        try:
            out[k] = None if v is None else max(0, min(last, int(v)))
        except (TypeError, ValueError):
            out[k] = None
    return out


def segments_from_sidecar(sidecar_data: Optional[dict], n_frames: int) -> List[Dict[str, Any]]:
    """The selections a sidecar holds: submask_segments, or -- a sidecar from
    before selections -- its one set of submask_* settings over the whole clip."""
    data = sidecar_data or {}
    segs = data.get("submask_segments")
    if isinstance(segs, list) and not segs and (data.get("submask_enabled") is False or data.get("submask_flat")):
        return []                       # the mask was removed, or every selection was flattened
    if isinstance(segs, list) and segs:
        out = [_clean_segment(sg, n_frames) for sg in segs if isinstance(sg, dict)]
        if out:
            return out
    one = settings_from_sidecar(data) if data else dict(DEFAULTS)
    return [_clean_segment({**one, "from": 0, "to": max(0, n_frames - 1)}, n_frames)]


def flat_from_sidecar(sidecar_data: Optional[dict], n_frames: int) -> List[Dict[str, Any]]:
    """The flattened selections a sidecar holds (submask_flat)."""
    flat = (sidecar_data or {}).get("submask_flat")
    if not isinstance(flat, list):
        return []
    return [_clean_segment(sg, n_frames) for sg in flat if isinstance(sg, dict)]


def segments_to_sidecar(segments: List[Dict[str, Any]]) -> Dict[str, Any]:
    """submask_segments, plus the old single keys from the first selection
    (what a reader from before selections understands)."""
    out = dict(settings_to_sidecar(segments[0]))
    out["submask_segments"] = [
        {"from": int(sg["from"]), "to": int(sg["to"]),
         **{k: (list(sg[k]) if k == "rect" and sg.get(k) else sg.get(k)) for k in SEG_KEYS},
         **{k: sg.get(k) for k in SEG_FRAME_KEYS}}
        for sg in segments]
    return out


class SubtitleMask:
    def __init__(self, eng, job_running: Optional[Callable[[], bool]] = None):
        self.eng = eng
        # True while a stage's job runs (preview.py asks GpuJobs): the CPU then
        self._job_running = job_running or (lambda: False)
        self._gpu_failed = False
        self.open = False
        self.cap = None
        self.video_path = ""
        self.n_frames = 0
        self.fps = 24.0
        self.vw = self.vh = 0
        self.frame_idx = 0
        self.frame_bgr = None
        self.mask = None
        # the selections; `settings` is the current one (see the property)
        self.segments: List[Dict[str, Any]] = [dict(DEFAULTS, **{"from": 0, "to": 0})]
        self._held_frames: Dict[int, Any] = {}       # freeze frames read, by index
        self._scratch: Dict[str, Any] = dict(DEFAULTS, **{"from": 0, "to": 0})
        self._next_pos: Optional[int] = None         # the frame self.cap reads next (exact)
        self._epoch = 0                              # bumped by reset_all: the window re-reads its controls
        self._cache: Dict[int, Any] = {}             # decoded frames, by index
        self._cache_bytes = 0
        self._cache_lock = threading.Lock()
        self._prefill: Optional[threading.Thread] = None
        self._prefill_stop = threading.Event()
        self._held_masks: Dict[tuple, Any] = {}      # their masks, by frame + settings
        self.current = 0
        self.flat: List[Dict[str, Any]] = []         # flattened selections (Flatten Layers)
        self._flattening = False                     # the render running is a Flatten
        self.mask_only = False
        self.zoomed = False
        self.view = (0, 0, 0, 0)          # x, y, w, h of the frame shown, video px
        self._render_thread: Optional[threading.Thread] = None
        self._stop_render = False

    @property
    def settings(self) -> Dict[str, Any]:
        """The current selection's settings (and range): what the controls edit."""
        if not self.segments:               # none: a scratch one nothing reads
            return self._scratch
        return self.segments[self.current]

    def use_gpu(self) -> bool:
        """The GPU may be used -- for the mask video's encode (NVENC) only --
        unless there is none or a job is running."""
        if self._gpu_failed or not submask_gpu.available():
            return False
        try:
            return not self._job_running()
        except Exception:
            return False

    # ---- exact frames (see "Exact frames" in the module notes) ----------------

    def _cache_put(self, idx: int, frame) -> None:
        with self._cache_lock:
            if idx in self._cache or self._cache_bytes + frame.nbytes > CACHE_BYTES:
                return
            self._cache[idx] = frame
            self._cache_bytes += frame.nbytes

    def _cache_get(self, idx: int):
        with self._cache_lock:
            return self._cache.get(idx)

    def _cache_clear(self) -> None:
        with self._cache_lock:
            self._cache.clear()
            self._cache_bytes = 0

    def _frame(self, idx: int):
        """Frame `idx`, exactly: from the cache, else decoded by reading on
        (self.cap from the start when `idx` is behind it). None past the end."""
        f = self._cache_get(idx)
        if f is not None:
            return f
        if self.cap is None and not self._reopen():
            return None
        if self._next_pos is None or idx < self._next_pos:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = cv2.VideoCapture(self.video_path)
            self._next_pos = 0
        while self._next_pos <= idx:
            ok, f = self.cap.read()
            if not ok:
                self._next_pos = None
                return None
            self._cache_put(self._next_pos, f)
            self._next_pos += 1
        return f

    def _start_prefill(self) -> None:
        """Decode the clip in order into the cache, on a thread of its own with
        its own capture -- not while a job runs (nothing may slow it)."""
        self._stop_prefill()
        try:
            if self._job_running():
                return
        except Exception:
            pass
        self._prefill_stop.clear()
        path, stop = self.video_path, self._prefill_stop

        def run():
            cap = cv2.VideoCapture(path)
            try:
                idx = 0
                while not stop.is_set():
                    ok, f = cap.read()
                    if not ok:
                        break
                    if self._cache_bytes + f.nbytes > CACHE_BYTES:
                        break
                    self._cache_put(idx, f)
                    idx += 1
            finally:
                cap.release()

        self._prefill = threading.Thread(target=run, name="submask-prefill", daemon=True)
        self._prefill.start()

    def _stop_prefill(self) -> None:
        self._prefill_stop.set()
        t, self._prefill = self._prefill, None
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=5)

    def _held_frame(self, idx: int):
        """Frame `idx` of the clip (a freeze frame)."""
        return self._frame(int(idx))

    def seg_mask(self, frame, seg: Dict[str, Any], idx: int, gpu: bool):
        """One selection's mask for frame `idx`: detected on `frame`, or on its
        freeze frame when it has one."""
        mask = None
        if seg.get("hold") is not None:
            # the same frame and settings give the same mask: made once
            key = (int(seg["hold"]),) + tuple(
                tuple(seg[k]) if k == "rect" and seg.get(k) else seg.get(k) for k in SEG_KEYS)
            mask = self._held_masks.get(key)
            if mask is None:
                held = self._held_frame(int(seg["hold"]))
                if held is not None:
                    mask = self._mask(held, seg, gpu)
                    if len(self._held_masks) > 16:
                        self._held_masks.clear()
                    self._held_masks[key] = mask
        if mask is None:
            mask = self._mask(frame, seg, gpu)
        return mask

    def _mask(self, frame, settings, gpu: bool = False):
        """compute_mask, on the CPU (see the module notes; `gpu` is ignored).
        With a rectangle only its area -- plus the reach of R and a Soft
        edge, so the edges inside it come out as on the whole frame -- is
        looked at."""
        rect = settings.get("rect")
        if not rect:
            return compute_mask(frame, settings)
        h, w = frame.shape[:2]
        x0, y0, x1, y1 = (int(v) for v in rect)
        r = max(0, int(settings.get("r", 0) or 0))
        pad = r + (int(np.ceil(3 * max(0.8, r * 0.8))) + 1 if settings.get("edge_mode") == "Soft" else 0) + 2
        cx0, cy0, cx1, cy1 = max(0, x0 - pad), max(0, y0 - pad), min(w, x1 + pad), min(h, y1 + pad)
        if cx1 <= cx0 or cy1 <= cy0:
            m = np.zeros((h, w), np.uint8)
            return 255 - m if settings.get("invert") else m
        sub = dict(settings, rect=(x0 - cx0, y0 - cy0, x1 - cx0, y1 - cy0), invert=False)
        m = np.zeros((h, w), np.uint8)
        m[cy0:cy1, cx0:cx1] = compute_mask(frame[cy0:cy1, cx0:cx1], sub)
        return 255 - m if settings.get("invert") else m

    # ---- opening (v1 open_subtitle_mask_editor + SubtitleMaskEditor.__init__) ----

    def start(self) -> Optional[str]:
        """None, or the message v1 shows instead of opening."""
        pv = self.eng.previewer
        if not getattr(pv, "video_list", None) or pv.current_video_index == -1:
            return "Load a clip in the previewer first."
        entry = pv.video_list[pv.current_video_index]
        source_path = entry.get("source_video")
        if not source_path or not os.path.isfile(source_path):
            return "Current clip has no valid source video path."
        try:
            start_frame = int(float(pv.frame_scrubber_var.get()))
        except Exception:
            start_frame = 0
        sc = self.eng._get_current_sidecar_paths_and_data()
        sidecar_data = sc[2] if sc else {}

        self._release()
        self._stop_prefill()
        self._cache_clear()
        cap = cv2.VideoCapture(source_path)        # read forward only (module notes)
        self._next_pos = 0
        if not cap.isOpened():
            return f"Could not open:\n{source_path}"
        self.cap, self.video_path = cap, source_path
        self.n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
        self.vw = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.vh = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.segments = segments_from_sidecar(sidecar_data, self.n_frames)
        self.flat = flat_from_sidecar(sidecar_data, self.n_frames)
        self._flattening = False
        self.current = 0
        self._held_frames.clear()
        self._held_masks.clear()
        self._start_prefill()
        self.mask_only = False
        self.zoomed = False
        self.frame_bgr = self.mask = None
        self.open = True
        self.seek(max(0, min(self.n_frames - 1, start_frame)))
        return None

    # ---- the frame (v1 seek / update_mask) ------------------------------------------

    def seek(self, idx: int) -> None:
        if self.cap is None and not self._reopen():
            return
        idx = max(0, min(self.n_frames - 1, int(idx)))
        frame = self._frame(idx)
        if frame is None:
            return
        self.frame_idx = idx
        self.frame_bgr = frame
        self._update_mask()

    def step(self, delta: int) -> None:
        self.seek(self.frame_idx + int(delta))

    def _flat_mask(self):
        """The flattened selections covering the frame showing, as Save
        renders them (None: none covers it)."""
        if self.frame_bgr is None:
            return None
        out = None
        for sg in self.flat:
            if sg["from"] <= self.frame_idx <= sg["to"]:
                m = self.seg_mask(self.frame_bgr, sg, self.frame_idx, False)
                out = m if out is None else np.maximum(out, m)
        return out

    def _update_mask(self) -> None:
        base = self._flat_mask()
        if not self.segments:
            self.mask = base
            return
        if self.frame_bgr is not None:
            seg = self.settings
            inside = seg["from"] <= self.frame_idx <= seg["to"]
            if not inside and seg.get("hold") is not None:
                # frozen: nothing outside its range, as Save renders it (user,
                # 2026-10-02: the frozen text stayed on frames it doesn't cover)
                self.mask = base
                return
            # outside an unfrozen one's range: its live detection, to tune by
            m = self.seg_mask(self.frame_bgr, seg, self.frame_idx, self.use_gpu())
            self.mask = m if base is None else np.maximum(base, m)

    # ---- the controls ---------------------------------------------------------

    def set(self, values: Dict[str, Any], mask_only: bool) -> None:
        """Core / Edge / Refine / R / Hard-Soft / colour / Invert (v1
        current_settings), plus the view-only Mask box."""
        s = self.settings
        s["core"] = int(values.get("core", s["core"]))
        s["edge"] = int(values.get("edge", s["edge"]))
        s["refine"] = bool(values.get("refine", s["refine"]))
        s["r"] = max(0, int(values.get("r", s["r"]) or 0))
        s["edge_mode"] = str(values.get("edge_mode", s["edge_mode"]))
        s["color"] = str(values.get("color", s["color"]))
        s["invert"] = bool(values.get("invert", s["invert"]))
        self.mask_only = bool(mask_only)
        self._update_mask()

    def set_rect(self, x0: float, y0: float, x1: float, y1: float) -> None:
        """v1 on_release: the dragged rectangle in video pixels (clamped);
        one under 4 px either way is no rectangle."""
        def cx(v):
            return int(max(0, min(self.vw, v)))

        def cy(v):
            return int(max(0, min(self.vh, v)))
        a, b, c, d = cx(x0), cy(y0), cx(x1), cy(y1)
        rect = (min(a, c), min(b, d), max(a, c), max(b, d))
        if rect[2] - rect[0] < 4 or rect[3] - rect[1] < 4:
            rect = None
        self.settings["rect"] = rect
        self._update_mask()

    # ---- selections (user, 2026-10-02) ----------------------------------------

    def add_segment(self) -> None:
        """A new selection from the frame showing to the end, with the
        current one's settings (rectangle included) to start from."""
        last = max(0, self.n_frames - 1)
        seg = dict(self.settings) if self.segments else dict(DEFAULTS)
        seg["from"], seg["to"] = min(self.frame_idx, last), last
        seg["hold"] = None
        self.segments.append(seg)
        self.current = len(self.segments) - 1
        self._update_mask()

    def remove_segment(self) -> None:
        """The current selection goes -- the last one too (Save then takes
        the clip's mask off)."""
        if not self.segments:
            return
        del self.segments[self.current]
        self.current = max(0, min(self.current, len(self.segments) - 1))
        self._update_mask()

    def select_segment(self, i: int) -> None:
        """Edit selection i; the picture jumps to its first frame when the
        one showing is outside its range."""
        i = int(i)
        if not 0 <= i < len(self.segments):
            return
        self.current = i
        seg = self.settings
        if not seg["from"] <= self.frame_idx <= seg["to"]:
            self.seek(seg["from"])
        else:
            self._update_mask()

    def set_range(self, from_text: str, to_text: str) -> Optional[str]:
        """The current selection's From / To; None, or v1's error."""
        try:
            f0, f1 = int(str(from_text).strip()), int(str(to_text).strip())
        except ValueError:
            return "Bad From/To frame numbers."
        f0, f1 = max(0, f0), min(self.n_frames - 1, f1)
        if f1 < f0:
            return "To must be >= From."
        self.settings["from"], self.settings["to"] = f0, f1
        return None

    def freeze_here(self) -> None:
        """The current selection uses the frame showing's mask throughout."""
        self.settings["hold"] = self.frame_idx
        self._update_mask()

    def unfreeze(self) -> None:
        self.settings["hold"] = None
        self._update_mask()

    def mark_in(self) -> None:
        """The current selection starts at the frame showing (I); its end
        moves along when it would come before it."""
        seg = self.settings
        seg["from"] = self.frame_idx
        seg["to"] = max(seg["to"], self.frame_idx)

    def mark_out(self) -> None:
        """The current selection ends at the frame showing (O)."""
        seg = self.settings
        seg["to"] = self.frame_idx
        seg["from"] = min(seg["from"], self.frame_idx)

    def redraw(self) -> None:
        """Nothing changes; the window is drawn again (after a render ends)."""

    def zoom_rect(self) -> None:
        if self.settings.get("rect"):
            self.zoomed = True

    def fit_view(self) -> None:
        self.zoomed = False

    def clear_rect(self) -> None:
        self.settings["rect"] = None
        self.zoomed = False
        self._update_mask()

    # ---- the picture (v1 redraw, less the canvas scaling Qt does) -----------------

    def render(self) -> Optional[Image.Image]:
        if self.frame_bgr is None:
            return None
        if self.mask_only and self.mask is not None:
            img = cv2.cvtColor(self.mask, cv2.COLOR_GRAY2RGB)
        else:
            img = cv2.cvtColor(self.frame_bgr, cv2.COLOR_BGR2RGB)
            if self.mask is not None:
                # img + (255 - img) * m / 255, in whole numbers and only over
                # the mask's bounding box (was float32 over the whole frame:
                # ~70 ms of each 1080p frame while scrubbing, now ~8)
                rows = np.flatnonzero(self.mask.any(axis=1))
                if rows.size:
                    cols = np.flatnonzero(self.mask.any(axis=0))
                    y0, y1, x0, x1 = rows[0], rows[-1] + 1, cols[0], cols[-1] + 1
                    part = img[y0:y1, x0:x1]
                    m = self.mask[y0:y1, x0:x1, None].astype(np.uint16)
                    img[y0:y1, x0:x1] = part + (((255 - part).astype(np.uint16) * m + 127) // 255).astype(np.uint8)
        rect = self.settings.get("rect")
        if self.zoomed and rect:
            x0, y0, x1, y1 = rect
            x0, y0 = max(0, x0 - ZOOM_PAD), max(0, y0 - ZOOM_PAD)
            x1, y1 = min(self.vw, x1 + ZOOM_PAD), min(self.vh, y1 + ZOOM_PAD)
            img = img[y0:y1, x0:x1]
            self.view = (x0, y0, x1 - x0, y1 - y0)
        else:
            self.view = (0, 0, img.shape[1], img.shape[0])
        return Image.fromarray(np.ascontiguousarray(img))

    def state(self) -> Dict[str, Any]:
        s = self.settings
        rect = s.get("rect")
        return {
            "title": f"Subtitle Mask — {os.path.basename(self.video_path)} — Frame {self.frame_idx}",
            "frame": self.frame_idx, "frames": self.n_frames,
            "vw": self.vw, "vh": self.vh,
            "view": list(self.view),
            "rect": list(rect) if rect else [],
            "zoomed": self.zoomed,
            "core": int(s["core"]), "edge": int(s["edge"]), "refine": bool(s["refine"]), "r": int(s["r"]),
            "edge_mode": str(s["edge_mode"]), "color": str(s["color"]), "invert": bool(s["invert"]),
            "mask_only": self.mask_only,
            "rendering": self._render_thread is not None,
            "segments": [{"from": int(sg["from"]), "to": int(sg["to"]),
                          "hold": -1 if sg.get("hold") is None else int(sg["hold"])}
                         for sg in self.segments],
            "hold": -1 if s.get("hold") is None else int(s["hold"]),
            "flat": [{"from": int(sg["from"]), "to": int(sg["to"])} for sg in self.flat],
            "flattening": self._flattening,
            "current": self.current if self.segments else -1,
            "epoch": self._epoch,
            "from": int(s["from"]), "to": int(s["to"]),
        }

    # ---- Save (v1 save_mask / _render_worker / _render_done) -------------------------

    def save(self, from_text: Optional[str], to_text: Optional[str], on_progress: Callable[[int, int], None],
             on_done: Callable[[str, dict, int, bool, Optional[str]], None]) -> Optional[str]:
        """Starts the mask render of every selection; None, or v1's error.
        From / To, when given, are the current selection's (the boxes)."""
        if self._render_thread:
            return None
        if from_text is not None and to_text is not None:
            bad = self.set_range(from_text, to_text)
            if bad:
                return bad
        if not self.segments and not self.flat:
            return self._remove_mask(on_done)
        segments = [dict(sg) for sg in self.flat + self.segments]
        # The preview holds the clip's previous mask open to draw it; on
        # Windows that open file cannot be replaced, so a second Save failed
        # (WinError 5). Let it go -- the preview opens the new one when it
        # next draws (saved() points it there).
        if self.eng is not None:
            self.eng._preview_submask_reader = None
            self.eng._preview_submask_path = None
            import gc
            gc.collect()
        f0 = min(sg["from"] for sg in segments)
        f1 = max(sg["to"] for sg in segments)
        out_path = submask_output_path(self.video_path)
        self._stop_render = False
        on_progress(0, f1 - f0 + 1)
        gpu = self.use_gpu()
        self._render_thread = threading.Thread(
            target=self._render_worker, args=(f0, f1, out_path, segments, on_progress, on_done, gpu),
            name="splat-submask", daemon=True)
        self._render_thread.start()
        return None

    def flatten(self, from_text: Optional[str], to_text: Optional[str], on_progress, on_done) -> Optional[str]:
        """Flatten Layers: Save, then (saved(), once the render is done) the
        selections are set aside as flattened and the list starts empty."""
        if self._render_thread:
            return None
        if not self.segments:
            return "Nothing to flatten: add a selection first."
        self._flattening = True
        bad = self.save(from_text, to_text, on_progress, on_done)
        if bad:
            self._flattening = False
        return bad

    def _remove_mask(self, on_done) -> Optional[str]:
        """Save with no selections: the clip's mask video goes (the preview's
        hold on it let go first) and on_done reports it, as a render would;
        saved() then turns the mask off in the sidecar."""
        out_path = submask_output_path(self.video_path)
        if self.eng is not None:
            self.eng._preview_submask_reader = None
            self.eng._preview_submask_path = None
            import gc
            gc.collect()
        try:
            if os.path.exists(out_path):
                os.remove(out_path)
        except OSError as e:
            return f"Could not remove {os.path.basename(out_path)}:\n{e}"
        on_done(out_path, [], 0, False, None)
        return None

    def cancel(self) -> None:
        if self._render_thread:
            self._stop_render = True

    def _render_worker(self, f0, f1, out_path, segments, on_progress, on_done, gpu=False) -> None:
        cap = cv2.VideoCapture(self.video_path)
        logger.info(f"[SUBMASK] Rendering; encoding on the {'GPU (NVENC)' if gpu and submask_gpu.nvenc_works() else 'CPU'}")
        # no seek: OpenCV's is inexact on long-GOP clips (module notes); the
        # padding below steps the capture to From frame by frame
        use_ffmpeg = shutil.which("ffmpeg") is not None
        proc = writer = None
        # Rendered under a temporary name and put in place only when complete:
        # a Cancel or an error keeps the clip's previous mask, never a
        # truncated one under its name (audit #5; core/common/partial_output).
        if use_ffmpeg:
            encode_path = part_path(out_path)
            cmd = ["ffmpeg", "-y", "-loglevel", "error",
                   "-f", "rawvideo", "-pix_fmt", "gray",
                   "-s", f"{self.vw}x{self.vh}", "-r", f"{self.fps:.6f}",
                   "-i", "-",
                   *submask_gpu.encoder_args(gpu),
                   "-pix_fmt", "yuv420p", *ffmpeg_output_args(encode_path)]
            proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE,
                creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
        else:
            # OpenCV picks the container from the name, so no .part here: the
            # system temp folder, moved into place at the end
            import tempfile
            fd, encode_path = tempfile.mkstemp(suffix=".mp4", prefix="submask_")
            os.close(fd)
            writer = cv2.VideoWriter(encode_path, cv2.VideoWriter_fourcc(*"mp4v"),
                                     self.fps, (self.vw, self.vh), False)
        n_done = 0
        error = None
        total = f1 - f0 + 1
        try:
            # The renderer indexes the mask by ABSOLUTE clip frame, so a
            # partial range is padded with black before From (v1's comment).
            if f0 > 0:
                black = np.zeros((self.vh, self.vw), np.uint8)
                black_bytes = black.tobytes()
                for _ in range(f0):
                    if self._stop_render:
                        break
                    cap.grab()                      # in step with the padding: exact
                    if proc:
                        proc.stdin.write(black_bytes)
                    else:
                        writer.write(black)
            black = np.zeros((self.vh, self.vw), np.uint8)
            for idx in range(f0, f1 + 1):
                if self._stop_render:
                    break
                covering = [sg for sg in segments if sg["from"] <= idx <= sg["to"]]
                if covering:
                    ok, frame = cap.read()
                    if not ok:
                        break
                    mask = self.seg_mask(frame, covering[0], idx, gpu)
                    for sg in covering[1:]:
                        mask = np.maximum(mask, self.seg_mask(frame, sg, idx, gpu))
                else:                       # a gap between selections: black, no decode
                    if not cap.grab():
                        break
                    mask = black
                if proc:
                    proc.stdin.write(mask.tobytes())
                else:
                    writer.write(mask)
                n_done += 1
                if n_done % 5 == 0 or idx == f1:
                    on_progress(n_done, total)
            if not self._stop_render and n_done < total:
                # the decode ran out before To (a frame count cv2 overstates, a
                # decode miss): a short mask under the real name would be taken
                # as whole, so it is an error and the old mask stays (bug sweep A6)
                error = (f"The clip could only be read to frame {f0 + n_done} of {f1 + 1}, so the mask "
                         f"would be short; the previous mask was kept.")
        except Exception as e:  # pragma: no cover - defensive, as v1
            error = str(e)
        finally:
            cap.release()
            ok = error is None and not self._stop_render
            if proc:
                if ok:
                    try:
                        proc.stdin.close()
                        ok = proc.wait() == 0
                        if not ok:
                            error = f"ffmpeg failed ({proc.returncode})"
                    except Exception as e:
                        ok, error = False, str(e)
                else:
                    reap(proc)
            if writer:
                writer.release()
            if ok and proc:
                if not publish(encode_path, out_path):
                    error = f"Could not write {os.path.basename(out_path)}"
            elif ok:
                try:
                    shutil.move(encode_path, out_path)
                except OSError as e:
                    error = str(e)
            if not ok or error:
                discard(encode_path)
        cancelled = self._stop_render
        if cancelled or error:
            self._flattening = False            # nothing was flattened
        self._render_thread = None
        on_done(out_path, segments, n_done, cancelled, error)

    def reset_all(self) -> str:
        """Reset All (user, 2026-10-02): back to a fresh start for this clip --
        one selection over the whole clip with the default settings, no
        rectangle or freeze -- with the saved settings erased from its
        sidecar and its mask video deleted (the mask off), so nothing of the
        old settings is left. The window confirms first."""
        last = max(0, self.n_frames - 1)
        self.segments = [_clean_segment({"from": 0, "to": last}, self.n_frames)]
        self.flat = []
        self.current = 0
        self.zoomed = False
        self._held_masks.clear()
        self._epoch += 1
        out_path = submask_output_path(self.video_path)
        removed, note = False, ""
        e = self.eng
        if e is not None:                   # the preview holds the old mask open
            e._preview_submask_reader = None
            e._preview_submask_path = None
            import gc
            gc.collect()
        try:
            if os.path.exists(out_path):
                os.remove(out_path)
                removed = True
        except OSError as exc:
            note = f" (could not delete {os.path.basename(out_path)}: {exc})"
        if e is not None:
            # None drops a key from the sidecar (SidecarConfigManager skips them)
            erase = {f"submask_{k}": None for k in SEG_KEYS}
            erase.update({"submask_segments": None, "submask_flat": None, "submask_enabled": False})
            try:
                if not e._save_current_sidecar_data(is_auto_save=True, extra_data=erase):
                    note += " (the sidecar could not be saved)"
            except Exception as exc:
                logger.error(f"[SUBMASK] Reset: sidecar save failed: {exc}", exc_info=True)
                note += " (the sidecar could not be saved)"
            try:
                e.previewer.invalidate_frame_buffer()
                if e.previewer.source_readers:
                    e.previewer.update_preview()
            except Exception:
                logger.debug("Preview re-render after the reset failed", exc_info=True)
        self._update_mask()
        return "Detect Text reset for this clip" + ("; its mask removed" if removed else "") + note + "."

    def saved(self, segments, out_path: str) -> str:
        """v1's on_save, on the worker: the submask_* keys into the clip's
        sidecar through the normal saver, the cached mask reader dropped (a
        re-save rewrites the same path) and the preview re-rendered."""
        e = self.eng
        flattened = self._flattening
        if flattened:                               # Flatten Layers: set aside, the list empty
            self.flat = self.flat + [dict(sg) for sg in self.segments]
            self.segments = []
            self.current = 0
            self._flattening = False
            self._epoch += 1                        # the window re-reads its controls
        try:
            if isinstance(segments, dict):          # one selection's settings (before selections)
                segments = [dict(segments, **{"from": 0, "to": max(0, self.n_frames - 1)})]
            everything = list(self.flat) + list(self.segments) if (self.flat or self.segments) else list(segments)
            if everything:
                submask_data = segments_to_sidecar(everything)
                submask_data["submask_segments"] = segments_to_sidecar(self.segments)["submask_segments"] \
                    if self.segments else ([] if self.flat else submask_data["submask_segments"])
                submask_data["submask_flat"] = segments_to_sidecar(self.flat)["submask_segments"] if self.flat else []
                submask_data["submask_enabled"] = True
            else:                                   # no selections: the mask is off
                submask_data = {"submask_enabled": False, "submask_segments": [], "submask_flat": []}
            if e._save_current_sidecar_data(is_auto_save=True, extra_data=submask_data):
                logger.info("[SUBMASK] Sidecar updated")
            else:
                logger.error("[SUBMASK] Sidecar save failed; the mask will not be picked up")
        except Exception as exc:
            logger.error(f"[SUBMASK] Failed to update sidecar: {exc}", exc_info=True)
        e._preview_submask_path = None
        e._preview_submask_reader = None
        try:
            e.previewer.invalidate_frame_buffer()
        except Exception:
            logger.debug("Preview buffer invalidate after submask save failed", exc_info=True)
        try:
            if e.previewer.source_readers:
                e.previewer.update_preview()
        except Exception:
            logger.debug("Preview re-render after submask save failed", exc_info=True)
        if flattened:
            self._update_mask()
            return f"Flattened: {len(self.flat)} selection(s) kept in {os.path.basename(out_path)}"
        if not segments:
            return f"Subtitle mask removed: {os.path.basename(out_path)}"
        return f"Subtitle mask saved: {os.path.basename(out_path)}"

    # ---- a batch is starting (audit 2026-09-28 #29) ------------------------------

    def suspend(self) -> None:
        """Let go of the clip while a job runs: an open capture holds it, and
        the batch's move to finished/ then fails (WinError 32). The window
        stays open; the next frame asked for opens the clip again. Its frame
        cache (up to 2 GiB) goes too: kept, it held that RAM through the whole
        batch (bug sweep A6); the next frames are read again."""
        self._release()
        self._cache_clear()

    def _reopen(self) -> bool:
        """The clip again after suspend() -- in finished/ if the batch moved it."""
        if not self.open or not self.video_path:
            return False
        folder, name = os.path.split(self.video_path)
        for path in (self.video_path, os.path.join(folder, "finished", name)):
            if os.path.isfile(path):
                cap = cv2.VideoCapture(path)
                self._next_pos = 0
                if cap.isOpened():
                    self.cap, self.video_path = cap, path
                    return True
                cap.release()
        return False

    # ---- closing -------------------------------------------------------------

    def close(self) -> None:
        self.open = False
        self._release()
        self._cache_clear()

    def _release(self) -> None:
        self._stop_prefill()                # its capture holds the clip too
        try:
            if self.cap is not None:
                self.cap.release()
        except Exception:
            pass
        self.cap = None
