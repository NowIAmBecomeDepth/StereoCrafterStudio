"""The Depth Map Editor's keyed controls stay live (user, 2026-10-25: "make sure any controls that keyframes can
attach to stay live after keyframes exist on a clip ... other settings like in the depth map editor have keyframes
too"), real app, offscreen, a synthetic clip and a Rectangle shape layer (no SAM 3):

* on a key, every keyed control turned: that key takes it, and the frame's settings (what the preview, the
  overlay and the render draw with) have it;
* off a key on a keyed layer: the turn adds a dynamic key there (as Splatting's dials do), so it shows -- it
  used to change only the layer's base value, which the keys hide on every frame;
* Sample Gradient on a keyed layer: near / far land in the key (they went to the base values: no change).

Configs and last_jobs.json redirected; no GPU.
"""
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
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from core.depthedit import depth_io
from core.depthedit import keys as K
from studio import app as A

W, H, N = 256, 160, 24
clip_dir = tmp / "clip"; clip_dir.mkdir()
rgb_path = str(clip_dir / "synthetic.mp4").replace("\\", "/")
depth_path = str(clip_dir / "synthetic_depth.mp4").replace("\\", "/")
subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                f"testsrc2=size=512x320:rate=24:duration={N / 24}", "-pix_fmt", "yuv420p", rgb_path], check=True)
info = depth_io.ClipInfo(depth_path, W, H, N, "24/1", "yuv420p10le", False)
writer = depth_io.DepthWriter(depth_path, info)
ramp = np.linspace(0.1, 0.6, W, dtype=np.float32)[None, :].repeat(H, axis=0)
for f in range(N):
    writer.write(depth_io.from_unit(ramp, info))
writer.close()

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
objs["Shell"].setProperty("uiScale", "1.0")
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
def find(it, pred):
    for c in it.childItems():
        if pred(c): return c
        r = find(c, pred)
        if r is not None: return r
def click_in(item):
    QTest.mouseMove(win, QPoint(5, 5)); pump(0.1)
    c = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(c.x()), int(c.y())))

pump(0.6)
de = objs["DepthEdit"]
stage = find(win.contentItem(), lambda c: c.objectName() == "stage")
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 4), Q_ARG("QVariant", True)); pump(0.8)
extra = stage.property("pages").property(4).toQObject()
extra.setProperty("tool", 1); pump(0.8)
page = find(extra, lambda c: c.objectName() == "depthEditPage")
check("the editor loads", until(lambda: de.property("state").get("ready"), 60), str(de.property("state").get("loadError")))
S = lambda: de.property("state")  # noqa: E731
eng = de._engine


class NoSam:
    """A shape layer never asks SAM 3."""
    loaded, owner, version = True, None, "sam3"
    def unload(self): pass
    def close_session(self): pass
    def cancel(self): pass
    def __getattr__(self, name):
        fails.append(f"SAM 3 was asked for {name}")
        raise RuntimeError("SAM 3 used")

de.setVar("rgb_path", rgb_path); pump(0.3)
de.setVar("output_dir", str(clip_dir / "edited").replace("\\", "/")); pump(0.2)
click_in(find(page, lambda c: c.objectName() == "deLoad"))
check("the clip loads", until(lambda: S().get("hasClip") and not S().get("busyJob"), 60))
eng.engine = NoSam()
de.setVar("view", "Edited"); de.setVar("tool", "Shape"); pump(0.4)
de.setShapeKind("Rectangle"); pump(0.3)
pic = find(page, lambda c: c.objectName() == "dePicture")
shown = find(pic, lambda c: c.objectName() == "deImage").parentItem()
def drag(a, b):
    pa = shown.mapToScene(QPointF(a[0] * shown.width(), a[1] * shown.height()))
    pb = shown.mapToScene(QPointF(b[0] * shown.width(), b[1] * shown.height()))
    QTest.mousePress(win, Qt.LeftButton, Qt.NoModifier, QPoint(round(pa.x()), round(pa.y()))); pump(0.1)
    for k in range(1, 6):
        q = pa + (pb - pa) * (k / 5)
        QTest.mouseMove(win, QPoint(round(q.x()), round(q.y()))); pump(0.05)
    QTest.mouseRelease(win, Qt.LeftButton, Qt.NoModifier, QPoint(round(pb.x()), round(pb.y()))); pump(0.4)
drag((0.3, 0.3), (0.7, 0.7))
until(lambda: not S().get("busyJob"), 30)
layer = eng._layer()
check("a Rectangle shape layer", layer is not None and bool(layer.shapes), str(layer))

#: a value per keyed control, different from its default
TRY = {"brightness": 0.3, "contrast": 1.4, "flatten": 0.5, "smooth": 3.0, "edge_top": 2.0, "edge_bottom": 3.0,
       "edge_left": 4.0, "edge_right": 5.0, "grow": 2.0, "edge_snap": 3.0, "feather": 4.0, "opacity": 0.6,
       "add_depth": 0.2, "gradient.near": 0.8, "gradient.far": 0.15, "gradient.curve": 1.7, "gradient.keep_detail": 0.4}
check("every keyed control has a value to try", sorted(TRY) == sorted(K.key_name(p) for p in K.KEY_PATHS))
def at(frame):
    return K.settings_at(layer.settings, layer.keys, frame)
def val(s, name):
    for part in name.split("."):
        s = getattr(s, part)
    return float(s)
def ctl(name, v):                    # in the settings' own units (setControl takes some in video pixels)
    de._set_control_raw(name, v); pump(0.15)

de.setFrame(5); pump(0.4)
de.addKeyframe("static"); pump(0.4)
check("a static key on frame 5", layer.keys.frame_at(5) is not None)
missed = []
for name, v in TRY.items():
    ctl(name, v)
    if abs(float(layer.keys.frame_at(5)["values"][name]) - v) > 1e-6 or abs(val(at(5), name) - v) > 1e-6:
        missed.append(f"{name}: key {layer.keys.frame_at(5)['values'][name]}, frame {val(at(5), name)}")
check("on the key, every keyed control turned: the key and the frame have it", not missed, "; ".join(missed))

de.setFrame(12); pump(0.4)
ctl("brightness", -0.2)
e12 = layer.keys.frame_at(12)
check("off a key: the turn adds a dynamic key there", e12 is not None and e12["type"] == "dynamic", str(e12))
check("...holding the turn, and the rest as the frame had them",
      e12 is not None and abs(e12["values"]["brightness"] + 0.2) < 1e-6
      and all(abs(e12["values"][n] - TRY[n]) < 1e-6 for n in TRY if n != "brightness"), str(e12))
check("...so the frame shows it", abs(at(12).brightness + 0.2) < 1e-6, at(12).brightness)
until(lambda: 12 in [k["frame"] for k in (S().get("keyframes") or [])], 5)
check("...and the editor lists the key, on it", 12 in [k["frame"] for k in (S().get("keyframes") or [])]
      and S().get("onKey") == "dynamic", str(S().get("keyframes")))

de.setFrame(5); pump(0.4)
de.run("_sample_gradient"); until(lambda: not S().get("busyJob"), 30); pump(0.4)
k5 = layer.keys.frame_at(5)["values"]
check("Sample Gradient on a keyed layer: near / far land in the key",
      (abs(k5["gradient.near"] - TRY["gradient.near"]) > 1e-6 or abs(k5["gradient.far"] - TRY["gradient.far"]) > 1e-6)
      and abs(at(5).gradient.near - k5["gradient.near"]) < 1e-6 and abs(at(5).gradient.far - k5["gradient.far"]) < 1e-6,
      f"key near {k5['gradient.near']} far {k5['gradient.far']}")

studio_warns = [w for w in warns if "EncodingDialog.qml" not in w]   # 34d33d5's, not this check's
check("no QML warnings (but EncodingDialog's known one)", not studio_warns, str(studio_warns[:3]))
h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
check("the real last_jobs.json untouched", h0 == h1)
print("PASS" if not fails else f"FAIL ({len(fails)})", fails)
_fixtures.exit_now(0 if not fails and h0 == h1 else 1)
