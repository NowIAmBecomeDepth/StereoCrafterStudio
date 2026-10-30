"""Headless QML: Splatting's High VRAM Mode (user, 2026-10-30) in Process Resolution -- the box off by default,
Jobs at Once shown only while it's on, 2 to 4, and both kept in the stage's settings. Configs and
last_jobs.json redirected; nothing runs."""
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
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, QPoint, QPointF, Qt, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
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


def named(n):
    root = win.contentItem()
    while root.parentItem():
        root = root.parentItem()
    return [c for c in walk(root, []) if c.objectName() == n]


pump(1.0)
QMetaObject.invokeMethod(named("stage")[0], "show", Q_ARG("QVariant", 1), Q_ARG("QVariant", True)); pump(0.8)
sp = objs["Splatting"]
st = sp.property("settings")
box, jobs = named("splatHighVram"), named("splatHighVramJobs")
check("High VRAM Mode's box and Jobs at Once are on the page", bool(box) and bool(jobs))
if box and jobs:
    box, jobs = box[0], jobs[0]
    check("off by default", st.property("high_vram_mode") is False and not box.property("checked"))
    check("...Jobs at Once hidden while off", not jobs.isVisible())
    p = box.mapToScene(QPointF(12, box.height() / 2))
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(p.x()), int(p.y()))); pump(0.6)
    check("ticked: on", st.property("high_vram_mode") is True)
    check("...Jobs at Once shown, 2 to begin with", jobs.isVisible() and jobs.property("currentText") == "2",
          str(jobs.property("currentText")))
    check("...offering 2, 3 and 4", [int(v) for v in jobs.property("model").toVariant()] == [2, 3, 4]
          if hasattr(jobs.property("model"), "toVariant") else list(jobs.property("model")) == [2, 3, 4])
    QMetaObject.invokeMethod(jobs, "activated", Q_ARG(int, 2)); pump(0.4)
    check("picked 4: kept", int(st.property("high_vram_jobs")) == 4, str(st.property("high_vram_jobs")))
    sp.save(); pump(0.3)
    import json
    saved = json.loads((ss.CONFIG_DIR / "splatting.json").read_text(encoding="utf-8"))
    check("...and saved with the stage", saved.get("high_vram_mode") is True and saved.get("high_vram_jobs") == 4,
          str({k: saved.get(k) for k in ("high_vram_mode", "high_vram_jobs")}))

# ---- Upcoming Jobs: every job's clip running, with its own bars; the finished ones marked
import _fixtures
fx = _fixtures.copy("small", tmp / "fx")
sp._set("input_source_clips", str(fx / "src").replace("\\", "/"))
sp._set("input_depth_maps", str(fx / "depth").replace("\\", "/"))
end = time.time() + 20
rows = []
while time.time() < end:
    pump(0.3)
    rows = [r for r in walk(win.contentItem(), []) if r.metaObject().indexOfProperty("current") >= 0
            and r.metaObject().indexOfProperty("finished") >= 0 and r.property("modelData")]
    if len({str(r.property("modelData")) for r in rows}) >= 2:
        break
names = sorted({str(r.property("modelData")) for r in rows})
check("Upcoming Jobs lists the two clips", len(names) >= 2, str(names))
if len(names) >= 2:
    a, b = names[0], names[1]

    def row(n):
        return next(r for r in rows if str(r.property("modelData")) == n)
    sp._busy = True
    sp._jobs = [{"name": a, "frames": [24, 0], "done": [False, False]},
                {"name": b, "frames": [48, 12], "done": [True, False]}]
    sp.stateChanged.emit(); sp.infoChanged.emit(); pump(0.6)
    check("two jobs: both clips shown running", row(a).property("current") and row(b).property("current"))
    def _passes(r):
        v = r.property("passes")
        return list(v.toVariant() if hasattr(v, "toVariant") else v or [])
    q = next(r for r in walk(win.contentItem(), [])   # Splatting's list: the one with H and L passes
             if r.metaObject().indexOfMethod("passProgressOf(QVariant,QVariant,QVariant)") >= 0 and _passes(r) == ["H", "L"])
    from PySide6.QtCore import Q_RETURN_ARG
    def bar(n, i):
        return QMetaObject.invokeMethod(q, "passProgressOf", Q_RETURN_ARG("QVariant"), Q_ARG("QVariant", n),
                                        Q_ARG("QVariant", i), Q_ARG("QVariant", 48))
    check("...each with its own bars", abs(bar(a, 0) - 0.5) < 1e-6 and bar(a, 1) == 0 and bar(b, 0) == 1
          and abs(bar(b, 1) - 0.25) < 1e-6, f"{bar(a, 0)} {bar(a, 1)} {bar(b, 0)} {bar(b, 1)}")
    sp._jobs = [{"name": b, "frames": [48, 30], "done": [True, False]}]
    sp._jobs_done = [a.rsplit(".", 1)[0]]
    sp.infoChanged.emit(); pump(0.6)
    check("one finished: marked finished, full bars; the other still running",
          row(a).property("finished") and not row(a).property("current") and bar(a, 0) == 1 and bar(a, 1) == 1
          and row(b).property("current"))
    sp._busy = False; sp._jobs = []; sp._jobs_done = []
    sp.stateChanged.emit(); sp.infoChanged.emit(); pump(0.3)

check("no QML warnings", not [w for w in warns if "EncodingDialog.qml" not in w], str(warns[:3]))
h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
check("the real last_jobs.json untouched", h0 == h1)
print("FAILS", len(fails), fails)
sys.stdout.flush()
os._exit(1 if fails else 0)
