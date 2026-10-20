"""Wheel over a slider or dial while the page scrolls (bug sweep 2026-10-09, A10).

Offscreen, on Splatting: in a window too short for the page, a wheel notch
over a slider scrolls the page and leaves the slider's value alone; once the
slider is clicked (has focus) the wheel steps it again. In a window the page
fits, the wheel steps a slider under the pointer as before (v1's "wheel = one
step").
"""
import os, sys, tempfile, hashlib, time
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
from PySide6.QtCore import (QCoreApplication, QEventLoop, QMetaObject, QObject, QPoint, QPointF, Q_ARG, Qt,
                            qInstallMessageHandler)
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication, QWheelEvent
from studio import app as A

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
objs["Shell"].setProperty("uiScale", "1.0")
win = engine.rootObjects()[0]
fails = []


def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok:
        fails.append(label)


def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.005)


def walk(it, out):
    for c in it.childItems():
        out.append(c); walk(c, out)
    return out


def wheel_at(item, notches=-1):
    """`notches` wheel clicks (negative: towards the user, i.e. scroll down) over the item's middle."""
    p = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    g = win.mapToGlobal(QPoint(int(p.x()), int(p.y())))
    ev = QWheelEvent(p, QPointF(g), QPoint(0, 0), QPoint(0, 120 * notches), Qt.NoButton, Qt.NoModifier,
                     Qt.NoScrollPhase, False)
    QCoreApplication.sendEvent(win, ev); pump(0.25)


def scroller():
    return [c for c in walk(win.contentItem(), []) if c.objectName() == "stageScroll"][0]


#: Splatting's dials and sliders: DialField / SliderRow and the page's wrappers of them
KINDS = ("SpDial", "DialField", "SpSlider", "SliderRow")


def a_slider():
    """A visible, enabled dial or slider on the page, inside the visible viewport."""
    sc = scroller()
    top = sc.mapToScene(QPointF(0, 0)).y()
    for c in walk(win.contentItem(), []):
        # one that can move both ways: at an end of its range, Qt passes the wheel on anyway
        if c.metaObject().className().split("_QML")[0] in KINDS and c.isVisible() and c.isEnabled()                 and c.property("from") < c.property("value") < c.property("to"):
            y = c.mapToScene(QPointF(0, c.height() / 2)).y()
            if top + 20 < y < top + sc.height() - 20:
                return c
    return None


def inner_slider(row):
    # the Basic style's Slider / Dial (QML types "Slider_QML..." / "Dial_QML..."), or the C++ ones
    return [c for c in walk(row, []) if c.metaObject().className().split("_QML")[0] in
            ("Slider", "Dial", "QQuickSlider", "QQuickDial")][0]


win.resize(1400, 1300); win.show(); pump(1.0)
stage = [c for c in walk(win.contentItem(), []) if c.objectName() == "stage"][0]
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 1), Q_ARG("QVariant", True)); pump(1.0)

# ---- a window too short for the page: the page scrolls
win.resize(1400, 520); pump(0.8)
sc = scroller()
check("the Splatting page scrolls in a short window", bool(sc.property("scrollable")),
      f"content {sc.property('contentHeight')} / {sc.height()}")
row = a_slider()
check("...a slider is in view", row is not None)
if row is not None:
    sc.setProperty("contentY", 150.0); pump(0.3)        # part-way down, so the page can go either way
    row = a_slider()
    before, y0 = row.property("value"), sc.property("contentY")
    wheel_at(row, +1)
    check("a wheel notch over the slider scrolls the page", sc.property("contentY") < y0,
          f"{y0} -> {sc.property('contentY')}")
    check("...and leaves the slider's value alone", row.property("value") == before,
          f"{before} -> {row.property('value')}")
    row = a_slider()
    s = inner_slider(row)
    s.forceActiveFocus(); pump(0.2)
    before, y0 = row.property("value"), sc.property("contentY")
    wheel_at(row, +1)
    check("once clicked (focused), the wheel steps it", row.property("value") != before,
          f"{before} -> {row.property('value')}")

# ---- a window the page fits: the wheel steps a slider under the pointer, as before
win.resize(1400, 1600); pump(0.8)
win.contentItem().forceActiveFocus(); pump(0.2)
check("the page fits a tall window", not sc.property("scrollable"))
row = a_slider()
if row is not None:
    before = row.property("value")
    wheel_at(row, +1)
    check("...and a wheel notch over a slider steps it (v1's wheel = one step)", row.property("value") != before,
          f"{before} -> {row.property('value')}")

studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
