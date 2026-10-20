"""The Depth Map Editor keeps unsaved layers (audit 2026-09-28 #12), in the real
app, offscreen, without SAM 3. Layers used to live only in memory until
Render or Save: Splatting's Edit Depth Map on another clip, Load Clip, or
closing the app replaced them without a word. Each of those three now saves
the project beside the output first, and reopening the clip brings the layers
back. A clip opened and left untouched gets no project file. Configs and
last_jobs.json redirected."""
import os, sys, tempfile, hashlib, time, subprocess
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
import numpy as np
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, QPoint, QPointF, Qt, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtTest import QTest
from core.depthedit import depth_io
from core.depthedit.project import project_path_for
import json
from studio import app as A


W, H, N = 256, 160, 8
clip_dir = tmp / "clip"; clip_dir.mkdir()
edited = clip_dir / "edit_history"  # where Edit Depth Map from Splatting saves (2026-09-29)
def make(name, shift):
    rgb = str(clip_dir / f"{name}.mp4").replace("\\", "/")
    depth = str(clip_dir / f"{name}_depth.mp4").replace("\\", "/")
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=size=512x320:rate=24:duration={N / 24}", "-pix_fmt", "yuv420p", rgb], check=True)
    info = depth_io.ClipInfo(depth, W, H, N, "24/1", "yuv420p10le", False)
    w = depth_io.DepthWriter(depth, info)
    ramp = np.linspace(0.1, 0.6, W, dtype=np.float32)[None, :].repeat(H, axis=0)
    for f in range(N):
        fr = ramp.copy(); fr[40:120, 80 + shift:160 + shift] = 0.5
        w.write(depth_io.from_unit(fr, info))
    w.close()
    return rgb, depth
A_rgb, A_depth = make("alpha", 0)
B_rgb, B_depth = make("beta", 10)

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

pump(0.6)
de = objs["DepthEdit"]
de.ensureLoaded()
check("the editor loads", until(lambda: de.property("state").get("ready"), 90), str(de.property("state").get("loadError")))
S = lambda: de.property("state")  # noqa: E731
eng = de._engine
def loaded(depth):
    return until(lambda: S().get("hasClip") and not S().get("busyJob") and eng.project is not None
                 and os.path.normcase(eng.project.depth_path) == os.path.normcase(depth), 60)
def saved_layers(depth):
    p = project_path_for(str(edited), depth)
    if not os.path.isfile(p):
        return None
    return [ly["name"] for ly in json.load(open(p, encoding="utf-8"))["layers"]]
def add_layer():
    n = len(eng.project.layers)
    de.run("_new_layer")
    return until(lambda: len(eng.project.layers) == n + 1, 10)

# 1. Splatting's Edit Depth Map on another clip
de.openFromSplatting(A_rgb, A_depth)
check("clip A opens from Splatting", loaded(A_depth), S().get("status"))
check("...one new layer", add_layer())
de.openFromSplatting(B_rgb, B_depth)
check("Edit Depth Map on clip B", loaded(B_depth), S().get("status"))
check("...clip A's layer was saved first", saved_layers(A_depth) == ["Layer 1"], str(saved_layers(A_depth)))
check("...an untouched clip B gets no project file", saved_layers(B_depth) is None)
de.openFromSplatting(A_rgb, A_depth)
check("reopening clip A brings its layer back",
      loaded(A_depth) and [ly.name for ly in eng.project.layers] == ["Layer 1"], str(eng.project and [ly.name for ly in eng.project.layers]))

# 2. Load Clip
check("...a second layer", add_layer())
de.setVar("rgb_path", B_rgb); de.setVar("depth_path", B_depth); pump(0.3)
de.run("_load_clip")
check("Load Clip on clip B", loaded(B_depth), S().get("status"))
check("...clip A's two layers were saved first", saved_layers(A_depth) == ["Layer 1", "Layer 2"], str(saved_layers(A_depth)))

# 3. closing the app
de.openFromSplatting(A_rgb, A_depth)
check("clip A again", loaded(A_depth) and len(eng.project.layers) == 2)
check("...a third layer", add_layer())
de.shutdown()
check("closing saved clip A's three layers", saved_layers(A_depth) == ["Layer 1", "Layer 2", "Layer 3"],
      str(saved_layers(A_depth)))

print()
_lj = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None) == h0
print("real last_jobs.json unchanged:", _lj)
if not _lj:
    fails.append("real last_jobs.json changed")
print("FAILED:" if fails else "ALL OK", fails if fails else "")
for name in ("DepthCrafter", "Splatting", "Inpainting", "InpaintingV2", "Merging", "SceneCut"):
    try: objs[name].shutdown()
    except Exception: pass
_fixtures.exit_now(1 if fails else 0)
