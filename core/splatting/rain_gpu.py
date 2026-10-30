"""rain_gpu.py -- the rain separation (core.splatting.rain) on the GPU (user, 2026-10-07:
"port it to the gpu").

The same steps as the CPU code, each OpenCV call done the same way in torch:

  filter2D (the line filters)   conv2d with the very same kernels, reflect-101 border
  morphology (ellipse)          per kernel row a horizontal min/max pool, rows combined
                                with shifts; the border ignored, as OpenCV's
  GaussianBlur / Sobel          separable conv2d with OpenCV's own kernels, reflect-101
  resize (area half / linear)   avg-pool / bilinear with half-pixel centres
  remap (lining up neighbours)  grid_sample, bilinear, border replicated, rounded to
                                whole levels as OpenCV's 8-bit remap is
  median / MAD                  torch.quantile (linear: numpy's median)

What stays on the CPU: the optical flow (OpenCV's DIS has no GPU version here; ~15-20%
of the old time) and grouping the mask into streaks (connected components, a few ms).
Results match the CPU's to rounding -- a few borderline pixels can go either way; the
CPU code is unchanged and still used without CUDA (or with device="cpu").
"""
from __future__ import annotations

from typing import List, Sequence, Tuple

import cv2
import numpy as np
import torch
import torch.nn.functional as F

from . import rain as R


class Ops:
    """OpenCV's image operations, in torch on one device. Kernels are cached."""

    def __init__(self, device):
        self.dev = torch.device(device)
        self._lines = {}
        self._rows = {}
        self._gauss = {}

    # ---- the line filters ------------------------------------------------------------
    def _line(self, angle: float, length: int) -> np.ndarray:
        key = (round(float(angle) % 360, 4), int(length))
        k = self._lines.get(key)
        if k is None:
            k = np.zeros((length, length), np.float32)
            c = (length - 1) / 2
            a = np.radians(angle)
            for r in np.linspace(-c, c, length * 2):
                k[int(round(c - r * np.sin(a))), int(round(c + r * np.cos(a)))] = 1
            k = k / k.sum()
            self._lines[key] = k
        return k

    def along_many(self, x: torch.Tensor, specs: Sequence[Tuple[float, int]]) -> torch.Tensor:
        """x [H, W] averaged along each (angle, length) line: [n, H, W]."""
        size = max(int(L) for _, L in specs)
        w = np.zeros((len(specs), 1, size, size), np.float32)
        for i, (a, L) in enumerate(specs):
            o = (size - L) // 2
            w[i, 0, o:o + L, o:o + L] = self._line(a, int(L))
        p = size // 2
        xp = self._pad(x[None, None], p, p)
        return F.conv2d(xp, torch.from_numpy(w).to(self.dev))[0]

    def along(self, x: torch.Tensor, angle: float, length: int) -> torch.Tensor:
        return self.along_many(x, [(angle, length)])[0]

    def along_rgb(self, x: torch.Tensor, angle: float, length: int) -> torch.Tensor:
        """x [H, W, 3] -> each channel averaged along the line."""
        w = torch.from_numpy(self._line(angle, length)).to(self.dev)[None, None]
        p = length // 2
        xp = self._pad(x.permute(2, 0, 1)[:, None], p, p)
        return F.conv2d(xp, w)[:, 0].permute(1, 2, 0)

    @staticmethod
    def _pad(x4: torch.Tensor, ph: int, pw: int) -> torch.Tensor:
        """OpenCV's BORDER_REFLECT_101 (torch 'reflect'), for any size: torch's needs the pad
        under the size, so a large pad reflects in steps."""
        while ph > 0 or pw > 0:
            h, w = x4.shape[-2:]
            sh, sw = min(ph, h - 1), min(pw, w - 1)
            x4 = F.pad(x4, (sw, sw, sh, sh), mode="reflect")
            ph, pw = ph - sh, pw - sw
        return x4

    # ---- morphology with an ellipse (OpenCV's structuring element) ------------------
    def _rows_of(self, k: int):
        rows = self._rows.get(k)
        if rows is None:
            m = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
            c = k // 2
            rows = []
            for y in range(k):
                xs = np.nonzero(m[y])[0]
                if len(xs):
                    rows.append((y - c, int(xs.min()) - c, int(xs.max()) - c))
            self._rows[k] = rows
        return rows

    def _morph(self, x: torch.Tensor, k: int, take_max: bool) -> torch.Tensor:
        """Erode (min) or dilate (max) [H, W] with OpenCV's k x k ellipse; outside the
        frame ignored (OpenCV's default border value)."""
        fill = float("-inf") if take_max else float("inf")
        H, W = x.shape
        out = torch.full_like(x, fill)
        cache = {}
        for dy, x0, x1 in self._rows_of(k):
            key = (x0, x1)
            row = cache.get(key)
            if row is None:
                # the window x+x0 .. x+x1 (symmetric for an ellipse)
                w = x1 - x0 + 1
                src = x if take_max else -x
                pooled = F.max_pool2d(F.pad(src[None, None], (-x0, x1, 0, 0), value=float("-inf")),
                                      (1, w), stride=1)[0, 0]
                row = pooled if take_max else -pooled
                cache[key] = row
            # out[y] = op(out[y], row[y + dy])
            if dy == 0:
                out = torch.maximum(out, row) if take_max else torch.minimum(out, row)
            elif dy > 0:
                seg = row[dy:]
                tgt = out[:H - dy]
                out[:H - dy] = torch.maximum(tgt, seg) if take_max else torch.minimum(tgt, seg)
            else:
                seg = row[:H + dy]
                tgt = out[-dy:]
                out[-dy:] = torch.maximum(tgt, seg) if take_max else torch.minimum(tgt, seg)
        return out

    def erode(self, x, k):
        return self._morph(x, k, False)

    def dilate(self, x, k):
        return self._morph(x, k, True)

    def tophat(self, x: torch.Tensor, k: int) -> torch.Tensor:
        return x - self.dilate(self.erode(x, k), k)

    # ---- blur, gradients, resizing -------------------------------------------------
    def gauss(self, x: torch.Tensor, sigma: float) -> torch.Tensor:
        """cv2.GaussianBlur(x, (0, 0), sigma) for float images."""
        ks = int(np.rint(sigma * 8 + 1)) | 1
        g = self._gauss.get((ks, sigma))
        if g is None:
            g = torch.from_numpy(cv2.getGaussianKernel(ks, sigma, cv2.CV_32F).ravel()).to(self.dev)
            self._gauss[(ks, sigma)] = g
        p = ks // 2
        y = F.conv2d(self._pad(x[None, None], 0, p), g.view(1, 1, 1, ks))
        y = F.conv2d(self._pad(y, p, 0), g.view(1, 1, ks, 1))
        return y[0, 0]

    def sobel_mag(self, x: torch.Tensor) -> torch.Tensor:
        kx = torch.tensor([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], dtype=torch.float32, device=self.dev)
        w = torch.stack([kx, kx.t()])[:, None]
        g = F.conv2d(self._pad(x[None, None], 1, 1), w)[0]
        return torch.hypot(g[0], g[1])

    @staticmethod
    def half(x: torch.Tensor) -> torch.Tensor:
        """cv2.resize(x, None, fx=0.5, fy=0.5, interpolation=INTER_AREA)."""
        H, W = x.shape
        if H % 2 == 0 and W % 2 == 0:
            return F.avg_pool2d(x[None, None], 2)[0, 0]
        return F.interpolate(x[None, None], (int(round(H * 0.5)), int(round(W * 0.5))), mode="area")[0, 0]

    @staticmethod
    def resize(x: torch.Tensor, hw) -> torch.Tensor:
        """cv2.resize(..., INTER_LINEAR) of [H, W] or [C, H, W]."""
        x4 = x[None, None] if x.ndim == 2 else x[None]
        y = F.interpolate(x4, tuple(hw), mode="bilinear", align_corners=False)
        return y[0, 0] if x.ndim == 2 else y[0]

    def median(self, x: torch.Tensor) -> float:
        x = x.reshape(-1).float()
        if x.numel() > 16_000_000:
            x = x[:: x.numel() // 16_000_000 + 1]
        return float(torch.quantile(x, 0.5))


def grey_of(frame_u8: torch.Tensor) -> torch.Tensor:
    """cv2.cvtColor(RGB2GRAY) on uint8 [H, W, 3] -- OpenCV's 15-bit fixed-point weights
    and rounding (exact) -- as float 0..1."""
    f = frame_u8.to(torch.int32)
    g = (f[..., 0] * 9798 + f[..., 1] * 19235 + f[..., 2] * 3735 + 16384) >> 15
    return g.to(torch.float32) / 255.0


def strands(ops: Ops, frame_u8: torch.Tensor, scale: float) -> torch.Tensor:
    """R.strands on the GPU (the grouping into pieces on the CPU): bool [H, W]."""
    g = grey_of(frame_u8)
    th = ops.tophat(g, R._odd(15 * scale))
    small = ops.half(th)
    ll = R._odd(R.STRAND_LINE * scale * 0.5)
    angles = np.arange(0, 180, 7.5)
    resp = ops.along_many(small, [(d, ll) for d in angles] + [(d + 90, ll) for d in angles])
    a = (resp[: len(angles)] - resp[len(angles):]).amax(0)
    a = ops.resize(a, th.shape)
    weak = ((a > R.STRAND_LO) & (th > 0.5 * R.STRAND_LO)).cpu().numpy()
    strong = (a > R.STRAND_HI).cpu().numpy()
    n, lab = cv2.connectedComponents(weak.astype(np.uint8), connectivity=8)
    keep = np.zeros(n, bool)
    keep[np.unique(lab[weak & strong])] = True
    keep[0] = False
    cand = keep[lab]
    long_ = R._streak_lengths(cand) >= R.STRAND_MIN_LEN * scale
    gk = R._odd(R.STRAND_GROW * scale)
    out = cv2.dilate(long_.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (gk, gk))).astype(bool)
    return torch.from_numpy(out).to(ops.dev)


_POOL = None
_LOCAL = None


def _flows(ext, greys, t, ks):
    """Forward and backward DIS flow between frame t and each neighbour, the pairs on
    worker threads (OpenCV releases the GIL; one DIS object per thread)."""
    global _POOL, _LOCAL
    import threading
    from concurrent.futures import ThreadPoolExecutor
    if _POOL is None:
        _POOL = ThreadPoolExecutor(max_workers=6, thread_name_prefix="rain-flow")
        _LOCAL = threading.local()

    def pair(k):
        dis = getattr(_LOCAL, "dis", None)
        if dis is None:
            dis = _LOCAL.dis = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
        return k, (dis.calc(greys[t], greys[k], None), dis.calc(greys[k], greys[t], None))
    return dict(_POOL.map(pair, ks))


def relight_gain(ops, frame: torch.Tensor, img: torch.Tensor, ok: torch.Tensor, scale: float):
    """R.relight_gain on the GPU: [3, H/2, W/2], or None."""
    def low(x):
        return ops.gauss(ops.half(x), R.RELIGHT_BLUR * scale * 0.5)
    v = ok.float()
    lv = low(v)
    g = torch.stack([(low(frame[..., c] * v / 255.0) + R.RELIGHT_FLOOR * lv)
                     / (low(img[..., c] * v / 255.0) + R.RELIGHT_FLOOR * lv + 1e-6) for c in range(3)])
    g = torch.where(lv < 0.25, torch.ones_like(g), g)
    if not bool((g >= R.RELIGHT_FROM).any()):
        return None
    w = ((g - R.RELIGHT_FROM) / (R.RELIGHT_FULL - R.RELIGHT_FROM)).clamp(0, 1)
    return (1.0 + w * (g - 1.0)).clamp(max=R.RELIGHT_MAX)


def texture_match(ops, frame: torch.Tensor, img: torch.Tensor, ok: torch.Tensor, scale: float) -> torch.Tensor:
    """R.texture_match on the GPU: [H/2, W/2], nan where img doesn't line up."""
    s = scale * 0.5

    def band(x):
        h = ops.half(x.mean(-1) / 255.0)
        return ops.gauss(h, R.TEXTURE_FINE * s) - ops.gauss(h, R.TEXTURE_COARSE * s)
    a, b = band(frame), band(img)
    v = ok[::2, ::2][: a.shape[0], : a.shape[1]].float()
    a, b = a * v, b * v
    sig = R.TEXTURE_WIN * scale * 0.5 / 2
    ab = ops.gauss(a * b, sig)
    aa = ops.gauss(a * a, sig) + R.TEXTURE_FLOOR ** 2
    bb = ops.gauss(b * b, sig) + R.TEXTURE_FLOOR ** 2
    out = ab / torch.sqrt(aa * bb)
    return torch.where(ops.gauss(v, sig) < 0.5, torch.full_like(out, float("nan")), out)


def background(ext, win_t: torch.Tensor, t: int, greys, quants=None):
    """R.RainExtractor._background on the GPU: [H, W, 3] float 0..1."""
    ops = ext.ops
    N, H, W = win_t.shape[:3]
    lo, hi = max(0, t - ext.radius), min(N, t + ext.radius + 1)
    stack = [win_t[t].float()]
    valid = [torch.ones((H, W), dtype=torch.bool, device=ops.dev)]
    if ext.s.mode == "moving":
        h2, w2 = greys[t].shape
        gx, gy = np.meshgrid(np.arange(w2, dtype=np.float32), np.arange(h2, dtype=np.float32))
        GY, GX = torch.meshgrid(torch.arange(H, dtype=torch.float32, device=ops.dev),
                                torch.arange(W, dtype=torch.float32, device=ops.dev), indexing="ij")
    flows = _flows(ext, greys, t, [k for k in range(lo, hi) if k != t]) if ext.s.mode == "moving" else {}
    for k in range(lo, hi):
        if k == t:
            continue
        if ext.s.mode == "static":
            stack.append(win_t[k].float())
            valid.append(torch.ones((H, W), dtype=torch.bool, device=ops.dev))
            continue
        # the flow and its check stay on the CPU (half size)
        f_tk, f_kt = flows[k]
        back = cv2.remap(f_kt, gx + f_tk[..., 0], gy + f_tk[..., 1], cv2.INTER_LINEAR,
                         borderMode=cv2.BORDER_CONSTANT, borderValue=1e3)
        ok = np.hypot(*(f_tk + back).transpose(2, 0, 1)) < R.FB_TOL + R.FB_REL * np.hypot(*f_tk.transpose(2, 0, 1))
        Ft = ops.resize(torch.from_numpy(f_tk).to(ops.dev).permute(2, 0, 1), (H, W)) / R.FLOW_SCALE
        okF = F.interpolate(torch.from_numpy(ok.astype(np.float32)).to(ops.dev)[None, None], (H, W),
                            mode="nearest")[0, 0] > 0.5
        mx, my = GX + Ft[0], GY + Ft[1]
        okF &= (mx >= 0) & (mx <= W - 1) & (my >= 0) & (my <= H - 1)
        mx, my = torch.round(mx * 32) / 32, torch.round(my * 32) / 32     # OpenCV's remap: 1/32 px
        grid = torch.stack([mx / max(W - 1, 1) * 2 - 1, my / max(H - 1, 1) * 2 - 1], -1)[None]
        img = win_t[k].permute(2, 0, 1)[None].float()
        warped = F.grid_sample(img, grid, mode="bilinear", padding_mode="border", align_corners=True)
        stack.append(warped[0].permute(1, 2, 0).round().clamp(0, 255))   # OpenCV's 8-bit remap rounds
        valid.append(okF)
    if R.RELIGHT and not ext.sparks:                          # R.RainExtractor._background's relight
        for i in range(1, len(stack)):
            g = relight_gain(ops, stack[0], stack[i], valid[i], ext.scale)
            if g is not None:
                stack[i] = (stack[i] * ops.resize(g, (H, W)).permute(1, 2, 0)).clamp(0, 255)
    ext.last_texture = None
    if R.TEXTURE and not ext.sparks and len(stack) > 1 and getattr(ext.s, "preset", "downpour") != "mist":
        m = torch.stack([texture_match(ops, stack[0], stack[i], valid[i], ext.scale) for i in range(1, len(stack))])
        m = torch.where(torch.isnan(m), torch.full_like(m, float("inf")), m).amin(0)
        ext.last_texture = torch.where(torch.isinf(m), torch.zeros_like(m), m)
    S = torch.stack(stack) / 255.0
    V = torch.stack(valid)
    ext.last_lined_up = (V[1:].float().mean(0).cpu().numpy()) if len(valid) > 1 else None
    masked = torch.where(V[..., None].expand_as(S), S, torch.nan)
    if quants is not None:                                      # several from the one stack (Sparks)
        out = torch.nanquantile(masked, torch.tensor(list(quants), device=ops.dev, dtype=masked.dtype), dim=0)
        return [out[i] for i in range(len(quants))]
    return torch.nanquantile(masked, ext.quant, dim=0)


def _angle(ext, th: torch.Tensor) -> float:
    ops = ext.ops
    q = ops.half(th)
    angles = list(range(0, 180, 5))
    resp = ops.along_many(q, [(a, 9) for a in angles] + [(a + 90, 9) for a in angles])
    scores = (resp[: len(angles)] - resp[len(angles):]).clamp(min=0).mean((1, 2)).tolist()
    return ext._angle_pick(dict(zip(angles, scores)))


def _open_vertical3(m: torch.Tensor) -> torch.Tensor:
    """cv2.morphologyEx(mask, MORPH_OPEN, MORPH_RECT (1, 3)): erode then dilate, vertically."""
    x = m.float()
    up = F.pad(x[None, None], (0, 0, 1, 1), value=1.0)[0, 0]
    er = torch.minimum(torch.minimum(up[:-2], up[1:-1]), up[2:])
    dn = F.pad(er[None, None], (0, 0, 1, 1), value=0.0)[0, 0]
    return torch.maximum(torch.maximum(dn[:-2], dn[1:-1]), dn[2:]) > 0.5


def rain_of(ext, frame_u8: torch.Tensor, bg: torch.Tensor) -> Tuple[np.ndarray, np.ndarray]:
    """R.RainExtractor._rain on the GPU; returns numpy (rain, length) like the CPU's."""
    ops = ext.ops
    res = (frame_u8.float() / 255.0 - bg).clamp(min=0)
    if getattr(ext.s, "preset", "downpour") == "mist":
        return sheet(ext, frame_u8, bg, res)
    g = res.mean(-1)
    th = ops.tophat(g, ext.thin)
    if ext.s.any_direction:
        specs = [(a, ext.line) for a in range(0, 180, 15)] + [(a + 90, ext.short) for a in range(0, 180, 15)]
        resp = ops.along_many(th, specs)
        n = len(specs) // 2
        aniso = (resp[:n] - resp[n:]).amax(0)
    else:
        ext.angle = _angle(ext, th if not ext.soft else ops.tophat(g, R._odd(7 * ext.scale)))
        resp = ops.along_many(th, [(ext.angle, ext.line), (ext.angle + 90, ext.short)])
        aniso = resp[0] - resp[1]
    if ext.relative:
        light = ops.gauss(bg.mean(-1), 40 * ext.scale)
        ref = ops.median(light)
        aniso = aniso * (ref / light.clamp(min=0.25 * ref + 1e-6))
    med = ops.median(aniso)
    noise = 1.4826 * ops.median((aniso - med).abs())
    mask = (aniso > max(ext.floor, ext.k * noise)) & (th > 0)
    if ext.s.strands:
        mask &= ~strands(ops, frame_u8, ext.scale)
    if getattr(ext.s, "lights", True):                  # the CPU's R.lights: one blur and an opening
        mask &= ~torch.from_numpy(R.lights(frame_u8.cpu().numpy(), ext.scale)).to(ops.dev)
    tex = getattr(ext, "last_texture", None)
    if R.TEXTURE and tex is not None:                   # Keep texture out (R.RainExtractor._rain)
        mask &= ops.resize(tex, mask.shape) < R.TEXTURE_FROM
    mask = _open_vertical3(mask)
    rain = (res * mask[..., None]).cpu().numpy().astype(np.float32)
    ext.last_src = res
    m = mask.cpu().numpy()
    length = R._streak_lengths(m)
    length[~m] = 0
    return rain, length


def box(ops: "Ops", x: torch.Tensor, k: int) -> torch.Tensor:
    """cv2.blur(x, (k, k)): a k x k mean, reflect-101 border."""
    p = k // 2
    w = torch.full((1, 1, k, k), 1.0 / (k * k), device=ops.dev)
    return F.conv2d(ops._pad(x[None, None], p, p), w)[0, 0]


def sparks_of(ext, frame_u8: torch.Tensor, nb: torch.Tensor) -> Tuple[np.ndarray, np.ndarray]:
    """R.RainExtractor._sparks on the GPU (the pieces' shapes on the CPU)."""
    ops = ext.ops
    sc = ext.scale
    f = frame_u8.float() / 255.0
    k = R._odd(R.SPARK_SIZE * sc)
    detail = torch.stack([ops.tophat(f[..., i].contiguous(), k) for i in range(3)], -1)
    moved = (f - nb).clamp(min=0).amax(-1)
    cand = (detail.amax(-1) > R.SPARK_ABS) & (moved > R.SPARK_ABS)
    dens = box(ops, cand.float(), R._odd(61 * sc)).cpu().numpy()
    grown = R._spark_pieces(cand.cpu().numpy(), detail.cpu().numpy(), dens, sc)
    light = (detail * torch.from_numpy(grown).to(ops.dev)[..., None]).cpu().numpy().astype(np.float32)
    length = R._streak_lengths(grown)
    length[~grown] = 0
    ext.last_src = detail
    return light, length


def sheet(ext, frame_u8, bg, res) -> Tuple[np.ndarray, np.ndarray]:
    """R.RainExtractor._sheet (Mist) on the GPU."""
    ops = ext.ops
    g = res.mean(-1)
    if ext.s.any_direction:
        rain = res.clone()
    else:
        ext.angle = _angle(ext, ops.tophat(g, R._odd(7 * ext.scale)))
        rain = ops.along_rgb(res, ext.angle, R._odd(5 * ext.scale))
    rain = torch.minimum(rain, res + 0.004)
    g2 = rain.mean(-1)
    fine = (g2 - ops.gauss(g2, 6 * ext.scale)).clamp(min=0)
    rain = rain * (fine / (g2 + 1e-5)).clamp(0, 1)[..., None]
    on = rain.mean(-1)
    sel = on[on > 0.5 / 255]
    if sel.numel():
        cap = 3.0 * ops.median(sel)
        rain = rain * torch.clamp(cap / on.clamp(min=1e-6), max=1.0)[..., None]
    grey = rain.mean(-1, keepdim=True)
    rain = grey + (rain - grey) / 3.0
    keep = torch.ones_like(g)
    lined = getattr(ext, "last_lined_up", None)
    if lined is not None:
        lined_t = ops.resize(torch.from_numpy(np.asarray(lined, np.float32)).to(ops.dev), g.shape)
        edges = ops.sobel_mag(bg.mean(-1)) > 0.04
        bad = ((lined_t < 0.75) & edges).float()
        bad = ops.dilate(bad, R._odd(9 * ext.scale))
        keep = 1.0 - (ops.gauss(bad, 4 * ext.scale) * 2).clamp(0, 1)
    if ext.s.strands:
        keep = keep.masked_fill(strands(ops, frame_u8, ext.scale), 0.0)
    if getattr(ext.s, "lights", True):
        keep = keep.masked_fill(torch.from_numpy(R.lights(frame_u8.cpu().numpy(), ext.scale)).to(ops.dev), 0.0)
    rain = (rain * keep[..., None]).clamp(min=0)
    length = rain.mean(-1)
    length = torch.where(length < 0.5 / 255, torch.zeros_like(length), length)
    rain = rain * (length > 0)[..., None]
    ext.last_src = res
    return rain.cpu().numpy().astype(np.float32), length.cpu().numpy().astype(np.float32)


def extract_lengths(ext, window: np.ndarray, centres: Sequence[int], keep_src: bool = False) -> List[tuple]:
    """R.RainExtractor._extract on the GPU: (rain, length, the light it was cut from or None)."""
    greys = [ext._flow_grey(f) for f in window] if ext.s.mode == "moving" else None
    with torch.inference_mode():
        win_t = torch.from_numpy(np.ascontiguousarray(window)).to(ext.ops.dev)
        out = []
        for t in centres:
            if ext.sparks:
                nb = background(ext, win_t, t, greys)
                rain, length = sparks_of(ext, win_t[t], nb)
                src_t = ext.last_src
                if getattr(ext.s, "reflections", False):            # the same step as the CPU's
                    rain, length = ext.drop_steady(window, t, greys, nb.float().cpu().numpy(), rain, length)
                ext.last_src = src_t
            else:
                rain, length = rain_of(ext, win_t[t], background(ext, win_t, t, greys))
            src = ext.last_src.float().cpu().numpy().astype(np.float32) if keep_src else None
            out.append((rain, length, src))
        return out
