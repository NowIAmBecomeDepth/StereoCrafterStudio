"""
Frame Break protrusion-mask helpers (shared preview + render).

Frame Break draws letterbox bars and lets near foreground pixels protrude
THROUGH them. Which pixels protrude is decided by a hard depth threshold: a
binary mask, 255 where the subject shows, 0 where the bar stays solid.

This module owns two things, kept here so the live preview (splatting_gui) and
the baked render mask (render_processor) run byte-identical logic:

  1. build_protrusion_mask -- depth >= threshold, hard, per-frame.
  2. morph_protrusion_mask -- the Frame Break "Edge" fine-tuning: signed
     HORIZONTAL erode/dilate applied independently to the left and right edges
     of the protruding region (plus a global term added to both), then an
     optional feather applied ONLY along the break boundary.

Design notes that matter:

  - Erode/dilate are horizontal-only (1xN structuring element). For a subject
    poking up through a bottom bar, that widens/narrows how much of it shows
    without eating into its height.
  - Left vs Right independence is directional: a positive Left value grows the
    mask's LEFT edge leftward; a positive Right value grows its RIGHT edge
    rightward. Negative pulls that edge inward. This is done with directional
    (anchored) morphology so the two edges move independently, which a single
    symmetric dilate/erode cannot do.
  - Edge Blur feathers only the boundary. The bar interior stays pure black and
    the subject interior stays fully opaque; only the transition is softened.
    So the mask becomes grayscale near the break edge but is still 0/255
    everywhere else -- the merge/preview stamp already treats it as a soft
    mask, so a feathered edge composites correctly without reopening the
    washed-out full-bar fade that a global blur would cause.

Everything is a no-op (returns the input mask unchanged) when its parameters
are zero, so an untouched Frame Break costs nothing here.
"""

from typing import Union

import numpy as np

try:
    import cv2
except Exception:  # pragma: no cover - cv2 always present in the app env
    cv2 = None


def build_protrusion_mask(depth_2d: np.ndarray, thresh: float,
                          out_h: int, out_w: int) -> np.ndarray:
    """Hard 0/255 protrusion mask at (out_h, out_w).

    depth_2d: normalized depth (near = white = 1.0), any resolution.
    thresh:   pixels with depth >= thresh protrude (show through the bar).
              thresh >= 1.0 -> nothing protrudes (all-zero mask).
    Resized NEAREST so the cutout stays hard-edged instead of interpolating
    a grey rim before the threshold runs.
    """
    if thresh >= 1.0:
        return np.zeros((out_h, out_w), dtype=np.uint8)
    d = depth_2d
    if d.shape[:2] != (out_h, out_w):
        if cv2 is not None:
            d = cv2.resize(d.astype(np.float32), (out_w, out_h),
                           interpolation=cv2.INTER_NEAREST)
        else:  # pragma: no cover
            d = np.asarray(d, dtype=np.float32)
    keep = d >= float(thresh)
    return np.where(keep, np.uint8(255), np.uint8(0))


def _directional_morph_x(mask: np.ndarray, left_px: int, right_px: int) -> np.ndarray:
    """Move the mask's left and right edges independently, horizontal only.

    Positive grows that edge outward (dilate), negative pulls it in (erode).
    Uses anchored 1xN morphology so each edge moves alone. The anchor->edge
    mapping is the empirically-verified cv2 behaviour (anchor is (x, y), x=0 is
    the element's left end):

        move LEFT edge  : dilate anchor=left(0)      / erode anchor=right(w-1)
        move RIGHT edge : dilate anchor=right(w-1)   / erode anchor=left(0)

    i.e. dilation extends toward the anchored end; erosion bites inward from the
    end OPPOSITE the anchor. A width-(n+1) element moves the edge by n px.
    """
    if cv2 is None or (left_px == 0 and right_px == 0):
        return mask

    out = mask

    def _apply(edge: str, px: int, m: np.ndarray) -> np.ndarray:
        w = abs(int(px)) + 1
        k = np.ones((1, w), dtype=np.uint8)
        grow = px > 0
        if edge == "left":
            anchor = (0, 0) if grow else (w - 1, 0)
        else:  # right
            anchor = (w - 1, 0) if grow else (0, 0)
        if grow:
            return cv2.dilate(m, k, anchor=anchor, iterations=1)
        return cv2.erode(m, k, anchor=anchor, iterations=1)

    if left_px != 0:
        out = _apply("left", left_px, out)
    if right_px != 0:
        out = _apply("right", right_px, out)

    return out


def morph_protrusion_mask(mask: np.ndarray, erode_left: float, erode_right: float,
                          erode_global: float, edge_blur: float) -> np.ndarray:
    """Apply the Frame Break edge fine-tuning to a 0/255 protrusion mask.

    erode_left / erode_right / erode_global: signed pixels. Negative erodes,
        positive dilates, horizontal only. Global is added to both edges.
    edge_blur: feather width in pixels along the break boundary, 0 = hard.

    Returns a mask of the same shape. May be grayscale near the edge when
    edge_blur > 0; still 0/255 everywhere else.
    """
    left = int(round(float(erode_left) + float(erode_global)))
    right = int(round(float(erode_right) + float(erode_global)))

    out = _directional_morph_x(mask, left, right)

    blur = int(round(float(edge_blur)))
    if blur > 0 and cv2 is not None:
        # Gaussian on the binary mask feathers every boundary (the break edge
        # is the only boundary in the bar region, which is what we want). Odd
        # kernel required; sigma derived from radius.
        k = blur * 2 + 1
        out = cv2.GaussianBlur(out, (k, k), 0)

    return out


def frame_break_edge_is_active(erode_left: float, erode_right: float,
                               erode_global: float, edge_blur: float) -> bool:
    """True when any edge fine-tuning parameter would change the mask."""
    try:
        return any(abs(float(v)) > 1e-9 for v in
                   (erode_left, erode_right, erode_global, edge_blur))
    except (TypeError, ValueError):
        return False
