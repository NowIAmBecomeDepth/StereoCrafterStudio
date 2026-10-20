"""controller.py -- Scene Cut as QML sees it.

The run is v1's own: ``SceneCutEngine`` in the generated engine.py carries
``_start_detection`` (which snapshots every setting for the worker, as v1
does) and everything the detection thread calls. This file is the glue v1's
Tk loop was:

* settings in a QQmlPropertyMap under v1's saved keys; the per-detector
  parameter fields (v1's ``detector_params`` cache) kept beside it and shown
  for the chosen detector;
* the engine imports torch and the detectors, so it loads on a thread the
  first time the page shows (the detector list and its fields come from it);
* v1's ``_process_queues``, hand-ported: a timer drains the progress and
  dialog queues the worker fills, and decides when a run has finished;
* v1's chapter picker, with its extra "Whole Movie (all chapters)" entry.
"""

from __future__ import annotations

import json
import logging
import os
from studio import chapter_paths
import threading

from studio.gpu_jobs import wait_for_gpu
from pathlib import Path
from typing import Any, Dict

from PySide6.QtCore import Property, QObject, Qt, QTimer, Signal, Slot
from PySide6.QtQml import QQmlPropertyMap

from ... import settings_store
from ...legacy import REPO
from . import settings as S

logger = logging.getLogger("scenecut.v2")

HELP_FILE = REPO / "dependency" / "scenecut_help.json"

#: The chapter picker's "Whole Movie (all chapters)" entry (v1 _CHAPTER_WHOLE).
WHOLE_MOVIE = -2


class _EngineHooks:
    """What the generated engine calls; message() may come from the worker."""

    def __init__(self, ctl: "SceneCutController"):
        self.ctl = ctl

    def message(self, kind, title, text):
        self.ctl._sigMessage.emit(str(kind), str(title), str(text))

    def widget(self, name, options):
        self.ctl._sigWidget.emit(str(name), dict(options))


class SceneCutController(QObject):
    stateChanged = Signal()
    progressChanged = Signal()
    paramsChanged = Signal()
    chapterChanged = Signal()
    toast = Signal(str, str, str)
    message = Signal(str, str, str)
    fieldRejected = Signal(str)

    _sigMessage = Signal(str, str, str)
    _sigWidget = Signal(str, object)
    _sigLoaded = Signal(object)

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self._shell = shell
        self._help = self._load_help()
        stored = S.normalise(settings_store.load("scenecut", REPO / "config_scenecut.json", S.seed_from_v1))
        self._params: Dict[str, Dict[str, str]] = stored.pop("detector_params")
        self._map = QQmlPropertyMap(self)
        for key, value in stored.items():
            self._map.insert(key, value)
        self._map.valueChanged.connect(self._on_value_changed)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self.save)

        self._module = None
        self._engine = None
        self._loading = False
        self._load_error = ""
        self._then = None
        self._busy = False
        self._progress = 0.0
        self._bar_open = False                # a console bar left mid-line (_console_bar)
        self._status = "Ready"

        self._poll = QTimer(self)
        self._poll.setInterval(200)          # v1 _process_queues
        self._poll.timeout.connect(self._process_queues)

        self._chapters: list = []
        self._chapter_num = -1
        self._chapter_expected: Dict[str, str] = {}
        self._chapter_applying = False

        q = Qt.ConnectionType.QueuedConnection
        self._sigMessage.connect(self.message, q)
        self._sigWidget.connect(self._on_widget, q)
        self._sigLoaded.connect(self._on_loaded, q)
        shell.projectChanged.connect(self.refreshChapters)
        # the folders / chapter this stage last used in each project (user, 2026-10-02)
        from studio import project_memory
        project_memory.attach(self, "scenecut", shell, ("output_dir",))
        self.refreshChapters()

    @staticmethod
    def _load_help() -> Dict[str, str]:
        try:
            with open(HELP_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {k: (v if isinstance(v, str) else v.get("text", "")) for k, v in data.items()}
        except Exception:
            logger.debug("No help text at %s", HELP_FILE)
            return {}

    # ---- settings ------------------------------------------------------------------

    settings = Property(QObject, lambda self: self._map, constant=True)
    help = Property("QVariantMap", lambda self: self._help, constant=True)
    presets = Property("QVariantList", lambda self: list(S.PRESETS), constant=True)
    spinRanges = Property("QVariantMap", lambda self: {k: list(v) for k, v in S.SPIN_RANGES.items()}, constant=True)

    def _values(self) -> Dict[str, Any]:
        out = {k: self._map.value(k) for k in self._map.keys()}
        out["detector_params"] = self._params
        return out

    def _on_value_changed(self, key: str, value: Any) -> None:
        coerced = S.coerce(key, value) if key in S.DEFAULTS else value
        if coerced != value or type(coerced) is not type(value):
            self._map.insert(key, coerced)
        if key == "output_dir" and not self._chapter_applying:
            self._maybe_leave_chapter()
        if key == "detector_type":
            self.paramsChanged.emit()
        self._save_timer.start()

    def _set(self, key: str, value: Any) -> None:
        self._map.insert(key, S.coerce(key, value) if key in S.DEFAULTS else value)

    @Slot()
    def save(self) -> None:
        settings_store.save("scenecut", S.to_v1(S.normalise(self._values())))

    # ---- the detector and its fields (v1 _build_detector_params) ----------------------

    @Property("QVariantList", notify=stateChanged)
    def detectors(self):
        if self._module is None:
            return [str(self._map.value("detector_type"))]
        return list(self._module.DETECTORS)

    @Property("QVariantList", notify=paramsChanged)
    def detectorParams(self):
        """The chosen detector's fields: [{key, label, value, values, help}]."""
        if self._module is None:
            return []
        det = str(self._map.value("detector_type"))
        spec = self._module.DETECTORS.get(det)
        if spec is None:
            return []
        cache = self._params.setdefault(det, {p.key: p.default for p in spec.params})
        return [{"key": p.key, "label": p.label, "value": str(cache.get(p.key, p.default)),
                 "values": [str(v) for v in p.values], "help": self._help.get(p.tooltip, "")}
                for p in spec.params]

    @Slot(str, str)
    def setDetectorParam(self, key: str, value: str) -> None:
        det = str(self._map.value("detector_type"))
        self._params.setdefault(det, {})[key] = str(value)
        self._save_timer.start()

    @Property(str, notify=stateChanged)
    def loadError(self):
        return self._load_error

    # ---- the engine (loaded on a thread) -----------------------------------------------

    busy = Property(bool, lambda self: self._busy, notify=stateChanged)
    loading = Property(bool, lambda self: self._loading, notify=stateChanged)
    ready = Property(bool, lambda self: self._module is not None, notify=stateChanged)
    progress = Property(float, lambda self: self._progress, notify=progressChanged)
    status = Property(str, lambda self: self._status, notify=progressChanged)

    @Slot()
    def ensureLoaded(self) -> None:
        self._with_engine(None)

    def _with_engine(self, then) -> None:
        if self._module is not None:
            if then is not None:
                then(self._ensure_engine())
            return
        if then is not None:
            self._then = then
        if self._loading:
            return
        self._loading = True
        self.stateChanged.emit()

        def load():
            try:
                from . import engine
                self._sigLoaded.emit(engine)
            except Exception as e:
                logger.exception("Scene Cut engine could not load")
                self._sigLoaded.emit(e)

        threading.Thread(target=load, name="scenecut-import", daemon=True).start()

    @Slot(object)
    def _on_loaded(self, result) -> None:
        self._loading = False
        then, self._then = self._then, None
        if isinstance(result, Exception):
            self._load_error = f"{type(result).__name__}: {result}"
            self.stateChanged.emit()
            if then is not None:
                self.message.emit("error", "Scene Cut", f"The Scene Cut engine could not be loaded:\n\n{self._load_error}")
            return
        self._module = result
        if str(self._map.value("detector_type")) not in result.DETECTORS:
            self._set("detector_type", "AutoShot")      # v1 _load_settings
        self.stateChanged.emit()
        self.paramsChanged.emit()
        if then is not None:
            then(self._ensure_engine())

    def _ensure_engine(self):
        if self._engine is None:
            self._engine = self._module.SceneCutEngine({}, _EngineHooks(self))
            # audit #15: a run claims the GPU like every other stage, and its
            # task waits for the hand-over (#14) -- see gen_scenecut_engine
            self._engine.claim_gpu = self._claim
            self._engine.wait_gpu = lambda: wait_for_gpu(self)
        vals = self._engine._values
        vals.clear()
        vals.update(self._values())
        self._engine._build_detector_params()
        return self._engine

    # ---- Start / Abort (v1 _start_detection, _abort_detection) -----------------------

    @Slot()
    def start(self) -> None:
        if self.is_busy():
            return
        self.save()

        def go(eng):
            eng._start_detection()
            if eng.is_busy():
                self._busy = True
                self.stateChanged.emit()
                self._poll.start()
        self._with_engine(go)

    def _claim(self) -> bool:
        """gpu_jobs: refused while another stage renders (Main shows why)."""
        jobs = getattr(self, "jobs", None)
        return jobs is None or jobs.claim("scenecut", self)

    @Slot()
    def abort(self) -> None:
        """Stop now (audit #13, user 2026-09-29): the scenes being cut are
        cancelled (their encoders ended, partial files discarded). The page
        asked first."""
        if self._engine is not None:
            self._engine.abort_now.set()
            self._engine._abort_detection()

    @Slot()
    def abortAfterScenes(self) -> None:
        """Stop after the scenes being cut: they finish, no more start."""
        if self._engine is not None:
            self._engine._abort_detection()

    def is_busy(self) -> bool:
        return self._loading and self._then is not None or bool(self._engine is not None and self._engine.is_busy())

    def holds_gpu(self) -> bool:
        return bool(self._engine is not None and self._engine.holds_gpu())

    def release_gpu(self) -> None:
        """gpu_jobs: another stage is starting."""
        self.releaseGpu()

    @Slot()
    def releaseGpu(self) -> None:
        if self._engine is not None:
            self._engine.release_gpu()

    @Slot(str, object)
    def _on_widget(self, name: str, options: Dict[str, Any]) -> None:
        """v1's progress bar and label, set by Start (the queue drain sets them too)."""
        if name == "progress_bar" and "value" in options:
            self._progress = float(options["value"] or 0)
            self.progressChanged.emit()
        elif name == "progress_label" and "text" in options:
            self._status = str(options["text"])
            self.progressChanged.emit()

    def _console_bar(self, progress: float, message: str, finished: bool) -> None:
        """The run as a bar in the console's Extra tab, as the other stages draw theirs (user,
        2026-10-18): redrawn only when the worker reports, never on a timer of its own. 100 %
        ends its line (draw_progress_bar); a run that stops short ends it here."""
        import sys
        from core.common.cli_utils import draw_progress_bar
        line = message.splitlines()[0].strip() if message.strip() else ""
        try:
            if progress >= 100:
                if not self._bar_open:             # the split's last update was 100 % too: one bar, not two
                    return
                draw_progress_bar(1000, 1000, bar_length=40, prefix="  Scene Cut:", suffix=line[:60], keep_suffix=True)
                self._bar_open = False
            elif finished:
                if self._bar_open:
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    self._bar_open = False
            else:
                done = int(round(max(0.0, progress) * 10))
                draw_progress_bar(done, 1000, bar_length=40, prefix="  Scene Cut:", suffix=line[:60].ljust(60),
                                  keep_suffix=True)
                self._bar_open = True
        except (UnicodeError, OSError, ValueError):     # a console that can't show it never stops the run
            pass

    def _process_queues(self) -> None:
        """v1 _process_queues, hand-ported: progress, the finish, the dialogs."""
        eng = self._engine
        if eng is None:
            return
        while not eng.progress_queue.empty():
            progress, message = eng.progress_queue.get_nowait()
            self._progress = float(progress)
            self._status = str(message)
            self.progressChanged.emit()
            is_finished = ("Done!" in message or "Aborted" in message or "Error" in message
                           or "No scenes found" in message or message.startswith("Done with errors")
                           or "No chapter markers" in message)
            self._console_bar(float(progress), str(message), is_finished)
            if float(progress) >= 100 or is_finished:
                # how long it took, as every stage's finish message says (user, 2026-10-05).
                # Only the finish itself reads (and forgets) the claim time: the split's last
                # update is 100 % too, and used it up before "Done!" (bug sweep 2026-10-09, A9)
                if "Done" in message:
                    from studio.gpu_jobs import job_seconds
                    from core.common.cli_utils import format_duration
                    secs = job_seconds("scenecut")
                    if secs is not None:
                        message = message.rstrip() + "\nTook " + format_duration(secs) + "."
                # the Windows notification while minimized (user, 2026-10-05):
                # Scene Cut's finish is its own message, not a verdict()
                from studio.outcome import announce
                if "Done!" in message:
                    eng._queue_dialog("info", "Success", message)
                    announce("success", "Scene Cut finished", message)
                elif message.startswith("Done with errors"):
                    # audit #3: failed splits are not a success; none split, an error
                    failed = getattr(eng, "_split_failed", 0)
                    total = len(eng.detected_scenes or [])
                    if failed >= total:
                        eng._queue_dialog("error", "Scene Cut failed", message)
                        announce("error", "Scene Cut failed", message)
                    else:
                        eng._queue_dialog("warning", "Scene Cut finished with errors", message)
                        announce("warning", "Scene Cut finished with errors", message)
                elif ("Error" in message or "Aborted" in message or "No scenes found" in message
                      or "No chapter markers" in message):
                    announce("error" if "Error" in message else "warning", "Scene Cut stopped", message)
        while not eng.dialog_queue.empty():
            kind, title, text = eng.dialog_queue.get_nowait()
            self.message.emit(kind, title, text)
        if not eng.is_busy() and eng.progress_queue.empty() and eng.dialog_queue.empty():
            self._poll.stop()
            if self._busy:
                self._busy = False
                self.stateChanged.emit()

    # ---- the file pickers (v1 browse_video) --------------------------------------------

    @Slot(str)
    def videoChosen(self, path: str) -> None:
        """v1 browse_video: the video, and -- on manual paths only -- an output
        folder beside it named after it."""
        self._set("video_path", path)
        logger.info("Selected video: %s", path)
        if not self._chapter_expected:
            out = str(Path(path).parent / Path(path).stem)
            self._set("output_dir", out.replace("\\", "/"))
            logger.info("Set default output folder: %s", out)
        self._save_timer.start()

    # ---- chapters (v1 _install_chapter_picker("scenecut", ...)) --------------------------

    chapters = Property("QVariantList", lambda self: self._chapters, notify=chapterChanged)
    chapterNum = Property(int, lambda self: self._chapter_num, notify=chapterChanged)
    hasProject = Property(bool, lambda self: bool(self._shell.projectFolder), notify=chapterChanged)

    #: the stage chapter saved once the user sets their own output folder
    MANUAL = {"num": -1, "name": "manual"}

    @Slot()
    def refreshChapters(self) -> None:
        self._chapters = self._shell.listChapters()
        saved = self._shell.stageChapter("scenecut")
        # No chapter dropdown (user, 2026-10-11: Scene Cut takes the whole movie). A
        # project Scene Cut hasn't been set for -- or one left on a single chapter by
        # the old dropdown -- gets Whole Movie (its paths, Split into chapters); one
        # where the user set their own output folder ("num" -1) keeps it. A show has
        # no whole-movie folder (stagePaths gives nothing): manual paths, as before.
        if (getattr(self._shell, "projectFolder", "") and saved.get("num", 0) != -1 and saved.get("num") != WHOLE_MOVIE
                and self._shell.stagePaths("scenecut", -1, "Whole Movie (all chapters)").get("output")):
            self.pickChapter(WHOLE_MOVIE)
            return
        nums = {c["num"] for c in self._chapters} | {WHOLE_MOVIE}
        self._chapter_num = saved.get("num", -1) if saved.get("num") in nums else -1
        if self._chapter_num != -1:
            self._chapter_expected = self._shell.stagePaths("scenecut", max(-1, self._chapter_num),
                                                            saved.get("name", ""))
        else:
            # no chapter in this project: forget the last project's paths, or a picked video
            # kept writing into its chapter folder (bug sweep 2026-10-09, A9)
            self._chapter_expected = {}
        self.chapterChanged.emit()

    @Slot(int)
    def pickChapter(self, num: int) -> None:
        if num == -1:
            self._chapter_num = -1
            self._chapter_expected = {}
            self._shell.setStageChapter("scenecut", dict(self.MANUAL))
            self.chapterChanged.emit()
            return
        whole = num == WHOLE_MOVIE
        name = "Whole Movie (all chapters)" if whole else next(
            (c["name"] for c in self._chapters if c["num"] == num), "")
        paths = self._shell.stagePaths("scenecut", -1 if whole else num, name)
        self._chapter_applying = True
        try:
            if paths.get("output"):
                self._set("output_dir", paths["output"].replace("\\", "/"))
            # v1 _on_chapter_chosen: the whole movie splits by chapter, one chapter does not.
            self._set("split_by_chapter", whole)
        finally:
            self._chapter_applying = False
        self._chapter_num = num
        self._chapter_expected = paths
        self._shell.setStageChapter("scenecut", {"num": num, "name": name})
        self._save_timer.start()
        self.chapterChanged.emit()

    def _maybe_leave_chapter(self) -> None:
        if self._chapter_num == -1:
            return
        exp = self._chapter_expected.get("output")
        if exp and not chapter_paths.same_place(exp, self._map.value("output_dir")):
            self._chapter_num = -1
            self._chapter_expected = {}
            # remembered, so the next refresh doesn't put Whole Movie back over it
            self._shell.setStageChapter("scenecut", dict(self.MANUAL))
            self.chapterChanged.emit()

    # ---- shutdown ----------------------------------------------------------------------

    def shutdown(self) -> None:
        """v1 shutdown: stop the run, save, free the models."""
        self._save_timer.stop()
        self._poll.stop()
        self.save()
        eng = self._engine
        if eng is not None:
            eng._closing = True
            eng.abort_now.set()  # quitting stops now (#13)
            eng.abort_flag.set()
            t = eng.processing_thread
            if t is not None and t.is_alive():
                t.join(timeout=10)
            eng.release_gpu()
