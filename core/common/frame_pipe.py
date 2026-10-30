"""frame_pipe.py -- Popen for an ffmpeg that writes raw frames to stdout.

On Windows the pipe Python makes is 4 KB, and a raw-frame read through it runs at
about half the speed of one through a 1 MB pipe (2026-10-27: a 3840x1038 16-bit
clip, 2.0 s -> 1.05 s for 72 frames). Kept free of torch / decord / cv2 so the
Depth Map Editor and the preview decoder can take it without their weight.
"""

from __future__ import annotations

import os
import subprocess

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

#: The stdout pipe of a raw-frame decode on Windows.
FRAME_PIPE_BYTES = 1 << 20


def popen_frames(cmd, stdin=None, stderr=subprocess.PIPE):
    """subprocess.Popen(cmd, stdout=PIPE, ...) with a 1 MB stdout pipe on Windows.
    ``proc.stdout.read(n)`` returns n bytes, or fewer only at the end, as before."""
    if os.name != "nt":
        return subprocess.Popen(cmd, stdin=stdin, stdout=subprocess.PIPE, stderr=stderr, creationflags=_NO_WINDOW)
    import msvcrt
    import _winapi
    r, w = _winapi.CreatePipe(None, FRAME_PIPE_BYTES)
    rfd = msvcrt.open_osfhandle(r, os.O_RDONLY)
    wfd = msvcrt.open_osfhandle(w, 0)
    try:
        proc = subprocess.Popen(cmd, stdin=stdin, stdout=wfd, stderr=stderr, creationflags=_NO_WINDOW)
    except BaseException:
        os.close(rfd)
        raise
    finally:
        os.close(wfd)          # the child has its own copy; ours would keep EOF from coming
    proc.stdout = open(rfd, "rb", buffering=FRAME_PIPE_BYTES)
    return proc


def popen_frames_in(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE):
    """subprocess.Popen(cmd, stdin=PIPE, ...) with a 1 MB stdin pipe on Windows: raw frames
    written to an ffmpeg encode, the same trick as popen_frames' read side."""
    if os.name != "nt":
        return subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=stdout, stderr=stderr, creationflags=_NO_WINDOW)
    import msvcrt
    import _winapi
    r, w = _winapi.CreatePipe(None, FRAME_PIPE_BYTES)
    rfd = msvcrt.open_osfhandle(r, os.O_RDONLY)
    wfd = msvcrt.open_osfhandle(w, 0)
    try:
        proc = subprocess.Popen(cmd, stdin=rfd, stdout=stdout, stderr=stderr, creationflags=_NO_WINDOW)
    except BaseException:
        os.close(wfd)
        raise
    finally:
        os.close(rfd)          # the child has its own copy; ours would keep its read side open
    proc.stdin = open(wfd, "wb", buffering=FRAME_PIPE_BYTES)
    return proc
