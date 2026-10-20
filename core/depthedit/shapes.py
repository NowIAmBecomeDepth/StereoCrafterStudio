"""shapes.py -- masks drawn as shapes, held on every frame (user, 2026-10-17: "simple polygon shapes for those
locked off shots that don't need to be tracked" -- "one option should be a point connector for custom shapes").

A layer can hold shapes instead of SAM 3's prompts: a polygon of clicked points, a rectangle, an ellipse --
in frame-relative coordinates (0..1), so they fit any size the map is read at. The shapes, in order, make
one mask (a shape drawn with Ctrl cuts what's under it out), and that mask is the layer's on EVERY frame:
nothing is tracked, and SAM 3 never runs for the layer. Kept with the project as the shapes themselves; the
masks are drawn again on load.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence

import cv2
import numpy as np

#: The shape tool's kinds, as the editor shows them
KINDS = ("Points", "Rectangle", "Ellipse")


def valid(shape: Dict[str, Any]) -> bool:
    """A shape that draws something: a polygon of 3+ points, or two corners apart."""
    try:
        pts = [(float(x), float(y)) for x, y in shape.get("pts", [])]
    except (TypeError, ValueError):
        return False
    kind = shape.get("kind")
    if kind == "Points":
        return len(pts) >= 3
    if kind in ("Rectangle", "Ellipse"):
        return len(pts) == 2 and abs(pts[0][0] - pts[1][0]) > 1e-4 and abs(pts[0][1] - pts[1][1]) > 1e-4
    return False


def clean(shapes: Sequence[Any]) -> List[Dict[str, Any]]:
    """The shapes as saved: the kind, the points (0..1, clamped), and whether it cuts; bad ones dropped."""
    out = []
    for s in shapes or []:
        if not isinstance(s, dict) or not valid(s):
            continue
        pts = [[min(max(float(x), 0.0), 1.0), min(max(float(y), 0.0), 1.0)] for x, y in s["pts"]]
        out.append({"kind": s["kind"], "pts": pts, "cut": bool(s.get("cut", False))})
    return out


def draw(shapes: Sequence[Dict[str, Any]], height: int, width: int) -> np.ndarray:
    """The shapes' mask (height, width, bool): each drawn in turn, added -- or cut out."""
    m = np.zeros((int(height), int(width)), np.uint8)
    sx, sy = float(width), float(height)
    for s in clean(shapes):
        one = np.zeros_like(m)
        pts = np.array([[x * sx, y * sy] for x, y in s["pts"]], np.float64)
        if s["kind"] == "Points":
            cv2.fillPoly(one, [np.round(pts).astype(np.int32)], 1)
        else:
            (x0, y0), (x1, y1) = pts.min(0), pts.max(0)
            if s["kind"] == "Rectangle":
                cv2.rectangle(one, (int(round(x0)), int(round(y0))), (int(round(x1)) - 1, int(round(y1)) - 1), 1, -1)
            else:
                centre = (int(round((x0 + x1) / 2)), int(round((y0 + y1) / 2)))
                axes = (max(1, int(round((x1 - x0) / 2))), max(1, int(round((y1 - y0) / 2))))
                cv2.ellipse(one, centre, axes, 0, 0, 360, 1, -1)
        if s["cut"]:
            m[one > 0] = 0
        else:
            m |= one
    return m > 0


def fill(layer, frames: int) -> None:
    """The layer's masks: its shapes' mask on frames 0..frames-1 (one packed copy, shared), at its masks'
    size; none at all when its shapes draw nothing. Its full-size copy goes (the shapes redraw at any size)."""
    store = layer.masks
    if store is None:
        return
    store.clear()
    mask = draw(layer.shapes, store.height, store.width)
    if mask.any():
        packed = np.packbits(mask, axis=None)
        for f in range(max(0, int(frames))):
            store._frames[f] = packed
    layer.masks_hi = None
    layer.shape_frames = max(0, int(frames))
    layer.tracked = bool(mask.any())
