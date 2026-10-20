"""settings.py -- Splatting settings: defaults, types, v1 remaps, run snapshot.

v1 (splatting_gui.py) keeps every setting in a tk variable and persists the
lot: its config is a harvest of every ``<name>_var`` under ``<name>``
(config_manager.get_current_config), plus ~20 keys it remaps by hand in
_get_current_config. So the keys here are v1's VARIABLE names and the types
are the variables' types -- StringVar values stay strings (v1 parses them
where it needs a number), BooleanVar bools, IntVar ints, DoubleVar floats.
A settings file saved by either version then loads in the other.

Three translations are v1's own and live here, pure:

* :func:`seed_from_v1` -- the load remaps (convergence_point -> the anchor,
  depth_boost_* -> the Pop Out Boost sliders, ...), from SplatterGUI.__init__.
* :func:`config_for_run` -- _get_current_config's harvest + remaps: the dict
  ``ProcessingSettings.from_config`` is built from.
* :func:`processing_overrides` / :func:`processing_range` -- the GUI-only
  values _get_processing_settings adds, and the From/To clip range.

No Qt, no Tk, no torch here.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

from studio.default_folders import localize as _localize

try:  # the render's own defaults; v1 starts its harvest from this dict
    from core.splatting.config_manager import SPLATTER_DEFAULT_CONFIG
except Exception:  # pragma: no cover - only without the repo on sys.path
    SPLATTER_DEFAULT_CONFIG = {}

#: v1 SplatterGUI.__init__'s variables and their initial values
#: (APP_CONFIG_DEFAULTS where a var reads one). Keyed by variable name
#: without ``_var``. Shell-owned v1 vars (theme, dark mode, glass) and the
#: read-only "Current Clip Info" display vars are not settings here.
DEFAULTS: Dict[str, Any] = {
    # folders
    "input_source_clips": "./input_source_clips",
    "input_depth_maps": "./input_depth_maps",
    "output_splatted": "./output_splatted",
    "multi_map": False,
    "selected_depth_map": "",
    # processing
    "max_disp": "30.0",
    "zero_disparity_anchor": "0.5",
    "process_length": "-1",
    "process_from": "",
    "process_to": "",
    "custom_clip_range": False,
    "batch_size": "10",
    "low_res_batch_size": "15",
    "pre_res_width": "1920",
    "pre_res_height": "1080",
    # Auto Size for the Loaded Clip (user, 2026-10-11): Low Res Width/Height follow
    # the loaded clip's shape; in a batch each clip of another shape runs at its own
    "low_res_auto_size": False,
    "enable_full_res": True,
    "enable_low_res": True,             # user, 2026-10-18: Inpainting V1 reads the low-res splats
    "dual_output": True,                # user, 2026-10-18
    "dual_eye": False,
    "dual_eye_bias": "0.5",
    "enable_global_norm": False,
    "move_to_finished": True,
    "skip_lowres_preproc": False,
    "strict_ffmpeg_decode": True,       # user, 2026-10-18: the ffmpeg box
    "flip_horizontal": False,
    "auto_convergence_mode": "Off",
    # output / encoding
    "output_crf": "23",
    "output_crf_full": "23",
    "output_crf_low": "23",
    "color_tags_mode": "Auto",
    "codec": "H.265",
    "encoder": "Auto",                  # enoky's GUIs' default (user, 2026-10-18); NVENC when there is one
    "encoding_quality": "Medium",
    "encoding_tune": "None",
    "bit_depth": "Auto",
    "pix_fmt": "Auto",
    "encoding_nvenc_lookahead_enabled": False,
    "encoding_nvenc_lookahead": 16,
    "encoding_nvenc_spatial_aq": False,
    "encoding_nvenc_temporal_aq": False,
    "encoding_nvenc_aq_strength": 8,
    "dnxhr_fullres_split": False,
    "dnxhr_profile": "HQX (10-bit 4:2:2)",
    "encoding_preset": "Custom",
    # depth pre-processing
    "depth_gamma": "1.0",
    "depth_dilate_size_x": "3.5",       # user, 2026-10-18
    "depth_dilate_size_y": "0",         # user, 2026-10-18
    "depth_blur_size_x": "5",
    "depth_blur_size_y": "5",
    "depth_dilate_left": "0",
    "depth_blur_left": "0",
    "depth_blur_left_mix": "0.5",
    "enable_sidecar_gamma": True,
    "enable_sidecar_blur_dilate": True,
    "update_slider_from_sidecar": True,
    "auto_save_sidecar": False,
    # borders
    "border_width": "0.0",
    "border_bias": "0.0",
    "border_mode": "Off",
    "auto_border_L": "0.0",
    "auto_border_R": "0.0",
    # warp / masks
    "mask_mode": "SC",
    "splat_backend": "SC",
    "hybrid_mask_sensitivity": 50.0,
    "hybrid_mask_taper": False,
    "m2s_fix_strength": 1.0,
    "dilreg_enabled": True,
    # Special FX: Pop Out Boost
    "boost_thresh": 0.85,
    "boost_amount": 0.0,
    "boost_curve": 2.0,
    "boost_knee": 0.0,
    "boost_prefilter_radius": 0,
    "boost_prefilter_eps": 0.001,
    "pop_amount": 0.0,
    # the Detect Text mask's own grow / soften, source px (user, 2026-10-02)
    "submask_grow": 0.0,
    "submask_soften": 0.0,
    # Text Clean: DepthCrafter's copy of the text taken out of the depth, px
    "submask_clean": 0.0,
    # Text Conv (user, 2026-10-11): the text's own Convergence, an offset from the scene's
    # (+ sets it back behind the screen, - brings it out); 0 = on the screen, as before
    "submask_conv": 0.0,
    # Special FX: Rain (user, 2026-10-06; core.splatting.rain): off | moving | static
    "rain_mode": "off",
    "rain_any_direction": False,
    "rain_depth": 0.5,
    "rain_amount": 1.0,
    "rain_layers": 3,
    "rain_strands": True,
    "rain_lights": True,
    "rain_pop": 0.0,
    "rain_preset": "downpour",
    "rain_guard": 0.0,
    "rain_particle": "rain",
    "rain_placement": "front",
    "rain_grow": 0,
    "rain_reflections": False,
    "rain_spread": 1.0,
    "rain_spacing": 1.0,
    "rain_conv": 0.0,
    "rain_method": "moving",     # the Particles method kept while the switch is off (page only)
    # Special FX: Frame Break
    "frame_break_bars": 0.0,
    "frame_break_thresh": 1.0,
    "fb_preset": "Custom",
    "fb_erode_left": 0.0,
    "fb_erode_right": 0.0,
    "fb_erode_global": 0.0,
    "fb_edge_blur": 0.0,
    # preview (view only)
    "preview_source": "Dubois Anaglyph",
    "preview_size": "75%",
    "preview_brightness": 1.0,
    "crosshair_enabled": False,
    "crosshair_white": False,
    "crosshair_multi": False,
    "depth_pop_enabled": False,
    "mask_highlight": False,
    "loop_playback": False,
    "mesh_warp_view_bias": "0.0",
    "mesh_warp_dolly_zoom": "0.0",
    "mesh_warp_extrusion_scale": "0.5",
    "mesh_warp_preview_density": "0.5",
    # panels / dev
    "dev_tools_shown": False,
    "output_settings_shown": True,
    "debug_logging": False,
    "debug_mode": False,
}
# the "./" folders under <Videos>/StereoCrafter Studio, not Studio's own folder (release check 2026-10-17;
# studio/default_folders.py)
DEFAULTS = _localize(DEFAULTS)

#: StringVars holding numbers: v1 only discovered a bad one at Start.
NUMERIC = {"max_disp", "zero_disparity_anchor", "process_length", "batch_size", "low_res_batch_size",
           "pre_res_width", "pre_res_height", "dual_eye_bias", "output_crf", "output_crf_full",
           "output_crf_low", "depth_gamma", "depth_dilate_size_x", "depth_dilate_size_y", "depth_blur_size_x",
           "depth_blur_size_y", "depth_dilate_left", "depth_blur_left", "depth_blur_left_mix", "border_width",
           "border_bias", "auto_border_L", "auto_border_R", "mesh_warp_view_bias",
           "mesh_warp_dolly_zoom", "mesh_warp_extrusion_scale", "mesh_warp_preview_density"}

#: ...of which these are whole numbers (the render's int fields): "2.5" is
#: refused as it is typed (audit 2026-09-28, minor).
WHOLE = {"batch_size", "low_res_batch_size", "pre_res_width", "pre_res_height", "process_length",
         "output_crf", "output_crf_full", "output_crf_low"}

FOLDER_KEYS = ("input_source_clips", "input_depth_maps", "output_splatted")

#: What Reset to Defaults puts back besides v1's SPLATTER_DEFAULT_CONFIG keys:
#: the picture settings added since -- Boost, Pop-Out, Detect Text, Particles,
#: Frame Break, Dual Eye and the mask / backend choices (bug sweep 2026-10-09,
#: A6: a reset kept raining and boosting). Like v1 it leaves the clip range and
#: map, the convergence anchor, the encoder, which outputs to make and the
#: view-only options alone (v1's own list does reset the folders).
RESET_EXTRA = ("dual_eye", "dual_eye_bias", "mask_mode", "splat_backend", "hybrid_mask_sensitivity",
               "hybrid_mask_taper", "m2s_fix_strength", "dilreg_enabled", "pop_amount")
RESET_EXTRA_PREFIXES = ("boost_", "submask_", "rain_", "frame_break_", "fb_")


def coerce(key: str, value: Any) -> Any:
    """`value` as the type v1's variable held it."""
    default = DEFAULTS.get(key)
    if isinstance(default, bool):
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if isinstance(default, int):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default
    if isinstance(default, float):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
    if default is None:
        return value
    return "" if value is None else str(value)


# ---- Low Res resolution presets (user, 2026-10-11): DepthCrafter's custom presets
# and Auto Size, in multiples of 16 -- both Inpainting engines take that (V1 needs
# 8 and resizes otherwise, V2 16 and trims). Shared helpers: studio/resolution_presets.
from studio import resolution_presets as RP  # noqa: E402

PRESET_STEP = 16
#: This machine's own Low Res preset columns (config/, never shipped).
LOCAL_PRESETS_FILE = "splatting_presets.json"
AUTO_SIZES_FILE = "splatting_auto_sizes.json"
#: The sizes suggested for a ratio (user, 2026-10-11: every width in steps of 16 was
#: far too many): these heights, each with the multiple-of-16 width nearest the ratio.
#: Any other size goes in by hand (the editor's W x H boxes).
SUGGESTED_HEIGHTS = (512, 640, 768, 960)
#: The height Auto picks when Settings > Splatting has no pick for a ratio.
AUTO_HEIGHT = 768


def local_presets(config_dir) -> list:
    return RP.read_presets(config_dir, LOCAL_PRESETS_FILE)


def width_for(height: int, ratio: float) -> int:
    """The multiple of 16 nearest height x ratio."""
    return max(PRESET_STEP, int(round(height * ratio / PRESET_STEP)) * PRESET_STEP)


def sizes_for(ratio: float):
    """The suggestions for a ratio: [(w, h, disparity label)], one per SUGGESTED_HEIGHTS."""
    return [(width_for(h, ratio), h, RP.disparity_label(width_for(h, ratio), h, ratio)) for h in SUGGESTED_HEIGHTS]


def default_auto_size(label: str):
    """(the multiple of 16 nearest AUTO_HEIGHT x its ratio, AUTO_HEIGHT): 16:9 -> 1360x768."""
    return width_for(AUTO_HEIGHT, RP.ratio_value(label)), AUTO_HEIGHT


def auto_sizes(config_dir):
    return RP.load_auto_sizes(config_dir, AUTO_SIZES_FILE, default_auto_size, PRESET_STEP)


def save_auto_sizes(config_dir, picks) -> None:
    RP.save_auto_sizes(config_dir, AUTO_SIZES_FILE, picks, default_auto_size)


def save_local_presets(config_dir, picked) -> list:
    return RP.save_presets(config_dir, LOCAL_PRESETS_FILE, picked)


#: Settings Studio no longer offers, held at these values whatever a config or
#: preset saved. Boost's Edge Clean / Edge Sens pre-filter (user, 2026-10-11: made
#: no difference in testing): off, or a saved radius would keep running unseen.
#: Per-clip sidecars are ignored for it in core/splatting/batch_processing.
RETIRED: Dict[str, Any] = {
    "boost_prefilter_radius": 0,
}


def normalise(data: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(DEFAULTS)
    for key, value in (data or {}).items():
        if key in DEFAULTS:
            out[key] = coerce(key, value)
    out.update(RETIRED)
    return out


def _clamp_anchor(value: Any) -> Any:
    """v1 clamps a saved convergence to 0-1 on load (the slider once ran to 2)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return value
    return f"{min(max(f, 0.0), 1.0):.2f}" if (f > 1.0 or f < 0.0) else value


def seed_from_v1(v1: Dict[str, Any]) -> Dict[str, Any]:
    """What to take from config_splat.splatcfg, with v1's load-time remaps."""
    out = {k: v for k, v in (v1 or {}).items() if k in DEFAULTS}
    # Pop Out Boost: v1 reads these from the render-side keys.
    for key in ("thresh", "amount", "curve", "knee", "prefilter_radius", "prefilter_eps"):
        if f"depth_boost_{key}" in v1:
            out[f"boost_{key}"] = v1[f"depth_boost_{key}"]
    for src, dst in (("frame_break_erode_left", "fb_erode_left"), ("frame_break_erode_right", "fb_erode_right"),
                     ("frame_break_erode_global", "fb_erode_global"), ("frame_break_edge_blur", "fb_edge_blur")):
        if src in v1:
            out[dst] = v1[src]
    # "Manual sync for non-standard mappings" (SplatterGUI.__init__)
    if "convergence_point" in v1:
        out["zero_disparity_anchor"] = _clamp_anchor(v1["convergence_point"])
    if "multi_map_enabled" in v1:
        out["multi_map"] = bool(v1["multi_map_enabled"])
    if "enable_full_resolution" in v1:
        out["enable_full_res"] = bool(v1["enable_full_resolution"])
    if "enable_low_resolution" in v1:
        out["enable_low_res"] = bool(v1["enable_low_resolution"])
    if "encoding_encoder" in v1:
        # v1's __init__ reads the encoder back from here (and, since the fix,
        # writes it here); "encoder" is only the harvest's copy.
        out["encoder"] = v1["encoding_encoder"]
    return out


def restore_folders(values: Dict[str, Any]) -> Dict[str, Any]:
    """v1 restores a saved folder only while it still exists; else the default. An input may be a single
    clip or depth map (Select File): kept while that file exists -- a restart reset them (2026-10-16:
    Merging's Put Back then couldn't find the clean clip's depth map, and its dials did nothing)."""
    out = dict(values)
    for key in FOLDER_KEYS:
        path = out.get(key)
        ok = path and (os.path.isdir(path) or (key != "output_splatted" and os.path.isfile(path)))
        if not ok:
            out[key] = DEFAULTS[key]
    return out


def _f(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def config_for_run(values: Dict[str, Any]) -> Dict[str, Any]:
    """v1 _get_current_config: the dict ProcessingSettings.from_config reads.

    Starts from the render's defaults, as v1 does. The encoder is filed under
    "encoding_encoder" explicitly: the harvest names it "encoder" (its
    variable), and until 2026-09-25 neither version wrote the key the render
    reads, so the Encoding dialog's choice never reached a render.
    """
    s = normalise(values)
    config = {k: v for k, v in SPLATTER_DEFAULT_CONFIG.items()}
    config.update(s)
    config["convergence_point"] = s["zero_disparity_anchor"]
    config["multi_map_enabled"] = s["multi_map"]
    config["enable_full_resolution"] = bool(s["enable_full_res"])
    config["enable_low_resolution"] = bool(s["enable_low_res"])
    config["dual_eye"] = bool(s["dual_eye"])
    config["dual_eye_bias"] = _f(s["dual_eye_bias"], 0.5)
    config["depth_boost_thresh"] = _f(s["boost_thresh"], 0.85)
    config["depth_boost_amount"] = _f(s["boost_amount"], 0.0)
    config["depth_boost_curve"] = _f(s["boost_curve"], 2.0)
    config["depth_boost_knee"] = _f(s["boost_knee"], 0.0)
    config["depth_boost_prefilter_radius"] = int(_f(s["boost_prefilter_radius"], 0))
    config["depth_boost_prefilter_eps"] = _f(s["boost_prefilter_eps"], 1e-3)
    config["depth_pop_amount"] = _f(s.get("pop_amount", 0.0), 0.0)
    config["submask_grow"] = _f(s.get("submask_grow", 0.0), 0.0)
    config["submask_soften"] = _f(s.get("submask_soften", 0.0), 0.0)
    config["submask_clean"] = _f(s.get("submask_clean", 0.0), 0.0)
    config["submask_conv"] = _f(s.get("submask_conv", 0.0), 0.0)
    config["rain_mode"] = str(s.get("rain_mode", "off") or "off")
    config["rain_any_direction"] = bool(s.get("rain_any_direction", False))
    config["rain_depth"] = _f(s.get("rain_depth", 0.5), 0.5)
    config["rain_amount"] = _f(s.get("rain_amount", 1.0), 1.0)
    config["rain_layers"] = int(_f(s.get("rain_layers", 3), 3))
    config["rain_strands"] = bool(s.get("rain_strands", True))
    config["rain_lights"] = bool(s.get("rain_lights", True))
    config["rain_pop"] = _f(s.get("rain_pop", 0.0), 0.0)
    config["rain_preset"] = str(s.get("rain_preset", "downpour") or "downpour")
    config["rain_guard"] = _f(s.get("rain_guard", 0.0), 0.0)
    config["rain_particle"] = str(s.get("rain_particle", "rain") or "rain")
    config["rain_placement"] = str(s.get("rain_placement", "front") or "front")
    config["rain_grow"] = int(round(_f(s.get("rain_grow", 0), 0)))
    config["rain_reflections"] = bool(s.get("rain_reflections", False))
    config["rain_spread"] = _f(s.get("rain_spread", 1.0), 1.0)
    config["rain_spacing"] = _f(s.get("rain_spacing", 1.0), 1.0)
    config["rain_conv"] = _f(s.get("rain_conv", 0.0), 0.0)
    config["frame_break_bars"] = _f(s["frame_break_bars"], 0.0)
    config["frame_break_thresh"] = _f(s["frame_break_thresh"], 1.0)
    config["frame_break_erode_left"] = _f(s["fb_erode_left"], 0.0)
    config["frame_break_erode_right"] = _f(s["fb_erode_right"], 0.0)
    config["frame_break_erode_global"] = _f(s["fb_erode_global"], 0.0)
    config["frame_break_edge_blur"] = _f(s["fb_edge_blur"], 0.0)
    config["preview_brightness"] = _f(s["preview_brightness"], 1.0)
    config["output_crf"] = s["output_crf_full"]           # v1's legacy CRF mapping
    config["encoding_encoder"] = s["encoder"]
    return config


def effective_depth_folder(values: Dict[str, Any]) -> str:
    """v1 _get_effective_depth_map_folder: a single depth FILE means its folder;
    Multi-Map reads the chosen subfolder."""
    base = values.get("input_depth_maps", "")
    if base and os.path.isfile(base):
        base = os.path.dirname(base)
    chosen = str(values.get("selected_depth_map") or "").strip()
    if values.get("multi_map") and chosen:
        return os.path.join(base, chosen)
    return base


def sidecar_folder(values: Dict[str, Any], create: bool = True) -> str:
    """v1 _get_sidecar_base_folder: Multi-Map keeps sidecars in <depth>/sidecars."""
    if values.get("multi_map"):
        folder = os.path.join(values.get("input_depth_maps", ""), "sidecars")
        if create:
            os.makedirs(folder, exist_ok=True)
        return folder
    return effective_depth_folder(values)


def m2s_render_settings(values: Dict[str, Any]) -> Dict[str, Any]:
    """v1 _m2s_render_settings: the four M2S selections, normalised."""
    return {
        "splat_backend": str(values.get("splat_backend") or "SC"),
        "hybrid_sensitivity": max(0.0, min(100.0, _f(values.get("hybrid_mask_sensitivity"), 50.0))),
        "hybrid_taper": bool(values.get("hybrid_mask_taper")),
        "m2s_fix_strength": max(0.50, min(2.00, _f(values.get("m2s_fix_strength"), 1.0))),
    }


def processing_overrides(values: Dict[str, Any], sidecar_ext: str = ".fssidecar") -> Dict[str, Any]:
    """v1 _get_processing_settings's GUI-only overrides (render, not a test)."""
    s = normalise(values)
    return {
        "enable_sidecar_gamma": bool(s["enable_sidecar_gamma"]),
        "enable_sidecar_blur_dilate": bool(s["enable_sidecar_blur_dilate"]),
        "multi_map": bool(s["multi_map"]),
        "selected_depth_map": str(s["selected_depth_map"]).strip(),
        "color_tags_mode": s["color_tags_mode"],
        "is_test_mode": False,
        "test_target_frame_idx": None,
        "skip_lowres_preproc": bool(s["skip_lowres_preproc"]),
        "sidecar_ext": sidecar_ext,
        "sidecar_folder": sidecar_folder(s),
        "mask_mode": s["mask_mode"],
        **m2s_render_settings(s),
        "dual_eye": bool(s["dual_eye"]),
        "dual_eye_bias": _f(s["dual_eye_bias"], 0.5),
    }


def processing_range(values: Dict[str, Any]) -> Tuple[int, Optional[int]]:
    """The batch's From/To: always the whole list now. Custom Batch Clip Range
    is gone from Studio (user, 2026-10-01): the clip list's tick boxes and
    Process Selected do the same. A range saved ticked in an older config
    must not quietly narrow a batch with no box left to untick, so it is
    ignored. (v1 _snapshot_processing_range is kept below for reference.)"""
    return 0, None


def _v1_processing_range(values: Dict[str, Any]) -> Tuple[int, Optional[int]]:
    """v1 _snapshot_processing_range: 1-based From/To, only while ticked."""
    if not values.get("custom_clip_range"):
        return 0, None
    from_idx, to_idx = 0, None
    text = str(values.get("process_from") or "").strip()
    if text:
        try:
            from_idx = int(text) - 1
        except ValueError:
            pass
    text = str(values.get("process_to") or "").strip()
    if text:
        try:
            to_idx = int(text)
        except ValueError:
            pass
    return from_idx, to_idx


def job_fields(values: Dict[str, Any]) -> Dict[str, str]:
    """v1 _record_job_handoff's record (hires/lowres joined with "/")."""
    splat_out = values.get("output_splatted", "")
    fields = {"source": values.get("input_source_clips", ""), "depth": values.get("input_depth_maps", ""),
              "splat_out": splat_out}
    if splat_out:
        base = str(splat_out).replace("\\", "/").rstrip("/")
        fields["hires"] = f"{base}/hires"
        fields["lowres"] = f"{base}/lowres"
    return fields
