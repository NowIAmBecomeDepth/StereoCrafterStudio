"""A dropdown's open list shows its options in full however narrow the box (user,
2026-10-11: in a small window Splatting's Front box shrinks, and the list cut its
options short too), real app, offscreen: Splatting's Front box made narrow, opened --
the list is as wide as its longest option, every row's text fits, the list stays
inside the window; a box already wide enough keeps a list of its own width.
Configs and last_jobs.json redirected.

    python studio/tests/checks/check_dropdown_list_width.py
"""
import os, sys, tempfile, hashlib, time
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
rec = REPO / "last_jobs.json"; h0 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, QObject, QPointF
from PySide6.QtGui import QGuiApplication
from studio import app as A

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
win.show()
fails = []
def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok: fails.append(label)
def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.005)
def tree(it, out):
    for c in it.childItems():
        out.append(c); tree(c, out)
    return out

pump(0.6)
objs["Shell"].openStage(1, -1); pump(0.8)
root = win.contentItem().parentItem() or win.contentItem()
box = next(i for i in tree(root, []) if i.objectName() == "flipFront" and i.isVisible())
full = int(box.property("fitWidth"))
box.setProperty("boxWidth", 130); pump(0.4)               # as a small window leaves it
check("the box is narrower than its options", box.width() < full - 40, f"{box.width():.0f} / {full}")
popup = next(o for o in box.findChildren(QObject) if "Popup" in o.metaObject().className())
QMetaObject.invokeMethod(popup, "open"); pump(0.6)
pw = float(popup.property("width"))
check("the open list is as wide as its longest option", pw >= full, f"{pw:.0f} / {full}")
lst = popup.property("contentItem")
rows = [c for c in tree(lst, []) if c.metaObject().className().startswith("QQuickText") and c.isVisible()]
cut = [r.property("text") for r in rows
       if r.property("text") and r.property("implicitWidth") > r.property("width") + 0.5]
check("every option's text fits its row", rows and not cut, f"{len(rows)} rows, cut: {cut}")
tl = popup.property("contentItem").mapToScene(QPointF(0, 0))
cw = win.contentItem().width()
check("...and the list stays inside the window", tl.x() >= 0 and tl.x() + pw <= cw + 1, f"{tl.x():.0f}+{pw:.0f} / {cw:.0f}")
QMetaObject.invokeMethod(popup, "close"); pump(0.4)

box.setProperty("boxWidth", full + 60); pump(0.4)          # wide enough already
QMetaObject.invokeMethod(popup, "open"); pump(0.6)
check("a box wide enough keeps a list of its own width", abs(float(popup.property("width")) - box.width()) < 1,
      f"{popup.property('width')} / {box.width()}")
QMetaObject.invokeMethod(popup, "close"); pump(0.3)

h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
print("last_jobs unchanged:", h0 == h1, flush=True)
print("FAILS", len(fails), fails, flush=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _fixtures  # noqa: E402
_fixtures.exit_now(1 if fails or h0 != h1 else 0)
