"""Put Back (user, 2026-10-15): an object erased from a clip before its conversion -- a cage's bars,
a fence, a pole -- laid back over the finished 3D at its own depth, both eyes.

Why: an object in front of everything (thin bars above all) splats badly -- a hole beside every edge,
the background smeared into it. Erased first, the clip converts cleanly; the object then comes back
as a layer in front.

    left eye   the original clip's object pixels (warped too in Dual Eye)
    right eye  the object warped as Splatting warped the scene: the same Splatting settings for
               the clean clip (its sidecar, the page's, its keyframes), the same depth
               normalisation and gamma, the same forward warp (core.splatting) -- so it sits
               exactly where the scene puts that depth
    depth      the ORIGINAL clip's depth map (the object is in it), brought onto the clean clip's
               depth scale: DepthCrafter normalises each clip to its own range, so the two maps
               differ by a gain and offset per frame. Fitted outside the object (MAD-robust),
               where both maps see the same scene, and evened over time.

The object's matte is a mask video (white = object) at any size -- the Depth Map Editor's SAM 3
layers export one -- or an .npy (frames, H, W).
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterator, List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class PutBackJob:
    original_clip: str                 # with the object
    original_depth: str                # its depth map (the object in it)
    mask: str                          # the object's matte: a video (white = object) or .npy
    clean_clip: str                    # the clip as converted (object erased): its name finds its sidecar
    clean_depth: str                   # the depth map the clean clip was splatted with
    merged_left: str                   # the clean conversion, left eye (or a full SBS: merged_right "")
    merged_right: str = ""
    output_dir: str = ""
    #: the object's edge: px taken off it (choke) and px it is softened over
    choke: float = 0.0
    feather: float = 1.0
    #: the depth fit's time smoothing (frames each side); 0: each frame its own
    fit_smooth: int = 4
    #: a steady object (a cage, a sign): its depth held at the fit over its first frames
    steady_frames: int = 0


@dataclass
class Geometry:
    """Everything Splatting's warp of this clip depended on, from Splatting's own code."""
    vid: dict
    track: object
    kf_base: dict
    assume_raw: bool
    gmin: float
    gmax: float
    max_raw: float
    tv_comp: float
    dual_eye: bool
    dual_eye_bias: float
    input_bias: float
    peak_raw: float
    match_depth_res: bool


def geometry_for(clean_clip: str, clean_depth: str, settings) -> Geometry:
    """`settings`: Splatting's ProcessingSettings (the page's, as a run builds them). The clean
    clip's sidecar is looked for beside its depth map, as a run does."""
    import queue
    from core.common.sidecar_manager import SidecarConfigManager
    from core.common.video_io import get_video_stream_info
    from core.splatting import BatchProcessor
    from core.splatting.render_processor import RenderProcessor

    settings.sidecar_folder = os.path.dirname(clean_depth)
    settings.input_depth_maps = os.path.dirname(clean_depth)
    bp = BatchProcessor(queue.Queue(), threading.Event(), SidecarConfigManager())
    vid = bp._get_video_specific_settings(clean_clip, settings, False)
    if vid.get("error"):
        raise RuntimeError(f"{os.path.basename(clean_clip)}: {vid['error']}")
    from core.splatting.batch_processing import boost_kwargs_for
    boost = boost_kwargs_for(vid)
    d_info = get_video_stream_info(clean_depth)
    max_raw = RenderProcessor._get_max_expected_raw_depth(None, d_info)
    assume_raw = not vid.get("enable_global_norm")
    tv = 1.0
    if assume_raw and d_info and max_raw > 256.0 and str(d_info.get("color_range", "")).lower() == "tv":
        from core.splatting.depth_processing import DEPTH_VIS_TV10_WHITE_NORM, DEPTH_VIS_TV10_BLACK_NORM
        tv = 1.0 / (DEPTH_VIS_TV10_WHITE_NORM - DEPTH_VIS_TV10_BLACK_NORM)
    kf_base = {
        "max_disp": float(vid["max_disparity_percentage"]),
        "zero_disparity_anchor_val": float(vid["convergence_plane"]),
        "depth_gamma": float(vid["depth_gamma"]),
        "boost_thresh": float(boost.get("boost_thresh", vid.get("boost_thresh", 0.85))),
        "boost_amount": float(boost.get("boost_amount", 0.0)),
        "boost_curve": float(boost.get("boost_curve", vid.get("boost_curve", 2.0))),
        "boost_knee": float(boost.get("boost_knee", vid.get("boost_knee", 0.0))),
        "boost_prefilter_radius": 0,
        "boost_prefilter_eps": float(vid.get("boost_prefilter_eps", 1e-3)),
        "pop_amount": float(boost.get("pop_amount", vid.get("pop_amount", 0.0))),
    }
    return Geometry(vid=vid, track=vid.get("keyframe_track"), kf_base=kf_base, assume_raw=assume_raw,
                    gmin=float(vid.get("global_min", 0.0)), gmax=float(vid.get("global_max", 1.0)),
                    max_raw=float(max_raw), tv_comp=tv, dual_eye=bool(settings.dual_eye),
                    dual_eye_bias=float(settings.dual_eye_bias), input_bias=float(vid.get("input_bias", 0.0)),
                    peak_raw=float(boost.get("depth_peak_raw", vid.get("depth_max_raw", 1.0)) or 1.0),
                    match_depth_res=bool(getattr(settings, "match_depth_res", True)))


def _effective(geo: Geometry, frame: int) -> dict:
    from core.splatting.render_processor import effective_settings
    eff = effective_settings(geo.kf_base, geo.track, frame) if geo.track is not None else geo.kf_base
    return eff


def _peak(geo: Geometry, eff: dict) -> float:
    """render_video's _peak_warp: the clip's raw depth max in the warp domain (Boost / Pop-Out)."""
    from core.splatting.depth_processing import normalize_and_gamma_depth
    if not (eff["boost_amount"] + eff["pop_amount"] > 0 and 0.0 < geo.peak_raw < 0.999):
        return 1.0
    pk = normalize_and_gamma_depth(
        batch_depth_numpy_raw=np.full((1, 1, 1, 1), geo.peak_raw * geo.max_raw, dtype=np.float32),
        assume_raw_input=geo.assume_raw, global_depth_max=geo.gmax, global_depth_min=geo.gmin,
        max_expected_raw_value=geo.max_raw, zero_disparity_anchor_val=eff["zero_disparity_anchor_val"],
        depth_gamma=eff["depth_gamma"], debug_task_name="PutBackPeak")
    return float(np.asarray(pk).reshape(-1)[0])


# ---- reading ------------------------------------------------------------------------------

def _depth_reader(path: str, w: int, h: int, match: bool):
    """The depth map as Splatting's full-resolution pass reads it (raw codes, at the clip's size)."""
    from core.splatting.depth_processing import load_pre_rendered_depth
    reader, total, _dh, _dw, info = load_pre_rendered_depth(path, process_length=-1, target_height=h,
                                                            target_width=w, match_resolution_to_target=match)
    return reader, int(total)


def _depth_frames(reader, total: int, w: int, h: int, batch: int = 8) -> Iterator[np.ndarray]:
    for start in range(0, total, batch):
        idx = list(range(start, min(total, start + batch)))
        arr = reader.get_batch(idx).asnumpy().astype(np.float32)
        if arr.ndim == 4:
            arr = arr[..., 0] if arr.shape[-1] in (1, 3) else arr.mean(-1)
        for f in arr:
            if f.shape != (h, w):
                f = cv2.resize(f, (w, h), interpolation=cv2.INTER_LINEAR)
            yield f


def _rgb48(path: str, w: int, h: int) -> Iterator[np.ndarray]:
    from core.removal import erase as E
    yield from E._reader(path, w, h, "rgb48le")


def _probe_size(path: str) -> Tuple[int, int, int]:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                          "stream=width,height,nb_read_packets", "-of", "csv=p=0", path],
                         capture_output=True, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    parts = [p for p in out.stdout.strip().split(",") if p]
    if len(parts) < 3:
        raise RuntimeError(f"Can't read {os.path.basename(path)}")
    return int(parts[0]), int(parts[1]), int(parts[2])


class MaskSource:
    """The object's matte, 0..1 at the clip's size: a video (white = object) or an .npy."""

    def __init__(self, path: str, w: int, h: int):
        self.w, self.h = w, h
        self._npy = np.load(path, mmap_mode="r") if path.lower().endswith(".npy") else None
        self._path = path

    def frames(self) -> Iterator[np.ndarray]:
        if self._npy is not None:
            top = 255.0 if self._npy.max() > 1 else 1.0
            for f in self._npy:
                yield self._fit(np.asarray(f, np.float32) / top)
        else:
            from core.removal import erase as E
            mw, mh, _n = _probe_size(self._path)
            for f in E._reader(self._path, mw, mh, "rgb24"):
                yield self._fit(f[..., 0].astype(np.float32) / 255.0)

    def _fit(self, m: np.ndarray) -> np.ndarray:
        if m.shape != (self.h, self.w):
            m = cv2.resize(m, (self.w, self.h), interpolation=cv2.INTER_LINEAR)
        return np.clip(m, 0.0, 1.0)


def shape_alpha(m: np.ndarray, choke: float, feather: float) -> np.ndarray:
    """The matte's edge: `choke` px off it, softened over `feather` px."""
    a = m.astype(np.float32)
    if choke > 0:
        k = int(np.ceil(choke)) * 2 + 1
        a = cv2.erode(a, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    if feather > 0:
        a = cv2.GaussianBlur(a, (0, 0), feather / 2.0)
    return np.clip(a, 0.0, 1.0)


# ---- the depth fit --------------------------------------------------------------------------

def fit_scale(orig: np.ndarray, clean: np.ndarray, mask: np.ndarray, stride: int = 6) -> Tuple[float, float, float]:
    """clean ~= gain * orig + offset over the scene both maps see (outside the object, grown so its
    soft edge stays out): (gain, offset, inlier share). Gain NaN: no fit."""
    from depthcrafter.depth_stabilize import _robust_affine
    keep = cv2.dilate((mask > 0.05).astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31, 31))) == 0
    sel = keep[::stride, ::stride]
    o = orig[::stride, ::stride][sel]
    c = clean[::stride, ::stride][sel]
    if o.size < 200:
        return float("nan"), 0.0, 0.0
    return _robust_affine(o, c)


def smooth_fits(fits: List[Tuple[float, float, float]], reach: int, steady: int = 0) -> List[Tuple[float, float]]:
    """Each frame's fit evened with its neighbours' (a median: one bad frame doesn't pull); a frame with
    no fit takes its neighbours'. `steady` > 0: the fit over the first frames held for all."""
    n = len(fits)
    g = np.array([f[0] if np.isfinite(f[0]) and f[2] >= 0.2 else np.nan for f in fits], np.float64)
    b = np.array([f[1] if np.isfinite(f[0]) and f[2] >= 0.2 else np.nan for f in fits], np.float64)
    if np.all(np.isnan(g)):
        return [(1.0, 0.0)] * n
    if steady > 0:
        gs, bs = np.nanmedian(g[:steady]), np.nanmedian(b[:steady])
        if np.isfinite(gs):
            return [(float(gs), float(bs))] * n
    out = []
    for i in range(n):
        lo, hi = max(0, i - reach), min(n, i + reach + 1)
        gi, bi = np.nanmedian(g[lo:hi]) if np.any(np.isfinite(g[lo:hi])) else np.nan, \
            np.nanmedian(b[lo:hi]) if np.any(np.isfinite(b[lo:hi])) else np.nan
        out.append((gi, bi))
    # gaps (no fit anywhere near): the nearest frame that has one
    good = [i for i, (gi, _b) in enumerate(out) if np.isfinite(gi)]
    for i in range(n):
        if not np.isfinite(out[i][0]):
            j = min(good, key=lambda k: abs(k - i))
            out[i] = out[j]
    return [(float(gi), float(bi)) for gi, bi in out]


# ---- the warp -------------------------------------------------------------------------------

# ---- Original Eyes: the object cut from the original clip's own finished eyes ------------------------------
# (user, 2026-10-16: "lets just try isolating him from the left and right eyes that already exist"). The
# original clip's merge -- made before the erase, the object in it -- has him in both eyes exactly as that
# conversion rendered him: his pixels come from there, nothing re-warped. Left: the left eye inside his mask.
# Right: the right eye inside his mask moved to where he is in it -- the move measured between the two eyes
# themselves (a stereo match inside the mask, on the GPU), so it needs neither that conversion's settings nor
# its depth map, and can't drift from a sidecar changed since.

#: the widest disparity looked for, a share of the eye's width (+ and -)
EYES_SEARCH = 0.08
#: the match's window (px, at half size) and how far his disparity is spread from his inside to his edge
EYES_WINDOW = 9
EYES_SPREAD = 6.0


def find_eyes(path: str, stem: str) -> Optional[Tuple[str, str]]:
    """The original merge of clip `stem` in `path` (a folder, or a file in it): Merging's eye pair
    ({stem}_..merged.._L / _R), else one side-by-side file (returned for both eyes: its halves); None."""
    if not path:
        return None
    folder = path if os.path.isdir(path) else os.path.dirname(path)
    if not os.path.isdir(folder):
        return None
    names = sorted(f for f in os.listdir(folder) if f.startswith(stem + "_") and "merged" in f.lower()
                   and os.path.splitext(f)[1].lower() in (".mov", ".mp4", ".mkv", ".avi"))
    for f in names:
        b, e = os.path.splitext(f)
        if b.endswith("_L") and b[:-2] + "_R" + e in names:
            return os.path.join(folder, f), os.path.join(folder, b[:-2] + "_R" + e)
    for f in names:
        if not os.path.splitext(f)[0].endswith(("_L", "_R")):
            p = os.path.join(folder, f)
            return p, p
    return None


def _shift_x(t, d: int):
    """t moved right by d px (out[x] = t[x - d]), its edge column repeated."""
    import torch
    if d == 0:
        return t
    if d > 0:
        return torch.cat([t[..., :1].expand(*t.shape[:-1], d), t[..., :-d]], -1)
    return torch.cat([t[..., -d:], t[..., -1:].expand(*t.shape[:-1], -d)], -1)


def eyes_disparity(left, right, inside, max_px: float):
    """His disparity between the eyes, px (N, 1, H, W: the right eye has him at x - D), on the GPU: a window
    match at half size over +-max_px, kept where `inside` (surely him), then spread over the rest by a
    normalised blur (his edge takes his inside's disparity -- the edge is where a match is least sure)."""
    import torch
    import torch.nn.functional as F
    from core.removal.erase import _gauss
    n, _c, h, w = left.shape
    lh, rh = F.avg_pool2d(left, 2), F.avg_pool2d(right, 2)
    ih = (F.avg_pool2d(inside.float(), 2) > 0.99).float()
    m = max(1, int(max_px // 2))
    best = torch.full((n, 1) + lh.shape[-2:], float("inf"), device=left.device)
    arg = torch.zeros_like(best)
    for d in range(-m, m + 1):
        c = F.avg_pool2d((lh - _shift_x(rh, d)).abs().mean(1, keepdim=True), EYES_WINDOW, 1, EYES_WINDOW // 2)
        better = c < best
        best = torch.where(better, c, best)
        arg = torch.where(better, torch.full_like(arg, float(d)), arg)
    num = _gauss(arg * ih, EYES_SPREAD, 1)
    den = _gauss(ih, EYES_SPREAD, 1)
    filled = torch.where(den > 1e-3, num / den.clamp_min(1e-3), torch.zeros_like(num))
    return F.interpolate(filled, size=(h, w), mode="bilinear", align_corners=False) * 2.0


def eyes_right_mask(a, disp):
    """The mask `a` (N, 1, H, W, 0..1) moved to where its object is in the right eye: each pixel to x - D
    (the largest value where several land), small gaps closed."""
    import torch
    import torch.nn.functional as F
    n, c, h, w = a.shape
    xs = torch.arange(w, device=a.device, dtype=torch.float32).view(1, 1, 1, w)
    tx = (xs - disp).round().long().clamp(0, w - 1)
    out = torch.zeros_like(a).scatter_reduce(3, tx, a, reduce="amax", include_self=True)
    out = F.max_pool2d(out, 5, 1, 2)
    return -F.max_pool2d(-out, 5, 1, 2)


# ---- Glass Plane: what's on a window laid on the glass itself ----------------------------------------------
# (user, 2026-10-17: the dust on chap4_00116923's windshield sat at the road's depth -- DepthCrafter sees through
# glass -- "it needs to be nearer on the right and further on the left following the car"; "match it to the
# depth map range"; "as long as there is a convergence setting"). A window's depth isn't in the depth map; the
# car frame around it is. Each window (a piece of the put-back's mask) gets a plane fitted to the depth map just
# around it -- its own (one plane over a windshield and the side windows came out flat: -3..+4 px against the
# car's -4..+11) -- and the layer is warped at that plane's depth as Splatting warps the scene, Convergence and
# Disparity on top. Signed (Add's layer is original - erased).

#: a window: a piece of the mask at least this share of the frame
GLASS_MIN_SHARE = 0.002
#: the car frame read around a window: from GLASS_GAP to GLASS_GAP + GLASS_RING px out, at the map's size
GLASS_GAP, GLASS_RING = 4, 12
#: the plane is fitted to the depth map's median over this many frames either side (steadier than one)
GLASS_STEADY = 2


def glass_planes(codes, mask, min_share: float = GLASS_MIN_SHARE):
    """The depth on each window of `mask` (h, w bool, the map's size) as a plane fitted to `codes` (h, w, the map's
    raw codes) around it: (h, w) float32 codes, the map's own value off the windows. On the GPU where it can be."""
    import torch
    import torch.nn.functional as F
    h, w = mask.shape
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    n, lab, stats, _c = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)    # a small map: the CPU's
    d = torch.from_numpy(np.ascontiguousarray(codes, np.float32)).to(dev)
    out = d.clone()
    yy, xx = torch.meshgrid(torch.arange(h, device=dev, dtype=torch.float32),
                            torch.arange(w, device=dev, dtype=torch.float32), indexing="ij")
    every = torch.from_numpy(mask).to(dev)[None, None].float()
    for k in range(1, n):
        if stats[k, cv2.CC_STAT_AREA] < min_share * h * w:
            continue
        win = torch.from_numpy(lab == k).to(dev)[None, None].float()
        near = F.max_pool2d(win, 2 * GLASS_GAP + 1, 1, GLASS_GAP)
        far = F.max_pool2d(win, 2 * (GLASS_GAP + GLASS_RING) + 1, 1, GLASS_GAP + GLASS_RING)
        ring = ((far > 0) & (near == 0) & (every == 0))[0, 0]
        if int(ring.sum()) < 30:
            continue
        x, y, v = xx[ring], yy[ring], d[ring]
        A = torch.stack([x, y, torch.ones_like(x)], 1)
        c = torch.linalg.lstsq(A, v[:, None]).solution
        res = (A @ c - v[:, None]).abs()[:, 0]
        ok = res <= torch.quantile(res, 0.75)                     # the worst quarter out: a head, a mirror stand
        if int(ok.sum()) >= 3:
            c = torch.linalg.lstsq(A[ok], v[ok][:, None]).solution
        inside = (F.max_pool2d(win, 2 * GLASS_GAP + 1, 1, GLASS_GAP) > 0)[0, 0]
        out[inside] = (xx[inside] * c[0, 0] + yy[inside] * c[1, 0] + c[2, 0])
    return out.cpu().numpy()


class GlassLayer:
    """For Merging (core.removal.putback, Add with depth "Glass Plane"): the put-back's layer warped at its
    windows' planes. The depth map's codes are kept (the preview seeks); each frame's planes from the median of
    its neighbours' maps."""

    RAM_BYTES = 1_500_000_000

    def __init__(self, clean_depth: str, mask_path: str, settings, clean_name: str):
        from core.depthedit import depth_io
        self.geo = geometry_for(clean_name, clean_depth, settings)
        info = depth_io.probe(clean_depth)
        self.dw, self.dh, self.n = info.width, info.height, info.num_frames
        size = self.n * self.dh * self.dw * 2
        self._tmp = None
        if size > self.RAM_BYTES:
            import tempfile
            self._tmp = tempfile.NamedTemporaryFile(prefix="putback_glass_", suffix=".u16", delete=False)
            self._tmp.close()
            self.codes = np.memmap(self._tmp.name, dtype=np.uint16, mode="w+", shape=(self.n, self.dh, self.dw))
        else:
            self.codes = np.empty((self.n, self.dh, self.dw), np.uint16)
        k = 0
        for k, c in enumerate(depth_io.iter_codes(info)):
            if k >= self.n:
                break
            self.codes[k] = c
        self.n = min(self.n, k + 1)
        # the windows per frame at the map's size, bit-packed (a long clip's would be hundreds of MB)
        self._masks = [np.packbits(m > 0.5) for m in MaskSource(mask_path, self.dw, self.dh).frames()]
        self._planes: Dict[int, np.ndarray] = {}
        self._warp = {}

    def _mask(self, i: int) -> np.ndarray:
        if not self._masks:
            return np.zeros((self.dh, self.dw), bool)
        i = min(max(int(i), 0), len(self._masks) - 1)
        return np.unpackbits(self._masks[i])[:self.dh * self.dw].reshape(self.dh, self.dw).astype(bool)

    def plane(self, i: int) -> np.ndarray:
        """Frame i's depth with its windows on their planes (raw codes, the map's size)."""
        i = min(max(int(i), 0), self.n - 1)
        hit = self._planes.get(i)
        if hit is None:
            lo, hi = max(0, i - GLASS_STEADY), min(self.n, i + GLASS_STEADY + 1)
            med = np.median(self.codes[lo:hi].astype(np.float32), 0)
            hit = glass_planes(med, self._mask(i))
            if len(self._planes) > 64:
                self._planes.pop(next(iter(self._planes)))
            self._planes[i] = hit
        return hit

    def warp(self, i: int, layer, alpha, conv: float = 0.0, disp: float = 1.0, flipped: bool = False):
        """The layer (3, H, W signed, on the GPU) inside `alpha` (1, H, W) warped at frame i's glass: {"right":
        (3, H, W) to add, "left": the same for Dual Eye or None}, signed, premultiplied."""
        import torch
        from core.splatting.depth_processing import normalize_and_gamma_depth
        from core.splatting.forward_warp import ForwardWarpStereo, execute_forward_warp
        _c, h, w = layer.shape
        proj = self._warp.get("proj")
        if proj is None:
            proj = self._warp["proj"] = ForwardWarpStereo(occlu_map=True).to(layer.device)
        plane = self.plane(i)
        if (self.dh, self.dw) != (h, w):
            plane = cv2.resize(plane, (w, h), interpolation=cv2.INTER_LINEAR)
        if flipped:
            plane = plane[:, ::-1].copy()
        geo = self.geo
        eff = _effective(geo, i)
        dn = normalize_and_gamma_depth(
            batch_depth_numpy_raw=np.clip(plane, 0, geo.max_raw)[None].astype(np.float32), assume_raw_input=geo.assume_raw,
            global_depth_max=geo.gmax, global_depth_min=geo.gmin, max_expected_raw_value=geo.max_raw,
            zero_disparity_anchor_val=eff["zero_disparity_anchor_val"], depth_gamma=eff["depth_gamma"],
            debug_task_name="PutBack")
        dep = torch.from_numpy(np.clip(np.asarray(dn, np.float32).reshape(h, w), 0, 1)).to(layer.device)[None, None]
        src = torch.cat([layer * alpha, alpha], 0)[None].float()
        out = execute_forward_warp(
            stereo_projector=proj, source_tensor=src, depth_tensor=dep, target_width=w,
            max_disp=eff["max_disp"] * max(0.0, float(disp)),
            zero_disparity_anchor_val=eff["zero_disparity_anchor_val"] + float(conv),
            input_bias=geo.input_bias, tv_disp_comp=geo.tv_comp, debug_task_name="Render", mask_mode="SC",
            splat_backend="SC", dual_eye=geo.dual_eye, dual_eye_bias=geo.dual_eye_bias)
        if geo.dual_eye:
            return {"left": out[0][0, :3], "right": out[2][0, :3]}
        return {"left": None, "right": out[0][0, :3]}

    def close(self) -> None:
        if self._tmp is not None:
            try:
                del self.codes
                os.remove(self._tmp.name)
            except OSError:
                pass
            self._tmp = None


#: decomb's reach: a gap is a pixel the warp left empty with object on both sides within this many px
DECOMB_REACH = 6
#: ...and a strip is combed where gaps are at least this share of a 15 x 15 neighbourhood (dropped over the next
#: 0.05); a speck left there -- covered, but its 15 x 15 neighbourhood under this share covered -- goes too
DECOMB_SHARE = 0.05
DECOMB_SPECK = 0.35


def decomb(t):
    """The warped object (4, H, W: premultiplied rgb + alpha, on the GPU) with its combed strips dropped and its
    odd gaps filled (2026-10-16). The original depth map's object is softer and wider than the object:
    around it its mask holds a strip of background whose depth slopes steeply, and the warp pulls that strip's
    pixels apart into stripes with the clean eye between them. Splatting's inpainting fills such gaps; here
    nothing would. Where gaps are many (a strip pulled apart) the layer is dropped and the clean eye's own
    background shows; a lone gap (inside the object, a gentle stretch) is filled from its neighbours. A clean
    outline -- object, then nothing beyond it -- has no gaps and is left as it is, so the object isn't shrunk."""
    import torch
    import torch.nn.functional as F
    k = DECOMB_REACH
    cov = (t[3:4] > 0.5).float()[None]                                   # 1, 1, H, W

    def both_sides(c):
        # covered within k px on both sides along the last axis
        cs = F.pad(c, (k + 1, k)).cumsum(-1)
        xp = torch.arange(c.shape[-1], device=t.device) + k + 1
        before = cs[..., xp - 1] - cs[..., xp - k - 1]
        after = cs[..., xp + k] - cs[..., xp]
        return (before > 0).float() * (after > 0).float()

    # along rows, and down columns: a strip pulled apart combs either way (the test clip's came out in rows)
    across = torch.maximum(both_sides(cov), both_sides(cov.transpose(-1, -2)).transpose(-1, -2))
    gap = (1 - cov) * across
    share = F.avg_pool2d(gap, 15, 1, 7)
    keep = 1 - ((share - DECOMB_SHARE) / 0.05).clamp(0, 1)
    dense = F.avg_pool2d(cov * keep, 15, 1, 7)
    keep = keep * (dense >= DECOMB_SPECK).float()
    # lone gaps: the neighbours' average (premultiplied, so colour and alpha together)
    near = F.avg_pool2d(t[None], 3, 1, 1)
    lone = gap * (share < DECOMB_SHARE).float()
    out = t[None] * (1 - lone) + near * lone
    return (out * keep)[0]


class ObjectWarp:
    """The object layer warped as Splatting warped the clip's scene, on the GPU."""

    def __init__(self, geo: Geometry, width: int):
        import torch
        from core.splatting.forward_warp import ForwardWarpStereo
        self.geo, self.width = geo, width
        self.torch = torch
        self.projector = ForwardWarpStereo(occlu_map=True).cuda()

    def __call__(self, frame: int, rgb: np.ndarray, alpha: np.ndarray, depth_raw: np.ndarray, conv: float = 0.0,
                 disp: float = 1.0):
        """rgb (H, W, 3) 0..1, alpha (H, W) 0..1, depth_raw (H, W) in the clean map's codes ->
        {"left": (pm rgb, alpha) or None, "right": (pm rgb, alpha)}, premultiplied, 0..1.
        `conv`: the object's own Convergence, off its measured depth (as Text Conv: + sets it back,
        - brings it out). `disp`: its Disparity, a share of the scene's (1 = as Splatting warped it)."""
        torch = self.torch
        from core.splatting.depth_processing import normalize_and_gamma_depth
        from core.splatting.forward_warp import execute_forward_warp
        geo = self.geo
        eff = _effective(geo, frame)
        d = normalize_and_gamma_depth(
            batch_depth_numpy_raw=depth_raw[None].astype(np.float32), assume_raw_input=geo.assume_raw,
            global_depth_max=geo.gmax, global_depth_min=geo.gmin, max_expected_raw_value=geo.max_raw,
            zero_disparity_anchor_val=eff["zero_disparity_anchor_val"], depth_gamma=eff["depth_gamma"],
            debug_task_name="PutBack")
        # Convergence moves the object's convergence plane, not its depth (user, 2026-10-16: a wider range):
        # disparity is linear in (depth - plane), so the whole object slides in or out with its shape kept --
        # shifting the depth instead clipped it flat against 0 or 1 past +-0.5
        d = np.clip(np.asarray(d, np.float32).reshape(depth_raw.shape), 0.0, 1.0)
        src = np.concatenate([rgb * alpha[..., None], alpha[..., None]], axis=-1)
        src_t = torch.from_numpy(np.ascontiguousarray(src.transpose(2, 0, 1)))[None].float().cuda()
        dep_t = torch.from_numpy(d)[None, None].float().cuda()
        out = execute_forward_warp(
            stereo_projector=self.projector, source_tensor=src_t, depth_tensor=dep_t, target_width=self.width,
            max_disp=eff["max_disp"] * max(0.0, float(disp)),
            zero_disparity_anchor_val=eff["zero_disparity_anchor_val"] + float(conv),
            input_bias=geo.input_bias, tv_disp_comp=geo.tv_comp, debug_task_name="Render", mask_mode="SC",
            splat_backend="SC", dual_eye=geo.dual_eye, dual_eye_bias=geo.dual_eye_bias,
            boost_thresh=eff["boost_thresh"], boost_amount=eff["boost_amount"], boost_curve=eff["boost_curve"],
            boost_knee=eff["boost_knee"], pop_amount=eff["pop_amount"], depth_peak=_peak(geo, eff))

        def layer(t):
            a = decomb(t[0].clamp(0, 1)).cpu().numpy().transpose(1, 2, 0)
            return a[..., :3], a[..., 3]

        if geo.dual_eye:
            return {"left": layer(out[0]), "right": layer(out[2])}
        return {"left": None, "right": layer(out[0])}


# ---- the run ------------------------------------------------------------------------------

def run(job: PutBackJob, settings, progress: Optional[Callable[[str, int, int], None]] = None,
        stop: Optional[threading.Event] = None) -> Dict[str, str]:
    """Lay the object back over the clean conversion; returns {"L", "R", "SBS"} paths written."""
    from core.common.color_tags import with_color_tags
    from core.removal import erase as E
    from studio.merge_all import probe, _matching_args

    say = progress or (lambda *_a: None)
    sbs_in = not job.merged_right
    mw, mh, n_merged = _probe_size(job.merged_left)
    w = mw // 2 if sbs_in else mw
    h = mh
    ow, oh, n_orig = _probe_size(job.original_clip)
    if (ow, oh) != (w, h):
        raise RuntimeError(f"The original clip is {ow}x{oh}, the conversion's eyes {w}x{h}: they must match.")
    n = min(n_merged, n_orig)
    geo = geometry_for(job.clean_clip, job.clean_depth, settings)

    # 1. the depth fit, frame by frame (both maps read as Splatting reads them)
    say("Fitting the depth", 0, n)
    o_rd, o_n = _depth_reader(job.original_depth, w, h, geo.match_depth_res)
    c_rd, c_n = _depth_reader(job.clean_depth, w, h, geo.match_depth_res)
    masks = MaskSource(job.mask, w, h)
    fits = []
    for i, (do, dc, m) in enumerate(zip(_depth_frames(o_rd, min(o_n, n), w, h),
                                        _depth_frames(c_rd, min(c_n, n), w, h), masks.frames())):
        if stop is not None and stop.is_set():
            return {}
        fits.append(fit_scale(do, dc, m))
        say("Fitting the depth", i + 1, n)
    for r in (o_rd, c_rd):
        if hasattr(r, "close"):
            r.close()
    scale = smooth_fits(fits, job.fit_smooth, job.steady_frames)
    n = len(scale)
    gains = [s[0] for s in scale]
    logger.info("Put Back: depth fit gain %.3f-%.3f over %d frames", min(gains), max(gains), n)

    # 2. the layer, both eyes, over the conversion
    os.makedirs(job.output_dir or os.path.dirname(job.merged_left), exist_ok=True)
    out_dir = job.output_dir or os.path.dirname(job.merged_left)
    stem = os.path.splitext(os.path.basename(job.merged_left))[0]
    for suf in ("_L", "_R"):
        if stem.endswith(suf):
            stem = stem[: -len(suf)]
    paths = {"L": os.path.join(out_dir, f"{stem}_L.mov"), "R": os.path.join(out_dir, f"{stem}_R.mov"),
             "SBS": os.path.join(out_dir, f"{stem}.mov")}

    def writer(path, ref, ww):
        info, p = E._probe(ref), probe(ref)
        color = []
        for key, flag in (("color_space", "-colorspace"), ("color_transfer", "-color_trc"),
                          ("color_primaries", "-color_primaries"), ("color_range", "-color_range")):
            if key in info["color"]:
                color += [flag, info["color"][key]]
        return subprocess.Popen(with_color_tags(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt",
                                                 "rgb48le", "-s", f"{ww}x{h}", "-r", info["rate"], "-i", "-",
                                                 *_matching_args(p), *color, "-movflags", "write_colr", path]),
                                stdin=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    ref = job.merged_left
    wL, wR, wS = writer(paths["L"], ref, w), writer(paths["R"], ref, w), writer(paths["SBS"], ref, 2 * w)
    warp = ObjectWarp(geo, w)
    o_rd, o_n = _depth_reader(job.original_depth, w, h, geo.match_depth_res)
    merged = _rgb48(job.merged_left, mw, mh)
    merged_r = None if sbs_in else _rgb48(job.merged_right, w, h)
    try:
        for i, (orig, do, m) in enumerate(zip(_rgb48(job.original_clip, w, h), _depth_frames(o_rd, n, w, h),
                                              masks.frames())):
            if i >= n or (stop is not None and stop.is_set()):
                break
            frame = next(merged)
            if sbs_in:
                left, right = frame[:, :w], frame[:, w:]
            else:
                left, right = frame, next(merged_r)
            a = shape_alpha(m, job.choke, job.feather)
            gain, off = scale[i]
            depth = np.clip(do * gain + off, 0.0, geo.max_raw)
            src = orig.astype(np.float32) / 65535.0
            lay = warp(i, src, a, depth)
            lf = left.astype(np.float32) / 65535.0
            rf = right.astype(np.float32) / 65535.0
            if lay["left"] is None:
                lf = lf * (1 - a[..., None]) + src * a[..., None]
            else:
                pm, al = lay["left"]
                lf = lf * (1 - al[..., None]) + pm
            pm, al = lay["right"]
            rf = rf * (1 - al[..., None]) + pm
            L16 = np.clip(lf * 65535 + 0.5, 0, 65535).astype(np.uint16)
            R16 = np.clip(rf * 65535 + 0.5, 0, 65535).astype(np.uint16)
            wL.stdin.write(L16.tobytes())
            wR.stdin.write(R16.tobytes())
            wS.stdin.write(np.hstack([L16, R16]).tobytes())
            say("Laying the object back", i + 1, n)
    finally:
        for wr in (wL, wR, wS):
            try:
                wr.stdin.close()
            except OSError:
                pass
            wr.wait()
        if hasattr(o_rd, "close"):
            o_rd.close()
    bad = [k for k, wr in (("L", wL), ("R", wR), ("SBS", wS)) if wr.returncode]
    if bad:
        raise RuntimeError(f"The encoder stopped writing {', '.join(bad)}.")
    return paths


class DepthMapLayer:
    """For Merging (core.removal.putback, depth "Depth Map"): one clip's object warped with the original
    clip's depth map, batch by batch. Built once per clip: the original map's codes kept (the preview
    seeks), each frame's fit onto the clean map worked out over the whole clip and evened."""

    #: above this many bytes the original map's codes go to a file, not memory
    RAM_BYTES = 1_500_000_000

    def __init__(self, original_depth: str, clean_depth: str, object_mask: str, settings, clean_name: str,
                 steady_frames: int = 0, fit_smooth: int = 4, original_scale: bool = False):
        """original_scale: the original map's codes as they are -- the object at the depth the original
        conversion gave it -- not fitted onto the clean map's scale (Put Back's "Original Depth")."""
        from core.depthedit import depth_io
        self.geo = geometry_for(clean_name, clean_depth, settings)
        info_o = depth_io.probe(original_depth)
        info_c = depth_io.probe(clean_depth)
        n = min(info_o.num_frames, info_c.num_frames)
        self.dw, self.dh = info_o.width, info_o.height
        size = n * self.dh * self.dw * 2
        if size > self.RAM_BYTES:
            import tempfile
            self._tmp = tempfile.NamedTemporaryFile(prefix="putback_depth_", suffix=".u16", delete=False)
            self._tmp.close()
            self.codes = np.memmap(self._tmp.name, dtype=np.uint16, mode="w+", shape=(n, self.dh, self.dw))
        else:
            self._tmp = None
            self.codes = np.empty((n, self.dh, self.dw), np.uint16)
        masks = MaskSource(object_mask, self.dw, self.dh).frames()
        fits = []
        for i, (co, cc) in enumerate(zip(depth_io.iter_codes(info_o), depth_io.iter_codes(info_c))):
            if i >= n:
                break
            self.codes[i] = co
            if (cc.shape[0], cc.shape[1]) != (self.dh, self.dw):
                cc = cv2.resize(cc.astype(np.float32), (self.dw, self.dh), interpolation=cv2.INTER_LINEAR)
            m = next(masks, None)
            fits.append(fit_scale(co.astype(np.float32), cc.astype(np.float32),
                                  m if m is not None else np.zeros((self.dh, self.dw), np.float32), stride=3))
        self.n = len(fits)
        self.scale = [(1.0, 0.0)] * self.n if original_scale else smooth_fits(fits, fit_smooth, steady_frames)
        self._warp = {}

    def depth_at(self, i: int, w: int, h: int) -> np.ndarray:
        """Frame i's object depth on the clean map's scale, at w x h (raw codes, as Splatting reads them)."""
        i = min(max(int(i), 0), self.n - 1)
        gain, off = self.scale[i]
        d = self.codes[i].astype(np.float32)
        if (self.dh, self.dw) != (h, w):
            d = cv2.resize(d, (w, h), interpolation=cv2.INTER_LINEAR)
        return np.clip(d * gain + off, 0.0, self.geo.max_raw)

    def warp(self, i: int, rgb: np.ndarray, alpha: np.ndarray, conv: float = 0.0, flipped: bool = False,
             disp: float = 1.0):
        """ObjectWarp's layers for frame i (rgb, alpha at the eyes' size; flipped: in Merging's flipped
        space -- the depth flipped to match, as Splatting flips video and depth together)."""
        h, w = alpha.shape
        warp = self._warp.get(w)
        if warp is None:
            warp = self._warp[w] = ObjectWarp(self.geo, w)
        d = self.depth_at(i, w, h)
        if flipped:
            d = d[:, ::-1].copy()
        return warp(i, rgb, alpha, d, conv, disp)

    def close(self) -> None:
        if self._tmp is not None:
            del self.codes
            try:
                os.remove(self._tmp.name)
            except OSError:
                pass
            self._tmp = None
