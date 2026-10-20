"""display.py -- the Depth Map Editor's picture of a depth frame (user,
2026-10-05: "the viewer in the depth editor is 8 bit cause i can easily see
banding").

The map is 10-bit (about 876 levels in TV range) and the edit works in float;
the window is 8-bit, so a gentle gradient -- a wall, a floor -- showed every
3 or 4 depth levels as one flat band. Here:

* grey: the float depth with an ordered (Bayer 8x8) dither added before it is
  rounded to 8 bits. A fixed pattern -- it stays put as frames are stepped --
  whose average over a few pixels is the depth between two 8-bit levels, so
  the eye sees the 10-bit steps and no bands. ``dither=False``: plain rounding
  (the old picture).
* colour (Turbo): the colour interpolated at the depth's own float position
  between the 256 Turbo colours, not looked up at its 8-bit level -- a step of
  one 10-bit level changes the colour -- dithered the same way.

Display only: nothing here reaches the rendered map.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Tuple

import cv2
import numpy as np

#: Bayer's 8x8 ordered-dither matrix, as thresholds in [0, 1).
_BAYER8 = (np.array([
    [0, 32, 8, 40, 2, 34, 10, 42],
    [48, 16, 56, 24, 50, 18, 58, 26],
    [12, 44, 4, 36, 14, 46, 6, 38],
    [60, 28, 52, 20, 62, 30, 54, 22],
    [3, 35, 11, 43, 1, 33, 9, 41],
    [51, 19, 59, 27, 49, 17, 57, 25],
    [15, 47, 7, 39, 13, 45, 5, 37],
    [63, 31, 55, 23, 61, 29, 53, 21],
], dtype=np.float32) + 0.5) / 64.0


@lru_cache(maxsize=4)
def _threshold(shape: Tuple[int, int]) -> np.ndarray:
    h, w = shape
    t = np.tile(_BAYER8, ((h + 7) // 8, (w + 7) // 8))[:h, :w]
    t.flags.writeable = False
    return t


#: Positions the colour table holds: 4096, finer than the map's 10 bits.
TURBO_STEPS = 4096


@lru_cache(maxsize=1)
def _turbo() -> np.ndarray:
    """Turbo as float RGB at TURBO_STEPS positions, (3, TURBO_STEPS): its 256
    colours, interpolated between."""
    ramp = np.arange(256, dtype=np.uint8)[:, None]
    rgb = cv2.applyColorMap(ramp, cv2.COLORMAP_TURBO)[:, 0, ::-1].astype(np.float32)
    x = np.linspace(0.0, 255.0, TURBO_STEPS, dtype=np.float32)
    table = np.stack([np.interp(x, np.arange(256, dtype=np.float32), rgb[:, c]) for c in range(3)])
    table = table.astype(np.float32)
    table.flags.writeable = False
    return table


def _to_8bit(values: np.ndarray, dither: bool) -> np.ndarray:
    """0..255 floats to uint8: dithered (floor of value + threshold) or rounded."""
    if dither:
        t = _threshold(values.shape[:2])
        if values.ndim == 3:
            t = t[:, :, None]
        out = np.floor(values + t)
    else:
        out = np.rint(values)
    return np.clip(out, 0, 255).astype(np.uint8)


def depth_view(unit: np.ndarray, colour: bool = False, dither: bool = True) -> np.ndarray:
    """A depth frame (0..1 floats, H x W) as an H x W x 3 uint8 RGB picture."""
    u = np.clip(np.asarray(unit, dtype=np.float32), 0.0, 1.0)
    if colour:
        table = _turbo()
        idx = (u * (TURBO_STEPS - 1) + 0.5).astype(np.uint16)
        # a channel at a time: one small table gathered three times is far
        # quicker than an (H, W, 3) interpolation (1080p: ~190 -> ~60 ms)
        return np.dstack([_to_8bit(table[c].take(idx), dither) for c in range(3)])
    g = _to_8bit(u * 255.0, dither)
    return np.repeat(g[:, :, None], 3, axis=2)
