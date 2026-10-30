"""controller.py -- Inpainting V2 (Wan VACE) as QML sees it.

The work is v1's own: ``WanInpaintRunner`` in the generated engine.py carries
v1's validation, batch loop, queue drain, finish report and GPU release
verbatim. This file is the glue v1's Tk loop and shell were:

* settings in a QQmlPropertyMap under v1's config keys (settings.py),
  handed to the runner as the dict its ``*_var`` stand-ins read;
* engine.py imports torch / diffusers / transformers (~9 s), so the first
  Start loads it on a thread, then carries on on the UI thread -- v1's
  ``start_processing`` validates there and shows its dialogs;
* the runner's queue wakes ``_poll_queue`` on the UI thread (v1: a 100 ms
  Tk timer), its messagebox calls become prompts in the page;
* v1's shell ``_sibling_guard``: V1 and V2 never run at once, and V2's
  14B engine -- kept loaded between batches, as v1 does -- is released
  before V1 starts (InpaintingController.start calls ``release_for_sibling``).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict

from PySide6.QtCore import Property, QObject, Qt, QTimer, Signal, Slot
from PySide6.QtQml import QQmlPropertyMap

from ... import sage1, settings_store
from ...legacy import REPO
from . import settings as S

logger = logging.getLogger("inpainting.v2.wan")

#: V2's tooltips (module constants in the generated engine.py beside this file, the same text as
#: v1's), read without importing it -- that would load torch. Not from inpainting_gui_v2.py: the
#: Prototype doesn't ship (release, 2026-10-17).
_TIP_NAMES = ("PARAM_TIP_CHUNK", "PARAM_TIP_OVERLAP", "RESUME_TIP", "OFFLOAD_TIP", "SAGE_TIP", "FREE_TE_TIP")


def _v1_tips() -> Dict[str, str]:
    import ast
    tips: Dict[str, str] = {}
    try:
        tree = ast.parse((Path(__file__).with_name("engine.py")).read_text(encoding="utf-8"))
        for node in tree.body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name) and node.targets[0].id in _TIP_NAMES):
                tips[node.targets[0].id] = ast.literal_eval(node.value)
    except Exception:
        logger.debug("Could not read V2's tooltips from engine.py", exc_info=True)
    return tips


#: V2's weights (engine.PRE_TRAINED_PATH / TRANSFORMER_PATH), named here so
#: the page can tell without importing the engine (torch).
WEIGHT_DIRS = ("Wan2.1-VACE-14B-diffusers", "StereoCrafter2")


def weights_installed(root=None) -> bool:
    """Every file V2 loads is there (weights.py's list; user, 2026-10-18 -- it was two folders not
    being empty, which a stopped download passed)."""
    from . import weights as W
    return W.installed(root=Path(root) / "weights" if root is not None else None)


class _RunnerHooks:
    """What the generated runner calls. wake / message may come from the
    worker thread; everything else runs on the UI thread."""

    def __init__(self, ctl: "InpaintingV2Controller"):
        self.ctl = ctl

    def var(self, key, value):
        self.ctl._on_runner_var(key, value)

    def message(self, kind, title, text):
        self.ctl._sigMessage.emit(str(kind), str(title), str(text))

    def wake(self):
        self.ctl._sigWake.emit()

    def running(self, on):
        self.ctl._set_busy(bool(on))

    def claim(self):
        return self.ctl._claim()

    def release(self):
        pass

    def wait_gpu(self):
        """The batch thread, before the models load (audit #14)."""
        from studio.gpu_jobs import wait_for_gpu
        wait_for_gpu(self.ctl)

    def toast(self, kind, title, text):
        """How a batch ended (outcome.verdict); _processing_done runs on the UI thread."""
        self.ctl.toast.emit(str(kind), str(title), str(text))


class InpaintingV2Controller(QObject):
    stateChanged = Signal()
    infoChanged = Signal()
    toast = Signal(str, str, str)
    #: v1's modal dialogs (showerror / showinfo / showwarning): the page asks.
    message = Signal(str, str, str)
    fieldRejected = Signal(str)

    _sigMessage = Signal(str, str, str)
    _sigWake = Signal()
    _sigLoaded = Signal(object)
    #: Download V2 Weights (user, 2026-10-18): its progress and its end, from its thread
    downloadChanged = Signal()
    _sigDlProgress = Signal(float, str)
    _sigDlDone = Signal(str, str)

    def __init__(self, shell, sibling=None, parent=None):
        super().__init__(parent)
        self._shell = shell
        self._sibling = sibling            # the Inpainting V1 controller
        self._tips = _v1_tips()
        stored = settings_store.load("inpainting_v2", REPO / "config_inpaint_v2.json", S.seed_from_v1)
        self._map = QQmlPropertyMap(self)
        for key, value in S.normalise(stored).items():
            self._map.insert(key, value)
        self._last_good: Dict[str, Any] = self._values()
        self._map.valueChanged.connect(self._on_value_changed)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self.save)

        self._runner = None               # engine.WanInpaintRunner, after the first Start
        self._stop_after = threading.Event()    # Stop After Clip
        self._runner_values: Dict[str, Any] = {}
        self._module = None
        self._loading = False
        self._load_error = ""
        self._busy = False
        self._status = "Ready"
        self._progress = 0.0
        # Upcoming Jobs (user, 2026-10-11: as V1 has): the videos a Start would process
        from studio.clip_queue import ClipQueue
        self.queue = ClipQueue("inpainting_v2", self._values, self)
        QTimer.singleShot(0, self.queue.rescan)
        self._only = None                  # the clips the next run is limited to (None: all)
        self.job_stats = None              # studio.app hands it over: the page's live numbers
        self._chunks = None                # job_stats.InpaintingV2Chunks, while a run goes

        q = Qt.ConnectionType.QueuedConnection
        self._sigMessage.connect(self.message, q)
        self._sigWake.connect(self._drain, q)
        self._sigLoaded.connect(self._on_loaded, q)
        self._dl = {"what": "", "progress": 0.0, "text": ""}   # what: "" | "weights" | "fp8"
        self._dl_stop = threading.Event()
        self._dl_thread = None
        self._dl_procs: list = []                 # the download's child process, for Stop and shutdown
        self._sigDlProgress.connect(self._on_dl_progress, q)
        self._sigDlDone.connect(self._on_dl_done, q)
        # the models folder changed, or Settings > Studio > Models downloaded V2's weights (2026-10-20)
        from core import weights_paths
        weights_paths.on_change(self._models_moved)

    # ---- settings ------------------------------------------------------------------

    settings = Property(QObject, lambda self: self._map, constant=True)
    offloadModes = Property("QVariantList", lambda self: list(S.OFFLOAD_MODES), constant=True)
    tips = Property("QVariantMap", lambda self: self._tips, constant=True)
    # ---- Download V2 Weights (user, 2026-10-18: as SAM 3 and ROSE have) ----------------
    # Only the files V2 loads (weights.py), each with its size; the page asks first, with the
    # numbers. Read again when a download ends.

    weightsInstalled = Property(bool, lambda self: weights_installed(), notify=downloadChanged)

    def _parts(self, what: str):
        from . import weights as W
        return [W.FP8] if what == "fp8" else list(W.REQUIRED)

    def _need(self, what: str) -> Dict[str, Any]:
        from . import weights as W
        parts = self._parts(what)
        todo = W.missing(parts)
        need = sum(n for _p, _f, n in todo)
        try:
            free = W.free_space()
        except OSError:
            free = -1
        return {"installed": not todo, "bytes": need, "size": W.gb(need),
                "total": W.gb(sum(p.size for p in parts)),
                "parts": [{"name": p.name, "size": W.gb(p.size),
                           "left": W.gb(sum(n for q, _f, n in todo if q is p))} for p in parts],
                "free": W.gb(free) if free >= 0 else "", "fits": free < 0 or free > need + 2e9,
                "folder": str(W.C.weights_paths.root())}

    @Slot(result="QVariantMap")
    def weightsNeed(self) -> Dict[str, Any]:
        """What Download V2 Weights would fetch: the sizes left per part, the free space."""
        return self._need("weights")

    @Slot(result="QVariantMap")
    def fp8Need(self) -> Dict[str, Any]:
        """The same for StereoCrafter2 FP8 (the FP8 resident mode's transformer)."""
        return self._need("fp8")

    def _fp8_installed(self) -> bool:
        from . import weights as W
        return W.installed([W.FP8])

    fp8Installed = Property(bool, _fp8_installed, notify=downloadChanged)
    download = Property("QVariantMap", lambda self: dict(self._dl), notify=downloadChanged)

    def _models_moved(self) -> None:
        self.downloadChanged.emit()

    @Slot()
    def downloadWeights(self) -> None:
        self._start_download("weights")

    @Slot()
    def downloadFp8(self) -> None:
        self._start_download("fp8")

    def _start_download(self, what: str) -> None:
        if self._dl["what"] or self._busy:
            return
        need = self._need(what)
        if need["installed"]:
            self.downloadChanged.emit()
            return
        if not need["fits"]:
            self.message.emit("error", "Not enough space",
                              f"The download needs {need['size']}; the drive with {need['folder']} has "
                              f"{need['free']} free. Make room there and try again.")
            return
        from . import weights as W
        parts = self._parts(what)
        self._dl_stop.clear()
        self._dl = {"what": what, "progress": 0.0, "text": "Starting the download..."}
        self.downloadChanged.emit()
        logger.info("Download V2 Weights: %s (%s)", ", ".join(p.name for p in parts), need["size"])

        def work():
            try:
                W.download(parts, lambda f, t: self._sigDlProgress.emit(f, t), self._dl_stop,
                           procs=self._dl_procs)
                self._sigDlDone.emit("ok", "")
            except W.Stopped:
                self._sigDlDone.emit("stopped", "")
            except Exception as e:
                logger.exception("Download V2 Weights failed")
                self._sigDlDone.emit("error", str(e))

        self._dl_thread = threading.Thread(target=work, name="inpainting-v2-download", daemon=True)
        self._dl_thread.start()

    @Slot()
    def cancelDownload(self) -> None:
        if self._dl["what"]:
            self._dl_stop.set()
            self._dl["text"] = "Stopping..."
            self.downloadChanged.emit()

    @Slot(float, str)
    def _on_dl_progress(self, fraction: float, text: str) -> None:
        if not self._dl["what"] or self._dl_stop.is_set():
            return
        self._dl["progress"], self._dl["text"] = fraction, text
        self.downloadChanged.emit()

    @Slot(str, str)
    def _on_dl_done(self, how: str, text: str) -> None:
        what = self._dl["what"]
        self._dl_thread = None
        self._dl = {"what": "", "progress": 0.0, "text": ""}
        title = "StereoCrafter2 FP8" if what == "fp8" else "V2 weights"
        if how == "ok":
            logger.info("Download V2 Weights: %s downloaded.", title)
            self.toast.emit("success", f"{title} downloaded",
                            "FP8 resident mode can run now." if what == "fp8" else "Inpainting V2 is ready to run.")
        elif how == "stopped":
            logger.info("Download V2 Weights stopped; the next download carries on from the part-files.")
            self.toast.emit("info", "Download stopped", "Download again later: it carries on where it stopped.")
        else:
            self.message.emit("error", f"Download of the {title} failed",
                              text + "\n\nDownload again to carry on: what came down is kept.")
        self.downloadChanged.emit()

    def _values(self) -> Dict[str, Any]:
        return {k: self._map.value(k) for k in self._map.keys()}

    def _on_value_changed(self, key: str, value: Any) -> None:
        # a number box refuses text that isn't one when it's left, as V1's do
        # (bug sweep A7: fieldRejected was declared and never sent; Start still checks)
        if key in S.NUMERIC:
            try:
                float(str(value).strip())
            except ValueError:
                self._map.insert(key, self._last_good.get(key, S.DEFAULTS.get(key)))
                self.fieldRejected.emit(key)
                return
        coerced = S.coerce(key, value)
        if coerced != value or type(coerced) is not type(value):
            self._map.insert(key, coerced)
        self._last_good[key] = coerced
        self._save_timer.start()

    def _set(self, key: str, value: Any) -> None:
        value = S.coerce(key, value)
        self._map.insert(key, value)
        self._last_good[key] = value

    @Slot()
    def save(self) -> None:
        settings_store.save("inpainting_v2", S.normalise(self._values()))
        self.queue.rescan()        # a typed or picked input: its clips (held during a job)

    @Slot(str)
    def loadSettingsFrom(self, path: str) -> None:
        """v1 load_settings: every key with a matching variable, v1's types."""
        if self._busy:                      # the run keeps what it started with (bug sweep A7)
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            for key, value in cfg.items():
                if key in S.DEFAULTS:
                    self._set(key, value)
            self.save()
            self._on_status(f"Loaded settings from {os.path.basename(path)}")
        except Exception as e:
            self.message.emit("error", "Load error", f"Failed to load settings:\n{e}")

    @Slot(str)
    def saveSettingsTo(self, path: str) -> None:
        """v1 save_settings: its _get_current_config, window state passed through."""
        try:
            v = S.normalise(self._values())
            # v1's key order: theme, the settings (DEFAULTS is in v1's order), geometry.
            data = {"dark_mode_enabled": v.get("dark_mode_enabled", False),
                    "frosted_glass": v.get("frosted_glass", True)}
            data.update({k: v[k] for k in S.DEFAULTS})
            data["window_geometry"] = v.get("window_geometry")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
            self._on_status(f"Saved settings to {os.path.basename(path)}")
        except Exception as e:
            self.message.emit("error", "Save error", f"Failed to save settings:\n{e}")

    # ---- run state -----------------------------------------------------------------

    busy = Property(bool, lambda self: self._busy, notify=stateChanged)

    # ---- Stop After Clip: finish the clip being inpainted, then end the batch ----------

    stopAfterClipChanged = Signal()
    stopAfterClip = Property(bool, lambda self: self._stop_after.is_set(), notify=stopAfterClipChanged)
    #: the running batch has more than one clip (the button's only use)
    multiClip = Property(bool, lambda self: self._busy and self._runner is not None and self._runner._batch_total > 1, notify=stateChanged)

    @Slot()
    def toggleStopAfterClip(self) -> None:
        if not self._busy:
            return
        if self._stop_after.is_set():
            self._stop_after.clear()
            logger.info("Stop After Clip cancelled: the batch carries on.")
        else:
            self._stop_after.set()
            logger.info("Stop After Clip: the batch ends once this clip has finished.")
        self.stopAfterClipChanged.emit()
    loading = Property(bool, lambda self: self._loading, notify=stateChanged)
    status = Property(str, lambda self: self._status, notify=infoChanged)
    jobClips = Property(QObject, lambda self: self.queue, constant=True)

    def _clip_name(self) -> str:
        """The clip being inpainted, from the status ("[2/5] clip.mp4  denoise ..."), "" between."""
        m = re.match(r"^\[\d+/\d+\] (.+?)(?:  |$)", self._status or "") if self._busy else None
        return m.group(1) if m else ""
    clipName = Property(str, _clip_name, notify=infoChanged)
    progress = Property(float, lambda self: self._progress, notify=infoChanged)   # 0..100, as v1

    def _on_status(self, text: str) -> None:
        self._status = str(text)
        self.infoChanged.emit()

    def _on_runner_var(self, key: str, value: Any) -> None:
        if key == "status":
            self._on_status(value)
        elif key == "progress":
            self._progress = float(value or 0.0)
            self.infoChanged.emit()

    def _set_busy(self, on: bool) -> None:
        if on != self._busy:
            self._busy = on
            if not on:      # a run's Stop After Clip ends with it
                self._stop_after.clear()
                self.stopAfterClipChanged.emit()
            self.queue.hold(on)             # the list stays as it is while the job runs
            self.stateChanged.emit()
            if not on:
                self._stop_stats()
                self._only = None
                self.save()

    def is_busy(self) -> bool:
        return self._loading or bool(self._runner is not None and self._runner.is_busy())

    def holds_gpu(self) -> bool:
        return bool(self._runner is not None and self._runner.holds_gpu())

    # ---- Start ---------------------------------------------------------------------

    @Slot()
    def start(self) -> None:
        """Start: every video of the input."""
        if self._loading or self.is_busy():
            return
        self._only = None
        self._test = False
        self._begin()

    @Slot(str)
    def startClip(self, path: str) -> None:
        """Upcoming Jobs' "Inpaint This Clip Now" (user, 2026-10-11)."""
        if path and not (self._loading or self.is_busy()):
            self._only = {os.path.normcase(os.path.abspath(path))}
            self._test = False
            self._begin()

    @Slot(str)
    def testClip(self, path: str) -> None:
        """Upcoming Jobs' "Test Render" (user, 2026-10-15; studio.test_renders): the clip into
        <Output Folder>/Test Renders, nothing moved to finished/."""
        if path and not (self._loading or self.is_busy()):
            self._only = {os.path.normcase(os.path.abspath(path))}
            self._test = True
            self._begin()

    @Slot()
    def startSelected(self) -> None:
        """Process Selected: the clips ticked in Upcoming Jobs only."""
        if self._loading or self.is_busy():
            return
        picked = self.queue.checked_paths()
        if not picked:
            self.message.emit("warning", "Process Selected", "Tick the clips to process in Upcoming Jobs first.")
            return
        logger.info("Process Selected: %d clip(s).", len(picked))
        self._only = {os.path.normcase(os.path.abspath(p)) for p in picked}
        self._test = False
        self._begin()

    def _begin(self) -> None:
        if self._dl["what"] or not weights_installed():
            # what Start would only fail on: the weights (user, 2026-10-18: the button is on the page)
            self.message.emit("warning", "Inpainting V2", "V2's weights aren't all on this PC yet: "
                              + ("wait for the download to finish." if self._dl["what"]
                                 else "Download V2 Weights at the top of the page first."))
            return
        self.save()
        if self._module is None:
            # First Start: the Wan libraries take seconds to import.
            self._loading = True
            self.stateChanged.emit()
            self._on_status("Loading the Wan VACE libraries...")

            def load():
                try:
                    from . import engine
                    self._sigLoaded.emit(engine)
                except Exception as e:
                    logger.exception("Inpainting V2 could not load")
                    self._sigLoaded.emit(e)

            threading.Thread(target=load, name="inpainting-v2-import", daemon=True).start()
            return
        self._start_now()

    @Slot(object)
    def _on_loaded(self, result) -> None:
        self._loading = False
        self.stateChanged.emit()
        if isinstance(result, Exception):
            # v1's shell tab: V1 is unaffected, and this says why V2 is not there.
            self._load_error = f"{type(result).__name__}: {result}"
            self._on_status("Inpainting V2 could not be loaded.")
            self.message.emit(
                "error", "Inpainting V2 (Wan VACE)",
                "This engine could not be loaded. V1 is unaffected and remains usable on the other sub-tab.\n\n"
                "V2 needs the Wan VACE classes from diffusers (WanVACETransformer3DModel, AutoencoderKLWan) "
                "plus transformers, decord and ftfy. A diffusers version predating Wan VACE is the usual "
                "cause.\n\n" + self._load_error)
            return
        self._module = result
        # v1 _configure_logging: pinned at INFO, so model-load and per-chunk
        # lines reach the console whatever the root level.
        result.logger.setLevel(logging.INFO)
        self._start_now()

    def _ensure_runner(self):
        if self._runner is None:
            self._runner_values = {}
            self._runner = self._module.WanInpaintRunner(self._runner_values, _RunnerHooks(self))
            self._runner.stop_after_clip = self._stop_after
        return self._runner

    def _start_now(self) -> None:
        runner = self._ensure_runner()
        self._runner_values.update(S.normalise(self._values()))
        if sage1.installed():   # Triton 3.2 (SageAttention 1, RTX 20) breaks Torch Compile: off, whatever was saved
            self._runner_values["use_compile"] = False
        if getattr(self, "_test", False):
            from studio import test_renders
            self._runner_values["output_folder"] = test_renders.folder(self._runner_values["output_folder"])
            self._runner_values["move_to_finished"] = False
            logger.info("Test render into %s", self._runner_values["output_folder"])
        self._test = False                 # the next Start runs as it is
        runner.only_clips = self._only     # Upcoming Jobs' picks, or None: every clip
        # Inpainting's Encoding Settings (V1's page, Settings > Inpainting) for V2's files too
        # (user, 2026-10-11): codec, bit depth (10-bit = HEVC Main 10), CRF, Enable DNxHR
        self._runner_values["_encoding"] = self._encoding_settings()
        self._start_stats()
        runner.start_processing()          # v1: validate, claim, start the worker
        if not self._busy:                 # refused (validation, the claim): no run to report
            self._stop_stats()
            return
        stats = self.job_stats
        if stats is not None:              # the batch's ETA beside the clip's, as V1's
            try:
                stats.batch_begin("inpainting", self.queue.frames_of(
                    list(self._only) if self._only else self.queue.clipPaths, -1))
            except Exception:
                logger.debug("No batch ETA for this run", exc_info=True)

    def _start_stats(self) -> None:
        """V1's numbers on V2's page (user, 2026-10-24): the run's chunks and steps into JobStats."""
        self._stop_stats()
        if self.job_stats is not None and self._module is not None:
            try:
                from studio.job_stats import InpaintingV2Chunks
                self._chunks = InpaintingV2Chunks(self._module, self.job_stats)
            except Exception:
                logger.debug("Chunk timeline unavailable for this run", exc_info=True)

    def _stop_stats(self) -> None:
        if self._chunks is not None:
            self._chunks.restore()
            self._chunks = None

    def _encoding_settings(self) -> Dict[str, Any]:
        sib = self._sibling
        if sib is None or not hasattr(sib, "_values"):
            return {}
        from studio.stages.inpainting import settings as V1
        v = V1.normalise(sib._values())
        return {k: v[k] for k in tuple(V1.ENCODING_KEYS) + ("output_crf",) if k in v}

    def _claim(self) -> bool:
        """v1's shell _sibling_guard, before the claim: refuse while V1 renders."""
        sib = self._sibling
        if sib is not None and getattr(sib, "busy", False):
            self.message.emit("warning", "Another engine is running",
                              "Inpainting V1 is currently processing. Wait for it to finish before "
                              "starting the other engine.")
            return False
        jobs = getattr(self, "jobs", None)
        return jobs is None or jobs.claim("inpainting", self)

    @Slot()
    def _drain(self) -> None:
        if self._runner is not None:
            self._runner._poll_queue()

    @Slot()
    def stop(self) -> None:
        if self._runner is not None and self._runner.is_busy():
            self._runner.stop_processing()

    # ---- File actions (v1's shell-mode buttons) --------------------------------------

    @Slot()
    def restoreFinished(self) -> None:
        """v1 restore_finished_files, after the page asked v1's question."""
        if self._busy:                      # not while the run moves clips there (bug sweep A7)
            return
        from core.common.file_organizer import describe_restore, restore_finished_files
        input_path = str(self._map.value("input_folder")).strip()
        input_folder = input_path if os.path.isdir(input_path) else os.path.dirname(input_path)
        if not input_folder or not os.path.isdir(input_folder):
            self.message.emit("error", "Restore Finished", "Input folder does not exist.")
            return
        restored, errors, failed = restore_finished_files([(input_folder, "finished")], logger=logger)
        self.message.emit("warning" if errors else "info", "Restore Finished", describe_restore(restored, failed))

    # ---- GPU ------------------------------------------------------------------------

    @Slot()
    def releaseGpu(self) -> None:
        """v1 release_gpu: drop the Wan engine (refused while it runs)."""
        if self._runner is not None:
            self._runner.release_gpu()

    def release_gpu(self) -> None:
        """gpu_jobs: another stage is starting."""
        self.releaseGpu()

    def release_for_sibling(self) -> bool:
        """V1 is about to start (v1 _sibling_guard). False while V2 renders;
        otherwise V2's resident engine is released first."""
        if self.is_busy():
            return False
        if self.holds_gpu():
            logger.info("Releasing GPU held by Inpainting V2")
            self._runner.release_gpu()
        return True

    def shutdown(self) -> None:
        self._save_timer.stop()
        if self._dl["what"]:                 # Studio closing: the download stops (its part-files stay)
            self._dl_stop.set()
            for proc in list(self._dl_procs):
                try:
                    proc.kill()
                except OSError:
                    pass
        self.save()
        if self._runner is not None:
            thread = self._runner.worker_thread
            self._runner.shutdown()          # v1: signal the worker, free the weights
            if thread is not None and thread.is_alive():
                thread.join(timeout=10)
