"""Deleting SAM 3 points in the Depth Map Editor, real app, offscreen (user, 2026-10-13:
"allow a user to select the sam3 selection points and press the delete key to delete
them"). SAM 3 stood in for (a fake whose replay masks a square around each Add point, as
Sam3Engine.replay returns each prompted frame's result): a click on a point picks it and
adds no click, Shift+click adds to the pick, Escape drops it; Delete takes the picked
points out and the layer's prompts are replayed without them, the mask following; one
Undo step puts them back; the frame's last points deleted take its mask too, with no SAM 3
run; a point on another frame can't be deleted from this one. Configs and last_jobs.json
redirected; no GPU."""
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


from core.depthedit.project import Click
replays = []
def square_at(clicks):
    m = np.zeros((1, H, W), bool)
    for c in clicks:
        if c.label == 1:
            x, y = int(c.x * W), int(c.y * H)
            m[0, max(0, y - 10):y + 10, max(0, x - 10):x + 10] = True
    return m
class FakeSam:
    """Sam3Engine as _session_for uses it: loaded, open() and replay()."""
    loaded, owner, version = True, None, "sam3"
    def open(self, path, offload):
        return N
    def replay(self, text, text_frame, clicks):
        clicks = list(clicks)
        replays.append(clicks)
        out = {}
        for f in sorted({c.frame for c in clicks}):
            if any(c.label == 1 for c in clicks if c.frame == f):   # only Remove points: nothing found
                out[f] = ([1], square_at([c for c in clicks if c.frame == f]))
        return out
    def close_session(self):
        pass
    def unload(self):
        pass
    def cancel(self):
        pass

de.setVar("rgb_path", rgb_path); pump(0.3)
de.setVar("output_dir", str(clip_dir / "edited").replace("\\", "/")); pump(0.2)
click_in(find(page, lambda c: c.objectName() == "deLoad"))
check("the clip loads", until(lambda: S().get("hasClip") and not S().get("busyJob"), 60))
eng.engine = FakeSam()
eng._sam_ready = lambda: True
de.setVar("view", "Edited"); de.setVar("tool", "Add"); pump(0.3)
de.run("_new_layer"); pump(0.3)
layer = eng._layer()
A_, B_, C_ = Click(0, 1, 0.3, 0.5, 1), Click(0, 1, 0.6, 0.5, 1), Click(0, 1, 0.45, 0.3, 0)
layer.clicks[:] = [A_, B_, C_, Click(5, 1, 0.5, 0.5, 1)]
layer.masks.set(0, square_at([A_, B_])[0])
de.setFrame(0); pump(0.4)
check("the frame's points carry their place in the layer's clicks",
      [p["i"] for p in de.property("markers")["points"]] == [0, 1, 2], str(de.property("markers")["points"]))

pic = find(page, lambda c: c.objectName() == "dePicture")
shown = find(pic, lambda c: c.objectName() == "deImage").parentItem()
def at_point(cl, mods=Qt.NoModifier):
    p = shown.mapToScene(QPointF(cl.x * shown.width(), cl.y * shown.height()))
    QTest.mouseClick(win, Qt.LeftButton, mods, QPoint(round(p.x()), round(p.y()))); pump(0.3)
def picked():
    return [int(v) for v in (page.property("selPoints").toVariant() if hasattr(page.property("selPoints"), "toVariant") else page.property("selPoints"))]
def rings():
    def walk(it):
        for c in it.childItems():
            yield c
            yield from walk(c)
    return sum(1 for c in walk(shown) if c.objectName() == "dePointPicked" and c.isVisible())

at_point(B_)
check("a click on a point picks it", picked() == [1] and rings() == 1, str(picked()))
check("...and adds no click", len(layer.clicks) == 4 and not replays)
at_point(C_, Qt.ShiftModifier)
check("Shift+click adds another to the pick", sorted(picked()) == [1, 2] and rings() == 2, str(picked()))
QTest.keyClick(win, Qt.Key_Escape); pump(0.3)
check("Escape drops the pick", picked() == [] and rings() == 0)
at_point(B_); at_point(C_, Qt.ControlModifier)
n0 = len(de._history._undo)
QTest.keyClick(win, Qt.Key_Delete); pump(0.3)
check("Delete takes the picked points out", until(lambda: not S().get("busyJob"), 20)
      and layer.clicks == [A_, layer.clicks[-1]] and layer.clicks[-1].frame == 5, str(layer.clicks))
check("...SAM 3 runs the layer's prompts again without them", replays and replays[-1] == layer.clicks, str(replays))
m0 = layer.masks.get(0)
check("...the mask follows: the deleted Add point's area gone, the kept one's there",
      m0 is not None and m0[80, int(0.3 * W)] and not m0[80, int(0.6 * W)])
check("...the picture's points with it, and the pick cleared",
      [p["i"] for p in de.property("markers")["points"]] == [0] and picked() == [])
check("...one Undo step", len(de._history._undo) == n0 + 1 and S()["undoLabel"] == "Delete 2 points", S()["undoLabel"])
de.undo(); pump(0.4)
layer = eng._layer()
check("Undo puts them back", len(layer.clicks) == 4 and [c.x for c in layer.clicks[:3]] == [0.3, 0.6, 0.45])

# bug sweep 2026-10-14c: points picked on markers drawn for another layer delete nothing here
de.setFrame(1); pump(0.3); de.setFrame(0); pump(0.4)
de._marker_layer = "another layer's"
de.deletePoints([1]); pump(0.4)
check("points picked on another layer's markers delete nothing from this one", len(eng._layer().clicks) == 4,
      str(eng._layer().clicks))
de.setFrame(1); pump(0.3); de.setFrame(0); pump(0.4)
# ...and a replay that finds nothing on the frame takes the deleted points' mask with them
replays.clear()
de.deletePoints([0, 1]); pump(0.3)
until(lambda: replays and not S().get("busyJob"), 20)
layer = eng._layer()
m0 = layer.masks.get(0)
check("only a Remove point left: SAM 3 finds nothing, and the deleted points' mask is gone",
      [c.label for c in layer.clicks if c.frame == 0] == [0] and (m0 is None or not m0.any()), str(m0 is None))
de.undo(); pump(0.4)
layer = eng._layer()

replays.clear()
for i in range(3):
    at_point(layer.clicks[i], Qt.ShiftModifier)
check("every point on the frame picked", sorted(picked()) == [0, 1, 2], str(picked()))
QTest.keyClick(win, Qt.Key_Backspace); pump(0.4)
check("the frame's last points deleted (Backspace too): its mask goes with them, no SAM 3 run",
      [c.frame for c in layer.clicks] == [5] and layer.masks.get(0) is None and not replays,
      (layer.clicks, replays))
de.deletePoints([0]); pump(0.3)
check("a point on another frame can't be deleted from this one", [c.frame for c in layer.clicks] == [5])

de.shutdown()
studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, " | ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
