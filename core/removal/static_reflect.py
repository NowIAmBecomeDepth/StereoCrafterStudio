"""static_reflect.py -- reflections that hold still on glass, taken off a locked-off
shot (user, 2026-10-06: a room's lamps on a TV screen).

When the camera and the lights hold still and what is seen through the glass
changes, a reflection is what is a small bright shape on the glass in EVERY
frame. Per frame, a white top-hat (the frame minus its morphological opening:
the bright shapes narrower than SIZE) inside the Erase layers' mask; the
per-pixel minimum over the clip keeps what was bright throughout -- the
reflections -- and drops the picture, which moves. That layer comes off every
frame; the clip is written in the original's place exactly as Erase from Clip
does (erase.place), so put-back Add lays it back at a depth of its own.

No model, no GPU: measured on that TV it beat both reflection-removal
models tried (Dereflection Any Image, DiffRS), which misread a faint
reflection on a changing picture.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import threading
from typing import Sequence

import cv2
import numpy as np

from . import erase as E

logger = logging.getLogger(__name__)

#: The widest reflection shape kept, in pixels of a 1920-wide clip.
SIZE = 31


def default_size(width: int) -> int:
    s = int(round(SIZE * width / 1920.0))
    return max(3, s | 1)


def erase_static(clip: str, layers: Sequence[E.EraseLayer], progress: E.Progress, stop: threading.Event,
                 size: int = 0, feather: float = 4.0) -> str:
    """Take the still reflections inside `layers`' masks off `clip`, in place
    (the original kept in edit_history/). Returns where the original is."""
    src = E.source_for(clip)
    info = E._probe(src)
    W, H, n = info["w"], info["h"], info["frames"]
    size = int(size) if size and int(size) > 2 else default_size(W)
    size |= 1

    # 1. where to look: every frame's mask, together
    progress(0.0, "Finding the glass")
    region = np.zeros((H, W), bool)
    for i in range(n):
        if stop.is_set():
            raise E.Stopped()
        m = E.frame_mask(layers, i, (W, H))
        if m is not None:
            region |= m
    if not region.any():
        raise RuntimeError("No Erase layer has a mask on any frame. Track the glass through the clip first.")
    ys, xs = np.nonzero(region)
    pad = size
    y0, y1 = max(0, ys.min() - pad), min(H, ys.max() + 1 + pad)
    x0, x1 = max(0, xs.min() - pad), min(W, xs.max() + 1 + pad)

    # 2. the bright shapes on the glass in every frame
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
    still = None
    for i, f in enumerate(E._reader(src, W, H, "rgb48le")):
        if stop.is_set():
            raise E.Stopped()
        crop = f[y0:y1, x0:x1].astype(np.float32) / 65535.0
        th = np.stack([cv2.morphologyEx(crop[..., c], cv2.MORPH_TOPHAT, kernel) for c in range(3)], -1)
        still = th if still is None else np.minimum(still, th)
        if i % 8 == 0:
            progress(0.05 + 0.45 * i / max(n, 1), f"Looking for still reflections ({i + 1} of {n})")
    alpha = cv2.GaussianBlur(region.astype(np.float32), (0, 0), max(0.5, float(feather)))
    layer = np.zeros((H, W, 3), np.float32)
    layer[y0:y1, x0:x1] = still * alpha[y0:y1, x0:x1, None]
    layer16 = layer * 65535.0
    logger.info(f"Still reflections: {float((layer.max(-1) > 0.02).mean()):.2%} of the frame, "
                f"up to {float(layer.max()):.2f}, size {size} px.")

    # 3. off every frame, written in the original's place
    return _take_off(clip, src, info, alpha, lambda i, f: layer16, progress, stop,
                     {"method": "static", "frames": n, "size": size, "feather": float(feather)},
                     "Still reflections taken off")


# ---- Dust (user, 2026-10-17: the dust on chap4_00116923's windshield "stands out in 3D as wrong") --------------
# The camera rides with the glass (a car's interior), so dust on it holds still on screen while the world moves
# behind it -- but it is faint and grainy, and its brightness changes as the light moves (frame to frame 0.6x to
# 1.8x the clip's median), so "bright in EVERY frame" (Static) finds nothing. The clip's MEDIAN frame keeps the
# still specks and loses the moving picture and the grain: its fine bright detail says WHERE the dust is; each
# frame's own fine detail there is what comes off that frame, so all of it moves (half of it left behind sat at
# the scene's depth). On the GPU.

#: the widest speck kept, px of a 1920-wide clip
DUST_SIZE = 9
#: a speck: the median frame's fine detail above this (0..1 of the 16-bit range; ~4 levels of 10-bit)
DUST_LEVEL = 4.0 / 1023.0
#: the median is taken over at most this many frames (spread across the clip)
DUST_FRAMES = 240


def default_dust_size(width: int) -> int:
    return max(3, int(round(DUST_SIZE * width / 1920.0)) | 1)


def _opening(x, k: int):
    """Grey opening (N, C, H, W) with a k x k square, on the GPU: what's left without the bright shapes narrower
    than k."""
    import torch.nn.functional as F
    r = k // 2
    eroded = -F.max_pool2d(F.pad(-x, (r, r, r, r), mode="replicate"), k, 1)
    return F.max_pool2d(F.pad(eroded, (r, r, r, r), mode="replicate"), k, 1)


def erase_dust(clip: str, layers: Sequence[E.EraseLayer], progress: E.Progress, stop: threading.Event,
               size: int = 0, feather: float = 4.0) -> str:
    """Take the dust inside `layers`' masks off `clip`, in place (the original kept in edit_history/), for
    Put Back to lay on the glass. Returns where the original is."""
    import torch
    import torch.nn.functional as F
    src = E.source_for(clip)
    info = E._probe(src)
    W, H, n = info["w"], info["h"], info["frames"]
    size = int(size) if size and int(size) > 2 else default_dust_size(W)
    size |= 1
    dev = "cuda" if torch.cuda.is_available() else "cpu"

    progress(0.0, "Finding the glass")
    region = np.zeros((H, W), bool)
    for i in range(n):
        if stop.is_set():
            raise E.Stopped()
        m = E.frame_mask(layers, i, (W, H))
        if m is not None:
            region |= m
    if not region.any():
        raise RuntimeError("No Erase layer has a mask on any frame. Draw or track the glass first.")
    ys, xs = np.nonzero(region)
    y0, y1 = max(0, ys.min() - size), min(H, ys.max() + 1 + size)
    x0, x1 = max(0, xs.min() - size), min(W, xs.max() + 1 + size)

    # 1. the median frame (luma, the glass's box) -> where the dust is
    step = max(1, int(np.ceil(n / DUST_FRAMES)))
    stack = []
    for i, f in enumerate(E._reader(src, W, H, "rgb48le")):
        if stop.is_set():
            raise E.Stopped()
        if i % step == 0:
            stack.append(torch.from_numpy(f[y0:y1, x0:x1].astype(np.float32).mean(-1) / 65535.0).to(dev).half())
        if i % 8 == 0:
            progress(0.02 + 0.28 * i / max(n, 1), f"Looking for dust ({i + 1} of {n})")
    with torch.no_grad():
        plate = torch.stack(stack).float().median(0).values[None, None]
        del stack
        detail = plate - _opening(plate, size)
        crop_region = torch.from_numpy(region[y0:y1, x0:x1]).to(dev)[None, None].float()
        speck = ((detail > DUST_LEVEL).float() * crop_region)
        speck = F.max_pool2d(speck, 3, 1, 1)                                   # its edge too
        k = torch.tensor([0.25, 0.5, 0.25], device=dev)
        speck = F.conv2d(F.conv2d(F.pad(speck, (1, 1, 0, 0), mode="replicate"), k.view(1, 1, 1, 3)),
                         k.view(1, 1, 3, 1), padding=(1, 0))                   # softened a pixel
        support = speck.clamp(0, 1)
    share = float((support > 0.5).float().mean()) * (y1 - y0) * (x1 - x0) / float(W * H)
    logger.info(f"Dust: {share:.2%} of the frame, size {size} px.")
    if share <= 0:
        raise RuntimeError("No dust found on the glass: nothing holds still there.")

    alpha = cv2.GaussianBlur(region.astype(np.float32), (0, 0), max(0.5, float(feather)))
    a_crop = torch.from_numpy(alpha[y0:y1, x0:x1]).to(dev)[None, None]

    # 2. each frame's own dust there, off -- on the GPU
    def layer_for(i, f):
        with torch.no_grad():
            t = torch.from_numpy(f[y0:y1, x0:x1].astype(np.float32) / 65535.0).to(dev).permute(2, 0, 1)[None]
            dust = (t - _opening(t, size)).clamp_min(0) * support * a_crop
            out = np.zeros((H, W, 3), np.float32)
            out[y0:y1, x0:x1] = dust[0].permute(1, 2, 0).cpu().numpy() * 65535.0
        if i % 8 == 0:
            progress(0.3 + 0.2 * i / max(n, 1), f"Taking the dust off ({i + 1} of {n})")
        return out

    try:
        return _take_off(clip, src, info, alpha, layer_for, progress, stop,
                         {"method": "dust", "frames": n, "size": size, "feather": float(feather)}, "Dust taken off")
    finally:
        del plate, detail, support, a_crop
        if dev == "cuda":
            torch.cuda.empty_cache()


def _take_off(clip: str, src: str, info, alpha: np.ndarray, layer_for, progress: E.Progress, stop: threading.Event,
              record: dict, done_text: str) -> str:
    """Every frame minus layer_for(i, frame) (rgb48 units, H x W x 3), written in the original's place as Erase
    from Clip does (erase.place): `alpha` (0..1) is the repaint mask and the object mask Put Back reads."""
    W, H, n = info["w"], info["h"], info["frames"]
    E.sweep_stale_temp(clip)
    tmp = tempfile.mkdtemp(prefix=".erase_", dir=os.path.dirname(os.path.abspath(clip)))
    try:
        stem, ext = os.path.splitext(os.path.basename(clip))
        out_tmp = os.path.join(tmp, f"{stem}.erased{ext or '.mp4'}")
        mask_tmp = os.path.join(tmp, f"{stem}.erase_mask.mkv")
        obj_tmp = os.path.join(tmp, f"{stem}.object_mask.mkv")
        writer = E._writer(out_tmp, src, info)
        mwriter = E._mask_writer(mask_tmp, W, H, info["rate"])
        owriter = E._mask_writer(obj_tmp, W, H, info["rate"])
        alpha8 = (np.clip(alpha, 0, 1) * 255 + 0.5).astype(np.uint8)
        written = 0
        try:
            for i, f in enumerate(E._reader(src, W, H, "rgb48le")):
                if stop.is_set():
                    raise E.Stopped()
                out = np.clip(f.astype(np.float32) - layer_for(i, f), 0, 65535).astype(np.uint16)
                try:
                    writer.stdin.write(np.ascontiguousarray(out).tobytes())
                    mwriter.stdin.write(alpha8.tobytes())
                    owriter.stdin.write(alpha8.tobytes())
                except OSError:
                    break                    # an encoder exited: its reason is read below (bug sweep A9)
                written += 1
                if written % 8 == 0:
                    progress(0.5 + 0.5 * written / max(n, 1), f"Writing the clip ({written} of {n})")
        finally:
            for p in (writer, mwriter, owriter):
                try:
                    p.stdin.close()
                except OSError:
                    pass
            for p in (writer, mwriter, owriter):
                p.wait()
        failed = E.writers_failed((writer, mwriter, owriter), written, n)
        if failed:
            raise RuntimeError(failed)
        original = E.place(clip, src, out_tmp, mask_tmp, obj_tmp, record)
        progress(1.0, done_text)
        return original
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
