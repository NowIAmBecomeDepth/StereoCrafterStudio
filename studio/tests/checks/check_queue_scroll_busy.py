"""Upcoming Jobs scrolls while a job runs (user, 2026-10-05: "allow the clip
list to be scrollable while a job runs in splatting or merging"). Splatting
and Merging, real app, offscreen: each list filled with 60 made-up clip
names (nothing on disk is read or written), the mouse wheel over it idle and
with the stage's busy flag up; while busy the rows stay hands-off (no ticks,
no click-to-load). Configs and last_jobs.json redirected."""
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
from PySide6.QtGui import QColor, QGuiApplication, QWheelEvent
from PySide6.QtTest import QTest
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
def wheel_over(item, notches=-2, wait=0.2):
    c = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    pos = QPointF(c.x(), c.y())
    ev = QWheelEvent(pos, win.mapToGlobal(pos), QPoint(0, 0), QPoint(0, 120 * notches), Qt.NoButton,
                     Qt.NoModifier, Qt.NoScrollPhase, False)
    QCoreApplication.sendEvent(win, ev)
    pump(wait)

pump(0.6)
stage = [c for c in find_all(win.contentItem(), lambda c: c.objectName() == "stage")][0]
N = 60
# Merging places the list differently wide and compact: both
for idx, name, width in ((1, "Splatting", 0), (3, "Merging", 1300), (3, "Merging", 2400)):
    print(name, f"at {width} px" if width else "")
    ctl = objs[name]
    if width:
        win.setWidth(width); pump(0.6)
    QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", idx), Q_ARG("QVariant", True)); pump(1.2)
    page = [p for p in find_all(win.contentItem(), lambda c: c.metaObject().className().startswith("MergingPage"))
            if p.isVisible()]
    if width and page:
        print("   wide layout:", page[0].property("wide"))
    q = ctl.property("previewer").property("jobClips") if ctl.property("previewer") is not None else ctl.preview.queue
    q._paths = [str(tmp / f"made_up_clip_{i:03}.mp4") for i in range(N)]
    q._depths = [""] * N
    q._fx = [[] for _ in range(N)]
    q._frames = [0] * N
    q.changed.emit(); pump(0.8)
    lists = [l for l in find_all(win.contentItem(), lambda c: c.objectName() == "jobQueueList")
             if l.isVisible() and l.property("count") == N]
    check("its Upcoming Jobs list shows the 60 clips", len(lists) == 1, str(len(lists)))
    if not lists:
        continue
    lst = lists[0]
    check("...more than it can show at once", lst.property("contentHeight") > lst.height() + 100,
          f"{lst.property('contentHeight'):.0f} in {lst.height():.0f}")
    lst.setProperty("contentY", 0); pump(0.1)
    wheel_over(lst)
    idle_y = lst.property("contentY")
    check("idle, the wheel scrolls it", idle_y > 0, f"contentY {idle_y:.0f}")
    lst.setProperty("contentY", 0); pump(0.1)
    ctl._busy = True; ctl.stateChanged.emit(); pump(0.4)
    check("a job running (the stage busy)", bool(ctl.property("busy")))
    wheel_over(lst)
    busy_y = lst.property("contentY")
    check("...the wheel still scrolls it", busy_y > 0, f"contentY {busy_y:.0f}")
    wheel_over(lst, notches=2)
    check("...both ways", lst.property("contentY") < busy_y, f"{lst.property('contentY'):.0f}")
    # the scroll bar dragged, as well as the wheel
    lst.setProperty("contentY", 0); pump(0.1)
    bars = [b for b in find_all(lst, lambda c: "ScrollBar" in c.metaObject().className()
                                or "SlimScrollBar" in c.metaObject().className()) if b.isVisible()]
    if bars:
        bar = bars[0]
        a = bar.mapToScene(QPointF(bar.width() / 2, 4))
        b = bar.mapToScene(QPointF(bar.width() / 2, bar.height() - 4))
        QTest.mousePress(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(a.x()), int(a.y()))); pump(0.05)
        for k in range(1, 6):
            y = a.y() + (b.y() - a.y()) * k / 5
            QTest.mouseMove(win, QPoint(int(a.x()), int(y))); pump(0.03)
        QTest.mouseRelease(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(b.x()), int(b.y()))); pump(0.2)
    check("...and so does dragging its scroll bar", bool(bars) and lst.property("contentY") > 0,
          f"{len(bars)} bar(s), contentY {lst.property('contentY'):.0f}")
    ticks = [t for t in find_all(lst, lambda c: c.objectName() == "jobQueueCheck") if t.isVisible()]
    check("...while the rows stay hands-off: no ticking", ticks and not any(t.property("enabled") for t in ticks), str(len(ticks)))
    rows = [r for r in find_all(lst, lambda c: c.objectName() == "jobQueueRow") if r.isVisible()]
    # the left-click (load) only: the right-click menu works during a job by design (Play, Open in Explorer)
    taps = [h for r in rows for h in r.children() if "TapHandler" in h.metaObject().className()
            and h.objectName() != "jobQueueRowMenuArea"]
    check("...and no click-to-load", taps and not any(h.property("enabled") for h in taps), str(len(taps)))
    ctl._busy = False; ctl.stateChanged.emit(); pump(0.3)
    q._paths, q._depths, q._fx, q._frames = [], [], [], []
    q.changed.emit(); pump(0.3)

# ---- smooth scrolling (user, 2026-10-05): a notch glides; quick notches add up;
# it glides during Splatting's and Merging's own jobs (not heavy on the GPU), and
# jumps while a heavy stage runs (Theme.durLight)
print("Smooth scrolling")
win.setWidth(1300); pump(0.4)
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 1), Q_ARG("QVariant", True)); pump(1.0)
sp = objs["Splatting"]
q = sp.preview.queue
q._paths = [str(tmp / f"made_up_clip_{i:03}.mp4") for i in range(N)]
q._depths, q._fx, q._frames = [""] * N, [[] for _ in range(N)], [0] * N
q.changed.emit(); pump(0.8)
lst = [l for l in find_all(win.contentItem(), lambda c: c.objectName() == "jobQueueList")
       if l.isVisible() and l.property("count") == N][0]
row = 52 + 6
def glide(notches=-1):
    """contentY seen right after the wheel, and once the glide is over."""
    wheel_over(lst, notches, wait=0.03)
    mid = lst.property("contentY")
    pump(0.45)
    return mid, lst.property("contentY")
lst.setProperty("contentY", 0); pump(0.1)
mid, end = glide()
check("idle, a notch glides: part-way at first, two rows once done", 0 < mid < end and abs(end - 2 * row) < 0.5,
      f"{mid:.0f} then {end:.0f}")
lst.setProperty("contentY", 0); pump(0.1)
wheel_over(lst, -1, wait=0.03); wheel_over(lst, -1, wait=0.45)
check("...two quick notches add up to four rows", abs(lst.property("contentY") - 4 * row) < 0.5,
      f"{lst.property('contentY'):.0f}")
lst.setProperty("contentY", 0); pump(0.1)
sp._busy = True; sp.stateChanged.emit(); pump(0.3)
mid, end = glide()
check("during Splatting's own job it still glides", 0 < mid < end and abs(end - 2 * row) < 0.5, f"{mid:.0f} then {end:.0f}")
sp._busy = False; sp.stateChanged.emit(); pump(0.3)
# A job's end reads the folder again (meant: for the clips it moved to finished/), and these made-up clips
# aren't on disk: listed again, or the next part scrolled an empty list
q._paths = [str(tmp / f"made_up_clip_{i:03}.mp4") for i in range(N)]
q._depths, q._fx, q._frames = [""] * N, [[] for _ in range(N)], [0] * N
q.changed.emit(); pump(0.8)
lst.setProperty("contentY", 0); pump(0.1)
ip = objs["Inpainting"]
ip._busy = True; ip.stateChanged.emit(); pump(0.3)
mid, end = glide()
check("while a heavy stage runs (Inpainting) it jumps: no animation", abs(mid - 2 * row) < 0.5 and mid == end,
      f"{mid:.0f} then {end:.0f}")
ip._busy = False; ip.stateChanged.emit(); pump(0.3)
q._paths, q._depths, q._fx, q._frames = [], [], [], []
q.changed.emit(); pump(0.3)

# ---- the batch count (user, 2026-10-05: Splatting's last batch's 29 / 29 sat over
# a new batch of 40). Splatting keeps its count after a run (Merging blanks its
# own); it stays up until the list holds other clips. The run stood in for: its
# controller's count and busy flag over a made-up list.
print("Batch count")
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 1), Q_ARG("QVariant", True)); pump(1.0)
sp = objs["Splatting"]
q = sp.preview.queue
def listed(names):
    q._paths = [str(tmp / n) for n in names]
    q._depths, q._fx, q._frames = [""] * len(names), [[] for _ in names], [0] * len(names)
    q.changed.emit(); pump(0.4)
shown = lambda: [t for t in find_all(win.contentItem(), lambda c: c.objectName() == "queueBatchProgress")  # noqa: E731
                 if t.isVisible()][0].property("text")
listed([f"batch_one_{i:02}.mp4" for i in range(29)])
sp._busy = True; sp._clip_count = "29 / 29"; sp.stateChanged.emit(); sp.infoChanged.emit(); pump(0.3)
check("a run shows its count", shown() == "29 / 29", repr(shown()))
sp._busy = False; sp.stateChanged.emit(); pump(0.3)
check("...which stays up after it", shown() == "29 / 29", repr(shown()))
q._frames = [7] * 29; q.changed.emit(); pump(0.3)
check("...the same clips re-counted keep it", shown() == "29 / 29", repr(shown()))
listed([f"batch_two_{i:02}.mp4" for i in range(40)])
check("...a new batch listed: the old count goes", shown() == "—", repr(shown()))
sp._busy = True; sp._clip_count = "1 / 40"; sp.stateChanged.emit(); sp.infoChanged.emit(); pump(0.3)
check("...and the next run shows its own", shown() == "1 / 40", repr(shown()))
sp._busy = False; sp._clip_count = "N/A"; sp.stateChanged.emit(); sp.infoChanged.emit(); pump(0.3)

# ---- finished rows (user, 2026-10-05): a clip the run has finished turns light
# green; the one it's on keeps the blue; the rest stay plain; cleared next run
print("Finished rows")
names = [f"green_{i}.mp4" for i in range(4)]
listed(names)
def row_colour(name):
    rows = [r for r in find_all(win.contentItem(), lambda c: c.objectName() == "jobQueueRow")
            if r.isVisible() and str(r.property("modelData")) == name]
    return rows[0].property("color") if rows else None
def greenish(c):
    return c is not None and c.green() > c.red() + 10 and c.green() > c.blue()
def on_clip(name):
    sp._info = dict(sp._info, filename=name); sp.infoChanged.emit(); pump(0.3)
plain = row_colour(names[3])
sp._busy = True; sp.stateChanged.emit(); pump(0.2)
on_clip(names[0]); on_clip(names[1]); on_clip(names[2])
check("the clips the run has finished are green", greenish(row_colour(names[0])) and greenish(row_colour(names[1])),
      f"{row_colour(names[0]).name(QColor.HexArgb)} {row_colour(names[1]).name(QColor.HexArgb)}")
check("...the one it's on keeps its own highlight", not greenish(row_colour(names[2])),
      row_colour(names[2]).name(QColor.HexArgb))
check("...the ones ahead stay plain", row_colour(names[3]) == plain and not greenish(row_colour(names[3])))
if os.environ.get("QUEUE_SHOT"): win.grabWindow().save(os.environ["QUEUE_SHOT"])
sp._busy = False; sp.stateChanged.emit(); pump(0.3)
listed(names)                  # the run's end read the folder again: the made-up clips listed again
check("...still green after the run", greenish(row_colour(names[0])))
sp._busy = True; sp._info = dict(sp._info, filename="N/A"); sp.stateChanged.emit(); sp.infoChanged.emit(); pump(0.3)
check("...cleared when the next run starts", not greenish(row_colour(names[0])) and not greenish(row_colour(names[1])))
sp._busy = False; sp.stateChanged.emit(); pump(0.3)
listed([])

# ---- a REAL Merging job (CPU, the merge fixture's two clips, a temp copy): the
# list scrolls while it runs, not only with the busy flag stood up
print("Merging, a real job")
import _fixtures
win.setWidth(1300); pump(0.6)
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 3), Q_ARG("QVariant", True)); pump(1.0)
mg = objs["Merging"]; st = mg.property("settings")
FIX = _fixtures.copy("merge", tmp / "mg")
for k, v in {"inpainted_folder": str(FIX / "inpainted"), "original_folder": str(FIX / "orig"),
             "mask_folder": str(FIX / "mask"), "output_folder": str(tmp / "mg_out"),
             "encoding_encoder": "Force CPU", "codec": "H.264", "use_gpu": False, "resume": False,
             "output_format": "Full SBS", "pad_to_16_9": False, "split_lr": False,
             "delete_sbs_after_split": False, "dnxhr_fullres_split": False}.items():
    st.setProperty(k, v)
q = mg.preview.queue
end = time.time() + 20
while len(q.property("clips")) != 2 and time.time() < end:
    pump(0.1)
lst = [l for l in find_all(win.contentItem(), lambda c: c.objectName() == "jobQueueList")
       if l.isVisible() and l.property("count") == 2]
check("the two fixture clips listed", len(lst) == 1, str(q.property("clips")))
if lst:
    lst = lst[0]
    over = lst.property("contentHeight") > lst.height() + 4
    check("...more than the list shows at once here", over, f"{lst.property('contentHeight'):.0f} in {lst.height():.0f}")
    mg.start()
    end = time.time() + 5
    while not mg.property("busy") and time.time() < end:
        pump(0.02)
    scrolled, tries = False, 0
    while mg.property("busy") and not scrolled and tries < 200:
        lst.setProperty("contentY", 0); pump(0.02)
        wheel_over(lst)
        scrolled = lst.property("contentY") > 0 and bool(mg.property("busy"))
        tries += 1
    check("...the wheel scrolls it while the real job runs", scrolled, f"after {tries} tries")
    end = time.time() + 180
    while mg.property("busy") and time.time() < end:
        pump(0.1)
    check("...and the job ends", not mg.property("busy"))

check("no QML warnings", not warns, "\n".join(warns[:5]))
h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
check("the real last_jobs.json untouched", h1 == h0)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(1 if fails else 0)   # not os._exit: the DLLs' teardown can crash after a green run
