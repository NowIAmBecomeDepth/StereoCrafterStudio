"""grain.py -- the film grain over the window (user, 2026-09-28).

One static tile, drawn one grain per SCREEN pixel over the whole window (Main
.qml), the previews kept clean (GrainHole.qml): grain there would be mistaken
for grain in the video. Per theme, as tuned in prototypes/grain_tuner and
saved to dependency/grain_by_theme.json; a theme with nothing saved gets
DEFAULTS. The tile never moves, so it costs nothing while a job runs.

    image://grain/<amount%>_<light%>_<size>_<seed>
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Dict

import cv2
import numpy as np
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickImageProvider

from .legacy import REPO

logger = logging.getLogger(__name__)

STORE = REPO / "dependency" / "grain_by_theme.json"
#: What a theme gets before anything is saved for it (the tuner's starting values).
DEFAULTS = {"enabled": True, "amount": 4.0, "light": 100.0, "size": 1.4}
TILE = 512


def normalise(s: dict) -> dict:
    """Rounded to the tuner's slider steps."""
    s = {k: v for k, v in (s or {}).items() if v is not None}
    return {
        "enabled": bool(s.get("enabled", DEFAULTS["enabled"])),
        "amount": round(float(s.get("amount", DEFAULTS["amount"])), 1),
        "light": float(round(float(s.get("light", DEFAULTS["light"])) / 5.0) * 5),
        "size": round(float(s.get("size", DEFAULTS["size"])), 1),
    }


def load(path: Path = STORE) -> Dict[str, dict]:
    """Every saved theme's grain, by palette name; {} when there is no file."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        logger.warning("Could not read %s", path, exc_info=True)
        return {}
    return {k: normalise(v) for k, v in data.items() if not k.startswith("_") and isinstance(v, dict)}


def source(settings: dict, seed: int = 1) -> str:
    """The image:// URL for these settings, "" for no grain."""
    s = normalise(settings)
    if not s["enabled"] or s["amount"] <= 0:
        return ""
    return f"image://grain/{s['amount']:.1f}_{s['light']:.0f}_{s['size']:.1f}_{seed}"


def grain_tile(amount: float, light: float, size: float, seed: int) -> np.ndarray:
    """A seamless TILE x TILE premultiplied BGRA grain texture.

    amount  opacity of the dark grains at one standard deviation, 0..1
    light   opacity of the white grains relative to the dark ones
    size    grain size in screen pixels; above 1 the noise is blurred with
            wrap-around padding, so the tile still repeats without a seam
    """
    v = np.random.default_rng(seed).standard_normal((TILE, TILE)).astype(np.float32)
    sigma = max(0.0, (size - 1.0) * 0.6)
    if sigma > 0:
        pad = int(math.ceil(sigma * 3)) + 1
        v = cv2.GaussianBlur(np.pad(v, pad, mode="wrap"), (0, 0), sigma)[pad:-pad, pad:-pad]
        v /= max(float(v.std()), 1e-6)
    up = np.clip(v * amount * light, 0.0, 1.0)
    down = np.clip(-v * amount, 0.0, 1.0)
    tile = np.zeros((TILE, TILE, 4), np.uint8)
    white = (up * 255.0 + 0.5).astype(np.uint8)   # premultiplied: white x alpha
    for c in range(3):
        tile[..., c] = white
    tile[..., 3] = (np.maximum(up, down) * 255.0 + 0.5).astype(np.uint8)
    return tile


class GrainProvider(QQuickImageProvider):
    """image://grain/<amount%>_<light%>_<size>_<seed>"""

    def __init__(self):
        super().__init__(QQuickImageProvider.ImageType.Image)

    def requestImage(self, ident, size, requested):
        try:
            a, l, s, seed = ident.split("?")[0].split("_")
            tile = grain_tile(float(a) / 100.0, float(l) / 100.0, float(s), int(seed))
        except Exception:
            tile = np.zeros((TILE, TILE, 4), np.uint8)
        img = QImage(tile.data, TILE, TILE, TILE * 4, QImage.Format.Format_ARGB32_Premultiplied).copy()
        if size is not None:
            size.setWidth(TILE)
            size.setHeight(TILE)
        return img
