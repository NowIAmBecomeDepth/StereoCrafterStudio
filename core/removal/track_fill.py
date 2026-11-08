"""track_fill.py -- Edge Fill's Copy From Other Frames with AllTracker (user, 2026-11-07: "Build it in").

flow_fill.py asks SEA-RAFT where the hidden strip goes; no model can see that (the strip is a blur), so it
borrows the motion of the picture beside it. This turns the question round: AllTracker (alltracker/, MIT;
weights from Settings > Studio > Models) tracks the REAL picture of a source frame through the frames around
it -- it keeps a point's track while the point passes behind something, here the blurred strip -- and where a
source pixel's track lands in another frame's strip, its colour is laid there (bilinear). Benched 2026-11-07 on
8 pans of Tears of Steel with the true edge hidden (scratchpad waft/at_time.py): sharper than SEA-RAFT on 7 of
8 (+0.7..+5 dB), less flicker on 7, about 40% quicker (33 s vs 53 s for 96 frames), ~6 GB of VRAM.

What made it clean (each a fault on the bench):
  * one tracking pass per SOURCE frame (every STEP-th) covers every frame within REACH of it -- tracking from
    each frame being filled cost 30 s a frame;
  * each copy weighed by nearness and by AllTracker's own confidence in the track;
  * a pixel kept only where its sources agree (brightness spread under SPREAD) -- wrong tracks on slow pans
    landed alone, at another colour;
  * never across a cut (CUT_CORREL): three of the eight pans held one, and a tree from the next shot was
    pasted in (a clip from Scene Cut is one shot; a hand-cut one may not be).
Then flow_fill's own tidy (islands / small holes) and hand-off to the inpainter (its mask grown and faded).

Same contract as flow_fill.propagate: (fill, copy 0-255, rose bool) for the left `hidden` columns, or None.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np

from core import weights_paths

from .flow_fill import Progress, _device, cached, handoff, tidy

logger = logging.getLogger(__name__)

#: the checkpoint (Settings > Studio > Models > AllTracker)
WEIGHTS = ("AllTracker", "alltracker.pth")
STEP = 8                  # every 8th frame is a source...
REACH = 16                # ...tracked this many frames each way
WINDOW = 16               # AllTracker's own window
ITERS = 4
DECAY = 6.0               # a copy d frames away weighs exp(-d / 6)
MARGIN = 3                # px into a source's real picture a tracked pixel must start
SPREAD = 12.0             # a pixel whose sources' brightness spreads this far (0-255) is dropped
MIN_WEIGHT = 0.02         # ...and one with less than this much behind it
CUT_CORREL = 0.6          # consecutive frames' colour histograms less alike than this: a cut
WORK_HEIGHT = 800         # AllTracker tracks at most this tall (the bench's size); the tracks are scaled up
LOOK_SOURCES = 4          # the quick look: this many sources tracked once...
LOOK_LEAST = 0.005        # ...must land on this share of their frames' strips, or the pass is skipped


def weights_path() -> Path:
    """Where the checkpoint is (or would be downloaded to)."""
    return _found() or weights_paths.root() / WEIGHTS[0] / WEIGHTS[1]


def _found() -> Optional[Path]:
    for base in weights_paths.roots():
        p = Path(base) / WEIGHTS[0] / WEIGHTS[1]
        if p.is_file():
            return p
    return None


def available() -> bool:
    """AllTracker's checkpoint is in the models folder."""
    return _found() is not None


class _Tracker:
    """AllTracker on the GPU, bf16: track(seq) -> where each pixel of seq[0] is in every frame of seq."""

    def __init__(self):
        import torch
        from alltracker import Net
        self.torch = torch
        net = Net(WINDOW)
        sd = torch.load(str(_found()), map_location="cpu", weights_only=True)
        net.load_state_dict(sd["model"], strict=True)
        self.net = net.cuda().eval()

    def track(self, seq):
        """seq [S, 3, h, w] float 0-255 on the GPU -> flows [S, 2, h, w], confidence [S, h, w] (frame 0 the query)."""
        torch = self.torch
        with torch.no_grad(), torch.autocast("cuda", dtype=torch.bfloat16):
            flows, vc, _, _ = self.net.forward_sliding(seq[None], iters=ITERS, sw=None, is_training=False)
        return flows[0].to(seq.device).float(), vc[0, :, 1].to(seq.device).float()

    def release(self):
        self.net = None
        try:
            self.torch.cuda.empty_cache()
        except Exception:
            pass


def _make_tracker():
    """The tracker (the tests put a stand-in here)."""
    return _Tracker()


def shots(frames) -> np.ndarray:
    """(n,) int: each frame's shot -- a new one where consecutive frames' colours stop matching (a cut)."""
    n = len(frames)
    out = np.zeros(n, int)
    prev = None
    for i in range(n):
        small = cv2.resize(np.asarray(frames[i]), (160, 90), interpolation=cv2.INTER_AREA)
        h = cv2.normalize(cv2.calcHist([cv2.cvtColor(small, cv2.COLOR_RGB2HSV)], [0, 1], None, [32, 32],
                                       [0, 180, 0, 256]), None).flatten()
        if prev is not None:
            out[i] = out[i - 1] + int(cv2.compareHist(prev, h, cv2.HISTCMP_CORREL) < CUT_CORREL)
        prev = h
    return out


def propagate(frames: np.ndarray, hidden: int, progress: Progress, stop, lo: float = 0.0, hi: float = 1.0,
              near: Optional[np.ndarray] = None) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """Copy real pixels into the left `hidden` columns of `frames` ((n, h, w, 3) uint8; a memmap is fine),
    as flow_fill.propagate does: (fill (n, h, hidden, 3) uint8, copy (n, h, hidden) uint8 0-255, rose
    (n, h, hidden) bool), or None when no frame shows the strip. `near` (n, h, w) uint8 0-255 (Depth Aware,
    depth_guard): a source's near pixels aren't copied -- a subject passing would be laid into the background's
    hole. The same input again in this session comes from the cache (flow_fill.cached). Raises erase.Stopped."""
    settings = (STEP, REACH, WINDOW, ITERS, DECAY, MARGIN, SPREAD, MIN_WEIGHT, CUT_CORREL, WORK_HEIGHT,
                LOOK_SOURCES, LOOK_LEAST)
    return cached("AllTracker", settings, frames, hidden, near, progress, lo, hi,
                  lambda: _propagate(frames, hidden, progress, stop, lo, hi, near))


def _propagate(frames, hidden, progress, stop, lo, hi, near):
    import torch
    import torch.nn.functional as F
    from . import erase as E

    n, H, W = frames.shape[:3]
    M = hidden
    if n < 2 or M >= W - 16:
        return None
    dev = _device()

    def check():
        if stop.is_set():
            raise E.Stopped()

    # what AllTracker sees: the strip blur-filled (as the inpainter sees it), at most WORK_HEIGHT tall
    scale = min(1.0, WORK_HEIGHT / float(H))
    h, w = max(8, int(round(H * scale / 8)) * 8), max(8, int(round(W * scale / 8)) * 8)
    seen = np.empty((n, h, w, 3), np.uint8)
    holes = np.zeros((min(n, 32), H, W), bool)
    holes[:, :, :M] = True
    for a in range(0, n, 32):
        check()
        chunk = np.array(frames[a:a + 32])
        filled = E.prefill_frames(chunk, holes[:len(chunk)])
        for j, f in enumerate(filled):
            seen[a + j] = cv2.resize(f, (w, h), interpolation=cv2.INTER_AREA)
    shot = shots(seen)
    if shot[-1]:
        logger.info(f"  Copy From Other Frames: {int(shot[-1])} cut(s) in the clip; nothing is copied across one")
    progress(lo + 0.05 * (hi - lo), "Copy From Other Frames: looking for motion")

    tracker = _make_tracker()
    ys, xs = torch.meshgrid(torch.arange(H, device=dev, dtype=torch.float32),
                            torch.arange(W, device=dev, dtype=torch.float32), indexing="ij")
    src_ok = xs >= M + MARGIN

    def run(k, idx):
        """frame k tracked through idx (k first) -> [(t, flow at full size [2, H, W], conf [H, W])]."""
        seq = torch.from_numpy(seen[idx]).to(dev).permute(0, 3, 1, 2).float()
        flows, conf = tracker.track(seq)
        flows, conf = flows.to(dev), conf.to(dev)
        out = []
        for j, t in enumerate(idx[1:], start=1):
            f = flows[j]
            if (h, w) != (H, W):
                f = F.interpolate(f[None], size=(H, W), mode="bilinear", align_corners=False)[0]
                f = f * torch.tensor([W / float(w), H / float(h)], device=dev).view(2, 1, 1)
                c = F.interpolate(conf[j][None, None], size=(H, W), mode="bilinear", align_corners=False)[0, 0]
            else:
                c = conf[j]
            out.append((t, f, c))
        return out

    def windows(k):
        fwd = [i for i in range(k, min(n, k + REACH + 1)) if shot[i] == shot[k]]
        back = [i for i in range(k, max(-1, k - REACH - 1), -1) if shot[i] == shot[k]]
        return [x for x in (fwd, back) if len(x) > 1]

    def landing(f):
        qx, qy = xs + f[0], ys + f[1]
        return qx, qy, src_ok & (qx > -0.5) & (qx < M - 0.5) & (qy > -0.5) & (qy < H - 0.5)

    try:
        # 1. a quick look: do a few sources' tracks land in their neighbours' strips at all?
        look = np.unique(np.linspace(0, n - 1, min(n, LOOK_SOURCES)).astype(int))
        landed = tried = 0
        for k in look:
            check()
            for idx in windows(int(k)):            # both ways: a pan shows the strip earlier or later
                for _t, f, _c in run(int(k), idx):
                    landed += int(landing(f)[2].sum())
                    tried += H * M
        if tried == 0 or landed < LOOK_LEAST * tried:
            logger.info("  Copy From Other Frames: no frame shows the strip (a still shot, or one moving the "
                        "other way): the inpainter fills it")
            return None

        # 2. each source frame's real picture tracked into its neighbours' strips, laid there weighted
        sources = sorted(set(range(0, n, STEP)) | {n - 1} | {i for i in range(1, n) if shot[i] != shot[i - 1]})
        fill = np.zeros((n, H, M, 3), np.uint8)
        found = np.zeros((n, H, M), bool)
        trust = np.zeros((n, H, M), np.float32)
        acc = {}                                   # t -> [num (3, H*M), den (H*M), sq (H*M)] on the GPU

        def finish(t):
            num, den, sq = acc.pop(t)
            d = den.clamp(min=1e-6)
            mean = num / d
            spread = (sq / d - mean.mean(0) ** 2).clamp(min=0).sqrt()
            ok = (den > MIN_WEIGHT) & (spread < SPREAD)
            fill[t] = mean.reshape(3, H, M).permute(1, 2, 0).round().clamp(0, 255).byte().cpu().numpy()
            found[t] = ok.reshape(H, M).cpu().numpy()
            trust[t] = ((den / 0.1).clamp(max=1.0) * ok).reshape(H, M).cpu().numpy()

        for si, k in enumerate(sources):
            check()
            colours = torch.from_numpy(np.ascontiguousarray(frames[k])).to(dev).permute(2, 0, 1).float().reshape(3, -1)
            far = (1.0 - torch.from_numpy(np.ascontiguousarray(near[k])).to(dev).float().reshape(-1) / 255.0
                   if near is not None else None)
            for idx in windows(k):
                for t, f, c in run(k, idx):
                    qx, qy, land = landing(f)
                    if not bool(land.any()):
                        continue
                    if t not in acc:
                        acc[t] = [torch.zeros(3, H * M, device=dev), torch.zeros(H * M, device=dev),
                                  torch.zeros(H * M, device=dev)]
                    num, den, sq = acc[t]
                    px, py = qx[land], qy[land]
                    col = colours[:, land.reshape(-1)]
                    lum = col.mean(0)
                    w0 = c[land] * math.exp(-abs(t - k) / DECAY)
                    if far is not None:
                        w0 = w0 * far[land.reshape(-1)]
                    x0, y0 = px.floor(), py.floor()
                    for dx in (0, 1):
                        for dy in (0, 1):
                            xi, yi = x0 + dx, y0 + dy
                            wt = (1 - (px - xi).abs()) * (1 - (py - yi).abs()) * w0
                            ok = (xi >= 0) & (xi < M) & (yi >= 0) & (yi < H) & (wt > 0)
                            flat = (yi[ok] * M + xi[ok]).long()
                            den.index_add_(0, flat, wt[ok])
                            num.index_add_(1, flat, col[:, ok] * wt[ok])
                            sq.index_add_(0, flat, lum[ok] ** 2 * wt[ok])
            # frames no later source can reach are done: off the card
            reach_next = sources[si + 1] - REACH if si + 1 < len(sources) else n
            for t in [t for t in acc if t < reach_next]:
                finish(t)
            progress(lo + (0.05 + 0.8 * (si + 1) / len(sources)) * (hi - lo),
                     f"Copy From Other Frames: frame {min(n, k + 1)} of {n}")
        for t in list(acc):
            finish(t)
    finally:
        tracker.release()
        if dev.type == "cuda":
            torch.cuda.empty_cache()

    # 3. flow_fill's tidy and hand-off to the inpainter
    tidy(found, fill, frames, M)
    copy, rose_mask = handoff(found, trust)
    logger.info(f"  Copy From Other Frames (AllTracker): {float(found.mean()):.0%} of the strip copied from "
                f"other frames; the inpainter fills the rest")
    progress(hi, "Copy From Other Frames: done")
    return fill, copy, rose_mask
