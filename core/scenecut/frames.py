"""frames.py -- AutoShot / OmniShotCut's frame reader, with ffmpeg's stderr drained.

detection.py's ``_autoshot_frame_generator`` (upstream's, kept byte-comparable
there) pipes ffmpeg's stderr and never reads it. A damaged clip makes ffmpeg
print a line per bad macroblock even at ``-v error``; a few KB of them fill
the pipe, ffmpeg blocks on the write, and the stdout read below waits for
good -- Scene Cut hangs at "Running AutoShot inference" and Stop can't end it
(bug sweep 2026-10-09 #13). This is the same reader with stderr read on a
thread, as core.depthedit.depth_io does. Studio's Scene Cut engine puts it in
detection's place, so OmniShotCut's decode uses it too.
"""

from __future__ import annotations

import collections
import logging
import subprocess
import sys
import threading

logger = logging.getLogger(__name__)


def autoshot_frame_generator(video_path: str, width: int = 48, height: int = 27):
    """(n, height, width, 3) uint8 chunks of up to 50 frames, as upstream's."""
    import numpy as np

    frame_size = width * height * 3
    cmd = [
        "ffmpeg", "-v", "error",
        "-vsync", "0",          # Force frame-exact passthrough
        "-i", video_path,
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{width}x{height}", "-"
    ]
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=creationflags)
    tail = collections.deque(maxlen=20)        # the last lines, for the log

    def drain():
        for line in iter(process.stderr.readline, b""):
            tail.append(line.decode("utf-8", "replace").rstrip())
    drainer = threading.Thread(target=drain, name="scenecut-ffmpeg-stderr", daemon=True)
    drainer.start()

    try:
        while True:
            chunk_size = 50 * frame_size
            raw = process.stdout.read(chunk_size)
            if not raw:
                break
            n_bytes = len(raw)
            n_frames = n_bytes // frame_size
            if n_frames == 0:
                break
            yield np.frombuffer(raw[:n_frames * frame_size], dtype=np.uint8).reshape((n_frames, height, width, 3))
            if n_bytes < chunk_size:
                break
    except Exception as e:
        logger.error(f"FFmpeg streaming error: {e}")
        raise
    finally:
        if process.stdout:
            process.stdout.close()
        if process.poll() is None:
            process.kill()        # closed early (Stop, an error): ffmpeg needn't decode the rest
        process.wait()
        drainer.join(timeout=2)
        if process.stderr:
            process.stderr.close()
        if tail and process.returncode not in (0, None):
            logger.debug("ffmpeg (%s): %s", video_path, " | ".join(tail))
