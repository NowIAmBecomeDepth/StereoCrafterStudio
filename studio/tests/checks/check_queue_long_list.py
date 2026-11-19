"""A long Upcoming Jobs list keeps up with the job (tester, 2026-11-18: a DVD batch of ~6400 clips froze the
window -- "Python is not responding" -- while the job ran on; the window caught up hundreds of clips late, then
froze again). The UI watchdog's stack: the UI thread inside ClipQueue.clips, called from DVD's _on_clip.

Cause: JobQueue's "keep the job's clip in view" ran `queue.src.clips.findIndex(...)` on every clip change. A
Python list property read straight from QML is a live reference: every element findIndex touched read the whole
Python list again -- N x N. Measured: 200 clips 0.16 s a clip, 800 2.4 s, 1600 10.5 s (6400: minutes).

Real app, offscreen, the DVD tab's own list given CLIPS made-up names (no files), DVD marked busy; the running
clip moved to clips near the end of the list, each move timed. Configs and last_jobs.json redirected.

    python studio/tests/checks/check_queue_long_list.py
"""
import hashlib
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
os.environ["CUDA_VISIBLE_DEVICES"] = "-1"
os.environ["STEREOCRAFTER_OFFLINE"] = "1"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
rec = REPO / "last_jobs.json"; h0 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()  # noqa: E402,E702
import studio.settings_store as ss  # noqa: E402
ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh  # noqa: E402
jh._RECORD_PATH = str(tmp / "last_jobs.json")
from PySide6.QtCore import QCoreApplication, QEventLoop  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from studio import app as A  # noqa: E402

CLIPS = int(os.environ.get("CHECK_CLIPS", "1500"))      # CHECK_CLIPS=6400: the tester's size
LIMIT_S = 0.5                       # one clip change; the old code took ~9 s here
fails = []


def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok:
        fails.append(label)


def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10)
        time.sleep(0.005)


def walk(it, out):
    for c in it.childItems():
        out.append(c)
        walk(c, out)
    return out


qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
win.resize(1700, 1200)
win.show()
pump(1.0)

dvd = objs["DVD"]
q = dvd.queue
names = [f"Person of Interest S02E01 1920x1080_V1-{i:04d}.mov" for i in range(CLIPS)]
q._paths = [f"D:/Shows/POI/S02E01/{n}" for n in names]
q._fx = [[] for _ in names]
q._frames = [0] * CLIPS
q._ratios = [""] * CLIPS
q.changed.emit()
pump(0.8)

jq = [c for c in walk(win.contentItem(), []) if c.objectName() == "dvdJobQueue"]
check("DVD's Upcoming Jobs list is built", len(jq) == 1)
lst = [c for c in walk(jq[0], []) if c.objectName() == "jobQueueList"] if jq else []
check(f"...and holds the {CLIPS} clips", bool(lst) and lst[0].property("count") == CLIPS,
      str(lst[0].property("count") if lst else None))

dvd._task = "check"                 # busy: the list follows the job's clip
dvd.stateChanged.emit()
pump(0.3)
took = []
for i in (CLIPS - 30, CLIPS - 20, CLIPS - 10):
    t = time.perf_counter()
    dvd._on_clip(names[i], "1920x1080", "131")        # what the batch emits as it starts a clip
    took.append(time.perf_counter() - t)
    pump(0.2)
check(f"each clip change keeps up (under {LIMIT_S} s with {CLIPS} clips)", max(took) < LIMIT_S,
      ", ".join(f"{s:.3f} s" for s in took))
y = lst[0].property("contentY") if lst else 0
check("...and the job's clip is scrolled into view", y > 0, f"contentY {y}")

dvd._task = ""
dvd.stateChanged.emit()
pump(0.2)
h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
check("real last_jobs.json unchanged", h0 == h1)
print("ALL OK" if not fails else f"FAILED: {fails}", flush=True)     # os._exit skips the flush
os._exit(1 if fails else 0)
