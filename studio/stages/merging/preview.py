"""preview.py -- the Merging preview, as QML sees it.

The preview itself is v1's, generated into preview_engine.py (see
studio/tools/gen_merging_preview.py): the clip scan, frame decode, the
blend (mask processing, shadow, colour transfer, borders, the view) and the
Current Clip Info update. This file is only the glue v1's Tk loop was, the
same glue as Splatting's (stages/splatting/preview.py), minus what only
Splatting has -- sidecars, editors, keyframes, the GPU drag path:

* one worker thread runs every preview job in order;
* the engine keeps its own copy of the settings, sent each change as it
  happens (``note_change``); a change that alters the picture re-renders,
  the newest render winning -- while a slider is held, as fast as the
  worker keeps up (v1 waited for the release);
* frames go to QML through ``PreviewImageProvider``
  (``image://mergepreview/frame/<serial>``);
* playback asks for each next frame and waits for it: every frame shown,
  never faster than the clip's frame rate (v1 replayed its buffer unpaced).

Nothing torch-related is imported until the first Load Preview.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import deque
from typing import Any, Dict, Optional

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot
from PySide6.QtGui import QImage

from ..splatting.preview import PreviewImageProvider, _to_qimage
from . import settings as S

logger = logging.getLogger("merging.v2.preview")

#: Settings the picture depends on (v1 re-renders on these: the sliders'
#: release, the Add Borders / Preview Brightness traces, the source and
#: scale boxes). Everything else -- folders, encoding, output, grain (the
#: main preview never shows grain) -- leaves the frame as it is.
RENDER_KEYS = {k for k, *_ in S.MASK_SLIDERS} | {
    "enable_color_transfer", "add_borders", "preview_source", "preview_size", "preview_brightness",
    "mask_highlight", "use_gpu",
}

FOLDER_KEYS = ("inpainted_folder", "original_folder", "mask_folder", "output_folder")

#: What the Grain Match Preview re-renders on (v1: traces on these nine, from
#: either window) -- and the blend settings, which its frame is made of too.
GRAIN_KEYS = {"grain_enable", "grain_amount", "grain_size", "grain_luma_falloff", "grain_chroma", "grain_seed",
              "fill_red_reduce", "fill_brightness", "fill_contrast"}

#: The Grain Match window's ops (grain.py).
GRAIN_OPS = {"seek", "set_zoom", "set_eye", "click", "auto_crop", "sigma", "auto_match", "reset_fill",
             "save_sidecar", "render"}


class _Hooks:
    """What the generated engine reports through. Called on the worker."""

    def __init__(self, owner: "MergingPreview"):
        self._o = owner

    def status(self, text):
        self._o._sig_status.emit(str(text))

    def message(self, kind, title, text):
        self._o._sig_toast.emit(kind, title, text)

    def frame(self, image, scale=1.0):
        # Glasses-Free 3D (studio/sr_display.py): the engine's tagged side-by-side --
        # its left eye is the page's picture, the pair goes to the SR display's weaver
        stereo = image is not None and bool(getattr(image, "info", {}).get("sr3d"))
        if stereo:
            import numpy as np
            from studio import sr_display
            # the engine's own array when nothing was drawn on the image (no copy back out of it)
            arr = image.info.get("sr3d_np")
            if arr is None or arr.shape[:2] != (image.height, image.width):
                arr = np.asarray(image if image.mode == "RGB" else image.convert("RGB"))
            sr_display.offer_frame("merging", arr)
            h, w = arr.shape[0], arr.shape[1] // 2
            # the left eye (ascontiguousarray: a strided slice's tobytes copies pixel by pixel, ~10x slower)
            left = QImage(np.ascontiguousarray(arr[:, :w]).tobytes(), w, h, 3 * w, QImage.Format.Format_RGB888)
            self._o._sig_frame.emit(left, float(scale or 1.0), True)
            return
        self._o._sig_frame.emit(_to_qimage(image), float(scale or 1.0), stereo)

    def wiggle(self, left, right, scale=1.0):
        self._o._sig_wiggle.emit(_to_qimage(left), _to_qimage(right), float(scale or 1.0))

    def loading(self, on):
        self._o._sig_loading.emit(bool(on))

    def nav(self, index, total):
        self._o._sig_nav.emit(int(index), int(total))

    def save_settings(self):
        self._o._sig_save.emit()


class PreviewBlocked(Exception):
    """Load Preview refused, with why (shown as a popup, the preview left as it was)."""

    def __init__(self, title: str, text: str):
        super().__init__(text)
        self.title, self.text = title, text


def _left_eye_text(missing: list, total: int, folder: str) -> str:
    names = [os.path.basename(str(e.get("inpainted") or "")) for e in missing]
    shown = "\n".join("  \u2022 " + n for n in names[:6]) + (f"\n  \u2026 and {len(names) - 6} more" if len(names) > 6 else "")
    where = f"'{folder}'" if folder else "the Original Video Folder (it is empty)"
    return (f"Merging needs each clip's left eye -- its original video -- and it was not found for "
            f"{len(missing)} of {total} clip(s) in {where}:\n\n{shown}\n\n"
            "Point Original Video Folder at the source clips (or pick the chapter again), then pick the clip again.")


class MergingPreview(QObject):
    changed = Signal()
    frameChanged = Signal()
    statusMessage = Signal(str)
    settingsWritten = Signal("QVariantMap")
    infoWritten = Signal("QVariantMap")
    toast = Signal(str, str, str)
    #: v1's settings save (the grain window's _save_grain_prefs): the controller saves.
    saveRequested = Signal()
    #: Save Preview Frame / as SBS: the name v1 suggests, for QML's dialog.
    saveSuggested = Signal(bool, str)
    #: Move to Finish: v1's "Move N file(s)...?" -- QML asks, then moveToFinish(True).
    moveAsked = Signal(str)
    #: The Grain Match Preview: its state after every op / render; would not
    #: open (v1's showinfo); several dual-eye clips to choose between.
    grainChanged = Signal("QVariantMap")
    grainRefused = Signal(str)
    grainChoose = Signal("QVariantList")
    #: v1's messagebox over the grain window (Save Grain to Sidecar's result).
    grainMessage = Signal(str, str)

    # worker -> UI (queued across threads automatically)
    _sig_status = Signal(str)
    _sig_toast = Signal(str, str, str)
    _sig_frame = Signal(object, float, bool)
    _sig_wiggle = Signal(object, object, float)
    _sig_loading = Signal(bool)
    _sig_nav = Signal(int, int)
    _sig_done = Signal(object)
    _sig_save = Signal()
    _sig_saved_name = Signal(bool, str)
    _sig_move_asked = Signal(str)
    _sig_grain = Signal(object)
    _sig_grain_scan = Signal(object)

    #: Playback frames kept for looping (display images).
    PLAY_CACHE_BYTES = 512 * 1024 * 1024

    def __init__(self, values_fn, parent=None):
        super().__init__(parent)
        self._values_fn = values_fn
        self.provider = PreviewImageProvider()
        self._jobs: deque = deque()
        self._cv = threading.Condition()
        self._thread: Optional[threading.Thread] = None
        self._engine = None
        self._pending: Dict[str, Any] = {}
        self._synced = False
        self._dragging = False
        self._playing = False
        self._play_step = 1
        self._play_inflight = False
        self._play_cache: Dict[int, Any] = {}
        self._play_cache_bytes = 0
        self._play_timer = QTimer(self)
        self._play_timer.timeout.connect(self._play_tick)
        self._resume_after_job = False
        self._after_release: deque = deque()     # release_for_job's callbacks, in order
        #: v1's loop toggle (its previewer's own variable, not a Merging setting).
        self._loop = False

        self._loaded = False
        self._loading = False
        self._serial = 0
        self._image_w = 0
        self._image_h = 0
        self._display_scale = 1.0
        self._wiggle = False
        self._stereo = False                # the frame shown is Glasses-Free 3D's left eye
        self._frame = 0
        self._frames = 0
        self._index = -1
        self._count = 0
        self._fps = 0.0
        self._status = ""

        self._sig_status.connect(self._on_status)
        self._sig_toast.connect(self.toast)
        self._sig_frame.connect(self._on_frame)
        self._sig_wiggle.connect(self._on_wiggle)
        self._sig_loading.connect(self._on_loading)
        self._sig_nav.connect(self._on_nav)
        self._sig_done.connect(self._on_done)
        self._sig_save.connect(self.saveRequested)
        self._sig_saved_name.connect(self.saveSuggested)
        self._sig_move_asked.connect(self.moveAsked)
        self._sig_grain.connect(self._on_grain)
        self._sig_grain_scan.connect(lambda top4: self._submit("grain", {"op": "scan_done", "args": [top4]}))
        self._grain = None                  # grain.GrainMatch, made on the worker
        self._grain_open = False
        self._grain_serial = 0
        # Every grain op is numbered; each render reports the newest op it
        # has applied, so the window can tell a stale render (one that left
        # before a slider was let go) from the answer to its own change.
        self._grain_seq = 0
        self._grain_applied = 0          # worker thread
        self._entries: list = []            # the clip list, as of the last job
        # Upcoming Jobs' list: from the folders, not the preview (clip_queue.py)
        from studio.clip_queue import ClipQueue
        self.queue = ClipQueue("merging", values_fn, self)
        QTimer.singleShot(0, self.queue.rescan)
        self._grain_timer = QTimer(self)
        self._grain_timer.setSingleShot(True)
        self._grain_timer.setInterval(220)  # v1 _schedule_render
        self._grain_timer.timeout.connect(lambda: self._submit("grain", {"op": "render", "args": []}))

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(120)
        self._render_timer.timeout.connect(lambda: self._submit("render"))

    # ---- properties -------------------------------------------------------------

    loaded = Property(bool, lambda self: self._loaded, notify=changed)
    loading = Property(bool, lambda self: self._loading, notify=changed)
    imageSerial = Property(int, lambda self: self._serial, notify=frameChanged)
    # the settings generation now, and the one the frame on screen was drawn with
    gen = Property(int, lambda self: getattr(self, "_play_gen", 0), notify=changed)
    frameGen = Property(int, lambda self: getattr(self, "_frame_gen", 0), notify=changed)
    imageWidth = Property(int, lambda self: self._image_w, notify=frameChanged)
    imageHeight = Property(int, lambda self: self._image_h, notify=frameChanged)
    displayScale = Property(float, lambda self: self._display_scale, notify=frameChanged)
    wiggling = Property(bool, lambda self: self._wiggle, notify=frameChanged)
    #: Glasses-Free 3D: the picture is the left eye, the SR display weaves the pair
    stereo3d = Property(bool, lambda self: self._stereo, notify=frameChanged)
    frame = Property(int, lambda self: self._frame, notify=changed)
    frameCount = Property(int, lambda self: self._frames, notify=changed)
    videoIndex = Property(int, lambda self: self._index, notify=changed)
    videoCount = Property(int, lambda self: self._count, notify=changed)
    #: The clip list's inpainted file names, in order (Upcoming Jobs, JobQueue.qml)
    clips = Property("QVariantList", lambda self: [os.path.basename(str((e or {}).get("inpainted") or ""))
                                                   for e in self._entries], notify=changed)
    #: ...and their full paths (the rows' thumbnails, clip_thumbs.py)
    clipPaths = Property("QVariantList", lambda self: [str((e or {}).get("inpainted") or "")
                                                       for e in self._entries], notify=changed)
    #: Upcoming Jobs' own list (clips, clipPaths), from the input folders
    jobClips = Property(QObject, lambda self: self.queue, constant=True)
    playing = Property(bool, lambda self: self._playing, notify=changed)
    fastForward = Property(bool, lambda self: self._playing and self._play_step > 1, notify=changed)
    status = Property(str, lambda self: self._status, notify=changed)
    loop = Property(bool, lambda self: self._loop, notify=changed)

    # ---- from the controller ------------------------------------------------------

    def note_change(self, key: str, value: Any) -> None:
        self._pending[key] = value
        if key in FOLDER_KEYS:
            self.queue.rescan()             # Upcoming Jobs follows the folders
        if self._grain_open and (key in GRAIN_KEYS or key in RENDER_KEYS) and not getattr(self, "_grain_held", False):
            # Held slider: live, as fast as the worker keeps up (grain renders
            # coalesce, newest wins); otherwise v1's 220 ms settle.
            self._grain_timer.start(0 if self._dragging else 220)
        if key not in RENDER_KEYS:
            return
        self._drop_play_cache()
        if self._loaded:
            self._render_timer.start(0 if self._dragging else 120)

    # ---- QML actions --------------------------------------------------------------

    @Slot()
    def load(self) -> None:
        self.queue.rescan()
        self._submit("load")

    @Slot(str)
    def showClip(self, path: str) -> None:
        """Upcoming Jobs' click: that clip in the preview -- a jump when the
        preview holds it, else a load that lands on it."""
        paths = [str((e or {}).get("inpainted") or "") for e in self._entries]
        from ..splatting.preview import same_clip
        path = same_clip(self._entries, path, key="inpainted")                    # however it is spelled
        if self._loaded and path in paths:
            self._submit("jump", paths.index(path))
        else:
            self._submit("load", str(path))

    @Slot()
    def clear(self) -> None:
        self._submit("clear")

    @Slot()
    def refresh(self) -> None:
        self._submit("render")

    @Slot(int)
    def step(self, direction: int) -> None:
        self._submit("nav", int(direction))

    @Slot(int)
    def jumpTo(self, number: int) -> None:
        """v1 _jump_to_video: 1-based."""
        self._submit("jump", int(number) - 1)

    @Slot(int)
    def seek(self, frame: int) -> None:
        self._frame = max(0, int(frame))
        self.changed.emit()
        self._submit("seek", self._frame)

    @Slot(int)
    def stepFrames(self, delta: int) -> None:
        """v1 _jump_frames_by (arrow keys)."""
        if not self._loaded:
            return
        self.pause()
        self.seek(max(0, min(self._frames - 1, self._frame + int(delta))))

    @Slot(bool)
    def setDragging(self, on: bool) -> None:
        """A slider was grabbed or let go: while held, each change renders at
        once (newest wins) instead of after the settle delay."""
        on = bool(on)
        if on == self._dragging:
            return
        self._dragging = on
        if not on and self._loaded and self._render_timer.isActive():
            self._render_timer.stop()
            self._submit("render")
        if not on and self._grain_open and self._grain_timer.isActive():
            self._grain_timer.stop()
            self._submit("grain", {"op": "render", "args": []})

    # ---- phase 4: File > Save Preview, Move to Finish, the current clip --------------

    @Slot(bool)
    def suggestSaveName(self, sbs: bool) -> None:
        """v1 save_preview_frame / _save_preview_sbs_frame up to their dialog:
        saveSuggested(sbs, name) follows (or v1's warning)."""
        self._submit("save", {"sbs": bool(sbs), "path": ""})

    @Slot(bool, str)
    def saveTo(self, sbs: bool, path: str) -> None:
        self._submit("save", {"sbs": bool(sbs), "path": str(path)})

    @Slot(bool)
    def moveToFinish(self, confirmed: bool) -> None:
        """v1 _move_current_to_finish. Unconfirmed, it stops at v1's question
        (moveAsked follows); confirmed, it moves."""
        self.pause()
        self._submit("move", bool(confirmed))

    def current_entries(self):
        """The clip list and the index on screen, as of the last job."""
        return list(self._entries), (self._index if self._loaded else -1)

    # ---- the Grain Match Preview (grain.py) -------------------------------------------

    @Slot(int)
    def openGrain(self, pick: int = -1) -> None:
        """v1 open_grain_preview. `pick`: the chosen dual-eye clip, when
        grainChoose asked."""
        self._submit("grain", {"op": "open", "args": [int(pick)]})

    @Slot(str, "QVariantList", result=int)
    def grainOp(self, op: str, args) -> int:
        """Returns the op's number (0 when the window is not open)."""
        if not self._grain_open:
            return 0
        self._grain_seq += 1
        self._submit("grain", {"op": str(op), "args": list(args or []), "seq": self._grain_seq})
        return self._grain_seq

    @Slot()
    def closeGrain(self) -> None:
        self._grain_timer.stop()
        if self._grain_open:
            self._grain_open = False
            self._submit("grain", {"op": "close", "args": []})

    # ---- playback -------------------------------------------------------------------

    @Slot()
    def toggleLoop(self) -> None:
        """v1 _toggle_loop."""
        self._loop = not self._loop
        self.changed.emit()

    @Slot()
    def togglePlay(self) -> None:
        self._set_playing(not (self._playing and self._play_step == 1), 1)

    @Slot(int)
    def toggleFastForward(self, step: int) -> None:
        step = max(2, int(step or 5))
        self._set_playing(not (self._playing and self._play_step > 1), step)

    @Slot()
    def pause(self) -> None:
        if self._playing:
            self._set_playing(False, 1)

    def _set_playing(self, on: bool, step: int) -> None:
        if on and not self._loaded:
            return
        self._playing, self._play_step = bool(on), int(step)
        if on:
            fps = self._fps if self._fps > 0 else 24.0
            self._play_timer.start(max(5, int(round(1000.0 / fps))))
        else:
            self._play_timer.stop()
        self.changed.emit()

    def _play_tick(self) -> None:
        if not self._playing or self._play_inflight:
            return
        last = max(0, self._frames - 1)
        nxt = self._frame + self._play_step
        if nxt > last:
            if self._loop and last > 0:
                nxt = 0
            else:
                self._set_playing(False, 1)
                return
        self._frame = nxt
        cached = self._play_cache.get(nxt)
        if cached is not None:
            self.provider.put("frame", cached)
            self._serial += 1
            self.frameChanged.emit()
            self.changed.emit()
            return
        self._play_inflight = True
        self._play_gen_sent = getattr(self, "_play_gen", 0)
        self.changed.emit()
        self._submit("play", nxt)

    def _drop_play_cache(self) -> None:
        self._play_cache.clear()
        self._play_cache_bytes = 0
        # a play frame already on the worker was drawn with what came before: don't cache
        # it after the drop, or it replays the old look every loop (bug sweep 2026-10-09, A8)
        self._play_gen = getattr(self, "_play_gen", 0) + 1

    # ---- around a batch -------------------------------------------------------------

    def release_for_job(self, then) -> None:
        """Before a batch: close the readers and give back the preview's GPU
        memory (v1 _clear_preview_resources), THEN call `then` on the UI
        thread. A preview that was showing comes back in restore_after_job."""
        self.pause()
        self._resume_after_job = self._loaded
        # Grain Match stays open but draws nothing until the job is over
        # (audit 2026-09-28, minor: it kept rendering on the GPU mid-job)
        self._grain_held = True
        self._grain_timer.stop()
        if self._engine is None and self._worker_idle():
            then()
            return
        self._after_release.append(then)
        self._submit("release")

    def _worker_idle(self) -> bool:
        """Nothing queued and nothing running on the worker."""
        with self._cv:
            return not self._jobs and not getattr(self, "_inflight", False)

    def restore_after_job(self) -> None:
        if getattr(self, "_grain_held", False):
            self._grain_held = False
            if self._grain_open:
                self._submit("grain", {"op": "render", "args": []})   # what changed meanwhile
        if self._resume_after_job:
            self._resume_after_job = False
            self._submit("load")      # v1 load_video_list returns to the last clip and frame

    def shutdown(self) -> None:
        self._play_timer.stop()
        with self._cv:
            self._jobs.clear()
            self._jobs.append(None)
            self._cv.notify()
        if self._thread is not None:
            self._thread.join(timeout=5)
        eng = self._engine
        if eng is not None and (self._thread is None or not self._thread.is_alive()):
            try:
                eng.previewer._clear_preview_resources()
            except Exception:
                logger.debug("Preview readers not closed at shutdown", exc_info=True)

    # ---- the worker -------------------------------------------------------------

    def _submit(self, kind: str, arg: Any = None) -> None:
        # With no engine there is nothing to clear -- unless a job that is
        # about to build one (the first load, importing) is queued or running:
        # then the release goes behind it (audit #14).
        if kind in ("clear", "release") and self._engine is None and self._worker_idle():
            if kind == "release":
                self._finish_release()      # nothing to let go of: its caller carries on (#14)
            return
        if not self._synced:
            changes, self._synced = dict(self._values_fn()), True
        else:
            changes = self._pending
        self._pending = {}
        with self._cv:
            # Newest render/seek wins; the dropped ones' changes are kept.
            if kind in ("render", "seek"):
                kept = deque()
                for job in self._jobs:
                    if job is not None and job[0] in ("render", "seek"):
                        merged = dict(job[2])
                        merged.update(changes)
                        changes = merged
                    else:
                        kept.append(job)
                self._jobs = kept
            # The grain window: only the newest render / seek / sigma is worth
            # doing. A sigma op renders too, so a queued render and a sigma
            # fold into one job (the sigma's, keeping the newest number).
            if kind == "grain" and arg.get("op") in ("render", "seek", "sigma"):
                same = ("render", "sigma") if arg.get("op") in ("render", "sigma") else (arg.get("op"),)
                kept = deque()
                for job in self._jobs:
                    if job is not None and job[0] == "grain" and job[1].get("op") in same:
                        merged = dict(job[2])
                        merged.update(changes)
                        changes = merged
                        if arg.get("op") == "render" and job[1].get("op") == "sigma":
                            arg = dict(job[1], seq=max(int(job[1].get("seq") or 0), int(arg.get("seq") or 0)))
                    else:
                        kept.append(job)
                self._jobs = kept
            # the settings generation it is drawn with (bug sweep 2026-10-09, A8: the flip card)
            self._jobs.append((kind, arg, changes, getattr(self, "_play_gen", 0)))
            self._cv.notify()
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="merge-preview", daemon=True)
            self._thread.start()
        if kind in ("load", "nav", "jump"):
            self._loading = True
            self.changed.emit()

    def _run(self) -> None:
        while True:
            with self._cv:
                self._inflight = False
                while not self._jobs:
                    self._cv.wait()
                job = self._jobs.popleft()
                self._inflight = job is not None
            if job is None:
                return
            kind, arg, changes, gen = job
            try:
                result = self._do(kind, arg, changes)
                result["gen"] = gen
            except PreviewBlocked as e:
                result = {"error": e.text, "title": e.title, "kind": kind}
            except Exception as e:  # never let one job kill the thread
                logger.error("Preview %s failed: %s", kind, e, exc_info=True)
                result = {"error": str(e), "kind": kind}
            self._sig_done.emit(result)

    def _ensure_engine(self):
        if self._engine is None:
            from .preview_engine import MergingPreviewEngine
            self._engine = MergingPreviewEngine(list(S.DEFAULTS), _Hooks(self))
        return self._engine

    def _do(self, kind: str, arg: Any, changes: Dict[str, Any]) -> Dict[str, Any]:
        eng = self._ensure_engine()
        eng.store.values.update(changes)
        pv = eng.previewer
        if kind == "load":
            # v1 _handle_load_refresh: re-scan the folders, then load.
            found = eng._find_preview_sources_callback()
            # A dual-splatted clip's left eye is its original video: without it
            # the preview showed the right eye alone (user, 2026-09-27) -- refuse.
            missing = [e for e in found if not e.get("is_quad_input") and not e.get("is_dual_eye_sbs")
                       and not e.get("original")]
            if missing:
                # Only those clips are left out (audit 2026-09-28, minor: one
                # blocked the preview of every clip) -- refused outright when
                # none is left, or the clip picked is one of them.
                def _is(e, want):
                    return any(os.path.normcase(os.path.abspath(str(e.get(k) or ""))) == want
                               for k in ("inpainted", "source_video") if e.get(k))
                want = os.path.normcase(os.path.abspath(str(arg))) if arg else None
                text = _left_eye_text(missing, len(found), eng.original_folder_var.get())
                if len(missing) == len(found) or (want and any(_is(e, want) for e in missing)):
                    raise PreviewBlocked("Left eye not found", text)
                found = [e for e in found if not any(e is m for m in missing)]
                self._sig_toast.emit("warning", "Left eye not found",
                                     text.replace("then pick the clip again.", "then load the preview again.")
                                     + "\n\nThe preview shows the other clips.")
            if arg:                         # Upcoming Jobs' click: land on that clip
                # the list's own spelling of it (v1 compares paths as strings)
                from ..splatting.preview import same_clip
                arg = same_clip(found, arg)
                if pv.last_loaded_video_path != arg:
                    pv.last_loaded_frame_index = 0
                    # close the clip shown first: v1's loader keeps the open
                    # readers when the path it lands on equals
                    # last_loaded_video_path -- set just below -- so a clip from
                    # another chapter showed the old clip (user, 2026-09-27)
                    pv._clear_preview_resources()
                pv.last_loaded_video_path = arg
            pv.load_video_list(lambda: found)
        elif kind in ("clear", "release"):
            pv._clear_preview_resources()
            from core.removal import putback
            putback.close_all()                # its readers hold the clip and its original (bug sweep #7)
            pv.video_list = []
            pv.current_video_index = -1
            eng.preview_original_left_tensor = None
            eng.preview_blended_right_tensor = None
            if self._grain is not None:
                self._grain._before = None     # Grain Match's GPU cache; the next render rebuilds it
                if kind == "release" and self._grain.open:
                    self._grain.release_files()   # its readers hold the clip (bug sweep 2026-10-09, A8)
            try:
                import torch
                if torch.cuda.is_available():
                    clear = getattr(torch._C, "_cuda_clearCublasWorkspaces", None)
                    if clear is not None:
                        clear()
                from core.common.gpu_utils import release_cuda_memory
                release_cuda_memory()
            except Exception:
                logger.debug("Could not release CUDA memory", exc_info=True)
        elif kind == "render":
            if pv.source_readers:
                pv.update_preview()
        elif kind in ("seek", "play"):
            if pv.source_readers:
                pv.frame_scrubber_var.set(int(arg))
                pv.on_scrubber_move(int(arg))
                pv.update_preview()
        elif kind in ("nav", "jump"):
            if pv.video_list:
                pv._nav_preview_video_apply(int(arg), absolute=(kind == "jump"))
        elif kind == "save":
            self._save(eng, pv, arg)
        elif kind == "move":
            self._move(eng, pv, bool(arg))
        elif kind == "grain":
            self._grain_applied = max(self._grain_applied, int(arg.get("seq") or 0))
            self._grain_job(eng, str(arg["op"]), list(arg.get("args") or []))
        return {
            "kind": kind,
            "writes": eng.store.take_writes(),
            "info": eng.info.take_writes(),
            "loaded": bool(pv.source_readers),
            "frame": int(pv.frame_scrubber_var.get() or 0),
            "frames": int(pv.frame_scrubber.cget("to") or 0) + 1 if pv.source_readers else 0,
            "index": int(pv.current_video_index),
            "count": len(pv.video_list),
            "fps": self._reader_fps(pv),
            "entries": [dict(e) for e in pv.video_list],
        }

    def _save(self, eng, pv, arg) -> None:
        from . import preview_engine as PE
        PE.filedialog.answer, PE.filedialog.suggested = arg["path"], ""
        try:
            if arg["sbs"]:
                self._render_eyes(eng, pv)
                eng._save_preview_sbs_frame()
            else:
                pv.save_preview_frame()
        finally:
            PE.filedialog.answer = ""
        if not arg["path"] and PE.filedialog.suggested:
            self._sig_saved_name.emit(arg["sbs"], PE.filedialog.suggested)

    @staticmethod
    def _render_eyes(eng, pv) -> None:
        """Save as SBS's own render of the frame on screen (bug sweep 2026-10-09,
        A8): the blend, past the frame cache, whatever view is showing; only this
        render keeps the two eyes. The view is swapped in the store directly, so
        nothing is written back or re-rendered for it."""
        store, key = eng.preview_source_var._store, eng.preview_source_var._key
        shown = store.values.get(key)
        eng.preview_original_left_tensor = eng.preview_blended_right_tensor = None
        eng._capture_sbs_eyes = True
        store.values[key] = "Blended Image"
        try:
            pv._process_frame_for_preview(int(pv.frame_scrubber_var.get()), source_frames={})
        finally:
            store.values[key] = shown
            eng._capture_sbs_eyes = False

    def _move(self, eng, pv, confirmed: bool) -> None:
        """v1 _move_current_to_finish. The first pass stops at v1's question:
        v1 has already let go of the clip by then (it closes its readers to
        free the files), so the clip is loaded again while QML asks."""
        from . import preview_engine as PE
        asked = []
        real = PE.messagebox.askyesno

        def askyesno(title="", message="", **_kw):
            asked.append(str(message))
            return confirmed
        PE.messagebox.askyesno = askyesno
        try:
            eng._move_current_to_finish()
        finally:
            PE.messagebox.askyesno = real
        if asked and not confirmed:
            if 0 <= pv.current_video_index < len(pv.video_list) and not pv.source_readers:
                pv._load_preview_by_index(pv.current_video_index)
            self._sig_move_asked.emit(asked[0])

    def _grain_job(self, eng, op: str, args: list) -> None:
        from .grain import GrainMatch
        if op == "open":
            if self._grain is not None and self._grain.open:
                self._grain.close()
            self._grain = GrainMatch(eng, lambda top4: self._sig_grain_scan.emit(top4))
            out = self._grain.start(None if args[0] < 0 else args[0])
            if out:
                self._sig_grain.emit(out)
                return
        g = self._grain
        if g is None or not g.open:
            return
        if op == "close":
            g.close()
            self._sig_grain.emit({"open": False})
            return
        if op == "scan_done":
            g.scan_done(args[0])
        elif op == "save_sidecar":
            kind, text = g.save_sidecar()
            self._sig_grain.emit({"message": [kind, text]})
            return
        elif op not in ("open", "render"):
            if op not in GRAIN_OPS:
                raise ValueError(f"grain: no op {op!r}")
            if getattr(self, "_grain_held", False) and op in ("auto_crop", "auto_match", "reset_fill"):
                self._sig_grain.emit({"message": ["info", "Grain Match waits for the job to finish."]})
                return
            getattr(g, op)(*args)
        if getattr(self, "_grain_held", False):
            return          # a job has the GPU: restore_after_job renders what changed
        if getattr(g, "_files_released", False) and not all(
                p and os.path.exists(p) for p in (g.inpainted_path, g.splatted_path)):
            # the batch moved this clip into finished/ while the window let go of it
            g.close()
            self._sig_grain.emit({"message": ["info", "Grain Match closed: the batch moved its clip to finished/."]})
            self._sig_grain.emit({"open": False})
            return
        g.render()
        images = {name: _to_qimage(im) for name, im in g.images.items()}
        self._sig_grain.emit({"open": True, "state": dict(g.state(), seq=self._grain_applied), "images": images})

    @staticmethod
    def _reader_fps(pv) -> float:
        for key in ("inpainted", "source_video", "splatted"):
            reader = pv.source_readers.get(key) if pv.source_readers else None
            if reader is None:
                continue
            try:
                return float(reader.get_avg_fps())
            except Exception:
                continue
        return 0.0

    # ---- worker -> UI -------------------------------------------------------------

    def _on_grain(self, out) -> None:
        if "message" in out:
            self.grainMessage.emit(*out["message"])
            self.queue.refreshFx()          # a sidecar may have been saved: the list's badge
            return
        if "refused" in out or "alert" in out:
            self.grainRefused.emit(out.get("refused") or out.get("alert"))
            return
        if "choose" in out:
            self.grainChoose.emit(out["choose"])
            return
        if not out.get("open"):
            self._grain_open = False
            for name in ("full", "master", "before", "after"):
                self.provider.put("grain_" + name, None)
            self.grainChanged.emit({"open": False})
            return
        self._grain_open = True
        for name, image in out["images"].items():
            self.provider.put("grain_" + name, image)
        self._grain_serial += 1
        state = dict(out["state"])
        state["open"] = True
        state["serial"] = self._grain_serial
        self.grainChanged.emit(state)

    def _on_status(self, text: str) -> None:
        self._status = text
        self.changed.emit()
        self.statusMessage.emit(text)

    def _on_frame(self, image, scale: float = 1.0, stereo: bool = False) -> None:
        self._display_scale = scale if scale > 0 else 1.0
        self.provider.put("frame", image)
        self._wiggle = False
        self._stereo = bool(stereo and image is not None)
        self._image_w = image.width() if image is not None else 0
        self._image_h = image.height() if image is not None else 0
        self._serial += 1
        self.frameChanged.emit()

    def _on_wiggle(self, left, right, scale: float = 1.0) -> None:
        if left is None:
            if self._wiggle:
                self._wiggle = False
                self.frameChanged.emit()
            return
        self.provider.put("left", left)
        self.provider.put("right", right)
        self._wiggle = True
        self._stereo = False
        self._display_scale = scale if scale > 0 else 1.0
        self._image_w, self._image_h = left.width(), left.height()
        self._serial += 1
        self.frameChanged.emit()

    def _on_loading(self, on: bool) -> None:
        self._loading = on
        self.changed.emit()

    def _on_nav(self, index: int, total: int) -> None:
        self._index, self._count = index, total
        self.changed.emit()

    def _on_done(self, result: Dict[str, Any]) -> None:
        kind = result.get("kind")
        if "error" in result:
            self._loading = False
            self._play_inflight = False
            self.toast.emit("error", result.get("title") or "Preview failed", result["error"])
            self.changed.emit()
            if kind == "release":
                self._finish_release()
            return
        if result["writes"]:
            self.settingsWritten.emit(result["writes"])
        if result["info"]:
            self.infoWritten.emit(result["info"])
        self._loaded = result["loaded"]
        self._fps = float(result.get("fps") or 0.0)
        self._entries = result.get("entries") or []
        if kind in ("render", "seek", "play", "load", "nav", "jump"):
            # the settings the frame on screen was drawn with: the flip card lands on a
            # frame from after its swap, not a play frame already in flight (A8)
            self._frame_gen = int(result.get("gen", 0))
        if kind == "play":
            self._play_inflight = False
            image = self.provider._images.get("frame")
            fresh = getattr(self, "_play_gen_sent", 0) == getattr(self, "_play_gen", 0)
            if image is not None and self._playing and not self._wiggle and not self._stereo and fresh:
                size = image.sizeInBytes()
                if self._play_cache_bytes + size <= self.PLAY_CACHE_BYTES:
                    self._play_cache[int(result["frame"])] = image
                    self._play_cache_bytes += size
        if kind in ("load", "nav", "jump", "clear", "release"):
            self._drop_play_cache()
            self._loading = False
            if kind != "load" and self._playing:
                self._set_playing(False, 1)
        self._frame = result["frame"]
        self._frames = result["frames"]
        self._index = result["index"]
        self._count = result["count"]
        if not self._loaded:
            self.provider.put("frame", None)
            self._image_w = self._image_h = 0
            self._wiggle = False
            self._stereo = False
            self._serial += 1
            self.frameChanged.emit()
        self.changed.emit()
        if kind == "release":
            self._finish_release()

    def _finish_release(self) -> None:
        """A release job is done: its caller's callback, first in first out --
        each release_for_job queued exactly one release, and the worker runs
        them in order (bug sweep 2026-10-09 #14: one slot, so a second
        request overwrote the first and its Start / Move never ran)."""
        if self._after_release:
            then = self._after_release.popleft()
            if then is not None:
                then()
