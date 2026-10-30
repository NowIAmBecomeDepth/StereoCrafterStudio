"""settings.py -- DepthCrafter's settings: defaults, coercion, run snapshot.

Keys are v1's own (``depthcrafter_gui_seg.DepthCrafterGUI.all_tk_vars``), so
seeding from ``config_depthcrafter.json`` is a straight copy and a settings
file saved by either version loads in the other. Defaults are v1's __init__
values, not its saved config.

No Qt and no Tk here: the runner, the controller and the tests all use it.
"""

from __future__ import annotations

import json
import logging
import math
from typing import Any, Dict, Optional

from studio.default_folders import localize as _localize

logger = logging.getLogger(__name__)

from studio import resolution_presets as RP

DEFAULTS: Dict[str, Any] = {
    "input_dir_or_file_var": "./input_clips",
    "output_dir": "./output_depthmaps",
    "guidance_scale": 1.0,
    "inference_steps": 5,
    "seed": 42,
    "cpu_offload": "model",
    "use_cudnn_benchmark": False,
    "process_length": -1,
    "target_fps": -1.0,
    "window_size": 60,                  # user, 2026-10-18: a generic starting point (was 110)
    "overlap": 45,                      # user, 2026-10-18 (was 25)
    "latent_checkpoint_var": True,      # user, 2026-10-18: costs little
    "process_as_segments_var": False,
    "save_final_output_json_var": False,
    # 10-bit by default: main10_mp4 at CRF 0 measured ~6x more accurate than
    # 8-bit mp4 at CRF 18 on a real depth map, and smaller (v1's reasoning).
    "merge_output_format_var": "main10_mp4",
    "merge_alignment_method_var": "Shift & Scale",
    "merge_dither_var": False,
    "merge_dither_strength_var": 0.5,
    "merge_gamma_correct_var": False,
    "merge_gamma_value_var": 1.5,
    "merge_percentile_norm_var": False,
    "merge_norm_low_perc_var": 0.1,
    "merge_norm_high_perc_var": 99.9,
    "keep_intermediate_npz_var": False,
    "min_frames_to_keep_npz_var": 0,
    "keep_intermediate_segment_visual_format_var": "mp4",
    "merge_output_suffix_var": "_depth",
    "use_local_models_only_var": False,
    "target_height": 384,
    "target_width": 640,
    # Auto size (user, 2026-10-05): a loaded clip fills Target Width/Height
    # with its aspect ratio's pick (auto_size), and in a batch each clip of
    # another shape gets its own shape's pick. Studio only (v1 has no such key).
    # Off by default (user, 2026-10-05).
    "auto_resolution_var": False,
    "stabilize_depth_var": False,
    "stabilize_ref_mode_var": "auto",
    "stabilize_ref_index_var": 0,
    "stabilize_smooth_window_var": 5,
    "full_percentile_norm_var": False,
    "full_norm_low_perc_var": 0.1,
    "full_norm_high_perc_var": 99.9,
    # Auto top (user, 2026-10-12): the percentiles from the clip's usual frames only, so a
    # brief close pass clips instead of setting the top -- off: as before
    "full_norm_auto_top_var": False,
    # "auto" resolves per output format in utils.save_video: 0 for
    # main10_mp4, 18 for 8-bit mp4. A number overrides both.
    "output_crf_var": "auto",
    "output_preset_var": "veryslow",
    "enable_dual_output_robust_norm": False,
    "robust_norm_low_percentile": 0.0,
    "robust_norm_high_percentile": 75.5,
    "robust_norm_output_min": 0.0,
    "robust_norm_output_max": 1.0,
    "robust_output_suffix": "_clipped_depth",
    "is_depth_far_black": True,
    "use_sageattention_var": True,
    # TensorRT (the boxes under CPU Offload Mode, 2026-10-29): the VAE as TensorRT FP16 engines, built once per
    # size into trt_engines/ -- as close to exact as the normal path. Installed from Settings > Studio > Models.
    "use_tensorrt_var": False,
    # ...and the UNet too (user, 2026-10-29: split in two): ~1.8x on a real clip, but the depth ~1.7 codes
    # from exact (mostly a uniform lift), against the VAE's 0.25
    "use_tensorrt_unet_var": False,
    # Resume (user, 2026-10-25: "like the other stages so any stage can turn off using finished folders"):
    # on, a finished clip's source moves into <input>/finished (a failed one into failed/) and the next
    # Start skips it, as before; off, the sources stay where they are
    "move_to_finished": True,
    # v2 only: v1 kept this in a menu checkbox and never saved it.
    "debug_logging": False,
}
# the "./" folders under <Videos>/StereoCrafter Studio, not Studio's own folder (release check 2026-10-17;
# studio/default_folders.py)
DEFAULTS = _localize(DEFAULTS)

#: Choice lists, in v1's order. The EXR entries are appended at runtime when
#: OpenEXR imports, exactly as v1 does.
CHOICES: Dict[str, list] = {
    "cpu_offload": ["model", "sequential", "none"],
    "merge_output_format_var": ["mp4", "main10_mp4", "png_sequence"],
    "keep_intermediate_segment_visual_format_var": ["png_sequence", "mp4", "main10_mp4", "none"],
    "merge_alignment_method_var": ["Shift & Scale", "Linear Blend"],
    "stabilize_ref_mode_var": ["auto", "manual"],
    "output_preset_var": ["veryslow", "slower", "slow", "medium", "fast"],
}

#: The resolution presets built in: none (user, 2026-10-11: the built-in
#: "16:9 · 1.85:1" column doubled the sizes ticked in Settings > DepthCrafter).
#: Every column is this machine's own, made there (config/, never shipped).
#: Sizes are multiples of 64 -- the DepthCrafter / inpaint VAE requirement.
RESOLUTION_PRESETS: list = []


def preset_height(width: int, ratio: float) -> int:
    """The multiple of 64 whose width / height comes closest to `ratio`."""
    return RP.nearest_height(width, ratio, 64)


#: This machine's own preset columns (settings_store.local_presets). Never ships (config/).
LOCAL_PRESETS_FILE = "depthcrafter_presets.json"


def local_presets(config_dir) -> list:
    return RP.read_presets(config_dir, LOCAL_PRESETS_FILE)


# ---- custom presets, made in Settings > DepthCrafter (user, 2026-10-05) -------
# The ratio helpers live in studio/resolution_presets.py, shared with Splatting.
CUSTOM_RATIOS = RP.CUSTOM_RATIOS
ratio_value = RP.ratio_value
disparity_label = RP.disparity_label
ratio_class = RP.ratio_class
video_ratio = RP.video_ratio
#: Sizes offered: widths in steps of 64 up to this, heights too, near 1080p.
CUSTOM_MAX_W, CUSTOM_MAX_H, CUSTOM_MIN = 1920, 1088, 128
#: How far from the true ratio a size may be and still be offered.
CUSTOM_MAX_DISPARITY = 0.10


def sizes_for(ratio: float):
    """Every width in steps of 64 up to ~1080p, each with the multiples of 64
    just under and just over the true height (one when it is exact) that are
    within CUSTOM_MAX_DISPARITY of the ratio: [(w, h, disparity label)].
    Both, so 640 wide at 4:3 offers 640x448 (7.1% wider) and 640x512 (6.2%
    taller) -- the pick is the user's."""
    return RP.sizes_for(ratio, 64, 256, CUSTOM_MAX_W, CUSTOM_MAX_H, CUSTOM_MIN, both=True,
                        max_disparity=CUSTOM_MAX_DISPARITY)


# ---- auto size (user, 2026-10-05) ----------------------------------------------
#: The width Auto picks when Settings > DepthCrafter has no pick for a ratio.
AUTO_WIDTH = 768
#: Each ratio's pick, this machine's own: {"2.39:1": [768, 320], ...}.
AUTO_SIZES_FILE = "depthcrafter_auto_sizes.json"
#: Built-in columns serving several ratios: none now (RESOLUTION_PRESETS is empty).
BUILTIN_COVERS: Dict[str, tuple] = {}


def default_auto_size(label: str):
    """(AUTO_WIDTH, the multiple of 64 nearest its ratio): 2.39:1 -> 768x320."""
    return AUTO_WIDTH, preset_height(AUTO_WIDTH, ratio_value(label))


def auto_sizes(config_dir) -> Dict[str, tuple]:
    """Every ratio's pick: the saved one, else default_auto_size."""
    return RP.load_auto_sizes(config_dir, AUTO_SIZES_FILE, default_auto_size, 64)


def save_auto_sizes(config_dir, picks: Dict[str, tuple]) -> None:
    """Write the picks that differ from the default."""
    RP.save_auto_sizes(config_dir, AUTO_SIZES_FILE, picks, default_auto_size)


def save_local_presets(config_dir, picked: Dict[str, list]) -> list:
    """Write the custom columns ({ratio label: [(w, h), ...]}); see RP.save_presets."""
    return RP.save_presets(config_dir, LOCAL_PRESETS_FILE, picked)

#: The settings a merge uses. When "Process as Segments" is switched off,
#: v1 clears these booleans so a later full-video run is not surprised by them.
MERGE_ONLY_FLAGS = ("keep_intermediate_npz_var", "merge_dither_var",
                    "merge_gamma_correct_var", "merge_percentile_norm_var")


def openexr_available() -> bool:
    try:
        import OpenEXR  # noqa: F401
        import Imath  # noqa: F401
        return True
    except ImportError:
        return False


def choices(exr: Optional[bool] = None) -> Dict[str, list]:
    out = {k: list(v) for k, v in CHOICES.items()}
    if exr if exr is not None else openexr_available():
        out["merge_output_format_var"] += ["exr_sequence", "exr"]
        out["keep_intermediate_segment_visual_format_var"] += ["exr_sequence", "exr"]
    return out


def coerce(key: str, value: Any) -> Any:
    """`value` as the type of the key's default, or the default if it won't go.

    A number field typed as "12.0" must become 12 for an int setting, and a
    half-typed "-" must not reach the runner at all.
    """
    default = DEFAULTS.get(key)
    if default is None:
        return value
    try:
        if isinstance(default, bool):
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if isinstance(default, int):
            return int(float(value))
        if isinstance(default, float):
            number = float(value)
            if not math.isfinite(number):          # "inf", "1e999", "nan" (bug sweep 2026-10-09, A3)
                raise ValueError(value)
            return number
        return "" if value is None else str(value)
    except (TypeError, ValueError, OverflowError):  # int(float("1e999")) overflows
        logger.warning("Setting %s: %r is not valid; using %r", key, value, default)
        return default


def normalise(data: Dict[str, Any]) -> Dict[str, Any]:
    """Every known key present and correctly typed; unknown keys dropped."""
    out = dict(DEFAULTS)
    for key, value in (data or {}).items():
        if key in DEFAULTS:
            out[key] = coerce(key, value)
    return out


def seed_from_v1(v1: Dict[str, Any]) -> Dict[str, Any]:
    """What to take from config_depthcrafter.json: every setting v2 knows."""
    return {k: v for k, v in v1.items() if k in DEFAULTS}


def resolve_output_crf(raw: Any) -> Optional[int]:
    """An int CRF, or None to let the output format pick its default.

    Free text so "auto" stays expressible. Anything non-numeric resolves to
    None rather than raising: a typo here must not throw away a finished
    denoising run at the save step (v1's reasoning).
    """
    text = str(raw or "").strip().lower()
    if text in ("", "auto", "default"):
        return None
    try:
        return max(0, min(51, int(float(text))))
    except (TypeError, ValueError):
        logger.warning("Output CRF %r is not a number; using the per-format default instead.", raw)
        return None


def window_overlap_problem(settings: Dict[str, Any]) -> str:
    """Why Window / Overlap can't run, or "" (audit 2026-09-28 #21 / #22).

    Full video: the pipeline steps Window - Overlap frames per window, so
    Overlap >= Window never advances (a hang) and Overlap 0 breaks its
    stitching after the first window. Segments: the merge blends each
    segment's first and last Overlap frames, so above half the Window it
    emits frames twice and the depth drifts against the video.
    """
    try:
        window, overlap = int(settings["window_size"]), int(settings["overlap"])
    except (KeyError, TypeError, ValueError):
        return "Window and Overlap must be whole numbers."
    if settings.get("process_as_segments_var"):
        if not 0 <= overlap <= window // 2:
            return (f"With segments, Overlap must be between 0 and half the Window ({window // 2} for a "
                    f"Window of {window}); it is {overlap}. More than that repeats frames in the merged depth map.")
        return ""
    if window < 2 or not 1 <= overlap < window:
        return (f"Overlap must be at least 1 and less than the Window ({window}); it is {overlap}. "
                f"Otherwise the full-video run cannot move from one window to the next.")
    return ""


def moves_finished(opts: Dict[str, Any], single: bool, test: bool) -> bool:
    """A run moves each source into <input>/finished (or failed/) as it ends: a folder of clips with
    Resume on (user, 2026-10-25: Resume, as the other stages) -- never a single clip or a Test Render."""
    return not single and not test and bool(opts.get("move_to_finished", True))


def snapshot(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Freeze everything a run reads. Taken on the UI thread at Start.

    Returns the normalised settings under their own keys PLUS the derived
    keys v1's ``_snapshot_run_options`` produced, under the names v1's job
    path reads through ``_opt()``. Keeping both spellings lets the ported
    runner stay line-for-line comparable with v1.
    """
    s = normalise(settings)
    ref_index = None
    if s["stabilize_ref_mode_var"] != "auto":
        ref_index = max(0, int(s["stabilize_ref_index_var"]))
    s.update({
        "keep_intermediate_npz": bool(s["keep_intermediate_npz_var"]),
        "min_frames_to_keep_npz": max(0, int(s["min_frames_to_keep_npz_var"])),
        "latent_checkpoint": bool(s["latent_checkpoint_var"]),
        "intermediate_segment_visual_format": s["keep_intermediate_segment_visual_format_var"],
        "save_final_output_json": bool(s["save_final_output_json_var"]),
        "stabilize_depth": bool(s["stabilize_depth_var"]),
        "stabilize_ref_index": ref_index,
        "stabilize_smooth_window": max(0, int(s["stabilize_smooth_window_var"])),
        "full_percentile_norm": bool(s["full_percentile_norm_var"]),
        "norm_low_percentile": float(s["full_norm_low_perc_var"]),
        "norm_high_percentile": float(s["full_norm_high_perc_var"]),
        "norm_auto_top": bool(s["full_norm_auto_top_var"]),
        "output_crf": resolve_output_crf(s["output_crf_var"]),
        "output_preset": s["output_preset_var"] or None,
        "output_format": s["merge_output_format_var"],
    })
    return s
