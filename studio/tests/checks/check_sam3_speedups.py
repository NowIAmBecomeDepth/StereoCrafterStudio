"""SAM 3's tracking speed-ups give the same masks (user, 2026-10-17: "anything to help speed up tracking from
sam3?" -- "if it can detect text well then it should still have the option"). Needs the GPU and SAM 3's weights --
run it only when no job is using the GPU:

    python studio/tests/checks/check_sam3_speedups.py

core.depthedit.sam3_backend.TRACK_SPEEDUPS: a click layer's per-frame step skips the detector whose detections SAM 3
drops, and the ViT keeps its weights in bf16. A made-up 60-frame clip (a square moving across it): a click layer,
then a text layer, then the click layer again in one session, with the speed-ups off and on -- every mask the same;
the skip ran on the click layer's frames only, and no stand-in detection is left in SAM 3's buffer for a text pass.
"""
import os, sys, time
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, str(Path(__file__).parent))
import _fixtures

import numpy as np
import torch

from core.depthedit import sam3_backend as S

W, H = 640, 360
fails = []


def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok:
        fails.append(label)


def track(eng):
    out = {}
    for f, res in eng.propagate(direction="forward", start_frame=0):
        m = S.union_mask(res, H, W)
        out[f] = None if m is None else m.copy()
    return out


def is_stand_in(entry):
    """A stand-in's detection outputs: every box zero (a real detection's never are), no score above -1e4 (SAM 3's
    NMS takes 1e4 more off each detection it drops: a frame with none reads -1e4 less its own scores)."""
    return not bool(entry["pred_boxes"][0].any()) and bool((entry["pred_logits"][0] <= -1e4).all())


def same(a, b):
    return a.keys() == b.keys() and all((x is None and y is None) or (x is not None and y is not None
                                        and np.array_equal(x, y)) for x, y in ((a[f], b[f]) for f in a))


def run(speedups):
    S.TRACK_SPEEDUPS = speedups
    eng = S.Sam3Engine()
    skipped = []
    try:
        eng.load()
        model = eng._predictor.model
        det = model.detector
        if speedups:
            check("speed-ups on: the detector skip and the bf16 ViT installed",
                  hasattr(det, "_studio_skip")
                  and det.backbone.vision_backbone.trunk.blocks[0].attn.qkv.weight.dtype == torch.bfloat16)
            fg = det.forward_grounding

            def counted(**kw):
                skipped.append(det._studio_skip)
                return fg(**kw)
            det.forward_grounding = counted
        else:
            check("speed-ups off: SAM 3 as upstream builds it", not hasattr(det, "_studio_skip")
                  and det.backbone.vision_backbone.trunk.blocks[0].attn.qkv.weight.dtype == torch.float32)
        eng.open(str(clip), True)
        eng.add_points(0, 1, [((60 + 60) / W, 180 / H)], [1])
        t = time.perf_counter()
        click = track(eng)
        dt = time.perf_counter() - t
        n_click = len(skipped)
        state = eng._predictor._all_inference_states[eng._session]["state"]
        buf = state["feature_cache"].get("multigpu_buffer") or {}
        held = [f for f, e in buf.items() if is_stand_in(e)]
        stale = None
        if speedups:
            check("...a stand-in buffered ahead stays while clicks are tracked", bool(held), str(held))
            # a full detection on that frame (as a text pass would run it): the stand-in goes first
            with torch.inference_mode(), torch.autocast("cuda", dtype=torch.bfloat16):
                for f in held:
                    model.run_backbone_and_detection(
                        frame_idx=f, num_frames=state["num_frames"], input_batch=state["input_batch"],
                        geometric_prompt=state["constants"]["empty_geometric_prompt"],
                        feature_cache=state["feature_cache"], reverse=False, allow_new_detections=True)
            stale = [f for f, e in buf.items() if is_stand_in(e)]
        eng.add_text(0, "square")
        text = track(eng)
        n_text = len(skipped) - n_click
        text_skipped = sum(skipped[n_click:])
        eng.add_points(0, 1, [((60 + 60) / W, 180 / H)], [1])
        again = track(eng)
        return click, text, again, dt, (skipped[:n_click], text_skipped, n_text, stale)
    finally:
        eng.unload()
        S.TRACK_SPEEDUPS = True


if not torch.cuda.is_available() or S.active_version() != "sam3":
    _fixtures.skip("needs a CUDA GPU and SAM 3's weights (not 3.1)")
clip = _fixtures.path("sam3_square") / "src" / "square.mp4"     # cached; read only
c0, t0, a0, d0, _ = run(False)
c1, t1, a1, d1, (click_skips, text_skipped, n_text, stale) = run(True)
check("the click layer: every mask the same", same(c0, c1), f"{len(c1)} frames")
check("...the square found on its frames", sum(m is not None and m.any() for m in c1.values()) >= 55)
check("...the detector skipped on its tracked frames", sum(click_skips) >= 55, f"{sum(click_skips)} of {len(click_skips)}")
check("...a full detection after it reads no stand-in", stale == [], str(stale))
check("the text layer: every mask the same", same(t0, t1),
      f"({sum(m is not None and m.any() for m in t1.values())} frames with a detection)")
check("...its detector never skipped", text_skipped == 0 and n_text > 0, f"{text_skipped} of {n_text}")
check("the click layer after the text layer: the same masks", same(a0, a1))
check("the click layer tracked faster", d1 < d0, f"{d0:.1f}s upstream, {d1:.1f}s with the speed-ups")
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(1 if fails else 0)
