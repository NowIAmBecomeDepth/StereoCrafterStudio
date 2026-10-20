"""The erased object put back in 3D (user, 2026-10-06; core.removal.putback),
real app, offscreen, CPU -- no ROSE, no SAM: an erase is stood in for.

Over the synthetic ``merge`` fixture: clip mA is made an erased clip (its
erase record, the "original" with a white box the erased clip doesn't have,
the repaint and object masks in edit_history) with put-back Over at +10%
parallax. Then:

  * putback.find: nothing while off; on, the layer;
  * the layer by itself: the left eye gets the box where it was, the right eye
    shifted 32 px left (10% of 320); Add adds original - erased; flipped space;
  * Merging's real batch (Full SBS): mA's left eye has the box, its right eye
    has it 32 px further left; mB (no put-back) is untouched there;
  * the preview's Blended Image (the right eye) shows it shifted too;
  * close_all() lets go of the clip (bug sweep #7: Add reads the erased clip,
    and its readers held it all session), and a Resume batch with mA on Add
    moves it into finished/;
  * parallax_for_depth is Splatting's disparity.

Configs and last_jobs.json redirected.
"""
import os, sys, tempfile, hashlib, time, json, subprocess
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, str(Path(__file__).parent))
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
import _fixtures
root = _fixtures.copy("merge", tmp / "fx")
import numpy as np
import torch
from core.removal import erase as E, putback as P

fails = []


def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok:
        fails.append(label)


# ---- make mA an erased clip ------------------------------------------------------------
W, H = 320, 180
clip = root / "orig" / "mA.mp4"
hist = root / "orig" / "edit_history"; hist.mkdir()
M = "size=320x180:rate=24:duration=2"
box = "drawbox=x=100:y=60:w=40:h=40:color=white:t=fill"
ff = lambda *a: subprocess.run(["ffmpeg", "-v", "error", "-y", *a], check=True)  # noqa: E731
ff("-f", "lavfi", "-i", f"testsrc2={M},{box}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "1", str(hist / "mA.original.mp4"))
ff("-f", "lavfi", "-i", f"color=black:{M},format=gray,drawbox=x=94:y=54:w=52:h=52:color=white:t=fill",
   "-c:v", "ffv1", str(hist / "mA.erase_mask.mkv"))
ff("-f", "lavfi", "-i", f"color=black:{M},format=gray,{box}", "-c:v", "ffv1", str(hist / "mA.object_mask.mkv"))
(hist / "mA.mp4.erase.json").write_text(json.dumps({"live": E._file_id(str(clip)), "original": str(hist / "mA.original.mp4")}))

check("off: no put-back", P.find(str(root / "orig"), "mA") is None)
P.save_settings(str(clip), {"enabled": True, "mode": "Over", "parallax": 10.0, "opacity": 1.0})
pb = P.find(str(root / "orig"), "mA")
check("on: the layer", pb is not None and pb.settings["mode"] == "Over" and pb.settings["parallax"] == 10.0)
check("a clip with no erase: none", P.find(str(root / "orig"), "mB") is None)

# ---- the layer by itself ------------------------------------------------------------------
grey = lambda: torch.full((2, 3, H, W), 0.25)  # noqa: E731
l, r = pb.apply(grey(), grey(), 10)
inside = lambda t, x0, x1: float(t[0, :, 65:95, x0:x1].mean())  # noqa: E731
check("Over: the left eye gets the box where it was", inside(l, 104, 136) > 0.85 and inside(l, 60, 90) < 0.3,
      f"{inside(l, 104, 136):.2f} / {inside(l, 60, 90):.2f}")
check("...the right eye 32 px left (10% of 320)", inside(r, 72, 104) > 0.85 and inside(r, 112, 136) < 0.3,
      f"{inside(r, 72, 104):.2f} / {inside(r, 112, 136):.2f}")
lf, rf = pb.apply(grey(), grey(), 10, flipped=True)
check("flipped space: the box mirrored, the shift the same way", inside(lf, 184, 216) > 0.85 and inside(rf, 152, 184) > 0.85,
      f"{inside(lf, 184, 216):.2f} / {inside(rf, 152, 184):.2f}")
P.save_settings(str(clip), {"enabled": True, "mode": "Add", "parallax": 0.0, "opacity": 1.0})
pb2 = P.find(str(root / "orig"), "mA")
erased = torch.from_numpy(pb2._read(str(clip), [10], W, H)).permute(0, 3, 1, 2).float() / 255
la, ra = pb2.apply(erased.clone(), erased.clone(), 10)
orig = torch.from_numpy(pb2._read(pb2.original, [10], W, H)).permute(0, 3, 1, 2).float() / 255
check("Add at 0: both eyes become the original (erased + what was taken)",
      float((la - orig).abs().mean()) < 0.01 and float((ra - orig).abs().mean()) < 0.01,
      f"{float((la - orig).abs().mean()):.4f}")
check("parallax_for_depth is Splatting's disparity", abs(P.parallax_for_depth(0.9, 0.5, 20) - 0.8) < 1e-9)
P.save_settings(str(clip), {"enabled": True, "mode": "Over", "parallax": 10.0, "opacity": 1.0})

# ---- close_all lets go of the files (bug sweep #7) ----------------------------------------
P.save_settings(str(clip), {"enabled": True, "mode": "Add", "parallax": 10.0, "opacity": 1.0})
_pb3 = P.find(str(root / "orig"), "mA"); _pb3.apply(grey(), grey(), 0)   # Add opens the clip itself
_moved = clip.with_name("mA_moved.mp4")
try:
    os.replace(clip, _moved); os.replace(_moved, clip); _held = False
except OSError:
    _held = True
check("(sanity) an open put-back holds the clip on Windows", _held or os.name != "nt")
getattr(P, "close_all", lambda: None)()   # (absent before the fix: the batch below then fails)
try:
    os.replace(clip, _moved); os.replace(_moved, clip); _free = True
except OSError:
    _free = False
check("close_all: the clip can be moved", _free)
P.save_settings(str(clip), {"enabled": True, "mode": "Over", "parallax": 10.0, "opacity": 1.0})

# ---- Merging's real batch, and its preview --------------------------------------------------
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from studio import app as A
qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
objs["Shell"].setProperty("uiScale", "1.0")
win.show()


def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.005)


def until(cond, sec=30):
    end = time.time() + sec
    while not cond() and time.time() < end:
        pump(0.05)
    return cond()


def walk(it, out):
    for c in it.childItems():
        out.append(c); walk(c, out)
    return out


pump(0.8)
mg = objs["Merging"]; st = mg.property("settings"); pv = mg.property("previewer")
stage = [c for c in walk(win.contentItem(), []) if c.objectName() == "stage"][0]
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 3), Q_ARG("QVariant", True)); pump(1.2)
out = tmp / "merged"
for k, v in {"inpainted_folder": str(root / "inpainted"), "original_folder": str(root / "orig"),
             "mask_folder": str(root / "mask"), "output_folder": str(out), "encoding_encoder": "Force CPU",
             "codec": "H.264", "use_gpu": False, "preview_source": "Blended Image", "preview_size": "100%",
             "grain_enable": False, "grain_automatch": False, "resume": False, "output_format": "Full SBS (Left-Right)",
             "split_lr": False, "fill_brightness": 0.0, "add_borders": False}.items():
    st.setProperty(k, v)
pump(0.5); pv.load()

# the preview first: mA's Blended Image is its right eye
check("the preview loads", until(lambda: pv.property("loaded") and not pv.property("loading"), 60))
img = None
if until(lambda: pv.provider._images.get("frame") is not None, 30):
    pump(1.0)
    qi = pv.provider._images.get("frame")
    img = np.array([[qi.pixelColor(x, y).lightness() for x in range(qi.width())] for y in range(qi.height())], np.float32)
if img is not None and img.shape[1] >= W:
    sx = img.shape[1] / W
    check("the preview's right eye has the box 32 px left", img[70:90, int(76 * sx):int(100 * sx)].mean() > 200,
          f"{img[70:90, int(76 * sx):int(100 * sx)].mean():.0f} (preview {img.shape[1]}x{img.shape[0]})")
else:
    check("the preview's right eye has the box 32 px left", False, f"no preview frame ({None if img is None else img.shape})")

mg.start(); pump(0.5)
merged = lambda: [p for p in out.glob("*") if p.suffix in (".mp4", ".mov", ".mkv")] if out.exists() else []  # noqa: E731
check("the batch merges", until(lambda: not mg.property("busy") and len(merged()) >= 2, 300),
      str(list(out.iterdir()) if out.exists() else "-"))


def frame(path, n=10):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vf", f"select=eq(n\\,{n})", "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(H, -1).astype(np.float32)


a = next((p for p in merged() if p.name.startswith("mA_")), None)
b = next((p for p in merged() if p.name.startswith("mB_")), None)
if a is not None and b is not None:
    fa, fb = frame(a), frame(b)
    check("mA's left eye has the box", fa[70:90, 105:135].mean() > 200, f"{fa[70:90, 105:135].mean():.0f}")
    check("...its right eye has it 32 px left", fa[70:90, W + 76:W + 100].mean() > 200 and fa[70:90, W + 112:W + 136].mean() < 200,
          f"{fa[70:90, W + 76:W + 100].mean():.0f} / {fa[70:90, W + 112:W + 136].mean():.0f}")
    check("mB (no put-back) has no box", fb[70:90, 105:135].mean() < 200 and fb[70:90, W + 76:W + 100].mean() < 200)

# Resume on (bug sweep #7): the put-back's readers held mA open, so it never reached finished/
out2 = tmp / "merged2"
P.save_settings(str(clip), {"enabled": True, "mode": "Add", "parallax": 10.0, "opacity": 1.0})
st.setProperty("output_folder", str(out2)); st.setProperty("resume", True); pump(0.5)
mg.start(); pump(0.5)
_fin = root / "orig" / "finished"
until(lambda: not mg.property("busy"), 300)
# (mB is a splatted4 quad, which carries its own original: none of its Original files move)
check("the Resume batch moves mA into the Original folder's finished/",
      until(lambda: (_fin / "mA.mp4").exists() and not clip.exists(), 20),
      str(sorted(p.name for p in (root / "orig").iterdir())))
studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
