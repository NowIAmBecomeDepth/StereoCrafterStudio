"""The shaper (user, 2026-10-06: "an LFO shape controller for dynamic keyframes";
"a user should be able to add extra dynamic keyframes in between the initial
range in the shaper"), real app, offscreen, over a TEMP copy of the small
fixture. Configs and last_jobs.json redirected.

  * no span -> a hint; a static key at 6 and a dynamic at 21, the playhead
    between: the shaper shows frames 6-21, Linear, its graph;
  * Sine, 2 cycles: saved on the dynamic keyframe, in the clip's sidecar, and
    the preview's own values follow it (2 full swings, landing on the key);
  * a click on the graph adds a point: the shape becomes Custom from Sine's
    own outline plus the new point; a drag moves it; right-click removes it;
  * Linear again drops the curve.

SHOTS_DIR=<folder> also saves the shaper as PNGs (Qt grabWindow of our own window).
"""
import os, sys, tempfile, hashlib, time, json
from pathlib import Path
import _fixtures
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
tmp = Path(tempfile.mkdtemp()); FIX = tmp / "fx"; _fixtures.copy("small", FIX)
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, QObject, QPoint, QPointF, QRect, Qt, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from studio import app as A
from core.common.keyframes import curve_fraction

SHOTS = os.environ.get("SHOTS_DIR")
qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
objs["Shell"].setProperty("uiScale", "1.0")
win.resize(1700, 1000)
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


sp = objs["Splatting"]; pv = sp.preview


def settle(t=120):
    end = time.time() + t
    while time.time() < end:
        pump(0.05)
        with pv._cv:
            idle = not pv._jobs
        if idle and not pv._loading and not pv._render_timer.isActive():
            pump(0.4)
            with pv._cv:
                if not pv._jobs:
                    return


def find(it, pred):
    for c in it.childItems():
        if pred(c):
            return c
        r = find(c, pred)
        if r is not None:
            return r


pump(0.6)
stage = find(win.contentItem(), lambda c: c.objectName() == "stage")
pages = stage.property("pages")
for i in range(6):
    p = pages.property(i).toQObject()
    if p is not None and p.metaObject().className().startswith("SplattingPage"):
        page, idx = p, i
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", idx), Q_ARG("QVariant", True)); pump(1.2)
for k, v in {"input_source_clips": str(FIX / "src").replace("\\", "/"),
             "input_depth_maps": str(FIX / "depth").replace("\\", "/"),
             "output_splatted": str(FIX / "out").replace("\\", "/"), "multi_map": False,
             "preview_source": "Splat Result", "preview_size": "100%", "border_mode": "Manual", "max_disp": 30,
             "auto_save_sidecar": True, "update_slider_from_sidecar": True, "auto_convergence_mode": "Off"}.items():
    sp._set(k, v)
pump(1.0)
pv.load(); settle()
check("preview loaded", pv.loaded)
page.setProperty("keyOpen", True); pump(0.4)
sh = find(page, lambda c: c.objectName() == "keyShaper")


def into_view(item):
    it = item.parentItem()
    while it is not None and not it.metaObject().className().startswith("QQuickFlickable"):
        it = it.parentItem()
    if it is not None:
        y = item.mapToItem(it.property("contentItem"), QPointF(0, 0)).y()
        it.setProperty("contentY", max(0.0, min(y - 200, it.property("contentHeight") - it.height())))
        pump(0.4)


def shot(name, hover=-1):
    if not SHOTS or sh is None:
        return
    QTest.mouseMove(win, QPoint(5, 5)); pump(1.2)          # no hover tip over the picture
    into_view(sh); pump(0.5)
    if hover >= 0:
        sh.setProperty("hoverSeg", hover); pump(0.3)        # the handle as a hover shows it
    card = sh.parentItem()
    while card is not None and not card.metaObject().className().startswith("FoldSection"):
        card = card.parentItem()
    item = card or sh
    p = item.mapToScene(QPointF(0, 0))
    img = win.grabWindow()                                   # our own window, never the screen
    dpr = img.devicePixelRatio() or 1.0
    r = QRect(int(p.x() * dpr) - 6, int(p.y() * dpr) - 6, int(item.width() * dpr) + 12, int(item.height() * dpr) + 12)
    os.makedirs(SHOTS, exist_ok=True)
    img.copy(r).save(os.path.join(SHOTS, name + ".png"))


pv.seek(5); settle(); pv.addKeyframe("static"); settle()
check("one keyframe: the shaper only hints", sh is not None and not sh.isVisible() or
      not find(sh, lambda c: c.objectName() == "keyShaperGraph").isVisible())
sp._set("max_disp", 60); pump(0.3)
pv.seek(20); settle(); pv.addKeyframe("dynamic"); settle()
pv.seek(12); settle()
graph = find(sh, lambda c: c.objectName() == "keyShaperGraph")
seg = sh.property("seg"); seg = seg.toVariant() if hasattr(seg, "toVariant") else seg
check("a static key at 6, a dynamic at 21, the playhead between: the shaper shows that span",
      sh.isVisible() and graph is not None and graph.isVisible() and seg.get("ok") and (seg["from"], seg["to"]) == (5, 20),
      str(seg))
check("...Linear to start", sh.property("curve").toVariant()["shape"] == "linear" if hasattr(sh.property("curve"), "toVariant")
      else sh.property("curve")["shape"] == "linear")
shot("1_linear")

shape = find(sh, lambda c: c.objectName() == "keyShape")
labels = list(shape.property("model").toVariant() if hasattr(shape.property("model"), "toVariant") else shape.property("model"))
check("the shapes offered", labels == ["Linear", "Ease In", "Ease Out", "Ease In-Out", "Sine", "Triangle", "Square", "Saw", "Random", "Custom"],
      str(labels))
QMetaObject.invokeMethod(shape, "activated", Q_ARG(int, labels.index("Sine"))); settle()
cyc = find(sh, lambda c: c.objectName() == "keyCycles")
cyc.setProperty("text", "2"); QMetaObject.invokeMethod(cyc, "editingFinished"); settle()
kf = {k["frame"]: k for k in pv.keyframes}
check("Sine, 2 cycles: on the dynamic keyframe", kf[20].get("curve") == {"shape": "sine", "cycles": 2}, str(kf[20]))
eng = pv._engine if hasattr(pv, "_engine") else None
track = eng.keyframe_track if eng is not None else None
if track is not None:
    md = [track.values_at(f)["max_disp_var"] for f in range(5, 21)]
    want = [30 + 30 * curve_fraction((f - 5) / 15, {"shape": "sine", "cycles": 2}, seed=20) for f in range(5, 21)]
    check("the preview's own values follow it: 2 full swings, landing on the key",
          max(abs(a - b) for a, b in zip(md, want)) < 1e-6 and abs(md[-1] - 60) < 1e-6,
          " ".join(f"{v:.0f}" for v in md))
side = list((FIX / "depth").glob("*.fssidecar"))
data = json.loads(side[0].read_text(encoding="utf-8")) if side else {}
saved = [k for k in data.get("keyframe_track", []) if k.get("frame") == 20]
check("...saved in the clip's sidecar", saved and saved[0].get("curve") == {"shape": "sine", "cycles": 2}, str(saved)[:200])
shot("2_sine_2_cycles")


def at(t, v):
    pad = graph.property("pad")
    x = pad + t * (graph.width() - 2 * pad)
    y = graph.height() - pad - v * (graph.height() - 2 * pad)
    p = graph.mapToScene(QPointF(x, y))
    return QPoint(int(p.x()), int(p.y()))


into_view(sh)
QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, at(0.5, 0.15)); settle()
kf = {k["frame"]: k for k in pv.keyframes}
c = kf[20].get("curve") or {}
check("a click adds a point: Custom, from Sine's outline plus the new one",
      c.get("shape") == "custom" and len(c.get("points", [])) > 20 and any(abs(p[0] - 0.5) < 0.01 and abs(p[1] - 0.15) < 0.03 for p in c["points"]),
      f"{c.get('shape')}, {len(c.get('points', []))} points")
shot("3_custom_from_sine")

# a clean custom curve: three points by hand, then drag one and remove one
pv.setKeyCurve(20, {"shape": "custom", "points": [[0.25, 1.0], [0.5, 0.2], [0.75, 0.9]]}); settle()
QTest.mousePress(win, Qt.LeftButton, Qt.NoModifier, at(0.5, 0.2)); pump(0.1)
for k in range(1, 6):
    QTest.mouseMove(win, at(0.5, 0.2 + 0.1 * k)); pump(0.05)
QTest.mouseRelease(win, Qt.LeftButton, Qt.NoModifier, at(0.5, 0.7)); settle()
pts = {k["frame"]: k for k in pv.keyframes}[20]["curve"]["points"]
check("dragging the middle point moves it", len(pts) == 3 and abs(pts[1][1] - 0.7) < 0.05, str(pts))
shot("4_custom_points")
QTest.mouseClick(win, Qt.RightButton, Qt.NoModifier, at(0.25, 1.0)); settle()
pts = {k["frame"]: k for k in pv.keyframes}[20]["curve"]["points"]
check("right-click removes a point", len(pts) == 2 and all(abs(p[0] - 0.25) > 0.01 for p in pts), str(pts))
QMetaObject.invokeMethod(shape, "activated", Q_ARG(int, labels.index("Linear"))); settle()
check("Linear again: the curve is gone", {k["frame"]: k for k in pv.keyframes}[20].get("curve") is None)

# ---- bends (user, 2026-10-06): a handle on the line, dragged
QTest.mouseMove(win, at(0.5, 0.5)); pump(0.3)
check("hovering the line shows its bend handle", sh.property("hoverSeg") == 0, str(sh.property("hoverSeg")))
shot("5_bend_handle", hover=0)
QTest.mouseMove(win, at(0.5, 0.5)); pump(0.2)
QTest.mousePress(win, Qt.LeftButton, Qt.NoModifier, at(0.5, 0.5)); pump(0.1)
for k in range(1, 6):
    QTest.mouseMove(win, at(0.5 - 0.04 * k, 0.5 + 0.06 * k)); pump(0.05)
QTest.mouseRelease(win, Qt.LeftButton, Qt.NoModifier, at(0.3, 0.8)); settle()
c = {k["frame"]: k for k in pv.keyframes}[20].get("curve") or {}
b = (c.get("bends") or [None])[0]
check("dragging it up and left bends the line: Custom, no points, one bend, early, as strong as the keys allow",
      c.get("shape") == "custom" and c.get("points") == [] and b is not None and abs(b[0] - 0.3) < 0.03
      and abs(b[1] - __import__("core.common.keyframes", fromlist=["bend_limits"]).bend_limits(b[0], 0.0, 1.0)[1]) < 1e-6,
      str(c))
check("...and never past the second key's look", max(curve_fraction(t / 200, c) for t in range(201)) <= 1.0 + 1e-9
      and curve_fraction(0.9, c) < 0.999)
if track is not None:
    md = [track.values_at(f)["max_disp_var"] for f in range(5, 21)]
    want = [30 + 30 * curve_fraction((f - 5) / 15, c, seed=20) for f in range(5, 21)]
    check("...the preview's values follow the bend (a fast rise, then easing in)",
          max(abs(x - y) for x, y in zip(md, want)) < 1e-6 and md[3] - md[0] > md[-1] - md[-4],
          " ".join(f"{v:.0f}" for v in md))
QTest.mouseMove(win, QPoint(5, 5)); pump(0.3)
shot("6_bent")
QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, at(0.85, 0.95)); settle()
c = {k["frame"]: k for k in pv.keyframes}[20].get("curve") or {}
check("a point on the bent stretch splits it in two, both straight", len(c.get("points", [])) == 1 and not c.get("bends"),
      str(c))
pv.setKeyCurve(20, {"shape": "custom", "points": [], "bends": [[0.5, -0.3]]}); settle()
h = at(0.5, 0.2)
QTest.mouseMove(win, h); pump(0.3)
QTest.mouseDClick(win, Qt.LeftButton, Qt.NoModifier, h); settle()
c = {k["frame"]: k for k in pv.keyframes}[20].get("curve") or {}
check("double-clicking a handle straightens its stretch", not c.get("bends"), str(c))
# points and bends together, for the screenshots
pv.setKeyCurve(20, {"shape": "custom", "points": [[0.3, 0.9], [0.55, 0.25], [0.8, 0.75]],
                    "bends": [[0.6, 0.2], None, [0.4, -0.15], [0.5, 0.1]]}); settle()
shot("7_points_and_bends", hover=2)

studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
