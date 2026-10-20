"""edit_ops.py -- what the Depth Map Editor does to a depth frame. Pure numpy/cv2.

Conventions
-----------
Depth arrives as float32 in "unit" space (depth_io.to_unit): 0.0 is the far
end of the clip's range, 1.0 the near end -- DepthCrafter's maps are inverse
depth, brighter is nearer. Every control below is written in that space, so
"Brightness +10%" means "pull the object a tenth of the full range nearer".

A frame is edited as: whole-frame adjustment first, then each enabled layer
in list order, bottom to top. A layer is an object mask (from SAM 3) plus the
edit applied inside it; its result is blended over the frame by the mask's
feathered alpha times the layer's opacity, so the edges are soft and nothing
outside the mask moves.

Why the object's MEDIAN is the pivot
------------------------------------
Contrast and Flatten need a centre to scale about. The mean is dragged by the
few background pixels a mask edge always catches; the median of the depth
under the mask is the object's own depth and moves smoothly frame to frame,
so a contrast change does not also slide the object nearer or further.

Nothing here keeps state between frames. The preview and the render call the
same function on the same inputs, so they cannot disagree about a frame.
"""

from __future__ import annotations

import logging
import math
from collections import OrderedDict
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Dict, Optional, Sequence, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------

GRADIENT_SHAPES = ("Linear", "Radial")
#: How a gradient meets the depth already there. "Replace" is the one that
#: fixes a road or a wall DepthCrafter flattened; the other three are for
#: nudging rather than rebuilding.
GRADIENT_BLENDS = ("Replace", "Only nearer", "Only further", "Add")


@dataclass
class Gradient:
    """A depth ramp painted inside the mask.

    p0 is the NEAR end, p1 the FAR end, each (x, y) in 0..1. With
    ``follow_object`` they are relative to the mask's bounding box on each
    frame, so the ramp moves and scales with the object; without it they are
    relative to the frame and stay put.
    """

    enabled: bool = False
    shape: str = "Linear"
    p0: Tuple[float, float] = (0.5, 1.0)
    p1: Tuple[float, float] = (0.5, 0.0)
    follow_object: bool = True
    near: float = 0.8
    far: float = 0.3
    #: Exponent on the 0..1 position along the ramp. Above 1 the ramp stays
    #: near for longer and falls away late; below 1 the opposite.
    curve: float = 1.0
    #: How much of the object's own surface relief rides on top of the ramp.
    #: 0 is a clean ramp; 1 keeps every bump DepthCrafter saw.
    keep_detail: float = 0.5
    blend: str = "Replace"


@dataclass
class LayerSettings:
    brightness: float = 0.0  # -1..1, added
    contrast: float = 1.0  # scale about the object's median
    flatten: float = 0.0  # 0..1, pull towards the median
    #: The pixel settings below are in DEPTH-MAP pixels and may be fractional
    #: (user, 2026-10-01: a whole depth pixel was ~2.5 pixels of a 1920 clip,
    #: so edits landed just over or under). A fraction blends the two nearest
    #: whole-pixel results; Splatting's bilinear upscale turns that into an
    #: edge between pixels. Studio's editor shows them in video pixels.
    smooth: float = 0.0  # px, blur inside the mask only
    #: px, one edge of the object's DEPTH moved on its own: - pulls that edge
    #: in (the depth behind shows there), + pushes it out. The other edge stays
    #: put -- a beam warped by Y dilation loses its top without its bottom.
    #: See edge_shift. Left / right the same, sideways (user, 2026-10-05).
    edge_top: float = 0.0
    edge_bottom: float = 0.0
    edge_left: float = 0.0
    edge_right: float = 0.0
    grow: float = 0.0  # px, + dilates the mask, - erodes it
    #: px radius of a morphological close (user, 2026-10-05): seals tears and
    #: notches up to about twice this wide that reach the mask's edge, which
    #: Fill holes can't (it fills only what the mask encloses). 0 = off.
    close_gaps: float = 0.0
    #: px radius of the guided filter that pulls the mask's edge onto the
    #: depth map's own edges. 0 = off. See snap_mask.
    edge_snap: int = 4
    feather: float = 2.0  # px, soft edge
    #: Frames either side a tracked mask is steadied over (user, 2026-10-11): each
    #: pixel is in the mask when most of the masks around it agree, so an outline
    #: SAM 3 redraws a little differently every frame -- the foot of a lamp post
    #: with no strong edge to hold -- stops shimmering. 0 = off. See MaskStore.get.
    steady: int = 0
    fill_holes: bool = False
    opacity: float = 1.0
    #: What the mask is for (user, 2026-10-03). "Alter": it selects, and the
    #: settings above change the depth already there. "Add": it IS the shape
    #: -- inside it the depth is replaced by add_depth (then Push / pull and
    #: the gradient on top), for an object the depth map missed or left as a
    #: blob; no edge snap (the outline is the point). "Erase" (user,
    #: 2026-10-05): the object is taken out -- the depth inside the mask is
    #: filled from the depth around it (erase_fill), and Erase from Clip
    #: removes it from the clip itself (core.removal).
    mode: str = "Alter"
    add_depth: float = 0.5       # 0..1 (1 nearest), Add's depth
    #: Alter's Despeck (user, 2026-10-12; core.depthedit.despeck): DepthCrafter's bright
    #: specks just inside left edges in the mask lowered to the object's own depth, by
    #: Strength (0..1). Off by default: a layer that never had it is as it was. (A mode
    #: of its own first -- "Despeck turns off when I switch back to Alter" -- then
    #: "add despeck to Alter instead".)
    despeck_on: bool = False
    despeck_strength: float = 1.0
    #: Level to edge (user, 2026-10-12): a small thing -- a particle -- whose white dot sits in a
    #: grey body has nothing beside it for Despeck to go by: anything in the mask brighter than
    #: the grey around its outer edge comes down to it. "Particles that are actually supposed to
    #: be near will be white throughout": their edge is white too, so they stay. Strength is
    #: Despeck's. Off by default.
    level_edge: bool = False
    #: Frames the layer is active on, inclusive. -1 = open-ended.
    start_frame: int = -1
    end_frame: int = -1
    gradient: Gradient = field(default_factory=Gradient)

    def __post_init__(self) -> None:
        # a project saved before they were fractional holds whole numbers; as
        # floats, the editor's value writer keeps the fractions it is given
        for name in _FRACTIONAL_PX:
            setattr(self, name, float(getattr(self, name)))


#: LayerSettings fields that take fractional (depth-map) pixels.
_FRACTIONAL_PX = ("smooth", "edge_top", "edge_bottom", "edge_left", "edge_right", "grow", "feather", "close_gaps")

#: The one-sided edge moves, as (setting, side).
EDGE_SIDES = (("edge_top", "top"), ("edge_bottom", "bottom"), ("edge_left", "left"), ("edge_right", "right"))


def _split_px(px: float) -> Tuple[int, float]:
    """(whole pixels toward zero, the fraction left over), e.g. -2.4 -> (-2, 0.4)."""
    px = float(px)
    whole = int(px)               # toward zero
    return whole, abs(px - whole)


@dataclass
class GlobalSettings:
    """Whole-frame adjustment, applied before any layer."""

    invert: bool = False
    in_black: float = 0.0  # levels: this maps to 0 ...
    in_white: float = 1.0  # ... and this to 1
    gamma: float = 1.0
    contrast: float = 1.0  # about mid-grey
    brightness: float = 0.0
    #: Global Despeck (user, 2026-10-12: "can we add a global despeck"): Alter's Despeck on
    #: the whole frame, first, on the map as it is (before Invert and the rest). Off by default.
    despeck_on: bool = False
    despeck_strength: float = 1.0
    #: how far a speck must stand above its object to be taken, 0..1 of the depth range
    #: (user, 2026-10-12: "would a threshold dial help"); 0 = everything it finds
    despeck_threshold: float = 0.0
    #: how far in from a left edge it works (of the width) and how sharp a drop makes a left
    #: edge (of the depth range): core.depthedit.despeck's REACH and STEP, its defaults
    #: (user, 2026-10-12: "add any other setting dials that may help tune the global despecker")
    despeck_reach: float = 0.02
    despeck_step: float = 0.04
    #: where it works, as shares of the frame (user, 2026-10-12: "horizontal top and bottom zone
    #: lines that can easily cut the global effects off past the line ... as well as vertical lines
    #: for x axis cutoffs"); the whole frame by default, eased over ZONE_SOFT at each line
    despeck_left: float = 0.0
    despeck_right: float = 1.0
    despeck_top: float = 0.0
    despeck_bottom: float = 1.0
    #: Local Match (user, 2026-10-13): on a locked-off shot, each frame's coarse depth matched
    #: to one frame's -- where DepthCrafter re-judged part of a scene that never moved (a hedge
    #: grey at the start, black at the end) -- before anything else. The reference frame's own
    #: depth is set at run time (set_match_reference), never saved. Off by default.
    match_on: bool = False
    match_frame: int = 0
    match_strength: float = 1.0

    def has_match(self) -> bool:
        ref = getattr(self, "_match_ref", None)
        return (bool(self.match_on) and float(self.match_strength) > 0 and ref is not None
                and getattr(self, "_match_ref_frame", None) == int(self.match_frame))

    def despeck_zone(self) -> Optional[Tuple[float, float, float, float]]:
        """(left, top, right, bottom), or None for the whole frame."""
        z = (float(self.despeck_left), float(self.despeck_top), float(self.despeck_right), float(self.despeck_bottom))
        return None if z == (0.0, 0.0, 1.0, 1.0) else z

    def despeck_args(self) -> Dict[str, float]:
        return {"threshold": float(self.despeck_threshold), "reach": float(self.despeck_reach),
                "step": float(self.despeck_step)}

    def has_despeck(self) -> bool:
        return bool(self.despeck_on) and float(self.despeck_strength) > 0

    def is_identity(self) -> bool:
        return (not self.invert and self.in_black == 0.0 and self.in_white == 1.0
                and self.gamma == 1.0 and self.contrast == 1.0 and self.brightness == 0.0
                and not self.has_despeck() and not self.has_match())


def set_match_reference(g: GlobalSettings, frame: Optional[int], unit: Optional[np.ndarray]) -> None:
    """The reference frame's depth for Local Match, as it is in the map (0..1) -- an attribute
    of the settings object, not a field: a deepcopy (Render's) carries it, the project file
    (asdict) never does. None: none loaded."""
    g._match_ref = None if unit is None else np.asarray(unit, dtype=np.float32)
    g._match_ref_frame = None if unit is None else int(frame)


def settings_to_dict(obj: Any) -> Dict[str, Any]:
    return asdict(obj)


def layer_settings_from_dict(d: Dict[str, Any]) -> LayerSettings:
    d = dict(d or {})
    grad = Gradient(**_known(Gradient, d.pop("gradient", {}) or {}))
    grad.p0 = tuple(grad.p0)
    grad.p1 = tuple(grad.p1)
    if d.get("mode") == "Despeck":          # a layer from the day Despeck was a mode of its own
        d["mode"] = "Alter"
        d.setdefault("despeck_on", True)
    s = LayerSettings(**_known(LayerSettings, d))
    s.gradient = grad
    return s


def global_settings_from_dict(d: Dict[str, Any]) -> GlobalSettings:
    # each value as its field's type, else the default: a null or a string from a hand-edited
    # project failed every redraw, logged only (bug sweep 2026-10-14c)
    base = GlobalSettings()
    out = {}
    for name, value in _known(GlobalSettings, d or {}).items():
        kind = type(getattr(base, name))
        try:
            if kind is bool:
                if not isinstance(value, (bool, int, float)):
                    raise TypeError(value)
                out[name] = bool(value)
            else:
                out[name] = kind(value)
        except (TypeError, ValueError):
            pass
    return GlobalSettings(**out)


def _known(cls, d: Dict[str, Any]) -> Dict[str, Any]:
    """Drop keys a newer or older project file carries that this one does not."""
    names = set(cls.__dataclass_fields__)
    return {k: v for k, v in d.items() if k in names}


# --------------------------------------------------------------------------
# Whole frame
# --------------------------------------------------------------------------


#: the Global Despeck's zone fades in over this share of the frame at each of its lines
ZONE_SOFT = 0.01


def zone_weight(zone: Optional[Tuple[float, float, float, float]], shape: Tuple[int, ...]) -> Optional[np.ndarray]:
    """How much of the Global Despeck each pixel gets: 1 inside the zone, 0 outside, eased
    over ZONE_SOFT across each line. None: the whole frame."""
    if zone is None:
        return None
    h, w = shape[-2], shape[-1]
    left, top, right, bottom = zone

    def axis(n: int, lo: float, hi: float) -> np.ndarray:
        p = (np.arange(n, dtype=np.float32) + 0.5) / n
        s = max(ZONE_SOFT, 4.0 / max(n, 1))          # at least 4 px, so a small frame eases too
        a = np.clip((p - (lo - s / 2)) / s, 0.0, 1.0) if lo > 0 else np.ones(n, np.float32)
        b = np.clip(((hi + s / 2) - p) / s, 0.0, 1.0) if hi < 1 else np.ones(n, np.float32)
        t = a * b
        return t * t * (3.0 - 2.0 * t)

    return (axis(h, top, bottom)[:, None] * axis(w, left, right)[None, :]).astype(np.float32)


#: Local Match: the scale its coarse depth is taken at, a share of the frame's width (16 px at 768)
MATCH_SIGMA = 16 / 768.0
#: ...a pixel counts toward it only where both frames are smooth (less than this a pixel at 768
#: wide: not an edge that moved) and the two differ by less than MATCH_MAX_DIFF of the range
MATCH_EDGE = 0.08
MATCH_MAX_DIFF = 0.25
#: ...and a frame correlating less than this with the reference is left alone: something close
#: fills it (the person passing the lens), and matching it would paint them out
MATCH_MIN_CORRELATION = 0.8


def local_match(v: np.ndarray, ref: np.ndarray, strength: float = 1.0) -> np.ndarray:
    """`v`'s coarse depth matched to `ref`'s (both 0..1, bright = near), its fine detail kept.

    The difference to the reference is measured only on pixels that are smooth in both frames
    and close in value -- an outline that moved between them (a pose, an arm) or something
    that came close is left out -- and filled in across the frame from those (a normalised
    blur), so nothing moving gets a halo. Measured on the user's chap2_00097213 (a hedge 0.16
    at the start, 0.10 from frame ~160 against a steady frame): 0.109 at frame 0 after it
    against 0.106 at frame 230, the woman's outline untouched. For locked-off shots: a pan or
    dolly moves everything, and this would pull it back."""
    v = np.asarray(v, dtype=np.float32)
    ref = np.asarray(ref, dtype=np.float32)
    h, w = v.shape[-2], v.shape[-1]
    if ref.shape != v.shape:
        ref = cv2.resize(ref, (w, h), interpolation=cv2.INTER_LINEAR)
    a, b = v[::4, ::4].ravel(), ref[::4, ::4].ravel()
    if a.std() < 1e-6 or b.std() < 1e-6 or float(np.corrcoef(a, b)[0, 1]) < MATCH_MIN_CORRELATION:
        return v
    edge = MATCH_EDGE * 768.0 / max(w, 1)

    def grad(x):
        return np.abs(cv2.Sobel(x, cv2.CV_32F, 1, 0, ksize=3)) + np.abs(cv2.Sobel(x, cv2.CV_32F, 0, 1, ksize=3))

    diff = ref - v
    trust = ((grad(v) < edge) & (grad(ref) < edge) & (np.abs(diff) < MATCH_MAX_DIFF)).astype(np.float32)
    sigma = max(2.0, MATCH_SIGMA * w)
    num = cv2.GaussianBlur(diff * trust, (0, 0), sigma)
    den = cv2.GaussianBlur(trust, (0, 0), sigma)
    fill = num / np.maximum(den, 1e-3) * np.clip(den * 4.0, 0.0, 1.0)
    return np.clip(v + float(strength) * fill, 0.0, 1.0).astype(np.float32)


def apply_global(v: np.ndarray, g: GlobalSettings) -> np.ndarray:
    if g.is_identity():
        return v
    if g.has_match():
        v = local_match(v, g._match_ref, float(g.match_strength))
    if g.has_despeck():
        from core.depthedit.despeck import despeck
        cleaned = despeck(v, float(g.despeck_strength), **g.despeck_args())
        w = zone_weight(g.despeck_zone(), v.shape)
        v = cleaned if w is None else (v + (cleaned - v) * w).astype(np.float32)
    x = 1.0 - v if g.invert else v.copy()
    span = max(g.in_white - g.in_black, 1e-4)
    x = (x - g.in_black) / span
    if g.gamma != 1.0:
        # Gamma on the clipped value: a negative base has no real power.
        x = np.power(np.clip(x, 0.0, 1.0), 1.0 / max(g.gamma, 1e-3))
    x = (x - 0.5) * g.contrast + 0.5 + g.brightness
    return np.clip(x, 0.0, 1.0).astype(np.float32)


# --------------------------------------------------------------------------
# Masks
# --------------------------------------------------------------------------


def _ellipse(radius: int) -> np.ndarray:
    k = 2 * int(radius) + 1
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))


#: Frames at least this big take the heavy steps -- Edge snap's guided filter,
#: Feather's and Smooth's Gaussians -- on the GPU (user, 2026-10-04): that is
#: Render at source resolution (1920x1038: ~150 ms a frame on the CPU). The
#: editor's own frames (the depth map's size) stay on the CPU, exactly as
#: before -- the copy to the GPU and back would cost more than it saves.
GPU_MIN_PIXELS = 1_000_000


def _on_gpu(a: np.ndarray) -> bool:
    if a.size < GPU_MIN_PIXELS:
        return False
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


def _gpu(a: np.ndarray):
    import torch
    return torch.from_numpy(np.ascontiguousarray(a, dtype=np.float32)).to("cuda")[None, None]


def _gaussian(a: np.ndarray, sigma: float) -> np.ndarray:
    """cv2.GaussianBlur(a, (0, 0), sigma) for a float32 frame -- on the GPU
    for a big one (core.common.gpu_ops: OpenCV's kernel and border)."""
    if _on_gpu(a):
        try:
            from core.common import gpu_ops
            return gpu_ops.gaussian_blur(_gpu(a), float(sigma))[0, 0].cpu().numpy()
        except Exception as e:
            logger.warning("Blur on the GPU failed (%s); using the CPU.", e)
    return cv2.GaussianBlur(a, (0, 0), sigmaX=sigma)


#: Guided-filter regulariser, in unit depth squared. Small enough that a depth
#: step of a few percent counts as an edge; measured on a real map it is the
#: value that put the most mask boundary on depth edges.
EDGE_SNAP_EPS = 1e-4


def snap_mask(mask: np.ndarray, guide: Optional[np.ndarray], radius: int) -> np.ndarray:
    """Pull a mask's boundary onto the edges of `guide` (the original depth).

    SAM predicts masks at a fraction of the source resolution and upsamples
    them, and a mask made on the RGB follows colour edges, which a depth map's
    edges only roughly match. The edit belongs where the DEPTH changes. A
    guided filter of the mask, steered by the depth, moves each boundary pixel
    to whichever side of the nearest depth step it belongs to. Measured on a
    real 768x448 map (cable car, 2026-09-22): boundary pixels within 1px of a
    depth edge went 85% -> 98% for an RGB-made mask, 95% -> 98% for a depth-
    made one, and a notch the RGB mask cut into a thin strut closed.
    """
    if radius <= 0 or guide is None or not mask.any():
        return mask
    g = np.clip(guide, 0.0, 1.0).astype(np.float32)
    if _on_gpu(g):
        try:
            from core.common import gpu_ops
            a = gpu_ops.guided_filter(_gpu(g), _gpu(mask), int(radius), EDGE_SNAP_EPS)
            return (a[0, 0] >= 0.5).cpu().numpy()
        except Exception as e:
            logger.warning("Edge snap on the GPU failed (%s); using the CPU.", e)
    a = cv2.ximgproc.guidedFilter(guide=g, src=mask.astype(np.float32), radius=int(radius), eps=EDGE_SNAP_EPS)
    return a >= 0.5


def effective_mask(mask: np.ndarray, s: LayerSettings, guide: Optional[np.ndarray]) -> np.ndarray:
    """The mask a layer actually edits: snapped, then filled / grown / shrunk.

    Remembered for a (mask content, edge snap / fill / grow, depth frame): the
    editor asked for it two or three times a redraw -- the edit, the overlay,
    the gradient markers -- and each is a guided filter, ~48 ms at 1080p
    (audit 2026-09-28 O6). Only a READ-ONLY guide is remembered by identity
    (the editor's cached depth frames are): a writable one could change under
    the cache, so it is simply worked out. The result is read-only.
    """
    snaps = s.edge_snap > 0 and guide is not None
    if snaps and guide.flags.writeable:
        return shape_mask(snap_mask(mask, guide, s.edge_snap), s)
    import hashlib
    key = (hashlib.blake2b(np.packbits(mask.astype(bool), axis=None).tobytes(), digest_size=16).digest(),
           mask.shape, int(s.edge_snap) if snaps else 0, bool(s.fill_holes), _split_px(s.grow)[0],
           int(round(s.close_gaps)),
           id(guide) if snaps else None)
    hit = _MASK_CACHE.get(key)
    if hit is not None and hit[0] is (guide if snaps else None):
        _MASK_CACHE.move_to_end(key)
        return hit[1]
    out = shape_mask(snap_mask(mask, guide, s.edge_snap), s)
    out.flags.writeable = False
    _MASK_CACHE[key] = (guide if snaps else None, out)   # the guide kept alive: its id can't be reused
    while len(_MASK_CACHE) > _MASK_CACHE_SIZE:
        _MASK_CACHE.popitem(last=False)
    return out


#: The last few layers x frames worked out (a 1080p mask is 2 MB).
_MASK_CACHE_SIZE = 48
_MASK_CACHE: "OrderedDict" = OrderedDict()


def clear_mask_cache() -> None:
    _MASK_CACHE.clear()


def shape_mask(mask: np.ndarray, s: LayerSettings) -> np.ndarray:
    """Binary mask after fill / grow / shrink, as uint8 0/1. Grown by the
    WHOLE pixels of `s.grow`; apply_layer blends in the fraction."""
    m = mask.astype(np.uint8)
    close = int(round(s.close_gaps))
    if close > 0 and m.any():
        # padded, so a gap at the frame's edge closes as one in the middle does
        k = _ellipse(close)
        p = cv2.copyMakeBorder(m, close, close, close, close, cv2.BORDER_REPLICATE)
        m = cv2.morphologyEx(p, cv2.MORPH_CLOSE, k)[close:-close, close:-close]
    if s.fill_holes and m.any():
        contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        m = np.zeros_like(m)
        cv2.drawContours(m, contours, -1, 1, thickness=cv2.FILLED)
    grow = _split_px(s.grow)[0]
    if grow > 0:
        m = cv2.dilate(m, _ellipse(grow))
    elif grow < 0:
        m = cv2.erode(m, _ellipse(-grow))
    return m


def feather_alpha(m: np.ndarray, feather: int) -> np.ndarray:
    a = m.astype(np.float32)
    if feather > 0:
        # sigma of half the radius puts most of the falloff inside `feather`
        # pixels either side of the edge, which is what the number promises.
        a = _gaussian(a, max(feather / 2.0, 0.5))
    return a


def _masked_blur(v: np.ndarray, m: np.ndarray, sigma: float) -> np.ndarray:
    """Blur `v` using only pixels inside `m`, so outside depth cannot bleed in.

    Normalised convolution: blur(v*m) / blur(m). Outside the mask the result
    is meaningless and callers only read it inside.
    """
    mf = m.astype(np.float32)
    num = _gaussian((v * mf).astype(np.float32), sigma)
    den = _gaussian(mf, sigma)
    return np.where(den > 1e-4, num / np.maximum(den, 1e-4), v).astype(np.float32)


def mask_bbox(m: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
    """(x0, y0, x1, y1) inclusive-exclusive, or None for an empty mask."""
    ys = np.flatnonzero(m.any(axis=1))
    if ys.size == 0:
        return None
    xs = np.flatnonzero(m.any(axis=0))
    return int(xs[0]), int(ys[0]), int(xs[-1]) + 1, int(ys[-1]) + 1


def object_stats(v: np.ndarray, m: np.ndarray) -> Optional[Dict[str, float]]:
    """Median and 5th/95th percentiles of the depth under a mask."""
    vals = v[m.astype(bool)]
    if vals.size == 0:
        return None
    if vals.size > 200_000:  # a percentile of a sample is plenty for a pivot
        vals = vals[:: vals.size // 200_000 + 1]
    p5, med, p95 = np.percentile(vals, (5, 50, 95))
    return {"median": float(med), "p5": float(p5), "p95": float(p95)}


# --------------------------------------------------------------------------
# Gradient
# --------------------------------------------------------------------------


def gradient_endpoints(g: Gradient, width: int, height: int,
                       bbox: Optional[Tuple[int, int, int, int]]) -> Tuple[Tuple[float, float], Tuple[float, float]]:
    """p0/p1 in pixel coordinates for this frame."""
    if g.follow_object and bbox is not None:
        x0, y0, x1, y1 = bbox
        ox, oy, sx, sy = x0, y0, max(x1 - x0, 1), max(y1 - y0, 1)
    else:
        ox, oy, sx, sy = 0, 0, width, height
    a = (ox + g.p0[0] * sx, oy + g.p0[1] * sy)
    b = (ox + g.p1[0] * sx, oy + g.p1[1] * sy)
    return a, b


def gradient_field(g: Gradient, width: int, height: int,
                   bbox: Optional[Tuple[int, int, int, int]]) -> np.ndarray:
    """0 at the near end, 1 at the far end, clamped, float32 (H, W)."""
    (ax, ay), (bx, by) = gradient_endpoints(g, width, height, bbox)
    xs = np.arange(width, dtype=np.float32)[None, :] + 0.5
    ys = np.arange(height, dtype=np.float32)[:, None] + 0.5
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq < 1e-6:
        return np.zeros((height, width), np.float32)
    if g.shape == "Radial":
        t = np.sqrt((xs - ax) ** 2 + (ys - ay) ** 2) / math.sqrt(length_sq)
    else:
        t = ((xs - ax) * dx + (ys - ay) * dy) / length_sq
    t = np.clip(t, 0.0, 1.0)
    if g.curve != 1.0:
        t = np.power(t, max(g.curve, 1e-3))
    return t.astype(np.float32)


def _apply_gradient(x: np.ndarray, m: np.ndarray, g: Gradient,
                    bbox: Optional[Tuple[int, int, int, int]]) -> np.ndarray:
    h, w = x.shape
    t = gradient_field(g, w, h, bbox)
    ramp = g.near + (g.far - g.near) * t
    if g.keep_detail > 0:
        # Relief = the surface minus a heavily smoothed copy of itself, taken
        # at a scale tied to the object's size so a small object keeps its
        # features and a big one is not flattened into its own low frequency.
        size = max((bbox[2] - bbox[0]) if bbox else w, (bbox[3] - bbox[1]) if bbox else h)
        base = _masked_blur(x, m, sigma=max(size / 12.0, 2.0))
        ramp = ramp + g.keep_detail * (x - base)
    if g.blend == "Only nearer":
        return np.maximum(x, ramp)
    if g.blend == "Only further":
        return np.minimum(x, ramp)
    if g.blend == "Add":
        return x + (ramp - (g.near + g.far) * 0.5)
    return ramp


# --------------------------------------------------------------------------
# Layer
# --------------------------------------------------------------------------


def _column(n: int) -> np.ndarray:
    return np.ones((n + 1, 1), np.uint8)


def _line(n: int, side: str) -> np.ndarray:
    """The one-sided window for `side`: a column for top / bottom, a row for left / right."""
    return _column(n) if side in ("top", "bottom") else np.ones((1, n + 1), np.uint8)


def _anchor(n: int, side: str, outward: bool) -> Tuple[int, int]:
    """Where the window sits on its pixel: `outward` looks past the edge
    (up for the top, left for the left), else into the object."""
    first = (side in ("top", "left")) != outward      # the window starts at the pixel
    at = 0 if first else n
    return (0, at) if side in ("top", "bottom") else (at, 0)


def has_edge_moves(s: "LayerSettings") -> bool:
    return any(getattr(s, name) for name, _side in EDGE_SIDES)


def edge_shift(v: np.ndarray, px: float, top: bool = True, side: Optional[str] = None) -> np.ndarray:
    """One edge of the near depth moved `px` pixels, the other left alone.

    Near is bright, so pulling an edge in is a minimum filter and pushing it
    out a maximum -- over a one-sided column: for the TOP edge, each pixel looks
    at the `n` pixels above it (pulling in: the depth above takes over the
    object's top rows) or below it (pushing out: the object's depth spreads up).
    Nowhere does the window reach the other edge, so it does not move. What a
    splat's negative Y dilation does to both edges of everything, this does to
    one edge of one object (user, 2026-09-28: a beam warped by Y dilation).

    A fractional `px` blends the two nearest whole shifts: the edge row ends
    part-way between near and far, which Splatting's bilinear upscale reads as
    an edge between pixels (user, 2026-10-01: finer steps).
    """
    side = side or ("top" if top else "bottom")
    whole, frac = _split_px(px)
    sign = -1 if float(px) < 0 else 1
    lo = _edge_shift_whole(v, whole, side=side)
    if frac == 0.0:
        return lo
    hi = _edge_shift_whole(v, whole + sign, side=side)
    return lo + (hi - lo) * np.float32(frac)


def _edge_shift_whole(v: np.ndarray, px: int, top: bool = True, side: Optional[str] = None) -> np.ndarray:
    side = side or ("top" if top else "bottom")
    n = abs(int(px))
    if n == 0:
        return v
    if px < 0:  # pull in: look outward, past this edge
        return cv2.erode(v, _line(n, side), anchor=_anchor(n, side, True), borderType=cv2.BORDER_REPLICATE)
    # push out: look inward, into the object
    return cv2.dilate(v, _line(n, side), anchor=_anchor(n, side, False), borderType=cv2.BORDER_REPLICATE)


def _edge_moves(x: np.ndarray, s: "LayerSettings") -> np.ndarray:
    for name, side in EDGE_SIDES:
        px = getattr(s, name)
        if px:
            x = edge_shift(x, px, side=side)
    return x


def _edge_region(m: np.ndarray, s: "LayerSettings") -> np.ndarray:
    """Where the edge moves may write: the mask, stretched past an edge that
    is pushed OUT (its new rows / columns lie outside the object)."""
    r = m
    for name, side in EDGE_SIDES:
        px = getattr(s, name)
        if px > 0:
            n = int(np.ceil(px))              # a fraction still reaches the next row
            # the region grows the way the edge goes: the window looks inward
            r = cv2.dilate(r, _line(n, side), anchor=_anchor(n, side, False))
    return r


def layer_active(s: LayerSettings, frame_idx: int) -> bool:
    if s.start_frame >= 0 and frame_idx < s.start_frame:
        return False
    if s.end_frame >= 0 and frame_idx > s.end_frame:
        return False
    return True


#: Whether an Erase layer fills the depth inside its mask from around it (erase_fill). For ROSE, yes: the object
#: comes out of the picture, so its shape must leave the map too. For Dust and Static, no (user, 2026-10-17: the
#: windows' depth filled from the car frame came out as streaks and the road behind the glass lost its depth) --
#: what comes off is on the glass, the depth seen through it stays. The editor sets it from the clip's own erase
#: (putback.erase_fills_depth), or Erase's Method for a clip not erased yet.
ERASE_FILLS_DEPTH = True


def apply_layer(v: np.ndarray, mask: Optional[np.ndarray], s: LayerSettings,
                guide: Optional[np.ndarray] = None) -> np.ndarray:
    """Edit `v` inside `mask`. Returns a new array; `v` is not touched.

    `guide` is the ORIGINAL depth frame, which Edge snap aligns the mask to --
    not `v`, which lower layers may already have moved.
    """
    if mask is None or s.opacity <= 0:
        return v
    if s.mode == "Add":
        return _add_layer(v, mask, s, guide)
    if s.mode == "Erase":
        return _erase_layer(v, mask, s, guide) if ERASE_FILLS_DEPTH else v
    m = effective_mask(mask, s, v if guide is None else guide)
    if not m.any():
        return v
    if has_despeck(s):
        v = _despeck(v, m, s)          # first: the rest of Alter works on the cleaned depth
    if has_level_edge(s):
        v = _level_to_edge(v, m, s)
    stats = object_stats(v, m)
    med = stats["median"] if stats else 0.5
    x = v
    region = m
    if has_edge_moves(s):
        x = _edge_moves(x, s)
        region = _edge_region(m, s)
    if s.smooth > 0:
        x = _masked_blur(x, m, sigma=float(s.smooth))
    if s.contrast != 1.0:
        x = med + (x - med) * s.contrast
    if s.flatten > 0:
        x = x + (med - x) * s.flatten
    if s.brightness != 0.0:
        x = x + s.brightness
    if s.gradient.enabled:
        x = _apply_gradient(np.asarray(x, np.float32), m, s.gradient, mask_bbox(m))
    x = np.clip(x, 0.0, 1.0)
    weight = region.astype(np.float32)
    whole, frac = _split_px(s.grow)
    if frac > 0.0:
        # the fraction of a pixel of grow / shrink: the next whole pixel's
        # mask, blended in by that fraction (the binary mask above, which the
        # statistics and the gradient use, is the whole-pixel one)
        step = replace(s, grow=float(whole + (-1 if s.grow < 0 else 1)))
        m2 = effective_mask(mask, step, v if guide is None else guide)
        r2 = _edge_region(m2, s) if has_edge_moves(s) else m2
        weight = weight + (r2.astype(np.float32) - weight) * np.float32(frac)
    alpha = feather_alpha(weight, s.feather) * float(s.opacity)
    return (v + (x - v) * alpha).astype(np.float32)


def _add_layer(v: np.ndarray, mask: np.ndarray, s: LayerSettings, guide: Optional[np.ndarray]) -> np.ndarray:
    """Add: the mask is a new shape at add_depth (Push / pull and the gradient
    on top), whatever the depth map had there. Grow / shrink, Fill holes,
    Feather and Opacity shape its edge as in Alter; Edge snap does not apply.
    Alter's other controls too (user, 2026-10-11): the four edge moves move
    the shape's own edges, and Smooth / Contrast / Flatten work on its depth
    about its middle (with a gradient on -- a flat shape has none to change).
    At their defaults the shape is what it always was."""
    s = replace(s, edge_snap=0)
    m = effective_mask(mask, s, v if guide is None else guide)
    if not m.any():
        return v
    x = np.full(v.shape, float(s.add_depth) + float(s.brightness), np.float32)
    if s.gradient.enabled:
        x = _apply_gradient(x, m, s.gradient, mask_bbox(m))
    if s.smooth > 0 or s.contrast != 1.0 or s.flatten > 0:
        if s.smooth > 0:
            x = _masked_blur(x, m, sigma=float(s.smooth))
        # m is 0/1 uint8 (OpenCV): as an index that picks rows -- the wrong values, and an
        # (H, W, W) temporary, ~16 GB at 1080p (bug sweep 2026-10-14c)
        med = float(np.median(x[m > 0]))
        if s.contrast != 1.0:
            x = med + (x - med) * s.contrast
        if s.flatten > 0:
            x = x + (med - x) * s.flatten
    x = np.clip(x, 0.0, 1.0)
    weight = m.astype(np.float32)
    whole, frac = _split_px(s.grow)
    if frac > 0.0:
        step = replace(s, grow=float(whole + (-1 if s.grow < 0 else 1)))
        m2 = effective_mask(mask, step, v if guide is None else guide)
        weight = weight + (m2.astype(np.float32) - weight) * np.float32(frac)
    if has_edge_moves(s):
        # the shape is the near thing here: one edge of it moved as Alter moves
        # an object's depth edge (a fraction ends the edge part-way)
        weight = _edge_moves(weight, s)
    alpha = feather_alpha(weight, s.feather) * float(s.opacity)
    return (v + (x - v) * alpha).astype(np.float32)


#: Erase fills this many pixels beyond the mask too: a depth map's edge is a
#: pixel or two softer than the object's outline, and what is left of it
#: would stand out as a halo where the object was.
ERASE_MARGIN = 2


def erase_fill(v: np.ndarray, m: np.ndarray) -> np.ndarray:
    """`v` with the pixels under `m` filled from the depth around them
    (OpenCV's Telea inpainting, on the map as 16-bit: its float path returns
    values far outside the depth around the hole). A large hole is filled on
    a smaller copy, enlarged back: the fill is smooth anyway, and Telea's
    time grows with the hole's area."""
    m8 = (m > 0).astype(np.uint8)
    if not m8.any():
        return v
    if ERASE_MARGIN > 0:
        m8 = cv2.dilate(m8, _ellipse(ERASE_MARGIN))
    src = np.ascontiguousarray(v, dtype=np.float32)
    u16 = (np.clip(src, 0.0, 1.0) * 65535.0 + 0.5).astype(np.uint16)
    h, w = src.shape
    area = int(m8.sum())
    k = 1
    while area / (k * k) > 40000 and min(h, w) // (k * 2) >= 32:
        k *= 2
    if k == 1:
        filled = cv2.inpaint(u16, m8, 3, cv2.INPAINT_TELEA)
    else:
        small = cv2.resize(u16, (w // k, h // k), interpolation=cv2.INTER_AREA)
        ms = (cv2.resize(m8.astype(np.float32), (w // k, h // k), interpolation=cv2.INTER_AREA) > 0).astype(np.uint8)
        filled = cv2.resize(cv2.inpaint(small, ms, 3, cv2.INPAINT_TELEA), (w, h), interpolation=cv2.INTER_LINEAR)
    filled = filled.astype(np.float32) / 65535.0
    out = src.copy()
    out[m8 > 0] = filled[m8 > 0]
    return out


def _erase_layer(v: np.ndarray, mask: np.ndarray, s: LayerSettings, guide: Optional[np.ndarray]) -> np.ndarray:
    """Erase: the object's depth gone, filled from around it. Edge snap, Grow /
    shrink and Fill holes shape the mask as in Alter; Feather and Opacity
    blend the fill in. The depth settings don't apply."""
    m = effective_mask(mask, s, v if guide is None else guide)
    if not m.any():
        return v
    filled = erase_fill(v, m)
    weight = m.astype(np.float32)
    if ERASE_MARGIN > 0:
        weight = cv2.dilate(weight, _ellipse(ERASE_MARGIN))
    alpha = feather_alpha(weight, s.feather) * float(s.opacity)
    return (v + (filled - v) * alpha).astype(np.float32)


#: Despeck reaches this many pixels beyond the mask: the speck sits on the
#: object's own left edge, which a mask (snapped or not) may stop just short of.
DESPECK_MARGIN = 2


def has_despeck(s: LayerSettings) -> bool:
    return s.mode == "Alter" and bool(s.despeck_on) and float(s.despeck_strength) > 0


def has_level_edge(s: LayerSettings) -> bool:
    return s.mode == "Alter" and bool(s.level_edge) and float(s.despeck_strength) > 0


#: Level to edge's ring: this many pixels in from the mask's edge
EDGE_RING = 2


def edge_level(v: np.ndarray, m: np.ndarray) -> Optional[float]:
    """The grey around the mask's outer edge: the median of a ring EDGE_RING px in
    from it (a median, so the odd bright or background pixel on the ring doesn't
    move it). None: no ring (an empty or one-pixel mask)."""
    m8 = m.astype(np.uint8)
    ring = (m8 > 0) & (cv2.erode(m8, _ellipse(EDGE_RING)) == 0)
    if not ring.any():
        return None
    return float(np.median(v[ring]))


def _level_to_edge(v: np.ndarray, m: np.ndarray, s: LayerSettings) -> np.ndarray:
    """Level to edge: anything in the mask brighter than the grey around its edge brought
    down to it, by Strength; feathered by the mask's Feather, Opacity on top."""
    level = edge_level(v, m)
    if level is None:
        return v
    took = np.clip(v - level, 0.0, None) * float(s.despeck_strength)
    alpha = feather_alpha(m.astype(np.float32), s.feather) * float(s.opacity)
    return (v - took * alpha).astype(np.float32)


def _despeck(v: np.ndarray, m: np.ndarray, s: LayerSettings) -> np.ndarray:
    """Alter's Despeck: the left-edge specks of the frame found (core.depthedit.despeck,
    on the whole frame -- an edge needs the background beside it), kept to the mask as
    edited (`m`) plus DESPECK_MARGIN, feathered; Strength is how far, Opacity on top."""
    from core.depthedit.despeck import despeck
    cleaned = despeck(v, float(s.despeck_strength))
    weight = m.astype(np.float32)
    if DESPECK_MARGIN > 0:
        weight = cv2.dilate(weight, _ellipse(DESPECK_MARGIN))
    alpha = feather_alpha(weight, s.feather) * float(s.opacity)
    return (v + (cleaned - v) * alpha).astype(np.float32)


def speck_highlight(v: np.ndarray, layers: Sequence[Tuple[LayerSettings, Optional[np.ndarray]]],
                    frame_idx: int, whole: bool = False,
                    global_args: Optional[Dict[str, float]] = None) -> Tuple[np.ndarray, np.ndarray]:
    """Highlight Specks (user, 2026-10-12: "so the user can see the speck being captured"):
    how far Despeck would lower each pixel of the frame `v`, and where a layer with
    Despeck on, active on this frame, covers it (its mask as edited, plus the margin) --
    the ones still to cover, and the ones taken care of. `whole`: the Global
    Despeck is on, so every one is, found with its dials (`global_args`: what it
    leaves isn't shown)."""
    from core.depthedit.despeck import despeck
    took = (v - despeck(v, 1.0, **({k: a for k, a in (global_args or {}).items() if k != "zone"} if whole else {}))
            ).astype(np.float32)
    covered = np.full(v.shape, bool(whole))
    if whole:
        zw = zone_weight((global_args or {}).get("zone"), v.shape)
        if zw is None:
            return took, covered
        covered = zw > 0.5                  # outside the zone they show as still to cover
    for s, mask in layers:
        if (mask is None or not (has_despeck(s) or has_level_edge(s)) or s.opacity <= 0
                or not layer_active(s, frame_idx)):
            continue
        if has_level_edge(s):
            # what Level to edge takes counts as found, and as covered
            me = effective_mask(mask, s, v).astype(bool)
            level = edge_level(v, me)
            if level is not None:
                took = np.where(me, np.maximum(took, v - level), took).astype(np.float32)
                covered |= me & (v > level)
            if not has_despeck(s):
                continue
        m = effective_mask(mask, s, v).astype(np.uint8)
        if DESPECK_MARGIN > 0:
            m = cv2.dilate(m, _ellipse(DESPECK_MARGIN))
        covered |= m > 0
    return took, covered


def sample_add_depth(v: np.ndarray, mask: Optional[np.ndarray]) -> Optional[float]:
    """Add's depth from the object itself: the nearest part (90th percentile)
    of what the depth map has under the mask. None: no mask."""
    if mask is None or not mask.any():
        return None
    return float(np.percentile(v[mask], 90))


def apply_all(v: np.ndarray, global_settings: GlobalSettings,
              layers: Sequence[Tuple[LayerSettings, Optional[np.ndarray]]],
              frame_idx: int) -> np.ndarray:
    """The whole edit for one frame. `layers` is (settings, mask) bottom-up."""
    x = apply_global(v, global_settings)
    for s, mask in layers:
        if layer_active(s, frame_idx):
            x = apply_layer(x, mask, s, guide=v)
    return x
