"""Preview Brightness: a double-click on the slider resets it to 1.0 (user, 2026-10-11),
as the right-click does; a single click still just moves it. Splatting and Merging,
real app, offscreen, real mouse events. Configs and last_jobs.json redirected."""
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
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, QObject, QPoint, QPointF, Qt, Q_ARG
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from studio import app as A

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


def find_all(it, pred, out):
    for c in it.childItems():
        if pred(c):
            out.append(c)
        find_all(c, pred, out)
    return out


pump(1.0)
stage = find_all(win.contentItem(), lambda c: c.objectName() == "stage", [])[0]
for index, name in ((1, "Splatting"), (3, "Merging")):
    QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", index), Q_ARG("QVariant", True)); pump(1.0)
    pg = stage.property("pages").property(index).toQObject()
    st = objs[name].property("settings")
    rows = [r for r in find_all(pg, lambda c: c.property("key") == "preview_brightness", []) if r.isVisible()]
    check(f"{name}: the Preview Brightness slider", len(rows) == 1, str(len(rows)))
    row = rows[0]
    sl = [c for c in find_all(row, lambda c: c.objectName() == "rowSlider", [])]
    slider = sl[0] if sl else None
    check(f"{name}: it resets on a double-click", row.property("doubleClickResets") is True and slider is not None)
    p = slider.mapToScene(QPointF(slider.width() * 0.8, slider.height() / 2))
    at = QPoint(int(p.x()), int(p.y()))
    st.setProperty("preview_brightness", 2.0); pump(0.2)
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, at); pump(0.6)
    one = float(st.property("preview_brightness"))
    check(f"{name}: a single click moves it (not a reset)", abs(one - 1.0) > 0.2, f"{one}")
    QTest.mouseDClick(win, Qt.LeftButton, Qt.NoModifier, at); pump(0.4)
    check(f"{name}: a double-click resets it to 1.0", abs(float(st.property("preview_brightness")) - 1.0) < 1e-6,
          str(st.property("preview_brightness")))
    check(f"{name}: ...and the knob shows 1.0", abs(float(slider.property("value")) - 1.0) < 1e-6, str(slider.property("value")))
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, at); pump(0.6)
    check(f"{name}: a click after that moves it again", abs(float(st.property("preview_brightness")) - one) < 1e-6,
          str(st.property("preview_brightness")))

_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
