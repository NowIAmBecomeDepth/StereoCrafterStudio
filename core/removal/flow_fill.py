"""flow_fill.py -- Edge Fill's Copy From Other Frames (user, 2026-11-02): before ROSE paints the edge
strip, the real picture is copied into it from the frames where a pan or a move shows it.

Splatting's shift leaves a strip at a frame's edge that this frame never saw -- but when the camera
pans or the subject moves, a frame further on often did see it. SEA-RAFT optical flow (sea_raft/,
BSD 3-Clause; weights from Settings > Studio > Models) finds where each strip pixel is in up to 24
other frames (1 to 48 away); where one shows it, its real pixel is taken. ROSE then fills only what
no frame shows. Measured 2026-11-02 (scratchpad bench, the left eye's real edge hidden under the
strip): 25.6 dB against ROSE alone's 23.4, 34.4 against 29.4 where frames reveal the edge, the same
flicker; on a pan (powers2_00039609) 62% of the strip came from real frames.

Works on Edge Fill's crop: (n, h, w, 3) uint8, the strip on the left (a right edge comes mirrored).
What made it clean (each was a visible fault on the bench):
  * the strip's flow is each row's median of the real picture's flow just beside it, smoothed down
    the frame -- the network's own flow inside a blurred strip found only 10% of it;
  * a copy is kept only when the round trip holds: k's flow at the landing point leads back (a person
    walking against the background failed it, and smeared);
  * every frame that passes is averaged, nearer frames and better round trips weighted more -- one
    frame per pixel left a patchwork with hard seams;
  * islands of copies under 400 px are dropped, holes under 400 px filled from around them;
  * steadying: each frame's strip is averaged with its +-2 neighbours', warped along the strip's flow,
    and a pixel this frame missed takes theirs -- a letter went soft for one frame without it;
  * ...and from a neighbour's real picture only where its round trip held (STEADY_CHECK), a frame's
    share faded out where its copy stops (SEAM), and no pixel copied from one far frame alone (SOLO):
    a pole passing close to a moving car showed early, doubled and as a stray dark shape without them.
A static shot (or one moving the wrong way) shows nothing new: a quick look at 8 frames first skips
the pass when no copy would be made, so it costs seconds, not minutes.
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Callable, Optional, Tuple

import cv2
import numpy as np

from core import weights_paths

logger = logging.getLogger(__name__)

Progress = Callable[[float, str], None]

#: the checkpoint (Settings > Studio > Models > SEA-RAFT)
WEIGHTS = ("SEA-RAFT", "model.safetensors")
#: SEA-RAFT's spring-M configuration (config/eval/spring-M.json upstream)
CONFIG = {"use_var": True, "var_min": 0, "var_max": 10, "pretrain": "resnet34", "initial_dim": 64,
          "block_dims": [64, 128, 256], "radius": 4, "dim": 128, "num_blocks": 2, "iters": 4}

OFFSETS = (1, 2, 3, 4, 6, 8, 12, 16, 24, 32, 40, 48)   # the frames looked in, each side
BAND = (4, 60)            # px past the hidden strip whose flow each row of the strip takes
MARGIN = 3                # px into a frame's real picture a copy must land
FB_ABS, FB_REL = 1.5, 0.05    # the round trip: within 1.5 px + 5% of the motion
NEAR_DECAY = 6.0          # a frame d away weighs exp(-d / 6)
SMALL = 400               # px: smaller islands of copies dropped, smaller holes filled from around them
STEADY = 2                # frames each side the strip is steadied over
#: ...taking a neighbour's real picture only where its round trip holds, as the copies do (user,
#: 2026-11-02, ch.1 smintroscenes00006103: a pole passing close was pasted a frame early at the
#: background's speed -- a ghost, then a doubled pole); its own strip is still taken where it copied
STEADY_CHECK = True
#: px over which a frame's share fades out where its copy stops (its landing leaves the picture, its round
#: trip nears the limit) -- a hard stop left a hard-edged shape in the average (user, 2026-11-02, ch.1
#: smintroscenes00006103 frame 2: a dark parallelogram on the pavement, sharpened by Detail) (0: off)
SEAM = 12.0
#: a pixel only one frame shows is copied only from a frame this near; far frames count when two or more
#: show it -- a lone far frame let an occluder through (user, 2026-11-02, ch.1 smintroscenes00006103: the
#: pole at frame 14, 12 frames on, passed the round trip and was frame 2's whole copy: a dark shape) (0: off)
SOLO = 4
FILL_FROM = 0.3          # the neighbours' share that fills a pixel this frame missed
LOOK_FRAMES = 8           # the quick look: frames tried...
LOOK_OFFSETS = (24, 48)   # ...against these, each side
LOOK_LEAST = 0.005        # ...and the share of their strips that must copy to run the pass
ROSE_GROW = 13            # px: ROSE's mask grown past what no frame showed
BATCH = 16                # flow pairs at once
#: the least half-size side SEA-RAFT runs on (its correlation pyramid halves 1/8 size three times; a
#: 160x96 crop failed in check_fill_edges, 2026-11-03)
MIN_HALF = 128


def weights_path() -> Path:
    return weights_paths.find(WEIGHTS[0], marker=WEIGHTS[1]) / WEIGHTS[1]


def available() -> bool:
    """SEA-RAFT's checkpoint is in the models folder."""
    try:
        return weights_path().is_file()
    except OSError:
        return False


def _device():
    import torch
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def _make_flow():
    """The flow model (the tests put a stand-in here)."""
    return _Flow()


class _Flow:
    """SEA-RAFT on the GPU, half size (its spring-M scale), bf16."""

    def __init__(self):
        import torch
        from safetensors.torch import load_file
        from sea_raft import RAFT
        self.torch = torch
        self.args = argparse.Namespace(**CONFIG)
        model = RAFT(self.args)
        # the release leaves out four downsample norms (16 tensors); they stay at their identity start,
        # which is how the checkpoint scores (2026-11-02 bench)
        model.load_state_dict(load_file(str(weights_path())), strict=False)
        self.model = model.cuda().eval()

    def __call__(self, a, b):
        """a, b: [P, 3, h, w] float 0-255 at half size -> [P, 2, h, w] a->b flow in half-size px."""
        torch = self.torch
        with torch.no_grad(), torch.autocast("cuda", dtype=torch.bfloat16):
            out = self.model(a, b, iters=self.args.iters, test_mode=True)
        return out["flow"][-1].float()

    def release(self):
        self.model = None
        try:
            self.torch.cuda.empty_cache()
        except Exception:
            pass


def propagate(frames: np.ndarray, hidden: int, progress: Progress, stop, lo: float = 0.0, hi: float = 1.0
              ) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """Copy real pixels into the left `hidden` columns of `frames` ((n, h, w, 3) uint8; a memmap is fine).

    Returns (fill (n, h, hidden, 3) uint8, copy (n, h, hidden) uint8 0-255: how much of the fill to
    take over ROSE's, rose (n, h, hidden) bool: where ROSE must paint), or None when no frame shows the
    strip (a static shot). Raises erase.Stopped."""
    import torch
    import torch.nn.functional as F
    from . import erase as E

    n, H, W = frames.shape[:3]
    if n < 2 or hidden >= W - BAND[1] - 8:
        return None
    M = hidden
    dev = _device()

    def check():
        if stop.is_set():
            raise E.Stopped()

    # the frames the flow sees: the strip blur-filled (as ROSE sees it), at half size
    h2, w2 = H // 2, W // 2
    half = np.empty((n, h2, w2, 3), np.uint8)
    holes = np.zeros((min(n, 32), H, W), bool)
    holes[:, :, :M] = True
    for a in range(0, n, 32):
        check()
        chunk = np.array(frames[a:a + 32])          # writable: a read-only memmap warns in torch
        filled = E.prefill_frames(chunk, holes[:len(chunk)])
        for j, f in enumerate(filled):
            half[a + j] = cv2.resize(f, (w2, h2), interpolation=cv2.INTER_AREA)
    progress(lo + 0.05 * (hi - lo), "Copy From Other Frames: looking for motion")

    flow = _make_flow()
    if not hasattr(flow, "between") and min(h2, w2) < MIN_HALF:   # SEA-RAFT itself fails that small
        flow.release()
        logger.info(f"  Copy From Other Frames: the {W}x{H} crop is too small for SEA-RAFT; the inpainter fills it all")
        return None
    ys, xs = torch.meshgrid(torch.arange(H, device=dev, dtype=torch.float32),
                            torch.arange(W, device=dev, dtype=torch.float32), indexing="ij")
    ysm, xsm = ys[:, :M], xs[:, :M]

    def flows(pairs):
        if hasattr(flow, "between"):        # the tests' stand-in: (t, k) pairs -> full-size flows
            return flow.between(pairs, H, W).to(dev)
        out = []
        for s in range(0, len(pairs), BATCH):
            pp = pairs[s:s + BATCH]
            a = torch.from_numpy(half[[p for p, _ in pp]]).to(dev).permute(0, 3, 1, 2).float()
            b = torch.from_numpy(half[[q for _, q in pp]]).to(dev).permute(0, 3, 1, 2).float()
            f = flow(a, b)
            out.append(F.interpolate(f, size=(H, W), mode="bilinear", align_corners=False) * 2.0)
        return torch.cat(out)

    def strip_flow(f):
        """the strip's flow: each row the median of the band beside it, smoothed down the frame -> [P,2,H,M]"""
        band = f[:, :, :, M + BAND[0]:M + BAND[1]].median(dim=3).values             # P,2,H
        band = F.avg_pool1d(band.reshape(-1, 1, H), 31, stride=1, padding=15,
                            count_include_pad=False).reshape(f.shape[0], 2, H)
        return band[..., None].expand(-1, -1, -1, M)

    def sample(img, fx, fy):
        """img [P,C,H,W] at the strip's pixels moved by (fx, fy) [P,H,M] -> [P,C,H,M]"""
        gx = (xsm + fx) / (W - 1) * 2 - 1
        gy = (ysm + fy) / (H - 1) * 2 - 1
        return F.grid_sample(img, torch.stack([gx, gy], -1), mode="bilinear", padding_mode="border",
                             align_corners=True)

    def gather(t, ks, want_near=False, solo=False):
        """frame t's strip from frames ks: (sum of weighted pixels [3,H,M], weights [H,M], near flows)"""
        f_tk = flows([(t, k) for k in ks])
        f_kt = flows([(k, t) for k in ks])
        fs = strip_flow(f_tk)
        fx, fy = fs[:, 0], fs[:, 1]
        qx, qy = xsm + fx, ysm + fy
        ok = (qx >= M + MARGIN) & (qx <= W - 1) & (qy >= 0) & (qy <= H - 1)   # the last row too (a 7-row ROSE band without)
        back = sample(f_kt, fx, fy)
        err = torch.hypot(fx + back[:, 0], fy + back[:, 1])
        tol = FB_ABS + FB_REL * torch.hypot(fx, fy)
        ok &= err < tol
        real = torch.from_numpy(np.stack([frames[k] for k in ks])).to(dev).permute(0, 3, 1, 2).float()
        pix = sample(real, fx, fy)
        d = torch.tensor([abs(k - t) for k in ks], device=dev, dtype=torch.float32)[:, None, None]
        w = ok.float() * torch.exp(-d / NEAR_DECAY) / (1.0 + err)
        if SEAM > 0:             # faded, not cut: into the picture, back from its far edge, near the trip's limit
            w = w * ((qx - (M + MARGIN)) / SEAM).clamp(0, 1) * ((W - 1 - qx) / SEAM).clamp(0, 1) \
                  * (2.0 * (1.0 - err / tol)).clamp(0, 1)
        near = {k - t: (fs[j].half().cpu(), ok[j].cpu()) for j, k in enumerate(ks) if abs(k - t) <= STEADY} \
            if want_near else None
        if solo and SOLO > 0:      # one frame alone: only a near one
            used = w > 0
            w = w * ((used.sum(0) != 1) | (used & (d <= SOLO)).any(0)).float()[None]
        return (pix * w[:, None]).sum(0), w.sum(0), near

    try:
        # 1. the quick look: does any frame show the strip at all?
        looked, seen = 0, 0.0
        for t in np.unique(np.linspace(0, n - 1, min(n, LOOK_FRAMES)).astype(int)):
            check()
            ks = [int(t) + s * d for d in LOOK_OFFSETS for s in (-1, 1) if 0 <= int(t) + s * d < n] or \
                 [k for k in (int(t) - 1, int(t) + 1) if 0 <= k < n]
            _acc, wsum, _ = gather(int(t), ks)
            seen += float((wsum > 0).float().mean())
            looked += 1
        if looked and seen / looked < LOOK_LEAST:
            logger.info(f"  Copy From Other Frames: no frame shows the strip ({seen / max(looked, 1):.1%} of "
                        f"{looked} frames looked at) -- a static shot; the inpainter fills it all")
            return None

        # 2. every frame from its neighbours
        fill = np.zeros((n, H, M, 3), np.float32)
        found = np.zeros((n, H, M), bool)
        near = [None] * n
        for t in range(n):
            check()
            ks = [t + s * d for d in OFFSETS for s in (-1, 1) if 0 <= t + s * d < n]
            acc, wsum, near[t] = gather(t, ks, want_near=True, solo=True)
            fill[t] = (acc / wsum.clamp(min=1e-6)).permute(1, 2, 0).cpu().numpy()
            found[t] = (wsum > 0).cpu().numpy()
            if t % 8 == 0:
                progress(lo + (0.05 + 0.75 * t / n) * (hi - lo), f"Copy From Other Frames: frame {t + 1} of {n}")
    finally:
        flow.release()

    # 3. tidy: islands of copies dropped, small holes filled from around them
    for t in range(n):
        m = found[t].astype(np.uint8)
        cnt, lab, st, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
        for c in range(1, cnt):
            if st[c, cv2.CC_STAT_AREA] < SMALL:
                m[lab == c] = 0
        hole = (1 - m).astype(np.uint8)
        cnt, lab, st, _ = cv2.connectedComponentsWithStats(hole, connectivity=8)
        small = np.zeros_like(hole)
        for c in range(1, cnt):
            # a small hole inside the copies, not one open to the strip's edge with the picture
            if st[c, cv2.CC_STAT_AREA] < SMALL and st[c, cv2.CC_STAT_LEFT] + st[c, cv2.CC_STAT_WIDTH] < M:
                small[lab == c] = 1
        if small.any():
            img = np.concatenate([np.clip(fill[t], 0, 255).astype(np.uint8), frames[t][:, M:M + 8]], 1)
            fixed = cv2.inpaint(img, np.pad(small, ((0, 0), (0, 8))), 5, cv2.INPAINT_TELEA)
            fill[t][small > 0] = fixed[:, :M][small > 0]
            m |= small
        found[t] = m > 0
    progress(lo + 0.85 * (hi - lo), "Copy From Other Frames: steadying")

    # 4. steadying: each frame's strip averaged with its neighbours', warped along the strip's flow; a
    # pixel this frame missed takes theirs. +-1..2 frames land in their own strip, where no round trip
    # can be checked, so the strip's flow is trusted there.
    steady = np.empty((n, H, M, 3), np.uint8)
    support = np.zeros((n, H, M), np.float32)
    norm = sum(1.0 / (1 + abs(d)) for d in range(-STEADY, STEADY + 1))
    pad_w = W - M
    with torch.no_grad():
        for t in range(n):
            check()
            ft = torch.from_numpy(found[t].astype(np.float32)).to(dev)
            acc = torch.from_numpy(fill[t]).to(dev).permute(2, 0, 1) * ft
            ws = ft.clone()
            for d in range(-STEADY, STEADY + 1):
                k = t + d
                if d == 0 or not 0 <= k < n or near[t] is None or d not in near[t]:
                    continue
                wt = 1.0 / (1 + abs(d))
                fs, held = near[t][d]
                fs = fs.to(dev).float()
                src = torch.from_numpy(np.ascontiguousarray(frames[k])).to(dev).permute(2, 0, 1).float()
                src[:, :, :M] = torch.from_numpy(fill[k]).to(dev).permute(2, 0, 1)
                fk = torch.cat([torch.from_numpy(found[k].astype(np.float32)).to(dev),
                                torch.ones((H, pad_w), device=dev)], 1)
                warped = sample(src[None], fs[None, 0], fs[None, 1])[0]
                fw = sample(fk[None, None], fs[None, 0], fs[None, 1])[0, 0] * wt
                if STEADY_CHECK:         # landing in k's picture: only where the round trip held
                    fw = fw * ((xsm + fs[0] < M + MARGIN) | held.to(dev)).float()
                acc += warped * fw
                ws += fw
            support[t] = (ws / norm).clamp(0, 1).cpu().numpy()      # this frame's own copy counts 1
            steady[t] = (acc / ws.clamp(min=1e-6)).clamp(0, 255).round().byte().permute(1, 2, 0).cpu().numpy()
    if dev.type == "cuda":
        torch.cuda.empty_cache()
    found = found | (support >= FILL_FROM)
    trust = np.clip(support * 1.5, 0.0, 1.0) * found        # held by its neighbours too: trusted fully

    # 5. how much of the copy is taken over ROSE: none where no frame showed it, faded over a few px
    rose = ~found
    grow = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ROSE_GROW, ROSE_GROW))
    copy = np.empty((n, H, M), np.uint8)
    rose_mask = np.empty((n, H, M), bool)
    for t in range(n):
        grown = cv2.dilate(rose[t].astype(np.uint8), grow)
        rose_mask[t] = grown > 0
        soft = np.maximum(cv2.GaussianBlur(grown.astype(np.float32), (0, 0), 3), rose[t].astype(np.float32))
        soft = np.maximum(soft, 1.0 - np.minimum(cv2.GaussianBlur(trust[t].astype(np.float32), (0, 0), 4), trust[t]))
        copy[t] = np.clip((1.0 - soft) * 255.0 + 0.5, 0, 255).astype(np.uint8)
    share = float(found.mean())
    logger.info(f"  Copy From Other Frames: {share:.0%} of the strip copied from other frames; the inpainter fills the rest")
    progress(hi, "Copy From Other Frames: done")
    return steady, copy, rose_mask
