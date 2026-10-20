"""controller.py -- Inpainting (V1, SVD) as QML sees it.

Settings live in a QQmlPropertyMap QML binds to. Start runs v1's own
validation, freezes the settings the way v1's start_processing does, and hands
them to the generated engine (engine.py) on a worker thread. The engine is
imported there, on first Start, so opening the tab never loads torch.

Engine callbacks arrive on the worker thread and are forwarded through queued
signals to the UI thread, like DepthCrafter's.
"""

from __future__ import annotations

import json
import logging
import os
from studio import chapter_paths
from studio.gpu_jobs import wait_for_gpu
from studio.outcome import Outcome, verdict
import threading
import time
from typing import Any, Dict, Optional

from PySide6.QtCore import Property, QObject, Qt, QTimer, Signal, Slot
from PySide6.QtQml import QQmlPropertyMap

from ... import settings_store
from ...legacy import REPO
from . import settings as S

logger = logging.getLogger("inpainting.v2")

HELP_FILE = REPO / "dependency" / "inpaint_help.json"

#: String settings that must parse as a number. v1 only found out at Start;
#: here a box refuses a bad value when you leave it, and Start still checks.
_NUMERIC = {"num_inference_steps", "tile_num", "frames_chunk", "frame_overlap", "original_input_blend_strength",
            "output_crf", "process_length", "inpaint_mask_initial_threshold", "inpaint_mask_post_threshold",
            "inpaint_mask_morph_kernel_size", "inpaint_mask_dilate_kernel_size", "inpaint_mask_blur_kernel_size",
            "mask_initial_threshold", "mask_post_threshold", "mask_morph_kernel_size", "mask_dilate_kernel_size",
            "mask_blur_kernel_size", "nvenc_lookahead", "nvenc_aq_strength", "guidance_scale"}

# v1 help keys whose text is absent from inpaint_help.json but set inline.
_HELP_FALLBACKS = {
    "hires_blend_folder": "Folder containing matching high-resolution splatted files for final blending.",
    "inpaint_direction": "Direction for inpainting: Forward, Reverse, or Both (Forward then Reverse)",
    "processing_clip_count": "Which clip of the batch is being inpainted, and how many there are in total. "
                             "The line under the progress bar counts the frames inpainted in this clip.",
}


def _load_help() -> Dict[str, str]:
    texts = dict(_HELP_FALLBACKS)
    try:
        with open(HELP_FILE, "r", encoding="utf-8") as f:
            for key, value in json.load(f).items():
                if isinstance(value, str):
                    texts[key] = value
                elif isinstance(value, dict) and "text" in value:
                    texts[key] = value["text"]
    except Exception:
        logger.warning("Could not load %s; tooltips will be limited.", HELP_FILE, exc_info=True)
    # Enable DNxHR in Inpainting (user, 2026-10-11): it applies now, V1 and V2, off by default
    texts.setdefault("dnxhr_fullres_split_tooltip", (
        "Writes the inpainted clips as DNxHR in .mov files instead of H.264 / H.265 .mp4, for a DNxHR "
        "pipeline from DepthCrafter on. Much bigger files, no compression loss to speak of. Off by default; "
        "Merging reads either. The 8-bit / 10-bit presets leave it as it is. Applies to V1 and V2."))
    texts.setdefault("encoding_bit_depth", (
        "10-bit (Main10) writes 10-bit HEVC (H.265 Main 10) on NVENC and on the CPU; Auto follows the source; "
        "8-bit writes 8-bit. Applies to V1 and V2. V2's picture is 8-bit (the model's output and the left eye are 8-bit), so a 10-bit file holds no more detail than that -- but it still means no second 8-bit compression on top: 10-bit (Main10) or Enable DNxHR keeps the fill exactly as V2 made it on its way to Merging."))
    texts.setdefault("dnxhr_profile", (
        "The DNxHR profile when Enable DNxHR is on: SQ and HQ are 8-bit 4:2:2, HQX 10-bit 4:2:2, "
        "444 10-bit 4:4:4."))
    return texts


def _encoding_presets() -> Dict[str, dict]:
    """v1's 8-bit / 10-bit presets, including the user's saved overrides."""
    try:
        from core.ui.encoding_settings import PRESET_8BIT, PRESET_10BIT, get_encoding_preset
        # Inpainting's presets leave DNxHR alone (user, 2026-10-11: off unless asked for)
        return {name: {k: v for k, v in (get_encoding_preset(name) or {}).items() if not k.startswith("dnxhr")}
                for name in (PRESET_8BIT, PRESET_10BIT)}
    except Exception:
        logger.debug("Encoding presets unavailable; using the built-in copies", exc_info=True)
        common = {"encoding_encoder": "NVENC", "encoding_quality": "Slowest", "encoding_tune": "Auto",
                  "color_tags": "Auto", "nvenc_lookahead_enabled": True, "nvenc_lookahead": 16,
                  "nvenc_spatial_aq": True, "nvenc_temporal_aq": True, "nvenc_aq_strength": 8}
        return {
            "8-bit": {**common, "codec": "H.264", "bit_depth": "Auto", "pix_fmt": "yuv420p"},
            "10-bit": {**common, "codec": "H.265", "bit_depth": "10-bit (Main10)", "pix_fmt": "p010le"},
        }


def _choices() -> Dict[str, list]:
    out = {k: list(v) for k, v in S.CHOICES.items()}
    try:
        from core.common import encoding_utils as eu
        out.update({"codec": list(eu.CODEC_OPTIONS), "encoding_encoder": list(eu.ENCODER_OPTIONS),
                    "encoding_quality": list(eu.QUALITY_PRESETS), "encoding_tune": list(eu.CPU_TUNE_OPTIONS),
                    "bit_depth": list(eu.BIT_DEPTH_OPTIONS), "pix_fmt": list(eu.PIX_FMT_OPTIONS),
                    "dnxhr_profile": list(eu.DNXHR_PROFILE_OPTIONS)})
    except Exception:
        pass
    # The presets set encoding_tune "Auto", which the CPU-tune list lacks; a
    # value the list cannot show would read as the first entry instead.
    if "Auto" not in out["encoding_tune"]:
        out["encoding_tune"] = ["Auto"] + out["encoding_tune"]
    return out


class _EngineHooks:
    def __init__(self, ctl: "InpaintingController"):
        self.ctl = ctl

    def status(self, text):
        self.ctl._sigStatus.emit(str(text))

    def progress(self, done, total):
        self.ctl._sigProgress.emit(int(done), int(total))

    def clip(self, name, resolution, frames, overlap, bias):
        self.ctl._sigClip.emit([str(name), str(resolution), str(frames), str(overlap), str(bias)])

    def message(self, kind, title, message):
        self.ctl._sigMessage.emit(kind, title, message)

    def finished(self, stopped, outcome=None):
        self.ctl._sigFinished.emit(bool(stopped), outcome)


def restore_dirs(input_folder: str, hires: str):
    """Restore Finished's (folder, "finished") pairs: the input's, and the
    Hi-Res folder's when it is another. A single clip picked as the input
    means its folder (audit 2026-09-28, minor: <clip.mp4>/finished was
    searched), and an empty Hi-Res folder is none (not the working folder)."""
    out = []
    for folder in (input_folder, hires):
        folder = str(folder or "").strip()
        if not folder:
            continue
        # a clip (moved to finished/ by now, so maybe not there): its folder
        if os.path.isfile(folder) or (not os.path.isdir(folder) and os.path.splitext(folder)[1]):
            folder = os.path.dirname(folder)
        if all(os.path.normcase(os.path.normpath(folder)) != os.path.normcase(os.path.normpath(f)) for f, _ in out):
            out.append((folder, "finished"))
    return out


class InpaintingController(QObject):
    stateChanged = Signal()
    infoChanged = Signal()
    importChanged = Signal()
    chapterChanged = Signal()
    encodingChanged = Signal()
    toast = Signal(str, str, str)
    fieldRejected = Signal(str)

    _sigStatus = Signal(str)
    _sigProgress = Signal(int, int)
    _sigClip = Signal("QVariantList")
    _sigMessage = Signal(str, str, str)
    _sigFinished = Signal(bool, object)

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self._shell = shell
        self._help = _load_help()
        self._choices = _choices()
        self._presets = _encoding_presets()

        stored = settings_store.load("inpainting", REPO / "config_inpaint.json", S.seed_from_v1)
        self._map = QQmlPropertyMap(self)
        for key, value in S.normalise(stored).items():
            self._map.insert(key, value)
        self._last_good: Dict[str, Any] = self._values()
        self._map.valueChanged.connect(self._on_value_changed)
        # Upcoming Jobs (user, 2026-10-10): the Lo-Res splats a Start would process
        from studio.clip_queue import ClipQueue
        self.queue = ClipQueue("inpainting", self._values, self)
        QTimer.singleShot(0, self.queue.rescan)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self.save)

        self._busy = False
        self._status = "Ready"
        self._done = 0
        self._total = 0
        self._stop_after = threading.Event()    # Stop After Clip
        self._clip = ["N/A"] * 5
        self._stop = threading.Event()
        self._stop_now = threading.Event()  # Stop (not after the chunk): cut off mid-chunk (#13)
        self._thread: Optional[threading.Thread] = None
        self._run_paths: Dict[str, str] = {}

        self._chapters: list = []
        self._chapter_num = -1
        self._chapter_expected: Dict[str, str] = {}
        self._chapter_applying = False
        self._import_desc = ""

        q = Qt.ConnectionType.QueuedConnection
        self._sigStatus.connect(self._on_status, q)
        self._sigProgress.connect(self._on_progress, q)
        self._sigClip.connect(self._on_clip, q)
        self._sigMessage.connect(self.toast, q)
        self._sigFinished.connect(self._on_finished, q)

        shell.projectChanged.connect(self.refreshChapters)
        shell.projectChanged.connect(self.refreshImport)       # the project's own jobs only
        # the folders / chapter this stage last used in each project (user, 2026-10-02)
        from studio import project_memory
        project_memory.attach(self, "inpainting", shell, [k for _r, k in self._CHAPTER_FIELDS])
        self.refreshChapters()
        self.refreshImport()

    # ---- settings --------------------------------------------------------------

    settings = Property(QObject, lambda self: self._map, constant=True)
    choices = Property("QVariantMap", lambda self: self._choices, constant=True)
    help = Property("QVariantMap", lambda self: self._help, constant=True)

    def _values(self) -> Dict[str, Any]:
        return {k: self._map.value(k) for k in self._map.keys()}

    def _on_value_changed(self, key: str, value: Any) -> None:
        if key in _NUMERIC or key == "single_clip_id":
            text = str(value).strip()
            ok = True
            if key == "single_clip_id":
                ok = text == "" or text.lstrip("-").isdigit()
            else:
                try:
                    float(text)
                except ValueError:
                    ok = False
            if not ok:
                self._map.insert(key, self._last_good.get(key, S.DEFAULTS.get(key)))
                self.fieldRejected.emit(key)
                return
        coerced = S.coerce(key, value)
        if coerced != value or type(coerced) is not type(value):
            self._map.insert(key, coerced)
        self._last_good[key] = coerced
        if key in ("input_folder", "hires_blend_folder", "output_folder") and not self._chapter_applying:
            self._maybe_leave_chapter()
        if key in S.ENCODING_KEYS or key == "output_crf":
            self.encodingChanged.emit()
        self._save_timer.start()

    def _set(self, key: str, value: Any) -> None:
        """Programmatic write (not from QML): no valueChanged, so record it here."""
        value = S.coerce(key, value)
        self._map.insert(key, value)
        self._last_good[key] = value

    jobClips = Property(QObject, lambda self: self.queue, constant=True)

    @Slot()
    def save(self) -> None:
        settings_store.save("inpainting", S.normalise(self._values()))
        self.queue.rescan()        # a typed, picked or imported input: its clips (held during a job)

    @Slot("QVariantMap")
    def loadSent(self, plan) -> None:
        """Upcoming Jobs' "Send to Inpainting" (user, 2026-10-11; studio.send_on): a clip
        the stage before finished, as this stage's input -- one file, so Start runs
        just it -- with the chapter's output folder. The settings are left as they are."""
        if self._busy:
            self.toast.emit("warning", "Inpainting is busy", "Wait for its job to finish, then try again.")
            return
        fields = dict(plan.get("fields") or {})
        for key, value in fields.items():
            self._set(key, os.path.normpath(str(value)))
        self._maybe_leave_chapter()           # a single clip isn't a chapter's paths
        self.save()
        out = fields.get("output_folder")
        # the Hi-Res folder said too: with no chapter it's the one from before (bug sweep 2026-10-14c)
        hires = fields.get("hires_blend_folder") or self._map.value("hires_blend_folder")
        hires_text = (os.path.normpath(str(hires)) + ("" if fields.get("hires_blend_folder") else " (unchanged)")
                      if hires else "(none)")
        note = f"{plan['note']}\n" if plan.get("note") else ""
        self.toast.emit("warning" if note else "info", "Ready in Inpainting",
                        f"{plan.get('clip', '')}\nInput: {os.path.basename(str(plan.get('input', '')))}\n"
                        f"{note}Hi-Res: {hires_text}\n"
                        f"Output: {os.path.normpath(out) if out else '(unchanged)'}\n"
                        "Check the settings, then press Start.")

    @Slot()
    def resetDefaults(self) -> None:
        for key, value in S.RESET_VALUES.items():
            self._set(key, value)
        self.save()
        self.encodingChanged.emit()
        logger.info("GUI settings reset to defaults.")
        self.toast.emit("info", "Settings reset", "All settings have been reset to their default values.")

    @Slot(str)
    def loadSettingsFrom(self, path: str) -> None:
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            seeded = S.seed_from_v1(data)
            # the file's own word, not the page's: the one-time DNxHR reset held for a file saved
            # before it, as at startup (Load Settings turned it back on; bug sweep 2026-10-14c)
            if data.get("inpaint_dnxhr_reviewed") is not True:
                seeded["dnxhr_fullres_split"] = False
            seeded["inpaint_dnxhr_reviewed"] = True
            for key, value in seeded.items():
                self._set(key, value)
            self.save()
            self.encodingChanged.emit()
            note = ""
            if data.get("enable_post_inpainting_blend") in (True, "True", "true", 1):
                # retired: switched off, said (it was silent; sweep 2026-10-14c)
                note = "\nPost-Inpainting Blend is retired and stays off: Merging blends the fill now."
                logger.info("Load Settings: %s had Post-Inpainting Blend on; it's retired (Merging blends).", path)
            self.toast.emit("info" if note else "success", "Settings loaded", os.path.basename(path) + note)
        except Exception as e:
            self.toast.emit("error", "Could not load settings", str(e))

    @Slot(str)
    def saveSettingsTo(self, path: str) -> None:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(S.normalise(self._values()), f, indent=4)
            self.toast.emit("success", "Settings saved", path)
        except Exception as e:
            self.toast.emit("error", "Could not save settings", str(e))

    # ---- encoding presets (v1 Options > Encoding Settings) --------------------

    presetNames = Property("QVariantList", lambda self: list(self._presets), constant=True)

    @Property(str, notify=encodingChanged)
    def encodingPreset(self):
        """The preset the current encoding settings match, or "Custom"."""
        cur = self._values()
        for name, preset in self._presets.items():
            if preset and all(str(cur.get(k)) == str(v) for k, v in preset.items() if k in S.DEFAULTS):
                return name
        return "Custom"

    @Slot(str)
    def applyEncodingPreset(self, name: str) -> None:
        preset = self._presets.get(name) or {}
        for key, value in preset.items():
            if key in S.DEFAULTS:
                self._set(key, value)
        self.save()
        self.encodingChanged.emit()

    # ---- run state -------------------------------------------------------------

    busy = Property(bool, lambda self: self._busy, notify=stateChanged)
    status = Property(str, lambda self: self._status, notify=infoChanged)
    progressDone = Property(int, lambda self: self._done, notify=infoChanged)
    progressTotal = Property(int, lambda self: self._total, notify=infoChanged)

    # ---- Stop After Clip: finish the clip being inpainted, then end the batch -------

    stopAfterClipChanged = Signal()
    stopAfterClip = Property(bool, lambda self: self._stop_after.is_set(), notify=stopAfterClipChanged)
    #: the running batch has more than one clip (the button's only use)
    multiClip = Property(bool, lambda self: self._busy and self._total > 1, notify=infoChanged)

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
    clipName = Property(str, lambda self: self._clip[0], notify=infoChanged)
    clipResolution = Property(str, lambda self: self._clip[1], notify=infoChanged)
    clipFrames = Property(str, lambda self: self._clip[2], notify=infoChanged)
    clipOverlap = Property(str, lambda self: self._clip[3], notify=infoChanged)
    clipBias = Property(str, lambda self: self._clip[4], notify=infoChanged)

    @Property(str, notify=infoChanged)
    def progressCount(self):
        current = self._done + 1 if self._busy else self._done
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

    @Slot("QVariantList")
    def _on_clip(self, values):
        moved_on = list(values)[:1] != self._clip[:1]
        self._clip = list(values)
        if moved_on:
            # the clip list's bar for it: from nothing, not the clip before's 100% while this one
            # loads (user, 2026-10-15)
            stats = getattr(self, "job_stats", None)
            if stats is not None:
                stats.clip_changed("inpainting")
        self.infoChanged.emit()

    def _claim(self) -> bool:
        """v1 _claim_job: the shell's request_job (studio/gpu_jobs.py)."""
        jobs = getattr(self, "jobs", None)
        return jobs is None or jobs.claim("inpainting", self)

    @Slot()
    def start(self) -> None:
        self._start(None)

    @Slot()
    def startSelected(self) -> None:
        """Process Selected (user, 2026-10-10): the clips ticked in Upcoming Jobs only."""
        picked = self.queue.checked_paths()
        if not picked:
            self.toast.emit("warning", "Process Selected", "Tick the clips to process in Upcoming Jobs first.")
            return
        logger.info("Process Selected: %d clip(s).", len(picked))
        self._start(picked)

    @Slot(str)
    def startClip(self, path: str) -> None:
        """Upcoming Jobs' "Inpaint This Clip Now" (user, 2026-10-10)."""
        if path:
            self._start([path])

    @Slot(str)
    def testClip(self, path: str) -> None:
        """Upcoming Jobs' "Test Render" (user, 2026-10-15; studio.test_renders): the clip into
        <Output Folder>/Test Renders, nothing moved to finished/, not handed on."""
        if path:
            self._start([path], test=True)

    def _start(self, only, test: bool = False) -> None:
        from studio.clip_queue import move_pending
        if self._busy or move_pending(getattr(self, "queue", None), self.toast):
            return
        # v1's shell _sibling_guard: V1 and V2 never run at once, and V2's
        # 14B engine (kept loaded between its batches) is released first --
        # once this start has passed its own checks (audit 2026-09-28: a
        # refused start still unloaded V2).
        sib = getattr(self, "sibling", None)
        busy_msg = ("Inpainting V2 is currently processing. Wait for it to finish before "
                    "starting the other engine.")
        if sib is not None and sib.is_busy():
            self.toast.emit("warning", "Another engine is running", busy_msg)
            return
        self.save()
        values = S.normalise(self._values())
        kwargs, error = S.validate(values)
        if kwargs is None:
            self.toast.emit("error", "Cannot start", error)
            return
        input_folder = values["input_folder"]
        output_folder = values["output_folder"]
        # Input may be a folder OR a single clip file (v1's "Select File").
        input_ok = os.path.isdir(input_folder) or os.path.isfile(input_folder)
        # The output folder is made when it isn't there yet, as every other stage makes its own: a new
        # project's chapter has no Inpainted/<chapter> until this stage runs (user, 2026-10-18).
        # Only for a start that has its input: a refused one makes nothing.
        if input_ok and output_folder and not os.path.isdir(output_folder):
            try:
                os.makedirs(output_folder, exist_ok=True)
            except OSError as e:
                logger.warning("Inpainting: could not make the output folder %s: %s", output_folder, e)
        if not input_ok or not os.path.isdir(output_folder):
            self.toast.emit("error", "Cannot start", "Invalid input folder/file or output folder")
            return
        if not self._claim():
            return
        self._test_run = bool(test)
        if test:
            from studio import test_renders
            output_folder = test_renders.folder(output_folder)
            values = dict(values, output_folder=output_folder, move_to_finished=False)
            logger.info("Test render: %s into %s", ", ".join(os.path.basename(p) for p in only), output_folder)
        # V2's resident engine goes only once the GPU is ours (bug sweep 2026-10-09,
        # A7: released before the claim, a start the claim then refused -- another
        # stage running -- had still unloaded it, and V2 reloaded 14B for minutes).
        # The claim can't release it itself: V2 shares this stage's id.
        if sib is not None:
            sib.release_for_sibling()

        self._run_paths = {"input": input_folder, "output": output_folder, "hires": values["hires_blend_folder"]}
        run_settings = S.run_settings(values)
        app_config = dict(values)
        self._stop.clear()
        self._stop_now.clear()
        self._stop_after.clear()
        self._busy = True
        self.queue.hold(True)           # the list stays as it is while the job runs
        self._done, self._total = 0, 0
        self._clip = ["N/A"] * 5
        self._status = "Starting processing..."
        self.stateChanged.emit()
        self.infoChanged.emit()

        hooks = _EngineHooks(self)
        paths = dict(self._run_paths)
        started = time.monotonic()

        def work():
            try:
                wait_for_gpu(self)          # the other stages have really let go (audit #14)
                self._sigStatus.emit("Loading the inpainting engine...")
                from . import engine as engine_module
                from .engine import InpaintingEngine
                engine = InpaintingEngine(run_settings, paths, app_config, hooks, self._stop)
                engine.only_videos = list(only) if only else None     # Process Selected
                engine._job_started = started
                engine.stop_after_clip = self._stop_after
                engine.stop_now = self._stop_now
                engine._configure_logging()
                # The chunk timeline under the progress bar (job_stats).
                stats = getattr(self, "job_stats", None)
                chunks = None
                if stats is not None:
                    try:
                        from ...job_stats import InpaintingChunks
                        chunks = InpaintingChunks(engine_module, stats)
                    except Exception:
                        logger.debug("Chunk timeline unavailable for this run", exc_info=True)
                try:
                    engine.run_batch_process(input_folder, output_folder, **kwargs)
                finally:
                    if chunks is not None:
                        chunks.restore()
            except Exception as e:
                logger.exception("Inpainting could not run")
                self._sigStatus.emit(f"Error: {e.__class__.__name__}. See console.")
                self._sigFinished.emit(False, Outcome(error=str(e) or e.__class__.__name__))

        self._thread = threading.Thread(target=work, name="inpainting-v1", daemon=True)
        self._thread.start()

    @Slot()
    def stop(self) -> None:
        """Stop now (audit #13, user 2026-09-29): the chunk being inpainted
        ends at its next denoising step. The page asked first."""
        if self._busy:
            self._stop_now.set()
            self._stop.set()
            self._on_status("Stopping...")

    @Slot()
    def stopAtChunk(self) -> None:
        """Stop after the chunk being inpainted: it is checkpointed, so the
        next Start on this clip carries on from there."""
        if self._busy:
            self._stop.set()
            self._on_status("Stopping after the current chunk...")

    @Slot(bool, object)
    def _on_finished(self, stopped, outcome=None):
        if not self._busy:
            return
        self._busy = False
        self._thread = None
        self.queue.hold(False)          # the clips it moved to finished leave the list (save rescans)
        self._stop_after.clear()
        self.stopAfterClipChanged.emit()
        self.stateChanged.emit()
        self.infoChanged.emit()
        self.save()
        # audit #3: only a run that produced something is handed on
        v = verdict("Inpainting", outcome or Outcome(), stopped=bool(stopped))
        test, self._test_run = getattr(self, "_test_run", False), False
        if v.handoff and not test:               # a Test Render isn't handed on
            self._record_job_handoff()
        self.toast.emit(v.kind, ("Test render: " if test else "") + v.title,
                        v.text + (f"\nIn {self._run_paths.get('output')}" if test else ""))

    def shutdown(self) -> None:
        self._save_timer.stop()
        self.save()
        if self._thread is not None and self._thread.is_alive():
            logger.info("Stopping inpainting before exit...")
            self._stop_now.set()  # quitting stops now (#13)
            self._stop.set()
            self._thread.join(timeout=10)

    # ---- File menu ---------------------------------------------------------------

    @Slot()
    def restoreFinished(self) -> None:
        """v1 restore_finished_files: <lowres>/finished and, if different, <hires>/finished."""
        dirs = restore_dirs(self._map.value("input_folder"), self._map.value("hires_blend_folder"))
        try:
            from core.common.file_organizer import describe_restore, restore_finished_files
            restored, errors, failed = restore_finished_files(restore_dirs=dirs, logger=logger, wait_before_move=0.5)
        except Exception as e:
            self.toast.emit("error", "Restore failed", str(e))
            return
        if restored or errors:
            logger.info(f"Restore complete: {restored} files moved, {errors} not.")
            self.toast.emit("warning" if errors else "success", "Restore complete",
                            describe_restore(restored, failed))
        else:
            logger.info("Restore complete: No files found to restore.")
            self.toast.emit("info", "Restore complete", "No files found to restore.")

    @Slot()
    def openOutputFolder(self) -> None:
        err = self._shell.openFolder(self._map.value("output_folder"))
        if err:
            self.toast.emit("warning", "Open Output Folder", err)

    # ---- Splatting import and handoff ------------------------------------------

    importDescription = Property(str, lambda self: self._import_desc, notify=importChanged)

    @Slot()
    def refreshImport(self) -> None:
        self._import_desc = ""
        try:
            from core.ui.job_handoff import describe, get_source_record, in_project
            record = get_source_record("inpainting")
            if record and (record.get("lowres") or record.get("hires")) \
                    and in_project(record, self._shell.projectFolder, ("hires", "lowres")):
                self._import_desc = describe(record) or "Splatting job"
        except Exception:
            logger.debug("Could not read the Splatting handoff", exc_info=True)
        self.importChanged.emit()

    @Slot()
    def importFromSplatting(self) -> None:
        try:
            from core.ui.job_handoff import get_source_record, in_project
            record = get_source_record("inpainting") or {}
            if not in_project(record, self._shell.projectFolder, ("hires", "lowres")):
                record = {}
            fwd = lambda p: str(p).replace("\\", "/")  # noqa: E731 - match the Browse dialogs
            if record.get("lowres"):
                self._set("input_folder", fwd(record["lowres"]))
            if record.get("hires"):
                self._set("hires_blend_folder", fwd(record["hires"]))
            if record.get("lowres") or record.get("hires"):
                self._maybe_leave_chapter()
                self._save_timer.start()
                self._on_status("Imported blend folders from Splatting.")
        except Exception:
            logger.exception("Import from Splatting failed")

    def _record_job_handoff(self) -> None:
        try:
            from core.ui.job_handoff import record_job
            record_job("inpainting", {"inpainted_out": self._run_paths.get("output"),
                                      "lowres": self._run_paths.get("input"),
                                      "hires": self._run_paths.get("hires")})
        except Exception:
            logger.debug("Could not record job handoff", exc_info=True)

    # ---- chapters ---------------------------------------------------------------

    chapters = Property("QVariantList", lambda self: self._chapters, notify=chapterChanged)
    chapterNum = Property(int, lambda self: self._chapter_num, notify=chapterChanged)
    hasProject = Property(bool, lambda self: bool(self._shell.projectFolder), notify=chapterChanged)

    _CHAPTER_FIELDS = (("lowres", "input_folder"), ("hires", "hires_blend_folder"), ("output", "output_folder"))

    @Slot()
    def refreshChapters(self) -> None:
        self._chapters = self._shell.listChapters()
        saved = self._shell.stageChapter("inpainting")
        nums = {c["num"] for c in self._chapters}
        self._chapter_num = saved.get("num", -1) if saved.get("num") in nums else -1
        if self._chapter_num >= 0:
            self._chapter_expected = self._shell.stagePaths("inpainting", self._chapter_num, saved.get("name", ""))
        self.chapterChanged.emit()

    @Slot(int)
    def pickChapter(self, num: int) -> None:
        if num < 0:
            self._chapter_num = -1
            self._chapter_expected = {}
            self._shell.setStageChapter("inpainting", {})
            self.chapterChanged.emit()
            return
        name = next((c["name"] for c in self._chapters if c["num"] == num), "")
        paths = self._shell.stagePaths("inpainting", num, name)
        self._chapter_applying = True
        try:
            for role, key in self._CHAPTER_FIELDS:
                if paths.get(role):
                    self._set(key, paths[role].replace("\\", "/"))
        finally:
            self._chapter_applying = False
        self._chapter_num = num
        self._chapter_expected = paths
        self._shell.setStageChapter("inpainting", {"num": num, "name": name})
        self._save_timer.start()
        self.chapterChanged.emit()
        if not getattr(self, "_restoring", False):        # a project switch restores quietly
            self.toast.emit("info", "Chapter selected", f"{name}\nLo-res, hi-res and output now point at this chapter.")

    def _maybe_leave_chapter(self) -> None:
        if self._chapter_num < 0:
            return
        for role, key in self._CHAPTER_FIELDS:
            exp = self._chapter_expected.get(role)
            # a job moving the clips a finished level down is not a hand edit
            if exp and not chapter_paths.same_place(exp, self._map.value(key)):
                self._chapter_num = -1
                self._shell.setStageChapter("inpainting", {})
                self.chapterChanged.emit()
                return
