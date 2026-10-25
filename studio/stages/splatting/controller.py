"""controller.py -- Splatting as QML sees it.

The render is v1's own: ``core.splatting.BatchProcessor``, which is already
toolkit-neutral (a progress queue in, a stop event, a ProcessingSettings built
from a flat config). So nothing is copied or generated -- this file is the
glue v1's SplatterGUI was: settings in a QQmlPropertyMap QML binds to, Start
building and validating the settings exactly as start_processing does, and a
100 ms poll of the progress queue on the UI thread as check_queue does.

core.splatting pulls in torch, so it is imported on first Start (and the
batch processor built then), never when the tab opens.
"""

from __future__ import annotations

import json
import logging
import os
from studio import chapter_paths
from studio.gpu_jobs import wait_for_gpu
from studio.outcome import Outcome, verdict
import queue
import threading
from typing import Any, Dict, Optional

from PySide6.QtCore import Property, QObject, Qt, QTimer, Signal, Slot
from PySide6.QtQml import QQmlPropertyMap

from ... import settings_store
from ...legacy import REPO
from . import settings as S
from .preview import SplattingPreview

logger = logging.getLogger("splatting.v2")

HELP_FILE = REPO / "dependency" / "splatter_help.json"
SIDECAR_EXT = ".fssidecar"          # v1 APP_CONFIG_DEFAULTS["SIDECAR_EXT"]

#: Current Clip Info rows, in v1's order, keyed as the render's
#: ("update_info", {...}) messages key them.
INFO_KEYS = ("filename", "task_name", "resolution", "frames", "disparity", "convergence", "gamma", "map")


#: The Low Res presets built in: none (user, 2026-10-11 -- they were DepthCrafter's
#: three shapes at 480p / 720p / 1080p). Every column is this machine's own, made in
#: Settings > Splatting > Custom Resolution Presets (config/splatting_presets.json,
#: never shipped), in multiples of 16 (settings.PRESET_STEP).

#: A shared encoding preset's keys -> this stage's variable names
#: (v1 _apply_encoding_preset).
_PRESET_KEYS = {"codec": "codec", "encoding_encoder": "encoder", "encoding_quality": "encoding_quality",
                "encoding_tune": "encoding_tune", "bit_depth": "bit_depth", "pix_fmt": "pix_fmt",
                "color_tags": "color_tags_mode", "nvenc_lookahead_enabled": "encoding_nvenc_lookahead_enabled",
                "nvenc_lookahead": "encoding_nvenc_lookahead", "nvenc_spatial_aq": "encoding_nvenc_spatial_aq",
                "nvenc_temporal_aq": "encoding_nvenc_temporal_aq",
                "nvenc_aq_strength": "encoding_nvenc_aq_strength", "dnxhr_fullres_split": "dnxhr_fullres_split",
                "dnxhr_profile": "dnxhr_profile"}


def _choices() -> Dict[str, list]:
    out = {"auto_convergence_mode": ["Off", "Manual", "Average", "Peak", "Hybrid"],
           "border_mode": ["Manual", "Auto Basic", "Auto Adv.", "Off"],
           "depth_blur_left_mix": [f"{i / 10:.1f}" for i in range(0, 11)],
           "mask_mode": ["SC", "M2S", "Hybrid", "Dual"],
           "splat_backend": ["SC", "M2S", "M2S+Fix"],
           # v1 set_source_values / VideoPreviewer PERCENTAGE_VALUES
           "preview_source": ["Splat Result", "Splat Result(Low)", "Occlusion Mask", "Occlusion Mask(Low)",
                              "Original (Left Eye)", "Depth Map", "Depth Map (Color)", "Boost Zone",
                              "Anaglyph 3D", "Dubois Anaglyph", "Optimized Anaglyph", "ColorCode (Amber/Blue)",
                              "ColorCode (Blue/Amber)", "Side-by-Side", "Side-by-Side (Cross-eye)", "Wigglegram",
                              "Mesh Warp", "SBS + Mesh"],
           "preview_size": [f"{p}%" for p in (250, 240, 230, 220, 210, 200, 190, 180, 170, 160, 150, 145, 140,
                                               135, 130, 125, 120, 115, 110, 105, 100, 95, 90, 85, 80, 75, 70,
                                               65, 60, 55, 50, 25)]}
    try:
        from core.splatting import m2s_backends
        out["mask_mode"] = list(m2s_backends.MASK_MODES)
        out["splat_backend"] = list(m2s_backends.SPLAT_BACKENDS)
    except Exception:
        pass
    # The Encoding Options dialog's lists (shared names; EncodingDialog.keyMap
    # maps them onto this stage's settings).
    out.update({"codec": ["Auto", "H.264", "H.265"], "encoding_encoder": ["Auto", "NVENC", "Force CPU"],
                "encoding_quality": ["Fastest", "Faster", "Fast", "Medium", "Slow", "Slower", "Slowest"],
                "encoding_tune": ["None", "Film", "Grain", "Animation", "Still Image", "PSNR", "SSIM",
                                  "Fast Decode", "Zero Latency"],
                "bit_depth": ["Auto", "8-bit", "10-bit (Main10)"],
                "pix_fmt": ["Auto", "yuv420p", "yuv420p10le", "p010le"],
                "color_tags": ["Off", "Auto", "BT.709 L", "BT.709 F", "BT.2020 PQ", "BT.2020 HLG"],
                "dnxhr_profile": ["SQ (8-bit 4:2:2)", "HQ (8-bit 4:2:2)", "HQX (10-bit 4:2:2)",
                                  "444 (10-bit 4:4:4)"]})
    try:
        from core.common import encoding_utils as eu
        out.update({"codec": list(eu.CODEC_OPTIONS), "encoding_encoder": list(eu.ENCODER_OPTIONS),
                    "encoding_quality": list(eu.QUALITY_PRESETS), "encoding_tune": list(eu.CPU_TUNE_OPTIONS),
                    "bit_depth": list(eu.BIT_DEPTH_OPTIONS), "pix_fmt": list(eu.PIX_FMT_OPTIONS),
                    "dnxhr_profile": list(eu.DNXHR_PROFILE_OPTIONS)})
    except Exception:
        pass
    if "Auto" not in out["encoding_tune"]:
        out["encoding_tune"] = ["Auto"] + out["encoding_tune"]
    return out


def _encoding_presets() -> Dict[str, dict]:
    try:
        from core.ui.encoding_settings import PRESET_8BIT, PRESET_10BIT, get_encoding_preset
        return {name: dict(get_encoding_preset(name) or {}) for name in (PRESET_8BIT, PRESET_10BIT)}
    except Exception:
        logger.debug("Encoding presets unavailable", exc_info=True)
        return {}


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
    return texts


class SplattingController(QObject):
    stateChanged = Signal()
    infoChanged = Signal()
    importChanged = Signal()
    chapterChanged = Signal()
    encodingChanged = Signal()
    _sigStatus = Signal(str)             # status from a worker thread
    toast = Signal(str, str, str)
    fieldRejected = Signal(str)
    presetsChanged = Signal()
    shapeChanged = Signal()
    _sigShape = Signal(str, str)        # (clip, its ratio class) from the probe thread

    def __init__(self, shell, parent=None):
        super().__init__(parent)
        self._shell = shell
        self._help = _load_help()
        self._choices = _choices()
        self._presets = _encoding_presets()
        # the loaded clip's shape, for the Low Res presets and Auto Size (user, 2026-10-11)
        self._shape = ""
        self._shape_path: Optional[str] = None
        self._sigShape.connect(self._on_shape, Qt.ConnectionType.QueuedConnection)
        try:
            from core.splatting import m2s_backends
            self._m2s = bool(m2s_backends.backend_available())
        except Exception:
            self._m2s = False

        stored = settings_store.load("splatting", REPO / "config_splat.splatcfg", S.seed_from_v1)
        self._map = QQmlPropertyMap(self)
        for key, value in S.restore_folders(S.normalise(stored)).items():
            self._map.insert(key, value)
        self._last_good: Dict[str, Any] = self._values()
        self._map.valueChanged.connect(self._on_value_changed)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self.save)

        # run state -- v1 check_queue's fields
        self._busy = False
        self._status = "Ready"
        self._done = 0
        self._total = 0
        self._pass_frac = 0.0       # how far the pass under way is, 0..1
        self._clip_count = "N/A"
        # the running clip's passes, [Full-Resolution, Low-Resolution] (user,
        # 2026-09-28: an H and an L bar per clip in Upcoming Jobs): the frames
        # the renderer has reported for each, and which have finished
        self._pass = -1
        self._pass_frames = [0, 0]
        self._pass_done = [False, False]
        self._info: Dict[str, str] = {k: "N/A" for k in INFO_KEYS}
        self._queue: "queue.Queue" = queue.Queue()
        self._stop = threading.Event()
        self._stop_after = threading.Event()    # Stop After Clip
        self._clip_total = 0                     # clips in the running batch
        self._thread: Optional[threading.Thread] = None
        self._processor = None
        self._run_values: Dict[str, Any] = {}
        self._poll = QTimer(self)
        self._poll.setInterval(100)
        self._poll.timeout.connect(self._drain)

        # The preview (phase 3): v1's own, generated -- see preview.py.
        self._applying_preview = False
        self.preview = SplattingPreview(self._values, self)
        # Upcoming Jobs keeps the job's clips (and their bars) while it runs
        self.stateChanged.connect(lambda: self.preview.queue.hold(self._busy))
        self._map_test = False
        self._splat_test = False
        self._single_test = ""
        self.preview.singleReady.connect(self._on_single_ready)
        self.preview.statusMessage.connect(lambda text: None if self._busy else self._set_status(text))
        self.preview.settingsWritten.connect(self._take_preview_writes)
        self.preview.infoWritten.connect(self._take_preview_info)
        self.preview.toast.connect(self.toast)
        self.preview.changed.connect(self._check_loaded_clip)
        self._sigStatus.connect(self._set_status)

        self._chapters: list = []
        self._chapter_num = -1
        self._chapter_expected: Dict[str, str] = {}
        self._chapter_applying = False
        self._import_desc = ""
        self._import_origin = "DepthCrafter"

        shell.projectChanged.connect(self.refreshChapters)
        shell.projectChanged.connect(self.refreshImport)       # the project's own jobs only
        # the folders / chapter this stage last used in each project (user, 2026-10-02)
        from studio import project_memory
        project_memory.attach(self, "splatting", shell, [k for _r, k in self._CHAPTER_FIELDS])
        self.refreshChapters()
        self.refreshImport()

    # ---- settings --------------------------------------------------------------

    settings = Property(QObject, lambda self: self._map, constant=True)
    previewer = Property(QObject, lambda self: self.preview, constant=True)
    help = Property("QVariantMap", lambda self: self._help, constant=True)
    choices = Property("QVariantMap", lambda self: self._choices, constant=True)
    # ---- Low Res resolution presets + Auto Size (user, 2026-10-11) -------------
    # DepthCrafter's custom presets and Auto Size, in multiples of 16: the same
    # slots, so PresetButton and DcPresetEditor serve both stages.

    @Property("QVariantList", notify=presetsChanged)
    def presets(self):
        """PresetButton's columns: this machine's own, each size with how far it
        is from its ratio (or its saved tag, "720p"); a clip loaded: only its
        shape's column, its Auto pick marked."""
        cols = []
        for cat, items in S.local_presets(settings_store.CONFIG_DIR):
            ratio = S.RP.ratio_value(cat)
            cols.append({"category": cat, "items": [
                {"w": w, "h": h, "ratio": t or (S.RP.disparity_label(w, h, ratio) if ratio else "")}
                for w, h, t in items]})
        if not self._shape:
            return cols
        shape = self._shape
        cols = [c for c in cols if c["category"] == shape]
        aw, ah = S.auto_sizes(settings_store.CONFIG_DIR)[shape]
        found = False
        for c in cols:
            for it in c["items"]:
                if (it["w"], it["h"]) == (aw, ah):
                    it["ratio"] = "auto" + (" · " + it["ratio"] if it["ratio"] else "")
                    found = True
        if not found:
            cols.insert(0, {"category": shape, "items": [
                {"w": aw, "h": ah, "ratio": "auto · " + S.RP.disparity_label(aw, ah, S.RP.ratio_value(shape))}]})
        return cols

    @Property(str, notify=shapeChanged)
    def loadedShape(self):
        """The loaded clip's aspect ratio ('2.39:1'), '' when none is known."""
        return self._shape

    @Property(str, notify=shapeChanged)
    def presetNote(self):
        return f"Sizes for {self._shape}, the loaded clip's shape" if self._shape else ""

    def _check_loaded_clip(self) -> None:
        """The preview moved to another clip (or loaded one): read its shape off
        the UI thread -- ffprobe on a cloud drive can take seconds."""
        pv = self.preview
        paths, i = list(getattr(pv, "_clip_paths", []) or []), int(getattr(pv, "_index", -1))
        path = paths[i] if getattr(pv, "_loaded", False) and 0 <= i < len(paths) else ""
        if path == (self._shape_path or ""):
            return
        self._shape_path = path
        if not path:
            return

        def work():
            shape = ""
            try:
                shape = S.RP.ratio_class(S.RP.video_ratio(path)) or ""
            except Exception:
                logger.debug("No clip shape for %s", path, exc_info=True)
            self._sigShape.emit(path, shape)

        threading.Thread(target=work, name="splatting-shape", daemon=True).start()

    @Slot(str, str)
    def _on_shape(self, path: str, shape: str) -> None:
        if path != self._shape_path:
            return                          # the preview moved on since
        if shape != self._shape:
            self._shape = shape
            self.shapeChanged.emit()
            self.presetsChanged.emit()
        if shape and bool(self._map.value("low_res_auto_size")) and not self._busy:
            self._apply_auto_size()

    def _apply_auto_size(self) -> None:
        w, h = S.auto_sizes(settings_store.CONFIG_DIR)[self._shape]
        if (str(self._map.value("pre_res_width")), str(self._map.value("pre_res_height"))) != (str(w), str(h)):
            self.applyResolutionSize(w, h)
            logger.info(f"Auto size: the loaded clip is {self._shape}, so Low Res {w}x{h}.")

    def _size_for_clip_fn(self, values: Dict[str, Any]):
        """For a batch with Auto Size on: clip path -> (w, h) for a clip of another
        shape than the loaded one (its shape's pick), else None (the page's size).
        Called by core.splatting's batch on its own thread, once per clip."""
        loaded = self._shape or None
        picks = S.auto_sizes(settings_store.CONFIG_DIR)

        def size_for(path: str):
            shape = S.RP.ratio_class(S.RP.video_ratio(path))
            if not shape or shape == loaded:
                return None
            return tuple(picks[shape])

        return size_for

    #: what a size typed into the editor must be a multiple of
    presetStep = Property(int, lambda self: S.PRESET_STEP, constant=True)

    @Slot(result="QVariantList")
    def customRatios(self):
        return list(S.RP.CUSTOM_RATIOS)

    @Slot(str, result="QVariantList")
    def customSizes(self, label: str):
        """The sizes offered for an aspect ratio: [{w, h, key, diff}] -- the
        suggestions, plus any size already saved for it (typed in by hand, or
        from before) and its Auto pick, so they stay ticked and pickable."""
        return S.RP.offered(label, S.sizes_for, S.local_presets(settings_store.CONFIG_DIR),
                            S.auto_sizes(settings_store.CONFIG_DIR))

    @Slot(result="QVariantMap")
    def customSaved(self):
        return {cat: [f"{w}x{h}" for w, h, _t in items]
                for cat, items in S.local_presets(settings_store.CONFIG_DIR) if cat in S.RP.CUSTOM_RATIOS}

    @Slot("QVariantMap", result=bool)
    def saveCustomPresets(self, picked) -> bool:
        try:
            data = {}
            for cat, keys in dict(picked or {}).items():
                if cat in S.RP.CUSTOM_RATIOS:
                    data[cat] = [tuple(int(v) for v in str(k).split("x")) for k in (keys or [])]
            for cat in S.RP.CUSTOM_RATIOS:
                data.setdefault(cat, [])
            S.save_local_presets(settings_store.CONFIG_DIR, data)
        except Exception:
            logger.exception("Could not save the Low Res presets")
            return False
        self.presetsChanged.emit()
        return True

    @Slot(result="QVariantMap")
    def autoSizes(self):
        return {lab: f"{w}x{h}" for lab, (w, h) in S.auto_sizes(settings_store.CONFIG_DIR).items()}

    @Slot(str, result=str)
    def defaultAutoSize(self, label: str) -> str:
        w, h = S.default_auto_size(label)
        return f"{w}x{h}"

    @Slot("QVariantMap", result=bool)
    def saveAutoSizes(self, picks) -> bool:
        try:
            S.save_auto_sizes(settings_store.CONFIG_DIR,
                              {lab: tuple(int(v) for v in str(k).split("x")) for lab, k in dict(picks or {}).items()})
        except Exception:
            logger.exception("Could not save the Low Res auto sizes")
            return False
        self.presetsChanged.emit()
        return True
    m2sAvailable = Property(bool, lambda self: self._m2s, constant=True)
    encodingPreset = Property(str, lambda self: self._encoding_preset(), notify=encodingChanged)

    def _values(self) -> Dict[str, Any]:
        return {k: self._map.value(k) for k in self._map.keys()}

    def _on_value_changed(self, key: str, value: Any) -> None:
        if key in S.NUMERIC:
            try:
                number = float(str(value).strip())
                ok = number == number and (key not in S.WHOLE or number.is_integer())
            except ValueError:
                ok = False
            if ok and key in S.WHOLE and str(value).strip() != str(int(number)):
                value = str(int(number))        # "1e3" / "10.0" as the render reads it
                self._map.insert(key, value)
            if not ok:
                self._map.insert(key, self._last_good.get(key, S.DEFAULTS.get(key)))
                self.fieldRejected.emit(key)
                return
        coerced = S.coerce(key, value)
        if coerced != value or type(coerced) is not type(value):
            self._map.insert(key, coerced)
        self._last_good[key] = coerced
        if key in S.FOLDER_KEYS and not self._chapter_applying:
            self._maybe_leave_chapter()
        if key in _PRESET_KEYS.values():
            self.encodingChanged.emit()
        if key == "low_res_auto_size" and coerced and self._shape and not self._busy:
            self._apply_auto_size()
        self.preview.note_change(key, coerced)
        if key in self._SETTING_INFO:
            self._fill_settings_info(emit=True)
        self._save_timer.start()

    def _set(self, key: str, value: Any) -> None:
        """Programmatic write (not from QML): no valueChanged, so record it here."""
        value = S.coerce(key, value)
        self._map.insert(key, value)
        self._last_good[key] = value
        if not self._applying_preview:
            self.preview.note_change(key, value)
        if key in self._SETTING_INFO:
            self._fill_settings_info(emit=True)

    def _take_preview_writes(self, writes: Dict[str, Any]) -> None:
        """What the preview's v1 code set: a clip's sidecar values loaded into
        the sliders, Boost zeroed on a clip load. Applied without sending them
        back -- the engine already has them."""
        self._applying_preview = True
        try:
            for key, value in writes.items():
                if key in S.DEFAULTS:
                    self._set(key, value)
        finally:
            self._applying_preview = False
        self._save_timer.start()

    #: v1's processing_*_var -> Current Clip Info rows.
    _PREVIEW_INFO = {"processing_filename": "filename", "processing_task_name": "task_name",
                     "processing_resolution": "resolution", "processing_frames": "frames",
                     "processing_disparity": "disparity", "processing_convergence": "convergence",
                     "processing_gamma": "gamma", "processing_map": "map"}

    def _take_preview_info(self, info: Dict[str, Any]) -> None:
        if self._busy:
            return
        for key, value in info.items():
            row = self._PREVIEW_INFO.get(key)
            if row:
                self._info[row] = str(value) if value not in (None, "") else "N/A"
        self._fill_settings_info()
        self.infoChanged.emit()

    #: Current Clip Info rows that are the clip's settings (the knobs, its
    #: sidecar's values once loaded) -- v1 never filled them outside a job.
    _SETTING_INFO = {"max_disp": "disparity", "zero_disparity_anchor": "convergence", "depth_gamma": "gamma"}

    def _fill_settings_info(self, emit: bool = False) -> None:
        """Disparity / Converge / Gamma for the clip the preview shows, as set
        now; N/A with no clip shown. Not while a job runs (it reports its own)."""
        if self._busy:
            return
        shown = self._info.get("filename", "N/A") not in ("N/A", "")
        for key, row in self._SETTING_INFO.items():
            if not shown:
                self._info[row] = "N/A"
                continue
            try:
                self._info[row] = f"{float(self._map.value(key)):g}"
            except (TypeError, ValueError):
                self._info[row] = str(self._map.value(key))
        if emit:
            self.infoChanged.emit()

    @Slot()
    def save(self) -> None:
        settings_store.save("splatting", S.normalise(self._values()))

    @Slot("QVariantMap")
    def loadSent(self, plan) -> None:
        """Upcoming Jobs' "Send to Splatting" (user, 2026-10-11; studio.send_on): a clip
        the stage before finished, as this stage's input -- one file, so Start runs
        just it -- with the chapter's output folder. The settings are left as they are."""
        if self._busy:
            self.toast.emit("warning", "Splatting is busy", "Wait for its job to finish, then try again.")
            return
        fields = dict(plan.get("fields") or {})
        for key, value in fields.items():
            self._set(key, os.path.normpath(str(value)))
        self._maybe_leave_chapter()           # a single clip isn't a chapter's paths
        self.save()
        out = fields.get("output_splatted")
        self.toast.emit("info", "Ready in Splatting",
                        f"{plan.get('clip', '')}\nInput: {os.path.basename(str(plan.get('input', '')))}\n"
                        f"Output: {os.path.normpath(out) if out else '(unchanged)'}\n"
                        "Check the settings, then press Start.")

    @Slot()
    def resetDefaults(self) -> None:
        """v1 reset_to_defaults: config_manager.reset_to_defaults resets only the
        variables whose NAME is a key of SPLATTER_DEFAULT_CONFIG, to that
        dict's value -- so e.g. the convergence anchor (saved as
        "convergence_point") and the encoder ("encoding_encoder") keep theirs."""
        from core.splatting.config_manager import SPLATTER_DEFAULT_CONFIG
        for key, value in SPLATTER_DEFAULT_CONFIG.items():
            if key in S.DEFAULTS:
                self._set(key, value)
        # ...and the picture settings v1 never had (bug sweep A6): S.RESET_EXTRA
        for key, value in S.DEFAULTS.items():
            if key not in SPLATTER_DEFAULT_CONFIG and (key in S.RESET_EXTRA
                                                       or key.startswith(S.RESET_EXTRA_PREFIXES)):
                self._set(key, value)
        self.save()
        self.encodingChanged.emit()
        self._info = {k: "N/A" for k in INFO_KEYS}
        self._clip_count = "N/A"
        self._set_status("Settings reset to defaults.")

    @Slot()
    def restoreFinished(self) -> None:
        """v1 restore_finished_files: <source>/finished and <depth>/finished back."""
        src, dep = self._map.value("input_source_clips"), self._map.value("input_depth_maps")
        if not (os.path.isdir(src) and os.path.isdir(dep)):
            self.toast.emit("error", "Restore Error",
                            "Restore 'finished' operation is only applicable when Input Source Clips and Input "
                            "Depth Maps are set to directories (batch mode). Please ensure current settings "
                            "reflect this.")
            self._set_status("Restore finished: Not in batch mode.")
            return
        try:
            from core.common.file_organizer import restore_finished_files
            restored, errors, _failed = restore_finished_files(
                restore_dirs=[(src, "finished"), (dep, "finished")], logger=logger, wait_before_move=0.5)
        except Exception as e:
            self.toast.emit("error", "Restore failed", str(e))
            return
        self._set_status(f"Restore complete: {restored} files moved, {errors} errors.")
        self.toast.emit("warning" if errors else "success", "Restore complete",
                        f"{restored} files moved, {errors} errors.")

    @Slot(int, int)
    def applyResolutionSize(self, w: int, h: int) -> None:
        """PresetButton's pick: a size, not a list index."""
        self._set("pre_res_width", str(int(w)))
        self._set("pre_res_height", str(int(h)))
        self._save_timer.start()

    # ---- encoding presets (v1's 8-bit / 10-bit quick-pick) --------------------

    presetNames = Property("QVariantList", lambda self: list(self._presets), constant=True)

    @Property(str, constant=True)
    def presetTooltip(self) -> str:
        """The 8-bit / 10-bit buttons' tooltip, as Merging's (user, 2026-10-25)."""
        try:
            from core.ui.encoding_settings import preset_tooltip
            return preset_tooltip()
        except Exception:
            return ""

    def _encoding_preset(self) -> str:
        cur = self._values()
        for name, preset in self._presets.items():
            if preset and all(str(cur.get(_PRESET_KEYS[k])) == str(v) for k, v in preset.items()
                              if k in _PRESET_KEYS):
                return name
        return "Custom"

    @Slot(str)
    def applyEncodingPreset(self, name: str) -> None:
        """v1 _apply_encoding_preset: the codec/format side only; CRF untouched."""
        for key, value in (self._presets.get(name) or {}).items():
            if key in _PRESET_KEYS:
                self._set(_PRESET_KEYS[key], value)
        self.save()
        self.encodingChanged.emit()

    @Slot(str)
    def loadSettingsFrom(self, path: str) -> None:
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            for key, value in S.seed_from_v1(data).items():
                self._set(key, value)
            self.save()
            self.toast.emit("success", "Settings loaded", os.path.basename(path))
        except Exception as e:
            self.toast.emit("error", "Could not load settings", str(e))

    @Slot(str)
    def saveSettingsTo(self, path: str) -> None:
        """Written as v1 writes its config, so v1 can load it."""
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(S.config_for_run(self._values()), f, indent=4)
            self.toast.emit("success", "Settings saved", path)
        except Exception as e:
            self.toast.emit("error", "Could not save settings", str(e))

    # ---- run state -------------------------------------------------------------

    busy = Property(bool, lambda self: self._busy, notify=stateChanged)
    status = Property(str, lambda self: self._status, notify=infoChanged)
    #: in hundredths of a task: whole passes done, plus how far the one under
    #: way is (its frames of the clip's), so the bar moves within a pass
    progressDone = Property(int, lambda self: int(round(100 * min(self._total, self._done + self._pass_frac))),
                            notify=infoChanged)
    progressTotal = Property(int, lambda self: 100 * self._total, notify=infoChanged)
    clipCount = Property(str, lambda self: self._clip_count, notify=infoChanged)
    passFrames = Property("QVariantList", lambda self: list(self._pass_frames), notify=infoChanged)
    passDone = Property("QVariantList", lambda self: list(self._pass_done), notify=infoChanged)
    info = Property("QVariantMap", lambda self: dict(self._info), notify=infoChanged)

    # ---- Stop After Clip: finish the clip rendering, then end the batch -------------

    stopAfterClipChanged = Signal()
    stopAfterClip = Property(bool, lambda self: self._stop_after.is_set(), notify=stopAfterClipChanged)
    #: the running batch has more than one clip (the button's only use)
    multiClip = Property(bool, lambda self: self._busy and self._clip_total > 1, notify=infoChanged)

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

    def _ensure_processor(self):
        if self._processor is None:
            from core.common.sidecar_manager import SidecarConfigManager
            from core.splatting import BatchProcessor
            self._processor = BatchProcessor(self._queue, self._stop, SidecarConfigManager())
        return self._processor

    def build_settings(self, values: Optional[Dict[str, Any]] = None):
        """v1 _get_processing_settings, from these values (default: current)."""
        from core.splatting import ProcessingSettings
        values = S.normalise(values if values is not None else self._values())
        return ProcessingSettings.from_config(S.config_for_run(values),
                                              **S.processing_overrides(values, SIDECAR_EXT))

    def _prepare(self, values: Dict[str, Any]):
        """Build and validate the run's settings; (processor, settings) or None."""
        try:
            processor = self._ensure_processor()
            settings = self.build_settings(values)
        except (ValueError, TypeError) as e:
            self._set_status(f"Error: {e}")
            self.toast.emit("error", "Cannot start", str(e))
            return None
        except Exception as e:
            logger.exception("Splatting could not start")
            self.toast.emit("error", "Splatting failed to load", str(e) or e.__class__.__name__)
            return None
        ok, message = processor.validate_settings(settings)
        if not ok:
            self._set_status(f"Error: {message}")
            self.toast.emit("error", "Cannot start", message)
            return None
        if S.normalise(values).get("low_res_auto_size"):
            settings.low_res_size_for = self._size_for_clip_fn(values)
        return processor, settings

    def _begin(self, processor, settings, values, from_idx, to_idx, release: bool = True) -> None:
        self._run_values = values
        self._stop.clear()
        self._busy = True           # Start is taken: no second one while the preview lets go
        self._clear_run_info()
        self._status = "Releasing the preview's GPU memory..." if release else "Starting processing..."
        self.stateChanged.emit()
        self.infoChanged.emit()
        if release:
            # The batch gets the GPU to itself: the preview's readers, cached
            # frames and CUDA tensors go first, then the run starts.
            self.preview.release_for_job(lambda: self._launch(processor, settings, from_idx, to_idx))
        else:
            self._launch(processor, settings, from_idx, to_idx)

    def _claim(self) -> bool:
        """v1 _claim_job: the shell's request_job (studio/gpu_jobs.py)."""
        jobs = getattr(self, "jobs", None)
        return jobs is None or jobs.claim("splatting", self)

    # -- GPU hand-over (gpu_jobs; v1 SplattingGUI.holds_gpu / release_gpu) --
    def holds_gpu(self) -> bool:
        """The preview's engine and projector, while a clip is loaded -- or
        about to be: a load still queued or running (the very first one is
        importing the engine) fills the GPU the moment it lands (audit #14)."""
        return bool(self.preview.loaded) or not self.preview._worker_idle()

    def release_gpu(self):
        """The preview lets go on its worker, behind any render in flight:
        the Event says when it has (gpu_jobs.wait_for_releases)."""
        done = threading.Event()
        self.preview.release_for_job(done.set)
        return done

    def restore_gpu(self) -> None:
        self.preview.restore_after_job()

    @Slot()
    def releaseGpu(self) -> None:
        """Settings > GPU > Release GPU Memory (audit 2026-09-28, minor: it
        skipped Splatting): the preview lets go, as for a job, and stays
        unloaded -- picking a clip loads it again. v1 dropped its projector."""
        self.preview.release_for_job(lambda: None)
        self.preview._resume_after_job = False

    @Slot()
    def start(self) -> None:
        """v1 start_processing: build, validate with the core, run on a thread."""
        from studio.clip_queue import move_pending
        if self._busy or move_pending(getattr(getattr(self, "preview", None), "queue", None), self.toast):
            return
        self.save()
        values = S.normalise(self._values())
        prepared = self._prepare(values)
        if prepared is None:
            return
        from_idx, to_idx = S.processing_range(values)
        if not self._claim():
            return
        self._begin(*prepared, values, from_idx, to_idx)

    @Slot()
    def startSelected(self) -> None:
        """Process Selected (user, 2026-09-30): the batch, on the clips ticked
        in Upcoming Jobs only (the From/To range doesn't apply)."""
        from studio.clip_queue import move_pending
        if self._busy or move_pending(getattr(getattr(self, "preview", None), "queue", None), self.toast):
            return
        picked = self.preview.queue.checked_paths()
        if not picked:
            self.toast.emit("warning", "Process Selected", "Tick the clips to process in Upcoming Jobs first.")
            return
        self.save()
        values = S.normalise(self._values())
        prepared = self._prepare(values)
        if prepared is None:
            return
        prepared[1].only_videos = list(picked)
        logger.info("Process Selected: %d clip(s).", len(picked))
        if not self._claim():
            return
        self._begin(*prepared, values, 0, None)

    # ---- SINGLE, and the Map / Splat Test it runs (v1 start_single_processing)

    #: Dev Tools' Map Test / Splat Test: session-only, as in v1 (not saved).
    testsChanged = Signal()
    mapTest = Property(bool, lambda self: self._map_test, lambda self, v: self._set_test("map", v), notify=testsChanged)
    splatTest = Property(bool, lambda self: self._splat_test, lambda self, v: self._set_test("splat", v),
                         notify=testsChanged)

    def _set_test(self, which: str, on: bool) -> None:
        if which == "map":
            self._map_test = bool(on)
        else:
            self._splat_test = bool(on)
        self.testsChanged.emit()

    @Slot()
    def startSingle(self) -> None:
        self._test_render = False            # a Test Render that never started isn't this run
        self._start_single(need_loaded=True)

    def _start_single(self, need_loaded: bool) -> None:
        if self._busy:
            return
        if need_loaded and not self.preview.loaded:
            self.toast.emit("warning", "Process Single Clip", "Please load a video in the Previewer first.")
            return
        values = S.normalise(self._values())
        if not (values.get("enable_full_res") or values.get("enable_low_res")):
            self.toast.emit("error", "Validation Error",
                            "At least one resolution (Full or Low) must be enabled to start processing.")
            return
        self.save()
        if not self._claim():
            return
        test = "map" if self._map_test else ("splat" if self._splat_test else "")
        self._busy = True
        self._clear_run_info()
        self._status = "Preparing single-clip processing..."
        self.stateChanged.emit()
        self.infoChanged.emit()
        self._single_test = test
        self.preview.prepare_single(test)

    @Slot(str)
    def startClip(self, path: str) -> None:
        """Upcoming Jobs' "Splat This Clip Now" (user, 2026-10-10): Single, on
        that clip. It is shown in the preview first -- the preview's jobs run in
        order, so Single's own job reads the clip once it has loaded (a load
        that fails says so, as Single does with nothing loaded)."""
        self._start_clip(path, False)

    @Slot(str)
    def testClip(self, path: str) -> None:
        """Upcoming Jobs' "Test Render" (user, 2026-10-15; studio.test_renders): Splat This Clip Now
        into <Output Splatted>/Test Renders, nothing moved to finished/, not handed on."""
        self._start_clip(path, True)

    def _start_clip(self, path: str, test: bool) -> None:
        from studio.clip_queue import move_pending
        if self._busy or not path or move_pending(getattr(getattr(self, "preview", None), "queue", None), self.toast):
            return
        self._test_render = bool(test)
        self.preview.showClip(path)
        self._start_single(need_loaded=False)
        if not self._busy:                   # refused: no test pending
            self._test_render = False

    @Slot(str, result=str)
    def depthFolderFor(self, path: str) -> str:
        """Where this clip's depth map lives -- what DepthCrafter writes to when the
        clip is re-run there (user, 2026-10-10): the folder of the map listed with
        it, else Input Depth Maps (a file's own folder)."""
        depth = self.preview.queue.depthOf(path)
        if depth:
            return os.path.dirname(depth)
        folder = str(self._map.value("input_depth_maps") or "")
        return os.path.dirname(folder) if os.path.isfile(folder) else folder

    def _on_single_ready(self, info: Dict[str, Any]) -> None:
        self._busy = False
        if info.get("error") or not info.get("video") or not info.get("depth"):
            self._test_render = False
            self.stateChanged.emit()
            self._set_status("Ready")
            self.toast.emit("error", "Process Single Clip",
                            info.get("error") or "Could not get both video and depth map paths from previewer.")
            return
        values = S.normalise(self._values())
        prepared = self._prepare(values)
        if prepared is None:
            self.stateChanged.emit()
            return
        processor, settings = prepared
        video, depth = info["video"], info["depth"]
        settings.input_source_clips = video
        settings.input_depth_maps = depth
        # Move to Finished (settings.move_to_finished, from the page): left unset, the
        # batch moves into finished/ beside the clip and beside its map, as folder mode
        # does. v1 handed it <dir>/finished, and move_single_file adds its own
        # "finished", so the clip, map and sidecar landed in finished/finished/ where
        # Restore Finished never looks (bug sweep 2026-10-09).
        settings.single_finished_source_folder = None
        settings.single_finished_depth_folder = None
        if getattr(self, "_test_render", False):
            from studio import test_renders
            settings.output_splatted = test_renders.folder(settings.output_splatted)
            settings.move_to_finished = False
            logger.info("Test render: %s into %s", os.path.basename(video), settings.output_splatted)
        test = self._single_test
        if test:
            # v1: a one-frame diagnostic capture instead of an encode; the
            # preview keeps its clip (it IS half of the comparison).
            settings.is_test_mode = True
            settings.test_target_frame_idx = int(info.get("frame") or 0)
        self._set_status(f"Starting single-clip processing for: {os.path.basename(video)}")
        self._begin(processor, settings, values, *S.processing_range(values), release=not test)

    def _launch(self, processor, settings, from_idx, to_idx) -> None:
        if self._stop.is_set():     # Stop pressed while the preview was letting go
            self._stop.clear()
            self._busy = False
            self._status = "Processing stopped"
            self.stateChanged.emit()
            self.infoChanged.emit()
            self.preview.restore_after_job()
            return
        self._stop.clear()
        self._drain_stale()
        self._busy = True
        self._outcome = None
        self._stop_after.clear()
        self._clip_total = 0
        processor.stop_after_clip = self._stop_after
        self.stopAfterClipChanged.emit()
        self._done, self._total, self._pass_frac = 0, 0, 0.0
        self._clip_count = "N/A"
        self._reset_passes()
        self._info = {k: "N/A" for k in INFO_KEYS}
        self._status = "Starting processing..."
        self.stateChanged.emit()
        self.infoChanged.emit()

        def work():
            try:
                wait_for_gpu(self)          # the other stages have really let go (audit #14)
                processor.run_batch_process(settings=settings, from_index=from_idx, to_index=to_idx,
                                            video_list=None)
            except Exception as e:
                logger.exception("Splatting batch failed")
                self._queue.put(("status", f"Error: {e.__class__.__name__}. See console."))
                self._queue.put(("result", {"error": f"{e.__class__.__name__}: {e}"}))
                self._queue.put("finished")

        self._thread = threading.Thread(target=work, name="splatting", daemon=True)
        self._thread.start()
        self._poll.start()

    @Slot()
    def stop(self) -> None:
        """v1 stop_processing."""
        if self._busy:
            self._stop.set()
            self._set_status("Stopping...")

    def _set_status(self, text: str) -> None:
        self._status = text
        self.infoChanged.emit()

    def _drain_stale(self) -> None:
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass

    @Slot()
    def _drain(self) -> None:
        """v1 check_queue, one pass."""
        changed = False
        try:
            while True:
                message = self._queue.get_nowait()
                changed = True
                if message == "finished":
                    self._finish()
                    return
                kind = message[0] if isinstance(message, tuple) and message else None
                if kind == "total":
                    self._total, self._done, self._pass_frac = int(message[1]), 0, 0.0
                    self._status = f"Processing 0 of {self._total} tasks"
                elif kind == "tasks_done":
                    # the batch's own count: whole tasks (passes) finished
                    self._done, self._pass_frac = int(message[1]), 0.0
                    self._status = f"Processed tasks: {self._done}/{self._total} (overall)"
                elif kind == "processed":
                    # the renderer's: FRAMES of the pass under way -- it used
                    # to become the task count, and the bar ran to 1200%
                    # (audit 2026-09-28 #20). Now it moves the bar within the task.
                    frames = int(message[1])
                    if self._pass >= 0:
                        self._pass_frames[self._pass] = frames
                    try:
                        clip_frames = int(float(self._info.get("frames") or 0))
                    except (TypeError, ValueError):
                        clip_frames = 0
                    self._pass_frac = min(1.0, frames / clip_frames) if clip_frames > 0 else 0.0
                elif kind == "status":
                    if str(message[1]).startswith("Processing "):
                        self._pass_frac = 0.0          # a new pass starts from its first frame
                    self._note_pass(str(message[1]))
                    tail = str(message[1]).split(":", 1)[-1].strip()
                    self._status = f"Overall: {self._done}/{self._total} - {tail}"
                elif kind == "clip":
                    idx, total = message[1]
                    self._clip_total = int(total)
                    self._clip_count = f"{idx} / {total}" if total > 1 else "1 / 1"
                elif kind == "update_info":
                    if "filename" in (message[1] or {}):     # the batch moves to its next clip
                        self._reset_passes()
                    for key, value in (message[1] or {}).items():
                        if key in self._info:
                            self._info[key] = str(value)
                elif kind == "diagnostic_capture":
                    self.preview.diagnostic_capture(message[1])
                elif kind == "result":
                    self._outcome = Outcome.from_dict(message[1])
        except queue.Empty:
            pass
        if changed:
            self.infoChanged.emit()
        # v1's safety net: a worker that died without "finished" must not
        # leave the stage busy for good.
        if self._busy and (self._thread is None or not self._thread.is_alive()) and self._queue.empty():
            self._finish(died=True)

    def _clear_run_info(self) -> None:
        """The last run's passes and the preview's clip name go the moment the
        page turns busy -- not when the run itself starts, after the preview
        has let go. In between, Upcoming Jobs took the preview's clip as the
        running one (with the last run's passes done), filled its bars and
        marked it finished once the name cleared: re-splatting that clip never
        refilled them (user, 2026-09-28)."""
        self._reset_passes()
        self._info = {k: "N/A" for k in INFO_KEYS}

    def _reset_passes(self) -> None:
        self._pass, self._pass_frames, self._pass_done = -1, [0, 0], [False, False]

    def _note_pass(self, text: str) -> None:
        """BatchProcessor's "Processing <task> for <clip>": the pass before it is done."""
        for i, name in enumerate(("Full-Resolution", "Low-Resolution")):
            if text.startswith(f"Processing {name} for "):
                if 0 <= self._pass != i:
                    self._pass_done[self._pass] = True
                self._pass = i
                self._pass_frames[i] = 0

    def _finish(self, died: bool = False) -> None:
        self._poll.stop()
        stopped = self._stop.is_set()
        test, self._test_render = getattr(self, "_test_render", False), False
        if not (died or stopped):
            # the last clip's passes are done: said before busy drops, so the
            # list marks it done as the job ends
            self._pass_done = [True, True]
            self.infoChanged.emit()
        self._busy = False
        self._thread = None
        self._done, self._pass_frac = 0, 0.0
        halted = self._stop_after.is_set()
        self._stop_after.clear()
        self.stopAfterClipChanged.emit()
        if died:
            self._status = "Processing stopped unexpectedly. See console."
        elif stopped:
            self._status = "Processing stopped"
        else:
            self._status = "Processing finished"
        self.stateChanged.emit()
        self.infoChanged.emit()
        self.save()
        self.preview.restore_after_job()
        # v1 recorded the handoff on ANY finish, a stopped batch included; a
        # stopped batch's output is partial, so here only a completed one
        # is offered to Inpainting -- and only when it produced something
        # (audit #3: an empty folder or clips without depth maps toasted
        # green and were handed on).
        if died or stopped:
            self.toast.emit("warning", "Splatting stopped", self._status)
            return
        outcome = getattr(self, "_outcome", None) or Outcome(error="The batch ended without reporting its clips.")
        outcome.halted = outcome.halted and halted
        v = verdict("Splatting", outcome)
        if v.handoff and not test:               # a Test Render isn't handed on
            self._record_job_handoff()
        self.toast.emit(v.kind, ("Test render: " if test else "") + v.title,
                        v.text + ("\nIn the output folder's Test Renders folder." if test else ""))
        if v.kind == "success":
            logger.info("==> All process completed.")

    def shutdown(self) -> None:
        self._save_timer.stop()
        self.save()
        self.preview.shutdown()
        if self._thread is not None and self._thread.is_alive():
            logger.info("Stopping splatting before exit...")
            self._stop.set()
            self._thread.join(timeout=10)

    # ---- File menu: Fusion Export (v1 _run_fusion_generator) ---------------------

    @Slot(str, str, str)
    def runFusion(self, method: str, export_path: str, second: str) -> None:
        """core.splatting.FusionSidecarGenerator, off the UI thread as v1 runs
        it. QML has already asked for both paths (the .fsexport, then the depth
        folder or the save name): they go in as a filedialog that answers
        with them; its message boxes become toasts, its status line ours."""
        if method not in ("generate_sidecars", "generate_custom_sidecars"):
            return
        ctl = self

        class _Picked:
            def askopenfilename(self, **_k):
                return export_path

            def askdirectory(self, **_k):
                return second

            def asksaveasfilename(self, **_k):
                return second

            def showinfo(self, title="", message="", **_k):
                ctl.toast.emit("success", str(title), str(message))

            def showwarning(self, title="", message="", **_k):
                ctl.toast.emit("warning", str(title), str(message))

            def showerror(self, title="", message="", **_k):
                ctl.toast.emit("error", str(title), str(message))

        class _Label:
            def config(self, **kw):
                if "text" in kw:
                    ctl._sigStatus.emit(str(kw["text"]))

            configure = config

        class _Gui:
            status_label = _Label()

        self._set_status("Starting Fusion Export Sidecar Generation..." if method == "generate_sidecars"
                         else "Starting Custom Fusion Export Sidecar Generation...")

        def work():
            try:
                from core.common.sidecar_manager import SidecarConfigManager
                from core.splatting import FusionSidecarGenerator
                ui = _Picked()
                getattr(FusionSidecarGenerator(_Gui(), SidecarConfigManager()), method)(filedialog=ui, messagebox=ui)
            except Exception:
                logger.exception("Fusion sidecar generation failed")
                ctl.toast.emit("error", "Fusion Export", "Sidecar generation failed. See the console for details.")

        threading.Thread(target=work, name="fusion-export", daemon=True).start()

    # ---- Help menu ---------------------------------------------------------------

    @Property(str, constant=True)
    def aboutText(self) -> str:
        """Help > About for Splatting (v1's show_about read the Prototype's GUI_VERSION; it doesn't ship)."""
        return ("Splatting -- StereoCrafter Studio\n\n"
                "Makes the right-eye view of each source clip from its depth map.\n"
                "Built on PyTorch, Decord and OpenCV.")

    @Slot()
    def openOutputFolder(self) -> None:
        err = self._shell.openFolder(self._map.value("output_splatted"))
        if err:
            self.toast.emit("warning", "Open Output Folder", err)

    # ---- DepthCrafter / Depth Map Editor import, and handoff --------------------

    importDescription = Property(str, lambda self: self._import_desc, notify=importChanged)
    importOrigin = Property(str, lambda self: self._import_origin, notify=importChanged)

    @Slot()
    def refreshImport(self) -> None:
        self._import_desc = ""
        try:
            from core.ui.job_handoff import describe, get_source_record, in_project, previous_stage_label
            record = get_source_record("splatting")
            if record and (record.get("source") or record.get("depth")) \
                    and in_project(record, self._shell.projectFolder, ("depth", "source")):
                self._import_desc = describe(record) or "DepthCrafter job"
            self._import_origin = previous_stage_label("splatting") or "DepthCrafter"
        except Exception:
            logger.debug("Could not read the DepthCrafter handoff", exc_info=True)
        self.importChanged.emit()

    @Slot()
    def importFromPrevious(self) -> None:
        """v1 _import_from_previous_stage."""
        try:
            from core.ui.job_handoff import get_source_record, in_project, resolve_subfolder
            record = get_source_record("splatting") or {}
            if not in_project(record, self._shell.projectFolder, ("depth", "source")):
                record = {}
            fwd = lambda p: str(p).replace("\\", "/")  # noqa: E731 - match the Browse dialogs
            filled = []
            if record.get("source"):
                # DepthCrafter moves each finished clip into <source>/finished.
                self._set("input_source_clips", fwd(resolve_subfolder(record["source"], "finished")))
                filled.append("source clips")
            if record.get("depth"):
                self._set("input_depth_maps", fwd(record["depth"]))
                filled.append("depth maps")
            if filled:
                self._maybe_leave_chapter()
                self._save_timer.start()
                self._set_status("Imported " + " and ".join(filled) + f" from {self._import_origin}.")
        except Exception:
            logger.exception("Import from DepthCrafter failed")

    def _record_job_handoff(self) -> None:
        try:
            from core.ui.job_handoff import record_job
            record_job("splatting", S.job_fields(self._run_values or self._values()))
        except Exception:
            logger.debug("Could not record job handoff", exc_info=True)

    # ---- chapters ---------------------------------------------------------------

    chapters = Property("QVariantList", lambda self: self._chapters, notify=chapterChanged)
    chapterNum = Property(int, lambda self: self._chapter_num, notify=chapterChanged)
    hasProject = Property(bool, lambda self: bool(self._shell.projectFolder), notify=chapterChanged)

    _CHAPTER_FIELDS = (("source", "input_source_clips"), ("depth", "input_depth_maps"),
                       ("output", "output_splatted"))

    @Slot()
    def refreshChapters(self) -> None:
        self._chapters = self._shell.listChapters()
        saved = self._shell.stageChapter("splatting")
        nums = {c["num"] for c in self._chapters}
        self._chapter_num = saved.get("num", -1) if saved.get("num") in nums else -1
        if self._chapter_num >= 0:
            self._chapter_expected = self._shell.stagePaths("splatting", self._chapter_num, saved.get("name", ""))
        self.chapterChanged.emit()

    @Slot(int)
    def pickChapter(self, num: int) -> None:
        if num < 0:
            self._chapter_num = -1
            self._chapter_expected = {}
            self._shell.setStageChapter("splatting", {})
            self.chapterChanged.emit()
            return
        name = next((c["name"] for c in self._chapters if c["num"] == num), "")
        paths = self._shell.stagePaths("splatting", num, name)
        self._chapter_applying = True
        try:
            for role, key in self._CHAPTER_FIELDS:
                if paths.get(role):
                    self._set(key, paths[role].replace("\\", "/"))
        finally:
            self._chapter_applying = False
        self._chapter_num = num
        self._chapter_expected = paths
        self._shell.setStageChapter("splatting", {"num": num, "name": name})
        self._save_timer.start()
        self.chapterChanged.emit()
        if not getattr(self, "_restoring", False):        # a project switch restores quietly
            self.toast.emit("info", "Chapter selected",
                            f"{name}\nSource clips, depth maps and output now point at this chapter.")

    def _maybe_leave_chapter(self) -> None:
        if self._chapter_num < 0:
            return
        for role, key in self._CHAPTER_FIELDS:
            exp = self._chapter_expected.get(role)
            # a job moving the clips a finished level down is not a hand edit
            if exp and not chapter_paths.same_place(exp, self._map.value(key)):
                self._chapter_num = -1
                self._shell.setStageChapter("splatting", {})
                self.chapterChanged.emit()
                return
