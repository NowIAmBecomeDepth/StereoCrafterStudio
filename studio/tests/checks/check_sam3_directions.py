"""SAM 3 tracks one way from a frame (user, 2026-10-05: the Depth Map Editor's
Start Here -- on over the next frames, or back over the previous ones). Needs
the GPU and SAM 3's weights -- run it only when no job is using the GPU:

    python studio/tests/checks/check_sam3_directions.py

A made-up 60-frame 640x360 clip (a square moving across it) in a temp folder,
clicked on frame 0. Both ways tracked: every frame once; forward from 30:
frames 30-59 only, in order, the same masks as the both-ways track there;
backward from 30: frames 29 down to 0 only (SAM 3's backward pass starts the
frame before -- the frame itself is the forward pass's).
"""
import os, sys, tempfile
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, str(Path(__file__).parent))
import _fixtures

import numpy as np
import torch

from core.depthedit import sam3_backend as S

FRAMES, W, H, MID = 60, 640, 360, 30
fails = []


def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok:
        fails.append(label)


def track(eng, **kw):
    out = []
    for f, res in eng.propagate(**kw):
        m = S.union_mask(res, H, W)
        out.append((f, None if m is None else m.copy()))
    return out


if not torch.cuda.is_available() or S.active_version() is None:
    _fixtures.skip("needs a CUDA GPU and SAM 3's weights")
clip = _fixtures.path("sam3_square") / "src" / "square.mp4"     # cached; read only
eng = S.Sam3Engine()
try:
    eng.load()
    eng.open(str(clip), True)
    eng.add_points(0, 1, [((60 + 60) / W, 180 / H)], [1])
    both = track(eng)
    frames = [f for f, _m in both]
    check("both ways: every frame, once", sorted(frames) == list(range(FRAMES)), f"{len(frames)} results")
    check("...the square found on every frame", all(m is not None and m.any() for _f, m in both))
    by_frame = {f: m for f, m in both}

    fwd = track(eng, direction="forward", start_frame=MID)
    check(f"forward from {MID}: frames {MID}-{FRAMES - 1} only, in order", [f for f, _m in fwd] == list(range(MID, FRAMES)),
          str([f for f, _m in fwd][:4]) + "...")
    agree = np.mean([(m == by_frame[f]).mean() for f, m in fwd if m is not None])
    check("...the same masks as the both-ways track there", agree > 0.999, f"{agree:.5f}")

    back = track(eng, direction="backward", start_frame=MID)
    check(f"backward from {MID}: the previous frames only, {MID - 1} down to 0", [f for f, _m in back] == list(range(MID - 1, -1, -1)),
          str([f for f, _m in back][:4]) + "...")
    agree = np.mean([(m == by_frame[f]).mean() for f, m in back if m is not None])
    check("...the same masks as the both-ways track there", agree > 0.999, f"{agree:.5f}")
    try:
        eng.propagate(direction="sideways").__next__()
        check("a made-up direction is refused", False)
    except ValueError:
        check("a made-up direction is refused", True)
finally:
    eng.unload()
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(1 if fails else 0)
