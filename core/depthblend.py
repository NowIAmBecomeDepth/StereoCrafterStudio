"""depthblend.py -- blend a clip's DepthCrafter and Video Depth Anything maps into one (user, 2026-10-26:
"let's build a non model approach" to "blend the vda and depthcrafter maps"). The other map can be DVD's
instead of VDA's (the Other Map setting; user, 2026-11-07): "VDA" below means whichever it is.

No model: per-pixel maths on the two depth videos, on the card.

Where the maps are -- one folder, Splatting's depth maps. VDA's rerun leaves a clip's maps in it as
core.vda.output does: the map Splatting reads at <folder>/<clip>_depth.mp4, a map of another kind moved
into DepthCrafter/ or VDA/ beside it; each kind told by its tag. ``pairs`` finds both maps of every clip
that has both. The blended map takes the name Splatting reads (tagged as a blend), and the map it
replaces moves into its own kind's folder -- so Splatting shows the blend at once, and nothing is lost.

Lining them up -- the two maps are normalised differently (DepthCrafter per clip; VDA its whole clip or
EMA), so one is fitted onto the other first: ``a * other + b`` by least squares, per clip, per frame
(smoothed over the frames around it), or not at all. The "base" map keeps its own range; the other is
fitted onto it. The fit is measured once per clip at a low resolution (``measure``: per-frame sums), so
every fit, and the preview, comes from those sums.

Blending --
* Mix: (1 - mix) * DepthCrafter + mix * VDA;
* Layout + Detail: the base map's broad depth (blurred) plus the other map's fine detail (it minus its
  own blur), times Detail. Base DepthCrafter, other VDA: DepthCrafter's layout with VDA's edges.

Manual controls (user, 2026-11-07: "do 1-4 first") --
* By Depth (Mix): Mix varies with depth -- Near Mix for what is close, Far Mix for what is far, crossing at
  Split over Softness (the base map's depth, smoothed) -- e.g. VDA's edges up close, DepthCrafter's steady
  background;
* Edge Keep (Layout + Detail): the blurs become edge-preserving (a guided filter), so a big depth step isn't
  taken for detail and leaves no halo beside it; 0 is the plain blur;
* Detail Limit / Noise Floor (Layout + Detail): the detail softly capped at Limit (% of the depth range) and
  wiggles under Floor dropped -- colour texture and shimmer don't come through as relief;
* Edge Band: the other map taken along the frame's left and top edges (or all four), fading out over the
  band -- where DepthCrafter's specks sit (its orientation bias).
* Detail Smoothing (Layout + Detail; user, 2026-11-07: "go ahead with 5-7"): each frame's detail averaged with
  the frames around it, but only where it changes little from frame to frame (shimmer) -- a moving edge's detail
  changes a lot and is left, so it doesn't trail;
* Levels: Black / White point and Gamma on the finished blend.

The output is 10-bit, tv range, as DepthCrafter's and VDA's maps (0..1 on codes 64..940), lossless
through the Depth Map Editor's DepthWriter, written under a .part name and put in place whole.
"""

from __future__ import annotations

import logging
import math
import os
import subprocess
import threading
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from core.vda import output as OUT

logger = logging.getLogger(__name__)

MODES = ("Mix", "Layout + Detail")
#: The model whose map is blended with DepthCrafter's (its kind in core.vda.output: the name, lower case).
OTHERS = ("VDA", "DVD")
#: Base and Output Size name DepthCrafter or the other map (its name: the page lists only the one picked).
BASES = ("DepthCrafter",) + OTHERS
ALIGNS = ("Per clip", "Per frame", "Off")
#: Edge Band: which edges take the other map
BAND_SIDES = ("Left + Top", "Left", "All edges")
SIZES = ("Larger", "DepthCrafter") + OTHERS

#: The width the fit is measured at (its sums; any map shape).
MEASURE_W = 160
#: Per frame: the fit is the sums over this many frames each side, so it can't flicker frame to frame.
FRAME_WINDOW = 12
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ---- the maps ------------------------------------------------------------------------------------

def pairs(folder: str, suffix: str = "_depth", other: str = "VDA") -> List[Dict[str, str]]:
    """Each clip in `folder` with both a DepthCrafter map and `other`'s (OTHERS): [{name, clip, dc, other, out}]
    by name. `out` is where Splatting reads the clip's map -- where the blend goes."""
    if not folder or not os.path.isdir(folder):
        return []
    kind = other.lower() if other in OTHERS else "vda"
    tail = suffix + ".mp4"
    names = set()
    for sub in ("", OUT.KIND_FOLDERS["dc"], OUT.KIND_FOLDERS[kind]):
        try:
            names.update(n for n in os.listdir(os.path.join(folder, sub)) if n.lower().endswith(tail.lower()))
        except OSError:
            pass
    out = []
    for name in sorted(names, key=str.lower):
        root = os.path.join(folder, name)
        here = OUT.kind_of(root) if os.path.isfile(root) else ""
        dc = root if here == "dc" else os.path.join(folder, OUT.KIND_FOLDERS["dc"], name)
        oth = root if here == kind else os.path.join(folder, OUT.KIND_FOLDERS[kind], name)
        if os.path.isfile(dc) and os.path.isfile(oth):
            out.append({"name": name, "clip": name[: -len(tail)], "dc": dc, "other": oth, "out": root})
    return out


def output_size(dc_info, vda_info, choice: str) -> Tuple[int, int]:
    """(width, height) of the blend: the larger map's, or the one named."""
    if choice == "DepthCrafter":
        return dc_info.width, dc_info.height
    if choice in OTHERS:
        return vda_info.width, vda_info.height
    a, b = (dc_info.width * dc_info.height), (vda_info.width * vda_info.height)
    return (vda_info.width, vda_info.height) if b > a else (dc_info.width, dc_info.height)


def read_frames(info, index: int, count: int = 1) -> List[np.ndarray]:
    """`count` frames' 10-bit codes from frame `index` on, each (H, W) uint16, by seeking -- for the preview
    (fewer at the clip's end)."""
    from core.depthedit import depth_io as D
    t = max(0, int(index)) / max(info.fps_float, 1e-3)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-ss", f"{t:.4f}", "-i", info.path,
           "-frames:v", str(max(1, int(count))), "-an", "-sn", "-dn", *D._range_filter(info), "-f", "rawvideo",
           "-pix_fmt", "yuv420p10le", "pipe:1"]
    out = subprocess.run(cmd, capture_output=True, creationflags=_NO_WINDOW).stdout
    y_px = info.width * info.height
    frame = y_px * 3 // 2 * 2                      # 4:2:0, 2 bytes a sample
    got = []
    for k in range(len(out) // frame):
        got.append(np.frombuffer(out, dtype="<u2", count=y_px, offset=k * frame).reshape(info.height, info.width).copy())
    if not got and len(out) >= y_px * 2:           # a short last frame: its luma is all the preview needs
        got.append(np.frombuffer(out, dtype="<u2", count=y_px).reshape(info.height, info.width).copy())
    return got


def read_frame(info, index: int) -> Optional[np.ndarray]:
    """One frame's 10-bit codes, (H, W) uint16, by seeking -- for the preview."""
    got = read_frames(info, index, 1)
    return got[0] if got else None


# ---- on the card ----------------------------------------------------------------------------------

def _device():
    import torch
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def to_unit_tensor(codes: np.ndarray, info, size: Tuple[int, int], device):
    """Codes -> (1, 1, H, W) float32 0..1 at `size` (width, height), on `device`."""
    import torch
    import torch.nn.functional as F
    lo, hi = info.code_range
    t = torch.from_numpy(codes.astype(np.float32)).to(device)
    t = ((t - lo) / float(hi - lo))[None, None]
    if (t.shape[-1], t.shape[-2]) != tuple(size):
        t = F.interpolate(t, size=(size[1], size[0]), mode="bilinear", align_corners=False)
    return t


def _blur(t, radius: float):
    """A Gaussian blur of (1, 1, H, W) `t`, sigma `radius` pixels, separable, edges mirrored."""
    import torch
    import torch.nn.functional as F
    sigma = max(0.1, float(radius))
    half = max(1, min(int(math.ceil(3 * sigma)), min(t.shape[-1], t.shape[-2]) - 1))
    x = torch.arange(-half, half + 1, device=t.device, dtype=t.dtype)
    k = torch.exp(-(x * x) / (2 * sigma * sigma))
    k = k / k.sum()
    t = F.conv2d(F.pad(t, (half, half, 0, 0), mode="reflect"), k.view(1, 1, 1, -1))
    return F.conv2d(F.pad(t, (0, 0, half, half), mode="reflect"), k.view(1, 1, -1, 1))


def _box(t, r: int):
    """The mean over a (2r+1)^2 window of (1, 1, H, W) `t`, edges mirrored."""
    import torch.nn.functional as F
    r = max(1, min(int(r), t.shape[-1] - 1, t.shape[-2] - 1))
    return F.avg_pool2d(F.pad(t, (r, r, r, r), mode="reflect"), 2 * r + 1, stride=1)


def _guided(t, r: int, eps: float):
    """`t` smoothed by a guided filter guided by itself: flat areas averaged over radius `r`, steps bigger
    than about sqrt(`eps`) kept sharp (He et al.)."""
    mean = _box(t, r)
    var = (_box(t * t, r) - mean * mean).clamp_(min=0.0)
    a = var / (var + eps)
    b = mean - a * mean
    return _box(a, r) * t + _box(b, r)


def edge_keep_eps(keep: float) -> float:
    """Edge Keep 0..1 -> the guided filter's eps: 0.1 (about the plain blur) to 1e-4 (a 1% step kept)."""
    return 10.0 ** (-1.0 - 3.0 * min(max(float(keep), 0.0), 1.0))


def _smooth(t, radius: float, keep: float):
    """Detail Size's blur: Gaussian, or with Edge Keep above 0 the edge-preserving one."""
    if keep <= 0:
        return _blur(t, radius)
    return _guided(t, max(1, int(round(radius))), edge_keep_eps(keep))


def shape_detail(d, limit_pct: float, floor_pct: float):
    """Detail `d` with wiggles under Floor (% of the depth range) dropped (the rest moved down by it, so
    nothing jumps) and the rest capped softly at Limit (%; 0 = no cap)."""
    import torch
    floor = max(0.0, float(floor_pct)) / 100.0
    if floor > 0:
        d = torch.sign(d) * (d.abs() - floor).clamp_(min=0.0)
    limit = max(0.0, float(limit_pct)) / 100.0
    if limit > 0:
        d = limit * torch.tanh(d / limit)
    return d


def depth_mix(base_t, opts: Dict):
    """By Depth: each pixel's Mix from the base map's depth (smoothed, so an edge doesn't flicker between the
    two): Far Mix below Split, Near Mix above it (near is bright), crossing over Softness."""
    near, far = (min(max(float(opts.get(k, 0.5)), 0.0), 1.0) for k in ("mix_near", "mix_far"))
    split = float(opts.get("mix_split", 0.5))
    soft = max(1e-3, float(opts.get("mix_soft", 0.2)))
    d = _blur(base_t, 3.0)
    x = ((d - split) / soft + 0.5).clamp(0.0, 1.0)
    w = x * x * (3.0 - 2.0 * x)                                  # smoothstep
    return far + (near - far) * w


def edge_band(h: int, w: int, px: float, sides: str, device, dtype):
    """(1, 1, h, w) weight of the Edge Band: 1 at the edges named, fading to 0 at `px` in (smoothstep)."""
    import torch
    px = float(px)
    if px <= 0:
        return None

    def ramp(n):
        x = (1.0 - (torch.arange(n, device=device, dtype=dtype) + 0.5) / px).clamp(0.0, 1.0)
        return x * x * (3.0 - 2.0 * x)
    cols, rows = ramp(w), ramp(h)
    if sides == "All edges":
        cols, rows = torch.maximum(cols, cols.flip(0)), torch.maximum(rows, rows.flip(0))
    out = cols.view(1, 1, 1, w).expand(1, 1, h, w)
    if sides != "Left":
        out = torch.maximum(out, rows.view(1, 1, h, 1).expand(1, 1, h, w))
    return out


# ---- lining them up -------------------------------------------------------------------------------

#: One frame's sums, x = VDA, y = DepthCrafter: count, sum x, sum y, sum xx, sum yy, sum xy.
STATS = 6


def frame_stats(dc_t, vda_t):
    """(STATS,) float64 sums of a frame pair at the measuring size."""
    import torch.nn.functional as F
    h = max(2, round(MEASURE_W * dc_t.shape[-2] / dc_t.shape[-1]))
    y = F.interpolate(dc_t, size=(h, MEASURE_W), mode="area").double().flatten()
    x = F.interpolate(vda_t, size=(h, MEASURE_W), mode="area").double().flatten()
    return np.array([x.numel(), float(x.sum()), float(y.sum()), float((x * x).sum()), float((y * y).sum()),
                     float((x * y).sum())], dtype=np.float64)


def measure(dc_info, vda_info, frames: int, stop: Optional[threading.Event] = None,
            tick: Optional[Callable[[int], None]] = None) -> np.ndarray:
    """(frames, STATS) per-frame sums of the clip's two maps (fewer rows when it is stopped or a map ends
    early)."""
    from core.depthedit import depth_io as D
    device = _device()
    rows = []
    size = (dc_info.width, dc_info.height)
    for i, (a, b) in enumerate(zip(D.iter_codes(dc_info, stop), D.iter_codes(vda_info, stop))):
        if i >= frames or (stop is not None and stop.is_set()):
            break
        rows.append(frame_stats(to_unit_tensor(a, dc_info, size, device), to_unit_tensor(b, vda_info, size, device)))
        if tick is not None:
            tick(i + 1)
    return np.array(rows, dtype=np.float64).reshape(-1, STATS)


def _fit(s, base: str) -> Tuple[float, float]:
    """(a, b): base ~ a * other + b from summed STATS `s`. A scale of 0 or less is never an answer (it turns
    near into far): the fit then matches the two maps' mean and spread instead."""
    n, sx, sy, sxx, syy, sxy = (float(v) for v in s)
    if base != "DepthCrafter":              # fit DepthCrafter (y) onto VDA (x): swap the roles
        sx, sy, sxx, syy = sy, sx, syy, sxx
    if n <= 1:
        return 1.0, 0.0
    var_x = sxx - sx * sx / n
    cov = sxy - sx * sy / n
    if var_x > 1e-12 and cov > 0:
        a = cov / var_x
    else:
        var_y = max(0.0, syy - sy * sy / n)
        a = math.sqrt(var_y / var_x) if var_x > 1e-12 else 1.0
    return a, (sy - a * sx) / n


def fits(stats: np.ndarray, base: str, align: str) -> np.ndarray:
    """(frames, 2): each frame's (a, b) fitting the other map onto the base one."""
    n = len(stats)
    if align == "Off" or n == 0:
        return np.tile([1.0, 0.0], (n, 1))
    if align == "Per clip":
        return np.tile(_fit(stats.sum(axis=0), base), (n, 1))
    csum = np.vstack([np.zeros((1, STATS)), np.cumsum(stats, axis=0)])
    out = np.empty((n, 2))
    for i in range(n):
        lo, hi = max(0, i - FRAME_WINDOW), min(n, i + FRAME_WINDOW + 1)
        out[i] = _fit(csum[hi] - csum[lo], base)
    return out


# ---- blending ------------------------------------------------------------------------------------

#: Detail Smoothing: a neighbour's detail counts less the more it differs from this frame's -- shimmer
#: (a fraction of a percent) is averaged, a moving edge (several percent) isn't
SMOOTH_SIGMA = 0.012


def blend_parts(dc_t, vda_t, fit: Tuple[float, float], opts: Dict) -> Dict:
    """A frame's blend before it is finished: {"out"} (Mix), or {"layout", "detail"} (Layout + Detail), and
    {"other"}: the other model's map in the base map's scale (the Edge Band's)."""
    a, b = float(fit[0]), float(fit[1])
    if opts["base"] != "DepthCrafter":
        base_t, other_t = vda_t, dc_t * a + b
        dc_t, vda_t = other_t, base_t
    else:
        base_t, other_t = dc_t, vda_t * a + b
        dc_t, vda_t = base_t, other_t
    if opts["mode"] == "Layout + Detail":
        r = float(opts["detail_size"])
        keep = float(opts.get("edge_keep", 0.0))
        detail = float(opts["detail_amount"]) * (other_t - _smooth(other_t, r, keep))
        detail = shape_detail(detail, opts.get("detail_limit", 0.0), opts.get("detail_floor", 0.0))
        return {"layout": _smooth(base_t, r, keep), "detail": detail, "other": vda_t}
    if opts.get("mix_by_depth"):
        m = depth_mix(base_t, opts)
    else:
        m = min(max(float(opts["mix"]), 0.0), 1.0)
    return {"out": (1.0 - m) * dc_t + m * vda_t, "other": vda_t}


def smooth_detail(details: List, centre: int, radius: Optional[int] = None, sigma: float = SMOOTH_SIGMA):
    """Frame `centre`'s detail averaged with its neighbours `details` (triangular weights by distance, over
    `radius` frames each side -- the window is cut short at a clip's ends), each neighbour's pixel weighed
    down by how far it differs from the centre's (so motion isn't averaged)."""
    import torch
    d0 = details[centre]
    num, den = torch.zeros_like(d0), torch.zeros_like(d0)
    reach = (max(centre, len(details) - 1 - centre) if radius is None else int(radius)) + 1
    for k, d in enumerate(details):
        w = float(reach - abs(k - centre))
        if w <= 0:
            continue
        diff = d - d0
        wt = w * torch.exp(-(diff * diff) / (2.0 * sigma * sigma))
        num += wt * d
        den += wt
    return num / den


def levels(t, opts: Dict):
    """Black / White point and Gamma: (t - black) / (white - black), clamped, to the power 1 / gamma (above 1
    the middle depths come nearer)."""
    black = float(opts.get("level_black", 0.0))
    white = float(opts.get("level_white", 1.0))
    gamma = float(opts.get("level_gamma", 1.0))
    if black <= 0.0 and white >= 1.0 and abs(gamma - 1.0) < 1e-6:
        return t
    t = ((t - black) / max(1e-3, white - black)).clamp(0.0, 1.0)
    return t.pow(1.0 / max(0.05, gamma)) if abs(gamma - 1.0) >= 1e-6 else t


def finish(parts: Dict, opts: Dict, detail=None):
    """The finished frame from blend_parts(): the detail (`detail`, smoothed, in place of its own), the Edge
    Band, Levels, clamped 0..1."""
    if "out" in parts:
        out = parts["out"]
    else:
        out = parts["layout"] + (parts["detail"] if detail is None else detail)
    band = edge_band(out.shape[-2], out.shape[-1], opts.get("edge_band", 0.0), opts.get("edge_band_sides", BAND_SIDES[0]),
                     out.device, out.dtype)
    if band is not None:                     # the other map (fitted) at the edges: DepthCrafter's specks
        out = out * (1.0 - band) + parts["other"] * band
    return levels(out.clamp(0.0, 1.0), opts).clamp_(0.0, 1.0)


def smoothing_radius(opts: Dict) -> int:
    """Detail Smoothing's frames each side (Layout + Detail only; 0 = off)."""
    if opts.get("mode") != "Layout + Detail":
        return 0
    return max(0, int(round(float(opts.get("detail_smooth", 0) or 0))))


def blend(dc_t, vda_t, fit: Tuple[float, float], opts: Dict):
    """The blended frame, (1, 1, H, W) 0..1, from the two at the output size and the frame's fit (without
    Detail Smoothing, which needs the frames around it: blend_clip and preview)."""
    return finish(blend_parts(dc_t, vda_t, fit, opts), opts)


def to_codes(t) -> np.ndarray:
    """0..1 -> tv-range 10-bit codes (64..940), (H, W) uint16."""
    return (t[0, 0] * (940 - 64) + 64).round_().clamp_(0, 1023).to("cpu").numpy().astype(np.uint16)


# ---- a clip --------------------------------------------------------------------------------------

def blend_clip(pair: Dict[str, str], opts: Dict, stop: Optional[threading.Event] = None,
               tick: Optional[Callable[[str, int, int], None]] = None,
               stats: Optional[np.ndarray] = None) -> Dict:
    """Blend one clip's maps into the map Splatting reads (pair["out"]): measure (unless `stats` is given),
    blend and encode, put it in place. {"frames", "set_aside", "size", "note"}; raises on failure.
    `tick(phase, done, total)`: progress ("measure", "blend")."""
    from core.common.partial_output import discard, part_path
    from core.depthedit import depth_io as D

    dci, vdi = D.probe(pair["dc"]), D.probe(pair["other"])
    frames = min(dci.num_frames, vdi.num_frames)
    note = ""
    if dci.num_frames != vdi.num_frames:
        note = (f"the maps have {dci.num_frames} and {vdi.num_frames} frames; the blend has {frames}")
        logger.warning("%s: %s.", pair["clip"], note)
    if stats is None or len(stats) < frames:
        stats = measure(dci, vdi, frames, stop, (lambda d: tick("measure", d, frames)) if tick else None)
        if stop is not None and stop.is_set():
            raise InterruptedError("stopped")
    frames = min(frames, len(stats))
    per_frame = fits(stats[:frames], opts["base"], opts["align"])
    w, h = output_size(dci, vdi, opts["size"])
    w, h = w - w % 2, h - h % 2
    base_info = dci if opts["base"] == "DepthCrafter" else vdi
    part = part_path(pair["out"])
    writer = D.DepthWriter(part, D.ClipInfo(path=pair["out"], width=w, height=h, num_frames=frames,
                                           fps=base_info.fps, pix_fmt="yuv420p10le", full_range=False),
                           metadata={"comment": OUT.BLEND_TAG})
    device = _device()
    radius = smoothing_radius(opts)
    try:
        import torch
        with torch.inference_mode():
            # Detail Smoothing: frame j is written once the frames up to j + radius are in (a window of parts)
            window: List[Dict] = []
            first = 0                                   # the frame window[0] is
            written = 0

            def flush(upto: int):
                nonlocal written
                while written < upto:
                    j = written - first
                    lo = max(0, j - radius)
                    hi = min(len(window), j + radius + 1)
                    det = (smooth_detail([p["detail"] for p in window[lo:hi]], j - lo, radius)
                           if radius and "detail" in window[j] else None)
                    writer.write(to_codes(finish(window[j], opts, det)))
                    written += 1
                    if tick:
                        tick("blend", written, frames)

            for i, (a, b) in enumerate(zip(D.iter_codes(dci, stop), D.iter_codes(vdi, stop))):
                if i >= frames:
                    break
                if stop is not None and stop.is_set():
                    raise InterruptedError("stopped")
                window.append(blend_parts(to_unit_tensor(a, dci, (w, h), device),
                                          to_unit_tensor(b, vdi, (w, h), device), per_frame[i], opts))
                flush(max(0, i + 1 - radius))
                while written - first > radius:          # the frames no window needs any more
                    window.pop(0)
                    first += 1
            flush(first + len(window))
            if written < frames:
                frames = written
        writer.close()
        writer = None
        moved = OUT.publish(part, pair["out"], new_kind="blend")
        part = None
    finally:
        if writer is not None:
            writer.abort()
        if part:
            discard(part)
    return {"frames": frames, "set_aside": moved or "", "size": (w, h), "note": note}


def preview(pair: Dict[str, str], opts: Dict, index: int, stats: np.ndarray,
            height: int = 240) -> Optional[np.ndarray]:
    """DepthCrafter | VDA | blend at frame `index`, side by side, (height, 3 * width) uint8 grey, the first
    two as the blend sees them (fitted); None when a frame can't be read."""
    import torch
    import torch.nn.functional as F
    from core.depthedit import depth_io as D

    dci, vdi = D.probe(pair["dc"], count=False), D.probe(pair["other"], count=False)
    index = max(0, min(int(index), len(stats) - 1)) if len(stats) else 0
    radius = smoothing_radius(opts)
    lo = max(0, index - radius)
    n = index - lo + radius + 1
    if len(stats):
        n = min(n, len(stats) - lo)
    dcs, vds = read_frames(dci, lo, n), read_frames(vdi, lo, n)
    if len(dcs) <= index - lo or len(vds) <= index - lo:
        return None
    w, h = output_size(dci, vdi, opts["size"])
    device = _device()
    all_fits = fits(stats, opts["base"], opts["align"]) if len(stats) else None

    def fit_at(i):
        return all_fits[min(i, len(all_fits) - 1)] if all_fits is not None else (1.0, 0.0)
    fit = fit_at(index)
    with torch.inference_mode():
        dc_t = to_unit_tensor(dcs[index - lo], dci, (w, h), device)
        vda_t = to_unit_tensor(vds[index - lo], vdi, (w, h), device)
        if radius:
            parts = [blend_parts(to_unit_tensor(x, dci, (w, h), device), to_unit_tensor(y, vdi, (w, h), device),
                                 fit_at(lo + k), opts) for k, (x, y) in enumerate(zip(dcs, vds))]
            out = finish(parts[index - lo], opts, smooth_detail([p["detail"] for p in parts], index - lo, radius))
        else:
            out = blend(dc_t, vda_t, fit, opts)
        if opts["base"] != "DepthCrafter":
            dc_t = (dc_t * float(fit[0]) + float(fit[1])).clamp(0, 1)
        else:
            vda_t = (vda_t * float(fit[0]) + float(fit[1])).clamp(0, 1)
        pw = max(2, round(height * w / h))
        row = torch.cat([F.interpolate(t, size=(height, pw), mode="area") for t in (dc_t, vda_t, out)], dim=-1)
        return (row[0, 0].clamp(0, 1) * 255).round().to(torch.uint8).cpu().numpy()
