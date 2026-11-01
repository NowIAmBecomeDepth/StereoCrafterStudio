"""What the check_send_back_* checks share (user, 2026-10-31: "Rerun in <stage before>"):
the real app offscreen, configs and last_jobs.json redirected before anything is built,
and the clip lists' right-click menu driven as a user does it -- a right-click on the row,
then the menu's item. Imported with `from _send_back_rig import *` so `tmp` is the check's
own (the fixtures' clean-up looks for it there). Files only -- no job runs, no GPU.
"""
import os, sys, tempfile, hashlib, time, shutil
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, str(Path(__file__).parent))
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
tmp = Path(tempfile.mkdtemp())
# never the real models folder (memory: a check once deleted the real SAM 3 weights)
os.environ["STEREOCRAFTER_WEIGHTS"] = str(tmp / "weights")
import _fixtures
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, QObject, Q_ARG, QPoint, QPointF, Qt, qInstallMessageHandler
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
    if not ok:
        fails.append(label)


def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.005)


def until(cond, sec=20):
    end = time.time() + sec
    while not cond() and time.time() < end:
        pump(0.05)
    return cond()


def find_all(it, pred, out=None):
    out = [] if out is None else out
    for c in it.childItems():
        if pred(c):
            out.append(c)
        find_all(c, pred, out)
    return out


def one(root, name):
    got = [c for c in find_all(root, lambda c: c.objectName() == name) if c.isVisible()]
    return got[0] if got else None


def centre(item):
    c = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    return QPoint(int(c.x()), int(c.y()))


n = lambda p: os.path.normcase(os.path.normpath(str(p)))
stage = find_all(win.contentItem(), lambda c: c.objectName() == "stage")[0]


def show(i):
    QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", i), Q_ARG("QVariant", True)); pump(1.0)


def current_stage():
    return int(stage.property("current"))


win.setProperty("userSized", True); win.setHeight(1600); pump(0.5)


def queue_item(page_i, name=None):
    """The visible clip list on stage `page_i`'s page."""
    show(page_i)
    qs = [q for q in find_all(win.contentItem(), lambda c: c.metaObject().className().startswith("JobQueue"))
          if q.isVisible() and q.property("src") is not None and (name is None or q.objectName() == name)]
    return qs[0] if qs else None


def names_of(src):
    return [str(c) for c in src.property("clips")]


def archived_of(src):
    return {str(c): bool(a) for c, a in zip(src.property("clips"), src.property("clipArchived"))}


def ticked_of(src):
    return sorted(str(c) for c, t in zip(src.property("clips"), src.property("checked")) if t)


def rows(q):
    return [r for r in find_all(q, lambda c: c.objectName() == "jobQueueRow") if r.isVisible()]


def row_of(q, name):
    lst = one(q, "jobQueueList")
    i = [k for k, c in enumerate(names_of(q.property("src"))) if name in c][0]
    QMetaObject.invokeMethod(lst, "positionViewAtIndex", Q_ARG(int, i), Q_ARG(int, 2)); pump(0.3)
    return [r for r in rows(q) if name in str(r.property("modelData"))][0]


def tick(q, name):
    src = q.property("src")
    path = [p for p in src.property("clipPaths") if name in os.path.basename(str(p))][0]
    src.setChecked(path, True); pump(0.2)


def menu(q, name, act=None):
    """Right-click the row: (opened, the action texts offered); with `act`, choose that item."""
    m = [o for o in q.findChildren(QObject) if o.objectName() == "jobQueueRowMenu"][0]
    end = time.time() + 12
    while True:                          # a toast may sit over the list for a moment
        QTest.mouseClick(win, Qt.RightButton, Qt.NoModifier, centre(row_of(q, name))); pump(0.5)
        if m.property("opened") or time.time() > end:
            break
        pump(1.0)
    items = [o for o in m.findChildren(QObject) if o.objectName().startswith("jobQueueAction_")]
    opened = m.property("opened") is True
    texts = [str(o.property("text")) for o in items]
    hit = [o for o in items if o.objectName() == "jobQueueAction_" + str(act)]
    if hit:
        QMetaObject.invokeMethod(hit[0], "click"); pump(0.8)
    else:
        QMetaObject.invokeMethod(m, "close"); pump(0.3)
    return opened, texts


def show_finished(q, on=True):
    """The list's Show Finished button, pressed until the list says `on`."""
    b = one(q, "queueShowFinished")
    if b is None:
        return False
    if bool(q.property("src").property("showFinished")) != on:
        QMetaObject.invokeMethod(b, "clicked"); pump(0.3)
    return bool(q.property("src").property("showFinished")) == on


def video(src: Path, dest: Path) -> Path:
    """A small real clip at `dest` (a fixture's, copied)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return dest


def finish():
    check("no QML warnings", not warns, str(warns[:3]))
    h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
    check("the real last_jobs.json untouched", h0 == h1)
    print("PASS" if not fails else f"FAIL ({len(fails)})", fails)
    _fixtures.exit_now(0 if not fails and h0 == h1 else 1)
