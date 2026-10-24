"""Upcoming Jobs scrolls to its very last clip with badges on (user, 2026-10-24: "the very bottom row
of the list cuts off the last clip, i can't scroll further down than this"). A row with its badge line
is taller, so the list only estimates the rows it hasn't built and shifts originY as it learns better;
the wheel scrolled within 0 .. contentHeight - height and stopped short of the last row (or ran past
it). Splatting, real app, offscreen, 46 copies of the small fixture clip: badges on the first 10, then
on every other one; the wheel to the end puts the last row's bottom on the list's, and back to the top
the first row's top on its top. Configs and last_jobs.json redirected.

    python studio/tests/checks/check_queue_scroll_end.py
"""
import os, sys, json, tempfile, hashlib, time, shutil
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
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, QPoint, QPointF, Qt, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication, QWheelEvent
from studio import app as A

N = 46
FIX = tmp / "fx"; _fixtures.copy("small", FIX)
src, dep = tmp / "src.mp4", tmp / "dep.mp4"
shutil.copy(sorted((FIX / "src").glob("*.mp4"))[0], src)
shutil.copy(sorted((FIX / "depth").glob("*_depth.mp4"))[0], dep)
for f in list((FIX / "src").glob("*.mp4")) + list((FIX / "depth").glob("*_depth.mp4")):
    f.unlink()
for i in range(N):
    shutil.copy(src, FIX / "src" / f"clip{i:02d}.mp4"); shutil.copy(dep, FIX / "depth" / f"clip{i:02d}_depth.mp4")

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
objs["Shell"].setProperty("uiScale", "1.0")
win = engine.rootObjects()[0]
win.resize(1700, 1300); win.show()
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
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 1), Q_ARG("QVariant", False)); pump(0.8)
sp = objs["Splatting"]; st = sp.property("settings")
for k, v in {"input_source_clips": str(FIX / "src"), "input_depth_maps": str(FIX / "depth"),
             "output_splatted": str(FIX / "out"), "multi_map": False}.items():
    st.insert(k, v.replace("\\", "/") if isinstance(v, str) else v)
pump(0.3); sp.save(); pump(0.5)
cq = sp.preview.queue
page = stage.property("pages").property(1).toQObject()
sq = [c for c in walk(page, []) if c.objectName() == "splatJobQueue"][0]
lst = [c for c in walk(sq, []) if c.objectName() == "jobQueueList"][0]


def badges(rows):
    """Frame Break + keyframes (the badge line under the name) on these clips, a plain sidecar on the rest."""
    for i in range(N):
        data = {"convergence_plane": 0.5}
        if i in rows:
            data.update(frame_break_bars=0.1, keyframe_track={"a": 1})
        (FIX / "depth" / f"clip{i:02d}_depth.fssidecar").write_text(json.dumps(data))
    cq.rescan()
    end = time.time() + 20
    while time.time() < end and len(cq.property("clips")) < N:
        pump(0.1)
    pump(0.5); cq.refreshFx(); pump(1.0)


def wheel(notches, down=True):
    c = lst.mapToScene(QPointF(lst.property("width") / 2, lst.property("height") / 2))
    for _ in range(notches):
        ev = QWheelEvent(c, c, QPoint(0, 0), QPoint(0, -120 if down else 120), Qt.NoButton, Qt.NoModifier,
                         Qt.NoScrollPhase, False)
        QGuiApplication.sendEvent(win, ev); pump(0.25)
    pump(1.0)


def row_at(index):
    rows = [r for r in walk(lst, []) if r.objectName() == "jobQueueRow" and r.property("index") == index]
    return rows[0] if rows else None


for label, rows in (("badges on the first 10 clips", set(range(10))), ("badges on every other clip", set(range(0, N, 2)))):
    badges(rows)
    fx = [list(f) for f in cq.property("clipFx")]
    check(f"{label}: {N} rows, the badge line on {len(rows)}", len(fx) == N and sum(1 for f in fx if f[1]) == len(rows),
          str(sum(1 for f in fx if f[1])))
    wheel(70, down=True)
    last = row_at(N - 1)
    bot = last.mapToItem(lst, 0, last.property("height")).y() if last else None
    check("...the wheel to the end: the last clip whole, its bottom on the list's",
          last is not None and abs(bot - lst.property("height")) <= 1.0,
          f"last row ends {bot} of {lst.property('height'):.0f} (originY {lst.property('originY'):.0f}, "
          f"contentY {lst.property('contentY'):.0f}, contentHeight {lst.property('contentHeight'):.0f})")
    wheel(70, down=False)
    first = row_at(0)
    top = first.mapToItem(lst, 0, 0).y() if first else None
    check("...and back to the top: the first clip's top on the list's", first is not None and abs(top) <= 1.0, f"first row at {top}")

studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
