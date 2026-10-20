"""settings.py -- Inpainting (V1, SVD) settings: defaults, types, run snapshot.

Keys are v1's config keys (``config_inpaint.json``), so seeding is a copy and
a settings file saved by either version loads in the other.

Types follow v1's tk variables, because the generated engine reads them
through ``_setting()`` exactly as v1 did: numeric fields are STRINGS (v1 used
StringVars and parses them where it needs a number), toggles are bools. The
engine is handed those values untouched; only Start validates them, with v1's
own rules and message (``InpaintingGUI.start_processing``).

No Qt, no Tk, no torch here.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from studio.default_folders import localize as _localize

#: v1 __init__ fallbacks -- what a key is when the config lacks it.
DEFAULTS: Dict[str, Any] = {
    "input_folder": "./output_splatted",
    "output_folder": "./completed_output",
    "hires_blend_folder": "./output_splatted_hires",
    "num_inference_steps": "10",                 # user, 2026-10-18
    "tile_num": "2",
    # 25 is SVD's native window and the practical ceiling for this engine (v1).
    "frames_chunk": "25",
    "frame_overlap": "5",
    "original_input_blend_strength": "0.1",      # user, 2026-10-18
    "output_crf": "23",
    "process_length": "-1",
    "single_clip_id": "",
    "offload_type": "model",
    "inpaint_mask_initial_threshold": "0.3",
    "inpaint_mask_post_threshold": "0.3",
    "inpaint_mask_morph_kernel_size": "0.0",
    "inpaint_mask_dilate_kernel_size": "5",
    "inpaint_mask_blur_kernel_size": "5",
    "mask_initial_threshold": "0.3",
    "mask_post_threshold": "0.3",
    "mask_morph_kernel_size": "0.0",
    "mask_dilate_kernel_size": "5",
    "mask_blur_kernel_size": "5",
    "blend_mask_source": "hybrid",
    "enable_post_inpainting_blend": False,
    "enable_color_transfer": True,
    "keep_inpaint_cache": False,
    "move_to_finished": True,
    "inpaint_direction": "Forward",
    "use_sageattention": True,
    # torch.compile the UNet + VAE decoder (Torch Compile on the page): ~13% faster
    # chunks with Sage, after a one-time warm-up of minutes -- for long runs.
    "use_compile": False,
    # Settings > Inpainting > Guidance Scale. 1.0 skips the unconditional UNet
    # pass (v1 used 1.01): ~30% faster chunks, same picture (2026-10-09).
    "guidance_scale": "1.0",
    "debug_mode": False,
    "dnxhr_fullres_split": False,
    # Enable DNxHR did nothing in Inpainting until 2026-10-11, and the presets switched it on:
    # it is set off once, then left to the user (normalise)
    "inpaint_dnxhr_reviewed": True,
    "dnxhr_profile": "HQX (10-bit 4:2:2)",
    # Encoding options: v1 keeps these in app_config (Options > Encoding
    # Settings) and the engine reads them from there.
    "codec": "H.265",
    "encoding_encoder": "Auto",                  # enoky's GUIs' default (user, 2026-10-18)
    "encoding_quality": "Medium",
    "encoding_tune": "None",
    "bit_depth": "Auto",
    "pix_fmt": "Auto",
    "nvenc_lookahead_enabled": False,
    "nvenc_lookahead": 16,
    "nvenc_spatial_aq": False,
    "nvenc_temporal_aq": False,
    "nvenc_aq_strength": 8,
    "color_tags": "Auto",
}
# the "./" folders under <Videos>/StereoCrafter Studio, not Studio's own folder (release check 2026-10-17;
# studio/default_folders.py)
DEFAULTS = _localize(DEFAULTS)

#: v1's File > Reset to Default. Not the same as DEFAULTS (chunk 23, overlap
#: 3, bias 0.5, blur 7...) -- these are what v1's reset actually sets, and it
#: leaves encoding alone.
RESET_VALUES: Dict[str, Any] = {
    "input_folder": "./output_splatted",
    "output_folder": "./completed_output",
    "num_inference_steps": "10",                 # user, 2026-10-18
    "tile_num": "2",
    "frames_chunk": "23",
    "frame_overlap": "3",
    "original_input_blend_strength": "0.1",      # as DEFAULTS (user, 2026-10-18)
    "single_clip_id": "",
    "offload_type": "model",
    "use_sageattention": True,
    "use_compile": False,
    "guidance_scale": "1.0",
    "inpaint_mask_initial_threshold": "0.3",
    "inpaint_mask_post_threshold": "0.3",
    "inpaint_mask_morph_kernel_size": "0.0",
    "inpaint_mask_dilate_kernel_size": "5",
    "inpaint_mask_blur_kernel_size": "7",
    "mask_initial_threshold": "0.3",
    "mask_post_threshold": "0.3",
    "mask_morph_kernel_size": "0.0",
    "mask_dilate_kernel_size": "5",
    "mask_blur_kernel_size": "7",
    "blend_mask_source": "hybrid",
    "keep_inpaint_cache": False,
    "move_to_finished": True,
    "enable_post_inpainting_blend": False,
    "enable_color_transfer": True,
    "inpaint_direction": "Forward",
}
# the "./" folders under <Videos>/StereoCrafter Studio, not Studio's own folder (release check 2026-10-17;
# studio/default_folders.py)
RESET_VALUES = _localize(RESET_VALUES)

#: Keys whose v1 tk variable is NOT "<key>_var".
_ATTR_EXCEPTIONS = {
    "enable_post_inpainting_blend": "enable_post_inpainting_blend",
    "enable_color_transfer": "enable_color_transfer",
}

#: Settings that are app_config only in v1 (no tk variable).
ENCODING_KEYS = ("codec", "encoding_encoder", "encoding_quality", "encoding_tune", "bit_depth", "pix_fmt",
                 "nvenc_lookahead_enabled", "nvenc_lookahead", "nvenc_spatial_aq", "nvenc_temporal_aq",
                 "nvenc_aq_strength", "color_tags", "dnxhr_fullres_split", "dnxhr_profile")

# Option lists: v1's own. The encoding ones mirror core.common.encoding_utils,
# which the controller prefers when it imports; these are the fallback.
CHOICES: Dict[str, list] = {
    "offload_type": ["model", "sequential", "none"],
    "inpaint_direction": ["Forward", "Reverse", "Both"],
    "blend_mask_source": ["hybrid", "hires", "lowres"],
    "codec": ["Auto", "H.264", "H.265"],
    "encoding_encoder": ["Auto", "NVENC", "Force CPU"],
    "encoding_quality": ["Fastest", "Faster", "Fast", "Medium", "Slow", "Slower", "Slowest"],
    "encoding_tune": ["None", "Film", "Grain", "Animation", "Still Image", "PSNR", "SSIM", "Fast Decode", "Zero Latency"],
    "bit_depth": ["Auto", "8-bit", "10-bit (Main10)"],
    "pix_fmt": ["Auto", "yuv420p", "yuv420p10le", "p010le"],
    "color_tags": ["Off", "Auto", "BT.709 L", "BT.709 F", "BT.2020 PQ", "BT.2020 HLG"],
    "dnxhr_profile": ["SQ (8-bit 4:2:2)", "HQ (8-bit 4:2:2)", "HQX (10-bit 4:2:2)", "444 (10-bit 4:4:4)"],
}


def attr_for(key: str) -> str:
    """The v1 tk-variable attribute name the engine reads a key under."""
    return _ATTR_EXCEPTIONS.get(key, key + "_var")


def coerce(key: str, value: Any) -> Any:
    """`value` as the type v1 held the key in. Strings stay strings."""
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
    if default is None:
        return value
    return "" if value is None else str(value)


#: Settings Studio no longer offers, held at these values whatever a config,
#: preset or older Studio saved. Post-Inpainting Blend (user, 2026-10-11):
#: Merging blends the fill into the original with more control; the hi-res
#: finalize (upscale, colour transfer, the hi-res left eye) still runs. The
#: key stays because the generated engine reads it.
RETIRED: Dict[str, Any] = {
    "enable_post_inpainting_blend": False,
    # Single Clip ID (user, 2026-10-11): Upcoming Jobs' Inpaint This Clip Now
    # runs one clip; a saved ID would filter every run with nothing showing it.
    "single_clip_id": "",
}


def normalise(data: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(DEFAULTS)
    for key, value in (data or {}).items():
        if key in DEFAULTS:
            out[key] = coerce(key, value)
    if (data or {}).get("inpaint_dnxhr_reviewed") is not True:
        out["dnxhr_fullres_split"] = False          # it never applied before: off, once (user, 2026-10-11)
    out.update(RETIRED)
    return out


def seed_from_v1(v1: Dict[str, Any]) -> Dict[str, Any]:
    """What to take from config_inpaint.json.

    Carries v1's two load-time fallbacks: the inference-mask keys fall back to
    the blend-mask keys (older configs had one set), and debug mode may be
    saved as either "debug_mode" or "debug_mode_enabled".
    """
    out = {k: v for k, v in v1.items() if k in DEFAULTS}
    for part in ("initial_threshold", "morph_kernel_size", "dilate_kernel_size", "blur_kernel_size"):
        if f"inpaint_mask_{part}" not in v1 and f"mask_{part}" in v1:
            out[f"inpaint_mask_{part}"] = v1[f"mask_{part}"]
    if "inpaint_mask_post_threshold" not in v1:
        out["inpaint_mask_post_threshold"] = out.get("inpaint_mask_initial_threshold",
                                                     v1.get("mask_initial_threshold", "0.3"))
    if "debug_mode" not in v1 and "debug_mode_enabled" in v1:
        out["debug_mode"] = v1["debug_mode_enabled"]
    return out


INVALID_MESSAGE = ("Please enter valid values: Inference Steps >=1, Tile Number >=1, Frames Chunk >=1, "
                   "Frame Overlap >=0, Original Input Bias between 0.0 and 1.0, Output CRF >=0, Process "
                   "Length is -1 or >0, Single Clip ID is blank or >=0, and Guidance Scale (Settings) is "
                   "between 1.0 and 10.")


def validate(s: Dict[str, Any]) -> Tuple[Optional[dict], str]:
    """v1 start_processing's checks. Returns (run_batch_process kwargs, "") or (None, message)."""
    try:
        steps = int(s["num_inference_steps"])
        tile = int(s["tile_num"])
        chunk = int(s["frames_chunk"])
        overlap = int(s["frame_overlap"])
        bias = float(s["original_input_blend_strength"])
        crf = int(s["output_crf"])
        length = int(s["process_length"])
        guidance = float(s["guidance_scale"])
        if not (1.0 <= guidance <= 10.0):
            raise ValueError
        if length != -1 and length <= 0:
            raise ValueError
        raw_id = str(s["single_clip_id"]).strip()
        clip_id = None
        if raw_id:
            clip_id = int(raw_id)
            if clip_id < 0:
                raise ValueError
        if steps < 1 or tile < 1 or chunk < 1 or overlap < 0 or not (0.0 <= bias <= 1.0) or crf < 0:
            raise ValueError
    except (TypeError, ValueError):
        return None, INVALID_MESSAGE
    if 2 * overlap > chunk:
        # each chunk cross-fades `overlap` frames but the window moves on only
        # chunk - overlap: past half the chunk the output runs longer than the
        # clip and every later frame is out of step with its mask (bug sweep
        # 2026-10-09 #10; at overlap >= chunk no progress at all, audit minor)
        return None, (f"Frame Overlap ({overlap}) can be at most half of Frames Chunk ({chunk}), "
                      f"so {chunk // 2} here: a larger overlap puts the frames after it out of step "
                      f"with their masks.")
    return {
        "num_inference_steps": steps, "tile_num": tile, "offload_type": s["offload_type"],
        "frames_chunk": chunk, "gui_overlap": overlap, "gui_original_input_blend_strength": bias,
        "gui_output_crf": crf, "process_length": length, "single_clip_id": clip_id,
        "inpaint_direction": s["inpaint_direction"],
    }, ""


def run_settings(s: Dict[str, Any]) -> Dict[str, Any]:
    """The engine's frozen view: every key under its v1 attribute name."""
    return {attr_for(k): v for k, v in s.items() if k not in ENCODING_KEYS or k.startswith("dnxhr")}
