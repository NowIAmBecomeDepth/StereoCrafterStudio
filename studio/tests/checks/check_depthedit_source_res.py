"""Upscale Map in the real app, offscreen (user, 2026-10-05: "i want to be
able to do it with the first clip i load in ... default to the depth map res
and if a user clicks Upscale Map it rerenders it at the source resolution").
The synthetic clip of check_depthedit (a 256x160 map, a 512x320 source):
Load Clip reads the map at its own size; Upscale Map (beside Load Clip,
with a tooltip) reads it again at 512x320 -- bilinear, as Splatting
enlarges it -- keeping the layer (an RGB layer's full-size masks become its
masks), and is then done for the clip; the dials' pixels are the clip's
own; Render writes 512x320 with every untouched pixel bit-exact to the
enlarged map; Load Clip again goes back to the map's size with the saved
masks shrunk. Configs and last_jobs.json redirected; no SAM 3, no GPU."""
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
import cv2
import numpy as np
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, QObject, Q_ARG, QPoint, QPointF, Qt, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtTest import QTest
from core.depthedit import depth_io
from studio import app as A

# ---- the synthetic clip (v1's check's recipe) --------------------------------------
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
    frame = ramp.copy()
    frame[40:120, 80 + f:160 + f] = 0.5
    writer.write(depth_io.from_unit(frame, info))
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
check("the Depth Map Editor is the second sub-tab", page is not None and page.isVisible())
check("it loads on first view", until(lambda: de.property("state").get("ready"), 60), str(de.property("state").get("loadError")))
S = lambda: de.property("state")  # noqa: E731
eng = de._engine

# ---- Start Tracking on every rail page (v1: a transition per page)
track = find(page, lambda c: c.objectName() == "deTrack")
hidden = []

from core.depthedit.project import MaskStore
upb = find(page, lambda c: c.objectName() == "deUpscale")
check("Upscale Map sits beside Load Clip, off until a clip is loaded", upb is not None and upb.isVisible()
      and not upb.property("enabled"))
_tips = [str(o.property("tip") or "") for o in upb.findChildren(QObject) if o.property("tip") is not None]
check("...with a tooltip", any("source video's size" in t for t in _tips), str(_tips)[:200])

de.setVar("rgb_path", rgb_path); pump(0.3)
de.setVar("output_dir", str(clip_dir / "edited").replace("\\", "/")); pump(0.2)
click_in(find(page, lambda c: c.objectName() == "deLoad"))
check("Load Clip loads at the map's own size", until(lambda: S().get("hasClip") and not S().get("busyJob"), 60)
      and eng.codes.shape == (N, H, W) and not S().get("upscaled"), (S().get("status"), None if eng.codes is None else eng.codes.shape))
check("...and Upscale Map is ready", until(lambda: upb.property("enabled"), 5))

# a layer made at the map's size, with SAM 3's full-size masks as an RGB layer keeps them
de.run("_new_layer"); pump(0.3)
layer = eng._layer()
layer.source = "RGB"
layer.masks_hi = MaskStore(320, 512)
masks = []
for f in range(N):
    m = np.zeros((H, W), bool); m[40:120, 80 + f:160 + f] = True
    layer.masks.set(f, m)
    big = np.zeros((320, 512), bool); big[80:240, 160 + 2 * f:320 + 2 * f] = True
    layer.masks_hi.set(f, big)
    masks.append(big)
layer.tracked = True
de.setControl("feather", 0); de.setControl("edge_snap", 0)
de.setControl("brightness", 0.2); pump(0.3)

native = eng.codes.copy()
click_in(upb)
check("Upscale Map reads the map at the source's 512x320", until(lambda: S().get("upscaled") and not S().get("busyJob"), 60)
      and eng.codes.shape == (N, 320, 512) and eng.info.file_size == (W, H), (S().get("status"), eng.codes.shape))
check("...enlarged bilinear, as Splatting enlarges it",
      all(np.array_equal(cv2.resize(a, (512, 320), interpolation=cv2.INTER_LINEAR), b) for a, b in zip(native, eng.codes)))
check("...says so", "upscaled to 512x320" in S()["status"], S()["status"])
check("...keeps the layer, its full-size masks now its masks", eng._layer() is layer and layer.tracked
      and np.array_equal(layer.masks.get(5), masks[5]) and layer.masks_hi is None)
check("...keeps its settings", abs(layer.settings.brightness - 0.2) < 1e-9)
check("...and is done for this clip", not upb.property("enabled") and upb.property("text") == "Map Upscaled",
      upb.property("text"))
de.setControl("edge_left", -3.0); pump(0.3)
check("a dial's pixels are the clip's own now: Left edge -3 px is -3 map px", abs(layer.settings.edge_left + 3.0) < 1e-6,
      str(layer.settings.edge_left))
de.setControl("edge_left", 0.0); pump(0.2)

de.setVar("page", "Output"); pump(0.3)
check("no source-resolution option left on the Output page", find(page, lambda c: c.objectName() == "deEditHires") is None)
questions = []
de.question.connect(lambda t, m: questions.append(m))
click_in(find(page, lambda c: c.objectName() == "deRender"))
out_path = clip_dir / "edited" / "synthetic_depth.mp4"
check("Render writes <clip>_depth.mp4", until(lambda: out_path.exists() and not S().get("busyJob"), 120), S().get("status"))
out_info = depth_io.probe(str(out_path))
check("...at 512x320", (out_info.width, out_info.height) == (512, 320), (out_info.width, out_info.height))
out = depth_io.load_codes(out_info)
outside = ~np.stack(masks)
check("every pixel outside the mask is bit-exact to the enlarged map", np.array_equal(out[outside], eng.codes[outside]))
shift = depth_io.to_unit(out, out_info)[~outside] - depth_io.to_unit(eng.codes, eng.info)[~outside]
check("inside, the object moved +20%", abs(float(shift.mean()) - 0.2) < 0.002, f"{float(shift.mean()):.4f}")

# Load Clip again: the map's own size, the saved masks shrunk and kept full size
click_in(find(page, lambda c: c.objectName() == "deLoad"))
check("Load Clip goes back to the map's own size", until(lambda: S().get("hasClip") and not S().get("busyJob")
                                                     and eng.codes is not None and eng.codes.shape == (N, H, W), 60)
      and not S().get("upscaled"))
ly = eng.project.layers[0] if eng.project.layers else None
check("...the layer still tracked, its masks shrunk to 256x160", ly is not None and ly.tracked
      and ly.masks.get(0) is not None and ly.masks.get(0).shape == (H, W))
check("...and kept full size, so Upscale Map brings them back", ly is not None and ly.masks_hi is not None
      and (ly.masks_hi.width, ly.masks_hi.height) == (512, 320))
check("Upscale Map is ready again", until(lambda: upb.property("enabled"), 5))
check("no QML warnings", not warns, "\n".join(warns[:5]))
h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
check("the real last_jobs.json untouched", h1 == h0)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(1 if fails else 0)   # not os._exit: the DLLs' teardown can crash after a green run
