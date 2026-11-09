"""DepthCrafter's Upcoming Jobs: right-click -> Render as 2D Full SBS (No Depth) (user, 2026-11-08:
end credits with no depth), in the real app offscreen:

* it asks first where to send it: Merging's Output Folder filled in, the file named as Merging
  names a Full SBS; Cancel writes nothing;
* Render writes the clip into that folder as a flat Full SBS (Merging's encoder), then the clip
  moves to finished and leaves the list;
* a folder typed in the dialog instead is where it goes.

Configs and last_jobs.json redirected; a TEMP copy of the small fixture.
"""
import os, sys, tempfile, hashlib, time, types
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")     # nothing here needs the GPU
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, str(Path(__file__).parent))
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
tmp = Path(tempfile.mkdtemp())
import _fixtures
FIX = tmp / "fx"; _fixtures.copy("small", FIX)
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, QObject, Q_ARG, QPoint, QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtGui import QGuiApplication
# DepthCrafter's model is never loaded here: stood in for, so nothing can
fake = types.ModuleType("depthcrafter.depthcrafter_logic"); fake.DepthCrafterDemo = object
sys.modules["depthcrafter.depthcrafter_logic"] = fake
from studio import app as A
from core.common import flat_sbs

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
def until(cond, sec=60):
    end = time.time() + sec
    while not cond() and time.time() < end:
        pump(0.05)
    return cond()
def find_all(it, pred, out=None):
    out = [] if out is None else out
    for c in it.childItems():
        if pred(c): out.append(c)
        find_all(c, pred, out)
    return out
def one(root, name):
    got = [c for c in find_all(root, lambda c: c.objectName() == name) if c.isVisible()]
    return got[0] if got else None
def centre(item):
    c = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    return QPoint(int(c.x()), int(c.y()))
def names(q):
    return [str(n) for n in (q.property("src").property("clips") or [])]
def row_of(q, name):
    lst = one(q, "jobQueueList")
    i = [k for k, n in enumerate(names(q)) if name in n][0]
    QMetaObject.invokeMethod(lst, "positionViewAtIndex", Q_ARG(int, i), Q_ARG(int, 2)); pump(0.3)
    return [r for r in find_all(q, lambda c: c.objectName() == "jobQueueRow")
            if r.isVisible() and name in str(r.property("modelData"))][0]
def action(q, name, act):
    m = [o for o in q.findChildren(QObject) if o.objectName() == "jobQueueRowMenu"][0]
    end = time.time() + 12
    while True:
        QTest.mouseClick(win, Qt.RightButton, Qt.NoModifier, centre(row_of(q, name))); pump(0.5)
        if m.property("opened") or time.time() > end:
            break
        pump(1.0)
    items = [o for o in m.findChildren(QObject) if o.objectName().startswith("jobQueueAction_")]
    texts = [str(o.property("text")) for o in items]
    hit = [o for o in items if o.objectName() == "jobQueueAction_" + act]
    if hit:
        QMetaObject.invokeMethod(hit[0], "click"); pump(0.5)
    return texts
n = lambda p: os.path.normcase(os.path.normpath(str(p)))
stage = find_all(win.contentItem(), lambda c: c.objectName() == "stage")[0]
win.setProperty("userSized", True); win.setWidth(1215); win.setHeight(1100); pump(0.5)
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 0), Q_ARG("QVariant", True)); pump(1.0)

dc = objs["DepthCrafter"]; dst = dc.property("settings")
page = stage.property("pages").property(0).toQObject()
for k, v in {"input_dir_or_file_var": str(FIX / "src"), "output_dir": str(tmp / "dc_out")}.items():
    dst.setProperty(k, v)
dc.save()
MERGED = tmp / "Merged"
mst = objs["Merging"].property("settings")
for k, v in {"output_folder": str(MERGED), "codec": "H.264", "encoding_encoder": "Force CPU",
             "output_container": "MP4"}.items():
    mst.setProperty(k, v)
dq = one(page, "dcJobQueue")
until(lambda: names(dq) == ["clipA.mp4", "clipB.mp4"], 20)
dlg = [o for o in win.findChildren(QObject) if o.objectName() == "flatSbsDialog"][0]
def part(name):
    got = [o for o in win.findChildren(QObject) if o.objectName() == name]
    return got[0] if got else None

texts = action(dq, "clipA", "flat")
check("right-click offers Render as 2D Full SBS (No Depth)", "Render as 2D Full SBS (No Depth)" in texts, str(texts))
check("...it asks first: the dialog opens", until(lambda: dlg.property("opened"), 5))
check("...Merging's Output Folder filled in", part("flatSbsFolder") is not None
      and n(part("flatSbsFolder").property("text")) == n(MERGED),
      str(part("flatSbsFolder").property("text")) if part("flatSbsFolder") else "no field")
check("...the file named as Merging names a Full SBS", part("flatSbsFile") is not None
      and part("flatSbsFile").property("text") == "clipA_640_merged_full_sbs.mp4",
      str(part("flatSbsFile").property("text")) if part("flatSbsFile") else "no name")
QMetaObject.invokeMethod(part("flatSbsCancel"), "click"); pump(0.8)
check("Cancel: closed, nothing written, no job", not dlg.property("visible") and not MERGED.exists()
      and not dc.property("busy") and names(dq) == ["clipA.mp4", "clipB.mp4"])

action(dq, "clipA", "flat"); until(lambda: dlg.property("opened"), 5)
QMetaObject.invokeMethod(part("flatSbsGo"), "click")
until(lambda: dc.property("busy"), 10); until(lambda: not dc.property("busy"), 60); pump(1.0)
out = MERGED / "clipA_640_merged_full_sbs.mp4"
info = flat_sbs.probe(str(out)) if out.exists() else {}
check("Render writes it into Merging's Output Folder, twice as wide", out.exists()
      and (info.get("w"), info.get("h")) == (1280, 360), str(info))
check("...then the clip moves to finished and leaves the list",
      (FIX / "src" / "finished" / "clipA.mp4").exists() and until(lambda: names(dq) == ["clipB.mp4"], 20), str(names(dq)))

OTHER = tmp / "Elsewhere"
action(dq, "clipB", "flat"); until(lambda: dlg.property("opened"), 5)
part("flatSbsFolder").setProperty("text", str(OTHER).replace("\\", "/")); pump(0.2)
QMetaObject.invokeMethod(part("flatSbsGo"), "click")
until(lambda: dc.property("busy"), 10); until(lambda: not dc.property("busy"), 60); pump(1.0)
check("a folder typed in the dialog instead is where it goes",
      (OTHER / "clipB_640_merged_full_sbs.mp4").exists() and not (MERGED / "clipB_640_merged_full_sbs.mp4").exists(),
      str(list(OTHER.iterdir())) if OTHER.exists() else "none")

h1 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
print("last_jobs unchanged:", h1 == h0)
if h1 != h0: fails.append("last_jobs.json changed")
print("FAILS", len(fails), fails, flush=True)
_fixtures.remove_after_exit([tmp])
_fixtures.exit_now(1 if fails else 0)
