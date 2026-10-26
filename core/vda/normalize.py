"""normalize.py -- Video Depth Anything's raw output to 0..1 depth.

VDA gives relative disparity (bigger is nearer), chained window to window by scale and shift, so
its numbers mean nothing until they are stretched to 0..1. The runner records each frame's
lowest and highest value as the frames come out; this turns those into each frame's range:

* "Whole clip": one range for every frame -- the clip's lowest and highest, as DepthCrafter does,
  so a thing keeps its depth however the rest of the shot changes;
* "EMA": iw3's moving range (depth_scaler.EMAMinMaxScaler): each frame's range is the lowest and
  highest over the next `buffer` frames (its lookahead), eased by `decay` into the last frame's.
  Long shots whose depth range really changes keep using all of it.

No torch here: per-frame numbers only (the tests run it on lists).
"""

from __future__ import annotations

from typing import List, Sequence, Tuple

MODES = ("Whole clip", "EMA")

#: tv-range 10-bit luma, as DepthCrafter's maps are (core.depthedit.depth_io: 0..1 -> 64..940)
CODE_LO, CODE_HI = 64, 940


def whole_clip(lows: Sequence[float], highs: Sequence[float]) -> List[Tuple[float, float]]:
    if not lows:
        return []
    lo, hi = float(min(lows)), float(max(highs))
    return [(lo, hi)] * len(lows)


def ema(lows: Sequence[float], highs: Sequence[float], decay: float = 0.9, buffer: int = 30) -> List[Tuple[float, float]]:
    """iw3's EMAMinMaxScaler, frame for frame: a frame is normalised once `buffer` frames from it on
    have been seen; the last frames (no full lookahead left) take the range as it stands."""
    n = len(lows)
    if n == 0:
        return []
    decay = min(max(float(decay), 0.0), 0.9999)
    buffer = max(1, int(buffer))
    out: List[Tuple[float, float]] = []
    cur = None
    for i in range(n):
        if i + buffer <= n:
            lo, hi = float(min(lows[i:i + buffer])), float(max(highs[i:i + buffer]))
            cur = (lo, hi) if cur is None else (decay * cur[0] + (1 - decay) * lo, decay * cur[1] + (1 - decay) * hi)
        elif cur is None:           # the clip is shorter than the lookahead: all of it, as iw3's flush
            cur = (float(min(lows)), float(max(highs)))
        out.append(cur)
    return out


def ranges(mode: str, lows: Sequence[float], highs: Sequence[float], decay: float = 0.9,
           buffer: int = 30) -> List[Tuple[float, float]]:
    return ema(lows, highs, decay, buffer) if mode == "EMA" else whole_clip(lows, highs)


def scale(lo: float, hi: float) -> Tuple[float, float]:
    """(multiplier, offset) taking a raw value to its 10-bit code: lo -> 64, hi -> 940. A flat frame
    (hi == lo) lands mid-grey rather than dividing by nothing."""
    span = hi - lo
    if span <= 1e-8:
        return 0.0, (CODE_LO + CODE_HI) / 2.0
    k = (CODE_HI - CODE_LO) / span
    return k, CODE_LO - lo * k
