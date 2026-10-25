import functools
import logging
import subprocess
import sys
from typing import Optional, Dict, Any, List
from core.common.gpu_utils import check_cuda_availability

logger = logging.getLogger(__name__)

QUALITY_PRESETS = ("Fastest", "Faster", "Fast", "Medium", "Slow", "Slower", "Slowest")

CODEC_OPTIONS = ("Auto", "H.264", "H.265")

# Bit depth selection for encoder output. "Auto" preserves existing behavior
# (only forces 10-bit when HDR color tags are detected). The explicit options
# let the user force 10-bit (Main10 / p010le / yuv420p10le) regardless of
# source HDR tagging -- e.g. for 10-bit SDR sources.
BIT_DEPTH_OPTIONS = ("Auto", "8-bit", "10-bit (Main10)")

# Explicit pixel-format override, exposed as a manual dropdown in the GUIs so the
# user can force and verify the exact pix_fmt reaching FFmpeg rather than relying
# on auto-detection. "Auto" defers to bit-depth / HDR logic. Note: NVENC cannot
# accept yuv420p10le and build_encoder_args() will transparently map it to p010le
# for NVENC encoders; pick p010le directly for GPU 10-bit to avoid confusion.
PIX_FMT_OPTIONS = ("Auto", "yuv420p", "yuv420p10le", "p010le")

# DNxHR profile choices, shared by the settings dialog and both GUIs so the
# option strings and the bit-depth-aware default cannot drift apart.
# SQ/HQ are 8-bit 4:2:2, HQX is 10-bit 4:2:2, 444 is 10-bit 4:4:4.
DNXHR_PROFILE_OPTIONS = (
    "SQ (8-bit 4:2:2)",
    "HQ (8-bit 4:2:2)",
    "HQX (10-bit 4:2:2)",
    "444 (10-bit 4:4:4)",
)

DNXHR_PROFILE_8BIT = "HQ (8-bit 4:2:2)"
DNXHR_PROFILE_10BIT = "HQX (10-bit 4:2:2)"


def default_dnxhr_profile(bit_depth: str = "Auto") -> str:
    """Bit-depth-appropriate DNxHR profile.

    HQ is the 8-bit 4:2:2 profile and HQX the 10-bit one. Pairing an 8-bit
    pipeline with HQX wastes bitrate on precision the data does not carry, and
    pairing 10-bit with HQ throws that precision away -- so the default follows
    the selected bit depth rather than being fixed.

    "Auto" resolves to HQX: it is the safe direction, since a 10-bit profile
    carrying 8-bit data loses nothing.
    """
    return DNXHR_PROFILE_8BIT if str(bit_depth) == "8-bit" else DNXHR_PROFILE_10BIT

CPU_PRESET_MAP = {
    "Fastest": "ultrafast",
    "Faster": "faster",
    "Fast": "fast",
    "Medium": "medium",
    "Slow": "slow",
    "Slower": "slower",
    "Slowest": "veryslow",
}

NVENC_PRESET_MAP = {
    "Fastest": "p1",
    "Faster": "p2",
    "Fast": "p3",
    "Medium": "p4",
    "Slow": "p5",
    "Slower": "p6",
    "Slowest": "p7",
}

CPU_TUNE_OPTIONS = ("None", "Film", "Grain", "Animation", "Still Image", "PSNR", "SSIM", "Fast Decode", "Zero Latency")

ENCODER_OPTIONS = ("Auto", "NVENC", "Force CPU")


@functools.lru_cache(maxsize=1)
def nvenc_available() -> bool:
    """Whether this machine can actually encode with NVENC.

    Two things have to be true and neither implies the other: a usable CUDA GPU,
    and an FFmpeg build with the NVENC encoders compiled in. Probed once and
    cached -- it cannot change while the program is running, and the probe costs
    an ffmpeg launch.

    Merging is CPU/ffmpeg work and is perfectly usable on a machine with no GPU
    at all, so getting this wrong is not a niche concern.
    """
    # check_cuda_availability(), not the CUDA_AVAILABLE constant: that global
    # starts False and is only set when the function runs, so `from ... import
    # CUDA_AVAILABLE` binds False forever. The old import here did exactly that
    # and was then never read -- which is probably why the GPU check got
    # bypassed in the first place. The function caches its own result.
    if not check_cuda_availability():
        logger.info("NVENC unavailable: no CUDA device.")
        return False
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=20, check=False,
            creationflags=creationflags,
        )
        listed = (result.stdout or "") + (result.stderr or "")
        ok = ("hevc_nvenc" in listed) or ("h264_nvenc" in listed)
        if not ok:
            logger.warning("NVENC unavailable: this FFmpeg build has no *_nvenc encoders.")
        return ok
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(f"NVENC unavailable: could not probe FFmpeg ({exc}).")
        return False


def resolve_use_nvenc(encoder: str) -> bool:
    """Decide GPU vs CPU encoding for an encoder setting.

    "Auto"      -> NVENC when this machine has it, CPU otherwise.
    "NVENC"     -> NVENC, falling back to CPU rather than failing the export.
    anything else ("Force CPU", legacy values) -> CPU.

    The fallback is the point. This used to return NVENC purely on the string,
    never consulting CUDA_AVAILABLE, so a machine without a usable NVENC failed
    every export with a raw FFmpeg error and the only way out was finding the
    Encoder dropdown.
    """
    setting = (encoder or "").strip()
    if setting == "Force CPU":
        return False
    if setting == "Auto" or not setting:
        return nvenc_available()
    if setting.startswith("NVENC"):
        if nvenc_available():
            return True
        logger.warning(
            "Encoder is set to NVENC but this machine has no usable NVENC; "
            "encoding on the CPU instead."
        )
        return False
    # Unknown/legacy value: treat like Auto rather than assuming a GPU.
    return nvenc_available()

DEFAULT_ENCODING_CONFIG = {
    "codec": "H.265",
    # Auto, not NVENC: a first run on a machine without NVENC must produce a
    # working export, not an ffmpeg error the user has to decode.
    "encoder": "Auto",
    "quality": "Medium",
    "tune": "None",
    "crf": 23,
    "bit_depth": "Auto",
    "pix_fmt": "Auto",
    "nvenc_lookahead_enabled": False,
    "nvenc_lookahead": 16,
    "nvenc_spatial_aq": False,
    "nvenc_temporal_aq": False,
    "nvenc_aq_strength": 8,
}


def get_encoder_codec(codec: str = "H.265", force_10bit: bool = False, encoder: str = "Auto") -> str:
    """Determine the encoder codec based on settings.

    Args:
        codec: "Auto", "H.264", or "H.265" — chooses the video format
        force_10bit: Whether to use 10-bit encoding
        encoder: "Auto", "NVENC" or "Force CPU" -- see resolve_use_nvenc()

    Returns:
        Codec string (e.g., "h264_nvenc", "libx264", "hevc_nvenc", "libx265")
    """
    # The Codec dropdown picks H.264 vs H.265; this picks GPU vs CPU, and falls
    # back to CPU when the machine cannot do NVENC.
    use_nvenc = resolve_use_nvenc(encoder)

    # H.264 cannot do 10-bit well — promote to H.265 when 10-bit is requested
    if codec == "H.264" and force_10bit:
        logger.warning("H.264 does not support 10-bit well; using H.265 instead.")
        codec = "H.265"

    if codec == "H.264":
        return "h264_nvenc" if use_nvenc else "libx264"

    if codec == "H.265":
        return "hevc_nvenc" if use_nvenc else "libx265"

    # "Auto": prefer H.265 (HEVC) which is the better default for this pipeline
    if use_nvenc:
        return "hevc_nvenc"
    return "libx265"


def quality_to_preset(quality: str, is_nvenc: bool) -> str:
    """Convert quality preset name to FFmpeg preset.

    Args:
        quality: Quality preset name (Fastest to Slowest)
        is_nvenc: Whether using NVENC encoder

    Returns:
        FFmpeg preset string
    """
    preset_map = NVENC_PRESET_MAP if is_nvenc else CPU_PRESET_MAP
    return preset_map.get(quality, "medium" if not is_nvenc else "p4")


def get_tune_flag(tune: str, codec: str) -> Optional[str]:
    """Get the FFmpeg tune flag.

    Args:
        tune: Tune name
        codec: Codec being used

    Returns:
        FFmpeg tune flag or None
    """
    if tune == "None" or not tune:
        return None

    if "nvenc" in codec:
        logger.debug("Tune is ignored when using NVENC encoder")
        return None

    tune_map = {
        "Film": "film",
        "Grain": "grain",
        "Animation": "animation",
        "Still Image": "stillimage",
        "PSNR": "psnr",
        "SSIM": "ssim",
        "Fast Decode": "fastdecode",
        "Zero Latency": "zerolatency",
    }
    return tune_map.get(tune)


def build_encoder_args(
    codec: str = "H.265",
    encoder: str = "Auto",
    quality: str = "Medium",
    tune: str = "None",
    crf: int = 23,
    force_10bit: bool = False,
    bit_depth: str = "Auto",
    pix_fmt_override: str = "Auto",
    nvenc_options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build encoding arguments for FFmpeg.

    Args:
        codec: Codec selection ("Auto", "H.264", or "H.265")
        encoder: Encoder selection ("Auto" or "Force CPU")
        quality: Quality preset ("Fastest" to "Slowest")
        tune: CPU tune option
        crf: CRF value for quality control
        force_10bit: Whether to force 10-bit output (e.g. from HDR color tag
            auto-detection). Ignored if `bit_depth` is explicitly set.
        bit_depth: "Auto", "8-bit", or "10-bit (Main10)". An explicit choice
            here overrides `force_10bit`, so a user can force 10-bit output
            (Main10 / p010le / yuv420p10le) even for 10-bit SDR sources that
            wouldn't otherwise trigger HDR-based auto-detection, or force
            8-bit even if HDR tags are present.
        nvenc_options: Optional dict with NVENC-specific options:
            - lookahead_enabled: bool
            - lookahead: int (frames)
            - spatial_aq: bool
            - temporal_aq: bool
            - aq_strength: int

    Returns:
        Dict with keys: codec, encoder, preset, tune, crf, pix_fmt, profile, extra_args
    """
    # An explicit bit_depth selection overrides HDR-driven force_10bit.
    if bit_depth == "10-bit (Main10)":
        force_10bit = True
    elif bit_depth == "8-bit":
        force_10bit = False

    # An explicit 10-bit pixel-format override implies 10-bit output too. This must
    # be resolved BEFORE codec selection below, otherwise get_encoder_codec() would
    # still treat the stream as 8-bit (skipping H.264->H.265 promotion) while the
    # pix_fmt and Main10 profile end up 10-bit -- producing a Main10-tagged stream
    # carrying 8-bit data (or a codec that can't encode the chosen pix_fmt).
    override_is_10bit = pix_fmt_override in ("yuv420p10le", "p010le")
    override_is_8bit = pix_fmt_override == "yuv420p"
    if override_is_10bit:
        force_10bit = True
    elif override_is_8bit:
        force_10bit = False

    codec_result = get_encoder_codec(codec, force_10bit, encoder)
    is_nvenc = "nvenc" in codec_result
    preset = quality_to_preset(quality, is_nvenc)
    tune_flag = get_tune_flag(tune, codec_result)

    pix_fmt = "yuv420p10le" if force_10bit else "yuv420p"

    # Explicit pixel format override (e.g. p010le, yuv420p10le) takes priority over auto
    if pix_fmt_override and pix_fmt_override != "Auto":
        pix_fmt = pix_fmt_override

    # NVENC requires p010le for 10-bit (it does not accept yuv420p10le)
    if is_nvenc and pix_fmt == "yuv420p10le":
        pix_fmt = "p010le"

    # CPU/libx265 cannot use p010le; map back to its 10-bit format if a GPU-style
    # override was chosen for a CPU encoder.
    if not is_nvenc and pix_fmt == "p010le":
        pix_fmt = "yuv420p10le"

    is_10bit_pix_fmt = pix_fmt in ("p010le", "yuv420p10le")

    # Main10 profile applies to HEVC (libx265 / hevc_nvenc) 10-bit output.
    # H.264 10-bit (High 10) is poorly supported by NVENC/most players, and
    # get_encoder_codec() already promotes H.264->H.265 when force_10bit is
    # set, so HEVC is the only codec we expect to see paired with 10-bit here.
    profile = "main10" if (is_10bit_pix_fmt and "hevc" in codec_result) else None

    extra_args = []

    if is_nvenc:
        extra_args.extend(["-qp", str(crf)])

        if nvenc_options:
            if nvenc_options.get("lookahead_enabled", False):
                la_frames = nvenc_options.get("lookahead", 16)
                extra_args.extend(["-rc-lookahead", str(la_frames)])

            if nvenc_options.get("spatial_aq", False):
                extra_args.extend(["-aq-strength", str(nvenc_options.get("aq_strength", 8))])
                extra_args.extend(["-spatial-aq", "1"])

            if nvenc_options.get("temporal_aq", False):
                extra_args.extend(["-aq-strength", str(nvenc_options.get("aq_strength", 8))])
                extra_args.extend(["-temporal-aq", "1"])
    else:
        extra_args.extend(["-crf", str(crf)])

    return {
        "codec": codec_result,
        "encoder": codec_result,
        "preset": preset,
        "tune": tune_flag,
        "pix_fmt": pix_fmt,
        "profile": profile,
        "extra_args": extra_args,
        "is_nvenc": is_nvenc,
    }


def get_encoding_config_from_dict(config: Dict[str, Any]) -> Dict[str, Any]:
    """Extract and normalize encoding config from a settings dict.

    Args:
        config: Settings dictionary that may contain encoding keys

    Returns:
        Normalized encoding config dict with defaults applied
    """
    result = DEFAULT_ENCODING_CONFIG.copy()

    if "codec" in config:
        result["codec"] = config["codec"]

    if "encoder" in config:
        result["encoder"] = config["encoder"]
    if "encoding_encoder" in config:
        result["encoder"] = config["encoding_encoder"]

    if "quality" in config:
        result["quality"] = config["quality"]
    if "encoding_quality" in config:
        result["quality"] = config["encoding_quality"]

    if "tune" in config:
        result["tune"] = config["tune"]
    if "encoding_tune" in config:
        result["tune"] = config["encoding_tune"]

    if "crf" in config:
        result["crf"] = int(config["crf"])
    if "output_crf" in config:
        result["crf"] = int(config["output_crf"])

    if "bit_depth" in config:
        result["bit_depth"] = config["bit_depth"]

    if "pix_fmt" in config:
        result["pix_fmt"] = config["pix_fmt"]

    if "nvenc_lookahead_enabled" in config:
        result["nvenc_lookahead_enabled"] = config["nvenc_lookahead_enabled"]
    if "nvenc_lookahead" in config:
        result["nvenc_lookahead"] = int(config["nvenc_lookahead"])
    if "nvenc_spatial_aq" in config:
        result["nvenc_spatial_aq"] = config["nvenc_spatial_aq"]
    if "nvenc_temporal_aq" in config:
        result["nvenc_temporal_aq"] = config["nvenc_temporal_aq"]
    if "nvenc_aq_strength" in config:
        result["nvenc_aq_strength"] = int(config["nvenc_aq_strength"])

    return result


def dnxhr_video_args(profile_label: str) -> List[str]:
    """ffmpeg's video arguments for a DNxHR profile label of the settings
    ("HQX (10-bit 4:2:2)", ...): the dnxhd encoder, its profile, its pixel format.
    SQ and HQ are 8-bit 4:2:2, HQX 10-bit 4:2:2, 444 10-bit 4:4:4."""
    key = (str(profile_label or "").strip().upper().split() or ["HQX"])[0]
    prof = {"SQ": "dnxhr_sq", "HQ": "dnxhr_hq", "HQX": "dnxhr_hqx", "444": "dnxhr_444"}.get(key, "dnxhr_hqx")
    pix = {"dnxhr_sq": "yuv422p", "dnxhr_hq": "yuv422p", "dnxhr_hqx": "yuv422p10le", "dnxhr_444": "yuv444p10le"}[prof]
    return ["-c:v", "dnxhd", "-profile:v", prof, "-pix_fmt", pix]


#: The containers a non-DNxHR output may be written in (user, 2026-10-25: Encoding Settings'
#: Container, MP4 or MKV); DNxHR always writes a .mov.
OUTPUT_CONTAINERS = ("MP4", "MKV")
#: Every container a stage writes -- what the next stage looks for among its inputs.
OUTPUT_EXTS = (".mp4", ".mkv", ".mov")


def output_ext(settings: Dict[str, Any]) -> str:
    """The extension a stage's output takes from its Encoding Settings: .mov with Enable DNxHR,
    else the Container's (.mp4 unless MKV)."""
    s = settings or {}
    if s.get("dnxhr_fullres_split"):
        return ".mov"
    return ".mkv" if str(s.get("output_container") or "").strip().upper() == "MKV" else ".mp4"


def video_args_from_settings(settings: Dict[str, Any]) -> "tuple[List[str], str]":
    """(ffmpeg video arguments, the file extension) for a stage's Encoding Settings:
    codec, encoding_encoder, encoding_quality, encoding_tune, bit_depth, pix_fmt,
    output_crf, the nvenc_* options -- or, with dnxhr_fullres_split on, DNxHR in a
    .mov (user, 2026-10-11: Inpainting V2 writes what the settings say, as V1 does).
    10-bit (Main10) gives HEVC Main 10 on NVENC and x265 alike."""
    s = settings or {}
    if s.get("dnxhr_fullres_split"):
        return dnxhr_video_args(s.get("dnxhr_profile", "")), ".mov"
    try:
        crf = int(float(s.get("output_crf", 23)))
    except (TypeError, ValueError):
        crf = 23
    a = build_encoder_args(
        codec=s.get("codec") or "H.265",
        encoder=s.get("encoding_encoder") or s.get("encoder") or "Auto",
        quality=s.get("encoding_quality") or "Medium",
        tune=s.get("encoding_tune") or "None",
        crf=crf,
        bit_depth=s.get("bit_depth") or "Auto",
        pix_fmt_override=s.get("pix_fmt") or "Auto",
        nvenc_options={
            "lookahead_enabled": bool(s.get("nvenc_lookahead_enabled", False)),
            "lookahead": int(s.get("nvenc_lookahead", 16) or 16),
            "spatial_aq": bool(s.get("nvenc_spatial_aq", False)),
            "temporal_aq": bool(s.get("nvenc_temporal_aq", False)),
            "aq_strength": int(s.get("nvenc_aq_strength", 8) or 8),
        },
    )
    args = ["-c:v", a["codec"]]
    if a.get("preset"):
        args += ["-preset", a["preset"]]
    if a.get("tune"):
        args += ["-tune", a["tune"]]
    args += list(a.get("extra_args") or [])
    args += ["-pix_fmt", a["pix_fmt"]]
    if a.get("profile"):
        args += ["-profile:v", a["profile"]]
    if a["codec"] == "libx265":
        args += ["-x265-params", "log-level=error"]
    return args, output_ext(s)
