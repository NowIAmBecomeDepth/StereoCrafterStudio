"""controller.py -- Video Depth Anything (DepthCrafter's VDA tab) as QML sees it.

DepthCrafter's controller, cut down to what VDA has: its own folders and settings (config/vda.json),
Upcoming Jobs, chapters, Scene Cut's import, the job handoff Splatting imports from -- recorded as
DepthCrafter's, since its maps are depth maps like DepthCrafter's -- and Splatting's "Rerun with
Video Depth Anything" (loadForRerun).

In GpuJobs it is "depthcrafter_vda" (JOB_ID): its own id, so it never runs beside DepthCrafter, and
its logs go to DepthCrafter's console tab. The model is released at the end of every batch, so between jobs it holds
nothing (user, 2026-10-15: release the GPU tightly).

Threading as DepthCrafter's: the runner reports through queued signals.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
from typing import Any, Dict, Optional

from PySide6.QtCore import Property, QObject, Qt, QTimer, Signal, Slot
from PySide6.QtQml import QQmlPropertyMap

from studio import chapter_paths
from studio.gpu_jobs import wait_for_gpu
from studio.outcome import Outcome, verdict

from ... import settings_store
from . import runner as R
from . import settings as S

logger = logging.getLogger("vda")

#: the settings folder's file (config/vda.json)
OWNER = "vda"
#: whose chapter paths and job handoff it shares
STAGE = "depthcrafter"
#: its GpuJobs id (app.build)
JOB_ID = "depthcrafter_vda"

HELP: Dict[str, str] = {
    "input_dir_or_file": "A folder of videos to give depth maps, or a single video. Image sequences are "
                         "DepthCrafter's.",
    "output_dir": "Where the depth maps go: <clip>_depth.mp4, as DepthCrafter names them, so Splatting finds "
                  "them the same way. A DepthCrafter map of the same clip already there moves into a "
                  "'DepthCrafter' folder inside this one, so the new map shows in Splatting at once.",
    "model_size": "Small (Apache-2.0) is fastest; Large (CC-BY-NC-4.0, non-commercial) is the most detailed "
                  "and what iw3 runs as VDA_L. Base sits between them (also CC-BY-NC-4.0). Each is its own "
                  "download in Settings > Studio > Models.",
    "input_size": "The short side the frames are run at, a multiple of 14 (518 is what the model is trained "
                  "at; 392 is iw3's default). Higher keeps finer detail and costs time and memory. The map is "
                  "written at about this size; Splatting scales it to its own.",
    "run_mode": "Online (iw3's): 32-frame windows, each joined to the last, so memory stays the same however "
                "long the clip is. Offline: Video Depth Anything's own reference loop, which holds the whole "
                "clip in memory -- short clips, and comparing.",
    "normalise_mode": "Whole clip: one depth range for the whole clip, as DepthCrafter does -- a thing keeps its "
                      "depth however the rest of the shot changes. EMA (iw3's): the range follows the clip, "
                      "eased by EMA Decay and looking ahead EMA Buffer frames, so a long clip whose depth range "
                      "really changes keeps using all of it.",
    "ema": "EMA Decay: how slowly the range follows (0.9 is iw3's; closer to 1 is steadier). Buffer: how many "
           "frames ahead each frame's range looks (30 is iw3's).",
    "use_fp16": "Half precision on the card: about twice as fast, the same maps to the eye.",
    "process_length": "Only the first this many frames of each clip; -1 for all of them.",
    "output_suffix": "Added to the clip's name: Splatting looks for _depth.",
    "move_to_finished": "On: each clip whose depth map is done moves into a 'finished' folder inside the input "
                        "folder (a failed one into 'failed'), so the next Start skips it and an interrupted batch "
                        "picks up where it stopped. Off: the clips stay where they are, and every Start runs them "
                        "all again.",
}


class _QtHooks(R.Hooks):
    def __init__(self, ctl: "VdaController"):
        self.ctl = ctl

    def status(self, text):
        self.ctl._sigStatus.emit(str(text))

    def progress(self, done, total):
        self.ctl._sigProgress.emit(int(done), int(total))

    def clip(self, filename, resolution, frames):
        self.ctl._sigClip.emit(str(filename), str(resolution), str(frames))

    def frames(self, done, total, phase):
        self.ctl._sigFrames.emit(int(done), int(total), str(phase))


class VdaController(QObject):
    stateChanged = Signal()
    infoChanged = Signal()
    framesChanged = Signal()
    sceneCutChanged = Signal()
    chapterChanged = Signal()
    weightsChanged = Signal()

    toast = Signal(str, str, str)
    fieldRejected = Signal(str)

    _sigStatus = Signal(str)
    _sigProgress = Signal(int, int)
    _sigClip = Signal(str, str, str)
    _sigFrames = Signal(int, int, str)
    _sigFinished = Signal(bool)
    _sigWeights = Signal()                       # weights_paths' listener runs on any thread

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self._shell = shell
        self.splatting = None           # app.build: the clip a rerun replaced is reloaded there
        stored = settings_store.load(OWNER)
        self._map = QQmlPropertyMap(self)
        for key, value in S.normalise(stored).items():
            self._map.insert(key, value)
        self._last_good: Dict[str, Any] = self._values()
        self._map.valueChanged.connect(self._on_value_changed)
        from studio.clip_queue import ClipQueue
        self.queue = ClipQueue(STAGE, self._values, self)     # DepthCrafter's listing: the same keys
        QTimer.singleShot(0, self.queue.rescan)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self.save)

        self._task = ""
        self._status = "Ready"
        self._done = self._total = 0
        self._clip = ("N/A", "N/A", "N/A")
        self._frames = (0, 0, "")
        self._stop = threading.Event()
        self._stop_now = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._runner: Optional[R.VdaRunner] = None
        self._test_run = False
        self._scene_cut_desc = ""

        self._chapters: list = []
        self._chapter_num = -1
        self._chapter_expected: Dict[str, str] = {}
        self._chapter_applying = False

        q = Qt.ConnectionType.QueuedConnection
        self._sigStatus.connect(self._on_status, q)
        self._sigProgress.connect(self._on_progress, q)
        self._sigClip.connect(self._on_clip, q)
        self._sigFrames.connect(self._on_frames, q)
        self._sigFinished.connect(self._on_finished, q)
        self._sigWeights.connect(self.weightsChanged, q)

        shell.projectChanged.connect(self.refreshChapters)
        shell.projectChanged.connect(self.refreshSceneCut)
        from studio import project_memory
        project_memory.attach(self, OWNER, shell, ("input_dir_or_file_var", "output_dir"))
        self.refreshChapters()
        self.refreshSceneCut()
        try:                                # a download landing, or the models folder moving
            from core import weights_paths
            weights_paths.on_change(self._weights_moved)
        except Exception:
            logger.debug("No models-folder listener", exc_info=True)

    # ---- settings ---------------------------------------------------------------------------

    settings = Property(QObject, lambda self: self._map, constant=True)
    choices = Property("QVariantMap", lambda self: S.choices(), constant=True)
    help = Property("QVariantMap", lambda self: HELP, constant=True)
    jobClips = Property(QObject, lambda self: self.queue, constant=True)

    def _values(self) -> Dict[str, Any]:
        return {k: self._map.value(k) for k in self._map.keys()}

    def _on_value_changed(self, key: str, value: Any) -> None:
        default = S.DEFAULTS.get(key)
        if default is not None and not isinstance(default, (bool, str)):
            text = str(value).strip()
            try:
                if not math.isfinite(float(text)):
                    raise ValueError(text)
            except ValueError:
                self._map.insert(key, self._last_good.get(key, default))
                self.fieldRejected.emit(key)
                return
        coerced = S.coerce(key, value)
        if coerced != value or type(coerced) is not type(value):
            self._map.insert(key, coerced)
        self._last_good[key] = coerced
        if key == "model_size":
            self.weightsChanged.emit()
        if key in ("input_dir_or_file_var", "output_dir") and not self._chapter_applying:
            self._maybe_leave_chapter()
        self._save_timer.start()

    @Slot()
    def save(self) -> None:
        settings_store.save(OWNER, S.normalise(self._values()))
        self.queue.rescan()

    def _replace_all(self, data: Dict[str, Any]) -> None:
        clean = S.normalise(data)
        for key, value in clean.items():
            self._map.insert(key, value)
        self._last_good = dict(clean)
        self.weightsChanged.emit()
        self.save()

    @Slot()
    def resetDefaults(self) -> None:
        keep = {k: self._map.value(k) for k in ("input_dir_or_file_var", "output_dir")}
        self._replace_all({**S.DEFAULTS, **keep})
        self.toast.emit("info", "Settings reset",
                        "Every Video Depth Anything setting is back to its default. Your folders were kept.")

    @Slot(str)
    def loadSettingsFrom(self, path: str) -> None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._replace_all({**self._values(), **{k: v for k, v in data.items() if k in S.DEFAULTS}})
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

    # ---- weights ----------------------------------------------------------------------------

    def _weights_moved(self) -> None:
        self._sigWeights.emit()

    @Property(bool, notify=weightsChanged)
    def weightsInstalled(self):
        """The picked size's checkpoint is in the models folder."""
        from core.vda import model as M
        try:
            return M.checkpoint_path(str(self._map.value("model_size"))).is_file()
        except Exception:
            return False

    @Property(str, notify=weightsChanged)
    def weightsKey(self):
        """The Models window's row for the picked size (Models.start takes it)."""
        return {"Small": "vda_s", "Base": "vda_b", "Large": "vda_l"}.get(str(self._map.value("model_size")), "vda_l")

    @Slot()
    def recheckWeights(self) -> None:
        self.weightsChanged.emit()

    # ---- run state ----------------------------------------------------------------------------

    busy = Property(bool, lambda self: bool(self._task), notify=stateChanged)
    status = Property(str, lambda self: self._status, notify=infoChanged)
    progressDone = Property(int, lambda self: self._done, notify=infoChanged)
    progressTotal = Property(int, lambda self: self._total, notify=infoChanged)
    clipName = Property(str, lambda self: self._clip[0], notify=infoChanged)
    clipResolution = Property(str, lambda self: self._clip[1], notify=infoChanged)
    clipFrames = Property(str, lambda self: self._clip[2], notify=infoChanged)
    framesDone = Property(int, lambda self: self._frames[0], notify=framesChanged)
    framesTotal = Property(int, lambda self: self._frames[1], notify=framesChanged)
    framesPhase = Property(str, lambda self: self._frames[2], notify=framesChanged)

    @Property(str, notify=infoChanged)
    def progressCount(self):
        current = self._done + 1 if self._task else self._done
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
        self.infoChanged.emit()

    @Slot(int, int, str)
    def _on_frames(self, done, total, phase):
        self._frames = (done, total, phase)
        self.framesChanged.emit()

    # ---- GpuJobs (studio/gpu_jobs.py) ---------------------------------------------------------

    def holds_gpu(self) -> bool:
        return False                    # the model goes at the end of every batch

    def release_gpu(self):
        return None

    def _claim(self) -> bool:
        jobs = getattr(self, "jobs", None)
        return jobs is None or jobs.claim(JOB_ID, self)

    # ---- commands -----------------------------------------------------------------------------

    @Slot()
    def startSelected(self) -> None:
        picked = self.queue.checked_paths()
        if not picked:
            self.toast.emit("warning", "Process Selected", "Tick the clips to process in Upcoming Jobs first.")
            return
        self.start(picked)

    @Slot(str)
    def startClip(self, path: str) -> None:
        if path:
            self.start([path])

    @Slot(str)
    def testClip(self, path: str) -> None:
        if path:
            self.start([path], test=True)

    @Slot()
    def start(self, only=None, test: bool = False) -> None:
        """The batch -- or, from Upcoming Jobs, only the clips in `only`; `test`: a Test Render."""
        from studio.clip_queue import move_pending
        from studio.stages.depthcrafter.runner import InputError, discover_sources
        if move_pending(getattr(self, "queue", None), self.toast):
            return
        if self._thread is not None and self._thread.is_alive():
            self.toast.emit("warning", "Busy", "A Video Depth Anything job is running. Please wait.")
            return
        self.save()
        opts = S.snapshot(self._values())
        if not self.weightsInstalled:
            self.toast.emit("error", "Cannot start",
                            f"Video Depth Anything {opts['model_size']} isn't in the models folder. "
                            "Download it on this page, or in Settings > Studio > Models.")
            return
        path = opts["input_dir_or_file_var"]
        try:
            specs, single = discover_sources(path)
        except InputError as e:
            self.toast.emit("error", "Cannot start", str(e))
            return
        if only:
            keys = {os.path.normcase(os.path.abspath(p)) for p in only}
            specs = [s for s in specs if os.path.normcase(os.path.abspath(s["path"])) in keys]
        if not specs:
            self._on_status("Nothing to process.")
            self.toast.emit("warning", "Nothing to process", f"No videos found in\n{path}")
            return
        if test:
            from studio import test_renders
            opts["output_dir"] = test_renders.folder(opts["output_dir"])
        runner = R.VdaRunner(opts, _QtHooks(self), self._stop, move_originals=S.moves_finished(opts, single, test),
                             stop_now=self._stop_now)
        if not self._claim():
            self._on_status("Ready")
            return
        self._test_run = bool(test)
        self._runner = runner
        self._stop.clear()
        self._stop_now.clear()
        self._clip = ("N/A", "N/A", "N/A")
        self._frames = (0, 0, "")
        self._done, self._total = 0, len(specs)
        self._task = "batch"
        self.queue.hold(True)
        self.stateChanged.emit()
        self.infoChanged.emit()
        self.framesChanged.emit()
        self._on_status(f"Starting {len(specs)} clip(s)...")

        def work():
            completed = False
            try:
                wait_for_gpu(self)
                completed = bool(runner.run_batch(specs))
            except Exception:
                logger.exception("Video Depth Anything batch failed")
                self._sigStatus.emit("Error: the batch failed. See console.")
            finally:
                self._sigFinished.emit(completed and not self._stop_now.is_set())

        self._thread = threading.Thread(target=work, name="vda-batch", daemon=True)
        self._thread.start()

    @Slot(bool)
    def _on_finished(self, completed: bool) -> None:
        runner, self._runner = self._runner, None
        self._task = ""
        self._thread = None
        self.queue.hold(False)
        self.queue.rescan()
        outcome = runner.outcome if runner is not None else Outcome()
        if not completed and not outcome.error and not self._stop.is_set():
            outcome.error = "The batch ended with an error."
        stopped = self._stop.is_set()
        v = verdict("Video Depth Anything", outcome, stopped=stopped)
        test, self._test_run = self._test_run, False
        self._status = v.title
        self.stateChanged.emit()
        self.infoChanged.emit()
        if v.handoff and not test and runner is not None:
            self._record_job_handoff(runner.opts)
        moved = [m for m in (runner.made if runner is not None else []) if m.get("set_aside")]
        text = v.text
        if test and runner is not None:
            text += f"\nIn {runner.opts.get('output_dir')}"
        if moved:
            text += ("\nThe DepthCrafter map it replaced is kept in "
                     + os.path.dirname(moved[0]["set_aside"]) if len(moved) == 1 else
                     f"\nThe {len(moved)} DepthCrafter maps it replaced are kept in their DepthCrafter folder.")
        self.toast.emit(v.kind, ("Test render: " if test else "") + v.title, text)
        if runner is not None and not test:
            self._show_in_splatting(runner.made)

    def _show_in_splatting(self, made) -> None:
        """Splatting lists the new maps at once (user, 2026-10-25: "so it instantly shows the new results in
        splatting"). Its preview needs nothing: the claim made it let go of its clip (so the DepthCrafter map
        could move), and GpuJobs loads the clip it showed again after the job -- now with the new map."""
        sp = self.splatting
        if sp is None or not made:
            return
        try:
            sp.preview.queue.rescan()
        except Exception:
            logger.debug("Could not refresh Splatting's list after the run", exc_info=True)

    @Slot()
    def stop(self) -> None:
        """Stop now: the clip running is cut off (its map isn't written; the old one stays)."""
        if self._thread is not None and self._thread.is_alive():
            logger.info("Stop requested: stopping now.")
            self._stop_now.set()
            self._stop.set()
            self._on_status("Stopping...")

    @Slot()
    def stopAfterClip(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            logger.info("Stop requested: stopping after the current clip.")
            self._stop.set()
            self._on_status("Stopping after the current clip...")

    @Slot(str)
    def restoreInputs(self, folder_type: str) -> None:
        from studio.stages.depthcrafter.runner import InputError, restore_input_files
        try:
            _restored, errors, msg = restore_input_files(self._map.value("input_dir_or_file_var"), folder_type)
            self._on_status(msg)
            self.toast.emit("warning" if errors else "success", f"Restore {folder_type.capitalize()}", msg)
        except InputError as e:
            self.toast.emit("error", f"Restore {folder_type.capitalize()}", str(e))
        self.queue.rescan()

    @Slot()
    def openOutputFolder(self) -> None:
        runner = self._runner
        folder = runner.opts.get("output_dir") if runner is not None else None
        err = self._shell.openFolder(folder or self._map.value("output_dir"))
        if err:
            self.toast.emit("warning", "Open Output Folder", err)

    def shutdown(self) -> None:
        self._save_timer.stop()
        self.save()
        if self._thread is not None and self._thread.is_alive():
            logger.info("Stopping Video Depth Anything before exit...")
            self._stop_now.set()
            self._stop.set()
            self._thread.join(timeout=10)

    # ---- Splatting's "Rerun with Video Depth Anything" ------------------------------------------

    @Slot(str, str)
    def loadForRerun(self, source: str, output: str) -> None:
        """That one clip as the input, the folder its depth map lives in as the output (as DepthCrafter's
        rerun). Start runs it; the DepthCrafter map there moves into DepthCrafter/ when the new one is
        written, and Splatting shows the clip afresh."""
        if self._task:
            self.toast.emit("warning", "Video Depth Anything is busy", "Wait for its job to finish, then try again.")
            return
        source = os.path.normpath(source)
        output = os.path.normpath(output) if output else ""
        for key, value in (("input_dir_or_file_var", source), ("output_dir", output)):
            if value:
                self._map.insert(key, value)
                self._last_good[key] = value
        self._maybe_leave_chapter()
        self.save()
        self.toast.emit("info", "Ready to re-run",
                        f"{os.path.basename(source)}\nOutput: {output or '(unchanged)'}\n"
                        "Check the settings, then press Start.")

    # ---- Scene Cut import and job handoff ---------------------------------------------------------

    @Property(str, notify=sceneCutChanged)
    def sceneCutDescription(self):
        return self._scene_cut_desc

    @Slot()
    def refreshSceneCut(self) -> None:
        self._scene_cut_desc = ""
        try:
            from core.ui.job_handoff import describe, get_source_record, in_project
            record = get_source_record(STAGE)
            if record and record.get("source") and in_project(record, self._shell.projectFolder, ("source",)):
                self._scene_cut_desc = describe(record) or record["source"]
        except Exception:
            logger.debug("Could not read the Scene Cut handoff", exc_info=True)
        self.sceneCutChanged.emit()

    @Slot()
    def importFromSceneCut(self) -> None:
        try:
            from core.ui.job_handoff import get_source_record, in_project
            record = get_source_record(STAGE) or {}
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
        """Splatting's Import from DepthCrafter: this run's folders (its Start-time snapshot)."""
        opts = opts or {}
        try:
            from core.ui.job_handoff import record_job
            record_job(STAGE, {"source": opts.get("input_dir_or_file_var") or self._map.value("input_dir_or_file_var"),
                               "depth": opts.get("output_dir") or self._map.value("output_dir")})
        except Exception:
            logger.debug("Could not record job handoff", exc_info=True)

    # ---- chapters (DepthCrafter's paths for a chapter; its own pick) ----------------------------

    chapters = Property("QVariantList", lambda self: self._chapters, notify=chapterChanged)
    chapterNum = Property(int, lambda self: self._chapter_num, notify=chapterChanged)
    hasProject = Property(bool, lambda self: bool(self._shell.projectFolder), notify=chapterChanged)

    @Slot()
    def refreshChapters(self) -> None:
        self._chapters = self._shell.listChapters()
        saved = self._shell.stageChapter(OWNER)
        nums = {c["num"] for c in self._chapters}
        self._chapter_num = saved.get("num", -1) if saved.get("num") in nums else -1
        if self._chapter_num >= 0:
            self._chapter_expected = self._shell.stagePaths(STAGE, self._chapter_num, saved.get("name", ""))
        self.chapterChanged.emit()

    @Slot(int)
    def pickChapter(self, num: int) -> None:
        if num < 0:
            self._chapter_num = -1
            self._chapter_expected = {}
            self._shell.setStageChapter(OWNER, {})
            self.chapterChanged.emit()
            return
        name = next((c["name"] for c in self._chapters if c["num"] == num), "")
        paths = self._shell.stagePaths(STAGE, num, name)
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
        self._shell.setStageChapter(OWNER, {"num": num, "name": name})
        self._save_timer.start()
        self.chapterChanged.emit()
        if not getattr(self, "_restoring", False):
            self.toast.emit("info", "Chapter selected", f"{name}\nInput and output now point at this chapter.")

    def _maybe_leave_chapter(self) -> None:
        if self._chapter_num < 0:
            return
        exp = self._chapter_expected
        if not (chapter_paths.same_place(exp.get("input"), self._map.value("input_dir_or_file_var"))
                and chapter_paths.same_place(exp.get("output"), self._map.value("output_dir"))):
            self._chapter_num = -1
            self._shell.setStageChapter(OWNER, {})
            self.chapterChanged.emit()
