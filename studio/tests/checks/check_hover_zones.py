"""Tooltips only over the setting itself (user, 2026-09-30): in the real app
offscreen, a setting row's help shows with the pointer on its label's text or
on its control -- not anywhere else on the row's line (the empty space to the
right of a short field). The same for a checkbox row. Configs and
last_jobs.json redirected."""
import os, sys, tempfile, hashlib, time
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, str(Path(__file__).parent))
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
tmp = Path(tempfile.mkdtemp())
import _fixtures
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, QPoint, QPointF, qInstallMessageHandler
from PySide6.QtTest import QTest
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from studio import app as A

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
def find_all(it, pred, out=None):
    out = [] if out is None else out
    for c in it.childItems():
        if pred(c): out.append(c)
        find_all(c, pred, out)
    return out
stage = find_all(win.contentItem(), lambda c: c.objectName() == "stage")[0]
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 0), Q_ARG("QVariant", True)); pump(1.0)
win.setProperty("userSized", True); win.setWidth(1600); win.setHeight(1000); pump(0.8)
page = stage.property("pages").property(0).toQObject()

def tip_of(item):
    return [c for c in item.childItems() if c.metaObject().className().startswith("HoverTip")][0]
def shows_at(item, x, y):
    QTest.mouseMove(win, QPoint(5, 5)); pump(0.4)
    p = item.mapToScene(QPointF(x, y))
    QTest.mouseMove(win, QPoint(int(p.x()), int(p.y()))); pump(1.1)          # past the 600 ms delay
    return bool(tip_of(item).property("showing"))

row = [r for r in find_all(page, lambda c: c.metaObject().className().startswith("FieldRow")
                           and c.property("label") == "Inference Steps")][0]
lbl = [c for c in row.childItems() if c.metaObject().className().startswith("QQuickText")][0]
h = row.height() / 2
# the columns are fixed-width since Upcoming Jobs sits beside them (2026-10-10): ~190 px past the label
check("a setting row is wider than its label and field (room to the right)",
      row.width() > row.property("labelWidth") + 150, f"row {row.width()}")
check("over the label's text: the help shows", shows_at(row, min(lbl.property("contentWidth"), 60) / 2, h))
check("over the field: the help shows", shows_at(row, row.property("labelWidth") + 8 + 20, h))
check("on the empty rest of the row's line: no help", not shows_at(row, row.width() - 30, h))
check("in the label's column but past its text: no help",
      lbl.property("contentWidth") + 12 < row.property("labelWidth")
      and not shows_at(row, lbl.property("contentWidth") + 8, h),
      f"text {lbl.property('contentWidth')} of {row.property('labelWidth')}")

cf = [c for c in find_all(page, lambda c: c.property("key") == "latent_checkpoint_var"
                          and c.property("text") == "Save Checkpoint")][0]
box = [c for c in cf.childItems() if c.metaObject().className().startswith("AnimCheck")][0]
check("a checkbox row: over the box and its text, the help shows", shows_at(cf, box.width() / 2, cf.height() / 2))
check("...on the empty rest of its line: no help", not shows_at(cf, cf.width() - 20, cf.height() / 2),
      f"row {cf.width()} box {box.width()}")

check("no QML warnings", not warns, str(warns[:3]))
h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
check("the real last_jobs.json untouched", h0 == h1)
print("PASS" if not fails else f"FAIL ({len(fails)})")
_fixtures.exit_now(0 if not fails else 1)
