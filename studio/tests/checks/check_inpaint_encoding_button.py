"""Inpainting's Encoding Options (user, 2026-10-30): out of Settings, a thin solid accent button at the
right end of the V1 / V2 tabs -- Splatting's and Merging's button, thinner to sit in that row. It opens
Inpainting's Encoding dialog on V1 and V2 alike.

    python studio/tests/checks/check_inpaint_encoding_button.py
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
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, QObject, QPointF, Q_ARG, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
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


def under(cls_prefix):
    return [c for c in walk(win.contentItem(), []) if c.metaObject().className().startswith(cls_prefix)]


pump(0.8)
stage = [c for c in walk(win.contentItem(), []) if c.objectName() == "stage"][0]
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 2), Q_ARG("QVariant", True)); pump(0.8)
page = under("InpaintingPage")[0]
button = [c for c in walk(page, []) if c.objectName() == "encodingButton"]
check("one Encoding Options button on the Inpainting tab", len(button) == 1, str(len(button)))
button = button[0]
tabs = [c for c in page.childItems() if c.metaObject().className().startswith("StageTabBar")][0]

b0, b1 = button.mapToItem(page, QPointF(0, 0)), button.mapToItem(page, QPointF(button.width(), button.height()))
t0, t1 = tabs.mapToItem(page, QPointF(0, 0)), tabs.mapToItem(page, QPointF(tabs.width(), tabs.height()))
check("...in the V1 / V2 row: centred on the tabs", abs((b0.y() + b1.y()) / 2 - (t0.y() + t1.y()) / 2) <= 1.5,
      f"button {b0.y():.0f}-{b1.y():.0f}, tabs {t0.y():.0f}-{t1.y():.0f}")
check("...at its right end, clear of the tabs", b0.x() > t1.x() and page.width() - b1.x() <= 30,
      f"button {b0.x():.0f}-{b1.x():.0f}, tabs end {t1.x():.0f}, page {page.width():.0f}")
check("...says Encoding Options with the film icon", button.property("text").startswith("Encoding Options")
      and button.property("iconGlyph") != "", button.property("text"))

QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 1), Q_ARG("QVariant", True)); pump(0.8)
splat = [c for c in walk(under("SplattingPage")[0], []) if c.objectName() == "encodingButton"][0]
check("...the solid accent kind Splatting's is", button.property("kind") == "filled" == splat.property("kind")
      and button.property("solid") is True, f"{button.property('kind')} / {splat.property('kind')}")
check("...and thinner than Splatting's", button.height() < splat.height(), f"{button.height()} vs {splat.height()}")
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 2), Q_ARG("QVariant", True)); pump(0.8)

dialogs = [o for o in win.findChildren(QObject) if o.metaObject().className().startswith("EncodingDialog")]
mine = [d for d in dialogs if d.property("ctl") is objs["Inpainting"]]
check("Inpainting's Encoding dialog is there", len(mine) == 1, str(len(mine)))
for engine_i, name in ((0, "V1"), (1, "V2")):
    page.setProperty("engine", engine_i); pump(0.6)
    check(f"{name}: the button showing", button.isVisible())
    QMetaObject.invokeMethod(button, "clicked"); pump(0.6)
    check(f"{name}: it opens Inpainting's Encoding dialog", bool(mine) and mine[0].property("opened") is True,
          str([d.property("opened") for d in dialogs]))
    for d in dialogs:
        QMetaObject.invokeMethod(d, "close")
    pump(0.4)
page.setProperty("engine", 0); pump(0.3)

studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
