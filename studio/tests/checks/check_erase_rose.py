"""Erase from Clip end to end (user, 2026-10-05): ROSE for real, on the GPU.

A synthetic clip (testsrc, 640x268) with a white disc moving across it; an
Erase layer whose masks follow the disc on frames 10-29 only. ROSE runs at a
few steps (quality isn't the point here, the plumbing is). Then:

  * the clip is replaced in place, same frame count and size, the original
    kept in edit_history/ with the erase mask and the record;
  * outside the mask every frame is still the original (re-encoded, so
    near, not equal); frames with nothing to erase are untouched the same way;
  * inside the mask the disc is gone (the area no longer looks like the disc);
  * erasing again starts from the kept original, not from the erased clip.

Needs ROSE set up (core.removal.rose_env) and a free GPU (~5 GB).
"""
import os, sys, tempfile, hashlib, time, json, subprocess, threading
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, str(Path(__file__).parent))
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
import numpy as np
import cv2
import _fixtures
from core.depthedit import edit_ops
from core.depthedit.project import MaskStore
from core.removal import erase as E, rose_env

fails = []


def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok:
        fails.append(label)


if not rose_env.ready():   # can't run here: a SKIP, as the SAM 3 checks are (bug sweep A12)
    _fixtures.skip("ROSE is not set up: " + ", ".join(rose_env.missing()))

tmp = Path(tempfile.mkdtemp())
W, H, N = 640, 268, 40
clip = tmp / "shot.mp4"
# testsrc with a white disc moving left to right
disc = "geq=lum='if(lt(hypot(X-(80+12*N),Y-134),30),235,lum(X,Y))':cb='if(lt(hypot(X-(80+12*N),Y-134),30),128,cb(X,Y))':cr='if(lt(hypot(X-(80+12*N),Y-134),30),128,cr(X,Y))'"
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={W}x{H}:rate=24:duration={N / 24}",
                "-vf", f"format=yuv420p,{disc}", "-frames:v", str(N), "-c:v", "libx264", "-crf", "12", str(clip)], check=True)
orig = [f for f in E._reader(str(clip), W, H, "rgb24")]
check("the synthetic clip", len(orig) == N, str(len(orig)))

# the Erase layer: masks at a "depth map" size of 320x134, frames 10-29
lo = MaskStore(134, 320)
for i in range(10, 30):
    m = np.zeros((134, 320), np.uint8)
    cv2.circle(m, (int((80 + 12 * i) / 2), 67), 16, 1, -1)
    lo.set(i, m.astype(bool))
layer = E.EraseLayer(edit_ops.LayerSettings(mode="Erase", edge_snap=0), None, lo, None)
msgs = []
t0 = time.time()
original = E.erase(str(clip), [layer], lambda f, t: msgs.append((round(f, 3), t)), threading.Event(), steps=4)
print(f"  erase took {time.time() - t0:.0f}s; last messages: {msgs[-3:]}")

hist = tmp / "edit_history"
check("the original is kept in edit_history", Path(original) == hist / "shot.original.mp4" and Path(original).is_file(), original)
check("...with the erase mask and the record", (hist / "shot.erase_mask.mkv").is_file() and (hist / "shot.mp4.erase.json").is_file())
out = [f for f in E._reader(str(clip), W, H, "rgb24")]
check("the clip in place: same frames and size", len(out) == N and out[0].shape == (H, W, 3), f"{len(out)} {out[0].shape}")
kept = [f for f in E._reader(original, W, H, "rgb24")]
check("the kept original is the original", all(np.abs(a.astype(int) - b.astype(int)).mean() < 0.5 for a, b in zip(kept, orig)))


def diff(a, b, m):
    return float(np.abs(a.astype(np.float32) - b.astype(np.float32))[m].mean()) if m.any() else 0.0


untouched = [diff(out[i], orig[i], np.ones((H, W), bool)) for i in list(range(0, 10)) + list(range(30, N))]
check("frames with nothing to erase: as before (re-encoded)", max(untouched) < 2.5, f"max mean diff {max(untouched):.2f}")
far = np.ones((H, W), bool)
gone, outside = [], []
for i in range(10, 30):
    c = np.zeros((H, W), np.uint8)
    cv2.circle(c, (80 + 12 * i, 134), 28, 1, -1)
    ring = cv2.dilate(c, np.ones((60, 60), np.uint8)) == 0
    outside.append(diff(out[i], orig[i], ring))
    # the disc was flat white (235): after, the area isn't
    gone.append(float(out[i][c > 0].astype(np.float32).mean()))
check("outside the mask: as before", max(outside) < 2.5, f"max mean diff {max(outside):.2f}")
check("inside: the white disc is gone", max(gone) < 200, f"brightest disc area now {max(gone):.0f} (was ~235)")
check("erasing again starts from the kept original", E.source_for(str(clip)) == original, E.source_for(str(clip)))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
