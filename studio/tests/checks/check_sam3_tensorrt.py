"""TensorRT for SAM 3's tracking (user, 2026-10-31): the ViT trunk, the tracker's memory attention and the text
detector's encoder, decoder and mask head as engines (core/depthedit/sam3_trt). Needs the GPU, SAM 3's weights
and Studio's TensorRT -- run it only when no job is
using the GPU:

    python studio/tests/checks/check_sam3_tensorrt.py

The engines are built into a temp folder, in this process (a build is where things went wrong: the export put
the tracker into training mode, its dropout on for every PyTorch call after). On the made-up 60-frame square
clip: a click layer, a text layer and a three-object click layer (the memory attention in groups of two),
with TensorRT off and on --
  * every trunk call and every memory-attention call with a whole memory on TensorRT;
  * the masks as close as SAM 3's own rounding changes move them (IoU; the car clip gave 0.99 mean);
  * after the build the model is in eval mode, and a PyTorch call agrees with the engine's on the same input.
"""
import os, sys, tempfile, time, shutil
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, str(Path(__file__).parent))
ENGINES = Path(tempfile.mkdtemp(prefix="sam3_trt_"))
os.environ["STEREOCRAFTER_TRT_ENGINES"] = str(ENGINES)          # before trt_unet is loaded: builds here
import _fixtures

import numpy as np
import torch

from core.depthedit import sam3_backend as S
from core.depthedit import sam3_trt as ST

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
        out[f] = np.zeros((H, W), bool) if m is None else m.copy()
    return out


def iou(a, b):
    vals = []
    for f in a:
        x, y = a[f], b.get(f, np.zeros_like(a[f]))
        u = (x | y).sum()
        vals.append(1.0 if u == 0 else (x & y).sum() / u)
    return float(np.mean(vals)), float(np.min(vals))


SQUARE = ((60 + 60) / W, 180 / H)
OTHERS = ((500 / W, 300 / H), (300 / W, 60 / H))       # two points on the noise background


def layers(eng, trt=None):
    """click, text, three objects; with trt, its call counts for each."""
    out, counts = {}, {}
    for name in ("click", "text", "three"):
        before = {k: list(v) for k, v in trt.calls.items()} if trt else None
        eng.reset()
        if name == "click":
            eng.add_points(0, 1, [SQUARE], [1])
        elif name == "text":
            eng.add_text(0, "square")
        else:
            for i, p in enumerate((SQUARE,) + OTHERS):
                eng.add_points(0, i + 1, [p], [1])
        t = time.perf_counter()
        out[name] = track(eng)
        out[name + "_s"] = time.perf_counter() - t
        if trt:
            counts[name] = {k: (v[0] - before[k][0], v[1] - before[k][1]) for k, v in trt.calls.items()}
    return out, counts


def run(tensorrt):
    S.TRACK_TENSORRT = tensorrt
    eng = S.Sam3Engine()
    try:
        eng.load()
        trt = eng.tensorrt
        if tensorrt:
            check("TensorRT on at load", trt is not None, ST.unavailable_reason())
            if trt is None:
                return None, None, None
        else:
            check("TensorRT off: none at load", trt is None)
        eng.open(str(clip), True)
        res, counts = layers(eng, trt)
        extra = None
        if tensorrt:
            model = eng._predictor.model
            enc, trunk = model.tracker.transformer.encoder, model.detector.backbone.vision_backbone.trunk
            det = model.detector
            extra = {"training": [m.training for m in (model, enc, trunk, det.transformer.encoder, det.transformer.decoder,
                                                       det.segmentation_head) + tuple(enc.layers)],
                     "engines": sorted(p.name for p in ENGINES.glob("sam3_*.engine"))}
            # a PyTorch call after the build, against the engine, on one real call's inputs
            caught = []
            real = trt._memattn

            def catch(**kw):
                caught.append(kw)
                return real(**kw)
            enc.forward = catch
            eng.add_points(0, 1, [SQUARE], [1])
            for i, _ in enumerate(eng.propagate(direction="forward", start_frame=0)):
                if i >= 10:
                    break
            enc.forward = real
            kw = next(k for k in reversed(caught) if k["prompt"].shape[0] >= 5184)
            with torch.no_grad(), torch.autocast("cuda", dtype=torch.bfloat16):
                a = trt.encoder_forward(**kw)["memory"].float()
                b = trt._memattn(**kw)["memory"].float()
            extra["pytorch_vs_engine"] = float((a - b).norm() / a.norm())
        return res, counts, extra
    finally:
        eng.unload()
        S.TRACK_TENSORRT = True


if not torch.cuda.is_available() or S.active_version() != "sam3":
    _fixtures.skip("needs a CUDA GPU and SAM 3's weights (not 3.1)")
if ST.unavailable_reason():
    _fixtures.skip("needs Studio's TensorRT: " + ST.unavailable_reason())
clip = _fixtures.path("sam3_square") / "src" / "square.mp4"     # cached; read only
try:
    base, _, _ = run(False)
    got, counts, extra = run(True)
    if got is not None:
        check("all five engines built in this process", len(extra["engines"]) == 5, str(extra["engines"]))
        check("...and the model left in eval mode (no dropout on after the export)", not any(extra["training"]),
              str(extra["training"]))
        check("...a PyTorch memory-attention call after the build agrees with the engine's",
              extra["pytorch_vs_engine"] < 0.02, f"rel {extra['pytorch_vs_engine']:.2e}")
        for name in ("click", "text", "three"):
            c = counts[name]
            frames = len(got[name])
            check(f"{name}: every trunk call on TensorRT", c["trunk"][1] == 0 and c["trunk"][0] >= frames - 1, str(c["trunk"]))
            check(f"...the memory attention on TensorRT but its first frames", c["memory attention"][0] >= frames - 3
                  and c["memory attention"][1] <= 2, str(c["memory attention"]))
            det = {k: c[k] for k in ("text encoder", "text decoder", "mask head")}
            if name == "text":
                check("...the text detector's encoder, decoder and mask head on TensorRT, every call",
                      all(v[1] == 0 and v[0] >= frames - 1 for v in det.values()), str(det))
            else:
                # a click layer's first frame runs the detector once (SAM 3's own pass over the prompted frame)
                check("...the text detector at most once on a click layer, on TensorRT",
                      all(v[1] == 0 and v[0] <= 1 for v in det.values()), str(det))
            mean, low = iou(base[name], got[name])
            check(f"...masks as close to PyTorch's as rounding moves them", mean >= 0.97 and low >= 0.85,
                  f"IoU mean {mean:.4f} min {low:.4f}")
            print(f"     {name}: PyTorch {base[name + '_s']:.1f} s, TensorRT {got[name + '_s']:.1f} s for {frames} frames")
finally:
    shutil.rmtree(ENGINES, ignore_errors=True)
print(f"FAILS {len(fails)} {fails}")
sys.exit(1 if fails else 0)
