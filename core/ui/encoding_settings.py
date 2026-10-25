import json
import logging
import os
import tkinter as tk
from tkinter import ttk
from typing import Optional, Dict, Any, Callable

logger = logging.getLogger(__name__)
from core.common.encoding_utils import (
    QUALITY_PRESETS,
    CPU_TUNE_OPTIONS,
    ENCODER_OPTIONS,
    CODEC_OPTIONS,
    BIT_DEPTH_OPTIONS,
    PIX_FMT_OPTIONS,
    DEFAULT_ENCODING_CONFIG,
    build_encoder_args,
    DNXHR_PROFILE_OPTIONS,
    DNXHR_PROFILE_8BIT,
    DNXHR_PROFILE_10BIT,
    default_dnxhr_profile,
)

COLOR_TAGS_OPTIONS = ["Off", "Auto", "BT.709 L", "BT.709 F", "BT.2020 PQ", "BT.2020 HLG"]

# --- Encoding presets (module level) -------------------------------------
# These live at module scope, not on the dialog, so the main GUIs can offer
# one-click preset buttons without constructing a dialog. There is exactly one
# copy of these values: edit them here and the dropdown, the quick-pick
# buttons, and the "does the current config match a preset" detection all move
# together. The dialog re-exports them as class attributes for back-compat.

PRESET_CUSTOM = "Custom"
# The codec used to be part of the name -- "8-bit (H.264)". These strings are
# the button labels, and the bit depth is the choice being made; the codec is
# a consequence of it and is shown in the dialog and the hover text. The long
# form also no longer fits now that the pickers are segmented buttons rather
# than a dropdown.
PRESET_8BIT = "8-bit"
PRESET_10BIT = "10-bit"

#: Old name -> new, for an overrides file written before the rename. The
#: names are the JSON keys in encoding_presets_custom.json, so without this a
#: hand-tuned preset would silently fall back to the built-in one.
_LEGACY_PRESET_NAMES = {
    "8-bit (H.264)": PRESET_8BIT,
    "10-bit (H.265 Main10)": PRESET_10BIT,
}

# Baked-in encoding presets matching the two common project types. Selecting
# one fills the fields below; every field remains freely editable afterward,
# and manual edits flip the dropdown to "Custom". These are convenience
# shortcuts, not locks. Values mirror the 8-bit / 10-bit reference setups.
ENCODING_PRESETS = {
    PRESET_8BIT: {
        "codec": "H.264",
        "encoding_encoder": "NVENC",
        "encoding_quality": "Slowest",
        "encoding_tune": "Auto",
        "bit_depth": "Auto",
        "pix_fmt": "yuv420p",
        "color_tags": "Auto",
        "nvenc_lookahead_enabled": True,
        "nvenc_lookahead": 16,
        "nvenc_spatial_aq": True,
        "nvenc_temporal_aq": True,
        "nvenc_aq_strength": 8,
        # DNxHR mezzanine for 8-bit too: the main output is a 4:2:2 DNxHR
        # .mov (SBS/grid, same layout as the 10-bit path), not a split.
        # 8-bit H.264 4:2:0 showed a clear compression loss between stages.
        "dnxhr_fullres_split": True,
        "dnxhr_profile": DNXHR_PROFILE_8BIT,
    },
    PRESET_10BIT: {
        "codec": "H.265",
        "encoding_encoder": "NVENC",
        "encoding_quality": "Slowest",
        "encoding_tune": "Auto",
        "bit_depth": "10-bit (Main10)",
        "pix_fmt": "p010le",
        "color_tags": "Auto",
        "nvenc_lookahead_enabled": True,
        "nvenc_lookahead": 16,
        "nvenc_spatial_aq": True,
        "nvenc_temporal_aq": True,
        "nvenc_aq_strength": 8,
        "dnxhr_fullres_split": True,
        "dnxhr_profile": DNXHR_PROFILE_10BIT,
    },
}

# Fields a preset owns, split by the coercion needed to compare them safely.
# CRF is deliberately absent: quality is a per-project choice independent of
# the 8/10-bit setup, so presets neither set nor compare it.
_PRESET_STR_KEYS = (
    "codec",
    "encoding_encoder",
    "encoding_quality",
    "encoding_tune",
    "bit_depth",
    "pix_fmt",
    "color_tags",
    "dnxhr_profile",
)
_PRESET_BOOL_KEYS = (
    "nvenc_lookahead_enabled",
    "nvenc_spatial_aq",
    "nvenc_temporal_aq",
    "dnxhr_fullres_split",
)
_PRESET_INT_KEYS = ("nvenc_lookahead", "nvenc_aq_strength")

PRESET_FIELD_KEYS = _PRESET_STR_KEYS + _PRESET_BOOL_KEYS + _PRESET_INT_KEYS

# Tooltip text for the 8-bit / 10-bit toggles in the Splatting and Merging
# toolbars. Defined here, not in either GUI, so the two stages cannot drift
# apart -- both already import from this module.
#
# The guidance is source-driven: the right choice depends on what the input
# footage actually carries, not on the output you want. Encoding 8-bit source
# as 10-bit gains nothing but costs size and speed; encoding a 10-bit/HDR
# source as 8-bit throws away precision and bands the gradients.
# What a preset does, as Studio's Encoding Settings has it (user, 2026-10-25: the old tail sent you to
# the Prototype's File > Save as ... Preset, which Studio doesn't have). Said once, after both presets'
# own lines, where a tooltip shows them together (Merging).
PRESET_TOOLTIP_TAIL = (
    "A preset sets every encoding setting except Output CRF. Change any of them in Encoding Settings "
    "and the preset reads Custom: neither button is lit."
)
_PRESET_TOOLTIP_TAIL = "\n\n" + PRESET_TOOLTIP_TAIL

ENCODING_PRESET_TOOLTIPS = {
    PRESET_8BIT: (
        "8-bit encoding preset.\n\n"
        "For 8-bit SDR sources:\n"
        "  \u2022 standard 1080p Blu-ray rips\n"
        "  \u2022 SDR web rips / streaming captures"
        + _PRESET_TOOLTIP_TAIL
    ),
    PRESET_10BIT: (
        "10-bit encoding preset (H.265 Main10).\n\n"
        "For sources with more than 8-bit precision:\n"
        "  \u2022 4K Blu-ray (UHD) rips\n"
        "  \u2022 4K HDR web files\n"
        "  \u2022 1080p SDR you tone-mapped from Dolby Vision yourself\n"
        "    (e.g. rendered out of DaVinci Resolve first)"
        + _PRESET_TOOLTIP_TAIL
    ),
}

# Said once under both presets (user, 2026-10-25: "adds no quality" for 8-bit sources and "tone mapping
# leaves gradients 8-bit would band" read as contradicting each other; and 10-bit isn't slower)
PRESET_TOOLTIP_ADVICE = (
    "If you want to be sure no new banding is introduced, render at 10-bit.\n\n"
    "For visually lossless output to edit in DaVinci Resolve, turn on Enable DNxHR in Encoding "
    "Settings. DNxHR files are very large -- many times the size of H.264 / H.265 -- so check the "
    "free space first."
)


def preset_tooltip() -> str:
    """The 8-bit / 10-bit buttons' tooltip (Splatting's and Merging's pages): each preset's own lines,
    then the advice and what a preset does -- once each."""
    own = [ENCODING_PRESET_TOOLTIPS[n].replace(_PRESET_TOOLTIP_TAIL, "") for n in (PRESET_8BIT, PRESET_10BIT)]
    return "\n\n".join(own + [PRESET_TOOLTIP_ADVICE, PRESET_TOOLTIP_TAIL])

# Anchored to the repo root (two levels up from core/ui/), so a saved override
# is found regardless of which stage or working directory launched it -- the
# 8-bit/10-bit presets are a shared, cross-stage concept, and a save made from
# Splatting should be the same preset Merging sees.
_PRESET_OVERRIDES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "encoding_presets_custom.json",
)


def _load_preset_overrides() -> None:
    """Apply a saved customization over the built-in presets, if one exists.

    Mutates ENCODING_PRESETS in place rather than reassigning it, since several
    places (EncodingSettingsDialog.ENCODING_PRESETS, detect_encoding_preset's
    module-global lookup) hold a reference to this exact dict object and would
    not see a new one.
    """
    try:
        with open(_PRESET_OVERRIDES_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
    except FileNotFoundError:
        return
    except Exception:
        logger.warning("Could not read %s; using built-in presets.", _PRESET_OVERRIDES_PATH, exc_info=True)
        return
    if not isinstance(saved, dict):
        return
    # A file written before the rename is keyed by the old long names.
    for old_name, new_name in _LEGACY_PRESET_NAMES.items():
        if old_name in saved and new_name not in saved:
            saved[new_name] = saved[old_name]

    for name in (PRESET_8BIT, PRESET_10BIT):
        override = saved.get(name)
        if not isinstance(override, dict):
            continue
        # Only known fields, so a hand-edited or stale file can't inject an
        # arbitrary key into the config that later gets handed to ffmpeg.
        ENCODING_PRESETS[name].update({k: v for k, v in override.items() if k in PRESET_FIELD_KEYS})


# Apply any saved customization now that both ENCODING_PRESETS and the loader
# function above exist. Anything after this point that reads ENCODING_PRESETS
# -- the dialog, the toolbar buttons, detect_encoding_preset -- sees the
# user's saved version if there is one, with no separate "did the override
# load" step for callers to remember.
_load_preset_overrides()


def save_preset(name: str, config: Dict[str, Any]) -> bool:
    """Overwrite the named built-in preset with `config` and persist it to disk.

    `config` may carry extra keys (it is typically a full dialog snapshot);
    only PRESET_FIELD_KEYS are read; anything else is ignored.

    Returns True on success. A write failure is logged but never raised --
    the in-memory preset is already updated by the time the write is
    attempted, so the running session sees the change either way, and it is
    only the next launch that would miss it.
    """
    if name not in (PRESET_8BIT, PRESET_10BIT):
        return False
    ENCODING_PRESETS[name].update({k: config[k] for k in PRESET_FIELD_KEYS if k in config})
    try:
        # Write the full pair, not just the changed one, so a hand-tuned 8-bit
        # preset and a hand-tuned 10-bit preset both survive across saves.
        payload = {n: {k: ENCODING_PRESETS[n][k] for k in PRESET_FIELD_KEYS} for n in (PRESET_8BIT, PRESET_10BIT)}
        tmp = _PRESET_OVERRIDES_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp, _PRESET_OVERRIDES_PATH)
        return True
    except Exception:
        logger.exception("Could not save preset override to %s", _PRESET_OVERRIDES_PATH)
        return False


def preset_bit_depth_conflicts(name: str, config: Dict[str, Any]) -> list:
    """Fields in `config` that contradict the bit depth `name` implies.

    Saving 8-bit-looking settings into the 10-bit preset (or vice versa) is
    almost always a slip, and it is silent and destructive: the preset is
    overwritten on disk for every stage and project, and the old values are
    gone. This reports the specific contradictions so the warning can name
    them rather than being a vague "are you sure".

    Advisory only -- the caller offers "save anyway", because a deliberate
    non-standard setup is legitimate (e.g. a 10-bit container fed 8-bit data
    on purpose).

    Returns a list of human-readable strings; empty means no conflict.
    "Auto" values are never a conflict: they resolve at encode time and carry
    no bit-depth claim of their own.
    """
    if name not in (PRESET_8BIT, PRESET_10BIT):
        return []
    want_10 = name == PRESET_10BIT
    conflicts = []

    bit_depth = str(config.get("bit_depth", "Auto"))
    if want_10 and bit_depth == "8-bit":
        conflicts.append("Bit Depth is '8-bit'")
    elif not want_10 and bit_depth == "10-bit (Main10)":
        conflicts.append("Bit Depth is '10-bit (Main10)'")

    pix_fmt = str(config.get("pix_fmt", "Auto"))
    if want_10 and pix_fmt == "yuv420p":
        conflicts.append("Pixel Format is 'yuv420p' (8-bit)")
    elif not want_10 and pix_fmt in ("yuv420p10le", "p010le"):
        conflicts.append(f"Pixel Format is '{pix_fmt}' (10-bit)")

    # Profile strings are self-describing ("HQ (8-bit 4:2:2)"), so a substring
    # test stays correct if the option list grows.
    profile = str(config.get("dnxhr_profile", ""))
    if want_10 and "8-bit" in profile:
        conflicts.append(f"DNxHR Profile is '{profile}'")
    elif not want_10 and "10-bit" in profile:
        conflicts.append(f"DNxHR Profile is '{profile}'")

    # H.264 cannot carry 10-bit in this pipeline -- build_encoder_args promotes
    # it to H.265 -- so pairing it with the 10-bit preset is misleading.
    if want_10 and str(config.get("codec", "")) == "H.264":
        conflicts.append("Codec is 'H.264', which cannot encode 10-bit (it is promoted to H.265)")

    return conflicts


def get_encoding_preset(name: str) -> Optional[Dict[str, Any]]:
    """Return a *copy* of the named preset, or None for Custom/unknown names.

    A copy, so a caller mutating the result can't corrupt the shared table.
    """
    preset = ENCODING_PRESETS.get(name)
    return dict(preset) if preset else None


def preset_matches_config(config: Dict[str, Any], preset: Dict[str, Any]) -> bool:
    """True if every preset-owned field in `config` equals the preset's value.

    Values are coerced per key group before comparing, because callers hand us
    a mix of sources: Tk vars (IntVar gives int, StringVar gives str), a JSON
    config (booleans may arrive as 0/1), and dialog results. Comparing raw
    would report a spurious "Custom" for a config that is in fact the preset.
    """
    try:
        for key in _PRESET_STR_KEYS:
            if str(config.get(key, "")) != str(preset[key]):
                return False
        for key in _PRESET_BOOL_KEYS:
            if bool(config.get(key, False)) != bool(preset[key]):
                return False
        for key in _PRESET_INT_KEYS:
            if int(config.get(key, -1)) != int(preset[key]):
                return False
        return True
    except (TypeError, ValueError, KeyError):
        # A malformed/partial config is simply "not this preset", never a crash.
        return False


def detect_encoding_preset(config: Dict[str, Any]) -> str:
    """Return the preset name whose fields match `config`, else PRESET_CUSTOM."""
    for name, preset in ENCODING_PRESETS.items():
        if preset_matches_config(config, preset):
            return name
    return PRESET_CUSTOM


def _readable_on(hex_color: str) -> str:
    """Black or white, whichever stays readable on *hex_color*.

    The selected-state background is the theme accent now, and those range from
    near-white (Graphite) to mid orange (Paper), so a fixed text colour is
    unreadable on one end or the other. Uses the sRGB luminance coefficients.
    """
    try:
        h = hex_color.lstrip("#")
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        return "#000000" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#ffffff"
    except Exception:
        return "#ffffff"


def _faded_on(text_hex: str, surface_hex: str, amount: float = 0.45) -> str:
    """*text_hex* mixed part-way toward *surface_hex*, for disabled captions.

    Local rather than imported for the same reason _readable_on above is: this
    module is used standalone by every stage and keeps its own colour maths.
    ThemeManager._blend does the same thing for the plain button styles.
    """
    _named = {"white": "#ffffff", "black": "#000000"}
    text_hex = _named.get(str(text_hex).strip().lower(), text_hex)
    surface_hex = _named.get(str(surface_hex).strip().lower(), surface_hex)
    try:
        ht, hs = text_hex.lstrip("#"), surface_hex.lstrip("#")
        rt, gt, bt = (int(ht[i:i + 2], 16) for i in (0, 2, 4))
        rs, gs, bs = (int(hs[i:i + 2], 16) for i in (0, 2, 4))
        t = max(0.0, min(1.0, amount))
        r, g, b = (int(round(c + (s - c) * t)) for c, s in ((rt, rs), (gt, gs), (bt, bs)))
        return f"#{r:02x}{g:02x}{b:02x}"
    except Exception:
        return text_hex


def preset_toolbutton_kwargs(colors: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The palette-driven arguments for configure_preset_toolbutton_styles.

    One place that knows which palette keys the preset buttons read, so every
    GUI styles them identically -- they share one ttk style name per process,
    so two callers passing different arguments would fight over it.
    """
    colors = colors or {}
    return {
        "accent": colors.get("panel_bar"),
        "panel_bg": colors.get("panel_bg"),
        # Palettes opt out of the outline with "preset_toolbutton_outline": False.
        "show_outline": bool(colors.get("preset_toolbutton_outline", True)),
    }


def configure_preset_toolbutton_styles(
    style: ttk.Style, dark_mode: bool, accent: Optional[str] = None, panel_bg: Optional[str] = None,
    show_outline: bool = True,
) -> None:
    """Define the EncPreset.Toolbutton style used by the quick-pick buttons.

    ttk's Toolbutton shape, but with an explicit selected background instead
    of relying on the ttk theme's default sunken look, which is too subtle to
    read at a glance on a dark UI.

    Call from each GUI's _apply_theme so the accent survives a theme switch.
    """
    if accent:
        # Selected 8-bit/10-bit button takes the palette accent, so it reads as
        # part of the theme rather than a fixed blue that clashes with eight of
        # the nine palettes.
        sel_bg, sel_fg = accent, _readable_on(accent)
    elif dark_mode:
        sel_bg, sel_fg = "#2f6fd0", "#ffffff"
    else:
        sel_bg, sel_fg = "#bcd6f7", "#0b2f5e"

    # Unselected fill: previously never set at all, so it fell back to
    # whatever the base ttk theme ("black" or "clam") defaults a Toolbutton
    # to -- a muted, theme-unrelated grey/violet on some palettes, not the
    # app's own surface colour. panel_bg is the same fill every plain TButton
    # already uses, so an unselected 8-bit/10-bit button now reads as "a
    # normal button that happens to be part of a toggle group" instead of a
    # separate, oddly-coloured control.
    base_bg = panel_bg if panel_bg else ("#2a2a2a" if dark_mode else "#e8e8e8")
    base_fg = _readable_on(base_bg)

    # Outline in the same accent as the selected fill: previously no border at
    # all was set, so both buttons were flat rectangles and only the selected
    # one's fill colour showed anything -- confirmed by checking element
    # options before adding this: ttk's Toolbutton.border element supports
    # bordercolor/relief/borderwidth exactly like a regular button's border,
    # so this is not a guess. relief="solid" (a flat single-colour line), not
    # the two-tone raised bevel real buttons get elsewhere -- these are a
    # segmented toggle, not a push-button, and a bevel would fight the flat
    # selected/unselected fill look already established here.
    outline = accent if accent else ("#666666" if dark_mode else "#999999")
    if show_outline:
        border_on, border_off = outline, outline
    else:
        # No visible outline, but the 1px border stays and is painted the
        # button's own fill in each state. Dropping borderwidth to 0 instead
        # would shrink both buttons by 2px and shift the row whenever the
        # palette changes.
        border_on, border_off = sel_bg, base_bg
    try:
        style.configure(
            "EncPreset.Toolbutton",
            padding=(6, 1),
            background=base_bg,
            foreground=base_fg,
            bordercolor=border_off,
            relief="solid",
            borderwidth=1,
        )
        # "disabled" first in each list: these buttons are disabled during a
        # render while one of them is still selected, so both states apply and
        # whichever is listed first wins. Faded rather than blanked -- the base
        # theme's own disabled colour is close enough to the fill on several
        # palettes that "8-bit"/"10-bit" disappeared mid-render.
        disabled_sel_fg = _faded_on(sel_fg, sel_bg)
        disabled_base_fg = _faded_on(base_fg, base_bg)
        style.map(
            "EncPreset.Toolbutton",
            background=[("selected", sel_bg), ("active !selected", sel_bg)],
            foreground=[
                ("disabled selected", disabled_sel_fg),
                ("disabled", disabled_base_fg),
                ("selected", sel_fg),
                ("active !selected", sel_fg),
            ],
            bordercolor=[("selected", border_on), ("active !selected", border_on), ("!selected", border_off)],
        )
    except tk.TclError:
        pass


class EncodingSettingsDialog:
    """Reusable encoding settings dialog for StereoCrafter GUIs.

    This dialog provides a comprehensive UI for configuring video encoding
    options including encoder, quality preset, tune, CRF, NVENC-specific
    options, and optionally DNxHR options and dual CRF fields.
    """

    def __init__(
        self,
        parent: tk.Tk,
        app_config: Optional[Dict[str, Any]] = None,
        help_data: Optional[Dict[str, str]] = None,
        title: str = "Encoding Settings",
        show_extra_options: bool = True,
        show_dual_crf: bool = False,
        show_color_tags: bool = True,
    ):
        """Initialize the encoding settings dialog.

        Args:
            parent: Parent tkinter window
            app_config: Optional existing config dict to populate values from
            help_data: Optional dict of help text strings
            title: Dialog window title
            show_extra_options: If True, show DNxHR options (for splatting).
            show_dual_crf: If True, show Full Res CRF and Low Res CRF fields (for splatting).
                          If False, show single CRF field.
            show_color_tags: If True, show Color Tags dropdown.
        """
        self.parent = parent
        self.app_config = app_config or {}
        self.help_data = help_data or {}
        self.title = title
        self.show_extra_options = show_extra_options
        self.show_dual_crf = show_dual_crf
        self.show_color_tags = show_color_tags

        self.result = None
        self._setup_variables()
        self._create_dialog()

    # Re-exported from module scope so existing `EncodingSettingsDialog.
    # ENCODING_PRESETS` references keep working. The definitions themselves now
    # live at the top of this module, shared with the GUIs' quick-pick buttons.
    ENCODING_PRESETS = ENCODING_PRESETS
    PRESET_CUSTOM = PRESET_CUSTOM

    def _setup_variables(self):
        """Initialize tkinter variables from app config."""
        config = self.app_config

        self.codec_var = tk.StringVar(value=config.get("codec", "H.265"))
        self.encoder_var = tk.StringVar(value=config.get("encoding_encoder", "Auto"))
        self.quality_var = tk.StringVar(value=config.get("encoding_quality", DEFAULT_ENCODING_CONFIG["quality"]))
        self.tune_var = tk.StringVar(value=config.get("encoding_tune", DEFAULT_ENCODING_CONFIG["tune"]))
        self.bit_depth_var = tk.StringVar(value=config.get("bit_depth", DEFAULT_ENCODING_CONFIG["bit_depth"]))
        self.pix_fmt_var = tk.StringVar(value=config.get("pix_fmt", DEFAULT_ENCODING_CONFIG["pix_fmt"]))

        if self.show_dual_crf:
            self.crf_full_var = tk.StringVar(
                value=str(config.get("output_crf_full", config.get("output_crf", DEFAULT_ENCODING_CONFIG["crf"])))
            )
            self.crf_low_var = tk.StringVar(
                value=str(config.get("output_crf_low", config.get("output_crf", DEFAULT_ENCODING_CONFIG["crf"])))
            )
        else:
            self.crf_var = tk.StringVar(
                value=str(config.get("output_crf", config.get("crf", DEFAULT_ENCODING_CONFIG["crf"])))
            )

        self.nvenc_lookahead_enabled_var = tk.BooleanVar(
            value=config.get("nvenc_lookahead_enabled", DEFAULT_ENCODING_CONFIG["nvenc_lookahead_enabled"])
        )
        self.nvenc_lookahead_var = tk.IntVar(
            value=config.get("nvenc_lookahead", DEFAULT_ENCODING_CONFIG["nvenc_lookahead"])
        )
        self.nvenc_spatial_aq_var = tk.BooleanVar(
            value=config.get("nvenc_spatial_aq", DEFAULT_ENCODING_CONFIG["nvenc_spatial_aq"])
        )
        self.nvenc_temporal_aq_var = tk.BooleanVar(
            value=config.get("nvenc_temporal_aq", DEFAULT_ENCODING_CONFIG["nvenc_temporal_aq"])
        )
        self.nvenc_aq_strength_var = tk.IntVar(
            value=config.get("nvenc_aq_strength", DEFAULT_ENCODING_CONFIG["nvenc_aq_strength"])
        )

        self.dnxhr_fullres_split_var = tk.BooleanVar(value=config.get("dnxhr_fullres_split", False))
        self.dnxhr_profile_var = tk.StringVar(
            value=config.get("dnxhr_profile", default_dnxhr_profile(self.bit_depth_var.get()))
        )

        self.color_tags_var = tk.StringVar(value=config.get("color_tags", config.get("color_tags_mode", "Auto")))

        # Preset selector. Starts on whatever matches current config, else Custom.
        self.preset_var = tk.StringVar(value=self._detect_matching_preset())

    def _current_preset_config(self) -> Dict[str, Any]:
        """Snapshot the preset-owned fields from the live dialog variables."""
        try:
            return {
                "codec": self.codec_var.get(),
                "encoding_encoder": self.encoder_var.get(),
                "encoding_quality": self.quality_var.get(),
                "encoding_tune": self.tune_var.get(),
                "bit_depth": self.bit_depth_var.get(),
                "pix_fmt": self.pix_fmt_var.get(),
                "color_tags": self.color_tags_var.get(),
                "nvenc_lookahead_enabled": self.nvenc_lookahead_enabled_var.get(),
                "nvenc_lookahead": self.nvenc_lookahead_var.get(),
                "nvenc_spatial_aq": self.nvenc_spatial_aq_var.get(),
                "nvenc_temporal_aq": self.nvenc_temporal_aq_var.get(),
                "nvenc_aq_strength": self.nvenc_aq_strength_var.get(),
                "dnxhr_fullres_split": self.dnxhr_fullres_split_var.get(),
                "dnxhr_profile": self.dnxhr_profile_var.get(),
            }
        except tk.TclError:
            # A partially-torn-down dialog reads as "not a preset".
            return {}

    def _preset_matches(self, preset: Dict[str, Any]) -> bool:
        """True if every field in `preset` equals the current variable values."""
        return preset_matches_config(self._current_preset_config(), preset)

    def _detect_matching_preset(self) -> str:
        """Return the preset name whose fields match current config, else Custom."""
        return detect_encoding_preset(self._current_preset_config())

    def _save_current_as_preset(self, name: str):
        """Snapshot every field below into the named preset and persist it.

        Overwrites a shared, cross-stage preset on disk, so this asks first --
        the built-in defaults are gone once replaced, and the only way back is
        typing the old values in by hand.
        """
        from tkinter import messagebox

        current = self._current_preset_config()

        # Bit-depth sanity check first: saving 8-bit-looking settings into the
        # 10-bit preset (or the reverse) is nearly always a slip, and the
        # overwrite is unrecoverable. Advisory, not a block -- a deliberate
        # non-standard setup is allowed, so this folds into the same confirm
        # rather than refusing outright.
        conflicts = preset_bit_depth_conflicts(name, current)

        overwrite_msg = (
            f"This replaces the '{name}' preset with the settings currently shown "
            f"in this dialog.\n\nIt applies everywhere -- Splatting, Merging, and any "
            f"future project -- and the previous '{name}' values cannot be recovered "
            f"once overwritten.\n\nSave?"
        )

        if conflicts:
            bullet_list = "\n".join(f"  \u2022 {c}" for c in conflicts)
            confirm_msg = (
                f"These settings do not look like a {name} setup:\n\n"
                f"{bullet_list}\n\n"
                f"Saving them as '{name}' means that preset will no longer produce "
                f"the bit depth its name implies.\n\n"
                f"{overwrite_msg}"
            )
            if not messagebox.askyesno(
                f"Bit depth mismatch - Save as {name}?", confirm_msg, default="no"
            ):
                return
        else:
            if not messagebox.askyesno(f"Save as {name}", overwrite_msg):
                return

        if save_preset(name, current):
            self.preset_var.set(name)
        else:
            messagebox.showerror(
                "Save Failed",
                f"Could not write the preset to disk. The '{name}' preset was still "
                f"updated for this session, but the change will not survive a restart.",
            )
            self.preset_var.set(name)

    def _attach_custom_watchers(self):
        """After any manual field change, flip the preset dropdown to Custom so it
        never misrepresents hand-tuned settings as a named preset."""
        def _mark_custom(*_):
            if getattr(self, "_applying_preset", False):
                return
            # Only downgrade to Custom; don't fight a value that still matches.
            if self._detect_matching_preset() == self.PRESET_CUSTOM:
                self.preset_var.set(self.PRESET_CUSTOM)

        for var in (
            self.codec_var, self.encoder_var, self.quality_var, self.tune_var,
            self.bit_depth_var, self.pix_fmt_var, self.color_tags_var,
            self.nvenc_lookahead_enabled_var, self.nvenc_lookahead_var,
            self.nvenc_spatial_aq_var, self.nvenc_temporal_aq_var,
            self.nvenc_aq_strength_var, self.dnxhr_fullres_split_var,
            self.dnxhr_profile_var,
        ):
            var.trace_add("write", _mark_custom)

    # -- widgets ------------------------------------------------------------
    #
    # Every stage that opens this dialog (Splatting, Inpainting, Merging) is a
    # CustomTkinter stage, so the dialog is built from THAT stage's factories:
    # the same dropdowns, fields, checkboxes and section cards as the page
    # behind it, painted in its palette and scaled for the monitor. A caller
    # that is not a CTk stage still gets the plain ttk dialog.

    def _ui(self):
        return self.parent if hasattr(self.parent, "_ctk_popup") else None

    def _mk_frame(self, parent):
        ui = self._ui()
        return ui._ctk_container(parent) if ui else ttk.Frame(parent)

    def _mk_label(self, parent, text=None, textvariable=None):
        ui = self._ui()
        kw = {"textvariable": textvariable} if textvariable is not None else {"text": text}
        return ui._ctk_label(parent, anchor="w", **kw) if ui else ttk.Label(parent, **kw)

    def _mk_combo(self, parent, var, values, width=18):
        ui = self._ui()
        if ui:
            return ui._ctk_option(parent, variable=var, values=[str(v) for v in values])
        return ttk.Combobox(parent, textvariable=var, values=values, state="readonly", width=width)

    def _mk_entry(self, parent, var, width=10):
        ui = self._ui()
        if ui:
            return ui._ctk_entry(parent, textvariable=var, width=width * 7)
        return ttk.Entry(parent, textvariable=var, width=width)

    def _mk_spin(self, parent, var, from_, to):
        ui = self._ui()
        if ui:
            return ui._ctk_spin(parent, from_=from_, to=to, textvariable=var, width=52, compact=True)
        return ttk.Spinbox(parent, from_=from_, to=to, increment=1, textvariable=var, width=8)

    def _mk_check(self, parent, var, text):
        ui = self._ui()
        if ui:
            return ui._ctk_check(parent, text=text, variable=var)
        return ttk.Checkbutton(parent, text=text, variable=var)

    def _mk_group(self, parent, title):
        """(outer, body): a section card, or a ttk.LabelFrame for both."""
        ui = self._ui()
        if ui:
            return ui._section(parent, title)
        frame = ttk.LabelFrame(parent, text=title, padding=10)
        return frame, frame

    def _mk_button(self, parent, text, command, primary=False):
        ui = self._ui()
        if ui:
            return ui._ctk_button(parent, text=text, command=command, primary=primary)
        return ttk.Button(parent, text=text, command=command)

    @staticmethod
    def _set_state(widget, state: str) -> None:
        """ttk's "readonly" is CTk's "normal"; either raises on some widgets."""
        is_ctk = type(widget).__module__.startswith("customtkinter") or hasattr(widget, "_apply_widget_scaling")
        try:
            widget.configure(state="normal" if (is_ctk and state == "readonly") else state)
        except Exception:
            pass

    def _create_dialog(self):
        """Create the dialog window and widgets."""
        self.dialog = tk.Toplevel(self.parent)
        self.dialog.title(self.title)
        self.dialog.transient(self.parent)
        self.dialog.grab_set()
        self.dialog.resizable(False, False)

        outer = self._mk_frame(self.dialog)
        outer.pack(fill="both", expand=True, padx=15, pady=15)

        row = 0

        # A menubar rather than a row of buttons: saving a preset is a
        # file-level action on the dialog, and the buttons ate a full row of
        # vertical space above the settings they act on.
        #
        # "Use Custom" is deliberately gone. _attach_custom_watchers already
        # flips preset_var to Custom on ANY manual field change, so an explicit
        # button was redundant -- touching a setting IS declaring it custom.
        menubar = tk.Menu(self.dialog)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(
            label="Save as 8-bit Preset",
            command=lambda: self._save_current_as_preset(PRESET_8BIT),
        )
        file_menu.add_command(
            label="Save as 10-bit Preset",
            command=lambda: self._save_current_as_preset(PRESET_10BIT),
        )
        menubar.add_cascade(label="File", menu=file_menu)
        try:
            self.dialog.config(menu=menubar)
            # Kept referenced so the menu isn't garbage-collected, and so the
            # theme bus can restyle it like the other menus.
            self.menubar = menubar
            self.file_menu = file_menu
        except Exception:
            logger.debug("Could not attach encoding dialog menubar", exc_info=True)

        # Own row, not sharing a row with anything else.
        self.preset_status_label = self._mk_label(outer, text="Active preset:")
        self.preset_status_label.grid(row=row, column=0, sticky="e", padx=(0, 8), pady=(0, 10))
        self.preset_status_value = self._mk_label(outer, textvariable=self.preset_var)
        self.preset_status_value.grid(row=row, column=1, sticky="w", pady=(0, 10))
        row += 1

        # (label, variable, values, help key) for every dropdown row.
        combos = [
            ("Codec:", self.codec_var, CODEC_OPTIONS, "encoding_codec"),
            ("Encoder:", self.encoder_var, ENCODER_OPTIONS, "encoding_encoder"),
            ("Quality Preset:", self.quality_var, QUALITY_PRESETS, "encoding_quality"),
            ("CPU Tune:", self.tune_var, CPU_TUNE_OPTIONS, "encoding_tune"),
            ("Bit Depth:", self.bit_depth_var, BIT_DEPTH_OPTIONS, "bit_depth"),
            ("Pixel Format:", self.pix_fmt_var, PIX_FMT_OPTIONS, "pix_fmt"),
        ]
        for text, var, values, key in combos:
            lbl = self._mk_label(outer, text=text)
            lbl.grid(row=row, column=0, sticky="e", padx=(0, 8), pady=4)
            self._add_tooltip(lbl, key)
            self._mk_combo(outer, var, values).grid(row=row, column=1, sticky="w", pady=4)
            row += 1

        crf_rows = ([("Full Res CRF:", self.crf_full_var), ("Low Res CRF:", self.crf_low_var)]
                    if self.show_dual_crf else [("CRF (quality):", self.crf_var)])
        for text, var in crf_rows:
            lbl = self._mk_label(outer, text=text)
            lbl.grid(row=row, column=0, sticky="e", padx=(0, 8), pady=4)
            self._add_tooltip(lbl, "output_crf")
            self._mk_entry(outer, var).grid(row=row, column=1, sticky="w", pady=4)
            row += 1

        if self.show_color_tags:
            lbl_color_tags = self._mk_label(outer, text="Color Tags:")
            lbl_color_tags.grid(row=row, column=0, sticky="e", padx=(0, 8), pady=4)
            self._add_tooltip(lbl_color_tags, "color_tags_mode")
            self._mk_combo(outer, self.color_tags_var, COLOR_TAGS_OPTIONS).grid(
                row=row, column=1, sticky="w", pady=4)
            row += 1

        self._create_nvenc_frame(outer, row)

        extra_row = row + 1

        if self.show_extra_options:
            self._create_dnxhr_frame(outer, extra_row)
            extra_row += 1
        else:
            self._applying_preset = False
            self._attach_custom_watchers()

        self._create_buttons(outer, extra_row)

        ui = self._ui()
        if ui is not None:
            # Painted, put on glass and sized to its contents for the monitor.
            ui._ctk_popup(self.dialog)
        self.dialog.update_idletasks()

        # Just inside the parent's top-left corner, kept on screen.
        x = max(0, self.parent.winfo_rootx() + 20)
        y = max(0, self.parent.winfo_rooty() + 10)
        if ui is not None:
            self.dialog.geometry(f"+{x}+{y}")
        else:
            width = self.dialog.winfo_reqwidth()
            height = self.dialog.winfo_reqheight()
            self.dialog.geometry(f"{width}x{height}+{x}+{y}")
            # Frosted glass for the ttk dialog, on the same terms as the stage
            # that opened it. Last, because it keys what is already built.
            try:
                from core.ui.ctk_theme import glass_dialog

                glass_dialog(self.dialog)
            except Exception:
                logger.debug("Could not put the encoding dialog on glass", exc_info=True)

    def _create_nvenc_frame(self, parent, row: int):
        """Create the NVENC options group."""
        nv_outer, nv_frame = self._mk_group(parent, "NVENC Options (only when NVENC is used)")
        nv_outer.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        nv_frame.grid_columnconfigure(1, weight=1)

        nv_row = 0
        chk_la = self._mk_check(nv_frame, self.nvenc_lookahead_enabled_var, "Enable Lookahead")
        chk_la.grid(row=nv_row, column=0, columnspan=2, sticky="w", pady=2)
        self._add_tooltip(chk_la, "encoding_nvenc_lookahead_enabled")

        nv_row += 1
        lbl_laf = self._mk_label(nv_frame, text="Lookahead Frames:")
        lbl_laf.grid(row=nv_row, column=0, sticky="e", padx=(0, 8), pady=2)
        self._add_tooltip(lbl_laf, "encoding_nvenc_lookahead")
        sp_la = self._mk_spin(nv_frame, self.nvenc_lookahead_var, 0, 64)
        sp_la.grid(row=nv_row, column=1, sticky="w", pady=2)

        nv_row += 1
        chk_saq = self._mk_check(nv_frame, self.nvenc_spatial_aq_var, "Spatial AQ")
        chk_saq.grid(row=nv_row, column=0, columnspan=2, sticky="w", pady=2)
        self._add_tooltip(chk_saq, "encoding_nvenc_spatial_aq")

        nv_row += 1
        chk_taq = self._mk_check(nv_frame, self.nvenc_temporal_aq_var, "Temporal AQ")
        chk_taq.grid(row=nv_row, column=0, columnspan=2, sticky="w", pady=2)
        self._add_tooltip(chk_taq, "encoding_nvenc_temporal_aq")

        nv_row += 1
        lbl_aq = self._mk_label(nv_frame, text="AQ Strength:")
        lbl_aq.grid(row=nv_row, column=0, sticky="e", padx=(0, 8), pady=2)
        self._add_tooltip(lbl_aq, "encoding_nvenc_aq_strength")
        sp_aq = self._mk_spin(nv_frame, self.nvenc_aq_strength_var, 1, 15)
        sp_aq.grid(row=nv_row, column=1, sticky="w", pady=2)

        self.nvenc_frame = nv_frame
        self.nvenc_lookahead_spinbox = sp_la
        self.nvenc_aq_spinbox = sp_aq

        self.encoder_var.trace_add("write", self._on_encoder_changed)
        self._update_nvenc_state()

    def _create_dnxhr_frame(self, parent, row: int):
        """Create the DNxHR options group (only shown when show_extra_options=True)."""
        dnx_outer, dnx_frame = self._mk_group(parent, "DNxHR Split (Full-Res Dual mode only)")
        dnx_outer.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        dnx_frame.grid_columnconfigure(1, weight=1)

        dnx_row = 0
        chk_dnx = self._mk_check(dnx_frame, self.dnxhr_fullres_split_var, "Enable DNxHR Split")
        chk_dnx.grid(row=dnx_row, column=0, columnspan=2, sticky="w", pady=2)
        self._add_tooltip(chk_dnx, "dnxhr_fullres_split_tooltip")

        dnx_row += 1
        lbl_prof = self._mk_label(dnx_frame, text="DNxHR Profile:")
        lbl_prof.grid(row=dnx_row, column=0, sticky="e", padx=(0, 8), pady=2)
        self._add_tooltip(lbl_prof, "dnxhr_profile")
        cb_prof = self._mk_combo(dnx_frame, self.dnxhr_profile_var, DNXHR_PROFILE_OPTIONS, width=20)
        cb_prof.grid(row=dnx_row, column=1, sticky="w", pady=2)

        self.dnxhr_frame = dnx_frame
        self.dnxhr_profile_combo = cb_prof

        self.dnxhr_fullres_split_var.trace_add("write", self._on_dnxhr_changed)
        self._update_dnxhr_state()

        # Watch all fields so manual edits flip the Preset dropdown to Custom.
        # Attached last so it doesn't fire during initial widget construction.
        self._applying_preset = False
        self._attach_custom_watchers()

    def _create_buttons(self, parent, row: int):
        """Create OK/Cancel buttons."""
        btn_row = self._mk_frame(parent)
        btn_row.grid(row=row, column=0, columnspan=2, sticky="e", pady=(15, 0))

        self._mk_button(btn_row, "Cancel", self._on_cancel).pack(side="right", padx=(5, 0))
        self._mk_button(btn_row, "OK", self._on_ok, primary=True).pack(side="right")

    def _add_tooltip(self, widget: tk.Widget, key: str):
        """Add tooltip to widget if help text exists."""
        if key in self.help_data:
            from core.ui.widgets import Tooltip

            Tooltip(widget, self.help_data[key])

    def _on_encoder_changed(self, *args):
        """Update NVENC frame state based on encoder selection."""
        self._update_nvenc_state()

    def _on_dnxhr_changed(self, *args):
        """Update DNxHR frame state based on checkbox."""
        self._update_dnxhr_state()

    def _update_nvenc_state(self):
        """Enable/disable NVENC options based on encoder."""
        state = "normal" if self.encoder_var.get() == "Auto" else "disabled"
        self._set_state(self.nvenc_frame, state)
        for child in self.nvenc_frame.winfo_children():
            self._set_state(child, state)

    def _update_dnxhr_state(self):
        """Enable/disable DNxHR profile based on checkbox."""
        state = "readonly" if self.dnxhr_fullres_split_var.get() else "disabled"
        self._set_state(self.dnxhr_profile_combo, state)

    def _on_ok(self):
        """Handle OK button click."""
        try:
            if self.show_dual_crf:
                crf_full = int(self.crf_full_var.get())
                crf_low = int(self.crf_low_var.get())
                if crf_full < 0 or crf_full > 51 or crf_low < 0 or crf_low > 51:
                    raise ValueError("CRF must be between 0 and 51")
            else:
                crf = int(self.crf_var.get())
                if crf < 0 or crf > 51:
                    raise ValueError("CRF must be between 0 and 51")
        except ValueError as e:
            from tkinter import messagebox

            messagebox.showerror("Invalid CRF", f"CRF must be a number between 0 and 51.\n\nError: {e}")
            return

        self.result = self.get_settings()
        self.dialog.destroy()

    def _on_cancel(self):
        """Handle Cancel button click."""
        self.result = None
        self.dialog.destroy()

    def get_settings(self) -> Dict[str, Any]:
        """Get current settings as a dictionary.

        Returns:
            Dict with all encoding settings
        """
        settings = {
            "codec": self.codec_var.get(),
            "encoding_encoder": self.encoder_var.get(),
            "encoding_quality": self.quality_var.get(),
            "encoding_tune": self.tune_var.get(),
            "bit_depth": self.bit_depth_var.get(),
            "pix_fmt": self.pix_fmt_var.get(),
            "nvenc_lookahead_enabled": self.nvenc_lookahead_enabled_var.get(),
            "nvenc_lookahead": self.nvenc_lookahead_var.get(),
            "nvenc_spatial_aq": self.nvenc_spatial_aq_var.get(),
            "nvenc_temporal_aq": self.nvenc_temporal_aq_var.get(),
            "nvenc_aq_strength": self.nvenc_aq_strength_var.get(),
        }

        if self.show_dual_crf:
            settings["output_crf_full"] = int(self.crf_full_var.get())
            settings["output_crf_low"] = int(self.crf_low_var.get())
        else:
            settings["output_crf"] = int(self.crf_var.get())

        if self.show_extra_options:
            settings["dnxhr_fullres_split"] = self.dnxhr_fullres_split_var.get()
            settings["dnxhr_profile"] = self.dnxhr_profile_var.get()

        if self.show_color_tags:
            settings["color_tags"] = self.color_tags_var.get()
            settings["color_tags_mode"] = self.color_tags_var.get()

        return settings

    def get_encoding_args(self, force_10bit: bool = False, crf: Optional[int] = None) -> Dict[str, Any]:
        """Get FFmpeg encoding arguments.

        Args:
            force_10bit: Whether to force 10-bit output
            crf: Optional override for CRF value

        Returns:
            Dict with codec, preset, tune, crf, pix_fmt, extra_args
        """
        if crf is None:
            if self.show_dual_crf:
                crf = int(self.crf_full_var.get())
            else:
                crf = int(self.crf_var.get())

        return build_encoder_args(
            codec=self.codec_var.get(),
            encoder=self.encoder_var.get(),
            quality=self.quality_var.get(),
            tune=self.tune_var.get(),
            crf=crf,
            force_10bit=force_10bit,
            bit_depth=self.bit_depth_var.get(),
            pix_fmt_override=self.pix_fmt_var.get(),
            nvenc_options={
                "lookahead_enabled": self.nvenc_lookahead_enabled_var.get(),
                "lookahead": self.nvenc_lookahead_var.get(),
                "spatial_aq": self.nvenc_spatial_aq_var.get(),
                "temporal_aq": self.nvenc_temporal_aq_var.get(),
                "aq_strength": self.nvenc_aq_strength_var.get(),
            },
        )


def create_encoding_dialog(
    parent: tk.Tk,
    app_config: Optional[Dict[str, Any]] = None,
    help_data: Optional[Dict[str, str]] = None,
    title: str = "Encoding Settings",
    show_extra_options: bool = True,
    show_dual_crf: bool = False,
    show_color_tags: bool = True,
    callback: Optional[Callable[[Optional[Dict[str, Any]]], None]] = None,
) -> Optional[EncodingSettingsDialog]:
    """Create and show an encoding settings dialog.

    Args:
        parent: Parent tkinter window
        app_config: Optional existing config dict
        help_data: Optional help text dict
        title: Dialog title
        show_extra_options: If True, show DNxHR options (for splatting).
        show_dual_crf: If True, show Full Res CRF and Low Res CRF fields.
        show_color_tags: If True, show Color Tags dropdown.
        callback: Optional callback function to call with result

    Returns:
        The dialog instance (result available via dialog.get_settings())
    """
    dialog = EncodingSettingsDialog(
        parent,
        app_config,
        help_data,
        title,
        show_extra_options=show_extra_options,
        show_dual_crf=show_dual_crf,
        show_color_tags=show_color_tags,
    )

    if callback:
        parent.wait_window(dialog.dialog)
        callback(dialog.result)

    return dialog
