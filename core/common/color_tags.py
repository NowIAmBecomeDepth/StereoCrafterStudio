"""color_tags.py -- put an encode's colour tags into the file, on any ffmpeg.

FFmpeg 7.1 and later take an encode's colour primaries and transfer from the
frames, not from ``-color_primaries`` / ``-color_trc``: on the ffmpeg Studio
ships with (a 2026 git build) those two options are silently ignored, and
every encode -- SDR included -- came out with primaries and transfer
"unknown", so players and DaVinci guessed (found fixing bug sweep 2026-10-09
#17). ``-colorspace`` and ``-color_range`` still land: they are negotiated
with the pixel format.

``with_color_tags(cmd)`` reads the primaries / transfer an ffmpeg command
asks for and sets them on the frames too, with a ``setparams`` filter at the
end of its ``-vf`` chain (or a ``-vf`` of its own). setparams only relabels;
it converts nothing. The ``-color_*`` options stay for older ffmpeg builds.
"""

from __future__ import annotations

from typing import List, Sequence

#: The names setparams accepts (ffmpeg -h filter=setparams). A value outside
#: these is left to the option alone: an unknown name would fail the encode.
_PRIMARIES = frozenset("bt709 bt470m bt470bg smpte170m smpte240m film bt2020 smpte428 smpte431 smpte432 "
                       "jedec-p22 ebu3213".split())
_TRC = frozenset("bt709 bt470m bt470bg smpte170m smpte240m linear log100 log316 iec61966-2-4 bt1361e "
                 "iec61966-2-1 bt2020-10 bt2020-12 smpte2084 smpte428 arib-std-b67".split())

_COPY = {"copy"}


def _after_inputs(cmd: Sequence[str]) -> int:
    """Where the output options start: after the last ``-i <input>``."""
    last = max((i for i, a in enumerate(cmd) if a == "-i"), default=None)
    return 0 if last is None else last + 2


def setparams_for(primaries: str = None, trc: str = None) -> str:
    """``setparams=...`` for the values setparams knows, or ""."""
    parts = []
    if primaries in _PRIMARIES:
        parts.append(f"color_primaries={primaries}")
    if trc in _TRC:
        parts.append(f"color_trc={trc}")
    return "setparams=" + ":".join(parts) if parts else ""


def with_color_tags(cmd: Sequence[str]) -> List[str]:
    """``cmd`` with its ``-color_primaries`` / ``-color_trc`` also set on the
    frames. Works on a whole ffmpeg command or on output arguments alone (no
    ``-i``). Unchanged when it asks for neither, copies the video stream, or
    filters with ``-filter_complex``."""
    cmd = [str(a) for a in cmd]
    start = _after_inputs(cmd)
    out = cmd[start:]
    want = {}
    for flag in ("-color_primaries", "-color_trc"):
        if flag in out[:-1]:
            want[flag] = out[out.index(flag) + 1]
    sp = setparams_for(want.get("-color_primaries"), want.get("-color_trc"))
    if not sp or "-filter_complex" in cmd:
        return cmd
    for flag in ("-c:v", "-vcodec", "-c"):
        if flag in out[:-1] and out[out.index(flag) + 1] in _COPY:
            return cmd
    for flag in ("-vf", "-filter:v"):
        if flag in out[:-1]:
            i = start + out.index(flag) + 1
            if "setparams=" not in cmd[i]:
                cmd[i] = f"{cmd[i]},{sp}" if cmd[i] else sp
            return cmd
    cmd[start:start] = ["-vf", sp]
    return cmd
