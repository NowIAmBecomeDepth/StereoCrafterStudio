"""despeck.py -- DepthCrafter's bright specks on left edges taken out of a depth map.

DepthCrafter now and then puts a bright spot just inside an object's LEFT
silhouette (user and a fellow converter, 2026-10-12: "it happens on the left
side of things pretty much every time"; "mostly when there is little or no
brighter object in front of it"). Measured on two test clips:
42 against 4 on left edges in one, 26 against 13 in the other. Some are hard
specks (a bone's tip 0.95 on a body of 0.85), others a soft glow: the depth
ramps up towards the left edge and falls to the background there (a chair
0.17 inside, 0.20 at its left edge, the background 0.07). In the warp the
right eye moves near pixels left, so a left-edge spot pokes out into the
background as a spike; one on a right edge mostly ends under the object.

Neither rain's detector nor a size filter suits them: a spot often stays for
dozens of frames (no frame-to-frame test sees it), a glow has no edge for a
top-hat, and the glow joins whatever nearer thing is below it, so it is no
peak of its own (an area opening missed every glow in the test clip).

What this does, per frame: find the left silhouettes (the depth drops by more
than STEP within GAP of the width to the left); within REACH of the width
inside each, cap the depth at the median of the object's own interior a
little further right -- sampled at SAMPLES offsets from BAND/3 to 2 x BAND,
counting only samples still on the object (nearer than half way from the
background beyond the edge to this pixel), so a narrow head or a helmet's top
row is not capped at the background past it; with fewer than MIN_VALID the
pixel is left alone. The change fades out towards the far end of the reach, is
feathered, and only ever lowers the depth.
A cap, not a removal, so a hard speck and a soft glow come out the same way.
The area the cap marks is then rebuilt from the object's surface all round it
(_rebuild: a membrane held by the pixels around; not the background, nor the
outline's fall-off), so a speck on a ridge or a small rounded thing comes down
onto the surface instead of into a dip.
On the two clips the removal itself is steady: its frame-to-frame change is
p99.9 0.019 / 0.004, against the depth's own 0.47 / 0.70.

A real nearer left side (a face turned three-quarters) loses a little within
the band; the layer's Opacity is how far it goes.

The Depth Map Editor's Despeck layers (edit_ops) run it on the whole frame --
an edge needs the background beside it -- and keep it inside a tracked mask
only (user, 2026-10-12: the editor, not Splatting; "a selection zone where the
remover only works in that area"). The grey level each speck is lowered to
comes from the pixels beside it on every frame, so nothing is picked by hand.

Torch on the GPU when there is one, else on the CPU: the same code either way.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import torch
import torch.nn.functional as F

BAND = 0.03      # of the width: where the interior is sampled (BAND/3 .. 2 x BAND in)
GAP = 0.01       # of the width: how sharply the depth must drop to the left
STEP = 0.04      # of the depth range: the drop that makes a left edge
TOL = 0.004      # of the depth range: what a pixel may stand above its interior
SAMPLES = 12
MIN_VALID = 2    # interior samples that must still be on the object (2: a head ~16 px wide
                 # still finds its own right half; half of them left the test clip's heads alone)
LEVEL = 0.5      # which of them the cap is at (0.5 = their median)
TAPER = 0.25     # the share of REACH capped in full before it fades out (user, 2026-10-12: "how can we
                 # blend these spots better" -- cut off at REACH it left hard lines and flat patches;
                 # faded from 25% the added roughness on V1's bone went 14 -> 2.5, the specks as low.
                 # Evening it out between rows instead gave the specks back: 5.5 -> 12.9% left)
CORNER = True    # where a row has nothing to go by, the level taken up and down instead
FILL = True      # the specks' area rebuilt from the surface around it (_rebuild)
FILL_STEPS = 0.15  # of the width: the rebuild's passes
FILL_TOL = 0.02  # of the depth range: how far below that level a neighbour may still count
FILL_BOUND = 2   # px: the rebuild lowers a pixel no more than the cap took this close to it
                 # (evening the removal out over the area instead gave the specks back: bone 5 -> 11%)
ON_OBJECT = 0.5  # a sample counts when it is this far from the background to this pixel (0.7 kept
                 # the head's seams no better and left the shoulder glow 2.2% high, against 0.5%)
REACH = 0.02     # of the width: the band capped inside a left edge (1% left a shoulder glow 1.3% of
                 # the range high, 2% 0.5%; 3% no better. Kept to a layer's mask, the wider band is fine)


def _device(device: Optional[str]) -> torch.device:
    if device:
        return torch.device(device)
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def _shift(t: torch.Tensor, k: int, fill: float) -> torch.Tensor:
    """out[..., x] = t[..., x - k] (k > 0: from the left; k < 0: from the right)."""
    if k == 0:
        return t
    if k > 0:
        return F.pad(t[..., :-k], (k, 0), value=fill)
    return F.pad(t[..., -k:], (0, -k), value=fill)


def _shift_y(t: torch.Tensor, k: int) -> torch.Tensor:
    """out[..., y, :] = t[..., y + k, :] (k > 0: from below), NaN past the frame."""
    if k == 0:
        return t
    if k > 0:
        return F.pad(t[..., k:, :], (0, 0, 0, k), value=float("nan"))
    return F.pad(t[..., :k, :], (0, 0, -k, 0), value=float("nan"))


def _min_left(t: torch.Tensor, n: int) -> torch.Tensor:
    """The minimum over [x - n + 1 .. x] (edge pixels repeated)."""
    return -F.max_pool2d(F.pad(-t, (n - 1, 0, 0, 0), mode="replicate"), (1, n), stride=1)


def _gauss(t: torch.Tensor, sigma: float) -> torch.Tensor:
    r = max(1, int(math.ceil(3 * sigma)))
    x = torch.arange(-r, r + 1, device=t.device, dtype=t.dtype)
    g = torch.exp(-(x * x) / (2 * sigma * sigma))
    g = g / g.sum()
    t = F.conv2d(F.pad(t, (r, r, 0, 0), mode="reflect"), g.view(1, 1, 1, -1))
    return F.conv2d(F.pad(t, (0, 0, r, r), mode="reflect"), g.view(1, 1, -1, 1))


def _rebuild(d: torch.Tensor, over: torch.Tensor, keep: torch.Tensor) -> torch.Tensor:
    """The specks' area rebuilt from the surface around it, not capped row by row
    (user, 2026-10-12: "still darkening too much" -- a ridge sloping down to the right,
    a small rounded thing: the depth to the right on its own row was lower than the
    surface above and below, and the spot sank into a dip). The area (the cap's,
    a pixel wider) is filled like a stretched membrane: each pixel the average of its
    four neighbours, over and over, the pixels around held as they are. Neighbours
    on the background beyond the edge (below `keep`) are left out, so the fill comes
    from the object only; it never rises above the map. Returns the new removal."""
    area = F.max_pool2d((over > 0.002).float(), 3, stride=1, padding=1) > 0.5
    if not bool(area.any()):
        return over
    ys, xs = torch.nonzero(area[0, 0], as_tuple=True)
    m = 2
    y0, y1 = max(0, int(ys.min()) - m), min(d.shape[-2], int(ys.max()) + m + 1)
    x0, x1 = max(0, int(xs.min()) - m), min(d.shape[-1], int(xs.max()) + m + 1)
    dd, kk, aa = d[..., y0:y1, x0:x1], keep[..., y0:y1, x0:x1], area[..., y0:y1, x0:x1]
    u = dd - over[..., y0:y1, x0:x1]
    pad = lambda t: F.pad(t, (1, 1, 1, 1), mode="replicate")  # noqa: E731
    dp = pad(dd)
    nbd = [dp[..., :-2, 1:-1], dp[..., 2:, 1:-1], dp[..., 1:-1, :-2], dp[..., 1:-1, 2:]]
    # a neighbour counts when it is on the object at the level this pixel comes down to --
    # not the background, nor the soft fall-off along the object's outline (taken in, it
    # dragged the fill down towards the edge: a dip)
    target = u - FILL_TOL
    ok = [((n > kk) & (n >= target)).float() for n in nbd]
    cnt = sum(ok)
    live = aa & (cnt > 0)
    # never lower than the cap took close by (user, 2026-10-12: despecking the whole frame left a
    # dot on V1's bone tip -- the rebuild sank the surface round an untouched middle, which then
    # stood out): at most what the cap took within FILL_BOUND px, so it smooths, never digs
    if FILL_BOUND > 0:
        kb = 2 * FILL_BOUND + 1
        floor = dd - F.max_pool2d(over[..., y0:y1, x0:x1], kb, stride=1, padding=FILL_BOUND)
    else:
        floor = torch.full_like(dd, -1.0)
    for _ in range(max(40, int(FILL_STEPS * d.shape[-1]))):
        up = pad(u)
        nb = [up[..., :-2, 1:-1], up[..., 2:, 1:-1], up[..., 1:-1, :-2], up[..., 1:-1, 2:]]
        avg = sum(o * n for o, n in zip(ok, nb)) / cnt.clamp(min=1.0)
        u = torch.where(live, torch.minimum(torch.maximum(avg, floor), dd), u)
    took = torch.where(aa, (dd - u).clamp(min=0.0), torch.zeros_like(u))
    out = over.clone()
    out[..., y0:y1, x0:x1] = torch.where(aa, took, torch.zeros_like(u))
    return out


def _gate(over: torch.Tensor, threshold: float) -> torch.Tensor:
    """Threshold (user, 2026-10-12: a lamp in a test clip, "nothing wrong with this part of the shot, but
    despeck changes it"): each spot judged whole, by how far its highest point stands above
    its object -- below half the threshold it is left alone, from the threshold up taken in
    full, eased between; a spot's faint fringe goes with it. (Judged pixel by pixel, a speck's
    fringe and a lamp's gentle shading looked alike: the lamp kept changing until the real
    specks were lost.)"""
    m = (over > 0.002)[0, 0].cpu().numpy().astype(np.uint8)
    if not m.any():
        return over
    import cv2
    n, lab = cv2.connectedComponents(m, connectivity=8)
    o = over[0, 0].cpu().numpy()
    peak = np.zeros(n, np.float32)
    np.maximum.at(peak, lab.ravel(), o.ravel())
    t = np.clip((peak - 0.5 * threshold) / (0.5 * threshold), 0.0, 1.0)
    w = (t * t * (3.0 - 2.0 * t)).astype(np.float32)
    w[0] = 0.0
    return over * torch.from_numpy(w[lab]).to(over.device)[None, None]


def _removal(d: torch.Tensor, threshold: float = 0.0, reach: Optional[float] = None,
             step: Optional[float] = None) -> torch.Tensor:
    """How much each pixel of `d` (1, 1, H, W; 0..1, bright = near) stands
    above its object's interior just inside a left edge, feathered."""
    W = d.shape[-1]
    w = max(3, int(BAND * W))
    g = max(2, int(GAP * W))
    edge = (d - _shift(_min_left(d, g), 1, 1.0)) > (STEP if step is None else float(step))
    r = max(1, int((REACH if reach is None else float(reach)) * W))
    band = F.max_pool2d(F.pad(edge.float(), (r - 1, 0, 0, 0)), (1, r), stride=1) > 0.5
    far = _min_left(d, w + g)                 # the background beyond the edge
    keep = far + ON_OBJECT * (d - far)
    offs = sorted({int(k) for k in np.linspace(max(2, w // 3), 2 * w, SAMPLES)})
    st = torch.stack([_shift(d, -k, float("nan")) for k in offs])
    st = torch.where(st > keep, st, torch.full_like(st, float("nan")))
    have = torch.isfinite(st).sum(0) >= MIN_VALID
    ref = torch.nanquantile(st, LEVEL, dim=0)
    if CORNER:
        # a corner (user, 2026-10-12: a dot left on top of V1's bone tip, despecking the whole
        # frame): along its own row there is only background to the right, so nothing to go by --
        # there the object's level is taken up and down from it instead
        sv = torch.stack([_shift_y(d, k) for k in offs] + [_shift_y(d, -k) for k in offs])
        sv = torch.where(sv > keep, sv, torch.full_like(sv, float("nan")))
        have_v = torch.isfinite(sv).sum(0) >= MIN_VALID
        use_v = ~have & have_v
        if bool(use_v.any()):
            ref = torch.where(use_v, torch.nanquantile(sv, LEVEL, dim=0), ref)
            have = have | use_v
    # never lower than the level most of its neighbours are capped at: one pixel whose few
    # samples fell on an object's soft far edge (a narrow head's right side, a helmet's top
    # row) would come down alone, a dark seam; a glow's levels agree, so it is untouched
    filled = torch.where(have, ref, d)
    around = F.unfold(F.pad(filled, (1, 1, 1, 1), mode="replicate"), 3).median(dim=1).values.view_as(d)
    ref = torch.maximum(ref, around)
    over = torch.where(band & have, (d - (ref + TOL)).clamp(min=0.0), torch.zeros_like(d))
    over = torch.nan_to_num(over, nan=0.0)
    if threshold > 0:
        over = _gate(over, float(threshold))
    if TAPER < 1.0:
        # faded out towards the far end of the reach, not cut off there (a hard line inside the
        # object): full for the first TAPER of it, down to nothing at REACH
        dist = torch.full_like(d, float(r))
        for k in range(r):
            hit = _shift(edge.float(), k, 0.0) > 0.5
            dist = torch.where(hit & (dist >= r), torch.full_like(d, float(k)), dist)
        t = ((dist / r - TAPER) / max(1e-6, 1.0 - TAPER)).clamp(0.0, 1.0)
        over = over * (1.0 - t * t * (3.0 - 2.0 * t))
    if FILL:
        over = _rebuild(d, over, keep)
    # Feathered inwards only: the full cap where it is due (a plain blur left a third of
    # a speck on the object's outermost pixels, mixed with the background's none), and
    # softened over a few pixels further in and between rows -- never out over the
    # edge, where it would dig a dark rim into the background
    sigma = max(1.0, W / 768.0)
    k = int(math.ceil(3 * sigma))
    # (sideways only: grown up or down, it would reach the background above an object's top)
    soft = F.max_pool2d(F.pad(band.float(), (k, 0, 0, 0)), (1, k + 1), stride=1) > 0.5
    return torch.where(soft, torch.maximum(over, _gauss(over, sigma)), torch.zeros_like(over)).clamp(min=0.0)


def despeck(depth: np.ndarray, amount: float = 1.0, device: Optional[str] = None,
            threshold: float = 0.0, reach: Optional[float] = None,
            step: Optional[float] = None) -> np.ndarray:
    """`depth` (H, W) or (N, H, W), 0..1, with its left-edge specks lowered by
    `amount` (0 = untouched, 1 = capped at the interior). `threshold`: what a
    speck must stand above its object to be taken; `reach` / `step`: REACH and
    STEP for this call (the Global Despeck's dials). Returns float32, the
    input's shape."""
    a = float(amount)
    src = np.asarray(depth, dtype=np.float32)
    if a <= 0.0 or src.size == 0:
        return src
    flat = src[None] if src.ndim == 2 else src
    dev = _device(device)
    out = np.empty_like(flat)
    with torch.no_grad():
        for i in range(flat.shape[0]):           # one frame at a time: little memory
            d = torch.from_numpy(np.ascontiguousarray(flat[i])).to(dev)[None, None]
            r = _removal(d, float(threshold), reach, step)
            out[i] = (d - min(a, 1.0) * r)[0, 0].cpu().numpy()
    return out[0] if src.ndim == 2 else out
