"""Shapes in the Depth Map Editor, real app, offscreen (user, 2026-10-17: "simple polygon shapes for those
locked off shots that don't need to be tracked" -- "one option should be a point connector for custom
shapes"). The Shape tool on the picture: Points -- clicks join up, a click on the first point closes the
shape, which fills a layer on EVERY frame with nothing tracked (no SAM 3: a stand-in that fails if asked);
Backspace takes a point back and Esc drops the shape; Rectangle and Ellipse are drags; Ctrl cuts; a shape
drawn while a tracked layer is selected starts a layer of its own; Clear Shapes and one Undo step each.
Configs and last_jobs.json redirected; no GPU."""
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
from core.depthedit import shapes as SH
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
    """Shapes never ask SAM 3: any use of it fails the check."""
    loaded, owner, version = True, None, "sam3"
    def unload(self): pass             # closing the editor lets go of it: not a use
    def close_session(self): pass
    def cancel(self): pass
    def __getattr__(self, name):
        fails.append(f"SAM 3 was asked for {name}")
        raise RuntimeError("SAM 3 used by a shape")

de.setVar("rgb_path", rgb_path); pump(0.3)
de.setVar("output_dir", str(clip_dir / "edited").replace("\\", "/")); pump(0.2)
click_in(find(page, lambda c: c.objectName() == "deLoad"))
check("the clip loads", until(lambda: S().get("hasClip") and not S().get("busyJob"), 60))
eng.engine = NoSam()
check("Shape is one of the tools, its kinds Points / Rectangle / Ellipse",
      "Shape" in de.property("choices")["tool"] and de.property("choices")["shapeKinds"] == list(SH.KINDS))
de.setVar("view", "Edited"); de.setVar("tool", "Shape"); pump(0.4)
kind_box = find(page, lambda c: c.objectName() == "deShapeKind")
check("the Shape tool shows its kinds, in a row of their own", kind_box is not None and kind_box.isVisible()
      and find(page, lambda c: c.objectName() == "deShapeRow").isVisible())
pic0 = find(page, lambda c: c.objectName() == "dePicture")
def right_edge(it):
    return it.mapToScene(QPointF(it.width(), 0)).x()
check("...the tool row and the shape row fit the picture's column (a 1220 px window)",
      right_edge(find(page, lambda c: c.objectName() == "deTool")) <= right_edge(pic0) + 1
      and right_edge(kind_box) <= right_edge(pic0) + 1,
      (right_edge(find(page, lambda c: c.objectName() == "deTool")), right_edge(kind_box), right_edge(pic0)))

pic = find(page, lambda c: c.objectName() == "dePicture")
shown = find(pic, lambda c: c.objectName() == "deImage").parentItem()
def at(x, y, mods=Qt.NoModifier, button=Qt.LeftButton):
    p = shown.mapToScene(QPointF(x * shown.width(), y * shown.height()))
    QTest.mouseClick(win, button, mods, QPoint(round(p.x()), round(p.y()))); pump(0.25)
def drag(a, b, mods=Qt.NoModifier):
    pa = shown.mapToScene(QPointF(a[0] * shown.width(), a[1] * shown.height()))
    pb = shown.mapToScene(QPointF(b[0] * shown.width(), b[1] * shown.height()))
    QTest.mousePress(win, Qt.LeftButton, mods, QPoint(round(pa.x()), round(pa.y()))); pump(0.1)
    for k in range(1, 6):
        q = pa + (pb - pa) * (k / 5)
        QTest.mouseMove(win, QPoint(round(q.x()), round(q.y()))); pump(0.05)
    QTest.mouseRelease(win, Qt.LeftButton, mods, QPoint(round(pb.x()), round(pb.y()))); pump(0.4)
draft = lambda: len(page.property("shapeDraft").toVariant() if hasattr(page.property("shapeDraft"), "toVariant") else page.property("shapeDraft"))  # noqa: E731

n_layers = len(eng.project.layers)
at(0.2, 0.2); at(0.6, 0.2); at(0.6, 0.7)
check("Points: each click adds a point, nothing made yet", draft() == 3 and len(eng.project.layers) == n_layers, draft())
at(0.2, 0.7)
QTest.keyClick(win, Qt.Key_Backspace); pump(0.2)
check("Backspace takes the last point back", draft() == 3, draft())
at(0.2, 0.7)
n_undo = len(de._history._undo)
at(0.2, 0.2)                                       # the first point again: closed
layer = eng._layer()
check("a click on the first point closes it: a new layer, the shape its only content",
      draft() == 0 and len(eng.project.layers) == n_layers + 1 and layer is not None and len(layer.shapes) == 1
      and layer.shapes[0]["kind"] == "Points" and len(layer.shapes[0]["pts"]) == 4, (draft(), layer and layer.shapes))
check("...its mask on every frame, nothing tracked (no SAM 3)", layer.masks.frames() == list(range(N)) and not layer.has_prompts())
m = layer.masks.get(N - 1)
check("...where it was drawn", m is not None and m[int(0.45 * H), int(0.4 * W)] and not m[int(0.1 * H), int(0.9 * W)])
check("...one Undo step", len(de._history._undo) == n_undo + 1 and S().get("undoLabel") == "Shape", S().get("undoLabel"))

at(0.7, 0.3); at(0.9, 0.3); at(0.9, 0.5)
QTest.keyClick(win, Qt.Key_Escape); pump(0.2)
check("Esc drops a shape half drawn", draft() == 0 and len(layer.shapes) == 1)

de.setShapeKind("Rectangle"); pump(0.3)
drag((0.7, 0.1), (0.95, 0.4))
check("Rectangle: a drag adds one to the same layer", len(layer.shapes) == 2 and layer.shapes[1]["kind"] == "Rectangle"
      and layer.masks.get(5)[int(0.25 * H), int(0.8 * W)], layer.shapes)
de.setShapeKind("Ellipse"); pump(0.3)
drag((0.3, 0.3), (0.5, 0.6), Qt.ControlModifier)
check("Ctrl: the shape cuts its area out", len(layer.shapes) == 3 and layer.shapes[2]["cut"]
      and not layer.masks.get(5)[int(0.45 * H), int(0.4 * W)], layer.shapes[-1:])

# a tracked layer selected: the shape starts a layer of its own
de.run("_new_layer"); pump(0.3)
tracked = eng._layer()
from core.depthedit.project import Click
tracked.clicks.append(Click(0, 1, 0.5, 0.5, 1)); tracked.masks.set(0, np.ones((H, W), bool))
de.setShapeKind("Rectangle"); pump(0.3)
drag((0.1, 0.1), (0.3, 0.3))
check("drawn while a tracked layer is selected: a layer of its own, the tracked one untouched",
      eng._layer() is not tracked and len(tracked.shapes) == 0 and tracked.masks.frames() == [0]
      and len(eng._layer().shapes) == 1, [ly.name for ly in eng.project.layers])

eng.selected = eng.project.layers.index(layer); de._dirty(); pump(0.4)
clear = find(page, lambda c: c.objectName() == "deClearShapes")
check("Clear Shapes shows for a layer with shapes", clear is not None and clear.isVisible())
# a press, a pause, a release (an instant click_in on this button is lost offscreen)
_bc = clear.mapToScene(QPointF(clear.width() / 2, clear.height() / 2)); _bp = QPoint(round(_bc.x()), round(_bc.y()))
QTest.mouseMove(win, _bp); pump(0.2)
QTest.mousePress(win, Qt.LeftButton, Qt.NoModifier, _bp); pump(0.15)
QTest.mouseRelease(win, Qt.LeftButton, Qt.NoModifier, _bp); pump(0.4)
check("Clear Shapes: the shapes gone, and the mask", layer.shapes == [] and len(layer.masks) == 0)
de.undo(); pump(0.4)
layer = eng.project.layers[eng.selected]
check("...and Undo brings them back", len(layer.shapes) == 3 and layer.masks.frames() == list(range(N)))

# one edge dragged (user, 2026-10-25: "drag a line and it extends only that one line of a shape. vertical
# lines can be dragged horizontally and horizontal lines dragged vertically"): Points and Rectangle
de.setShapeKind("Rectangle"); pump(0.3)
poly0 = [list(p) for p in layer.shapes[0]["pts"]]          # (.2,.2) (.6,.2) (.6,.7) (.2,.7): edge 1 the right
n_undo = len(de._history._undo)
drag((0.6, 0.45), (0.66, 0.40))                           # the right edge, dragged out (and a little up)
poly = layer.shapes[0]["pts"]
check("Points: dragging the upright right edge moves just it, sideways -- no new shape",
      len(layer.shapes) == 3 and all(abs(poly[k][0] - 0.66) < 0.01 for k in (1, 2))
      and [poly[k][1] for k in (1, 2)] == [poly0[k][1] for k in (1, 2)] and [poly[0], poly[3]] == [poly0[0], poly0[3]],
      (poly0, poly))
check("...the mask stretches with it, on every frame", layer.masks.get(N - 1)[int(0.45 * H), int(0.63 * W)]
      and layer.masks.frames() == list(range(N)))
check("...one Undo step", len(de._history._undo) == n_undo + 1 and S().get("undoLabel") == "Move shape edge",
      S().get("undoLabel"))
rect0 = [list(p) for p in layer.shapes[1]["pts"]]        # (.7,.1) (.95,.4): its top edge at y .1
drag((0.82, 0.1), (0.86, 0.04))                          # the flat top edge, dragged up (and a little right)
rect = layer.shapes[1]["pts"]
check("Rectangle: dragging its flat top edge moves just it, up -- the sides stay",
      len(layer.shapes) == 3 and abs(min(rect[0][1], rect[1][1]) - 0.04) < 0.01
      and sorted([rect[0][0], rect[1][0]]) == sorted([rect0[0][0], rect0[1][0]])
      and max(rect[0][1], rect[1][1]) == max(rect0[0][1], rect0[1][1]), (rect0, rect))
de.undo(); pump(0.4)
layer = eng.project.layers[eng.selected]
check("...Undo puts the edge back", sorted(p[1] for p in layer.shapes[1]["pts"]) == sorted(p[1] for p in rect0))

de.setVar("tool", "Add"); pump(0.3)
check("another tool: the shape row hidden", not kind_box.isVisible()
      and not find(page, lambda c: c.objectName() == "deShapeRow").isVisible())

de.shutdown()
studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, " | ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
