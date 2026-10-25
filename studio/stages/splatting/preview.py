"""preview.py -- the Splatting preview, as QML sees it.

The preview itself is v1's, generated into preview_engine.py (see
studio/tools/gen_splatting_preview.py): clip discovery, frame decode,
the sidecar sync on clip load, the ~1,000-line frame render and v1's overlays.
This file is only the glue v1's Tk event loop was:

* one worker thread runs every preview job in order -- v1 ran them all on
  its Tk thread, so nothing in that code is written for concurrency;
* the engine keeps its own copy of the settings. The controller sends it
  each change as it happens (``note_change``), and what the engine writes
  back -- a clip's sidecar values loaded into the sliders, a clip load
  zeroing Pop Out Boost -- comes back through ``settingsWritten`` for the
  controller to apply without re-rendering;
* renders are coalesced: only the newest queued render or seek runs;
* finished frames go to QML through ``PreviewImageProvider``
  (``image://splatpreview/<serial>``), bumping ``imageSerial`` each time.

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
from PySide6.QtQuick import QQuickImageProvider

from . import settings as S

logger = logging.getLogger("splatting.v2.preview")

#: Settings that never change the picture: no re-render when they move.
#: (Folders change WHICH clips there are -- that is a reload, not a render.)
NO_RENDER = set(S.FOLDER_KEYS) | {
    "batch_size", "low_res_batch_size", "enable_full_res", "enable_low_res", "dual_output",
    "process_length", "custom_clip_range", "process_from", "process_to", "output_settings_shown",
    "dev_tools_shown", "move_to_finished", "multi_map", "update_slider_from_sidecar",
    "auto_save_sidecar", "codec", "encoder", "encoding_quality",
    "encoding_tune", "bit_depth", "pix_fmt", "color_tags_mode", "encoding_nvenc_lookahead_enabled",
    "encoding_nvenc_lookahead", "encoding_nvenc_spatial_aq", "encoding_nvenc_temporal_aq",
    "encoding_nvenc_aq_strength", "dnxhr_fullres_split", "dnxhr_profile", "pre_res_height",
    "batch_size", "strict_ffmpeg_decode",
    # nor these: toggling one dropped up to 768 MB of played frames and re-rendered (bug sweep A6)
    "loop_playback", "debug_logging", "debug_mode",
}

#: The crosshair is an overlay on the finished frame: v1 redraws it through
#: _apply_preview_overlay_toggles rather than a render.
OVERLAY_KEYS = {"crosshair_enabled", "crosshair_white", "crosshair_multi"}


class PreviewImageProvider(QQuickImageProvider):
    """Serves the newest preview frame (and the wigglegram's two eyes)."""

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._images: Dict[str, QImage] = {}
        self._lock = threading.Lock()

    def put(self, name: str, image: Optional[QImage]) -> None:
        with self._lock:
            if image is None:
                self._images.pop(name, None)
            else:
                self._images[name] = image

    def requestImage(self, image_id, size, requested_size):  # noqa: N802 (Qt API)
        name = str(image_id).split("/", 1)[0]
        with self._lock:
            image = self._images.get(name)
        if image is None:
            image = QImage(1, 1, QImage.Format.Format_ARGB32)
            image.fill(0)
        if size is not None:
            size.setWidth(image.width())
            size.setHeight(image.height())
        return image


def same_clip(entries, path: str, key: str = "source_video") -> str:
    """`path` as the preview's own list spells it (slashes, case), or `path`
    itself when the list does not have it. v1's loader looks a clip up by
    plain string equality."""
    want = os.path.normcase(os.path.abspath(str(path)))
    for e in entries or []:
        p = str((e or {}).get(key) or "")
        if p and os.path.normcase(os.path.abspath(p)) == want:
            return p
    return str(path)


def _left_overlay_hook(overlay, flip: bool):
    """The Depth Map Editor's masks over the 3D view's LEFT eye (render_external; user, 2026-10-25):
    `overlay` = (colour HxWx3, strength HxW), float 0..1 at the frame's size -- fitted to the eye the
    preview made, mirrored when the clip is. None: no hook."""
    if overlay is None:
        return None
    colour, alpha = overlay

    def hook(left):
        import numpy as np
        import torch
        import torch.nn.functional as F
        if left is None or left.dim() != 4:
            return left
        h, w = left.shape[-2:]
        c = torch.from_numpy(np.ascontiguousarray(colour, np.float32)).to(left.device, left.dtype).permute(2, 0, 1)[None]
        a = torch.from_numpy(np.ascontiguousarray(alpha, np.float32)).to(left.device, left.dtype)[None, None]
        if tuple(c.shape[-2:]) != (h, w):
            c = F.interpolate(c, size=(h, w), mode="bilinear", align_corners=False)
            a = F.interpolate(a, size=(h, w), mode="bilinear", align_corners=False)
        if flip:
            c, a = torch.flip(c, dims=[3]), torch.flip(a, dims=[3])
        return left * (1.0 - a) + c * a
    return hook


def _to_qimage(pil_image) -> Optional[QImage]:
    if pil_image is None:
        return None
    img = pil_image if pil_image.mode == "RGB" else pil_image.convert("RGB")
    data = img.tobytes("raw", "RGB")
    # No .copy() (Studio audit 2026-09-28, PERF: each frame was copied twice,
    # ~1.2 ms of 3.5 at 1080p): PySide6's QImage holds a reference to `data`
    # until its last (shallow) copy is gone, and `bytes` is never written --
    # nothing here modifies a served frame.
    return QImage(data, img.width, img.height, 3 * img.width, QImage.Format.Format_RGB888)


class _Hooks:
    """What the generated engine reports through. Called on the worker."""

    def __init__(self, owner: "SplattingPreview"):
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
            sr_display.offer_frame("splatting", arr)
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

    def sidecar(self, exists):
        self._o._sig_sidecar.emit(bool(exists))

    def keyframes(self):
        pass

    def rerender(self):
        # From inside a job (v1's on_slider_release after a sidecar sync): the
        # job that asked is about to render anyway, or is a load that renders.
        pass


class SplattingPreview(QObject):
    changed = Signal()
    frameChanged = Signal()
    statusMessage = Signal(str)            # v1's status label
    singleReady = Signal("QVariantMap")    # SINGLE: the clip's paths, the test setup
    autoConvRunning = Signal(bool)
    settingsWritten = Signal("QVariantMap")
    toast = Signal(str, str, str)
    #: Hole Shift / Detect Text ("holeshift" | "submask"): the editor's
    #: state after every op and every preview render while it is open.
    editorChanged = Signal(str, "QVariantMap")
    #: The editor would not open (v1's showinfo instead of the window).
    editorRefused = Signal(str, str)
    #: Detect Text: v1's showerror over the editor window (bad From/To, a
    #: failed render), and the mask render's progress (value, maximum, status).
    editorAlert = Signal(str, str)
    editorProgress = Signal(str, int, int, str)

    # worker -> UI (queued across threads automatically)
    _sig_status = Signal(str)
    _sig_toast = Signal(str, str, str)
    _sig_frame = Signal(object, float, bool)
    _sig_wiggle = Signal(object, object, float)
    _sig_loading = Signal(bool)
    _sig_nav = Signal(int, int)
    _sig_sidecar = Signal(bool)
    _sig_done = Signal(object)
    _sig_editor = Signal(str, object)
    _sig_submask_done = Signal(object)

    def __init__(self, values_fn, parent=None):
        super().__init__(parent)
        self._values_fn = values_fn         # the controller's current settings
        self.provider = PreviewImageProvider()
        self._jobs: deque = deque()
        self._after_release: deque = deque()     # release_for_job's callbacks, in order
        self._cv = threading.Condition()
        self._thread: Optional[threading.Thread] = None
        self._engine = None
        self._pending: Dict[str, Any] = {}  # changes the engine has not seen yet
        self._dragging = False              # a slider is held: renders are fast ones
        # playback (v1 _start_playback / _playback_tick, paced here)
        self._playing = False
        self._play_step = 1
        self._play_inflight = False
        self._fps = 0.0
        self._play_cache: Dict[int, QImage] = {}
        self._play_cache_bytes = 0
        self._play_cache_gen = 0            # bumped whenever the cache is dropped
        self._play_gen = -1                 # ...as it stood when the play frame in flight was asked for
        self._play_timer = QTimer(self)
        self._play_timer.timeout.connect(self._play_tick)
        self._synced = False                # has the engine had a full snapshot

        # state QML reads
        self._loaded = False
        self._loading = False
        self._serial = 0
        self._image_w = 0
        self._image_h = 0
        self._display_scale = 1.0
        self._wiggle = False
        self._stereo = False                # the frame shown is Glasses-Free 3D's left eye
        self._frame = 0
        # the clip's keyframes, a copy on this thread: the dials show the values at the frame on
        # screen, playing from the cache too (user, 2026-10-11: as the Depth Map Editor does)
        self._kf_track = None
        self._kf_rev = 0
        self._kf_cache = None
        self._frames = 0
        self._index = -1
        self._count = 0
        self._clip_paths: list = []
        # Upcoming Jobs' list: from the folders, not the preview (clip_queue.py)
        from studio.clip_queue import ClipQueue
        self.queue = ClipQueue("splatting", values_fn, self)
        QTimer.singleShot(0, self.queue.rescan)
        self._sidecar = False
        self._status = ""

        self._sig_status.connect(self._on_status)
        self._sig_toast.connect(self.toast)
        self._sig_frame.connect(self._on_frame)
        self._sig_wiggle.connect(self._on_wiggle)
        self._sig_loading.connect(self._on_loading)
        self._sig_nav.connect(self._on_nav)
        self._sig_sidecar.connect(self._on_sidecar)
        self._sig_done.connect(self._on_done)
        self._sig_editor.connect(self._on_editor)
        self._sig_submask_done.connect(self._on_submask_done)
        # Hole Shift (editors.py) and Detect Text (submask.py), made on the worker on
        # first open; the ones a render has left stale since they last drew.
        self._editors: Dict[str, Any] = {}
        self._editors_stale: set = set()

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(120)
        self._render_timer.timeout.connect(lambda: self._submit("render"))
        self._overlay_timer = QTimer(self)
        self._overlay_timer.setSingleShot(True)
        self._overlay_timer.setInterval(30)
        self._overlay_timer.timeout.connect(lambda: self._submit("overlay"))

    # ---- properties -----------------------------------------------------------

    loaded = Property(bool, lambda self: self._loaded, notify=changed)
    loading = Property(bool, lambda self: self._loading, notify=changed)
    imageSerial = Property(int, lambda self: self._serial, notify=frameChanged)
    imageWidth = Property(int, lambda self: self._image_w, notify=frameChanged)
    imageHeight = Property(int, lambda self: self._image_h, notify=frameChanged)
    #: Preview Scale, applied by Qt as it draws the frame (display only).
    displayScale = Property(float, lambda self: self._display_scale, notify=frameChanged)
    wiggling = Property(bool, lambda self: self._wiggle, notify=frameChanged)
    #: Glasses-Free 3D: the picture is the left eye, the SR display weaves the pair
    stereo3d = Property(bool, lambda self: self._stereo, notify=frameChanged)
    frame = Property(int, lambda self: self._frame, notify=changed)
    frameCount = Property(int, lambda self: self._frames, notify=changed)
    videoIndex = Property(int, lambda self: self._index, notify=changed)
    videoCount = Property(int, lambda self: self._count, notify=changed)
    #: The clip list's source clips: names and full paths (Upcoming Jobs)
    clips = Property("QVariantList", lambda self: [os.path.basename(p) for p in self._clip_paths], notify=changed)
    clipPaths = Property("QVariantList", lambda self: list(self._clip_paths), notify=changed)
    #: Upcoming Jobs' own list (clips, clipPaths, clipFx), from the input folders
    jobClips = Property(QObject, lambda self: self.queue, constant=True)
    sidecarExists = Property(bool, lambda self: self._sidecar, notify=changed)
    # the clip has a saved Detect Text mask: Splatting's Text Grow / Soften
    # dials show only then (user, 2026-10-02)
    hasTextMask = Property(bool, lambda self: getattr(self, "_text_mask", False), notify=changed)
    playing = Property(bool, lambda self: self._playing, notify=changed)
    fastForward = Property(bool, lambda self: self._playing and self._play_step > 1, notify=changed)
    fps = Property(float, lambda self: self._fps, notify=changed)
    currentDepth = Property(str, lambda self: getattr(self, "_depth", ""), notify=changed)
    #: Frame Break presets wider than this clip (v1 _fb_offered_presets).
    letterboxPresets = Property("QVariantList", lambda self: getattr(self, "_fb_presets", []), notify=changed)
    letterboxClip = Property(bool, lambda self: getattr(self, "_fb_clip", False), notify=changed)
    #: Multi-Map: the depth-map subfolders to choose between.
    depthMaps = Property("QVariantList", lambda self: getattr(self, "_maps", []), notify=changed)
    #: This clip's keyframes, [{frame, kind}] (v1 _kf_marker_positions' source).
    keyframes = Property("QVariantList", lambda self: getattr(self, "_keyframes", []), notify=changed)

    def _keyed_values(self) -> Dict[str, float]:
        track = self._kf_track
        if track is None or track.is_empty() or not self._loaded:
            return {}
        key = (self._frame, self._kf_rev)
        if self._kf_cache is not None and self._kf_cache[0] == key:
            return self._kf_cache[1]
        out = {(name[:-4] if name.endswith("_var") else name): float(v)
               for name, v in (track.values_at(self._frame) or {}).items()}
        self._kf_cache = (key, out)
        return out

    #: The keyframed settings at the frame on screen, by setting key ({} with no keyframes):
    #: what the picture uses there, and what the dials show (user, 2026-10-11).
    keyed = Property("QVariantMap", _keyed_values, notify=changed)
    #: The frame on screen has a keyframe: a dial turned there changes it.
    onKey = Property(bool, lambda self: bool(self._loaded and self._kf_track is not None
                                             and self._kf_track.frame_at(self._frame) is not None), notify=changed)
    status =Property(str, lambda self: self._status, notify=changed)

    # ---- from the controller ----------------------------------------------------

    def note_change(self, key: str, value: Any) -> None:
        """A setting moved (QML or a programmatic write): tell the engine,
        and re-render if it changes the picture."""
        self._pending[key] = value
        if key in S.FOLDER_KEYS or key in ("multi_map", "selected_depth_map"):
            self.queue.rescan()             # Upcoming Jobs follows the folders
        if key not in NO_RENDER:
            self._drop_play_cache()
        if not self._loaded:
            return
        if key in OVERLAY_KEYS:
            self._overlay_timer.start()
        elif key in S.FOLDER_KEYS or key == "multi_map":
            pass                            # Load Preview picks the new clips up
        if key in ("multi_map", "input_depth_maps") and self._engine is not None:
            self._submit("sync")            # v1 rescans the map subfolders on these
        elif key not in NO_RENDER:
            # Held slider: render as fast as the worker keeps up (newest wins);
            # otherwise wait for the value to settle.
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
        path = same_clip([{"source_video": p} for p in self._clip_paths], path)   # however it is spelled
        if self._loaded and path in self._clip_paths:
            self._submit("jump", self._clip_paths.index(path))
        else:
            self._submit("load", str(path))

    @Slot(str)
    def reloadClip(self, path: str) -> None:
        """That clip loaded afresh -- its files changed on disk (the Depth Map
        Editor's Use in Splatting put an edited map in place). One clear and
        one load AT that clip, queued in that order: a clear then showClip()
        decided "jump" before the clear landed, jumped in an emptied list, and
        the batch's first clip came up instead (user, 2026-09-28)."""
        self._submit("clear")
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

    # ---- phase 3b: v1's buttons, each a job on the worker ------------------------

    @Slot()
    def updateSidecar(self) -> None:
        """v1 update_sidecar_file. QML confirmed an overwrite already."""
        self._submit("sidecar")

    @Slot()
    def rescanBorders(self) -> None:
        self._submit("rescan")

    @Slot()
    def autoConverge(self) -> None:
        """v1 run_preview_auto_converge (the Preview Auto-Converge button)."""
        self.autoConvRunning.emit(True)
        self._submit("autoconv")

    @Slot()
    def autoConvergeModeChosen(self) -> None:
        """v1 on_auto_convergence_mode_select: apply the cached anchor for
        the chosen mode, or run the pre-pass."""
        self.autoConvRunning.emit(True)
        self._submit("autoconv_select")

    # ---- Special FX > Keyframe (v1 _kf_add_keyframe / _kf_clear_*) ------------------

    @Slot(str)
    def addKeyframe(self, kind: str) -> None:
        """"static" or "dynamic", at the frame on screen."""
        self._drop_play_cache()
        self._submit("kf_add", "dynamic" if kind == "dynamic" else "static")

    @Slot()
    def clearKeyframe(self) -> None:
        self._drop_play_cache()
        self._submit("kf_clear")

    @Slot(str, float, result=bool)
    def setKeyValue(self, key: str, value: float) -> bool:
        """A keyframed dial turned by hand: on a keyframe, that keyframe takes the value
        too, so the picture follows (user, 2026-10-11: as the Depth Map Editor's layers).
        False off a keyframe -- the keyframes decide that frame, so nothing changes there."""
        track = self._kf_track
        entry = track.frame_at(self._frame) if track is not None and self._loaded else None
        if entry is None:
            return False
        var = key + "_var"
        values = dict(entry.get("values") or {})
        if var not in values:
            return False
        values[var] = float(value)
        track.set_keyframe(self._frame, entry.get("type", "static"), values)    # the copy, now: no jump back
        self._kf_rev += 1
        self._drop_play_cache()
        self._kf_sent = getattr(self, "_kf_sent", 0) + 1
        self._submit("kf_value", (self._frame, var, float(value), self._kf_sent))
        self.changed.emit()
        return True

    # ---- the shaper: a dynamic keyframe's curve (user, 2026-10-06; core.common.keyframes) ----
    #: The shapes offered, as (stored name, label).
    from core.common.keyframes import CURVE_LABELS
    curveShapes = Property("QVariantList", lambda self: [{"key": k, "label": lab} for k, lab in self.CURVE_LABELS],
                           constant=True)

    @Slot(int, "QVariant")
    def setKeyCurve(self, frame: int, curve) -> None:
        """The curve of the dynamic keyframe at `frame` ({shape, cycles} or
        {shape: custom, points}; None or linear: a straight ramp). Saved to
        the clip's sidecar with its keyframes."""
        self._drop_play_cache()
        c = curve.toVariant() if hasattr(curve, "toVariant") else curve
        self._submit("kf_curve", (int(frame), dict(c) if c else None))

    @Slot("QVariant", int, int, result="QVariantList")
    def curveSamples(self, curve, seed: int, n: int = 120):
        """The curve at n+1 even steps of its span (0..1 each), for drawing:
        the same function the preview and the render use."""
        from core.common.keyframes import curve_fraction
        c = curve.toVariant() if hasattr(curve, "toVariant") else curve
        n = max(2, int(n))
        return [curve_fraction(i / n, dict(c) if c else None, seed=int(seed)) for i in range(n + 1)]

    @Slot("QVariant", int, result="QVariantList")
    def bakeCurve(self, curve, seed: int):
        """Any curve as custom points that trace it -- where adding a point
        in the shaper starts from."""
        from core.common.keyframes import bake_curve
        c = curve.toVariant() if hasattr(curve, "toVariant") else curve
        return bake_curve(dict(c) if c else None, seed=int(seed))

    @Slot()
    def clearAllKeyframes(self) -> None:
        self._drop_play_cache()
        self._submit("kf_clear_all")

    # ---- Hole Shift (editors.py), Detect Text (submask.py) ----------------------------

    #: Ops that change the clip's regions/zones: frames kept for looping are stale.
    EDITOR_EDITS = {"click", "commit", "del_key", "clear_all", "set_enabled"}

    @Slot(str)
    def openEditor(self, name: str) -> None:
        """v1 _dilreg_open. editorChanged (with "open") or
        editorRefused follows."""
        if self._engine is None or not self._loaded:
            from .editors import NO_SPLAT
            self.editorRefused.emit(name, "Load a clip in the previewer first." if name == "submask" else NO_SPLAT)
            return
        self._submit("editor", {"ed": name, "op": "open", "args": []})

    @Slot(str, str, "QVariantList")
    def editorOp(self, name: str, op: str, args) -> None:
        if name == "submask" and op == "cancel":
            # v1 _on_close mid-render: a flag the render thread polls, set now
            # rather than behind whatever the worker has queued.
            ed = self._editors.get("submask")
            if ed is not None:
                ed.cancel()
            return
        if op in self.EDITOR_EDITS and name != "submask":
            self._drop_play_cache()
        if op in ("commit", "set_enabled"):
            # These re-render (v1 _dilreg_apply), carrying any
            # setting change still waiting -- such as the Enable box's own.
            self._render_timer.stop()
        self._submit("editor", {"ed": name, "op": op, "args": list(args or [])})

    @Slot(str)
    def closeEditor(self, name: str) -> None:
        if self._engine is not None:
            self._submit("editor", {"ed": name, "op": "close", "args": []})

    @Slot(bool)
    def moveSidecars(self, depth_to_source: bool) -> None:
        """v1 File > Sidecars: Depth -> Source / Source -> Depth."""
        self._submit("sidecars_d2s" if depth_to_source else "sidecars_s2d")

    @Slot()
    def choosePreset(self) -> None:
        """A Letterbox Size button (fb_preset already set): v1 _fb_on_preset_selected."""
        self._submit("fb_preset")

    @Slot()
    def chooseMap(self) -> None:
        """A Multi-Map radio (selected_depth_map already set): v1 _on_map_selection_changed."""
        self._drop_play_cache()
        self._submit("map_select")

    @Slot()
    def estimateDepthPop(self) -> None:
        """v1 run_estimate_dp_total_max (the ≈ button)."""
        self._submit("estimate")

    def prepare_single(self, test: str) -> None:
        """SINGLE: the clip on screen (and, for Map/Splat Test, v1's
        switch to the view the test compares against); singleReady follows."""
        self._submit("single", str(test or ""))

    def diagnostic_capture(self, payload) -> None:
        self._submit("diag", payload)

    @Slot()
    def stopAutoConverge(self) -> None:
        eng = self._engine
        if eng is not None:
            eng.stop_event.set()

    # ---- playback -------------------------------------------------------------------
    #
    # v1 plays by rendering each next frame on its Tk timer as fast as the
    # render allows, and once its frame buffer holds the clip, replays that
    # without pacing -- faster than the clip runs. Here every tick asks for
    # the next frame and does not advance until it is on screen, so playback
    # shows every frame and never runs faster than the clip's own frame rate.
    # Frames render on the GPU pre-processing (fast_depth.py) and are kept for
    # the next loop; pausing renders the frame you stopped on exactly.

    #: Playback frames kept for looping (display images).
    PLAY_CACHE_BYTES = 768 * 1024 * 1024

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
        was = self._playing
        self._playing, self._play_step = bool(on), int(step)
        if on:
            fps = self._fps if self._fps > 0 else 24.0
            self._play_timer.start(max(5, int(round(1000.0 / fps))))
        else:
            self._play_timer.stop()
            if was:
                self._submit("seek", self._frame)      # the paused frame, exact
        self.changed.emit()

    def _play_tick(self) -> None:
        if not self._playing or self._play_inflight:
            return
        last = max(0, self._frames - 1)
        nxt = self._frame + self._play_step
        if nxt > last:
            if self._pending.get("loop_playback", self._values_fn().get("loop_playback")) and last > 0:
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
        self._play_gen = self._play_cache_gen
        self.changed.emit()
        self._submit("play", nxt)

    def _drop_play_cache(self) -> None:
        self._play_cache.clear()
        self._play_cache_bytes = 0
        self._play_cache_gen += 1

    @Slot(int)
    def stepFrames(self, delta: int) -> None:
        """v1 _jump_frames_by (arrow keys)."""
        if not self._loaded:
            return
        self.pause()
        self.seek(max(0, min(self._frames - 1, self._frame + int(delta))))

    @Slot(bool)
    def setDragging(self, on: bool) -> None:
        """A slider was grabbed or let go. While held, renders take the GPU
        pre-processing (fast_depth.py); letting go renders exactly."""
        on = bool(on)
        if on == self._dragging:
            return
        self._dragging = on
        if not on and self._loaded:
            self._render_timer.stop()
            self._submit("render")

    @Slot(int)
    def seek(self, frame: int) -> None:
        self._frame = max(0, int(frame))
        self.changed.emit()
        self._submit("seek", self._frame)

    def release_for_job(self, then) -> None:
        """Before a batch: give back every byte of GPU memory the preview
        holds, THEN call `then` (on the UI thread). Queued behind whatever
        the worker is doing, so a render in flight finishes first. A preview
        that was showing is reloaded by ``restore_after_job``."""
        self._resume_after_job = self._loaded
        if self._engine is None and self._worker_idle():
            then()
            return
        self._after_release.append(then)
        self._submit("release")

    def _finish_release(self) -> None:
        """A release job is done: its caller's callback, first in first out --
        each release_for_job queued exactly one release, and the worker runs
        them in order (bug sweep 2026-10-09 #14: one slot, so a second
        request overwrote the first and its Start / Move never ran)."""
        if self._after_release:
            then = self._after_release.popleft()
            if then is not None:
                then()

    def _worker_idle(self) -> bool:
        """Nothing queued and nothing running on the worker."""
        with self._cv:
            return not self._jobs and not getattr(self, "_inflight", False)

    def restore_after_job(self) -> None:
        if getattr(self, "_resume_after_job", False):
            self._resume_after_job = False
            self._submit("load")      # v1 load_video_list returns to the last clip and frame

    def shutdown(self) -> None:
        with self._cv:
            self._jobs.clear()
            self._jobs.append(None)
            self._cv.notify()
        if self._thread is not None:
            self._thread.join(timeout=5)
        # The worker has stopped, so its readers are ours to close. Left open,
        # the persistent ffmpeg streams' pump threads (fast_decode.py) are
        # still running at process exit, and tearing the process down under
        # them crashed it (an access violation after every check had passed).
        eng = self._engine
        if eng is not None and (self._thread is None or not self._thread.is_alive()):
            try:
                eng.previewer._clear_preview_resources()
            except Exception:
                logger.debug("Preview readers not closed at shutdown", exc_info=True)
            for ed in self._editors.values():
                close = getattr(ed, "close", None)
                if getattr(ed, "open", False) and close is not None:
                    try:
                        close()
                    except Exception:
                        pass

    # ---- the worker -------------------------------------------------------------

    # ---- the Depth Map Editor's 3D view (user, 2026-09-30) -------------------------------

    def render_external(self, video: str, frame: int, codes, mode: str, holes: bool, reply,
                        stage: str = "depthedit", depth: str = "", left_overlay=None) -> None:
        """This preview's own render of ``video`` at ``frame`` -- its settings,
        sidecar, keyframes and effects, exactly as Splatting would show it --
        with ``codes`` (the edited map's 10-bit Y codes, HxW, what the editor's
        render will write) in place of the depth read from disk. ``mode``:
        "Dubois Anaglyph", "Splat Result" or "Glasses-Free 3D" (the pair goes to the
        SR display as `stage`'s, the reply is its left eye); ``holes`` highlights the
        disocclusions. ``reply(QImage or None, error)`` runs on this preview's
        thread. Only the newest request waiting is kept.

        A clip that isn't in Splatting's input folders (user, 2026-10-12: "work without
        the splatting connection too for single edits, and ... a non dc map") is drawn
        on its own, with ``depth`` (the editor's map) as its map: Splatting's settings
        as they are on its page, or the clip's sidecar beside the map if it has one.
        Splatting goes back to its own clips, at the clip and frame it was on, when it
        is shown again (refreshIfStale).

        ``left_overlay``: (colour HxWx3, strength HxW) float32 0..1, frame-sized -- the editor's masks
        and shapes, laid over the LEFT eye only, after the warp (user, 2026-10-25: "have it only show
        in the left eye"); the right eye is made from the clean frame."""
        with self._cv:
            self._jobs = deque(j for j in self._jobs if not (j is not None and j[0] == "ext_depth"))
        self._ext_stale = True
        self._submit("ext_depth", {"video": str(video), "frame": int(frame), "codes": codes,
                                   "mode": str(mode), "holes": bool(holes), "reply": reply,
                                   "stage": str(stage), "depth": str(depth or ""),
                                   "left_overlay": left_overlay})

    @Slot()
    def refreshIfStale(self) -> None:
        """Splatting shown again after the editor rendered through this
        preview: its own picture is redrawn at the frame it is now on."""
        home = getattr(self, "_ext_home", None)
        if home is not None:
            # the editor drew a clip of its own here: Splatting's clips back, where it was
            self._ext_home = None
            self._ext_stale = False
            path, frame = home
            if self._loaded:
                self._submit("load", path) if path else self._submit("load")
                if path:
                    self.seek(frame)
            return
        if getattr(self, "_ext_stale", False) and self._loaded:
            self._ext_stale = False
            self._submit("render")

    def _ext_depth(self, eng, pv, arg: Dict[str, Any]) -> None:
        reply = arg["reply"]
        try:
            import numpy as np
            import torch
            found = self._ext_sources(eng)
            target = same_clip(found, arg["video"])
            norm = lambda p: os.path.normcase(os.path.abspath(str(p or "")))  # noqa: E731
            if not any(norm((e or {}).get("source_video")) == norm(target) for e in found):
                # not one of Splatting's: drawn on its own, with the editor's map
                depth = arg.get("depth") or ""
                if not os.path.isfile(depth):
                    reply(None, "The 3D view needs the clip's depth map (Depth map, above).")
                    return
                if getattr(self, "_ext_home", None) is None:
                    own = pv.last_loaded_video_path if self._loaded else None
                    self._ext_home = (own, int(self._frame))
                target = str(arg["video"])
                found = [{"source_video": target, "depth_map": depth}]
                alone = depth
            else:
                alone = None
            if not pv.source_readers or norm(pv.last_loaded_video_path) != norm(target):
                pv.last_loaded_frame_index = 0
                pv._clear_preview_resources()
                pv.last_loaded_video_path = target
                if alone:
                    # v1, finding no sidecar, looks the map up again in Splatting's depth folder
                    # by name, before the readers open -- not there for a clip of the editor's own,
                    # so its map came up missing: skipped while it loads, the map stays the editor's
                    eng._on_map_selection_changed = lambda *a, **k: None
                try:
                    pv.load_video_list(lambda: found)
                finally:
                    eng.__dict__.pop("_on_map_selection_changed", None)
                eng._fb_refresh_presets()
            reader = pv.source_readers.get("source_video")
            if reader is None:
                reply(None, "Splatting could not open this clip.")
                return
            idx = max(0, min(int(arg["frame"]), len(reader) - 1))
            pv.frame_scrubber_var.set(idx)          # keyframes follow the scrubber
            pv.on_scrubber_move(idx)
            src = torch.from_numpy(reader.get_batch([idx]).asnumpy()).permute(0, 3, 1, 2).float() / 255.0
            dep = torch.from_numpy(np.asarray(arg["codes"], dtype=np.float32))[None, None]
            flip = pv.current_params.get("flip_horizontal")
            if flip is None:
                flip = getattr(pv, "flip_horizontal", False)
            if flip:
                src, dep = torch.flip(src, dims=[3]), torch.flip(dep, dims=[3])
            saved = (pv._depth_bit_depth, eng.preview_source_var.get(), eng.mask_highlight_var.get())
            # the edited map is written as 10-bit codes: read it the way Splatting will
            pv._depth_bit_depth = 10
            eng.preview_source_var.set(arg["mode"])
            eng.mask_highlight_var.set(bool(arg["holes"]))
            eng.left_eye_hook = _left_overlay_hook(arg.get("left_overlay"), bool(flip))
            try:
                img = pv.processing_callback({"source_video": src, "depth_map": dep}, pv.current_params)
            finally:
                eng.left_eye_hook = None
                pv._depth_bit_depth = saved[0]
                eng.preview_source_var.set(saved[1])
                eng.mask_highlight_var.set(saved[2])
                eng._render_requested = False     # Splatting's own picture waits for refreshIfStale
            if img is None:
                reply(None, "Splatting's preview could not render this frame (see the console).")
                return
            if img.info.get("sr3d"):
                # Glasses-Free 3D: the pair to the SR display, its left eye to the editor
                from studio import sr_display
                arr = img.info.get("sr3d_np")
                if arr is None or arr.shape[:2] != (img.height, img.width):
                    arr = np.asarray(img.convert("RGB"))
                sr_display.offer_frame(arg.get("stage", "depthedit"), arr)
                h, w = arr.shape[0], arr.shape[1] // 2
                reply(QImage(np.ascontiguousarray(arr[:, :w]).tobytes(), w, h, 3 * w,
                             QImage.Format.Format_RGB888).copy(), "")
                return
            img = img.convert("RGB")
            data = img.tobytes()
            qimg = QImage(data, img.width, img.height, 3 * img.width, QImage.Format.Format_RGB888).copy()
            from core.common import gpu_ops
            gpu_ops.vram_note(logger, "a 3D view frame")
            reply(qimg, "")
        except Exception as e:
            logger.exception("Depth Map Editor 3D view: render failed")
            reply(None, f"The 3D view failed: {e}")

    def _ext_sources(self, eng) -> list:
        """Splatting's source/depth pairs for the 3D view, scanned once and
        kept while the input paths and their folders are unchanged (user,
        2026-10-05: every frame drawn re-scanned the folders). A folder's
        mtime moves when a file is added, removed or renamed in it."""
        paths = (str(eng.input_source_clips_var.get() or ""), str(eng.input_depth_maps_var.get() or ""))
        # Multi-Map and the chosen map change what the scan finds, and with Multi-Map on
        # the maps live in subfolders whose files don't move the depth folder's mtime
        # (bug sweep 2026-10-09, A6: the old list was served after a switch)
        multi = bool(eng.multi_map_var.get())
        try:
            key = (paths + tuple(os.stat(p).st_mtime_ns for p in paths)
                   + (multi, str(eng.selected_depth_map_var.get() or "")))
            if multi and os.path.isdir(paths[1]):
                key += tuple((d.name, d.stat().st_mtime_ns) for d in sorted(os.scandir(paths[1]), key=lambda d: d.name)
                             if d.is_dir())
        except OSError:
            key = None                      # a path missing: scan, and say so as the scan does
        cached = getattr(self, "_ext_scan", None)
        if key is not None and cached is not None and cached[0] == key:
            return [dict(e) for e in cached[1]]     # copies: the preview keeps and edits its list
        found = eng._find_preview_sources_callback()
        self._ext_scan = (key, [dict(e) for e in found]) if key is not None else None
        return found

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
            # Newest render/seek wins: drop any still waiting. Their setting
            # changes are carried forward, not lost.
            if kind in ("render", "seek", "overlay"):
                kept = deque()
                for job in self._jobs:
                    if job is not None and job[0] in ("render", "seek", "overlay") and (
                            job[0] == kind or kind != "overlay"):
                        merged = dict(job[2])
                        merged.update(changes)
                        changes = merged
                        # a render taking a queued seek's place renders at
                        # ITS frame (audit 2026-09-28, minor: the old frame
                        # was drawn, picture and counter jumped back)
                        if kind == "render" and job[0] == "seek":
                            kind, arg = "seek", job[1]
                    else:
                        kept.append(job)
                self._jobs = kept
            # Detect Text's scrubber and sliders, Hole Shift's knobs (per offset)
            # and any editor's commit: only the newest of each is worth doing --
            # a knob turned on a 1080p clip sent one job per notch, each redrew
            # the editor, and the queue crawled behind the hand (user, 2026-09-27).
            if kind == "editor" and arg.get("op") in ("seek", "set", "set_offset", "commit"):
                same_attr = (lambda j: arg.get("op") != "set_offset"
                             or (j[1].get("args") or [None])[0] == (arg.get("args") or [None])[0])
                kept = deque()
                for job in self._jobs:
                    if (job is not None and job[0] == "editor" and job[1].get("ed") == arg.get("ed")
                            and job[1].get("op") == arg.get("op") and same_attr(job)):
                        merged = dict(job[2])
                        merged.update(changes)
                        changes = merged
                    else:
                        kept.append(job)
                self._jobs = kept
            # A keyed dial turned (setKeyValue): only the newest value for a key and setting is worth
            # applying -- a job a notch redrew the frame and saved the sidecar each, the queue crawled
            # behind the hand, and every older result pulled the dial back (user, 2026-10-25)
            if kind == "kf_value":
                kept = deque()
                for job in self._jobs:
                    if job is not None and job[0] == "kf_value" and tuple(job[1][:2]) == tuple(arg[:2]):
                        merged = dict(job[2])
                        merged.update(changes)
                        changes = merged
                    else:
                        kept.append(job)
                self._jobs = kept
            # a held slider -- the frame scrubber too -- renders the fast way
            fast = (self._dragging and kind in ("render", "overlay", "seek")) or kind == "play"
            self._jobs.append((kind, arg, changes, fast))
            self._cv.notify()
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="splat-preview", daemon=True)
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
            kind, arg, changes, fast = job
            try:
                result = self._do(kind, arg, changes, fast)
            except Exception as e:  # never let one job kill the thread
                logger.error("Preview %s failed: %s", kind, e, exc_info=True)
                result = {"error": str(e), "kind": kind}
            self._sig_done.emit(result)

    def _ensure_engine(self):
        if self._engine is None:
            from .preview_engine import SplattingPreviewEngine
            self._engine = SplattingPreviewEngine(list(S.DEFAULTS), _Hooks(self))
            self._fresh_engine = True
        return self._engine

    def _do(self, kind: str, arg: Any, changes: Dict[str, Any], fast: bool = False) -> Dict[str, Any]:
        eng = self._ensure_engine()
        eng.store.values.update(changes)
        # v1's traces, as if the UI had written each var (Tk fires them on the
        # write). Not for the first, full snapshot: that is not a change.
        if getattr(self, "_fresh_engine", False):
            self._fresh_engine = False
        else:
            for key in changes:
                eng.store.fire(key)
        pv = eng.previewer
        from . import preview_engine as PE
        # A fast frame is shown, never kept: v1's frame buffer would hand it
        # back later as if it were the exact one.
        PE.FAST["on"] = bool(fast)
        saved_buffer = pv._frame_buffer
        if fast:
            pv._frame_buffer = None
        try:
            result = self._do_job(eng, pv, kind, arg)
        finally:
            PE.FAST["on"] = False
            pv._frame_buffer = saved_buffer
        self._flush_editors()
        return result

    def _do_job(self, eng, pv, kind: str, arg: Any) -> Dict[str, Any]:
        if kind == "load":
            # Multi-Map's subfolders, the first time (v1 scans on the checkbox /
            # folder change) -- keeping the saved map if it is still there.
            if eng.multi_map_var.get() and not eng.depth_map_subfolders:
                saved = eng.selected_depth_map_var.get()
                eng._scan_depth_map_folders()
                if saved and saved in eng.depth_map_subfolders:
                    eng.selected_depth_map_var.set(saved)
            # v1 _handle_load_refresh: re-scan the folders, then load -- at the
            # clip Upcoming Jobs asked for, if it did (v1 returns to the last one).
            found = eng._find_preview_sources_callback()
            if arg:
                # the list's own spelling of that clip: v1 matches paths letter
                # for letter, and "C:/x/clipB.mp4" is not "C:/x\\clipB.mp4" --
                # the batch's first clip came up instead (user, 2026-09-28)
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
            eng._fb_refresh_presets()
        elif kind in ("clear", "release"):
            pv._clear_preview_resources()
            pv.video_list = []
            pv.current_video_index = -1
            # Everything else a render leaves on the GPU (v1 keeps these for
            # its editor windows and the live-convergence drag).
            eng._preview_stereo_projector = None
            eng._live_conv = None
            eng._warp_last_disp = None
            eng._dilreg_last_occ = None
            eng._dilreg_last_splat = None
            if kind == "release":
                from .preview_engine import _LazyEstimator
                eng.convergence_estimator = _LazyEstimator()   # its network, too
                # the Detect Text mask it shows: the batch moves it to finished/
                # with its clip, and an open reader would hold it
                eng._preview_submask_reader = None
                eng._preview_submask_path = None
                # an open editor lets go of its clip, or the batch cannot move
                # it to finished/ (audit #29: Detect Text held it open)
                for ed in list(getattr(self, "_editors", {}).values()):
                    suspend = getattr(ed, "suspend", None)
                    if callable(suspend):
                        try:
                            suspend()
                        except Exception:
                            logger.debug("Could not suspend an editor", exc_info=True)
            try:
                import torch
                if torch.cuda.is_available():
                    # cuBLAS keeps a workspace per stream in the allocator --
                    # not a Python object, so only this lets it go.
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
        elif kind == "overlay":
            if pv.source_readers:
                eng._apply_preview_overlay_toggles()
        elif kind in ("seek", "play"):
            if pv.source_readers:
                pv.frame_scrubber_var.set(int(arg))
                pv.on_scrubber_move(int(arg))
                pv.update_preview()
        elif kind in ("nav", "jump"):
            if pv.video_list:
                pv._nav_preview_video_apply(int(arg), absolute=(kind == "jump"))
                eng._fb_refresh_presets()
        elif kind == "sidecar":
            from . import preview_engine as PE
            PE.messagebox.answer = True       # QML asked "Overwrite Sidecar File?"
            try:
                eng.update_sidecar_file()
            finally:
                PE.messagebox.answer = False
            eng._update_sidecar_button_text()
        elif kind == "rescan":
            if pv.source_readers:
                eng._on_border_rescan_click()
                eng._update_sidecar_button_text()
        elif kind == "autoconv":
            eng.stop_event.clear()
            eng.run_preview_auto_converge()
        elif kind == "autoconv_select":
            eng.stop_event.clear()
            eng.on_auto_convergence_mode_select(None)
        elif kind == "estimate":
            eng.run_estimate_dp_total_max()
        elif kind in ("sidecars_d2s", "sidecars_s2d"):
            from . import preview_engine as PE
            PE.messagebox.answer = True        # QML asked v1's "Continue?" first
            try:
                (eng.move_sidecars_depth_to_source if kind == "sidecars_d2s"
                 else eng.move_sidecars_source_to_depth)()
            finally:
                PE.messagebox.answer = False
        elif kind == "kf_add":
            eng._kf_add_keyframe(str(arg))
        elif kind == "kf_clear":
            eng._kf_clear_current()
        elif kind == "kf_value":
            frame, var, value, seq = arg
            entry = eng.keyframe_track.frame_at(frame)
            if entry is not None:
                values = dict(entry.get("values") or {})
                values[var] = float(value)
                eng.keyframe_track.set_keyframe(frame, entry.get("type", "static"), values)
                eng._kf_autosave()
                if pv.source_readers:
                    pv.update_preview()
            self._kf_done = max(getattr(self, "_kf_done", 0), int(seq))
        elif kind == "kf_curve":
            frame, curve = arg
            if eng.keyframe_track.set_curve(frame, curve):
                eng._kf_autosave()
                if pv.source_readers:
                    pv.update_preview()
        elif kind == "kf_clear_all":
            from . import preview_engine as PE
            PE.messagebox.answer = True        # QML asked "Clear All Keyframes?"
            try:
                eng._kf_clear_all()
            finally:
                PE.messagebox.answer = False
        elif kind == "fb_preset":
            eng._fb_on_preset_selected()
        elif kind == "map_select":
            eng._on_map_selection_changed()
            if pv.source_readers:
                pv.update_preview()
        elif kind == "sync":
            pass                              # the traces above did the work
        elif kind == "diag":
            eng._handle_gui_diagnostic_capture(arg)
        elif kind == "editor":
            self._editor_job(eng, str(arg["ed"]), str(arg["op"]), list(arg.get("args") or []))
        elif kind == "single":
            return self._single(eng, pv, str(arg or ""))
        elif kind == "ext_depth":
            self._ext_depth(eng, pv, arg)
        # v1's debounced on_slider_release, due now that the job is done.
        if eng._render_requested:
            eng._render_requested = False
            if pv.source_readers and kind not in ("render", "seek"):
                pv.update_preview()
        return self._state(eng, pv, kind)

    def _clip_info(self, eng, pv, info: Dict[str, Any]) -> Dict[str, Any]:
        """Current Clip Info for the clip the preview shows (user, 2026-09-27:
        all but Filename read N/A): v1 set Filename, Task and Frames; add its
        Resolution, and name its depth map where v1 left Map blank."""
        if not pv.source_readers or not (0 <= pv.current_video_index < len(pv.video_list)):
            return info
        entry = pv.video_list[pv.current_video_index] or {}
        src = str(entry.get("source_video") or "")
        cache = self.__dict__.setdefault("_res_cache", {})
        if src and src not in cache:
            try:
                import cv2
                cap = cv2.VideoCapture(src)
                w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                cap.release()
                cache[src] = f"{w}x{h}" if w > 0 and h > 0 else ""
            except Exception:
                cache[src] = ""
        if cache.get(src):
            info["processing_resolution"] = cache[src]
        if not info.get("processing_map") and entry.get("depth_map"):
            info["processing_map"] = os.path.basename(str(entry["depth_map"]))
        return info

    def _state(self, eng, pv, kind: str) -> Dict[str, Any]:
        info = eng.info.take_writes()
        if kind in ("load", "nav", "jump", "sync"):
            info = self._clip_info(eng, pv, info)
        return {
            "kind": kind,
            "writes": eng.store.take_writes(),
            "info": info,
            "loaded": bool(pv.source_readers),
            "frame": int(pv.frame_scrubber_var.get() or 0),
            "frames": int(pv.frame_scrubber.cget("to") or 0) + 1 if pv.source_readers else 0,
            "index": int(pv.current_video_index),
            "count": len(pv.video_list),
            # the list's source clips (Upcoming Jobs, JobQueue.qml)
            "clip_paths": [str((e or {}).get("source_video") or "") for e in pv.video_list],
            "fps": self._reader_fps(pv),
            "fb_presets": list(getattr(eng, "_fb_preset_ar", {}) or {}),
            "fb_clip": bool(pv.source_readers),
            "maps": list(eng.depth_map_subfolders) if eng.multi_map_var.get() else [],
            "keyframes": [{"frame": int(f), "kind": str(k), "curve": eng.keyframe_track.curve_at(f)}
                          for f, k, _v in eng.keyframe_track.items_sorted()],
            "kf_list": eng.keyframe_track.to_list(),
            "kf_done": getattr(self, "_kf_done", 0),     # the last keyed dial turn applied
            "depth": (pv.video_list[pv.current_video_index].get("depth_map") or ""
                      if 0 <= pv.current_video_index < len(pv.video_list) else ""),
        }

    # The editors' own ops. Each one's picture is redrawn by _flush_editors
    # once the job is done, as is an open editor's after any preview render
    # (v1 re-drew it from every render through _dilreg_refresh).
    EDITOR_OPS = {
        "holeshift": {"click", "nudge", "set_offset", "commit", "add_key", "del_key", "clear_all", "set_enabled"},
        "submask": {"seek", "step", "set", "set_rect", "zoom_rect", "fit_view", "clear_rect", "redraw",
                    "add_segment", "remove_segment", "select_segment", "set_range", "mark_in", "mark_out",
                    "freeze_here", "unfreeze", "reset_all"},
    }
    #: Called from inside every preview render while that editor is open.
    EDITOR_HOOKS = {"holeshift": "_dilreg_refresh"}

    def _job_running(self) -> bool:
        """A stage's job is running (GpuJobs): Detect Text stays off the GPU."""
        jobs = getattr(self.parent(), "jobs", None)
        return jobs is not None and jobs.running() is not None

    def _editor_job(self, eng, name: str, op: str, args: list) -> None:
        from . import editors
        if name not in self.EDITOR_OPS:
            raise ValueError(f"no editor {name!r}")
        ed = self._editors.get(name)
        if ed is None:
            if name == "submask":
                from .submask import SubtitleMask
                ed = SubtitleMask(eng, job_running=self._job_running)
            else:
                ed = editors.HoleShift(eng)
            self._editors[name] = ed
        hook = self.EDITOR_HOOKS.get(name)
        if op == "open":
            refused = ed.start()
            if refused is not None:
                self._sig_editor.emit(name, {"refused": refused})
                return
            # Called from inside every preview render while open (engine.after
            # runs at once, on this thread): mark it, draw once the job ends.
            if hook:
                setattr(eng, hook, lambda n=name: self._editors_stale.add(n))
        elif op == "close":
            if ed.open:
                ed.close()
            if hook:
                setattr(eng, hook, None)
            self._editors_stale.discard(name)
            self._sig_editor.emit(name, {"open": False})
            return
        elif name == "submask" and getattr(ed, "_render_thread", None) is not None                 and op not in ("saved", "cancel"):
            # Save / Flatten is rendering: no edits, seeks or steps meanwhile -- the render
            # shares the editor's capture, and saved() writes the selections it rendered
            # (bug sweep 2026-10-09, A6). The window greys these out; this is the backstop.
            return
        elif name == "submask" and op in ("save", "flatten"):
            if not ed.open:
                return
            bad = getattr(ed, op)(args[0] if len(args) > 0 else None, args[1] if len(args) > 1 else None,
                          lambda n, total: self._sig_editor.emit(name, {"progress": [n, total, "Rendering…"]}),
                          lambda *done: self._sig_submask_done.emit(done))
            if bad:
                self._sig_editor.emit(name, {"alert": bad})
            # else: the Save button goes while it renders (state "rendering").
        elif name == "submask" and op == "set_range":
            if not ed.open:
                return
            ed.set_range(*args)     # a bad one is reported by Save (no second alert on blur)
        elif name == "submask" and op == "saved":
            # v1 on_save, then the editor's destroy_safe -- unless it was a
            # Flatten Layers: the window stays open for the next selections
            flattening = getattr(ed, "_flattening", False)
            self._sig_status.emit(ed.saved(args[0], args[1]))
            if flattening:
                self._editors_stale.add(name)
                return
            ed.close()
            self._editors_stale.discard(name)
            self._sig_editor.emit(name, {"open": False, "saved": True})
            return
        else:
            if not ed.open:
                return
            if op not in self.EDITOR_OPS[name]:
                raise ValueError(f"{name}: no op {op!r}")
            message = getattr(ed, op)(*args)
            if message:
                self._sig_status.emit(str(message))   # v1: the main status label
        self._editors_stale.add(name)

    def _flush_editors(self) -> None:
        stale, self._editors_stale = self._editors_stale, set()
        for name in stale:
            ed = self._editors.get(name)
            if ed is None or not ed.open:
                continue
            try:
                image = ed.render()
            except Exception:
                logger.error("%s: render failed", name, exc_info=True)
                image = None
            state = dict(ed.state())
            state["open"] = True
            if image is not None:
                self.provider.put(name, _to_qimage(image))
                state["imageWidth"], state["imageHeight"] = image.width, image.height
            self._sig_editor.emit(name, state)

    @staticmethod
    def _reader_fps(pv) -> float:
        reader = pv.source_readers.get("source_video") if pv.source_readers else None
        try:
            return float(reader.get_avg_fps()) if reader is not None else 0.0
        except Exception:
            return 0.0

    def _single(self, eng, pv, test: str) -> Dict[str, Any]:
        """v1 start_single_processing, up to where it starts the thread: the
        clip's paths (Multi-Map: the sidecar-chosen map), and for a Map/Splat
        Test v1's sidecar sync, view switch and the preview capture the test
        compares the render against."""
        import os
        out: Dict[str, Any] = {}
        if not pv.source_readers or not (0 <= pv.current_video_index < len(pv.video_list)):
            out["error"] = "Please load a video in the Previewer first."
            return {**self._state(eng, pv, "single"), "single": out}
        entry = pv.video_list[pv.current_video_index]
        video, depth = entry.get("source_video"), entry.get("depth_map")
        if eng.multi_map_var.get() and video:
            try:
                chosen = eng._get_sidecar_selected_map_for_video(video)
                if chosen:
                    name = os.path.splitext(os.path.basename(video))[0]
                    folder = os.path.join(eng.input_depth_maps_var.get(), chosen)
                    for ext in (".mp4", ".npz"):
                        cand = os.path.join(folder, f"{name}_depth{ext}")
                        if os.path.exists(cand):
                            depth = cand
                            break
            except Exception:
                logger.debug("Multi-Map: sidecar map not resolved for SINGLE", exc_info=True)
        out.update({"video": video or "", "depth": depth or "", "frame": int(pv.frame_scrubber_var.get() or 0)})
        if test:
            eng.update_gui_from_sidecar(depth)
            depth = pv.video_list[pv.current_video_index].get("depth_map") or depth
            out["depth"] = depth
            if test == "map":
                target = "Depth Map"
            elif eng.enable_low_res_var.get() and not eng.enable_full_res_var.get():
                target = "Splat Result(Low)"
            else:
                target = "Splat Result"
            eng.preview_source_var.set(target)
            eng.preview_size_var.set("100%")
            pv.update_preview()
            img = pv.pil_image_for_preview
            # v1's _diag_test_pending: what _handle_gui_diagnostic_capture saves
            # the render beside when the capture comes back.
            eng._diag_test_pending = {
                "test_type": test,
                "video_base": os.path.splitext(os.path.basename(video or ""))[0],
                "frame_idx": out["frame"],
                "ffmpeg_tag": "_ffmpeg" if bool(eng.strict_ffmpeg_decode_var.get()) else "",
                "output_dir": str(eng.output_splatted_var.get() or ""),
                "preview_img": img.convert("RGB").copy() if img is not None else None,
            }
        else:
            eng._diag_test_pending = None
        return {**self._state(eng, pv, "single"), "single": out}

    # ---- worker -> UI -------------------------------------------------------------

    def _refresh_text_mask(self) -> None:
        """hasTextMask for the clip shown: its <clip>_submask.mp4 is there.
        Not on every frame -- on clip changes and after a Save / Remove."""
        from core.splatting.render_processor import resolve_subtitle_mask
        clip = self._clip_paths[self._index] if 0 <= self._index < len(self._clip_paths) else ""
        has = bool(clip and self._loaded and resolve_subtitle_mask(clip))
        if has != getattr(self, "_text_mask", False):
            self._text_mask = has
            self.changed.emit()

    def _on_editor(self, name: str, state: Dict[str, Any]) -> None:
        if name == "submask" and state.get("saved"):
            self._refresh_text_mask()       # saved, or taken off (Remove Mask)
            self.queue.refreshFx()          # the clip list's Detect Text badge
        if "refused" in state:
            self.editorRefused.emit(name, state["refused"])
            return
        if "alert" in state:
            self.editorAlert.emit(name, state["alert"])
            return
        if "progress" in state:
            n, total, text = state["progress"]
            self.editorProgress.emit(name, int(n), int(total), str(text))
            return
        if "imageWidth" in state:
            self._editor_serial = getattr(self, "_editor_serial", 0) + 1
            state["serial"] = self._editor_serial   # a fresh image:// URL, so QML reloads it
        self.editorChanged.emit(name, state)

    def _on_submask_done(self, done) -> None:
        """v1 _render_done, back on the UI thread."""
        import os
        out_path, settings, n, cancelled, error = done
        if error:
            self.editorProgress.emit("submask", 0, 1, "Render failed.")
            self.editorAlert.emit("submask", f"Mask render failed:\n{error}")
        elif cancelled:
            self.editorProgress.emit("submask", 0, 1, "Cancelled.")
        else:
            self.editorProgress.emit("submask", 0, 1, f"Saved {n} frames → {os.path.basename(out_path)}" if settings
                                     else "Mask removed.")
            self._drop_play_cache()
            self._submit("editor", {"ed": "submask", "op": "saved", "args": [settings, out_path]})
            return
        # Still open: redraw, so the Save button comes back.
        self._submit("editor", {"ed": "submask", "op": "redraw", "args": []})

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

    def _on_sidecar(self, exists: bool) -> None:
        self._sidecar = exists
        self.changed.emit()

    def _on_done(self, result: Dict[str, Any]) -> None:
        if "error" in result:
            self._loading = False
            self.autoConvRunning.emit(False)
            if result.get("kind") == "single":
                self.singleReady.emit({"error": result["error"]})
            self.toast.emit("error", "Preview failed", result["error"])
            if result.get("kind") == "play":
                # the frame never came: without this Play waited on it for ever
                # (audit 2026-09-28, minor). Stopped, so a clip that cannot
                # render is not one error per frame.
                self._play_inflight = False
                self._set_playing(False, 1)
            self.changed.emit()
            # only the release's own result finishes it: an earlier job's
            # failure used to say "released" while the release still waited
            if result.get("kind") == "release":
                self._finish_release()
            return
        if result["writes"]:
            self.settingsWritten.emit(result["writes"])
        if result["info"]:
            self.infoWritten.emit(result["info"])
        self._loaded = result["loaded"]
        self._fps = float(result.get("fps") or 0.0)
        self._fb_presets = result.get("fb_presets") or []
        self._fb_clip = bool(result.get("fb_clip"))
        self._maps = result.get("maps") or []
        self._keyframes = result.get("keyframes") or []
        if "kf_list" in result and int(result.get("kf_done", 0)) >= getattr(self, "_kf_sent", 0):
            # (only a copy with every keyed dial turn in it: an older one -- even one handled after the
            # worker finished the newest turn -- would pull the dial back; user, 2026-10-25)
            from core.common.keyframes import KeyframeTrack
            self._kf_track = KeyframeTrack.from_dict(result["kf_list"])
            self._kf_rev += 1
        self._depth = result.get("depth", "")
        if result["kind"] == "play":
            self._play_inflight = False
            image = self.provider._images.get("frame")
            # not in Wigglegram (the frame shown is the two eyes; "frame" is
            # stale -- the loop replayed it frozen), nor one asked for before
            # the settings changed (audit 2026-09-28, minor)
            if (image is not None and self._playing and not self._wiggle and not self._stereo
                    and self._play_gen == self._play_cache_gen):
                size = image.sizeInBytes()
                if self._play_cache_bytes + size <= self.PLAY_CACHE_BYTES:
                    self._play_cache[int(result["frame"])] = image
                    self._play_cache_bytes += size
        if result["kind"] in ("load", "nav", "jump", "clear", "release"):
            self._drop_play_cache()
            if result["kind"] != "load" and self._playing:
                self._set_playing(False, 1)
        self._frame = result["frame"]
        self._frames = result["frames"]
        self._index = result["index"]
        self._count = result["count"]
        if "clip_paths" in result:
            self._clip_paths = list(result["clip_paths"])
        # not for the Depth Map Editor's 3D frames either: a sidecar scan and a list rebind
        # per frame of a drag, on the UI thread, for nothing saved (bug sweep A6)
        if result["kind"] not in ("render", "seek", "play", "overlay", "ext_depth"):
            self.queue.refreshFx()          # a sidecar may have been saved: the list's badges
            self._refresh_text_mask()
        if result["kind"] in ("load", "nav", "jump", "clear", "release"):
            self._loading = False
        if result["kind"] in ("autoconv", "autoconv_select"):
            self.autoConvRunning.emit(False)
        if result["kind"] == "single":
            self.singleReady.emit(result.get("single") or {})
        if not self._loaded:
            self.provider.put("frame", None)
            self._image_w = self._image_h = 0
            self._wiggle = False
            self._stereo = False
            self._serial += 1
            self.frameChanged.emit()
        self.changed.emit()
        if result["kind"] == "release":
            self._finish_release()

    infoWritten = Signal("QVariantMap")
