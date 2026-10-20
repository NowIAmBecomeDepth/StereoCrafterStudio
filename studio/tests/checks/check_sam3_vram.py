"""SAM 3 tracking's VRAM, before and after (user, 2026-10-05: a 750-frame
scene ate the VRAM and a second object slowed it right down). Needs the GPU
and SAM 3's weights -- run it only when no job is using the GPU:

    python studio/tests/checks/check_sam3_vram.py [FRAMES]

A made-up 1920x1038 clip (FRAMES, default 300) with two squares moving across
it, in a temp folder. Each object is clicked on frame 0 and tracked through
the clip twice: as SAM 3 keeps things by itself, and with Studio's savings
(the full-size mask cache packed in system memory, the tracker's per-frame
memory in system memory, the frames kept in system memory instead of the
whole clip copied to the GPU -- core/depthedit/sam3_backend). It reports the peak
VRAM and the time of each, and checks the masks agree.
"""
import os, sys, tempfile, time
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, str(Path(__file__).parent))
import _fixtures

import numpy as np
import torch

from core.depthedit import sam3_backend as S

FRAMES = int(sys.argv[1]) if len(sys.argv) > 1 else 300
W, H = 1920, 1038
fails = []


def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok:
        fails.append(label)


def make_clip(path: Path) -> None:
    _fixtures._square_clip(path, FRAMES, W, H, two=True)


def run(path: Path, savings: bool):
    eng = S.Sam3Engine()
    import contextlib
    real_pack, real_host = S._pack_mask_cache, S._frames_stay_on_host
    if not savings:
        S._pack_mask_cache = lambda state: None
        S._frames_stay_on_host = contextlib.nullcontext
    torch.cuda.empty_cache(); torch.cuda.reset_peak_memory_stats()
    eng.load()
    base = torch.cuda.memory_allocated()
    held = {}
    try:
        if not savings:
            # SAM 3's own way: frames offloaded (as the user has it), nothing else
            orig = eng._predictor.handle_request
            def no_state_offload(req, orig=orig):
                if req.get("type") == "start_session":
                    req = dict(req, offload_state_to_cpu=False)
                return orig(req)
            eng._predictor.handle_request = no_state_offload
        eng.open(str(path), True)
        eng.add_points(0, 1, [((200 + 120) / W, 430 / H)], [1])
        eng.add_points(0, 2, [((1700 - 100) / W, 760 / H)], [1])
        t0 = time.perf_counter()
        masks = {}
        for f, res in eng.propagate():
            m = S.union_mask(res, H // 4, W // 4)
            masks[f] = None if m is None else m.copy()
        dt = time.perf_counter() - t0
        peak = torch.cuda.max_memory_allocated() - base
        held["after"] = torch.cuda.memory_allocated() - base     # what stays while the session is open
    finally:
        S._pack_mask_cache, S._frames_stay_on_host = real_pack, real_host
        eng.unload()
    return peak, dt, masks, held["after"]


if not torch.cuda.is_available() or S.active_version() is None:
    _fixtures.skip("needs a CUDA GPU and SAM 3's weights")
if FRAMES == 300:                       # the default: cached, read only
    clip = _fixtures.path("sam3_two") / "src" / "two_objects.mp4"
else:                                   # another length: built here, removed at exit
    tmp = Path(tempfile.mkdtemp())
    clip = tmp / "two_objects.mp4"
    make_clip(clip)
print(f"{FRAMES} frames at {W}x{H}, two objects", flush=True)
peak0, t0, m0, h0 = run(clip, savings=False)
print(f"   SAM 3 as it was:  peak {peak0 / 2**30:.2f} GB above the model, {h0 / 2**30:.2f} GB held after, {t0:.1f} s", flush=True)
peak1, t1, m1, h1 = run(clip, savings=True)
print(f"   with the savings: peak {peak1 / 2**30:.2f} GB above the model, {h1 / 2**30:.2f} GB held after, {t1:.1f} s", flush=True)
check("the savings use less VRAM", peak1 < peak0, f"{peak0 / 2**30:.2f} -> {peak1 / 2**30:.2f} GB")
check("...and hold far less while the session is open", h1 < h0 * 0.5, f"{h0 / 2**30:.2f} -> {h1 / 2**30:.2f} GB")
check("...not much slower (within 25%)", t1 <= t0 * 1.25, f"{t0:.1f} -> {t1:.1f} s")
same = [f for f in m0 if m0[f] is not None and m1.get(f) is not None]
agree = np.mean([(m0[f] == m1[f]).mean() for f in same]) if same else 0
# bit-identical: the frames kept in system memory reach the detector in upstream's own layout
check("...and the masks are the same", len(same) >= FRAMES * 0.9 and agree == 1.0, f"{len(same)} frames, {agree:.5f} agree")
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(1 if fails else 0)
