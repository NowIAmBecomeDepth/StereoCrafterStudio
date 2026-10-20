"""Upcoming Jobs reads the clip lists once per change (bug sweep 2026-10-09,
A2): each row read clipPaths / clipFrames / clipFx itself, and each read is a
fresh copy of the whole list from Python -- about 6N copies of N entries per
change, after every rescan, frame-count pass, sidecar save and clip move.
A 300-clip list here; the reads are counted. Offscreen, config and
last_jobs.json redirected.

    python studio/tests/checks/check_queue_reads.py
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
from PySide6.QtCore import Property, QCoreApplication, QEventLoop, QMetaObject, Q_ARG, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication

# count every read of the three lists from QML
import studio.clip_queue as CQ
reads = {"clipPaths": 0, "clipFrames": 0, "clipFx": 0}


def counted(name, fn):
    def get(self):
        reads[name] += 1
        return fn(self)
    return get


CQ.ClipQueue.clipPaths = Property("QVariantList", counted("clipPaths", lambda self: list(self._paths)),
                                  notify=CQ.ClipQueue.changed)
CQ.ClipQueue.clipFrames = Property("QVariantList", counted("clipFrames", lambda self: list(self._frames)),
                                   notify=CQ.ClipQueue.changed)
CQ.ClipQueue.clipFx = Property("QVariantList", counted("clipFx", lambda self: [list(f) for f in self._fx]),
                               notify=CQ.ClipQueue.changed)

from studio import app as A
qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
objs["Shell"].setProperty("uiScale", "1.0")
win = engine.rootObjects()[0]
win.resize(1600, 1000); win.show()
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


pump(0.8)
stage = [c for c in walk(win.contentItem(), []) if c.objectName() == "stage"][0]
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 1), Q_ARG("QVariant", True)); pump(1.0)
q = objs["Splatting"].preview.queue
N = 300
q._paths = [f"E:/clips/clip_{i:03d}.mp4" for i in range(N)]
q._depths = [f"E:/depth/clip_{i:03d}_depth.mp4" for i in range(N)]
q._frames = [48] * N
q._fx = [[False, False, False, False, False, False, False] for _ in range(N)]
q.changed.emit(); pump(1.0)
rows = [c for c in walk(win.contentItem(), []) if c.objectName() == "jobQueueRow"]
check("the list shows its rows", len(rows) > 3, str(len(rows)))
for k in reads:
    reads[k] = 0
q._frames = [49] * N                               # one change: a frame-count pass
q.changed.emit(); pump(1.0)
total = sum(reads.values())
print(f"   reads after one change, with {len(rows)} rows built: {reads}", flush=True)
check("each list is read from Python a few times per change, not once a row", total <= 12, str(reads))
check("...and the rows still show the new counts",
      any("49" in str(c.property("text") or "") for r in rows for c in walk(r, [])))

studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, " | ".join(studio_warns[:6]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
check("the real last_jobs.json untouched", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails else 1)
