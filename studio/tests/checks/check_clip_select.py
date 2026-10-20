"""Ticking clips in Upcoming Jobs (user, 2026-09-30), Splatting and Merging,
in the real app offscreen: a tick box per row (its own click: the row's click
still loads the preview), Select All / Deselect All, Process Selected runs the
ticked clips only, and Move to Finished moves the ticked clips -- with what a
batch moves for each -- into finished/, after which they leave the list.
Real runs over TEMP copies of the small and merge fixtures; configs and
last_jobs.json redirected."""
import os, sys, tempfile, hashlib, time, glob
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, str(Path(__file__).parent))
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
tmp = Path(tempfile.mkdtemp())
import _fixtures
FIX = tmp / "fx"; _fixtures.copy("small", FIX)
MFX = tmp / "mfx"; _fixtures.copy("merge", MFX)
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, QPoint, QPointF, Qt, qInstallMessageHandler
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
def click(item):
    c = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(c.x()), int(c.y())))
    pump(0.3)
def rows(q):
    return sorted([r for r in find_all(q, lambda c: c.objectName() == "jobQueueRow") if r.isVisible()],
                  key=lambda r: r.property("index"))
def row_of(q, name):
    """The row, scrolled into view first (a short list shows only some)."""
    lst = one(q, "jobQueueList")
    names = [str(n) for n in q.property("src").property("clips")]
    i = [k for k, n in enumerate(names) if name in n][0]
    QMetaObject.invokeMethod(lst, "positionViewAtIndex", Q_ARG(int, i), Q_ARG(int, 2)); pump(0.3)  # Contain
    return [r for r in rows(q) if name in str(r.property("modelData"))][0]
def run_to_end(ctl, start, sec=300):
    start()
    until(lambda: ctl.property("busy"), 20)
    until(lambda: not ctl.property("busy"), sec)
    pump(1.0)
stage = find_all(win.contentItem(), lambda c: c.objectName() == "stage")[0]
def show(i):
    QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", i), Q_ARG("QVariant", True)); pump(1.0)
win.setProperty("userSized", True); win.setHeight(1600); pump(0.5)

# ================= Splatting =================
sp = objs["Splatting"]; pv = sp.preview
show(1)
for k, v in {"input_source_clips": str(FIX / "src").replace("\\", "/"), "input_depth_maps": str(FIX / "depth").replace("\\", "/"),
             "output_splatted": str(FIX / "out").replace("\\", "/"), "multi_map": False, "auto_save_sidecar": False,
             "move_to_finished": False, "enable_full_res": False, "enable_low_res": True, "dual_output": False,
             "auto_convergence_mode": "Off", "custom_clip_range": False}.items():
    sp._set(k, v)
jc = pv.property("jobClips")
jc.rescan()
sq = find_all(win.contentItem(), lambda c: c.objectName() == "splatJobQueue")[0]
check("Splatting: two clips listed, each with a tick box, none ticked",
      until(lambda: len(rows(sq)) == 2 and all(one(r, "jobQueueCheck") for r in rows(sq)), 30)
      and jc.property("checked") == [False, False])
check("...nothing ticked: Process Selected / Move to Finished there but off",
      one(sq, "queueProcessSelected").property("enabled") is False
      and one(sq, "queueMoveFinished").property("enabled") is False and one(sq, "queueBatchProgress") is not None)
bar = one(sq, "queueTickBar")
pump(1.0)       # the Flow wraps once the list's width has settled (it measured mid-layout)
check("...the tick buttons all inside the list's width (they wrap in a narrow column)",
      all(0 <= b.mapToItem(sq, QPointF(0, 0)).x() and b.mapToItem(sq, QPointF(b.width(), 0)).x() <= sq.width()
          for b in bar.childItems() if b.isVisible()),
      str([(round(b.mapToItem(sq, QPointF(0, 0)).x()), round(b.width())) for b in bar.childItems()]) + f" in {round(sq.width())}")
was_loaded = pv.property("loaded")
click(one(row_of(sq, "clipB"), "jobQueueCheck"))
check("ticking clipB ticks it only", jc.property("checked") == [False, True])
pump(1.0)
check("...and doesn't load it into the preview (the box is its own click)", pv.property("loaded") == was_loaded)
check("...Process Selected and Move to Finished on, the header counts it",
      one(sq, "queueProcessSelected").property("enabled") is True and one(sq, "queueMoveFinished").property("enabled") is True
      and "1 selected" in one(sq, "queueClipCount").property("text"), one(sq, "queueClipCount").property("text"))
click(one(sq, "queueSelectAll"))
check("Select All ticks every clip", jc.property("checked") == [True, True])
click(one(sq, "queueSelectNone"))
check("Deselect All clears them", jc.property("checked") == [False, False]
      and one(sq, "queueProcessSelected").property("enabled") is False)
click(one(row_of(sq, "clipB"), "jobQueueCheck"))
run_to_end(sp, lambda: click(one(sq, "queueProcessSelected")))
outs = sorted(os.path.basename(p) for p in glob.glob(str(FIX / "out" / "**" / "*.mp4"), recursive=True))
check("Process Selected renders the ticked clip only (clipB)", outs and all(o.startswith("clipB") for o in outs), str(outs))

click(one(row_of(sq, "clipB"), "jobQueueCheck"))                  # untick B
click(one(row_of(sq, "clipA"), "jobQueueCheck"))                  # tick A
click(one(sq, "queueMoveFinished"))
check("Move to Finished: clipA leaves the list", until(lambda: [str(r.property("modelData")) for r in rows(sq)] == ["clipB.mp4"], 30),
      str([str(r.property("modelData")) for r in rows(sq)]))
check("...its clip and depth map are in finished/, clipB's are not",
      (FIX / "src" / "finished" / "clipA.mp4").exists() and (FIX / "depth" / "finished" / "clipA_depth.mp4").exists()
      and (FIX / "src" / "clipB.mp4").exists() and not (FIX / "src" / "clipA.mp4").exists())
check("...and nothing is left ticked", jc.property("checkedCount") == 0)

# ================= Merging =================
mg = objs["Merging"]; st = mg.property("settings"); mpv = mg.property("previewer")
show(3)
for k, v in {"inpainted_folder": str(MFX / "inpainted"), "original_folder": str(MFX / "orig"),
             "mask_folder": str(MFX / "mask"), "output_folder": str(tmp / "merged"),
             "encoding_encoder": "Force CPU", "codec": "H.264", "use_gpu": False, "resume": False}.items():
    st.setProperty(k, v)
mjc = mpv.property("jobClips")
mjc.rescan()
mq = [q for q in find_all(win.contentItem(), lambda c: c.metaObject().className().startswith("JobQueue"))
      if q.isVisible() and q.property("pv") is not None and q.property("src") is not None
      and q.property("src").property("clips") is not None and q is not sq]
mq = [q for q in mq if q.property("ctl") is mg][0]
check("Merging: two clips listed with tick boxes", until(lambda: len(rows(mq)) == 2, 30)
      and all(one(r, "jobQueueCheck") for r in rows(mq)))
click(one(row_of(mq, "mB"), "jobQueueCheck"))
check("...ticking mB (scrolled into view) ticks it", mjc.checked_paths() and "mB" in mjc.checked_paths()[0])
run_to_end(mg, lambda: click(one(mq, "queueProcessSelected")))
merged = sorted(os.path.basename(p) for ext in ("mp4", "mov")
                for p in glob.glob(str(tmp / "merged" / "**" / f"*.{ext}"), recursive=True))
check("Process Selected merges the ticked clip only (mB)", merged and all(m.startswith("mB") for m in merged), str(merged))
click(one(row_of(mq, "mB"), "jobQueueCheck"))
click(one(row_of(mq, "mA"), "jobQueueCheck"))
click(one(mq, "queueMoveFinished"))
check("Move to Finished: mA leaves the list",
      until(lambda: [str(r.property("modelData")) for r in rows(mq)] == ["mB_320_inpainted_right_eye.mp4"], 30),
      str([str(r.property("modelData")) for r in rows(mq)]))
check("...its inpainted clip, splat and original are in finished/",
      (MFX / "inpainted" / "finished" / "mA_320_inpainted_right_eye.mp4").exists()
      and (MFX / "mask" / "finished" / "mA_320_splatted2.mp4").exists()
      and (MFX / "orig" / "finished" / "mA.mp4").exists()
      and (MFX / "mask" / "mB_320_splatted4.mp4").exists() and (MFX / "orig" / "mB.mp4").exists())

check("no QML warnings", not warns, str(warns[:3]))
h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
check("the real last_jobs.json untouched", h0 == h1)
print("PASS" if not fails else f"FAIL ({len(fails)})")
_fixtures.exit_now(0 if not fails else 1)
