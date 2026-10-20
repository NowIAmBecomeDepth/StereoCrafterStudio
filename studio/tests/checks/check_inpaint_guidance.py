"""Inpainting's Guidance Scale (user, 2026-10-09; moved 2026-10-10): on the page,
in Parameters under Original Input Bias -- not in Settings any more; hidden in
Beginner Mode; 1.0 by default (one UNet pass a step), with a plain tooltip; a
typed value reaches the settings, a non-number is refused; the Single Pass
switch it replaced is gone.
"""
import os, sys, tempfile, hashlib, time, json
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
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, QObject, QPoint, QPointF, Q_ARG, Qt, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from studio import app as A

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
objs["Shell"].setProperty("uiScale", "1.0")
win = engine.rootObjects()[0]
win.resize(1400, 950); win.show()
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


def everything():
    root = win.contentItem()
    while root.parentItem():
        root = root.parentItem()
    return walk(root, [])


def named(n):
    return [c for c in everything() if c.objectName() == n and c.isVisible()]


def click(item):
    p = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(p.x()), int(p.y()))); pump(0.4)


def type_into(box, text):
    # as typing then Enter does: the box writes back on commit() (TextBox.qml)
    box.setProperty("text", text)
    QMetaObject.invokeMethod(box, "commit"); pump(0.3)


pump(1.0)
stage = named("stage")[0]
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 2), Q_ARG("QVariant", True)); pump(0.8)
ip = objs["Inpainting"]
st = ip.property("settings")
rows = named("guidanceScaleRow")
check("the Inpainting page has a Guidance Scale row", len(rows) == 1)
bias = [c for c in everything() if c.isVisible() and c.property("label") == "Original Input Bias"]
if rows and bias:
    g, b = rows[0].mapToScene(QPointF(0, 0)), bias[0].mapToScene(QPointF(0, 0))
    check("...in Parameters, right under Original Input Bias", abs(g.x() - b.x()) < 2 and 0 < g.y() - b.y() < 80,
          f"bias at {b.x():.0f},{b.y():.0f}; guidance at {g.x():.0f},{g.y():.0f}")
else:
    check("...in Parameters, right under Original Input Bias", False, f"{len(rows)} rows, {len(bias)} bias")
box = named("guidanceScaleBox")[0]
check("...1.0 by default (one pass a step)", box.property("text") == "1.0" and st.value("guidance_scale") == "1.0",
      repr(box.property("text")))
tip = str(rows[0].property("help") or "") if rows else ""
check("...with a plain tooltip: 1.0 the fastest, above it twice the work, results almost the same",
      "At 1.0 (the default)" in tip and "30-45% longer" in tip and "almost the same" in tip, tip[:90])
type_into(box, "1.5")
check("typing 1.5 sets it", st.value("guidance_scale") == "1.5", repr(st.value("guidance_scale")))
type_into(box, "abc")
check("...text that isn't a number is refused", st.value("guidance_scale") == "1.5", repr(st.value("guidance_scale")))
type_into(box, "1.0"); pump(0.2)
img = win.grabWindow()
shot = Path(os.environ.get("CHECK_SHOT", str(tmp / "inpaint_page.png")))
img.save(str(shot)); print("  shot:", shot)
# Beginner Mode hides it
beg = objs["Beginner"]
beg.setEnabled(True); pump(0.6)
check("Beginner Mode hides it", not named("guidanceScaleRow") and not named("guidanceScaleBox"))
beg.setEnabled(False); pump(0.6)
check("...and shows it again when off", len(named("guidanceScaleRow")) == 1)
# not in Settings any more
dlg = win.findChild(QObject, "settingsDialog")
QMetaObject.invokeMethod(dlg, "open"); pump(0.5)
dlg.setProperty("current", 0); pump(0.6)          # the stage first, then Studio, Appearance, Shortcuts, GPU
in_dlg = [c for c in walk(dlg.property("contentItem"), []) if c.objectName() == "guidanceScaleBox"] \
    if dlg.property("contentItem") is not None else []
check("Settings > Inpainting has no Guidance Scale any more", not [c for c in in_dlg if c.isVisible()]
      and len([c for c in everything() if c.objectName() == "guidanceScaleBox" and c.isVisible()]) == 1)
items = [c.property("text") for c in named("settingsItem")]
check("...and no Single Pass switch", not any("Single Pass" in str(t) for t in items), str(items))
QMetaObject.invokeMethod(dlg, "close"); pump(0.4)
studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
