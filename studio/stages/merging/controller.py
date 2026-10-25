"""controller.py -- Merging as QML sees it.

The batch is v1's own: ``MergingEngine`` in the generated engine.py carries
``run_batch_process`` and everything it calls (mask / blend / shadow, Frame
Break, the L/R split, the grain auto-match, the finished-folder cleanup
thread), plus v1's ``start_processing`` / ``processing_done`` /
``restore_finished_files``. This file is the glue
v1's Tk loop and shell were:

* settings in a QQmlPropertyMap under v1's config keys, handed to the engine
  as the dict its ``*_var`` stand-ins read;
* the engine imports torch, so the first Start loads it on a thread;
* v1's ``process_single_file`` is not ported: nothing in v1 calls it (the
  Inpainted row's Select File does that job -- the batch takes a file);
* v1 marshals UI work with ``after`` / ``_post_ui``; here those post to the
  UI thread (``_sigPost`` -> QTimer), and its display vars (progress, clip
  info, status) arrive through ``hooks.var``;
* v1's messagebox calls become prompts in the page;
* the preview (phase 3) is v1's too, generated -- see preview.py. Start
  gives its readers and GPU memory back before the batch (v1 does the same
  in start_processing) and reloads it afterwards.
"""

from __future__ import annotations

import json
import logging
import os
import re
from studio import chapter_paths
import threading
from typing import Any, Dict

from PySide6.QtCore import Property, QObject, Qt, QTimer, Signal, Slot
from PySide6.QtQml import QQmlPropertyMap

from ... import settings_store
from ...legacy import REPO
from . import settings as S
from .preview import MergingPreview

logger = logging.getLogger("merging.v2")

HELP_FILE = REPO / "dependency" / "merge_help.json"

#: String settings that must parse as numbers (v1 found out at Start, with a
#: dialog); a box refuses a bad value when you leave it.
def _positive_int(text: str) -> int:
    """A chunk of 0 frames crashed the merge; a negative one wrote an empty
    clip (audit 2026-09-28, minor)."""
    n = int(text)
    if n < 1:
        raise ValueError(f"{n} is below 1")
    return n


_NUMERIC = {"batch_chunk_size": _positive_int, "output_crf": int, "grain_seed": float, "mergeall_crf": int}

#: v1 MergingGUI.__init__ help fallbacks (setdefault: a JSON entry wins).
_HELP_FALLBACKS = {
    "preview_brightness": (
        "View-only exposure lift for judging dark scenes on screen. 1.00 = unchanged. Multiplies the "
        "finished PREVIEW image right before it's shown — it does not touch the blend, grain, color "
        "grade, or anything written to the export; the actual merged output is completely unaffected."),
    "current_video_count": (
        "How many clips are loaded in the current list, and which one is showing (e.g. 3 / 12). "
        "Use Load/Refresh List to rebuild it."),
    # Merge All SBS to 1 Video (user, 2026-10-11)
    "merge_all": (
        "Puts the whole movie back together: every chapter's merged scenes, in chapter and scene order, as one "
        "video (the layout, codec and soundtrack are in Settings > Encoding Options > Merge All). It first checks "
        "that every scene Scene Cut made has a merged output; any that don't are listed, to send on to the stage "
        "each still needs, to play, or to merge anyway with black in their place. For a TV show: the episode "
        "Merging is on."),
    "mergeall_layout": (
        "Full SBS: both eyes at full width, side by side. Half SBS: both squeezed into one normal-width frame, "
        "as most 3D TVs read side-by-side. L and R files: a whole-movie file for each eye."),
    "mergeall_codec": "H.265 / H.264 use the Encoder, Quality Preset and CRF below; DNxHR uses its profile.",
    "mergeall_crf": "Quality for H.265 / H.264: lower is better and bigger. 18 is near-transparent.",
    "mergeall_audio_file": (
        "The movie file whose soundtrack goes in, all its audio tracks (e.g. the original Blu-ray rip). It lines "
        "up when every scene is there -- a missing scene merged anyway is black for its own length, so it still "
        "does. Empty: no audio."),
    "mergeall_audio_codec": "Copy keeps the tracks as they are (TrueHD / Atmos too: use MKV). AAC re-encodes at 320 kbps.",
    "mergeall_container": "MKV holds every codec and audio track. DNxHR goes in MOV when MP4 is picked.",
    "mergeall_output_folder": "Where the movie is written. Empty: the project's folder (for a show, the episode's).",
    # Fill Edges: opens Extra > Edge Fill on this stage's folders (user, 2026-10-14)
    "fill_edges": (
        "Opens Extra > Edge Fill on the Output and Mask folders: the merged clips as a grid of stills, the strip "
        "a deep 3D shift leaves at the frame's edge measured on each, and ROSE to refill it."),
}

#: The encoding fields a preset owns (v1 _current_encoding_config): CRF is not one.
ENCODING_KEYS = ("codec", "encoding_encoder", "encoding_quality", "encoding_tune", "bit_depth", "pix_fmt",
                 "color_tags", "nvenc_lookahead_enabled", "nvenc_lookahead", "nvenc_spatial_aq",
                 "nvenc_temporal_aq", "nvenc_aq_strength", "dnxhr_fullres_split", "dnxhr_profile")


def _load_help() -> Dict[str, str]:
    texts: Dict[str, str] = {}
    try:
        with open(HELP_FILE, "r", encoding="utf-8") as f:
            for key, value in json.load(f).items():
                if isinstance(value, str):
                    texts[key] = value
                elif isinstance(value, dict) and "text" in value:
                    texts[key] = value["text"]
    except Exception:
        logger.warning("Could not load %s; tooltips will be limited.", HELP_FILE, exc_info=True)
    for key, text in _HELP_FALLBACKS.items():
        texts.setdefault(key, text)
    texts["output_format"] = (texts.get("output_format", "").rstrip() + "\n\n" if texts.get("output_format") else "") + (
        S.SPLIT_LR_FORMAT + ": merges a Full SBS (Left-Right) video, splits it into separate left (_L) and "
        "right (_R) eye files, then moves the SBS to the Recycle Bin, so only the two eye files are left -- "
        "the same as Full SBS with Split L/R and Delete SBS Videos ticked. If either eye fails to split, the "
        "SBS is kept.")
    return texts


#: v1's Preview Source list (create_widgets set_source_values) and
#: VideoPreviewer's PERCENTAGE_VALUES.
PREVIEW_SOURCES = ["Blended Image", "Original (Left Eye)", "Warped (Right BG)", "Inpainted Right Eye",
                   "Processed Mask", "Anaglyph 3D", "Dubois Anaglyph", "Optimized Anaglyph", "Side-by-Side",
                   "Wigglegram", "Depth Map", "Mesh Warp"]
PREVIEW_SIZES = [f"{p}%" for p in (250, 240, 230, 220, 210, 200, 190, 180, 170, 160, 150, 145, 140, 135, 130,
                                    125, 120, 115, 110, 105, 100, 95, 90, 85, 80, 75, 70, 65, 60, 55, 50, 25)]


def _choices() -> Dict[str, list]:
    out: Dict[str, list] = {"output_format": list(S.OUTPUT_FORMATS), "preview_source": list(PREVIEW_SOURCES),
                            "preview_size": list(PREVIEW_SIZES)}
    try:
        from core.common import encoding_utils as eu
        out.update({"codec": list(eu.CODEC_OPTIONS), "encoding_encoder": list(eu.ENCODER_OPTIONS),
                    "encoding_quality": list(eu.QUALITY_PRESETS), "encoding_tune": list(eu.CPU_TUNE_OPTIONS),
                    "bit_depth": list(eu.BIT_DEPTH_OPTIONS), "pix_fmt": list(eu.PIX_FMT_OPTIONS),
                    "dnxhr_profile": list(eu.DNXHR_PROFILE_OPTIONS)})
    except Exception:
        logger.debug("encoding lists unavailable", exc_info=True)
    from studio import merge_all
    out.update({"mergeall_layout": list(merge_all.LAYOUTS), "mergeall_codec": list(merge_all.CODECS),
                "mergeall_container": list(merge_all.CONTAINERS), "mergeall_audio_codec": list(merge_all.AUDIO_CODECS)})
    if "encoding_tune" in out and "Auto" not in out["encoding_tune"]:
        out["encoding_tune"] = ["Auto"] + out["encoding_tune"]
    return out


class _EngineHooks:
    """What the generated engine calls. var / message / post may come from
    the worker; everything else runs on the UI thread."""

    def __init__(self, ctl: "MergingController"):
        self.ctl = ctl

    def var(self, key, value):
        self.ctl._sigVar.emit(str(key), value)

    def message(self, kind, title, text):
        self.ctl._sigMessage.emit(str(kind), str(title), str(text))

    def wait_gpu(self):
        """The batch thread, before it loads anything (audit #14)."""
        from studio.gpu_jobs import wait_for_gpu
        wait_for_gpu(self.ctl)

    def toast(self, kind, title, text):
        """How a batch ended (outcome.verdict), from processing_done."""
        self.ctl._sigToast.emit(str(kind), str(title), str(text))

    def post(self, delay_ms, fn):
        self.ctl._sigPost.emit(int(delay_ms), fn)

    def running(self, on):
        self.ctl._set_busy(bool(on))

    def claim(self):
        jobs = getattr(self.ctl, "jobs", None)       # v1 _claim_job -> the shell's request_job
        return jobs is None or jobs.claim("merging", self.ctl)

    def release(self):
        pass

    def release_preview(self):
        pass                       # Start already had the preview release (release_for_job)

    def ask_missing_originals(self, missing, original_folder):
        """The batch thread: clips with no original. The page asks; Continue starts again."""
        self.ctl._sigMissing.emit(list(missing), str(original_folder))

    def info_from_preview(self):
        self.ctl._clip_from_preview()


class MergingController(QObject):
    stateChanged = Signal()
    infoChanged = Signal()
    #: the clip on screen's put-back (core.removal.putback) changed, or another clip is on screen
    putBackChanged = Signal()
    importChanged = Signal()
    chapterChanged = Signal()
    encodingChanged = Signal()
    toast = Signal(str, str, str)
    #: v1's modal dialogs: the page asks.
    message = Signal(str, str, str)
    #: clips with no original (title, message): the page asks; Continue -> continueWithoutOriginals
    missingOriginals = Signal(str, str)
    fieldRejected = Signal(str)

    _sigVar = Signal(str, object)
    _sigMessage = Signal(str, str, str)
    _sigToast = Signal(str, str, str)
    _sigPost = Signal(int, object)
    _sigLoaded = Signal(object)
    _sigMissing = Signal(list, str)

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self._shell = shell
        self._help = _load_help()
        self._choices = _choices()
        stored = settings_store.load("merging", REPO / "config_merging.mergecfg", S.seed_from_v1)
        self._map = QQmlPropertyMap(self)
        for key, value in S.at_session_start(S.normalise(stored)).items():
            self._map.insert(key, value)
        self._last_good: Dict[str, Any] = self._values()
        self._map.valueChanged.connect(self._on_value_changed)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self.save)

        self._engine = None
        self._restart = None                  # how the last batch was started (Continue repeats it)
        self._accept_next = frozenset()       # clips to merge without an original, next start only
        self._asked_missing = []
        self._stop_after = threading.Event()    # Stop After Clip
        self._engine_values: Dict[str, Any] = {}
        self._module = None
        self._loading = False
        self._pending_start = None        # what to run once the engine has loaded
        self._busy = False
        # v1's display vars
        self._disp = {"status_label": "Ready.", "progress": 0.0, "progress_maximum": 1,
                      "processing_clip_count": "—", "current_filename": "No video loaded",
                      "current_resolution": "N/A", "current_total_frames": "N/A",
                      "current_flip_status": "N/A", "border_info": "Borders: N/A"}

        self._chapters: list = []
        self._chapter_num = -1
        self._chapter_expected: Dict[str, str] = {}
        self._chapter_applying = False
        self._import_desc = ""
        self._import_v2_desc = ""
        # Merge All SBS to 1 Video (user, 2026-10-11): the last scan, the run
        self._merge_info = None
        self._merging_all = False
        self._merge_build = None
        self._merge_stop = threading.Event()
        self._merge_shown = 0.0

        # The preview (phase 3): v1's own, generated -- see preview.py.
        self._applying_preview = False
        self._preview_info: Dict[str, Any] = {}
        self._releasing = False           # Start waits for the preview to let go
        self.preview = MergingPreview(self._values, self)
        # Upcoming Jobs keeps the job's clips (and their bars) while it runs
        self.stateChanged.connect(lambda: self.preview.queue.hold(self._busy))
        self.preview.settingsWritten.connect(self._take_preview_writes)
        self.preview.infoWritten.connect(self._take_preview_info)
        # v1's messagebox calls from the preview are modal there: the page's prompt.
        self.preview.toast.connect(self.message)
        self.preview.saveRequested.connect(self.save)
        # Put Back's controls follow the clip on screen (user, 2026-10-15)
        self._pb_key = None
        self._pb_state: Dict[str, Any] = {}
        # the page's fields read and write this, as they do the stage's settings
        self._pb_map = QQmlPropertyMap(self)
        self._pb_filling = False
        for k, v in (("enabled", False), ("mode", "Over"), ("depth", "Original Depth"), ("conv", 0.0), ("grow", 0.0),
                     ("blur", 0.0), ("opacity", 100), ("parallax", 0.0), ("disp", 100.0)):
            self._pb_map.insert(k, v)
        self._pb_map.valueChanged.connect(lambda k, v: None if self._pb_filling else self.setPutBack(k, v))
        self.preview.changed.connect(self._putback_follow)

        q = Qt.ConnectionType.QueuedConnection
        self._sigVar.connect(self._on_var, q)
        self._sigMessage.connect(self.message, q)
        self._sigToast.connect(self.toast, q)
        self._sigPost.connect(self._on_post, q)
        self._sigLoaded.connect(self._on_loaded, q)
        self._sigMissing.connect(self._on_missing_originals, q)
        self._sigMergeScanned.connect(self._on_merge_scanned, q)
        self._sigMergeDone.connect(self._on_merge_done, q)

        shell.projectChanged.connect(self.refreshChapters)
        shell.projectChanged.connect(self.refreshImport)       # the project's own jobs only
        # the folders / chapter this stage last used in each project (user, 2026-10-02)
        from studio import project_memory
        project_memory.attach(self, "merging", shell, [k for _r, k in self._CHAPTER_FIELDS])
        self.refreshChapters()
        self.refreshImport()

    # ---- settings --------------------------------------------------------------------

    settings = Property(QObject, lambda self: self._map, constant=True)
    previewer = Property(QObject, lambda self: self.preview, constant=True)
    choices = Property("QVariantMap", lambda self: self._choices, constant=True)
    help = Property("QVariantMap", lambda self: self._help, constant=True)
    maskSliders = Property("QVariantList", lambda self: [
        {"key": k, "label": lbl, "from": lo, "to": hi, "decimals": d, "step": st,
         "defaultValue": S.RESET_VALUES.get(k, S.DEFAULTS[k])}
        for k, lbl, lo, hi, d, st in S.MASK_SLIDERS], constant=True)

    def _values(self) -> Dict[str, Any]:
        return {k: self._map.value(k) for k in self._map.keys()}

    def _on_value_changed(self, key: str, value: Any) -> None:
        if key in _NUMERIC:
            try:
                _NUMERIC[key](str(value).strip())
            except (TypeError, ValueError):
                self._map.insert(key, self._last_good.get(key, S.DEFAULTS.get(key)))
                self.fieldRejected.emit(key)
                return
        coerced = S.coerce(key, value) if key in S.DEFAULTS else value
        if coerced != value or type(coerced) is not type(value):
            self._map.insert(key, coerced)
        self._last_good[key] = coerced
        if key in ("inpainted_folder", "original_folder", "mask_folder", "output_folder") \
                and not self._chapter_applying:
            self._maybe_leave_chapter()
        if key in ENCODING_KEYS:
            self.encodingChanged.emit()
        if key == "debug_logging_enabled":
            self._apply_debug_logging()
        self.preview.note_change(key, coerced)
        self._save_timer.start()

    def _set(self, key: str, value: Any) -> None:
        """Programmatic write (not from QML): no valueChanged, so record it here."""
        value = S.coerce(key, value) if key in S.DEFAULTS else value
        self._map.insert(key, value)
        self._last_good[key] = value
        if not self._applying_preview:
            self.preview.note_change(key, value)

    def _take_preview_writes(self, writes: Dict[str, Any]) -> None:
        """What the preview's v1 code set (the Preview Source kept valid).
        Applied without sending it back -- the engine already has it."""
        self._applying_preview = True
        try:
            for key, value in writes.items():
                if key in S.DEFAULTS:
                    self._set(key, value)
        finally:
            self._applying_preview = False
        self._save_timer.start()

    def _take_preview_info(self, info: Dict[str, Any]) -> None:
        """v1 _update_info_display / the border scan, for the clip on screen.
        While a batch runs the panel follows the batch instead (v1
        set_live_clip_info), so it is only remembered."""
        self._preview_info.update(info)
        if self._busy:
            return
        for key, value in info.items():
            if key in self._disp:
                self._disp[key] = value
        self.infoChanged.emit()

    @Slot()
    def save(self) -> None:
        settings_store.save("merging", S.normalise(self._values()))

    @Slot("QVariantMap")
    def loadSent(self, plan) -> None:
        """Upcoming Jobs' "Send to Merging" (user, 2026-10-11; studio.send_on): a clip
        the stage before finished, as this stage's input -- one file, so Start runs
        just it -- with the chapter's output folder. The settings are left as they are."""
        if self._busy or self.is_busy():
            self.toast.emit("warning", "Merging is busy", "Wait for its job to finish, then try again.")
            return
        fields = dict(plan.get("fields") or {})
        for key, value in fields.items():
            self._set(key, os.path.normpath(str(value)))
        self._maybe_leave_chapter()           # a single clip isn't a chapter's paths
        self.save()
        out = fields.get("output_folder")
        self.toast.emit("info", "Ready in Merging",
                        f"{plan.get('clip', '')}\nInput: {os.path.basename(str(plan.get('input', '')))}\n"
                        f"Output: {os.path.normpath(out) if out else '(unchanged)'}\n"
                        "Check the settings, then press Start.")

    @Slot()
    def resetDefaults(self) -> None:
        """v1 reset_to_defaults: _apply_settings(APP_DEFAULTS), after the page asked."""
        for key, value in S.RESET_VALUES.items():
            self._set(key, value)
        self.save()
        self.encodingChanged.emit()
        logger.info("GUI settings reset to defaults.")

    @Slot()
    def resetMaskParameters(self) -> None:
        """v1 _reset_mask_parameters: the eight Mask Processing sliders."""
        keys = [k for k, *_ in S.MASK_SLIDERS]
        for key in keys:
            self._set(key, S.RESET_VALUES[key])
        self._save_timer.start()
        self._on_var("status_label", f"Mask processing parameters reset to defaults ({len(keys)} values).")

    @Slot(str)
    def loadSettingsFrom(self, path: str) -> None:
        """v1 load_settings_dialog: every key that has a var."""
        try:
            with open(path, "r") as f:
                data = S.from_settings_file(json.load(f))
            for key, value in data.items():
                if key in S.DEFAULTS:
                    self._set(key, value)
            self.save()
            self.encodingChanged.emit()
            logger.info(f"Settings loaded from {path}")
        except Exception as e:
            self.message.emit("error", "Load Error", f"Failed to load settings from {path}:\n{e}")

    @Slot(str)
    def saveSettingsTo(self, path: str) -> None:
        """v1 save_settings_dialog: get_current_settings(), as v1 converts it."""
        try:
            data = S.settings_file_data(S.normalise(self._values()))
        except (ValueError, TypeError) as e:
            self.message.emit("error", "Invalid Settings",
                              f"Please check your parameter values. They must be valid numbers.\n\nError: {e}")
            return
        try:
            with open(path, "w") as f:
                json.dump(data, f, indent=4)
            logger.info(f"Settings saved to {path}")
        except Exception as e:
            self.message.emit("error", "Save Error", f"Failed to save settings to {path}:\n{e}")

    def _apply_debug_logging(self) -> None:
        """v1 _toggle_debug_logging / _configure_logging."""
        level = logging.DEBUG if self._map.value("debug_logging_enabled") else logging.INFO
        if self._module is not None:
            try:
                self._module.set_logger_level(self._module.logger, level)
            except Exception:
                self._module.logger.setLevel(level)

    # ---- encoding presets (v1's 8-bit / 10-bit quick-pick) ---------------------------

    @Property(str, notify=encodingChanged)
    def encodingPreset(self) -> str:
        try:
            from core.ui.encoding_settings import detect_encoding_preset
            return detect_encoding_preset({k: self._map.value(k) for k in ENCODING_KEYS})
        except Exception:
            return "Custom"

    @Property("QVariantList", constant=True)
    def presetNames(self):
        try:
            from core.ui.encoding_settings import PRESET_8BIT, PRESET_10BIT
            return [PRESET_8BIT, PRESET_10BIT]
        except Exception:
            return ["8-bit", "10-bit"]

    @Property(str, constant=True)
    def presetTooltip(self) -> str:
        try:
            from core.ui.encoding_settings import preset_tooltip
            return preset_tooltip()             # Splatting's page shows the same (user, 2026-10-25)
        except Exception:
            return ""

    @Slot(str)
    def applyEncodingPreset(self, name: str) -> None:
        """v1 _apply_encoding_preset: every preset field, CRF left alone."""
        try:
            from core.ui.encoding_settings import get_encoding_preset
            preset = get_encoding_preset(name) or {}
        except Exception:
            preset = {}
        if not preset:
            return
        for key in ENCODING_KEYS:
            if key in preset:
                self._set(key, preset[key])
        self._save_timer.start()
        self.encodingChanged.emit()
        self._on_var("status_label", f"Encoding preset applied: {name}")

    # ---- run state -------------------------------------------------------------------

    busy = Property(bool, lambda self: self._busy, notify=stateChanged)

    # ---- Stop After Clip: finish the clip being merged, then end the batch ----------

    stopAfterClipChanged = Signal()
    stopAfterClip = Property(bool, lambda self: self._stop_after.is_set(), notify=stopAfterClipChanged)
    #: the running batch has more than one clip (the button's only use)
    multiClip = Property(bool, lambda self: self._busy and not self._merging_all
                         and float(self._disp["progress_maximum"] or 1) > 1, notify=infoChanged)

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
    status = Property(str, lambda self: str(self._disp["status_label"]), notify=infoChanged)
    progressValue = Property(float, lambda self: float(self._disp["progress"] or 0), notify=infoChanged)
    progressMaximum = Property(float, lambda self: float(self._disp["progress_maximum"] or 1), notify=infoChanged)
    clipCount = Property(str, lambda self: str(self._disp["processing_clip_count"]), notify=infoChanged)
    clipName = Property(str, lambda self: str(self._disp["current_filename"]), notify=infoChanged)
    clipResolution = Property(str, lambda self: str(self._disp["current_resolution"]), notify=infoChanged)
    clipFrames = Property(str, lambda self: str(self._disp["current_total_frames"]), notify=infoChanged)
    clipFlip = Property(str, lambda self: str(self._disp["current_flip_status"]), notify=infoChanged)
    # v1's label reads "Borders: L=..%, R=..%"; the panel's row is already
    # labelled Borders, as v1's is.
    clipBorders = Property(str, lambda self: str(self._disp["border_info"]).replace("Borders: ", "", 1),
                           notify=infoChanged)

    @Slot(str, object)
    def _on_var(self, key: str, value: Any) -> None:
        if key in self._disp:
            self._disp[key] = value
            self.infoChanged.emit()

    @Slot(int, object)
    def _on_post(self, delay_ms: int, fn) -> None:
        def run():
            try:
                fn()
            except Exception:
                logger.exception("Queued UI update failed")
        if delay_ms > 0:
            QTimer.singleShot(delay_ms, run)
        else:
            run()

    def _set_busy(self, on: bool) -> None:
        if on != self._busy:
            self._busy = on
            if not on:      # a run's Stop After Clip ends with it
                self._stop_after.clear()
                self.stopAfterClipChanged.emit()
            self.stateChanged.emit()
            self.infoChanged.emit()
            if not on:
                self.save()
                self.refreshImport()
                self.preview.restore_after_job()

    def _clip_from_preview(self) -> None:
        """v1 _update_info_display at the end of a batch: the previewer's clip.
        The batch had the preview released, so this is "No video loaded"
        until restore_after_job has reloaded it (its info follows)."""
        loaded = self.preview.loaded
        for key, value in (("current_filename", "No video loaded"), ("current_resolution", "N/A"),
                           ("current_total_frames", "N/A"), ("current_flip_status", "N/A")):
            self._disp[key] = self._preview_info.get(key, value) if loaded else value
        self.infoChanged.emit()

    def is_busy(self) -> bool:
        return (self._loading or self._merging_all
                or bool(self._engine is not None and self._engine.is_busy()))

    # ---- Start / Stop / Single File ------------------------------------------------------

    def _with_engine(self, then) -> None:
        """Run ``then(engine)`` on the UI thread, loading engine.py first if needed."""
        if self._module is not None:
            then(self._ensure_engine())
            return
        self._pending_start = then
        if self._loading:
            return
        self._loading = True
        self.stateChanged.emit()
        self._on_var("status_label", "Loading the merging engine...")

        def load():
            try:
                from . import engine
                self._sigLoaded.emit(engine)
            except Exception as e:
                logger.exception("Merging engine could not load")
                self._sigLoaded.emit(e)

        threading.Thread(target=load, name="merging-import", daemon=True).start()

    @Slot(object)
    def _on_loaded(self, result) -> None:
        self._loading = False
        self.stateChanged.emit()
        then, self._pending_start = self._pending_start, None
        if isinstance(result, Exception):
            self._on_var("status_label", "The merging engine could not be loaded.")
            self.message.emit("error", "Merging", f"The merging engine could not be loaded:\n\n"
                                                  f"{type(result).__name__}: {result}")
            return
        self._module = result
        self._apply_debug_logging()
        if then is not None:
            then(self._ensure_engine())

    def _ensure_engine(self):
        if self._engine is None:
            self._engine = self._module.MergingEngine(self._engine_values, _EngineHooks(self))
            self._engine.stop_after_clip = self._stop_after
        # Every *_var the engine reads sees the settings as they are now.
        # (Split L/R Output is handed over as Full SBS + Split L/R + Delete SBS)
        self._engine_values.update(S.for_engine(S.normalise(self._values())))
        return self._engine

    @Slot()
    def start(self, only=None, test: bool = False) -> None:
        if self.is_busy():
            return
        self.save()
        # v1 start_processing clears the preview's resources first; here that
        # happens on the preview's own thread, and the batch starts after.
        if self._releasing:
            return
        from studio.clip_queue import move_pending
        if move_pending(getattr(getattr(self, "preview", None), "queue", None), self.toast):
            return
        self._releasing = True
        # the list held from now, not from the run's start: while the preview let go, Move to
        # Finished could take clips the batch was about to read (bug sweep 2026-10-14c)
        self.preview.queue.hold(True)
        only_keys = {os.path.normcase(os.path.abspath(p)) for p in only} if only else None
        self._restart = lambda: self.start(only, test)
        accept, self._accept_next = self._accept_next, frozenset()

        def go():
            self._releasing = False

            def run(eng):
                eng.only_clips = only_keys          # Process Selected, or None: every clip
                eng.accept_missing_originals = accept
                if test:
                    # a Test Render: into <Output Folder>/Test Renders, nothing moved to finished/
                    # (Resume's move); the next start takes the page's values again (_ensure_engine)
                    from studio import test_renders
                    self._engine_values["output_folder"] = test_renders.folder(self._engine_values["output_folder"])
                    self._engine_values["resume"] = False
                    logger.info("Test render into %s", self._engine_values["output_folder"])
                eng.start_processing()
                if not eng.is_processing:          # refused (v1's validation, or another stage runs)
                    self.preview.queue.hold(False)
                    self.preview.restore_after_job()
            self._with_engine(run)
        self.preview.release_for_job(go)

    @Slot()
    def startSelected(self) -> None:
        """Process Selected (user, 2026-09-30): the batch, on the clips ticked
        in Upcoming Jobs only."""
        if self.is_busy():
            return
        picked = self.preview.queue.checked_paths()
        if not picked:
            self.toast.emit("warning", "Process Selected", "Tick the clips to process in Upcoming Jobs first.")
            return
        logger.info("Process Selected: %d clip(s).", len(picked))
        self.start(picked)

    @Slot(str)
    def startClip(self, path: str) -> None:
        """Upcoming Jobs' "Merge This Clip Now" (user, 2026-10-10): the batch,
        on that one clip -- Process Selected with just it."""
        if path:
            logger.info("Merge This Clip Now: %s", os.path.basename(path))
            self.start([path])

    @Slot(str)
    def testClip(self, path: str) -> None:
        """Upcoming Jobs' "Test Render" (user, 2026-10-15; studio.test_renders): Merge This Clip Now
        into <Output Folder>/Test Renders, nothing moved to finished/."""
        if path:
            logger.info("Test render: %s", os.path.basename(path))
            self.start([path], test=True)

    @Slot()
    def processCurrentClip(self) -> None:
        """v1 process_current_clip: the batch, on the clip the preview shows.
        The preview's clip list is handed to the engine as it stood (it lives
        on the preview's thread), then the preview lets go, then v1 runs."""
        if self.is_busy() or self._releasing:
            return
        self.save()
        entries, index = self.preview.current_entries()
        self._releasing = True
        self._restart = self.processCurrentClip
        accept, self._accept_next = self._accept_next, frozenset()

        def go():
            self._releasing = False

            def run(eng):
                eng.previewer = self._module._Previewed(entries, index)
                eng.accept_missing_originals = accept
                eng.process_current_clip()
                if not eng.is_processing:          # v1 refused (a warning): give the preview back
                    self.preview.restore_after_job()
            self._with_engine(run)
        self.preview.release_for_job(go)

    @Slot()
    def stop(self) -> None:
        if self._merging_all:                 # Merge All: the movie being written is discarded
            self._merge_stop.set()
            build = self._merge_build
            if build is not None:
                build.stop()
            return
        if self._engine is not None:
            self._engine.stop_processing()

    # ---- no original for some clips (user, 2026-10-09: ask, don't refuse) ---------
    @Slot(list, str)
    def _on_missing_originals(self, missing, original_folder) -> None:
        from core.common.merge_preflight import question
        self._asked_missing = list(missing)
        self.preview.restore_after_job()
        self.missingOriginals.emit("No original for some clips", question(missing, original_folder))

    @Slot()
    def continueWithoutOriginals(self) -> None:
        """Continue: the same start again, those clips merged as the right eye only."""
        if self.is_busy() or not self._asked_missing or self._restart is None:
            return
        logger.info("Continuing without originals for: %s", ", ".join(self._asked_missing))
        self._accept_next, self._asked_missing = frozenset(self._asked_missing), []
        self._restart()

    @Slot()
    def cancelWithoutOriginals(self) -> None:
        self._asked_missing = []
        self._on_var("status_label", "Cancelled: fix the Original folder and start again.")

    # ---- File actions --------------------------------------------------------------------

    @Slot()
    def restoreFinished(self) -> None:
        """v1 restore_finished_files, after the page asked v1's question."""
        def go(eng):
            from . import engine as E
            E.messagebox.answer = True
            try:
                eng.restore_finished_files()
            finally:
                E.messagebox.answer = False
            # v1 then reloads its previewer's list (load_video_list), which
            # now has the restored clips in it.
            self.preview.load()
        self._with_engine(go)

    @Property(str, constant=True)
    def aboutText(self) -> str:
        """Help > About for Merging (v1's show_about_dialog read the Prototype's GUI_VERSION; it doesn't ship)."""
        return ("Merging -- StereoCrafter Studio\n\n"
                "Blends each inpainted right eye with its full-resolution source clip to make the finished "
                "stereo video, with mask processing, colour matching and film grain.")

    @Slot()
    def openOutputFolder(self) -> None:
        err = self._shell.openFolder(self._map.value("output_folder"))
        if err:
            self.message.emit("warning", "Open Output Folder", err)

    # ---- Merge All SBS to 1 Video (user, 2026-10-11; studio/merge_all.py) -----------------

    mergeAllScanned = Signal("QVariantMap")
    _sigMergeScanned = Signal(object)
    _sigMergeDone = Signal(object)
    mergingAll = Property(bool, lambda self: self._merging_all, notify=stateChanged)

    def _merge_base(self) -> str:
        """The project's folder, or for a show the episode of Merging's chapter."""
        fn = getattr(self._shell, "chapterBase", None)
        return fn(self._chapter_num) if fn is not None else ""

    @Slot()
    def mergeAllScan(self) -> None:
        """The button: every scene looked up on a thread; mergeAllScanned brings
        what was found ({"error"} when it can't be looked through)."""
        if self._busy or self.is_busy():
            self.toast.emit("warning", "Merging is busy", "Wait for its job to finish, then try again.")
            return
        base = self._merge_base()
        if not base:
            self.mergeAllScanned.emit({"error": "Which episode? Pick one of its chapters in Merging's chapter "
                                                "list first: Merge All puts that episode together."})
            return
        from studio import merge_all
        opts = merge_all.options_from(self._values())

        def run():
            try:
                info = merge_all.scan(base)
                if "error" not in info:
                    info["outputs"] = [p.replace("\\", "/") for p in merge_all.output_paths(info, opts)]
                    info["layout"] = opts["layout"]
                    info["audio"] = opts["audio_file"]
            except Exception as e:
                logger.exception("Merge All: could not look through %s", base)
                info = {"error": f"Could not look through the project:\n\n{e}"}
            self._sigMergeScanned.emit(info)
        threading.Thread(target=run, name="merge-all-scan", daemon=True).start()

    @Slot(object)
    def _on_merge_scanned(self, info) -> None:
        self._merge_info = info if "error" not in info else None
        self.mergeAllScanned.emit(info)

    @Slot(int, result="QVariantMap")
    def mergeAllSendPlan(self, index: int) -> dict:
        """What loads the scan's missing scene `index` into the stage it still needs."""
        from studio import merge_all
        missing = (self._merge_info or {}).get("missing") or []
        if not 0 <= index < len(missing):
            return {"error": "That scene isn't in the list any more: run Merge All again."}
        return merge_all.send_plan(missing[index])

    @Slot(bool)
    def mergeAllStart(self, override: bool) -> None:
        """Merge the last scan's scenes into the movie; `override`: a missing scene
        is black for its own length."""
        info = self._merge_info
        if info is None or self._busy or self.is_busy():
            return
        if info.get("missing") and not override:
            return
        jobs = getattr(self, "jobs", None)
        if jobs is not None and not jobs.claim("merging", self):
            return
        from studio import merge_all
        opts = merge_all.options_from(self._values())
        self._merge_stop.clear()
        self._merging_all = True
        self._disp.update({"status_label": "Merge All: checking the clips...", "progress": 0.0,
                           "progress_maximum": 1, "processing_clip_count": f"{info['total']} scenes",
                           "current_filename": os.path.basename(info["outputs"][0]) if info.get("outputs") else ""})
        self._set_busy(True)

        def status(text):
            self._sigVar.emit("status_label", "Merge All: " + text)

        def progress(done, total):
            import time
            now = time.monotonic()
            if done < total and now - self._merge_shown < 0.5:        # twice a second: no redraw storm
                return
            self._merge_shown = now
            self._sigVar.emit("progress_maximum", max(1, int(total)))
            self._sigVar.emit("progress", float(done))

        def run():
            try:
                from studio.gpu_jobs import wait_for_gpu
                wait_for_gpu(self)
                paths = [f for sc in info["scenes"] for f in sc["files"]]
                if override:
                    paths += [sc["source"] for sc in info["scenes"] if not sc["kind"]]
                probes = merge_all.probe_all(
                    paths, progress=lambda d, t: status(f"checking the clips {d} / {t}...") if d % 25 == 0 or d == t else None,
                    stop=self._merge_stop)
                if self._merge_stop.is_set():
                    result = {"stopped": True}
                else:
                    plan = merge_all.plan_build(info, probes, override)
                    if "error" in plan:
                        result = plan
                    else:
                        build = merge_all.Build(info, plan, opts, status=status, progress=progress)
                        self._merge_build = build
                        if self._merge_stop.is_set():
                            build.stop()
                        logger.info("Merge All: %d scenes, %d frames, %d black, the %s way, into %s",
                                    len(plan["pieces"]), plan["frames"], plan["black"], plan["way"],
                                    ", ".join(merge_all.output_paths(info, opts)))
                        result = build.run()
            except Exception as e:
                logger.exception("Merge All failed")
                result = {"error": str(e)}
            self._sigMergeDone.emit(result)
        self._merge_thread = threading.Thread(target=run, name="merge-all", daemon=True)
        self._merge_thread.start()

    @Slot(object)
    def _on_merge_done(self, result) -> None:
        self._merge_build = None
        self._merging_all = False
        if result.get("stopped"):
            self._disp["status_label"] = "Merge All stopped; nothing was written."
            self.toast.emit("info", "Merge All stopped", "The unfinished movie was discarded.")
        elif result.get("error"):
            self._disp["status_label"] = "Merge All failed."
            self.message.emit("error", "Merge All", str(result["error"]))
        else:
            outs = result.get("outputs") or []
            black = int(result.get("black") or 0)
            short = result.get("short") or []
            self._disp.update({"status_label": "Merge All done.", "progress": self._disp.get("progress_maximum") or 1})
            logger.info("Merge All: wrote %s", ", ".join(outs))
            # a scene that decoded short is black-filled to keep the sound in step: said, not only
            # logged (bug sweep 2026-10-14c)
            self.toast.emit("warning" if short else "success", "The movie is merged",
                            "\n".join(os.path.normpath(p) for p in outs)
                            + (f"\n{black} missing scene(s) are black." if black else "")
                            + (f"\nCame up short, the rest black: {', '.join(short)}" if short else ""))
        self._set_busy(False)

    @Slot()
    def openMergeAllFolder(self) -> None:
        from studio import merge_all
        folder = merge_all.options_from(self._values())["output_folder"] or self._merge_base()
        err = self._shell.openFolder(folder)
        if err:
            self.message.emit("warning", "Open Folder", err)

    @Slot()
    def releaseGpu(self) -> None:
        if self._engine is not None:
            self._engine.release_gpu()

    # -- GPU hand-over (gpu_jobs; v1 MergingGUI.holds_gpu / release_gpu) --
    def holds_gpu(self) -> bool:
        grain = getattr(self.preview, "_grain", None)
        # a preview job still queued or running counts: a load in flight
        # fills the GPU the moment it lands (audit #14)
        return bool((self._engine is not None and self._engine.holds_gpu()) or self.preview.property("loaded")
                    or not self.preview._worker_idle()
                    or (grain is not None and getattr(grain, "_before", None) is not None))

    def release_gpu(self):
        """The engine's cache now; the preview on its worker, behind any
        render in flight -- the Event says when (gpu_jobs.wait_for_releases)."""
        self.releaseGpu()
        done = threading.Event()
        self.preview.release_for_job(done.set)
        return done

    def restore_gpu(self) -> None:
        self.preview.restore_after_job()

    def shutdown(self) -> None:
        self._save_timer.stop()
        self.save()
        if self._merging_all:
            self.stop()
            # waited for, so its own cleanup runs: a quit left the .part and the black-clip
            # folder (GBs) behind (bug sweep 2026-10-14c)
            t = getattr(self, "_merge_thread", None)
            if t is not None and t.is_alive():
                t.join(timeout=15)
        self.preview.shutdown()
        eng = self._engine
        if eng is not None and eng.is_busy():
            logger.info("Stopping merging before exit...")
            eng.stop_event.set()
            for t in (eng.processing_thread, eng.cleanup_thread):
                if t is not None:
                    t.join(timeout=10)

    # ---- Import from Inpainting (v1 _refresh_import_button / _import_from_previous_stage) --

    importDescription = Property(str, lambda self: self._import_desc, notify=importChanged)
    #: Inpainting V2's last job (job_handoff.SIDE_STAGES), for its own button.
    importV2Description = Property(str, lambda self: self._import_v2_desc, notify=importChanged)

    @Slot()
    def refreshImport(self) -> None:
        self._import_desc = self._import_v2_desc = ""
        try:
            from core.ui.job_handoff import describe, get_record, get_source_record, in_project
            project = self._shell.projectFolder
            record = get_source_record("merging")
            if record and record.get("inpainted_out") and in_project(record, project, ("inpainted_out",)):
                self._import_desc = describe(record) or "Inpainting job"
            v2 = get_record("inpainting_v2")
            if v2 and v2.get("inpainted_out") and in_project(v2, project, ("inpainted_out",)):
                self._import_v2_desc = describe(v2) or "Inpainting V2 job"
        except Exception:
            logger.debug("Could not read the Inpainting handoff", exc_info=True)
        self.importChanged.emit()

    @Slot()
    def importFromInpainting(self) -> None:
        from core.ui.job_handoff import get_source_record
        self._import_record(get_source_record("merging") or {}, "Inpainting")

    @Slot()
    def importFromInpaintingV2(self) -> None:
        from core.ui.job_handoff import get_record
        self._import_record(get_record("inpainting_v2") or {}, "Inpainting V2")

    def _import_record(self, record, label: str) -> None:
        try:
            from core.ui.job_handoff import in_project
            if not in_project(record, self._shell.projectFolder, ("inpainted_out",)):
                return                     # another project's job (the button is hidden then)
            from core.ui.job_handoff import resolve_subfolder
            fwd = lambda p: str(p).replace("\\", "/")  # noqa: E731 - match the Browse dialogs
            filled = []
            # Inside a project (user, 2026-10-02): every folder from the
            # inpainted folder's own chapter, as the chapter list fills them --
            # output too -- whatever else the record carries (it can hold an
            # earlier job's source: core.ui.job_handoff._rebase).
            from core.ui import chapters
            at = chapters.locate_chapter(record.get("inpainted_out") or "")
            if at:
                paths = chapters.stage_paths("merging", *at)
                self._set("inpainted_folder", fwd(record["inpainted_out"]))
                filled.append("inpainted")
                for role, key in (("original", "original_folder"), ("mask", "mask_folder"), ("output", "output_folder")):
                    if paths.get(role):
                        self._set(key, fwd(paths[role]))
                        filled.append(role)
                record = {}
            if record.get("inpainted_out"):
                self._set("inpainted_folder", fwd(record["inpainted_out"]))
                filled.append("inpainted")
            if record.get("source"):
                self._set("original_folder", fwd(resolve_subfolder(record["source"], "finished")))
                filled.append("original")
            if record.get("hires"):
                self._set("mask_folder", fwd(resolve_subfolder(record["hires"], "finished")))
                filled.append("mask")
            if filled:
                self._maybe_leave_chapter()
                self._save_timer.start()
                self._on_var("status_label", "Imported " + ", ".join(filled) + f" folders from {label}.")
        except Exception:
            logger.exception("Import from %s failed", label)

    # ---- Put Back (user, 2026-10-15; core.removal.putback) ---------------------------------
    # A clip erased in the Depth Map Editor (Erase from Clip) keeps its object to lay back over both
    # eyes. When the clip on screen has one, the page shows its controls: on / off, the depth (the
    # original clip's depth map, or one parallax), Convergence, Grow (+ dilate / - erode), Blur and
    # Opacity -- saved beside the clip, where Merging's render and preview read them.

    def _putback_clip(self) -> str:
        """The erased clip on screen (in the Original folder), or ""."""
        entries, index = self.preview.current_entries()
        if not (0 <= index < len(entries)):
            return ""
        core = str((entries[index] or {}).get("core_name") or "")
        folder = str(self._values().get("original_folder") or "")
        if not core or not folder:
            return ""
        if os.path.isfile(folder):
            return folder if os.path.splitext(os.path.basename(folder))[0] == core else ""
        from core.common.sidecar_manager import find_video_by_core_name
        try:
            return find_video_by_core_name(folder, core) or ""
        except Exception:
            return ""

    def _putback_follow(self) -> None:
        clip = self._putback_clip()
        key = clip
        if clip:
            from core.removal import putback as P
            try:
                key = (clip, os.path.getmtime(P.settings_path(clip)) if os.path.isfile(P.settings_path(clip)) else 0)
            except OSError:
                pass
        if key == self._pb_key:
            return
        self._pb_key = key
        self._pb_state = self._putback_read(clip)
        self._pb_filling = True
        try:
            for k in ("enabled", "mode", "depth", "conv", "grow", "blur", "opacity", "parallax"):
                if k in self._pb_state:
                    self._pb_map.insert(k, self._pb_state[k])
        finally:
            self._pb_filling = False
        self.putBackChanged.emit()

    def _putback_read(self, clip: str) -> Dict[str, Any]:
        from core.removal import putback as P
        if not clip:
            return {}
        rec = P.erased(clip)
        if rec is None:
            return {}
        s = P.load_settings(clip)
        modes = list(P.modes_for(rec))
        mode = s["mode"] if s["mode"] in modes else modes[0]
        # each mode's depths (Add: Glass Plane or one Parallax -- 2026-10-17); a depth the mode hasn't shows its first
        depths = list(P.ADD_DEPTHS if mode == "Add" else P.OVER_DEPTHS)
        if s["depth"] not in depths:
            s["depth"] = "Parallax" if "Parallax" in depths else depths[0]
        has_map = bool(s["original_depth"]) and os.path.isfile(s["original_depth"])
        # why Depth Map can't run, said where its dials are (2026-10-16: it fell back to Parallax in silence,
        # and Convergence and Disparity did nothing)
        problem = ""
        stem = os.path.splitext(os.path.basename(clip))[0]
        eyes_found = None
        if s["depth"] == "Original Eyes":
            from core.putback.engine import find_eyes
            eyes_found = find_eyes(s["eyes"], stem)
        if s["depth"] == "Original Eyes" and s["mode"] == "Over" and eyes_found is None:
            problem = (f"Original Eyes: no merge of {stem} found" + (f" in {os.path.basename(s['eyes'].rstrip('/'))}"
                       if s["eyes"] else " -- choose the folder the original clip was merged into") +
                       ". Until then it uses Original Depth.")
        elif ((s["depth"] in P.MAP_DEPTHS and s["mode"] == "Over" and has_map)
              or (s["depth"] == "Glass Plane" and mode == "Add")):
            try:
                found = P.GEOMETRY(stem) if P.GEOMETRY is not None else None
            except Exception:
                found = None
            if not found:
                problem = (f"{s['depth']} can't run: Splatting's Depth Maps input has no {stem}_depth for this clip "
                           "(its clean depth map). Point Splatting's Depth Maps at it -- until then the object "
                           "uses Parallax, and Convergence and Disparity do nothing.")
        return {"available": True, "clip": os.path.basename(clip), "enabled": s["enabled"],
                "mode": mode, "modes": modes,
                "depth": s["depth"], "depths": depths,
                "hasDepthMap": has_map, "depthMapProblem": problem, "eyes": s["eyes"],
                "eyesFound": os.path.basename(eyes_found[0]) if eyes_found else "",
                "conv": s["conv"], "grow": s["grow"], "blur": s["blur"], "disp": s["disp"],
                "opacity": round(s["opacity"] * 100), "parallax": s["parallax"], "tracked": bool(s["track"])}

    putBack = Property("QVariantMap", lambda self: self._pb_state, notify=putBackChanged)
    putBackStore = Property(QObject, lambda self: self._pb_map, constant=True)

    @Slot(str, "QVariant")
    def setPutBack(self, name: str, value) -> None:
        """One of the clip on screen's put-back settings, saved; the preview renders it again."""
        from core.removal import putback as P
        clip = self._putback_clip()
        if not clip or P.erased(clip) is None:
            return
        s = P.load_settings(clip)
        try:
            if name == "enabled":
                s["enabled"] = bool(value)
            elif name == "mode":
                if value not in P.modes_for(P.erased(clip)):
                    raise ValueError(value)
                s["mode"] = str(value)
            elif name == "depth":
                if value not in P.DEPTHS:
                    raise ValueError(value)
                s["depth"] = str(value)
            elif name == "eyes":
                s["eyes"] = str(value or "").replace("\\", "/")
            elif name in ("conv", "grow", "blur", "disp"):
                lo, hi = {"conv": P.CONV_RANGE, "grow": P.GROW_RANGE, "blur": P.BLUR_RANGE, "disp": P.DISP_RANGE}[name]
                s[name] = max(lo, min(hi, float(value)))
            elif name == "parallax":
                s["parallax"] = max(-10.0, min(10.0, float(value)))
                s["track"] = []
            elif name == "opacity":
                s["opacity"] = max(0.0, min(1.0, float(value) / 100.0))
            else:
                return
        except (TypeError, ValueError):
            self.putBackChanged.emit()
            return
        P.save_settings(clip, s)
        self._pb_key = None
        self._putback_follow()
        self.preview.refresh()

    # ---- chapters -----------------------------------------------------------------------

    chapters = Property("QVariantList", lambda self: self._chapters, notify=chapterChanged)
    chapterNum = Property(int, lambda self: self._chapter_num, notify=chapterChanged)
    hasProject = Property(bool, lambda self: bool(self._shell.projectFolder), notify=chapterChanged)

    #: v1 _install_chapter_picker("merging", ...): the roles and the fields they fill.
    _CHAPTER_FIELDS = (("inpainted", "inpainted_folder"), ("original", "original_folder"),
                       ("mask", "mask_folder"), ("output", "output_folder"))

    @Slot()
    def refreshChapters(self) -> None:
        self._chapters = self._shell.listChapters()
        saved = self._shell.stageChapter("merging")
        nums = {c["num"] for c in self._chapters}
        self._chapter_num = saved.get("num", -1) if saved.get("num") in nums else -1
        if self._chapter_num >= 0:
            self._chapter_expected = self._shell.stagePaths("merging", self._chapter_num, saved.get("name", ""))
        self.chapterChanged.emit()

    @Slot(int)
    def pickChapter(self, num: int) -> None:
        if num < 0:
            self._chapter_num = -1
            self._chapter_expected = {}
            self._shell.setStageChapter("merging", {})
            self.chapterChanged.emit()
            return
        name = next((c["name"] for c in self._chapters if c["num"] == num), "")
        paths = self._shell.stagePaths("merging", num, name)
        self._chapter_applying = True
        try:
            for role, key in self._CHAPTER_FIELDS:
                if paths.get(role):
                    self._set(key, paths[role].replace("\\", "/"))
        finally:
            self._chapter_applying = False
        self._chapter_num = num
        self._chapter_expected = paths
        self._shell.setStageChapter("merging", {"num": num, "name": name})
        self._save_timer.start()
        self.chapterChanged.emit()
        if not getattr(self, "_restoring", False):        # a project switch restores quietly
            self.toast.emit("info", "Chapter selected",
                            f"{name}\nInpainted, original, mask and output now point at this chapter.")

    def _maybe_leave_chapter(self) -> None:
        if self._chapter_num < 0:
            return
        for role, key in self._CHAPTER_FIELDS:
            exp = self._chapter_expected.get(role)
            # a job moving the clips a finished level down is not a hand edit
            if exp and not chapter_paths.same_place(exp, self._map.value(key)):
                self._chapter_num = -1
                self._shell.setStageChapter("merging", {})
                self.chapterChanged.emit()
                return
