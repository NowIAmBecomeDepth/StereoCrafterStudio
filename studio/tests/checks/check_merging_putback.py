"""Put Back in Merging (user, 2026-10-15), real app, offscreen: an erased clip's object laid back
over both eyes at the depth the ORIGINAL clip's depth map gives it, warped as Splatting warped the
scene; Merging shows its controls when the clip on screen has one.

A clip "erased" by hand (no ROSE): the merge fixture's mA with an erase record, its original kept,
an object mask (a square), the original's depth map (the square near) and the clean clip's (another
scale, as DepthCrafter gives each clip its own range):

  * Merging finds the put-back: the Put Back section shows, its fields the clip's settings;
  * a change there (Grow) is saved beside the clip and the preview renders it;
  * the object goes where Splatting's warp puts its depth -- in the right eye shifted by the
    parallax that depth gives, in the left eye where it was; Convergence moves it;
  * a clip with no put-back shows no section.

Configs and last_jobs.json redirected; temp folders only.
"""
import os, sys, tempfile, hashlib, time, json, shutil
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, str(Path(__file__).parent))
rec_path = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec_path.read_bytes()).hexdigest() if rec_path.exists() else None)
tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
import _fixtures
import numpy as np
import torch
from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from studio import app as A
from core.depthedit import depth_io
from core.removal import erase as E, putback as P

if not torch.cuda.is_available():
    _fixtures.skip("Put Back's Depth Map warps on the GPU")

W, H, N = 320, 180, 48
fx = _fixtures.copy("merge", tmp / "mfx")
clip = fx / "orig" / "mA.mp4"
hist = Path(E.history_dir(str(clip))); hist.mkdir(parents=True, exist_ok=True)
original = hist / "mA.original.mp4"
shutil.copyfile(clip, original)
# the object: a square, near; its mask
SQ = (60, 120, 140, 200)                       # y0, y1, x0, x1
mask = np.zeros((H, W), np.uint8); mask[SQ[0]:SQ[1], SQ[2]:SQ[3]] = 255
import subprocess
p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{W}x{H}", "-r", "24",
                      "-i", "-", "-c:v", "ffv1", str(hist / "mA.object_mask.mkv")], stdin=subprocess.PIPE)
for _ in range(N):
    p.stdin.write(mask.tobytes())
p.stdin.close(); p.wait()
json.dump({"live": E._file_id(str(clip)), "original": str(original), "method": "rose"},
          open(E.record_path(str(clip)), "w", encoding="utf-8"))
# the depth maps: the clean one a ramp; the original the same ramp on another scale, the square near
info = depth_io.ClipInfo("", W, H, N, "24/1", "yuv420p10le", False)
ramp = np.linspace(0.2, 0.6, W, dtype=np.float32)[None, :].repeat(H, 0)
depth_dir = tmp / "depth"; depth_dir.mkdir()
for name, scene_of in (("mA_depth.mp4", lambda r: r), ("orig_depth.mp4", lambda r: 0.1 + 0.5 * r)):
    w = depth_io.DepthWriter(str(depth_dir / name), depth_io.ClipInfo(str(depth_dir / name), W, H, N, "24/1",
                                                                     "yuv420p10le", False))
    for _ in range(N):
        f = scene_of(ramp).copy()
        if name == "orig_depth.mp4":
            f[SQ[0]:SQ[1], SQ[2]:SQ[3]] = 0.1 + 0.5 * 0.95       # the object: 0.95 on the clean scale
        w.write(depth_io.from_unit(f, w.info if hasattr(w, "info") else info))
    w.close()
kept = P.keep_original_depth(str(clip), str(depth_dir / "orig_depth.mp4"))
s = P.load_settings(str(clip)); s.update(enabled=True, mode="Over"); P.save_settings(str(clip), s)

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
win.show()
fails = []
def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok: fails.append(label)
def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.005)
def until(cond, sec=30):
    end = time.time() + sec
    while not cond() and time.time() < end:
        pump(0.05)
    return cond()

check("Erase from Clip's depth map kept for it, the put-back set to Original Depth (the default)",
      os.path.isfile(kept) and P.load_settings(str(clip))["depth"] == "Original Depth"
      and P.load_settings(str(clip))["original_depth"] == kept, kept)
sp, mg = objs["Splatting"], objs["Merging"]
sp.settings.insert("input_depth_maps", str(depth_dir).replace("\\", "/"))
for k, v in (("inpainted_folder", fx / "inpainted"), ("original_folder", fx / "orig"), ("mask_folder", fx / "mask"),
             ("output_folder", tmp / "out")):
    mg._map.insert(k, str(v).replace("\\", "/"))
pump(0.5)
from PySide6.QtCore import QMetaObject, Q_ARG
def _find(it, name):
    for c in it.childItems():
        if c.objectName() == name:
            return c
        r = _find(c, name)
        if r is not None:
            return r
QMetaObject.invokeMethod(_find(win.contentItem(), "stage"), "show", Q_ARG("QVariant", 3), Q_ARG("QVariant", True))
pump(0.8)
pv = mg.preview
pv.load(); until(lambda: pv.property("loaded"), 60)
def section():
    def walk(it):
        for c in it.childItems():
            yield c
            yield from walk(c)
    return next((c for c in walk(win.contentItem()) if c.objectName() == "putBackSection"), None)
def on_clip(name):
    for _ in range(4):
        if (mg.property("clipName") or "").startswith(name):
            return True
        pv.step(1); pump(1.0)
    return False
check("Merging is on mA", on_clip("mA"), mg.property("clipName"))
until(lambda: (mg.property("putBack") or {}).get("available"), 10)
st = mg.property("putBack") or {}
sec = section()
check("the clip's put-back found: the Put Back section shows, its fields the clip's",
      st.get("available") and sec is not None and sec.isVisible() and st.get("depth") == "Original Depth"
      and st.get("mode") == "Over" and st.get("enabled") is True and st.get("hasDepthMap") is True, str(st))

store = mg.property("putBackStore")
store.insert("grow", 3.0)                        # as the page writes it
mg._pb_map.valueChanged.emit("grow", 3.0)
pump(0.5)
check("a change there is saved beside the clip", P.load_settings(str(clip))["grow"] == 3.0,
      str(P.load_settings(str(clip))["grow"]))
mg.setPutBack("grow", 0.0); pump(0.3)

# Original Depth: the original map's codes as they are -- the object at the original's own scale
P.close_all()
pbo = P.find(str(fx / "orig"), "mA")
dmo = pbo.depth_map_layer() if pbo is not None else None
sq_o = dmo.depth_at(10, W, H)[SQ[0] + 5:SQ[1] - 5, SQ[2] + 5:SQ[3] - 5].mean() / dmo.geo.max_raw if dmo else -1
check("Original Depth: the object at the original map's own depth (0.575), not fitted (0.95)",
      dmo is not None and dmo.scale[0] == (1.0, 0.0) and abs(sq_o - 0.575) < 0.02, f"{sq_o:.3f}")
_lo, ro = pbo.apply(torch.zeros(1, 3, H, W, device="cuda"), torch.zeros(1, 3, H, W, device="cuda"), 10)

# Depth Map: the original's depth fitted onto the clean map's scale
s = P.load_settings(str(clip)); s["depth"] = "Depth Map"; P.save_settings(str(clip), s)
# the layer itself, as Merging's render applies it: where the object goes
P.close_all()
pb = P.find(str(fx / "orig"), "mA")
check("the render finds it, the Depth Map layer built", pb is not None and pb.depth_map_layer() is not None,
      getattr(pb, "_dm_failed", ""))
z = torch.zeros(1, 3, H, W, device="cuda")
left, right = pb.apply(z.clone(), z.clone(), 10)
def cols(t):
    m = (t[0].abs().sum(0) > 0.05).any(0).nonzero().flatten()
    return (int(m.min()), int(m.max())) if len(m) else (-1, -1)
lc, rc = cols(left), cols(right)
geo = pb.depth_map_layer().geo
vals = geo.kf_base
want = (0.95 - vals["zero_disparity_anchor_val"]) * vals["max_disp"] / 1000.0 * W     # px the warp moves it
check("the left eye: the object where it was", lc == (SQ[2], SQ[3] - 1), str(lc))
check("Original Depth and Depth Map place it differently", cols(ro) != rc, f"{cols(ro)} vs {rc}")
check("the right eye: moved by the parallax its depth gives (Splatting's warp)",
      abs((rc[0] - lc[0]) - (-want)) <= 1.5 or abs((rc[0] - lc[0]) - want) <= 1.5,
      f"moved {rc[0] - lc[0]}, depth 0.95 -> {want:.1f} px (disp {vals['max_disp']}, conv {vals['zero_disparity_anchor_val']})")
s = P.load_settings(str(clip)); s["conv"] = 0.3; P.save_settings(str(clip), s)
P.close_all()
pb2 = P.find(str(fx / "orig"), "mA")
_l2, r2 = pb2.apply(z.clone(), z.clone(), 10)
check("Convergence moves it off its measured depth", cols(r2) != rc, f"{rc} -> {cols(r2)}")
s["conv"] = 0.0; s["disp"] = 200.0; P.save_settings(str(clip), s)
P.close_all()
pb3 = P.find(str(fx / "orig"), "mA")
_l3, r3 = pb3.apply(z.clone(), z.clone(), 10)
check("Disparity 200%: it moves twice as far", abs((cols(r3)[0] - lc[0]) - 2 * (rc[0] - lc[0])) <= 2,
      f"{rc[0] - lc[0]} -> {cols(r3)[0] - lc[0]}")
s["disp"] = 100.0; P.save_settings(str(clip), s)
P.close_all()

# the dials: Convergence and Disparity for Depth Map, Parallax for Parallax
def dial(name):
    return _find(win.contentItem(), name)
mg.setPutBack("depth", "Depth Map"); pump(0.4)
check("Depth Map: Convergence and Disparity show, Parallax doesn't",
      dial("putBackConv").isVisible() and dial("putBackDisp").isVisible() and not dial("putBackParallax").isVisible())
mg.setPutBack("disp", 150); pump(0.3)
check("Disparity is saved beside the clip", P.load_settings(str(clip))["disp"] == 150.0,
      str(P.load_settings(str(clip))["disp"]))
mg.setPutBack("disp", 100); pump(0.3)
mg.setPutBack("depth", "Parallax"); pump(0.4)
check("Parallax: Disparity and Convergence hidden, Parallax shows",
      not dial("putBackDisp").isVisible() and not dial("putBackConv").isVisible() and dial("putBackParallax").isVisible())
mg.setPutBack("depth", "Depth Map"); pump(0.3)
check("Depth Map found: no warning", not dial("putBackProblem").isVisible(), str((mg.property("putBack") or {}).get("depthMapProblem")))
saved_geo = P.GEOMETRY
P.GEOMETRY = lambda core: None                 # Splatting's Depth Maps no longer has the clean map
mg.setPutBack("grow", 1.0); pump(0.4)
check("Depth Map not found: the section says why, and that the dials do nothing",
      dial("putBackProblem").isVisible() and "Convergence and Disparity do nothing" in dial("putBackProblem").property("text"),
      str((mg.property("putBack") or {}).get("depthMapProblem")))
P.GEOMETRY = saved_geo
mg.setPutBack("grow", 0.0); pump(0.4)

# Original Eyes (2026-10-16): the merge's row; its dials hidden; no merge -> it says so
mg.setPutBack("depth", "Original Eyes"); mg.setPutBack("eyes", str(tmp).replace("\\", "/")); pump(0.5)
st = mg.property("putBack") or {}
check("Original Eyes: the merge's row shows, Convergence/Disparity/Parallax hidden",
      dial("putBackEyesRow").isVisible() and not dial("putBackConv").isVisible()
      and not dial("putBackDisp").isVisible() and not dial("putBackParallax").isVisible())
check("Original Eyes with no merge there: it says so", "no merge of mA found" in (st.get("depthMapProblem") or ""),
      str(st.get("depthMapProblem")))
mg.setPutBack("depth", "Original Depth"); pump(0.3)

# Add on the glass (2026-10-17): Glass Plane with Convergence and Disparity, no Parallax
mg.setPutBack("mode", "Add"); mg.setPutBack("depth", "Glass Plane"); pump(0.5)
st = mg.property("putBack") or {}
check("Add: its depths are Glass Plane and Parallax", st.get("depths") == ["Glass Plane", "Parallax"], str(st.get("depths")))
check("Add on Glass Plane: Convergence and Disparity show, Parallax hidden",
      dial("putBackDepth").isVisible() and dial("putBackConv").isVisible() and dial("putBackDisp").isVisible()
      and not dial("putBackParallax").isVisible())
mg.setPutBack("depth", "Parallax"); pump(0.4)
check("Add on Parallax: Parallax shows, the depth dials hidden",
      dial("putBackParallax").isVisible() and not dial("putBackConv").isVisible())
mg.setPutBack("mode", "Over"); mg.setPutBack("depth", "Original Depth"); pump(0.4)

# a clip without one: no section
on_clip("mB"); until(lambda: not (mg.property("putBack") or {}).get("available"), 10)
check("a clip with no put-back: no Put Back section", not (mg.property("putBack") or {}).get("available")
      and not section().isVisible())

P.close_all()
studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec_path.read_bytes()).hexdigest() if rec_path.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
