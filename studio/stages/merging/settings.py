"""settings.py -- Merging settings: v1's config keys, v1's types.

Keys are ``config_merging.mergecfg``'s (v1 ``get_current_settings`` /
``save_config``), so seeding is a copy and a settings file saved by either
version loads in the other. Types follow v1's tk variables, because the
generated engine reads them through its ``*_var`` stand-ins and v1's own
``get_current_settings`` converts them exactly as it did (int(), float()).

No Qt, no Tk, no torch here.
"""

from __future__ import annotations

from typing import Any, Dict

from studio.default_folders import localize as _localize

#: v1 MergingGUI.__init__ / create_widgets fallbacks, by the var's type.
STRINGS: Dict[str, str] = {
    "inpainted_folder": "./completed_output",
    "original_folder": "./input_source_clips",
    "mask_folder": "./output_splatted/hires",
    "output_folder": "./final_videos",
    "output_format": "Full SBS (Left-Right)",
    "batch_chunk_size": "20",
    "preview_source": "Blended Image",
    "preview_size": "30%",
    "codec": "H.265",
    "encoding_encoder": "Auto",                  # enoky's GUIs' default (user, 2026-10-18)
    "encoding_quality": "Medium",
    "encoding_tune": "None",
    "bit_depth": "Auto",
    "pix_fmt": "Auto",
    "output_crf": "23",
    "color_tags": "Auto",
    "dnxhr_profile": "HQX (10-bit 4:2:2)",
    # Container (user, 2026-10-25): MP4 or MKV for a non-DNxHR output (DNxHR writes .mov)
    "output_container": "MP4",
    "grain_seed": "-1",
    # Merge All SBS to 1 Video (user, 2026-10-11; studio/merge_all.py): Settings > Encoding > Merge All.
    # The movie file's soundtrack goes in ("" = none); "" output folder = the project's (or episode's) folder.
    "mergeall_layout": "Full SBS",
    "mergeall_codec": "H.265",
    "mergeall_encoder": "Auto",
    "mergeall_quality": "Medium",
    "mergeall_crf": "18",
    "mergeall_bit_depth": "Auto",
    "mergeall_dnxhr_profile": "HQX (10-bit 4:2:2)",
    "mergeall_container": "MKV",
    "mergeall_audio_file": "",
    "mergeall_audio_codec": "Copy",
    "mergeall_output_folder": "",
}
FLOATS: Dict[str, float] = {
    "mask_binarize_threshold": 0.3,
    "mask_dilate_kernel_size": 3.0,
    "mask_blur_kernel_size": 5.0,
    "shadow_shift": 5.0,
    "shadow_decay_gamma": 1.3,
    "shadow_start_opacity": 0.87,
    "shadow_opacity_decay": 0.08,
    "shadow_min_opacity": 0.14,
    "grain_amount": 0.035,
    "grain_size": 1.2,
    "grain_luma_falloff": 0.6,
    "grain_chroma": 0.12,
    "fill_red_reduce": 0.0,
    "fill_brightness": 0.0,
    "fill_contrast": 1.0,
    "preview_brightness": 1.0,
}
INTS: Dict[str, int] = {
    "nvenc_lookahead": 16,
    "nvenc_aq_strength": 8,
}
BOOLS: Dict[str, bool] = {
    "use_gpu": False,
    "pad_to_16_9": False,
    "add_borders": True,
    "resume": False,
    "split_lr": False,
    "delete_sbs_after_split": False,
    "enable_color_transfer": True,
    "grain_enable": False,
    "grain_automatch": False,
    "nvenc_lookahead_enabled": False,
    "nvenc_spatial_aq": False,
    "nvenc_temporal_aq": False,
    "dnxhr_fullres_split": False,
    "debug_logging_enabled": False,
    "mask_highlight": False,
}

DEFAULTS: Dict[str, Any] = {**STRINGS, **FLOATS, **INTS, **BOOLS}
# the "./" folders under <Videos>/StereoCrafter Studio, not Studio's own folder (release check 2026-10-17;
# studio/default_folders.py)
DEFAULTS = _localize(DEFAULTS)

#: v1 builds these with a fixed value, whatever the config says: Add Borders
#: is on and Preview Scale opens at 30% every start; the mask highlight is a
#: view toggle that is never saved.
SESSION_START = {"add_borders": True, "preview_size": "30%", "mask_highlight": False}

#: Not settings v1 has a var for, kept and written back untouched.
PASSTHROUGH = ("dark_mode_enabled", "frosted_glass", "theme_name", "concat_outputs",
               "window_x", "window_y", "window_width", "window_height")

#: v1's Output Format list, in its order (create_widgets).
#: One choice for "Full SBS, split into _L / _R files, SBS deleted" (user,
#: 2026-09-27) -- v1 needed Full SBS + Split L/R + Delete SBS Videos for it.
#: The engine never sees it: for_engine() hands it those three instead.
SPLIT_LR_FORMAT = "Split L/R Output"

OUTPUT_FORMATS = [
    "Full SBS (Left-Right)", SPLIT_LR_FORMAT, "Double SBS", "Half SBS (Left-Right)", "Full SBS Cross-eye (Right-Left)",
    "Anaglyph (Red/Cyan)", "Anaglyph Half-Color", "Right-Eye Only",
]

#: v1's Mask Processing sliders: key -> (label, from, to, decimals, step), in order.
MASK_SLIDERS = [
    ("mask_binarize_threshold", "Binarize Thresh (<0=Off):", -0.01, 1.0, 2, 0.01),
    ("mask_dilate_kernel_size", "Dilate Kernel:", 0, 101, 0, 1),
    ("mask_blur_kernel_size", "Blur Kernel:", 0, 101, 0, 1),
    ("shadow_shift", "Shadow Shift:", 0, 50, 0, 1),
    ("shadow_decay_gamma", "Shadow Gamma:", 0.1, 5.0, 2, 0.01),
    ("shadow_start_opacity", "Shadow Opacity Start:", 0.0, 1.0, 2, 0.01),
    ("shadow_opacity_decay", "Shadow Opacity Decay:", 0.0, 1.0, 2, 0.01),
    ("shadow_min_opacity", "Shadow Opacity Min:", 0.0, 1.0, 2, 0.01),
]

#: v1 APP_DEFAULTS values for the keys File > Reset to Default touches (the
#: keys with a var), which is NOT the same set as DEFAULTS above -- v1's reset
#: leaves encoding alone and sets batch_chunk_size / preview_size as strings.
RESET_VALUES: Dict[str, Any] = {
    "inpainted_folder": "./completed_output", "original_folder": "./input_source_clips",
    "mask_folder": "./output_splatted/hires", "output_folder": "./final_videos",
    "mask_binarize_threshold": 0.3, "mask_dilate_kernel_size": 3.0, "mask_blur_kernel_size": 5.0,
    "shadow_shift": 5.0, "shadow_decay_gamma": 1.3, "shadow_start_opacity": 0.87,
    "shadow_opacity_decay": 0.08, "shadow_min_opacity": 0.14, "use_gpu": False,
    "output_format": "Full SBS (Left-Right)", "pad_to_16_9": False, "enable_color_transfer": True,
    "batch_chunk_size": "20", "preview_size": "30%", "grain_enable": False, "grain_automatch": False,
    "grain_amount": 0.035, "grain_size": 1.2, "grain_luma_falloff": 0.6, "grain_chroma": 0.12,
    "grain_seed": -1, "fill_red_reduce": 0.0, "fill_brightness": 0.0, "fill_contrast": 1.0,
}
RESET_VALUES = _localize(RESET_VALUES)


def coerce(key: str, value: Any) -> Any:
    """A value as v1's var for ``key`` would hold it."""
    try:
        if key in BOOLS:
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if key in FLOATS:
            return float(value)
        if key in INTS:
            return int(float(value))
        if key in STRINGS:
            # v1: StringVar(value=str(cfg.get(...))) -- a number becomes its text.
            return str(value)
    except (TypeError, ValueError):
        return DEFAULTS[key]
    return value


def normalise(values: Dict[str, Any]) -> Dict[str, Any]:
    out = {k: coerce(k, values.get(k, v)) for k, v in DEFAULTS.items()}
    for k in PASSTHROUGH:
        if k in values:
            out[k] = values[k]
    return out


def for_engine(values: Dict[str, Any]) -> Dict[str, Any]:
    """The settings as the (v1) engine reads them: Split L/R Output becomes
    Full SBS (Left-Right) with Split L/R and Delete SBS Videos on -- exactly
    what those three did together."""
    if values.get("output_format") != SPLIT_LR_FORMAT:
        return values
    out = dict(values)
    out.update({"output_format": "Full SBS (Left-Right)", "split_lr": True, "delete_sbs_after_split": True})
    return out


def settings_file_data(values: Dict[str, Any]) -> Dict[str, Any]:
    """Save Settings' file: current_settings of what the engine runs, so
    Split L/R Output -- a Studio choice -- is written as v1 spells it (Full
    SBS + Split L/R + Delete SBS) and v1 can load the file (audit
    2026-09-28)."""
    return current_settings(for_engine(values))


def from_settings_file(data: Dict[str, Any]) -> Dict[str, Any]:
    """A loaded settings file: that spelling reads back as Split L/R Output."""
    out = dict(data)
    if (out.get("output_format") == "Full SBS (Left-Right)" and out.get("split_lr")
            and out.get("delete_sbs_after_split")):
        out["output_format"] = SPLIT_LR_FORMAT
    return out


def seed_from_v1(v1: Dict[str, Any]) -> Dict[str, Any]:
    """config_merging.mergecfg -> v2's file. v1's config writes
    debug_logging_enabled; everything else is keyed as the vars are."""
    return normalise(v1 or {})


def at_session_start(values: Dict[str, Any]) -> Dict[str, Any]:
    """What v1's vars hold right after it starts (SESSION_START applied)."""
    out = dict(values)
    out.update(SESSION_START)
    return out


def current_settings(values: Dict[str, Any]) -> Dict[str, Any]:
    """v1 get_current_settings: the settings dict the batch and Save Settings
    use, converted exactly as v1 converts. Raises ValueError / TypeError where
    v1 does (a non-numeric Batch Chunk Size or CRF). Kept here so saving a
    settings file does not import torch; test_merging pins it to the engine's
    own generated copy of v1's method."""
    v = values
    try:
        grain_seed = int(float(v["grain_seed"]))
    except (ValueError, TypeError):
        grain_seed = -1
    return {
        "inpainted_folder": v["inpainted_folder"], "original_folder": v["original_folder"],
        "mask_folder": v["mask_folder"], "output_folder": v["output_folder"],
        "use_gpu": v["use_gpu"], "pad_to_16_9": v["pad_to_16_9"], "add_borders": v["add_borders"],
        "resume": v["resume"], "split_lr": v["split_lr"], "delete_sbs_after_split": v["delete_sbs_after_split"],
        "output_format": v["output_format"], "batch_chunk_size": max(1, int(v["batch_chunk_size"])),
        "enable_color_transfer": v["enable_color_transfer"], "preview_size": v["preview_size"],
        "preview_source": v["preview_source"], "preview_brightness": float(v["preview_brightness"] or 1.0),
        "codec": v["codec"], "encoding_encoder": v["encoding_encoder"], "encoding_quality": v["encoding_quality"],
        "encoding_tune": v["encoding_tune"], "bit_depth": v["bit_depth"], "pix_fmt": v["pix_fmt"],
        "output_crf": int(v["output_crf"]), "nvenc_lookahead_enabled": v["nvenc_lookahead_enabled"],
        "nvenc_lookahead": v["nvenc_lookahead"], "nvenc_spatial_aq": v["nvenc_spatial_aq"],
        "nvenc_temporal_aq": v["nvenc_temporal_aq"], "nvenc_aq_strength": v["nvenc_aq_strength"],
        "color_tags": v["color_tags"], "dnxhr_fullres_split": v["dnxhr_fullres_split"],
        "dnxhr_profile": v["dnxhr_profile"], "output_container": v.get("output_container", "MP4"),
        "mask_binarize_threshold": float(v["mask_binarize_threshold"]),
        "mask_dilate_kernel_size": int(v["mask_dilate_kernel_size"]),
        "mask_blur_kernel_size": int(v["mask_blur_kernel_size"]),
        "shadow_shift": int(v["shadow_shift"]),
        "shadow_start_opacity": float(v["shadow_start_opacity"]),
        "shadow_opacity_decay": float(v["shadow_opacity_decay"]),
        "shadow_min_opacity": float(v["shadow_min_opacity"]),
        "shadow_decay_gamma": float(v["shadow_decay_gamma"]),
        "grain_enable": v["grain_enable"], "grain_automatch": v["grain_automatch"],
        "grain_amount": float(v["grain_amount"]), "grain_size": float(v["grain_size"]),
        "grain_luma_falloff": float(v["grain_luma_falloff"]), "grain_chroma": float(v["grain_chroma"]),
        "grain_seed": grain_seed, "fill_red_reduce": float(v["fill_red_reduce"]),
        "fill_brightness": float(v["fill_brightness"]), "fill_contrast": float(v["fill_contrast"]),
    }
