"""The Depth Map Editor's one-sided edge moves (edit_ops.edge_shift, user
2026-09-28: a beam warped by Splatting's Y dilation needs its TOP pulled in,
not its bottom, and nothing else in the shot touched). Pure numpy on a
synthetic depth frame: a near horizontal beam over a far wall, a near post
beside it outside the mask."""
import sys
from pathlib import Path
import numpy as np
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO))
from core.depthedit import edit_ops as E

fails = []
def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok: fails.append(label)

H, W = 120, 160
FAR, NEAR, POST = 0.2, 0.8, 0.7
v = np.full((H, W), FAR, np.float32)
v[50:70, 20:140] = NEAR          # the beam: rows 50..69
v[10:110, 145:155] = POST        # a near post, outside the beam's mask
mask = np.zeros((H, W), bool); mask[50:70, 20:140] = True
rows = lambda a, c=80: [round(float(a[y, c]), 2) for y in range(44, 76)]

def edit(**kw):
    s = E.LayerSettings(edge_snap=0, feather=0, **kw)
    return E.apply_layer(v, mask, s)

def beam_rows(a, c=80):
    return [y for y in range(H) if a[y, c] > 0.5]

out = edit(edge_top=-10)
check("Top edge -10: the beam's top pulled in by 10 rows, its bottom where it was",
      beam_rows(out) == list(range(60, 70)), f"{beam_rows(out)[:1]}..{beam_rows(out)[-1:]}")
check("...the rows it gave up take the depth behind (far)", abs(float(out[55, 80]) - FAR) < 1e-6, str(out[55, 80]))
check("...nothing outside the mask moved (the post, the wall)", np.array_equal(out[:, 140:], v[:, 140:])
      and np.array_equal(out[:50], v[:50]) and np.array_equal(out[70:], v[70:]))

out = edit(edge_bottom=-6)
check("Bottom edge -6: the bottom pulled in, the top where it was", beam_rows(out) == list(range(50, 64)),
      f"{beam_rows(out)[:1]}..{beam_rows(out)[-1:]}")

out = edit(edge_top=8)
check("Top edge +8: the top pushed out 8 rows, the bottom where it was", beam_rows(out) == list(range(42, 70)),
      f"{beam_rows(out)[:1]}..{beam_rows(out)[-1:]}")
check("...only above the beam, within its columns", np.array_equal(out[:, :20], v[:, :20])
      and np.array_equal(out[:, 140:], v[:, 140:]))

out = edit()
check("both 0: the frame untouched", np.array_equal(out, v))

s = E.layer_settings_from_dict({"brightness": 0.1})
check("an older project (no edge keys) loads with both at 0", s.edge_top == 0 and s.edge_bottom == 0)
check("...and a new one keeps them", E.layer_settings_from_dict(E.settings_to_dict(E.LayerSettings(edge_top=-10))).edge_top == -10)

print("FAILS", len(fails), fails, flush=True)
sys.exit(1 if fails else 0)
