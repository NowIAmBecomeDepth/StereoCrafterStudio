"""controller.py -- the DepthCrafter stage as QML sees it.

Owns the settings (a QQmlPropertyMap QML binds to directly), starts the
runner on a worker thread, and turns the runner's hooks into Qt signals.

Threading
---------
The runner calls its hooks on the worker thread. Each hook emits a signal
connected with QueuedConnection, so the UI-side slot runs on the UI thread.
``ask`` additionally blocks the worker on a threading.Event until QML calls
:meth:`answerPrompt`, which is v1's ``_ask_yes_no_cancel`` arrangement. A
cancel or shutdown while a prompt is open answers it with Cancel, so the
worker can never be left waiting on a dialog nobody will see.
"""

from __future__ import annotations

import json
import logging
import math
import os
from studio import chapter_paths
import threading
from typing import Any, Dict, Optional

from PySide6.QtCore import Property, QObject, Qt, QTimer, Signal, Slot
from PySide6.QtQml import QQmlPropertyMap

from ... import settings_store
from ...legacy import REPO
from . import runner as R
from studio.gpu_jobs import wait_for_gpu
from studio.outcome import Outcome, verdict
from . import settings as S

logger = logging.getLogger("depthcrafter.v2")

HELP_FILE = REPO / "depthcrafter" / "help_content.json"

# v1 adds these at runtime (_load_help_content): absent from the JSON.
_HELP_FALLBACKS = {
    "latent_checkpoint": (
        "Full-video jobs only. After denoising finishes, write the denoised latents next to the output "
        "as <name>_latents.ckpt (~95 MB for a 2000-frame clip). If a later stage fails (decode, save, disk "
        "full), the next run can resume from those latents and skip the expensive denoising pass entirely.\n\n"
        "You will be asked whether to resume or start over whenever a checkpoint is found. A checkpoint "
        "recorded under different settings (window, overlap, steps, seed, resolution, frame count) is "
        "rejected automatically and the job restarts from scratch.\n\n"
        "The checkpoint is deleted once the job saves successfully."),
    "current_video_count": (
        "Which job of the batch is running, and how many there are in total. With Process as Segments on, "
        "a single video counts as several jobs - one per segment."),
    "resolution_preset": (
        "Pick a preset to fill Target Width/Height. All values are divisible by 64 (DepthCrafter/inpaint "
        "requirement). The boxes stay editable after selecting.\n\n"
        "Once a clip is loaded, the menu shows only the sizes for that clip's aspect ratio; the one Auto "
        "Size picks is marked 'auto'."),
    "auto_resolution": (
        "When a clip is loaded, fill Target Width/Height with the size picked for its aspect ratio: 768 wide "
        "with the height (a multiple of 64) nearest the ratio, unless Settings > DepthCrafter picks another "
        "for that ratio. You can still change the size after.\n\n"
        "In a folder of mixed shapes, the clips shaped like the loaded one run at Target Width/Height and "
        "each other clip runs at its own shape's pick."),
}


def _load_help() -> Dict[str, str]:
    texts = dict(_HELP_FALLBACKS)
    try:
        with open(HELP_FILE, "r", encoding="utf-8") as f:
            for key, content in json.load(f).items():
                if isinstance(content, dict) and "text" in content:
                    texts[key] = content["text"]
    except Exception:
        logger.warning("Could not load %s; tooltips will be limited.", HELP_FILE, exc_info=True)
    return texts


class _QtHooks(R.Hooks):
    """Runner hooks that forward to the controller's queued signals."""

    def __init__(self, ctl: "DepthCrafterController"):
        self.ctl = ctl

    def status(self, text):
        self.ctl._sigStatus.emit(str(text))

    def progress(self, done, total):
        self.ctl._sigProgress.emit(int(done), int(total))

    def clip(self, filename, resolution, frames):
        self.ctl._sigClip.emit(str(filename), str(resolution), str(frames))

    def segments(self, frames):
        """Process as Segments: the clip's segments to run, their frames in order (the ETA's)."""
        stats = getattr(self.ctl, "job_stats", None)
        if stats is not None:
            stats.dc_event("segments", list(frames))

    def ask(self, title, message, allow_cancel=True):
        return self.ctl._ask_from_worker(title, message, allow_cancel)


class DepthCrafterController(QObject):
    stateChanged = Signal()
    infoChanged = Signal()
    sceneCutChanged = Signal()
    chapterChanged = Signal()
    presetsChanged = Signal()
    shapeChanged = Signal()

    promptRequested = Signal(str, str, bool)
    toast = Signal(str, str, str)                # kind, title, message
    fieldRejected = Signal(str)                  # settings key whose edit was refused

    _sigStatus = Signal(str)
    _sigProgress = Signal(int, int)
    _sigClip = Signal(str, str, str)
    _sigPrompt = Signal(str, str, bool)
    _sigFinished = Signal(str, bool)             # task, ran to completion
    _sigShape = Signal(str, str, bool)           # input path, its shape ("" unknown), apply the auto pick

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self._shell = shell
        self._exr = S.openexr_available()
        self._choices = S.choices(self._exr)
        self._help = _load_help()

        stored = settings_store.load("depthcrafter", REPO / "config_depthcrafter.json", S.seed_from_v1)
        self._map = QQmlPropertyMap(self)
        for key, value in S.normalise(stored).items():
            self._map.insert(key, value)
        # The last value each field held that parsed, to put back when an
        # edit is refused. Filled now: a lazy fill would capture the bad value.
        self._last_good: Dict[str, Any] = self._values()
        self._map.valueChanged.connect(self._on_value_changed)
        # Upcoming Jobs (user, 2026-10-10): the clips a Start would process
        from studio.clip_queue import ClipQueue
        self.queue = ClipQueue("depthcrafter", self._values, self)
        QTimer.singleShot(0, self.queue.rescan)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self.save)

        self._task = ""
        self._status = "Ready"
        self._done = 0
        self._total = 0
        self._clip = ("N/A", "N/A", "N/A")
        self._stop = threading.Event()
        self._stop_now = threading.Event()  # Stop (not the safe point): cut off mid-clip (#13)
        self._thread: Optional[threading.Thread] = None
        self._prompt_event: Optional[threading.Event] = None
        self._prompt_answer: Optional[bool] = None

        self._chapters: list = []
        self._chapter_num = -1
        self._chapter_expected: Dict[str, str] = {}
        self._chapter_applying = False

        q = Qt.ConnectionType.QueuedConnection
        self._sigStatus.connect(self._on_status, q)
        self._sigProgress.connect(self._on_progress, q)
        self._sigClip.connect(self._on_clip, q)
        self._sigPrompt.connect(self.promptRequested, q)
        self._sigFinished.connect(self._on_finished, q)
        self._sigShape.connect(self._on_shape, q)

        # the loaded clip's aspect ratio (a CUSTOM_RATIOS label; user, 2026-10-05)
        self._shape = ""
        self._rerun = None          # Rerun in DepthCrafter waiting for its clip's shape: (clip, output)
        self._shape_path: Optional[str] = None     # the input the shape was probed for
        self._shape_for: Optional[str] = None      # ...and the one `_shape` actually belongs to

        self._apply_log_level()
        shell.projectChanged.connect(self.refreshChapters)
        shell.projectChanged.connect(self.refreshSceneCut)     # the project's own jobs only
        # the folders / chapter this stage last used in each project (user, 2026-10-02)
        from studio import project_memory
        project_memory.attach(self, "depthcrafter", shell, ("input_dir_or_file_var", "output_dir"))
        self.refreshChapters()
        self.refreshSceneCut()
        self._probe_input(apply=False)     # the stored input: narrow the menu, keep its size

    # ---- settings ----------------------------------------------------------

    settings = Property(QObject, lambda self: self._map, constant=True)
    choices = Property("QVariantMap", lambda self: self._choices, constant=True)
    help = Property("QVariantMap", lambda self: self._help, constant=True)
    exrAvailable = Property(bool, lambda self: self._exr, constant=True)

    @Property("QVariantList", notify=presetsChanged)
    def presets(self):
        cols = [{"category": cat, "items": [{"w": w, "h": h, "ratio": r or ""} for w, h, r in items]}
                for cat, items in S.RESOLUTION_PRESETS]
        # this machine's own columns: each size with how far it is from its
        # column's true ratio, when the column is a ratio (user, 2026-10-05)
        for cat, items in S.local_presets(settings_store.CONFIG_DIR):
            ratio = S.ratio_value(cat)
            cols.append({"category": cat, "items": [
                {"w": w, "h": h, "ratio": r or (S.disparity_label(w, h, ratio) if ratio else "")} for w, h, r in items]})
        if not self._shape:
            return cols
        # a clip is loaded: only its shape's columns, its auto pick marked
        # (user, 2026-10-05)
        shape = self._shape
        cols = [c for c in cols if shape in S.BUILTIN_COVERS.get(c["category"], (c["category"],))]
        aw, ah = S.auto_sizes(settings_store.CONFIG_DIR)[shape]
        found = False
        for c in cols:
            for it in c["items"]:
                if (it["w"], it["h"]) == (aw, ah):
                    it["ratio"] = "auto" + (" · " + it["ratio"] if it["ratio"] else "")
                    found = True
        if not found:
            cols.insert(0, {"category": shape, "items": [
                {"w": aw, "h": ah, "ratio": "auto · " + S.disparity_label(aw, ah, S.ratio_value(shape))}]})
        return cols

    @Property(str, notify=shapeChanged)
    def loadedShape(self):
        """The loaded clip's aspect ratio ('2.39:1'), '' when none is known."""
        return self._shape

    @Property(str, notify=shapeChanged)
    def presetNote(self):
        return f"Sizes for {self._shape}, the loaded clip's shape" if self._shape else ""

    # ---- the loaded clip's shape (user, 2026-10-05) ----------------------------
    def _probe_input(self, apply: bool) -> None:
        """Find the aspect ratio of the clip the input names (a folder: its
        first video) off the UI thread -- ffprobe on a cloud drive can take
        seconds. _on_shape takes it from there."""
        path = str(self._map.value("input_dir_or_file_var") or "")
        self._shape_path = path

        def work():
            shape = ""
            try:
                specs, _single = R.discover_sources(path)
                shape = self._shape_of(specs)
            except Exception:
                logger.debug("No clip shape for %s", path, exc_info=True)
            self._sigShape.emit(path, shape, apply)

        threading.Thread(target=work, name="depthcrafter-shape", daemon=True).start()

    @staticmethod
    def _shape_of(specs) -> str:
        """The aspect-ratio class of the first video among `specs` ("" when none)."""
        video = next((s for s in specs if s.get("type") in ("single_video_file", "video_file")), None)
        return (S.ratio_class(S.video_ratio(video["path"])) or "") if video is not None else ""

    @Slot(str, str, bool)
    def _on_shape(self, path: str, shape: str, apply: bool) -> None:
        if path != self._shape_path:
            return                          # the input changed again since
        changed = shape != self._shape
        self._shape = shape
        self._shape_for = path
        if changed:
            self.shapeChanged.emit()
            self.presetsChanged.emit()
        if apply and shape and bool(self._map.value("auto_resolution_var")) and not self._task:
            self._apply_auto_size()
        if self._rerun is not None and path == self._rerun[0]:
            self._finish_rerun(shape)

    def _apply_auto_size(self) -> None:
        w, h = S.auto_sizes(settings_store.CONFIG_DIR)[self._shape]
        if (int(self._map.value("target_width")), int(self._map.value("target_height"))) != (w, h):
            self.applyPreset(w, h)
            logger.info(f"Auto size: the loaded clip is {self._shape}, so {w}x{h}.")

    @Slot(result="QVariantMap")
    def autoSizes(self):
        """Each ratio's auto pick: {label: "WxH"}."""
        return {lab: f"{w}x{h}" for lab, (w, h) in S.auto_sizes(settings_store.CONFIG_DIR).items()}

    @Slot(str, result=str)
    def defaultAutoSize(self, label: str) -> str:
        w, h = S.default_auto_size(label)
        return f"{w}x{h}"

    @Slot("QVariantMap", result=bool)
    def saveAutoSizes(self, picks) -> bool:
        """Save each ratio's auto pick ({label: "WxH"}); the next clip loaded
        uses them, and the menu's 'auto' mark follows at once."""
        try:
            data = {lab: tuple(int(v) for v in str(key).split("x")) for lab, key in dict(picks or {}).items()}
            S.save_auto_sizes(settings_store.CONFIG_DIR, data)
        except Exception:
            logger.exception("Could not save the auto sizes")
            return False
        self.presetsChanged.emit()
        return True

    # ---- custom presets (Settings > DepthCrafter; user, 2026-10-05) ----------
    @Slot(result="QVariantList")
    def customRatios(self):
        return list(S.CUSTOM_RATIOS)

    #: what a size typed into the editor must be a multiple of
    presetStep = Property(int, lambda self: 64, constant=True)

    @Slot(str, result="QVariantList")
    def customSizes(self, label: str):
        """The sizes offered for an aspect ratio: [{w, h, key, diff}] -- the steps,
        plus any size saved for it (typed in by hand) and its Auto pick."""
        return S.RP.offered(label, S.sizes_for, S.local_presets(settings_store.CONFIG_DIR),
                            S.auto_sizes(settings_store.CONFIG_DIR))

    @Slot(result="QVariantMap")
    def customSaved(self):
        """What is saved now: {ratio label: ["WxH", ...]}, the offered ratios only."""
        return {cat: [f"{w}x{h}" for w, h, _t in items]
                for cat, items in S.local_presets(settings_store.CONFIG_DIR) if cat in S.CUSTOM_RATIOS}

    @Slot("QVariantMap", result=bool)
    def saveCustomPresets(self, picked) -> bool:
        """Save the sizes ticked per ratio ({label: ["WxH", ...]}); they show in
        the Resolution Preset menu at once."""
        try:
            data = {}
            for cat, keys in dict(picked or {}).items():
                if cat not in S.CUSTOM_RATIOS:
                    continue
                data[cat] = [tuple(int(v) for v in str(k).split("x")) for k in (keys or [])]
            for cat in S.CUSTOM_RATIOS:
                data.setdefault(cat, [])
            S.save_local_presets(settings_store.CONFIG_DIR, data)
        except Exception:
            logger.exception("Could not save the custom resolution presets")
            return False
        self.presetsChanged.emit()
        return True

    def _values(self) -> Dict[str, Any]:
        return {k: self._map.value(k) for k in self._map.keys()}

    def _on_value_changed(self, key: str, value: Any) -> None:
        """A write from QML. Coerce it, refuse it if it will not coerce."""
        default = S.DEFAULTS.get(key)
        if default is not None and not isinstance(default, (bool, str)):
            text = str(value).strip()
            try:
                if not math.isfinite(float(text)):    # "inf" / "1e999" pass float() (bug sweep 2026-10-09)
                    raise ValueError(text)
            except ValueError:
                # Put back the last good value so the field shows it again.
                self._map.insert(key, self._last_good.get(key, default))
                self.fieldRejected.emit(key)
                return
        coerced = S.coerce(key, value)
        if coerced != value or type(coerced) is not type(value):
            self._map.insert(key, coerced)
        self._last_good[key] = coerced

        if key == "process_as_segments_var" and not coerced:
            # v1: switching segments off clears the merge-only flags, so a
            # later full-video run is not affected by options it cannot see.
            for flag in S.MERGE_ONLY_FLAGS:
                self._map.insert(flag, False)
        if key == "debug_logging":
            self._apply_log_level()
        if key == "auto_resolution_var" and coerced and self._shape and not self._task:
            self._apply_auto_size()
        if key in ("input_dir_or_file_var", "output_dir") and not self._chapter_applying:
            self._maybe_leave_chapter()
        self._save_timer.start()

    jobClips = Property(QObject, lambda self: self.queue, constant=True)

    @Slot()
    def save(self) -> None:
        settings_store.save("depthcrafter", S.normalise(self._values()))
        self.queue.rescan()        # a typed, picked or imported input: its clips (held during a job)
        # typing, Browse, a chapter, Scene Cut's import and a project switch
        # all end in this save: a new input is a newly loaded clip
        if str(self._map.value("input_dir_or_file_var") or "") != self._shape_path:
            self._probe_input(apply=True)

    def _replace_all(self, data: Dict[str, Any]) -> None:
        clean = S.normalise(data)
        for key, value in clean.items():
            self._map.insert(key, value)
        self._last_good = dict(clean)
        self._apply_log_level()
        self.save()

    @Slot()
    def resetDefaults(self) -> None:
        keep = {k: self._map.value(k) for k in ("input_dir_or_file_var", "output_dir")}
        self._replace_all({**S.DEFAULTS, **keep})
        self.toast.emit("info", "Settings reset", "Every DepthCrafter setting is back to its default. Your folders were kept.")

    @Slot(str)
    def loadSettingsFrom(self, path: str) -> None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            known = {k: v for k, v in data.items() if k in S.DEFAULTS}
            self._replace_all({**self._values(), **known})
            self.toast.emit("success", "Settings loaded", os.path.basename(path))
        except Exception as e:
            logger.warning("Could not load settings from %s: %s", path, e)
            self.toast.emit("error", "Could not load settings", str(e))

    @Slot(str)
    def saveSettingsTo(self, path: str) -> None:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(S.normalise(self._values()), f, indent=4)
            self.toast.emit("success", "Settings saved", path)
        except Exception as e:
            self.toast.emit("error", "Could not save settings", str(e))

    @Slot(int, int)
    def applyPreset(self, w: int, h: int) -> None:
        self._map.insert("target_width", int(w))
        self._map.insert("target_height", int(h))
        self._last_good["target_width"], self._last_good["target_height"] = int(w), int(h)
        self._save_timer.start()

    def _apply_log_level(self) -> None:
        debug = bool(self._map.value("debug_logging"))
        logging.getLogger().setLevel(logging.DEBUG if debug else logging.INFO)

    # ---- run state ----------------------------------------------------------

    busy = Property(bool, lambda self: bool(self._task), notify=stateChanged)
    task = Property(str, lambda self: self._task, notify=stateChanged)
    status = Property(str, lambda self: self._status, notify=infoChanged)
    progressDone = Property(int, lambda self: self._done, notify=infoChanged)
    progressTotal = Property(int, lambda self: self._total, notify=infoChanged)
    clipName = Property(str, lambda self: self._clip[0], notify=infoChanged)
    clipResolution = Property(str, lambda self: self._clip[1], notify=infoChanged)
    clipFrames = Property(str, lambda self: self._clip[2], notify=infoChanged)

    @Property(str, notify=infoChanged)
    def progressCount(self):
        """v1 _update_progress_count: the job being WORKED ON, "n / total"."""
        current = self._done + 1 if self._task == "batch" else self._done
        if self._total <= 1 or current <= 0:
            return ""
        return f"{min(current, self._total)} / {self._total}"

    @Slot(str)
    def _on_status(self, text):
        self._status = text
        self.infoChanged.emit()

    @Slot(int, int)
    def _on_progress(self, done, total):
        self._done, self._total = done, total
        self.infoChanged.emit()

    @Slot(str, str, str)
    def _on_clip(self, name, res, frames):
        self._clip = (name, res, frames)
        stats = getattr(self, "job_stats", None)
        if stats is not None and self._task == "batch":
            stats.batch_clip("depthcrafter", name)
        self.infoChanged.emit()

    def _claim(self) -> bool:
        """v1 _claim_job: the shell's request_job (studio/gpu_jobs.py)."""
        jobs = getattr(self, "jobs", None)
        return jobs is None or jobs.claim("depthcrafter", self)

    def _begin(self, task: str, target, *args, before=None) -> bool:
        """Start `task` on its thread. `before` runs once the run is really starting --
        past the busy check and the GPU claim -- so a refused Start leaves the page's
        figures from the last run alone (bug sweep 2026-10-09, A3)."""
        if self._thread is not None and self._thread.is_alive():
            self.toast.emit("warning", "Busy", "Another DepthCrafter job is running. Please wait.")
            return False
        if not self._claim():
            self._on_status("Ready")
            return False
        if before is not None:
            before()
        self._stop.clear()
        self._stop_now.clear()
        self._task = task
        self.queue.hold(True)           # the list stays as it is while the job runs
        self.stateChanged.emit()
        self.infoChanged.emit()

        def work():
            completed = False
            timeline = None
            try:
                wait_for_gpu(self)          # the other stages have really let go (audit #14)
                stats = getattr(self, "job_stats", None)
                if task == "batch" and stats is not None:
                    try:                    # the ETA under the bar (job_stats.DcEta; user, 2026-10-12)
                        import depthcrafter.depthcrafter_logic as logic
                        from ...job_stats import DepthCrafterTimeline
                        timeline = DepthCrafterTimeline(logic, stats)
                    except Exception:
                        logger.debug("DepthCrafter ETA unavailable for this run", exc_info=True)
                completed = bool(target(*args))
            except Exception:
                logger.exception("DepthCrafter %s failed", task)
                self._sigStatus.emit(f"Error: {task} failed. See console.")
            finally:
                if timeline is not None:
                    timeline.restore()
                self._sigFinished.emit(task, completed and not self._stop.is_set())

        self._thread = threading.Thread(target=work, name=f"depthcrafter-{task}", daemon=True)
        self._thread.start()
        return True

    @Slot(str, bool)
    def _on_finished(self, task, completed):
        self._task = ""
        self._thread = None
        self.queue.hold(False)
        self.queue.rescan()             # the clips the batch moved to finished leave the list
        self.stateChanged.emit()
        self.infoChanged.emit()
        if task == "batch":
            # audit #3: a batch where every clip failed (or the model never
            # loaded) is not a success, and hands nothing on
            runner, self._batch_runner = getattr(self, "_batch_runner", None), None
            outcome = runner.outcome if runner is not None else Outcome()
            if not completed and not outcome.error and not self._stop.is_set():
                outcome.error = "The batch ended with an error."
            v = verdict("DepthCrafter", outcome, stopped=self._stop.is_set())
            test, self._test_run = getattr(self, "_test_run", False), False
            if v.handoff and not test:           # a Test Render isn't handed on
                self._record_job_handoff(runner.opts if runner is not None else None)
            self.toast.emit(v.kind, ("Test render: " if test else "") + v.title,
                            v.text + (f"\nIn {runner.opts.get('output_dir')}" if test and runner is not None else ""))

    # ---- commands -------------------------------------------------------------

    @Slot()
    def startSelected(self) -> None:
        """Process Selected (user, 2026-10-10): the clips ticked in Upcoming Jobs only."""
        picked = self.queue.checked_paths()
        if not picked:
            self.toast.emit("warning", "Process Selected", "Tick the clips to process in Upcoming Jobs first.")
            return
        self.start(picked)

    @Slot(str)
    def startClip(self, path: str) -> None:
        """Upcoming Jobs' "Process This Clip Now" (user, 2026-10-10)."""
        if path:
            self.start([path])

    @Slot(str)
    def testClip(self, path: str) -> None:
        """Upcoming Jobs' "Test Render" (user, 2026-10-15; studio.test_renders): the clip into
        <output>/Test Renders, nothing moved to finished/, not handed on."""
        if path:
            self.start([path], test=True)

    @Slot()
    def start(self, only=None, test: bool = False) -> None:
        """The batch -- or, from Upcoming Jobs, only the clips in `only`; `test`: a Test Render."""
        from studio.clip_queue import move_pending
        if move_pending(getattr(self, "queue", None), self.toast):
            return
        self.save()
        opts = S.snapshot(self._values())
        problem = S.window_overlap_problem(opts)  # audit #21 / #22
        if problem:
            self._on_status("Ready")
            self.toast.emit("error", "Cannot start", problem)
            return
        path = opts["input_dir_or_file_var"]
        self._on_status("Scanning input folder...")
        logger.info("Scanning input folder: Please wait...")
        try:
            specs, single = R.discover_sources(path)
        except R.InputError as e:
            logger.error("GUI: %s", e)
            self._on_status("Ready")
            self.toast.emit("error", "Cannot start", str(e))
            return
        if only:                        # Process Selected / This Clip Now: just those
            keys = {os.path.normcase(os.path.abspath(p)) for p in only}
            specs = [s for s in specs if os.path.normcase(os.path.abspath(s["path"])) in keys]
            logger.info("Process Selected: %d clip(s).", len(specs))
        if not specs:
            logger.warning(f"GUI: No valid video files or image sequences found in '{path}'.")
            self._on_status("Nothing to process.")
            self.toast.emit("warning", "Nothing to process", f"No videos or image sequences found in\n{path}")
            return
        seed = R.effective_seed(opts["seed"])
        # Auto size: the shape Target Width/Height were set for, and every
        # shape's pick for the clips of other shapes (runner._size_for_source)
        if opts.get("auto_resolution_var") and self._shape_for != path:
            # still being read for this input (or not yet asked): read it now, from
            # the clips just found -- the previous input's shape would have the
            # runner replace the size set for these clips (bug sweep 2026-10-09, A3)
            shape = self._shape_of(specs)
            if shape != self._shape:
                self._shape = shape
                self.shapeChanged.emit()
                self.presetsChanged.emit()
            self._shape_path = self._shape_for = path
        opts["auto_loaded_class"] = self._shape or None
        opts["auto_sizes"] = S.auto_sizes(settings_store.CONFIG_DIR)
        self._test_run = bool(test)
        if test:
            from studio import test_renders
            opts["output_dir"] = test_renders.folder(opts["output_dir"])
            opts["test_render"] = True        # a test before replaces it without asking
            logger.info("Test render: %s into %s", ", ".join(os.path.basename(s["path"]) for s in specs),
                        opts["output_dir"])
        # Resume off (user, 2026-10-25): the sources stay where they are, as for a single clip
        runner = R.DepthCrafterRunner(opts, _QtHooks(self), self._stop,
                                      move_originals=S.moves_finished(opts, single, test),
                                      stop_now=self._stop_now)

        def before():
            self._clip = ("N/A", "N/A", "N/A")
            self._done, self._total = 0, len(specs)
            self._on_status(f"Starting processing {len(specs)} files/sequences...")
            self._batch_runner = runner
        if self._begin("batch", runner.run_batch, specs, seed, before=before):
            stats = getattr(self, "job_stats", None)
            if stats is not None and getattr(self, "queue", None) is not None:
                try:                    # the batch's ETA beside the clip's (user, 2026-10-21)
                    stats.batch_begin("depthcrafter", self.queue.frames_of(
                        [s["path"] for s in specs], int(opts.get("process_length", -1) or -1)))
                except Exception:
                    logger.debug("No batch ETA for this run", exc_info=True)

    @Slot()
    def cancel(self) -> None:
        """Stop now (audit #13, user 2026-09-29): the clip is cut off at the
        model's next denoising step. The page asked first."""
        if self._thread is not None and self._thread.is_alive():
            logger.info("Stop requested: stopping now.")
            self._stop_now.set()
            self._stop.set()
            self._on_status("Stopping...")
            self._resolve_prompt(None)

    @Slot()
    def cancelAtSafePoint(self) -> None:
        """Stop after the segment (or, for a clip processed whole, the clip)
        being processed: what is finished is kept, and Resume carries on."""
        if self._thread is not None and self._thread.is_alive():
            logger.info("Stop requested: stopping after the current segment.")
            self._stop.set()
            self._on_status("Stopping after the current segment...")
            self._resolve_prompt(None)

    @Slot(str, str)
    def remerge(self, master_meta_path: str, output_path: str) -> None:
        if R._merge_module() is None:
            self.toast.emit("error", "Re-merge unavailable", "The merge module could not be loaded.")
            return
        logger.info(f"--- Starting Re-Merge for: {os.path.basename(master_meta_path)} ---")
        runner = R.DepthCrafterRunner(S.snapshot(self._values()), _QtHooks(self), self._stop)
        self._begin("remerge", runner.run_remerge, master_meta_path, output_path,
                    before=lambda: setattr(self, "_done", 0) or setattr(self, "_total", 1))

    @Slot(str)
    def generateVisuals(self, master_meta_path: str) -> None:
        fmt = self._map.value("keep_intermediate_segment_visual_format_var")
        if fmt == "none":
            self.toast.emit("info", "Pick a format", "Segment Format is 'none'. Choose a format under Merged Output Options.")
            return
        logger.info(f"--- Starting Segment Visual Generation for: {os.path.basename(master_meta_path)} (Format: {fmt}) ---")
        runner = R.DepthCrafterRunner(S.snapshot(self._values()), _QtHooks(self), self._stop)
        self._begin("visuals", runner.run_generate_visuals, master_meta_path, fmt,
                    before=lambda: setattr(self, "_done", 0) or setattr(self, "_total", 1))

    @Slot(str)
    def restoreInputs(self, folder_type: str) -> None:
        try:
            restored, errors, msg = R.restore_input_files(self._map.value("input_dir_or_file_var"), folder_type)
            self._on_status(msg)
            self.toast.emit("warning" if errors else "success", f"Restore {folder_type.capitalize()}", msg)
        except R.InputError as e:
            self.toast.emit("error", f"Restore {folder_type.capitalize()}", str(e))

    @Slot()
    def openOutputFolder(self) -> None:
        # during a batch, the folder it is writing to -- a project switch can change the page's
        runner = getattr(self, "_batch_runner", None)
        folder = runner.opts.get("output_dir") if runner is not None else None
        err = self._shell.openFolder(folder or self._map.value("output_dir"))
        if err:
            self.toast.emit("warning", "Open Output Folder", err)

    # ---- prompts --------------------------------------------------------------

    def _ask_from_worker(self, title, message, allow_cancel) -> Optional[bool]:
        if self._stop.is_set():
            return None
        event = threading.Event()
        self._prompt_event = event
        self._prompt_answer = None
        self._sigPrompt.emit(title, message, allow_cancel)
        event.wait()
        self._prompt_event = None
        return self._prompt_answer

    def _resolve_prompt(self, answer: Optional[bool]) -> None:
        event = self._prompt_event
        if event is not None:
            self._prompt_answer = answer
            event.set()

    @Slot(int)
    def answerPrompt(self, code: int) -> None:
        """1 = Yes, 0 = No, -1 = Cancel."""
        self._resolve_prompt(True if code > 0 else (False if code == 0 else None))

    def shutdown(self) -> None:
        self._save_timer.stop()
        self.save()
        if self._thread is not None and self._thread.is_alive():
            logger.info("Stopping processing before exit...")
            self._stop_now.set()  # quitting stops now (#13)
            self._stop.set()
            self._resolve_prompt(None)
            self._thread.join(timeout=10)
            if self._thread is not None and self._thread.is_alive():
                logger.warning("Processing thread did not terminate gracefully. Forcing exit.")

    # ---- Scene Cut import and job handoff (v1 job_handoff) -------------------

    @Property(str, notify=sceneCutChanged)
    def sceneCutDescription(self):
        return self._scene_cut_desc

    @Slot()
    def refreshSceneCut(self) -> None:
        self._scene_cut_desc = ""
        try:
            from core.ui.job_handoff import describe, get_source_record, in_project
            record = get_source_record("depthcrafter")
            if record and record.get("source") and in_project(record, self._shell.projectFolder, ("source",)):
                self._scene_cut_desc = describe(record) or record["source"]
        except Exception:
            logger.debug("Could not read the Scene Cut handoff", exc_info=True)
        self.sceneCutChanged.emit()

    @Slot()
    def importFromSceneCut(self) -> None:
        try:
            from core.ui.job_handoff import get_source_record, in_project
            record = get_source_record("depthcrafter") or {}
            source = record.get("source") if in_project(record, self._shell.projectFolder, ("source",)) else None
            if source:
                self._map.insert("input_dir_or_file_var", os.path.normpath(source))
                self._last_good["input_dir_or_file_var"] = os.path.normpath(source)
                self._maybe_leave_chapter()
                self._save_timer.start()
                self._on_status("Imported source clips from Scene Cut.")
        except Exception:
            logger.exception("Import from Scene Cut failed")

    def _record_job_handoff(self, opts: Optional[dict] = None) -> None:
        """Remember this job's paths so Splatting (v1 or v2) can import them.

        The folders the run used (its Start-time snapshot), not the page's at the end: a
        project switch during the batch rewrites the page (bug sweep 2026-10-09, A3)."""
        opts = opts or {}
        try:
            from core.ui.job_handoff import record_job
            record_job("depthcrafter", {
                "source": opts.get("input_dir_or_file_var") or self._map.value("input_dir_or_file_var"),
                "depth": opts.get("output_dir") or self._map.value("output_dir")})
        except Exception:
            logger.debug("Could not record job handoff", exc_info=True)

    # ---- chapters (v1: CtkThemedStage._install_chapter_picker) ----------------

    chapters = Property("QVariantList", lambda self: self._chapters, notify=chapterChanged)
    chapterNum = Property(int, lambda self: self._chapter_num, notify=chapterChanged)
    hasProject = Property(bool, lambda self: bool(self._shell.projectFolder), notify=chapterChanged)

    @Slot()
    def refreshChapters(self) -> None:
        self._chapters = self._shell.listChapters()
        saved = self._shell.stageChapter("depthcrafter")
        nums = {c["num"] for c in self._chapters}
        self._chapter_num = saved.get("num", -1) if saved.get("num") in nums else -1
        if self._chapter_num >= 0:
            self._chapter_expected = self._shell.stagePaths("depthcrafter", self._chapter_num, saved.get("name", ""))
        self.chapterChanged.emit()

    @Slot(int)
    def pickChapter(self, num: int) -> None:
        """Point input and output at one chapter; -1 = manual paths."""
        if num < 0:
            self._chapter_num = -1
            self._chapter_expected = {}
            self._shell.setStageChapter("depthcrafter", {})
            self.chapterChanged.emit()
            return
        name = next((c["name"] for c in self._chapters if c["num"] == num), "")
        paths = self._shell.stagePaths("depthcrafter", num, name)
        self._chapter_applying = True
        try:
            for role, key in (("input", "input_dir_or_file_var"), ("output", "output_dir")):
                if paths.get(role):
                    value = os.path.normpath(paths[role])
                    self._map.insert(key, value)
                    self._last_good[key] = value
        finally:
            self._chapter_applying = False
        self._chapter_num = num
        self._chapter_expected = paths
        self._shell.setStageChapter("depthcrafter", {"num": num, "name": name})
        self._save_timer.start()
        self.chapterChanged.emit()
        if not getattr(self, "_restoring", False):        # a project switch restores quietly
            self.toast.emit("info", "Chapter selected", f"{name}\nInput and output now point at this chapter.")

    @Slot(str, str)
    def loadForRerun(self, source: str, output: str) -> None:
        """Splatting's "Rerun in DepthCrafter" (user, 2026-10-10): that one clip
        as the input, the folder its depth map lives in as the output; the
        settings are left for the user, and Start runs it. A single clip is not
        moved to finished, so it stays where Splatting lists it, and its new
        map lands where Splatting reads the old one."""
        if self._task:
            self.toast.emit("warning", "DepthCrafter is busy", "Wait for its job to finish, then try again.")
            return
        source, output = os.path.normpath(source), os.path.normpath(output) if output else ""
        for key, value in (("input_dir_or_file_var", source), ("output_dir", output)):
            if value:
                self._map.insert(key, value)
                self._last_good[key] = value
        self._maybe_leave_chapter()           # a single clip isn't a chapter's paths
        # Its own size (user, 2026-10-11): the clip's auto size whether or not Auto Size is
        # on -- a clip re-run from Splatting may not have the last input's shape. Read off
        # the UI thread; _on_shape finishes (and says the size). Probed here, so save()
        # doesn't probe it a second time.
        self._rerun = (str(source), output)
        self._probe_input(apply=False)
        self.save()

    def _finish_rerun(self, shape: str) -> None:
        source, output = self._rerun
        self._rerun = None
        size = ""
        if shape and not self._task:
            w, h = S.auto_sizes(settings_store.CONFIG_DIR)[shape]
            was = (int(self._map.value("target_width")), int(self._map.value("target_height")))
            self.applyPreset(w, h)
            # the change said (user, 2026-10-11: "add the aspect ratio resolution change")
            size = (f"\nSize: {was[0]}x{was[1]} \u2192 {w}x{h} (the auto size for {shape})" if was != (w, h)
                    else f"\nSize: {w}x{h}, already the auto size for {shape}")
        self.toast.emit("info", "Ready to re-run",
                        f"{os.path.basename(source)}\nOutput: {output or '(unchanged)'}{size}\n"
                        "Check the settings, then press Start.")

    def _maybe_leave_chapter(self) -> None:
        """Editing a path by hand switches back to Manual paths (v1)."""
        if self._chapter_num < 0:
            return
        exp = self._chapter_expected
        # a job moving the clips a finished level down is not a hand edit
        if not (chapter_paths.same_place(exp.get("input"), self._map.value("input_dir_or_file_var"))
                and chapter_paths.same_place(exp.get("output"), self._map.value("output_dir"))):
            self._chapter_num = -1
            self._shell.setStageChapter("depthcrafter", {})
            self.chapterChanged.emit()
