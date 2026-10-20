"""Adding to a tracked layer: patches (user, 2026-10-05), with the real SAM 3.

"I want to add to the tracking after running the tracker once, but when I try
to fill in the gaps it deselects what was tracked and tracks what I clicked."
A made-up clip: a yellow square moving across, a red disc standing still. A
layer finds "yellow square" by text and is tracked; then an Add click on the
red disc (outside the mask) is a patch: the frame keeps the square AND gains
the disc; the layer's text and its own clicks are untouched; Start Tracking
tracks only the patch and the square's masks are as they were on every frame,
with the disc added; a full re-track keeps both; Clear Prompts drops the patch.

Whole app, offscreen; needs the GPU and SAM 3's weights -- run it only when no
job is using the GPU. Configs and last_jobs.json redirected.
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
import cv2
import numpy as np
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, QPoint, QPointF, Qt, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from core.depthedit import depth_io, sam3_backend
from studio import app as A

if not sam3_backend.weights_present():
    print("SAM 3's weights are not here."); _fixtures.exit_now(1)

# ---- the clip: noise, a yellow square moving right, a red disc standing still -------------
W, H, N = 640, 360, 30
DW, DH = 320, 180
clip_dir = tmp / "clip"; clip_dir.mkdir()
rgb_path = str(clip_dir / "shapes.mp4").replace("\\", "/")
depth_path = str(clip_dir / "shapes_depth.mp4").replace("\\", "/")
rng = np.random.default_rng(0)
bg = (rng.random((H, W, 3)) * 60 + 40).astype(np.uint8)
vw = cv2.VideoWriter(str(clip_dir / "raw.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), 24, (W, H))
for f in range(N):
    img = bg.copy()
    x = 250 + int(250 * f / (N - 1))
    cv2.rectangle(img, (x, 120), (x + 110, 230), (40, 210, 240), -1)      # BGR yellow
    cv2.circle(img, (110, 180), 55, (40, 40, 220), -1)                    # BGR red
    vw.write(img)
vw.release()
subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(clip_dir / "raw.mp4"), "-pix_fmt", "yuv420p", rgb_path], check=True)
info = depth_io.ClipInfo(depth_path, DW, DH, N, "24/1", "yuv420p10le", False)
writer = depth_io.DepthWriter(depth_path, info)
for f in range(N):
    writer.write(depth_io.from_unit(np.full((DH, DW), 0.3, np.float32), info))
writer.close()

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
objs["Shell"].setProperty("uiScale", "1.0")
win.show()
fails = []


def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok:
        fails.append(label)


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
        if pred(c):
            return c
        r = find(c, pred)
        if r is not None:
            return r


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
check("the editor loads", until(lambda: de.property("state").get("ready"), 60))
S = lambda: de.property("state")  # noqa: E731
eng = de._engine
idle = lambda: not eng._job_pending() and not S().get("busyJob")  # noqa: E731

de.setVar("rgb_path", rgb_path); pump(0.3)
de.setVar("output_dir", str(clip_dir / "edited").replace("\\", "/")); pump(0.2)
click_in(find(page, lambda c: c.objectName() == "deLoad"))
check("the clip loads", until(lambda: S().get("hasClip") and idle(), 60))

# square / disc regions at the depth map's size
def square(f):
    m = np.zeros((DH, DW), bool); x = (250 + int(250 * f / (N - 1))) // 2
    m[60:115, x:x + 55] = True
    return m
disc = np.zeros((DH, DW), bool); cv2.circle(disc.view(np.uint8), (55, 90), 27, 1, -1)
def cover(m, region):
    return float((m & region).sum()) / float(region.sum()) if m is not None else 0.0

de.run("_new_layer"); pump(0.3)
layer = eng._layer()
de.setVar("text", "yellow square"); pump(0.2)
de.run("_find_text")
check("Find 'yellow square'", until(idle, 300) and layer.text == "yellow square")
de.run("_track")
check("...tracked", until(lambda: idle() and layer.tracked, 600))
before = {f: layer.masks.get(f) for f in range(N)}
sq = [cover(before[f], square(f)) for f in range(N)]
dk = [cover(before[f], disc) for f in range(N)]
check("the tracked mask has the square on every frame, not the disc", min(sq) > 0.8 and max(dk) < 0.1,
      f"square min {min(sq):.2f}, disc max {max(dk):.2f}")

F = 10
de.goto(F) if hasattr(de, "goto") else None
eng.frame_idx = F; pump(0.2)
eng.tool_var.set("Add"); pump(0.1)
de.click(110 / W, 180 / H, False)
check("an Add click on the disc (outside the mask) is a patch", until(idle, 300) and len(layer.patch_clicks) == 1
      and not layer.clicks and layer.text == "yellow square", f"patch {len(layer.patch_clicks)}, clicks {len(layer.clicks)}")
m10 = layer.masks.get(F)
check("...the frame KEEPS the square and gains the disc", cover(m10, square(F)) > 0.8 and cover(m10, disc) > 0.8,
      f"square {cover(m10, square(F)):.2f}, disc {cover(m10, disc):.2f}")
check("...other frames as they were", all(np.array_equal(layer.masks.get(f), before[f]) for f in (0, 20, 29)))

de.run("_track")
check("Start Tracking with a patch pending", until(lambda: idle() and layer.patch_base is None, 600))
after = {f: layer.masks.get(f) for f in range(N)}
kept = all(after[f] is not None and bool((after[f] | ~before[f]).all()) for f in range(N) if before[f] is not None)
check("...the square's masks are still there on every frame", kept)
dk2 = [cover(after[f], disc) for f in range(N)]
check("...and the disc is added on every frame", min(dk2) > 0.8, f"disc min {min(dk2):.2f}")

de.run("_track")
check("a full re-track", until(lambda: idle() and layer.tracked, 600))
full = {f: layer.masks.get(f) for f in range(N)}
check("...keeps the square and the patch", min(cover(full[f], square(f)) for f in range(N)) > 0.8
      and min(cover(full[f], disc) for f in range(N)) > 0.8)

de.run("_clear_prompts"); pump(0.5)
check("Clear Prompts drops the patch too", not layer.patch_clicks and layer.patch_base is None)

_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
