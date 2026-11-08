"""controller.py -- Depth Map's Blend tab (user, 2026-10-26) as QML sees it.

The clips in Splatting's depth-map folder that have both a DepthCrafter and a VDA map (core.depthblend.pairs),
a preview of one frame -- DepthCrafter | VDA | the blend, side by side -- and the job that blends them.

The preview is drawn on a worker thread, only when asked (a clip, a frame or a setting changed; nothing
redraws on its own) and never while a job runs. Its first frame of a clip measures the clip's fit (a pass at
a low resolution); the sums are kept, so later frames and settings, and the job, reuse them.

GpuJobs id "depthcrafter_blend": it never runs beside DepthCrafter or VDA, and logs to the Depth Map tab.
"""

from __future__ import annotations

import logging
import math
import os
import tempfile
import threading
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Property, QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtQml import QQmlPropertyMap

from studio.gpu_jobs import wait_for_gpu
from studio.outcome import Outcome, verdict

from ... import settings_store
from ..vda.runner import Hooks
from . import runner as R
from . import settings as S

logger = logging.getLogger("blend")

OWNER = "blend"
JOB_ID = "depthcrafter_blend"

HELP: Dict[str, str] = {
    "depth_folder": "Splatting's depth-map folder. VDA's rerun leaves a clip's two maps here -- the one Splatting "
                    "reads, and the other in a DepthCrafter or VDA folder beside it -- and the blend goes where "
                    "Splatting reads it, the map it replaces moved into its own folder. Every clip with both maps "
                    "is listed.",
    "mode": "Mix: the two maps averaged, by Mix. Layout + Detail: the broad depth of the Base map with the fine "
            "detail -- edges, small shapes -- of the other.",
    "other": "Which model's map is blended with DepthCrafter's: Video Depth Anything's (VDA) or DVD's. Each "
             "clip needs both maps -- the one Splatting reads and the other in its folder beside it.",
    "mix": "0 is all DepthCrafter, 1 all the other map (VDA or DVD).",
    "base": "Mix: the map whose depth range the blend keeps. Layout + Detail: the map whose broad depth it keeps; "
            "the other gives the detail. The other map is fitted onto the Base before blending.",
    "detail_size": "Layout + Detail: how big a feature counts as detail, in pixels of the blend. Small keeps only "
                   "edges from the other map; large takes more of its shapes.",
    "detail_amount": "Layout + Detail: how strongly the other map's detail is added (1 = as it is).",
    "mix_by_depth": "Mix by depth instead of one Mix for the whole frame: Near Mix for what is close, Far Mix for "
                    "what is far. For example VDA's sharper edges on the people up close (Near Mix high) and "
                    "DepthCrafter's steadier background (Far Mix low).",
    "mix_near": "By Depth: the Mix for what is close to the camera (0 = DepthCrafter, 1 = the other map).",
    "mix_far": "By Depth: the Mix for what is far away (0 = DepthCrafter, 1 = the other map).",
    "mix_split": "By Depth: the depth where near turns to far, in the Base map's depth (0 = farthest, 1 = nearest).",
    "mix_soft": "By Depth: how gradually near turns to far around Split. Small is a sharp change; large a slow fade.",
    "edge_keep": "Layout + Detail: keeps big depth steps (an outline against the background) out of the detail, so "
                 "they leave no halo beside them. 0 is the plain blur; higher keeps smaller steps sharp too.",
    "detail_limit": "Layout + Detail: the most the other map's detail can move the depth, in % of the depth range "
                    "-- larger detail is eased off, not cut. 0 = no limit. Stops colour texture or shimmer showing "
                    "as strong relief.",
    "detail_floor": "Layout + Detail: detail smaller than this (% of the depth range) is dropped -- the faint "
                    "wiggles and shimmer -- and the rest kept. 0 = all of it.",
    "edge_band": "Takes the other map in a strip along the frame's edges, fading out over this many pixels (of "
                 "the blend). DepthCrafter's specks sit along the left and top edges; a band of 20-40 px hides "
                 "them. 0 = off.",
    "edge_band_sides": "Which edges the Edge Band covers: the left and top (where DepthCrafter's specks are), the "
                       "left only, or all four.",
    "detail_smooth": "Layout + Detail: evens out the other map's detail over this many frames each side, so its "
                     "shimmer doesn't come through. Only small frame-to-frame changes are averaged: a moving "
                     "edge's detail is left alone, so it doesn't trail. 0 = off.",
    "level_black": "Levels: the depth that becomes the farthest (0). Raise it to push the far depths back.",
    "level_white": "Levels: the depth that becomes the nearest (1). Lower it to bring the near depths forward.",
    "level_gamma": "Levels: above 1 brings the middle depths nearer, below 1 pushes them back. 1 = as it is.",
    "own": "Gives the clip in the preview its own Blend settings: the settings on the left then change only this "
           "clip, and the others keep the shared ones. Untick to put the clip back on the shared settings. Kept "
           "in blend_clips.json in the depth-map folder.",
    "align": "The two maps are scaled differently, so the other is fitted onto the Base first. Per clip: one fit "
             "for the clip, steady. Per frame: refitted as the shot goes (eased over about half a second), for "
             "shots whose depth range changes. Off: as they are.",
    "size": "The blend's size: the larger map's, or DepthCrafter's or VDA's. Splatting scales it to its own.",
}


class _QtHooks(Hooks):
    def __init__(self, ctl: "BlendController"):
        self.ctl = ctl

    def status(self, text):
        self.ctl._sigStatus.emit(str(text))

    def progress(self, done, total):
        self.ctl._sigProgress.emit(int(done), int(total))

    def clip(self, filename, resolution, frames):
        self.ctl._sigClip.emit(str(filename))

    def frames(self, done, total, phase):
        self.ctl._sigFrames.emit(int(done), int(total), str(phase))


class BlendController(QObject):
    stateChanged = Signal()
    infoChanged = Signal()
    framesChanged = Signal()
    clipsChanged = Signal()
    ownChanged = Signal()
    previewChanged = Signal()
    toast = Signal(str, str, str)
    fieldRejected = Signal(str)

    _sigStatus = Signal(str)
    _sigProgress = Signal(int, int)
    _sigClip = Signal(str)
    _sigFrames = Signal(int, int, str)
    _sigFinished = Signal(bool)
    _sigPreview = Signal(int, str, int, str)      # request, image path ("" none), clip's frames, message

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self._shell = shell
        self.splatting = None           # app.build: its folder ("Same as Splatting"), its list after a run
        self._map = QQmlPropertyMap(self)
        for key, value in S.normalise(settings_store.load(OWNER)).items():
            self._map.insert(key, value)
        self._last_good: Dict[str, Any] = self._values()
        # the shared settings, and each clip's own (Own Settings; user, 2026-11-07): the page shows the
        # preview clip's own while it has some, the shared ones otherwise
        self._global: Dict[str, Any] = self._values()
        self._own: Dict[str, Dict[str, Any]] = {}
        self._own_folder = ""
        self._map.valueChanged.connect(self._on_value_changed)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self.save)

        self._pairs: List[Dict[str, str]] = []
        self._busy = False
        self._status = "Ready"
        self._done = self._total = 0
        self._clip = ""
        self._frames = (0, 0, "")
        self._stop = threading.Event()
        self._stop_now = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._runner: Optional[R.BlendRunner] = None

        # the preview: which clip and frame, the image, and the measured sums per clip (shared with the job)
        self._stats: Dict = {}
        self._pv_clip = 0
        self._pv_frame = 0
        self._pv_frames = 0
        self._pv_image = ""
        self._pv_message = ""
        self._pv_busy = False
        self._pv_req = 0
        self._pv_lock = threading.Lock()
        self._pv_want: Optional[tuple] = None
        self._pv_thread: Optional[threading.Thread] = None
        self._pv_dir = tempfile.mkdtemp(prefix="studio-blend-")
        self._pv_timer = QTimer(self)
        self._pv_timer.setSingleShot(True)
        self._pv_timer.setInterval(250)
        self._pv_timer.timeout.connect(self._preview_now)

        q = Qt.ConnectionType.QueuedConnection
        self._sigStatus.connect(self._on_status, q)
        self._sigProgress.connect(self._on_progress, q)
        self._sigClip.connect(self._on_clip, q)
        self._sigFrames.connect(self._on_frames, q)
        self._sigFinished.connect(self._on_finished, q)
        self._sigPreview.connect(self._on_preview, q)
        QTimer.singleShot(0, self.rescan)

    # ---- settings --------------------------------------------------------------------------------------

    settings = Property(QObject, lambda self: self._map, constant=True)
    choicesChanged = Signal()
    choices = Property("QVariantMap", lambda self: S.choices(str(self._map.value("other") or "VDA")), notify=choicesChanged)
    help = Property("QVariantMap", lambda self: HELP, constant=True)

    def _values(self) -> Dict[str, Any]:
        return {k: self._map.value(k) for k in self._map.keys()}

    def _on_value_changed(self, key: str, value: Any) -> None:
        default = S.DEFAULTS.get(key)
        if isinstance(default, float):
            try:
                if not math.isfinite(float(str(value).strip())):
                    raise ValueError(value)
            except ValueError:
                self._map.insert(key, self._last_good.get(key, default))
                self.fieldRejected.emit(key)
                return
        coerced = S.coerce(key, value)
        if coerced != value or type(coerced) is not type(value):
            self._map.insert(key, coerced)
        self._last_good[key] = coerced
        self._keep(key, coerced)
        if key == "other":                  # VDA's maps or DVD's (user, 2026-11-07): Base / Output Size follow it
            for k, v in S.follow_other(self._values()).items():
                if self._map.value(k) != v:
                    self._map.insert(k, v)
                    self._last_good[k] = v
                    self._keep(k, v)
            for vals in self._own.values():   # the clips' own Base / Output Size too
                vals.update({k: v for k, v in S.follow_other({**vals, "other": coerced}).items()
                             if k in ("base", "size") and k in vals})
            self.choicesChanged.emit()
        if key in ("depth_folder", "output_suffix", "other"):
            self.rescan()
        else:
            self.refreshPreview()
        self._save_timer.start()

    def _shown_clip(self) -> str:
        return self._pairs[self._pv_clip]["clip"] if 0 <= self._pv_clip < len(self._pairs) else ""

    def _keep(self, key: str, value: Any) -> None:
        """A setting changed on the page: the shown clip's own, while it has some; else the shared one."""
        clip = self._shown_clip()
        if key in S.CLIP_KEYS and clip in self._own:
            self._own[clip][key] = value
        else:
            self._global[key] = value

    def _show_clip_values(self) -> None:
        """The page shows the preview clip's own settings, or the shared ones."""
        vals = S.normalise({**self._global, **self._own.get(self._shown_clip(), {})})
        for k in S.CLIP_KEYS:
            if self._map.value(k) != vals[k]:
                self._map.insert(k, vals[k])
            self._last_good[k] = vals[k]
        self.ownChanged.emit()

    def _load_own(self) -> None:
        raw = str(self._global.get("depth_folder") or "")
        folder = os.path.normcase(os.path.abspath(raw)) if raw else ""
        if folder != self._own_folder:
            # the folder left: its clips' own settings kept there first (an edit may still be waiting to save)
            if self._own_folder and os.path.isdir(self._own_folder):
                S.save_clip_settings(self._own_folder, self._own)
            self._own_folder = folder
            self._own = S.load_clip_settings(raw) if raw else {}

    @Slot()
    def save(self) -> None:
        settings_store.save(OWNER, S.normalise(self._global))
        folder = str(self._global.get("depth_folder") or "")
        if folder and os.path.isdir(folder) and not S.save_clip_settings(folder, self._own):
            logger.warning("Blend: couldn't keep the clips' own settings in %s", folder)

    previewOwn = Property(bool, lambda self: self._shown_clip() in self._own, notify=ownChanged)
    clipLabels = Property("QVariantList", lambda self: [p["clip"] + ("  (own settings)" if p["clip"] in self._own else "")
                                                        for p in self._pairs], notify=ownChanged)

    @Slot(bool)
    def setOwn(self, on: bool) -> None:
        """Own Settings for the preview clip: its own from the settings shown now, or back to the shared ones."""
        clip = self._shown_clip()
        if not clip or bool(on) == (clip in self._own):
            return
        if on:
            self._own[clip] = S.clip_values(S.normalise(self._values()))
        else:
            self._own.pop(clip, None)
        self._show_clip_values()
        self.save()
        self.refreshPreview()

    @Slot()
    def resetDefaults(self) -> None:
        """Every setting shown back to its default: the shown clip's own while it has some (the shared ones
        left as they are), else the shared ones. The folder is kept."""
        clip = self._shown_clip()
        if clip in self._own:
            self._own[clip] = S.clip_values(S.normalise({**S.DEFAULTS, "other": self._global.get("other", "VDA")}))
            what = f"{clip}'s own settings are back to the defaults."
        else:
            keep = {"depth_folder": self._global.get("depth_folder")}
            self._global = S.normalise({**S.DEFAULTS, **keep})
            what = "Every Blend setting is back to its default. Your folder was kept."
        for k in S.GLOBAL_KEYS:
            if self._map.value(k) != self._global[k]:
                self._map.insert(k, self._global[k])
            self._last_good[k] = self._global[k]
        self._show_clip_values()
        self.save()
        self.choicesChanged.emit()
        self.rescan()
        self.toast.emit("info", "Settings reset", what)

    @Slot()
    def useSplattingFolder(self) -> None:
        """Splatting's Input Depth Maps (its folder, when it names a single map)."""
        sp = self.splatting
        folder = str(sp.settings.value("input_depth_maps") or "") if sp is not None else ""
        if os.path.isfile(folder):
            folder = os.path.dirname(folder)
        if not folder:
            self.toast.emit("warning", "Same as Splatting", "Splatting has no depth-map folder set.")
            return
        self._map.insert("depth_folder", os.path.normpath(folder))
        self._last_good["depth_folder"] = os.path.normpath(folder)
        self._global["depth_folder"] = os.path.normpath(folder)
        self.rescan()
        self._save_timer.start()

    # ---- the clips -----------------------------------------------------------------------------------

    @Slot()
    def rescan(self) -> None:
        from core import depthblend as B
        try:
            self._pairs = B.pairs(str(self._map.value("depth_folder") or ""),
                                  str(self._map.value("output_suffix") or "_depth"),
                                  str(self._map.value("other") or "VDA"))
        except Exception:
            logger.debug("Could not list the clips to blend", exc_info=True)
            self._pairs = []
        if self._pv_clip >= len(self._pairs):
            self._pv_clip = 0
        self._load_own()
        self._show_clip_values()
        self.clipsChanged.emit()
        self.refreshPreview()

    clips = Property("QVariantList", lambda self: [p["clip"] for p in self._pairs], notify=clipsChanged)

    # ---- the preview ---------------------------------------------------------------------------------

    previewClip = Property(int, lambda self: self._pv_clip, notify=previewChanged)
    previewFrame = Property(int, lambda self: self._pv_frame, notify=previewChanged)
    previewFrames = Property(int, lambda self: self._pv_frames, notify=previewChanged)
    previewBusy = Property(bool, lambda self: self._pv_busy, notify=previewChanged)
    previewMessage = Property(str, lambda self: self._pv_message, notify=previewChanged)

    @Property(str, notify=previewChanged)
    def previewImage(self):
        return QUrl.fromLocalFile(self._pv_image).toString() if self._pv_image else ""

    @Slot(int)
    def setPreviewClip(self, index: int) -> None:
        if 0 <= index < len(self._pairs) and index != self._pv_clip:
            self._pv_clip, self._pv_frame, self._pv_frames = index, 0, 0
            self._show_clip_values()          # its own settings, or the shared ones
            self.previewChanged.emit()
            self.refreshPreview()

    @Slot(int)
    def setPreviewFrame(self, frame: int) -> None:
        frame = max(0, int(frame))
        if frame != self._pv_frame:
            self._pv_frame = frame
            self.previewChanged.emit()
            self.refreshPreview()

    @Slot()
    def refreshPreview(self) -> None:
        """Draw the preview again soon (several changes at once draw it once)."""
        self._pv_timer.start()

    def _preview_now(self) -> None:
        if self._busy:
            return                          # nothing redraws while a job runs (the job's card is busy)
        if not self._pairs:
            self._pv_image, self._pv_frames = "", 0
            self._pv_message = (f"No clip in this folder has both a DepthCrafter and a "
                                f"{self._map.value('other') or 'VDA'} map yet.")
            self.previewChanged.emit()
            return
        self._pv_req += 1
        want = (self._pv_req, dict(self._pairs[self._pv_clip]), int(self._pv_frame), S.snapshot(self._values()))
        with self._pv_lock:
            self._pv_want = want
            running = self._pv_thread is not None and self._pv_thread.is_alive()
        if not running:
            self._pv_busy = True
            self.previewChanged.emit()
            self._pv_thread = threading.Thread(target=self._preview_work, name="blend-preview", daemon=True)
            self._pv_thread.start()

    def _preview_work(self) -> None:
        from core import depthblend as B
        from core.depthedit import depth_io as D
        while True:
            with self._pv_lock:
                want, self._pv_want = self._pv_want, None
            if want is None:
                return
            req, pair, frame, opts = want
            path, frames, msg = "", 0, ""
            try:
                key = R.stats_key(pair)
                stats = self._stats.get(key)
                if stats is None:
                    self._sigPreview.emit(-1, "", 0, f"Measuring {pair['clip']} (once)...")
                    dci, vdi = D.probe(pair["dc"]), D.probe(pair["other"])
                    stats = B.measure(dci, vdi, min(dci.num_frames, vdi.num_frames))
                    self._stats[key] = stats
                frames = len(stats)
                img = B.preview(pair, opts, min(frame, max(0, frames - 1)), stats)
                if img is None:
                    msg = "That frame couldn't be read."
                else:
                    from PySide6.QtGui import QImage
                    path = os.path.join(self._pv_dir, f"preview_{req % 4}.png")
                    QImage(img.data, img.shape[1], img.shape[0], img.strides[0], QImage.Format_Grayscale8).save(path)
            except Exception as e:
                logger.exception("Blend preview failed")
                msg = f"The preview failed: {e}"
            self._sigPreview.emit(req, path, frames, msg)

    @Slot(int, str, int, str)
    def _on_preview(self, req: int, path: str, frames: int, message: str) -> None:
        if req == -1:                       # measuring: say so, keep the last picture
            self._pv_message = message
            self.previewChanged.emit()
            return
        if req != self._pv_req:
            return                          # an older request's; the newer one is coming
        self._pv_busy = False
        self._pv_image, self._pv_frames, self._pv_message = path, frames, message
        if frames and self._pv_frame >= frames:
            self._pv_frame = frames - 1
        self.previewChanged.emit()

    # ---- run state -----------------------------------------------------------------------------------

    busy = Property(bool, lambda self: self._busy, notify=stateChanged)
    status = Property(str, lambda self: self._status, notify=infoChanged)
    progressDone = Property(int, lambda self: self._done, notify=infoChanged)
    progressTotal = Property(int, lambda self: self._total, notify=infoChanged)
    clipName = Property(str, lambda self: self._clip, notify=infoChanged)
    framesDone = Property(int, lambda self: self._frames[0], notify=framesChanged)
    framesTotal = Property(int, lambda self: self._frames[1], notify=framesChanged)
    framesPhase = Property(str, lambda self: self._frames[2], notify=framesChanged)

    @Slot(str)
    def _on_status(self, text):
        self._status = text
        self.infoChanged.emit()

    @Slot(int, int)
    def _on_progress(self, done, total):
        self._done, self._total = done, total
        self.infoChanged.emit()

    @Slot(str)
    def _on_clip(self, name):
        self._clip = name
        self.infoChanged.emit()

    @Slot(int, int, str)
    def _on_frames(self, done, total, phase):
        self._frames = (done, total, phase)
        self.framesChanged.emit()

    def holds_gpu(self) -> bool:
        return False

    def release_gpu(self):
        return None

    def _claim(self) -> bool:
        jobs = getattr(self, "jobs", None)
        return jobs is None or jobs.claim(JOB_ID, self)

    # ---- commands ------------------------------------------------------------------------------------

    @Slot()
    def start(self) -> None:
        """Blend every clip listed."""
        self._start(list(self._pairs))

    @Slot()
    def startPreviewClip(self) -> None:
        """Blend the clip the preview shows."""
        if 0 <= self._pv_clip < len(self._pairs):
            self._start([self._pairs[self._pv_clip]])

    def _start(self, pairs: List[Dict[str, str]]) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self.save()
        if not pairs:
            self.toast.emit("warning", "Nothing to blend",
                            f"No clip in this folder has both a DepthCrafter and a {self._map.value('other') or 'VDA'} "
                            "map. Make it on its tab, or with Splatting's right-click Rerun with Video Depth Anything "
                            "/ Rerun with DVD.")
            return
        if not self._claim():
            return
        opts = S.snapshot(self._global)       # the shared settings; a clip's own over them in the runner
        runner = R.BlendRunner(opts, _QtHooks(self), self._stop, self._stop_now, self._stats,
                               per_clip={c: dict(v) for c, v in self._own.items()})
        self._runner = runner
        self._stop.clear()
        self._stop_now.clear()
        self._busy = True
        self._done, self._total, self._clip, self._frames = 0, len(pairs), "", (0, 0, "")
        self.stateChanged.emit()
        self.infoChanged.emit()
        self.framesChanged.emit()

        def work():
            completed = False
            try:
                wait_for_gpu(self)
                completed = bool(runner.run_batch(pairs))
            except Exception:
                logger.exception("Blend batch failed")
                self._sigStatus.emit("Error: the batch failed. See console.")
            finally:
                self._sigFinished.emit(completed and not self._stop_now.is_set())

        self._thread = threading.Thread(target=work, name="blend-batch", daemon=True)
        self._thread.start()

    @Slot(bool)
    def _on_finished(self, completed: bool) -> None:
        runner, self._runner = self._runner, None
        self._busy = False
        self._thread = None
        outcome = runner.outcome if runner is not None else Outcome()
        if not completed and not outcome.error and not self._stop.is_set():
            outcome.error = "The batch ended with an error."
        v = verdict("Blend", outcome, stopped=self._stop.is_set())
        self._status = v.title
        self.stateChanged.emit()
        self.infoChanged.emit()
        made = runner.made if runner is not None else []
        text = v.text + (f"\nIn {self._map.value('depth_folder')}, where Splatting reads them." if made else "")
        self.toast.emit(v.kind, v.title, text)
        if made and self.splatting is not None:
            try:
                self.splatting.preview.queue.rescan()
            except Exception:
                logger.debug("Could not refresh Splatting's list after the blend", exc_info=True)
        self.rescan()

    @Slot()
    def stop(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            self._stop_now.set()
            self._stop.set()
            self._on_status("Stopping...")

    @Slot()
    def stopAfterClip(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            self._stop.set()
            self._on_status("Stopping after the current clip...")

    @Slot()
    def openOutputFolder(self) -> None:
        err = self._shell.openFolder(self._map.value("depth_folder"))
        if err:
            self.toast.emit("warning", "Open Folder", err)

    def shutdown(self) -> None:
        self._save_timer.stop()
        self.save()
        if self._thread is not None and self._thread.is_alive():
            self._stop_now.set()
            self._stop.set()
            self._thread.join(timeout=10)
