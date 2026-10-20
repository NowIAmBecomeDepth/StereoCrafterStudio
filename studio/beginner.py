"""beginner.py -- Studio > Use Beginner Mode, for v2.

v1's Beginner Mode (unified_gui/beginner_mode.py) hides the advanced settings
in every stage, gives a few of the rest plainer names, pins a handful of
options to what a beginner should run with, and shows plain-language hover
text. Turning it off puts everything back. This is the same mode with v1's
rule keys, in v2's terms:

* hiding and renaming: the pages bind to ``Beginner.hide[<rule key>]`` and
  ``Beginner.names[<rule key>]`` (a rule key is v1's, e.g. "dc.guidance_scale")
  -- QML hides the element in place, nothing is destroyed, and a hidden
  setting keeps its value;
* pinning: ``FORCE`` sets a stage setting while the mode is on and keeps the
  value it replaced (in config/studio.json, as v1 keeps it in
  config_studio.json), restored when the mode goes off, even in a later
  session; re-pinned whenever a stage is shown (v1 re-pins on activation,
  which also undoes a Load Settings behind a hidden box);
* Merging: Auto-match is the only grain switch left, so it is mirrored into
  the master switch (v1 _apply_merging_grain);
* hover text: ``tip(text)`` swaps in dependency/beginner_help.json's text
  for a help text of the same stage and key, or a ``_matches`` entry;
* refused while any stage is running (v1: forcing a value mid-render could
  change what the job has not read yet).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Property, QObject, Signal, Slot

from .legacy import REPO

logger = logging.getLogger("studio.beginner")

BEGINNER_HELP = REPO / "dependency" / "beginner_help.json"

#: v1's STAGE_RULES that hide something, by rule key.
HIDE = {
    # DepthCrafter
    "dc.guidance_scale", "dc.stabilize_depth", "dc.percentile_norm", "dc.process_max_frames",
    "dc.target_fps",  # user, 2026-10-11 (pinned to -1 below)
    "dc.secondary_output", "dc.gen_seg_visuals", "dc.remerge", "dc.save_final_json",
    "dc.output_crf",
    # Splatting
    "splat.full_res", "splat.low_res", "splat.full_res_batch", "splat.low_res_batch", "splat.dual_output",
    "splat.ffmpeg", "splat.multi_map", "splat.auto_converge_preview", "splat.output_settings", "splat.dev_tools",
    "splat.vwf_hole_shift", "splat.border_width_bias", "splat.global_norm",
    # (Crosshair, White and Multi stay: a beginner sets Convergence by them too -- user, 2026-10-11)
    "splat.flip",
    # Particles: tuned per scene, too much for a first conversion (user, 2026-10-08)
    "splat.particles",
    # Inpainting (and its V2 sub-tab)
    "inp.process_length", "inp.pre_thresh", "inp.post_thresh", "inp.morph_close",
    "inp.dilate_kernel", "inp.blur_kernel", "inp.keep_cache", "inp.color_transfer",
    "inp.guidance_scale",
    "inp.v2_tab",
    # Merging
    "merge.shadow_shift", "merge.shadow_gamma", "merge.shadow_start", "merge.shadow_decay", "merge.shadow_min",
    "merge.highlight_mask", "merge.color_transfer", "merge.move_to_finish", "merge.grain_enable",
    "merge.grain_preview",
}

#: v1's renames: rule key -> the beginner label.
NAMES = {
    "splat.edge_alignment": "Edge Alignment",
    "splat.depth_strength": "Depth Strength:",
    "splat.expand_left": "Expand Left:",
    "splat.expand_x": "Expand X:",
}

#: v1's force_check / force_var: rule key -> (stage, setting key, value).
FORCE: Dict[str, Tuple[str, str, Any]] = {
    # not dc.latent_checkpoint: Save Checkpoint shows in the mode now, so it is the
    # user's to set (user, 2026-10-11) -- a pin would undo their click
    "dc.save_final_json": ("depthcrafter", "save_final_output_json_var", False),
    # hidden: anything but the clip's own rate makes depth that misses the clip's frames
    "dc.target_fps": ("depthcrafter", "target_fps", -1.0),
    "dc.output_preset": ("depthcrafter", "output_preset_var", "veryslow"),
    "dc.output_crf": ("depthcrafter", "output_crf_var", "auto"),
    "splat.full_res": ("splatting", "enable_full_res", True),
    "splat.low_res": ("splatting", "enable_low_res", True),
    "splat.dual_output": ("splatting", "dual_output", True),
    "splat.ffmpeg": ("splatting", "strict_ffmpeg_decode", True),
    "inp.color_transfer": ("inpainting", "enable_color_transfer", True),
}

#: v1 GRAIN_SAVED_KEY: the master grain switch, from before the mode took it over.
GRAIN_SAVED_KEY = "merge.grain_master"


def _apply_text_match(match: dict, text: str) -> Optional[str]:
    """v1 _apply_text_match: beginner text for a tooltip not in a help file."""
    result = None
    if "starts_with" in match:
        if text.startswith(match["starts_with"]):
            result = match["text"]
    elif "regex" in match:
        found = re.match(match["regex"], text, re.DOTALL)
        if found:
            result = match["text"]
            for name, value in found.groupdict().items():
                result = result.replace("{" + name + "}", value or "")
    if result is None:
        return None
    marker = match.get("keep_from")
    if marker:
        index = text.find(marker)
        if index >= 0:
            result = f"{result}\n\n{text[index:]}"
    return result


def _write(ctl, key: str, value: Any) -> None:
    """A programmatic settings write, as each controller makes its own."""
    if hasattr(ctl, "_set"):
        ctl._set(key, value)
        return
    ctl._map.insert(key, value)
    last = getattr(ctl, "_last_good", None)
    if isinstance(last, dict):
        last[key] = value


def _busy(ctl) -> bool:
    try:
        if hasattr(ctl, "is_busy"):
            return bool(ctl.is_busy())
        return bool(ctl.busy)
    except Exception:
        return False


class Beginner(QObject):
    changed = Signal()
    #: A switch refused (v1's warning): the page shows it.
    refused = Signal(str, str)

    def __init__(self, shell, controllers: Dict[str, Any], parent=None):
        super().__init__(parent)
        self._shell = shell
        self._ctl = controllers            # stage id -> controller (settings map + _set)
        data = shell._data
        self._on = bool(data.get("beginner_mode", False))
        self._saved: Dict[str, Any] = dict(data.get("beginner_mode_saved") or {})
        self._help: Dict[str, Dict[str, str]] = {}
        self._matches: List[dict] = []
        self._text_keys: Dict[str, List[Tuple[str, str]]] = {}
        self._load_help()
        merging = controllers.get("merging")
        if merging is not None:
            merging._map.valueChanged.connect(self._on_merging_value)
        if self._on:
            self._apply(True)

    # ---- QML -------------------------------------------------------------------

    on = Property(bool, lambda self: self._on, notify=changed)
    hide = Property("QVariantMap", lambda self: {k: self._on for k in HIDE}, notify=changed)
    names = Property("QVariantMap", lambda self: dict(NAMES) if self._on else {}, notify=changed)

    @Slot(bool, result=bool)
    def setEnabled(self, on: bool) -> bool:
        on = bool(on)
        if on == self._on:
            return True
        busy = [n for n, c in self._ctl.items() if _busy(c)]
        if busy:
            self.refused.emit("Beginner Mode", "A stage is still processing. Beginner Mode can be switched "
                                               "once it has finished.")
            self.changed.emit()             # the menu tick goes back
            return False
        self._on = on
        self._apply(on)
        self._persist()
        self.changed.emit()
        return True

    @Slot()
    def reapply(self) -> None:
        """v1 apply_stage on every activation: re-pin what a Load Settings or
        a sidecar may have changed behind a hidden box. studio.json is written
        only when the mode's own record changed (audit 2026-09-28, PERF: this
        runs on every stage switch)."""
        if self._on:
            before = dict(self._saved)
            self._apply(True)
            if self._saved != before:
                self._persist()

    @Slot(str, result=str)
    def tip(self, text: str) -> str:
        """v1 resolve_tooltip_text."""
        if not text or not self._on:
            return text
        for stage, key in self._text_keys.get(text, ()):
            replacement = self._help.get(stage, {}).get(key)
            if replacement:
                return replacement
        for match in self._matches:
            replacement = _apply_text_match(match, text)
            if replacement:
                return replacement
        return text

    # ---- the rules ------------------------------------------------------------------

    def _put(self, ctl, key: str, value: Any) -> None:
        """_write, noting the stage as changed (it is saved)."""
        _write(ctl, key, value)
        self._touched.add(id(ctl))

    def _apply(self, on: bool) -> None:
        self._touched = set()
        for rule, (stage, key, value) in FORCE.items():
            ctl = self._ctl.get(stage)
            if ctl is None:
                continue
            m = ctl._map
            if on:
                if rule not in self._saved:
                    self._saved[rule] = m.value(key)
                if m.value(key) != value:
                    self._put(ctl, key, value)
            elif rule in self._saved:
                self._put(ctl, key, self._saved.pop(rule))
        self._apply_merging_grain(on)
        # Only the stages a value was written to (audit 2026-09-28, PERF:
        # every stage's settings file was rewritten, on the UI thread, on
        # every stage switch).
        for ctl in self._ctl.values():
            if id(ctl) not in self._touched:
                continue
            save = getattr(ctl, "save", None)
            if callable(save):
                try:
                    save()
                except Exception:
                    logger.debug("Could not save after Beginner Mode", exc_info=True)

    def _apply_merging_grain(self, on: bool) -> None:
        """v1 _apply_merging_grain: Auto-match becomes the grain switch."""
        ctl = self._ctl.get("merging")
        if ctl is None:
            return
        m = ctl._map
        if on:
            if GRAIN_SAVED_KEY not in self._saved:
                self._saved[GRAIN_SAVED_KEY] = bool(m.value("grain_enable"))
            self._sync_grain()
        elif GRAIN_SAVED_KEY in self._saved:
            self._put(ctl, "grain_enable", bool(self._saved.pop(GRAIN_SAVED_KEY)))

    def _sync_grain(self) -> None:
        ctl = self._ctl.get("merging")
        if ctl is None or not self._on:
            return
        want = bool(ctl._map.value("grain_automatch"))
        if bool(ctl._map.value("grain_enable")) != want:
            _write(ctl, "grain_enable", want)
            getattr(self, "_touched", set()).add(id(ctl))

    def _on_merging_value(self, key: str, _value) -> None:
        if key in ("grain_automatch", "grain_enable"):
            self._sync_grain()

    def _persist(self) -> None:
        self._shell._data["beginner_mode"] = self._on
        self._shell._data["beginner_mode_saved"] = dict(self._saved)
        self._shell.save()

    # ---- hover text (v1 _load_beginner_help / _index_help_texts) ------------------

    def _load_help(self) -> None:
        try:
            with open(BEGINNER_HELP, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            logger.warning("Beginner mode: %s not readable; hover text stays technical", BEGINNER_HELP)
            return
        for stage, entries in data.items():
            if stage.startswith("_") or not isinstance(entries, dict):
                continue
            self._help[stage] = {k: v for k, v in entries.items() if isinstance(v, str) and v}
        self._matches = [m for m in data.get("_matches", []) if isinstance(m, dict) and m.get("text")]
        for stage, ctl in self._ctl.items():
            try:
                texts = ctl.help
            except Exception:
                continue
            for key, text in dict(texts or {}).items():
                if isinstance(text, str) and text:
                    owners = self._text_keys.setdefault(text, [])
                    if (stage, key) not in owners:
                        owners.append((stage, key))
