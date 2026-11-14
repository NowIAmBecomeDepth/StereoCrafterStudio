"""formats.py -- the formats a DVD (or VDA) depth map can be written in (user, 2026-11-13: "add encoding output
formats for dvd like pngs and 10 bit and h264/265").

The encode pass works in 10-bit code space (core.vda.normalize: the clip's range on codes 64..940, tv range);
each writer here takes a batch of those codes as float, on the card, and quantises it its own way:

* H.265 10-bit lossless -- what the maps always were (core.depthedit.depth_io.DepthWriter, NVENC when it's there).
* H.265 10-bit -- the same codes, near-lossless (x265 crf 0 / NVENC qp 0): a fraction of the size.
* H.265 8-bit, H.264 8-bit -- codes / 4 (tv 16..235), for tools and players that want 8-bit.
* PNG 16-bit, PNG 8-bit -- one greyscale picture a frame, full range (0 far .. max near), quantised from the float
  values, not from the 10-bit codes -- 16-bit keeps every bit the model gave. Written into a folder
  <clip><suffix>/ beside where the video would be. Splatting reads videos only, so the PNGs are for other tools.

Every video is <clip><suffix>.mp4, as before, tagged with its kind (core.vda.output), so Splatting and the clip
list find it the same way.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

LOSSLESS = "H.265 10-bit lossless"
HEVC10 = "H.265 10-bit"
HEVC8 = "H.265 8-bit"
H264 = "H.264 8-bit"
PNG16 = "PNG 16-bit"
PNG8 = "PNG 8-bit"
#: the menu, in order; the first is the default (what every map was before)
FORMATS = [LOSSLESS, HEVC10, HEVC8, H264, PNG16, PNG8]

CODE_LO, CODE_HI = 64.0, 940.0        # core.vda.normalize's range in 10-bit codes

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def is_png(fmt: str) -> bool:
    return fmt in (PNG16, PNG8)


def final_path(output_dir: str, basename: str, suffix: str, fmt: str) -> str:
    """The map's path: <clip><suffix>.mp4, or the folder <clip><suffix> for a PNG sequence."""
    from core.vda.output import map_path
    path = map_path(output_dir, basename, suffix)
    return os.path.splitext(path)[0] if is_png(fmt) else path


def open_writer(fmt: str, final: str, width: int, height: int, fps: str, metadata: Optional[dict] = None):
    """A writer for `fmt` into a partial name beside `final`: write(block) / close() -> where it went / abort().
    `block` is (B, H, W) float 10-bit codes on any device."""
    if is_png(fmt):
        return _PngWriter(final, 16 if fmt == PNG16 else 8)
    return _VideoWriter(fmt, final, width, height, fps, metadata)


class _VideoWriter:
    def __init__(self, fmt, final, width, height, fps, metadata):
        from core.common.partial_output import part_path
        from core.depthedit.depth_io import ClipInfo, DepthWriter, nvenc_available
        self.fmt, self.final, self.part = fmt, final, part_path(final)
        self.bits = 10 if fmt in (LOSSLESS, HEVC10) else 8
        if fmt == LOSSLESS:
            self._w = DepthWriter(self.part, ClipInfo(path=final, width=width, height=height, num_frames=0, fps=fps,
                                                      pix_fmt="yuv420p10le", full_range=False), metadata=metadata)
            return
        self._w = _FfmpegYuv(self.part, width, height, fps, self.bits, _codec(fmt, nvenc_available()), metadata)

    def write(self, block) -> None:
        import torch
        if self.bits == 10:
            codes = block.round().clamp_(0, 1023).to(torch.int32).cpu().numpy().astype("<u2")
        else:
            codes = (block / 4.0).round_().clamp_(0, 255).to(torch.uint8).cpu().numpy()
        for c in codes:
            self._w.write(c)

    def close(self) -> str:
        self._w.close()
        return self.part

    def abort(self) -> None:
        from core.common.partial_output import discard
        self._w.abort()
        discard(self.part)


def _codec(fmt: str, nvenc: bool) -> list:
    """The encoder arguments. Quality first: depth maps are flat, low-gradient pictures -- the content an
    encoder quantises hardest (depthcrafter/utils.py's measurements) -- so no fast presets."""
    if fmt == HEVC10:
        return (["-c:v", "hevc_nvenc", "-pix_fmt", "p010le", "-preset", "p7", "-rc", "constqp", "-qp", "0"] if nvenc
                else ["-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-preset", "slow", "-crf", "0",
                      "-x265-params", "log-level=error"])
    if fmt == HEVC8:
        return (["-c:v", "hevc_nvenc", "-pix_fmt", "yuv420p", "-preset", "p7", "-rc", "constqp", "-qp", "0"] if nvenc
                else ["-c:v", "libx265", "-pix_fmt", "yuv420p", "-preset", "slow", "-crf", "0",
                      "-x265-params", "log-level=error"])
    if fmt == H264:
        # High profile so every player takes it (x264's crf 0 is lossless, which only High 4:4:4 can carry)
        return (["-c:v", "h264_nvenc", "-pix_fmt", "yuv420p", "-preset", "p7", "-rc", "constqp", "-qp", "1",
                 "-profile:v", "high"] if nvenc
                else ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "slow", "-crf", "1", "-profile:v", "high"])
    raise ValueError(f"not a video format: {fmt}")


class _FfmpegYuv:
    """Y codes (H, W) in, a tv-range BT.709 video out -- DepthWriter's way (the tags on the input as well as the
    output, so ffmpeg converts nothing), at 8 or 10 bits, with any encoder."""

    def __init__(self, path, width, height, fps, bits, codec, metadata):
        import numpy as np
        from core.common.partial_output import ffmpeg_output_args
        self.path, self.width, self.height = path, width, height
        c_px = ((width + 1) // 2) * ((height + 1) // 2)
        if bits == 10:
            self._chroma = np.full(2 * c_px, 512, dtype="<u2").tobytes()
            in_fmt = "yuv420p10le"
        else:
            self._chroma = np.full(2 * c_px, 128, dtype=np.uint8).tobytes()
            in_fmt = "yuv420p"
        tags = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]
        extra = []
        for key, value in (metadata or {}).items():
            extra += ["-metadata", f"{key}={value}"]
        hvc1 = ["-tag:v", "hvc1"] if "hevc" in codec[1] or codec[1] == "libx265" else []
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
               "-f", "rawvideo", "-pix_fmt", in_fmt, "-s", f"{width}x{height}", "-r", str(fps), *tags, "-i", "pipe:0",
               "-an", *codec, *hvc1, *tags, *extra, *ffmpeg_output_args(path)]
        self._proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=_NO_WINDOW)
        self._err: list = []
        self._drain = threading.Thread(target=lambda: self._err.append(self._proc.stderr.read()), daemon=True)
        self._drain.start()

    def write(self, codes) -> None:
        if codes.shape != (self.height, self.width):
            raise ValueError(f"frame is {codes.shape}, writer expects {(self.height, self.width)}")
        try:
            self._proc.stdin.write(codes.tobytes())
            self._proc.stdin.write(self._chroma)
        except OSError:
            self.close()            # raises with what ffmpeg said
            raise

    def close(self) -> None:
        try:
            self._proc.stdin.close()
        except Exception:
            pass
        code = self._proc.wait()
        self._drain.join(timeout=5)
        if code != 0:
            err = b"".join(c for c in self._err if c).decode("utf-8", "replace").strip()
            raise RuntimeError(f"ffmpeg exited {code} writing {self.path}: {err[-800:]}")

    def abort(self) -> None:
        try:
            self._proc.kill()
            self._proc.wait()
        except Exception:
            pass


class _PngWriter:
    """One greyscale PNG a frame, <folder>/<folder name>_000000.png on, into <folder>.part until close()."""

    def __init__(self, final: str, bits: int):
        self.final, self.bits = final, bits
        self.part = final + ".part"
        if os.path.isdir(self.part):
            shutil.rmtree(self.part, ignore_errors=True)        # our own leftover from a stopped run
        os.makedirs(self.part, exist_ok=True)
        self.stem = os.path.basename(final)
        self.n = 0

    def write(self, block) -> None:
        import cv2
        import torch
        top = 65535.0 if self.bits == 16 else 255.0
        unit = ((block - CODE_LO) / (CODE_HI - CODE_LO)).clamp_(0, 1)
        if self.bits == 16:
            frames = (unit * top).round_().to(torch.int32).cpu().numpy().astype("uint16")
        else:
            frames = (unit * top).round_().to(torch.uint8).cpu().numpy()
        for f in frames:
            path = os.path.join(self.part, f"{self.stem}_{self.n:06d}.png")
            if not cv2.imwrite(path, f):
                raise RuntimeError(f"could not write {path}")
            self.n += 1

    def close(self) -> str:
        return self.part

    def abort(self) -> None:
        shutil.rmtree(self.part, ignore_errors=True)


def publish_pngs(part: str, final: str) -> Optional[str]:
    """Put the finished PNG folder at `final`; an earlier folder there moves aside with a timestamp (never
    deleted). Returns where it went, or None."""
    moved = None
    if os.path.exists(final):
        moved = f"{final}{time.strftime('_%Y%m%d%H%M%S')}"
        os.replace(final, moved)
        logger.info("The earlier PNG maps %s moved to %s.", os.path.basename(final), moved)
    os.replace(part, final)
    return moved
