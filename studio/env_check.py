"""env_check.py -- what this PC is missing for Studio, said once at start-up (release check, 2026-10-17).

A first-time user on a PC without an NVIDIA card, with a driver too old for Studio's PyTorch (built for
CUDA 13: driver 580 or newer), or without FFmpeg on PATH found out only when a job failed -- with "the model
could not be loaded" or "[WinError 2]". Here each is found before any job and said in plain words.

The driver is read through NVML (nvml.dll, the driver's own library, as job_stats' VRAM meter does): no
torch import, no CUDA context, nothing on the card. No Qt here.
"""

from __future__ import annotations

import ctypes
import shutil
import sys
from typing import Callable, List, Optional, Tuple

#: The oldest NVIDIA driver Studio's PyTorch (cu130) runs on.
MIN_DRIVER = 580


def nvidia_driver() -> Optional[str]:
    """The NVIDIA driver's version ("581.57"), or None: no NVIDIA driver (or no NVIDIA card) on this PC."""
    if not sys.platform.startswith("win"):
        return None
    try:
        nv = ctypes.WinDLL("nvml.dll")
    except OSError:
        return None
    try:
        if nv.nvmlInit_v2() != 0:
            return None
    except Exception:
        return None
    try:
        count = ctypes.c_uint(0)
        if nv.nvmlDeviceGetCount_v2(ctypes.byref(count)) != 0 or count.value == 0:
            return None
        buf = ctypes.create_string_buffer(96)
        if nv.nvmlSystemGetDriverVersion(buf, ctypes.c_uint(len(buf))) != 0:
            return None
        return buf.value.decode("ascii", "replace").strip() or None
    except Exception:
        return None
    finally:
        try:
            nv.nvmlShutdown()
        except Exception:
            pass


def driver_major(version: Optional[str]) -> Optional[int]:
    """581.57 -> 581; None when it can't be read."""
    try:
        return int(str(version).split(".")[0])
    except (TypeError, ValueError):
        return None


def problems(driver: Callable[[], Optional[str]] = nvidia_driver,
             which: Callable[[str], Optional[str]] = shutil.which) -> List[Tuple[str, str]]:
    """[(title, what to do)] for what this PC is missing; [] when it has all of it."""
    out: List[Tuple[str, str]] = []
    version = driver()
    if version is None:
        out.append(("No NVIDIA graphics card found",
                    "Studio's stages run on an NVIDIA graphics card (RTX 20 series or newer) with driver "
                    f"{MIN_DRIVER} or newer. Without one, DepthCrafter, Splatting, Inpainting and Merging can't "
                    "run. If this PC has one, install the latest NVIDIA driver and restart Studio."))
    else:
        major = driver_major(version)
        if major is not None and major < MIN_DRIVER:
            out.append(("NVIDIA driver too old",
                        f"This PC has driver {version}; Studio needs {MIN_DRIVER} or newer (its PyTorch is built "
                        "for CUDA 13), or every job will fail to start. Update it from nvidia.com or the NVIDIA "
                        "app, then restart Studio."))
    missing = [tool for tool in ("ffmpeg", "ffprobe") if not which(tool)]
    if missing:
        out.append(("FFmpeg not found",
                    f"Studio reads and writes video with FFmpeg, but {' and '.join(missing)} "
                    f"{'is' if len(missing) == 1 else 'are'} not on this PC's PATH. Install FFmpeg (a full build, "
                    "with ffprobe) and add its bin folder to PATH, then restart Studio."))
    return out
