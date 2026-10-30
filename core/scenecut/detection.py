"""detection.py -- shot-boundary detectors, ffmpeg probes and timecode maths.

Everything below the imports is enoky's SceneCutGUI (MIT) with the tkinter half
lifted out: the same detector registry, the same model loading, the same
inference loops, producing the same (start, end) Timecode pairs. Keeping it
byte-comparable to upstream is deliberate -- when SceneCutGUI moves, diffing it
against this file should show only what Studio changed.

What Studio changed, all of it here in the header:

  * The two vendored detectors live under third_party/ instead of beside the
    script, and are resolved from the repo root rather than from __file__'s
    parent, so the tool works the same launched standalone or inside Studio,
    from any working directory.
  * Default weights paths point at weights/SceneCut/, next to every other
    model this suite downloads, instead of a bare ./weights.
  * No logging configuration. Upstream reconfigured the root logger with ANSI
    colours at import; in Studio the shell owns the root logger and the console
    window captures it, and a module that hijacks it on import would take the
    other four stages' logging with it. Standalone, scenecut_gui.py sets up the
    same coloured handler it always had.

AutoShot and OmniShotCut import their own modules by bare name (``from linear
import Linear_``, ``from omnishotcut.engine import ...``), so their folders go
on sys.path before the import, as upstream did. Both are guarded: a missing
detector disables its entry in the registry and says why, rather than stopping
the tool from loading.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Tuple

logger = logging.getLogger(__name__)

#: Repo root -- core/scenecut/detection.py -> core/scenecut -> core -> root.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

from core import weights_paths  # noqa: E402

#: Where the Scene Cut checkpoints are expected: SceneCut/ in the models folder
#: (Settings > Studio > Models, 2026-10-20; core.weights_paths). Each detector
#: finds its own file at use time (DetectorSpec.weights_path).
WEIGHTS_DIR = weights_paths.find("SceneCut")


@weights_paths.on_change
def _models_moved() -> None:
    global WEIGHTS_DIR
    WEIGHTS_DIR = weights_paths.find("SceneCut")


#: TransNetV2's checkpoint. The transnetv2-pytorch package ships the same
#: weights (every tensor equal, checked 2026-10-20), so it needs no download.
TRANSNETV2_FILE = "transnetv2-pytorch-weights.pth"


def detector_weights(filename: str) -> Path:
    """Where a detector's checkpoint is: SceneCut/<filename> in the models folder (or
    <repo>/weights), else TransNetV2's own copy in its package, else where it would go."""
    path = weights_paths.find("SceneCut", filename)
    if not path.is_file() and filename == TRANSNETV2_FILE:
        try:
            import importlib.util
            spec = importlib.util.find_spec("transnetv2_pytorch")
            bundled = Path(spec.origin).parent / filename if spec and spec.origin else None
            if bundled is not None and bundled.is_file():
                return bundled
        except Exception:
            pass
    return path


# ANSI colour helpers for the keep/merge lines the validator prints.
KEEP_FG, MERG_FG, BOLD, RESET = "\033[94m", "\033[92m", "\033[1m", "\033[0m"

# ------------------------------------------------------------------ #
# Torch dependency
# ------------------------------------------------------------------ #
TORCH_IMPORT_ERROR = None
try:
    import torch  # noqa: F401
except Exception as e:
    torch = None
    TORCH_IMPORT_ERROR = e
    logger.error("Failed to import torch. Please ensure PyTorch is installed: %s", e)

# ------------------------------------------------------------------ #
# AutoShot dependency (vendored: third_party/AutoShot)
# ------------------------------------------------------------------ #
AUTO_SHOT_IMPORT_ERROR = None
AutoShotNet = None
try:
    if torch is None:
        raise RuntimeError("torch is not available")
    AUTO_SHOT_DIR = _REPO_ROOT / "third_party" / "AutoShot"
    if AUTO_SHOT_DIR.exists() and str(AUTO_SHOT_DIR) not in sys.path:
        sys.path.insert(0, str(AUTO_SHOT_DIR))
    from supernet_flattransf_3_8_8_8_13_12_0_16_60 import TransNetV2Supernet as AutoShotNet
except Exception as e:
    AUTO_SHOT_IMPORT_ERROR = e
    logger.error(
        "Failed to import AutoShot model. Ensure third_party/AutoShot is present and 'einops' is installed: %s",
        e,
    )

# ------------------------------------------------------------------ #
# TransNetV2 dependency (pip: transnetv2-pytorch)
# ------------------------------------------------------------------ #
TRANSNET_IMPORT_ERROR = None
TransNetV2 = None
try:
    if torch is None:
        raise RuntimeError("torch is not available")
    from transnetv2_pytorch import TransNetV2
except Exception as e:
    TRANSNET_IMPORT_ERROR = e
    logger.error(
        "Failed to import TransNetV2 (transnetv2-pytorch). Please ensure it is installed: %s",
        e,
    )

# ------------------------------------------------------------------ #
# OmniShotCut dependency (vendored: third_party/OmniShotCut)
# ------------------------------------------------------------------ #
OMNISHOT_IMPORT_ERROR = None
omnishotcut = None
try:
    if torch is None:
        raise RuntimeError("torch is not available")
    OMNI_SHOT_DIR = _REPO_ROOT / "third_party" / "OmniShotCut"
    if OMNI_SHOT_DIR.exists() and str(OMNI_SHOT_DIR) not in sys.path:
        sys.path.insert(0, str(OMNI_SHOT_DIR))
    import omnishotcut  # noqa: F401
except Exception as e:
    OMNISHOT_IMPORT_ERROR = e
    logger.error(
        "Failed to import OmniShotCut. Ensure third_party/OmniShotCut is present and "
        "'ffmpeg-python' / 'huggingface_hub' are installed: %s",
        e,
    )

# ------------------------------------------------------------------ #
# Minimal timecode abstraction (minimal timecode abstraction for this GUI)
# ------------------------------------------------------------------ #
@dataclass(frozen=True)
class Timecode:
    """Frame-based timecode with helpers compatible with this GUI."""
    frame: int
    fps: float

    def get_frames(self) -> int:
        return int(self.frame)

    def get_seconds(self) -> float:
        if not self.fps:
            return 0.0
        return float(self.frame) / float(self.fps)


def _format_hhmmss_ms(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    ms = int(round((seconds - int(seconds)) * 1000.0))
    total = int(seconds)
    s = total % 60
    m = (total // 60) % 60
    h = total // 3600
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _parse_fraction(frac: str) -> float:
    """Parse ffprobe r_frame_rate / avg_frame_rate (e.g., '30000/1001')."""
    try:
        if not frac:
            return 0.0
        if "/" in frac:
            num, den = frac.split("/", 1)
            num_f = float(num)
            den_f = float(den)
            return num_f / den_f if den_f else 0.0
        return float(frac)
    except Exception:
        return 0.0


def _parse_fraction_exact(frac: str) -> tuple[int, int]:
    """Parse an ffprobe rational ('30000/1001') into an exact (num, den) pair.

    Returns (0, 0) when the value cannot be interpreted, so callers can fall
    back to a float frame rate.
    """
    try:
        if not frac:
            return (0, 0)
        if "/" in frac:
            num, den = frac.split("/", 1)
            num_i, den_i = int(num), int(den)
            return (num_i, den_i) if num_i > 0 and den_i > 0 else (0, 0)
        num_i = int(round(float(frac) * 1000))
        return (num_i, 1000) if num_i > 0 else (0, 0)
    except Exception:
        return (0, 0)


# How far before a scene start to place the fast input seek. Correctness does
# not depend on this (input seeking always lands on a keyframe at or before the
# target); it only absorbs imprecise container indexes.
SEEK_MARGIN_SEC = 0.5


@functools.lru_cache(maxsize=1)
def has_nvenc_hevc() -> bool:
    """True when this FFmpeg build exposes the NVIDIA hevc_nvenc encoder."""
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-hide_banner", "-encoders"],
            capture_output=True, text=True, creationflags=creationflags, check=False,
        )
        return "hevc_nvenc" in (result.stdout or "")
    except FileNotFoundError:
        return False


def verify_cfr(video_path: str, fps_num: int, fps_den: int) -> bool:
    """Check that every video packet actually sits on the constant-frame-rate grid.

    Comparing r_frame_rate to avg_frame_rate is not enough: containers routinely
    report both as a clean value (e.g. 30/1) for material whose real timestamps
    are irregular, and acting on that false positive makes timestamp-based
    trimming select the wrong frames. Reading packet timestamps settles it - this
    inspects the container index only and never decodes.
    """
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "packet=pts_time", "-of", "csv=p=0", video_path],
            capture_output=True, text=True, creationflags=creationflags, check=False,
        )
        if result.returncode != 0:
            return False
        stamps = []
        for line in (result.stdout or "").split():
            try:
                stamps.append(float(line))
            except ValueError:
                # "N/A" means the container has no usable timestamp for a packet,
                # which by itself rules out timestamp-based trimming.
                return False
        stamps.sort()
    except FileNotFoundError:
        return False

    if len(stamps) < 2:
        return False
    # Packets arrive in decode order, so sort into presentation order first, then
    # require every frame within half a frame of its ideal slot.
    frame_dur = fps_den / fps_num
    tolerance = 0.5 * frame_dur
    origin = stamps[0]
    for i, pts in enumerate(stamps):
        if abs(pts - (origin + i * frame_dur)) > tolerance:
            return False
    return True


def can_hwdecode(video_path: str) -> bool:
    """Test whether this source can run the full NVDEC -> CUDA -> NVENC pipeline.

    NVDEC does not cover every codec and chroma layout (4:2:2, ProRes, DNxHD and
    AV1 on older GPUs are common gaps), so rather than guess from the codec name
    we run the real filter chain over a single frame and see if it survives.
    """
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-hide_banner",
             "-hwaccel", "cuda", "-hwaccel_output_format", "cuda",
             "-i", video_path, "-frames:v", "1",
             "-filter_complex", "[0:v]scale_cuda=format=p010le[v]", "-map", "[v]",
             "-c:v", "hevc_nvenc", "-f", "null", "-"],
            capture_output=True, text=True, creationflags=creationflags, check=False,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def probe_stream_props(video_path: str) -> dict:
    """Probe the properties needed for a frame-exact, colour-faithful re-encode."""
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    props: dict = {"has_audio": False, "fps_num": 0, "fps_den": 0,
                   "start_time": 0.0, "is_cfr": False}
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "stream=codec_type,r_frame_rate,avg_frame_rate,start_time,"
             "color_range,color_space,color_transfer,color_primaries",
             "-of", "json", video_path],
            capture_output=True, text=True, creationflags=creationflags, check=False,
        )
        if result.returncode != 0:
            return props
        for stream in json.loads(result.stdout or "{}").get("streams", []):
            if stream.get("codec_type") == "audio":
                props["has_audio"] = True
            elif stream.get("codec_type") == "video" and not props["fps_num"]:
                r_num, r_den = _parse_fraction_exact(stream.get("r_frame_rate", ""))
                props["fps_num"], props["fps_den"] = r_num, r_den
                try:
                    props["start_time"] = max(0.0, float(stream.get("start_time") or 0.0))
                except (TypeError, ValueError):
                    props["start_time"] = 0.0
                # Frame index <-> timestamp arithmetic is only valid on constant
                # frame rate material. The declared rates agreeing is a cheap
                # prerequisite but NOT proof - containers report a clean
                # r_frame_rate/avg_frame_rate pair for genuinely irregular
                # material - so confirm against real packet timestamps.
                a_num, a_den = _parse_fraction_exact(stream.get("avg_frame_rate", ""))
                rates_agree = bool(
                    r_num and a_num and abs(r_num / r_den - a_num / a_den) < 1e-6
                )
                props["is_cfr"] = rates_agree and verify_cfr(video_path, r_num, r_den)
                for key in ("color_range", "color_space", "color_transfer", "color_primaries"):
                    value = stream.get(key)
                    if value and value != "unknown":
                        props[key] = value
    except (FileNotFoundError, ValueError):
        pass
    return props


def probe_frame_count(video_path: str) -> int:
    """Container-reported frame count of a video (0 when unavailable)."""
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
             "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", video_path],
            capture_output=True, text=True, creationflags=creationflags, check=False,
        )
        return int((result.stdout or "0").strip() or 0)
    except (FileNotFoundError, ValueError):
        return 0


def get_video_info(video_path: str) -> tuple[float, int]:
    """Return (fps, total_frames) with OpenCV and ffprobe fallback."""
    try:
        import cv2
        cap = cv2.VideoCapture(video_path)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        cap.release()
        if fps > 0.0 and total_frames > 0:
            return fps, total_frames
    except Exception:
        pass

    # ffprobe fallback (more reliable for some formats)
    try:
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=avg_frame_rate,r_frame_rate,nb_frames,duration",
                "-of",
                "json",
                video_path,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(probe.stdout)
        stream = (data.get("streams") or [{}])[0]
        fps = _parse_fraction(stream.get("avg_frame_rate") or "") or _parse_fraction(stream.get("r_frame_rate") or "")
        nb = stream.get("nb_frames")
        total_frames = int(nb) if nb and str(nb).isdigit() else 0
        if total_frames <= 0:
            dur = float(stream.get("duration") or 0.0)
            if fps > 0.0 and dur > 0.0:
                total_frames = int(round(dur * fps))
        if fps > 0.0 and total_frames > 0:
            return fps, total_frames
    except Exception:
        pass

    raise RuntimeError("Could not determine FPS / frame count for the selected video.")


# ------------------------------------------------------------------ #
# Detector registry
# ------------------------------------------------------------------ #
# Each detector declares the parameters it exposes, its default weights file and
# how to report unavailability. The GUI builds its combobox, its parameter
# widgets and its settings round-trip from this table, so adding a detector
# means adding an entry here plus a branch in _detection_and_output_task.


def _validate_threshold(value: float) -> None:
    if not (0.0 < value < 1.0):
        raise ValueError("threshold must be between 0 and 1.")


def _cast_min_scene_len(text: str) -> int:
    return max(1, int(float(text)))


def _cast_overlap(text: str) -> int:
    value = int(float(text))
    if value < 0:
        raise ValueError("overlap must be 0 or greater.")
    return value


def _validate_confidence(value: float) -> None:
    # 0.0 keeps every boundary the model proposes (upstream behaviour); 1.0 would
    # reject all of them, so it is excluded.
    if not (0.0 <= value < 1.0):
        raise ValueError("confidence must be between 0 and 1 (0 disables filtering).")


@dataclass(frozen=True)
class DetectorParam:
    """One parameter widget on the detector panel."""
    key: str
    label: str
    default: str
    tooltip: str
    values: Tuple[str, ...] = ()          # non-empty -> readonly combobox
    cast: Callable[[str], object] = str
    validate: Optional[Callable[[object], None]] = None


@dataclass(frozen=True)
class DetectorSpec:
    name: str
    weights_file: str
    params: Tuple[DetectorParam, ...]
    unavailable_reason: Callable[[], Optional[str]]

    @property
    def weights_path(self) -> str:
        """Looked up when used: the models folder can change while Studio runs."""
        return str(detector_weights(self.weights_file))


_DEVICE_PARAM = DetectorParam(
    key="device", label="device:", default="auto", tooltip="device",
    values=("auto", "cpu", "cuda", "mps"),
)
_MIN_SCENE_LEN_PARAM = DetectorParam(
    key="min_scene_len", label="min_scene_len (frames):", default="12",
    tooltip="min_scene_len", cast=_cast_min_scene_len,
)


def _threshold_param(default: str) -> DetectorParam:
    return DetectorParam(
        key="threshold", label="threshold:", default=default, tooltip="threshold",
        cast=float, validate=_validate_threshold,
    )


def _autoshot_unavailable() -> Optional[str]:
    if AutoShotNet is not None:
        return None
    detail = f"\n\nImport error: {AUTO_SHOT_IMPORT_ERROR}" if AUTO_SHOT_IMPORT_ERROR else ""
    return "AutoShot is not available. Ensure the AutoShot repo exists and install 'einops'." + detail


def _transnetv2_unavailable() -> Optional[str]:
    if TransNetV2 is not None:
        return None
    detail = f"\n\nImport error: {TRANSNET_IMPORT_ERROR}" if TRANSNET_IMPORT_ERROR else ""
    return "TransNetV2 is not available. Ensure 'transnetv2-pytorch' is installed." + detail


def check_omnishot_room(total_frames: int, width: int, height: int, folder: str,
                        margin: int = 512 * 2 ** 20) -> None:
    """OmniShotCut decodes the whole clip into one raw temp file (frames x
    width x height x 3 bytes): refuse, before decoding, when `folder` has not
    the room -- a full disk failed it mid-decode (Studio audit 2026-09-28).
    A length not known (0) is not checked."""
    import shutil
    if total_frames <= 0:
        return
    need = int(total_frames) * int(width) * int(height) * 3
    free = shutil.disk_usage(folder).free
    if need + margin > free:
        raise OSError(
            f"OmniShotCut needs about {need / 2 ** 30:.1f} GB free in {folder} to decode this clip "
            f"({total_frames} frames); only {free / 2 ** 30:.1f} GB is free. Free some space, or point "
            f"TEMP at a larger drive, and try again.")


def _omnishotcut_unavailable() -> Optional[str]:
    if omnishotcut is not None:
        return None
    detail = f"\n\nImport error: {OMNISHOT_IMPORT_ERROR}" if OMNISHOT_IMPORT_ERROR else ""
    return (
        "OmniShotCut is not available. Ensure the OmniShotCut folder is present and "
        "'ffmpeg-python' / 'huggingface_hub' are installed." + detail
    )


DETECTORS: dict[str, DetectorSpec] = {
    "AutoShot": DetectorSpec(
        name="AutoShot",
        weights_file="ckpt_0_200_0.pth",
        params=(_DEVICE_PARAM, _threshold_param("0.24"), _MIN_SCENE_LEN_PARAM),
        unavailable_reason=_autoshot_unavailable,
    ),
    "TransNetV2": DetectorSpec(
        name="TransNetV2",
        weights_file=TRANSNETV2_FILE,
        params=(_DEVICE_PARAM, _threshold_param("0.3"), _MIN_SCENE_LEN_PARAM),
        unavailable_reason=_transnetv2_unavailable,
    ),
    # OmniShotCut has no frame-difference threshold like the other detectors: it
    # localises boundaries with a query head. Its equivalent dial is how sharply
    # that head pins down the cut's frame, exposed here as "confidence" — see
    # _omnishot_predict_windows for why that and not the no-object class.
    "OmniShotCut": DetectorSpec(
        name="OmniShotCut",
        weights_file="OmniShotCut_ckpt.pth",
        params=(
            _DEVICE_PARAM,
            DetectorParam(
                key="mode", label="mode:", default="clean_shot", tooltip="omnishot_mode",
                values=("clean_shot", "default"),
            ),
            DetectorParam(
                key="confidence", label="min confidence:", default="0.0",
                tooltip="omnishot_confidence", cast=float, validate=_validate_confidence,
            ),
            DetectorParam(
                key="overlap", label="overlap (frames):", default="20",
                tooltip="omnishot_overlap", cast=_cast_overlap,
            ),
            _MIN_SCENE_LEN_PARAM,
        ),
        unavailable_reason=_omnishotcut_unavailable,
    ),
}


def short_scene_guard_frames(fps: float, guard_seconds: float = 0.30) -> int:
    """Return frame length below which cuts are preserved (default 0.30s)."""
    if fps <= 0:
        return 8
    return max(2, int(round(float(fps) * float(guard_seconds))))


def transnetv2_scenes_to_timecodes(
    tn_scenes: list,
    fps: float,
    total_frames: int,
) -> list[tuple[Timecode, Timecode]]:
    """Convert TransNetV2 output dicts into [(start_tc, end_tc_excl), ...]."""
    scenes_out: list[tuple[Timecode, Timecode]] = []

    # Sort by start frame to be safe
    tn_scenes.sort(key=lambda x: int(x.get("start_frame", 0)) if "start_frame" in x else float(x.get("start_time", 0.0)))

    last_end = 0
    for s in tn_scenes:
        if isinstance(s, dict) and "start_frame" in s and "end_frame" in s:
            start_f = int(s["start_frame"])
            end_f = int(s["end_frame"])
        else:
            start_t = float(s.get("start_time", 0.0)) if isinstance(s, dict) else 0.0
            end_t = float(s.get("end_time", 0.0)) if isinstance(s, dict) else 0.0
            start_f = int(round(start_t * fps))
            end_f = int(round(end_t * fps))

        start_f = max(0, min(start_f, total_frames))
        end_f = max(start_f + 1, min(end_f, total_frames))

        # Enforce continuity:
        # If there is a gap between last_end and this start_f, the gap is likely a transition.
        # We can either:
        # 1. Extend previous scene to start_f (last_end -> start_f)
        # 2. Start this scene at last_end (start_f -> last_end)
        # 3. Split the gap.
        #
        # Simple robust approach: "Scene ends where next starts".
        # So we fix PREVIOUS scene's end to be CURRENT scene's start.

        if scenes_out:
            prev_start_tc, _ = scenes_out[-1]
            # Update previous scene end to meet current start
            # But ensure we don't shrink it to zero/negative
            new_prev_end = max(prev_start_tc.get_frames() + 1, start_f)
            scenes_out[-1] = (prev_start_tc, Timecode(new_prev_end, fps))

            # Now current scene starts exactly where previous ended
            start_f = new_prev_end
        else:
            # First scene always starts at 0
            start_f = 0

        end_f = max(end_f, start_f + 1)
        last_end = end_f

        scenes_out.append((Timecode(start_f, fps), Timecode(end_f, fps)))

    # If TransNet didn't cover the tail, extend last scene
    if scenes_out and scenes_out[-1][1].get_frames() < total_frames:
        scenes_out[-1] = (scenes_out[-1][0], Timecode(total_frames, fps))

    # If it returned nothing, create a single full-length scene
    if not scenes_out and total_frames > 0:
        scenes_out = [(Timecode(0, fps), Timecode(total_frames, fps))]

    return scenes_out


def _autoshot_select_device(requested: str) -> str:
    if requested in {"auto", "", None}:
        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
        return "cpu"
    if requested == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but not available.")
    if requested == "mps" and not (getattr(torch.backends, "mps", None) and torch.backends.mps.is_available()):
        raise RuntimeError("MPS requested but not available.")
    return str(requested)


def _autoshot_load_model(weights_path: str, device: str):
    if torch is None:
        raise RuntimeError(f"PyTorch is unavailable: {TORCH_IMPORT_ERROR}")
    if AutoShotNet is None:
        raise RuntimeError(f"AutoShot is unavailable: {AUTO_SHOT_IMPORT_ERROR}")
    if not os.path.exists(weights_path):
        raise FileNotFoundError(f"AutoShot weights file not found: {weights_path}\n\n"
                                "Settings > Studio > Models has its download page (Get It).")

    device = _autoshot_select_device(device)
    model = AutoShotNet().eval()

    ckpt = torch.load(weights_path, map_location=device)
    if isinstance(ckpt, dict) and "net" in ckpt:
        ckpt = ckpt["net"]

    model_dict = model.state_dict()
    pretrained = {k: v for k, v in ckpt.items() if k in model_dict}
    if not pretrained:
        raise RuntimeError("AutoShot checkpoint did not match model parameters.")
    model_dict.update(pretrained)
    model.load_state_dict(model_dict)
    model.to(device)
    model.eval()
    return model, device


def _autoshot_frame_generator(video_path: str, width: int = 48, height: int = 27):
    import numpy as np

    frame_size = width * height * 3
    cmd = [
        "ffmpeg", "-v", "error",
        "-fps_mode", "passthrough",  # Studio, 2026-10-30: -vsync is gone from newer FFmpeg builds
        "-i", video_path,
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{width}x{height}", "-"
    ]

    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=creationflags
    )

    try:
        while True:
            # Read a chunk of frames to reduce overhead (50 frames ~ 200KB)
            chunk_size = 50 * frame_size
            raw = process.stdout.read(chunk_size)
            if not raw:
                break

            n_bytes = len(raw)
            n_frames = n_bytes // frame_size
            if n_frames == 0:
                break

            frames = np.frombuffer(raw[:n_frames*frame_size], dtype=np.uint8).reshape((n_frames, height, width, 3))
            yield frames

            if n_bytes < chunk_size:
                break
    except Exception as e:
        logger.error(f"FFmpeg streaming error: {e}")
        raise
    finally:
        if process.stdout: process.stdout.close()
        if process.stderr: process.stderr.close()
        process.wait()




def _find_peak_cuts(predictions: list, threshold: float) -> list[int]:
    """Find local maxima (peaks) in contiguous regions above threshold."""
    import numpy as np

    pred = np.asarray(predictions).reshape(-1)
    # Identify regions above threshold
    is_candidate = (pred > threshold).astype(np.int32)

    # Pad to handle edge cases
    padded = np.pad(is_candidate, (1, 1), mode='constant', constant_values=0)
    diff = np.diff(padded)

    # Start and end indices of candidate regions
    starts = np.where(diff == 1)[0]
    ends = np.where(diff == -1)[0]

    cuts = []
    for s, e in zip(starts, ends):
        # Look at the original predictions in this range [s, e)
        # We need the relative index of max value within the slice
        region = pred[s:e]
        if len(region) == 0: continue

        peak_idx = np.argmax(region)
        # The absolute frame index of the cut
        cut_frame = s + peak_idx
        cuts.append(cut_frame)

    return cuts


def autoshot_predictions_to_timecodes(
    predictions,
    fps: float,
    total_frames: int,
    threshold: float,
) -> list[tuple[Timecode, Timecode]]:
    import numpy as np

    pred = np.asarray(predictions).reshape(-1)
    if total_frames <= 0:
        total_frames = len(pred)

    # Ensure prediction array matches total_frames length
    if len(pred) < total_frames:
        pred = np.pad(pred, (0, total_frames - len(pred)), mode="constant", constant_values=0)
    pred = pred[:total_frames]

    # Find peaks to determine exact cut locations
    cut_frames = _find_peak_cuts(pred, threshold)

    # Construct contiguous scenes from cuts
    # Format: [0, cut1), [cut1, cut2), ... [cutN, total_frames)
    scene_boundaries = [0] + sorted(cut_frames)

    # Ensure the last boundary is total_frames if not already
    # If the last cut was somehow at total_frames, we don't want a 0-length scene
    if scene_boundaries[-1] >= total_frames:
        scene_boundaries[-1] = total_frames
    else:
        scene_boundaries.append(total_frames)

    # Filter unique (in case 0 was a cut) and sort
    scene_boundaries = sorted(list(set(scene_boundaries)))

    scenes_out: list[tuple[Timecode, Timecode]] = []

    for i in range(len(scene_boundaries) - 1):
        start_f = scene_boundaries[i]
        end_f = scene_boundaries[i+1]

        # Skip empty scenes (though set/sorted should prevent this unless total_frames=0)
        if end_f <= start_f:
            continue

        scenes_out.append((Timecode(start_f, fps), Timecode(end_f, fps)))

    return scenes_out


def _autoshot_predict_from_stream(frame_gen, model, device, total_frames_est=None, abort_flag=None, progress_cb=None):
    import numpy as np

    buffer = []
    predictions = []

    real_frames_count = 0
    first_frame_seen = False

    with torch.no_grad():
        for chunk in frame_gen:
            if abort_flag and abort_flag.is_set():
                raise InterruptedError

            # Convert chunk (N,H,W,C) to list of frames
            # Using list of arrays is flexible
            frames_list = list(chunk)
            chunk_len = len(frames_list)
            real_frames_count += chunk_len

            if not first_frame_seen and chunk_len > 0:
                 # Pre-pad with 25 copies of start
                 buffer.extend([frames_list[0]] * 25)
                 first_frame_seen = True

            buffer.extend(frames_list)

            # Process complete batches (sliding window)
            while len(buffer) >= 100:
                if abort_flag and abort_flag.is_set():
                    raise InterruptedError

                batch_frames = buffer[:100]
                batch_np = np.stack(batch_frames)

                batch_t = torch.from_numpy(
                    batch_np.transpose((3, 0, 1, 2))[np.newaxis, ...]
                ).float().to(device)

                one_hot = model(batch_t)
                if isinstance(one_hot, tuple): one_hot = one_hot[0]
                one_hot = torch.sigmoid(one_hot[0]).squeeze(-1)

                predictions.append(one_hot[25:75].detach().cpu().numpy())

                # Slide by 50
                del buffer[:50]

                if progress_cb and total_frames_est:
                     processed_so_far = len(predictions) * 50
                     progress_cb(min(0.95, processed_so_far / total_frames_est), f"AutoShot inference ({processed_so_far} frames)")

    if real_frames_count == 0:
        raise RuntimeError("AutoShot received no frames.")

    # Flush remaining buffer with padding
    if buffer:
        last_frame = buffer[-1]
        # Pad enough to flush pending frames through the center window
        buffer.extend([last_frame] * 100)

        while len(buffer) >= 100:
            batch_frames = buffer[:100]
            batch_np = np.stack(batch_frames)

            batch_t = torch.from_numpy(
                batch_np.transpose((3, 0, 1, 2))[np.newaxis, ...]
            ).float().to(device)

            one_hot = model(batch_t)
            if isinstance(one_hot, tuple): one_hot = one_hot[0]
            one_hot = torch.sigmoid(one_hot[0]).squeeze(-1)

            predictions.append(one_hot[25:75].detach().cpu().numpy())
            del buffer[:50]

            # Stop if we have generated enough predictions to cover real_frames_count
            if len(predictions) * 50 >= real_frames_count + 50:
                 break

    final_preds = np.concatenate(predictions, 0)
    return final_preds[:real_frames_count], real_frames_count

def _omnishot_decode_to_memmap(video_path: str, width: int, height: int, raw_path: str,
                               abort_flag=None, progress_cb=None, total_frames_est=None):
    """Decode a video to a raw rgb24 temp file and return it as a memmap (T,H,W,3).

    OmniShotCut's own _decode_video() materialises the whole video in RAM at the
    model's process resolution (~36 KB/frame, so ~4 GB for an hour). Writing to
    disk and memory-mapping keeps peak RAM flat, lets the OS page in only the
    window under inference, and gives us somewhere to check the abort flag.
    """
    import numpy as np

    frame_bytes = width * height * 3
    frames_written = 0

    with open(raw_path, "wb") as raw:
        for chunk in _autoshot_frame_generator(video_path, width=width, height=height):
            if abort_flag is not None and abort_flag.is_set():
                raise InterruptedError
            raw.write(np.ascontiguousarray(chunk).tobytes())
            frames_written += len(chunk)
            if progress_cb and total_frames_est:
                progress_cb(min(1.0, frames_written / total_frames_est),
                            f"Decoding for OmniShotCut ({frames_written} frames)")

    if frames_written == 0:
        raise RuntimeError("OmniShotCut received no frames.")

    video_np = np.memmap(raw_path, dtype=np.uint8, mode="r",
                         shape=(frames_written, height, width, 3))
    return video_np, frames_written


def _omnishot_predict_windows(video_np, model, model_args, overlap: int,
                              confidence: float = 0.0,
                              abort_flag=None, progress_cb=None):
    """Run OmniShotCut window-by-window.

    Returns (ranges, intra_labels, inter_labels, confidences).

    This mirrors omnishotcut.engine._run_on_numpy but reuses that module's own
    split_videos()/merge_predictions() so the windows and valid regions are
    identical by construction, while adding per-window abort, progress and
    confidence filtering.

    `confidence` is a minimum per-boundary localisation score in [0, 1), taken as
    the peak of the range head's softmax over frame positions.

    The intra head does have a trailing no-object column (10 outputs for 9 real
    labels), but measuring it on the shipped checkpoint shows p(no-object) is
    ~0 for every query, so it carries no signal. This model rejects a query a
    different way: idle queries emit range_idx == window_length with intra label
    "padding", which the degenerate start >= end check below already discards.
    What remains variable is how sharply the range head localises the boundary it
    did propose, and that is what we threshold on. 0.0 reproduces upstream
    behaviour exactly.
    """
    from omnishotcut.engine import video_transform, split_videos, merge_predictions

    window = int(model_args.max_process_window_length)
    if not (0 <= overlap < window):
        raise ValueError(f"overlap must be between 0 and {window - 1} (model window length).")
    if not (0.0 <= confidence < 1.0):
        raise ValueError("confidence must be between 0 and 1 (0 disables filtering).")

    device = next(model.parameters()).device
    windows = split_videos(video_np, window, overlap)
    pred_boundary_full: list = []
    seen_confidences: list[float] = []
    num_rejected = 0

    for idx, (chunk, _num_pad, window_start, valid_start, valid_end, valid_len) in enumerate(windows):
        if abort_flag is not None and abort_flag.is_set():
            raise InterruptedError

        # np.asarray materialises just this window from the memmap.
        import numpy as np
        video_tensor = video_transform(np.asarray(chunk)).unsqueeze(0).to(device)

        with torch.inference_mode():
            outputs = model(video_tensor)

        query_intra_idx = outputs["intra_clip_logits"].softmax(-1)[0, :, :-1].argmax(dim=-1)
        query_inter_idx = outputs["inter_clip_logits"].softmax(-1)[0, :, :-1].argmax(dim=-1)

        # Peak of the range head doubles as the boundary's confidence: a flat
        # distribution means the model is unsure where the cut actually falls.
        range_probs = outputs["pred_shot_logits"].softmax(-1)[0, :, :-1]
        range_peak = range_probs.max(dim=-1)
        query_range_idx = range_peak.indices
        query_conf = range_peak.values.detach().cpu()

        pred_boundary = []
        start_local = 0
        for keep_idx in range(len(query_intra_idx)):
            end_local = min(int(query_range_idx[keep_idx].detach().cpu()), valid_len)
            if start_local >= end_local:
                continue

            end_global = window_start + end_local
            if valid_start < end_global <= valid_end:
                conf = float(query_conf[keep_idx])
                seen_confidences.append(conf)
                if conf >= confidence:
                    pred_boundary.append({
                        "end_frame_idx": int(end_global),
                        "intra_label": int(query_intra_idx[keep_idx].detach().cpu()),
                        "inter_label": int(query_inter_idx[keep_idx].detach().cpu()),
                        "confidence": conf,
                    })
                else:
                    # Dropping the boundary merges this segment into the next one,
                    # because ranges are rebuilt contiguously from what survives.
                    num_rejected += 1

            # Advance regardless: start_local chains the queries' local ranges, so
            # it must track the model's segmentation even where we reject a boundary.
            start_local = end_local
            if end_local >= valid_len:
                break

        pred_boundary_full = merge_predictions(pred_boundary_full, pred_boundary)

        if progress_cb:
            progress_cb((idx + 1) / len(windows),
                        f"OmniShotCut inference (window {idx + 1}/{len(windows)})")

    # Boundaries -> contiguous ranges, identical to upstream's final pass.
    ranges, intra_labels, inter_labels, confidences = [], [], [], []
    start_frame_idx = 0
    for item in pred_boundary_full:
        end_frame_idx = min(int(item["end_frame_idx"]), len(video_np))
        if end_frame_idx <= start_frame_idx:
            continue
        ranges.append([int(start_frame_idx), int(end_frame_idx)])
        intra_labels.append(int(item["intra_label"]))
        inter_labels.append(int(item["inter_label"]))
        confidences.append(float(item.get("confidence", 1.0)))
        start_frame_idx = end_frame_idx

    # Log the distribution so the threshold can be calibrated against a real run
    # instead of guessed at.
    if seen_confidences:
        ordered = sorted(seen_confidences)
        quantiles = {
            f"p{q}": round(ordered[min(len(ordered) - 1, int(len(ordered) * q / 100))], 4)
            for q in (5, 25, 50, 75, 95)
        }
        logger.info(
            "OmniShotCut confidence over %d proposed boundaries: %s (rejected %d at confidence>=%.3f)",
            len(seen_confidences), quantiles, num_rejected, confidence,
        )

    return ranges, intra_labels, inter_labels, confidences
