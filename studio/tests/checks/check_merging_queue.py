"""Merging's Upcoming Jobs (user, 2026-09-27): the clip list under Options with
Batch Progress above it; idle, a click loads that clip into the preview; while
a job runs each row has its own bar. Real app, offscreen, over a TEMP copy of
the merge fixture; configs and last_jobs.json redirected."""
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
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, QObject, Q_ARG, QPoint, QPointF, Qt, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from studio import app as A

root = _fixtures.copy("merge", tmp / "fx")
before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))

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
    # Not QTest.qWait: it holds the GIL and starves the preview's worker.
    end = time.time() + sec
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.005)
def until(cond, sec=30):
    end = time.time() + sec
    while not cond() and time.time() < end:
        pump(0.05)
    return cond()
def find(it, pred):
    for c in it.childItems():
        if pred(c): return c
        r = find(c, pred)
        if r is not None: return r
def click_item(item):
    c = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(c.x()), int(c.y())))

pump(0.6)
mg = objs["Merging"]
st = mg.property("settings")
pv = mg.property("previewer")
stage = find(win.contentItem(), lambda c: c.objectName() == "stage")
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 3), Q_ARG("QVariant", True)); pump(1.2)
page = stage.property("pages").property(3).toQObject()
for k, v in {"inpainted_folder": str(root / "inpainted"), "original_folder": str(root / "orig"),
             "mask_folder": str(root / "mask"), "output_folder": str(tmp / "merged"),
             "encoding_encoder": "Force CPU", "codec": "H.264", "use_gpu": False,
             "preview_source": "Blended Image", "preview_size": "100%"}.items():
    st.setProperty(k, v)
pump(0.3)

def _walk(it, out=None):
    out = [] if out is None else out
    for c in it.childItems(): out.append(c); _walk(c, out)
    return out
def _is_in(c, anc):
    p = c
    while p is not None:
        if p == anc: return True
        p = p.parentItem()
    return False
# the list comes from the folders (user, 2026-09-27), before any Load Preview
jc = pv.property("jobClips")
check("Upcoming Jobs lists the clips once the folders are set -- no Load Preview",
      until(lambda: len(jc.property("clips")) == 2, 20) and not pv.property("loaded"), str(jc.property("clips")))
pump(0.5)
q = [c for c in _walk(page) if c.objectName() in ("jobQueue", "jobQueueCompact") and c.isVisible()][0]
rows = lambda: [c for c in _walk(q) if c.objectName() == "jobQueueRow" and c.isVisible()]
check("...as rows", len(rows()) == 2, str(len(rows())))
# each row numbered, before its still (user, 2026-09-28)
_num = lambda r: [x for x in _walk(r) if x.objectName() == "jobQueueNumber"][0]
_thb = lambda r: [x for x in _walk(r) if x.objectName() == "jobQueueThumbBox"][0]
_rs = sorted(rows(), key=lambda r: r.property("index"))
check("each clip numbered, before its still", [_num(r).property("text") for r in _rs] == ["1", "2"]
      and all(_num(r).mapToScene(QPointF(_num(r).width(), 0)).x() <= _thb(r).mapToScene(QPointF(0, 0)).x() for r in _rs),
      str([_num(r).property("text") for r in _rs]))
# each row's frame count under its name (user, 2026-09-27), counted after the list shows
import cv2 as _cv2
def _fc(p):
    c = _cv2.VideoCapture(p); n = int(c.get(_cv2.CAP_PROP_FRAME_COUNT)); c.release(); return n
until(lambda: all([x for x in _walk(r) if x.objectName() == "jobQueueFrames"][0].isVisible() for r in rows()), 20)
_ft = {r.property("index"): [x for x in _walk(r) if x.objectName() == "jobQueueFrames"][0].property("text") for r in rows()}
_want = {i: f"{_fc(p)} frames" for i, p in enumerate(jc.property("clipPaths"))}
# the clip's shape follows since 1ed03db ("48 frames  ·  16:9")
check("each clip shows its frame count", set(_ft) == set(_want) and all(str(_ft[i]).split("  ·  ")[0] == _want[i] for i in _want)
      and all(v != "0 frames" for v in _ft.values()), f"{_ft} vs {_want}")
_txt = [str(c.property("text")) for c in _walk(page) if c.property("text") is not None]
check("no Load Preview button and no Jump to box (the clip list loads a clip when picked)",
      not any(t in ("Load Preview", "Jump to:") for t in _txt), "")
# the heading line has the clip count; "Progress" labels the bars, its P over their left ends
_cc = [x for x in _walk(q) if x.objectName() == "queueClipCount"][0]
_hd = [x for x in _walk(q) if x.property("text") and "UPCOMING" in str(x.property("text")).upper()][0]
_pl = [x for x in _walk(q) if x.objectName() == "queueProgressLabel"][0]
_bar = [x for x in _walk(rows()[0]) if x.objectName() == "jobQueueBar"][0]
_y = lambda it: it.mapToScene(QPointF(0, it.height() / 2)).y()
check("the clip count is on the heading line", _cc.property("text") == "2 clips" and abs(_y(_cc) - _y(_hd)) < 3,
      f"{_cc.property('text')!r} y {_y(_cc):.0f} vs heading {_y(_hd):.0f}")
_px, _bx = _pl.mapToScene(QPointF(0, 0)).x(), _bar.mapToScene(QPointF(0, 0)).x()
check("...\"Progress\" above the bars, its P over their left ends", _pl.property("text") == "Progress" and abs(_px - _bx) <= 1,
      f"label x {_px:.1f}, bar x {_bx:.1f}")
# the left eye: Load Preview refuses (a popup) when the original videos are not there
_toasts = []; pv.toast.connect(lambda k, t, m: _toasts.append((k, t, m)))
(tmp / "no_orig").mkdir(exist_ok=True)
st.setProperty("original_folder", str(tmp / "no_orig")); pump(0.3)
# audit 2026-09-28 (minor): mA (dual) has no original here, but mB (quad)
# needs none -- one clip without its left eye blocked the preview of them all.
pv.load(); until(lambda: _toasts and not pv.property("loading"), 30); pump(0.5)
check("one clip without its left eye: the others still load, it is left out with a warning naming it",
      pv.property("loaded") and list(pv.property("clips")) == ["mB_320_inpainted_right_eye.mp4"]
      and bool(_toasts) and _toasts[-1][0] == "warning" and "mA_320" in _toasts[-1][2],
      f"loaded {pv.property('loaded')} clips {pv.property('clips')} toasts {str(_toasts[-1:])[:200]}")
_toasts.clear()
pv.showClip(str(root / "inpainted" / "mA_320_inpainted_right_eye.mp4")); until(lambda: _toasts, 30); pump(0.3)
check("no left eye: picking that clip is refused with a popup naming it", bool(_toasts) and _toasts[-1][1] == "Left eye not found"
      and "mA_320" in _toasts[-1][2], str(_toasts[-1:])[:200])
dlg = [c for c in win.findChildren(QObject) if c.metaObject().className().startswith("PromptDialog") and c.property("visible")]
check("...shown as a popup (not a toast)", bool(dlg) and dlg[0].property("title") == "Left eye not found",
      str([d.property("title") for d in dlg]))
# every prompt, in turn: questions asked while one is open now wait for it
# (PromptDialog queue, audit 2026-09-28) -- the warning above, then this one
for _ in range(6):
    dlg = [c for c in win.findChildren(QObject) if c.metaObject().className().startswith("PromptDialog") and c.property("visible")]
    if not dlg:
        break
    for d in dlg: QMetaObject.invokeMethod(d, "finish", Q_ARG("QVariant", 1))
    pump(0.4)
pump(0.5)
st.setProperty("original_folder", str(root / "orig")); pump(0.4)
pv.clear(); until(lambda: not pv.property("loaded"), 20); pump(0.3)   # nothing loaded again
# idle and nothing loaded: a click on a row loads the preview, on that clip
_ql = [x for x in _walk(q) if x.objectName() == "jobQueueList"][0]
QMetaObject.invokeMethod(_ql, "positionViewAtIndex", Q_ARG(int, 1), Q_ARG(int, 2)); pump(0.3)
_r1 = [r for r in rows() if r.property("index") == 1][0]
QTest.mouseMove(win, QPoint(5, 5)); pump(0.2)
click_item(_r1)
check("a click on a row loads the preview at that clip", until(lambda: pv.property("loaded") and not pv.property("loading")
      and pv.property("videoIndex") == 1, 60), f"loaded {pv.property('loaded')} index {pv.property('videoIndex')}")
check("preview loads the fixture's clips", len(pv.property("clips")) == 2, str(pv.property("clips")))
pump(0.5)
# the frame scrubber is live while held (user, 2026-09-27)
scr = find(page, lambda c: c.objectName() == "frameScrubber")
_seeks = []; _orig = pv.seek
_serial0 = pv.property("imageSerial")
_a = scr.mapToScene(QPointF(scr.property("leftPadding") + 6, scr.height() / 2))
_b = scr.mapToScene(QPointF(scr.width() * 0.8, scr.height() / 2))
_fr = []
QTest.mousePress(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(_a.x()), int(_a.y()))); pump(0.2)
for k in range(1, 11):
    QTest.mouseMove(win, QPoint(int(_a.x() + (_b.x() - _a.x()) * k / 10), int(_a.y()))); pump(0.15)
    _fr.append(pv.property("frame"))
_mid_serial = pv.property("imageSerial")
QTest.mouseRelease(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(_b.x()), int(_b.y()))); pump(1.5)
check("scrubber held: the preview follows it, frame by frame", len(set(_fr)) >= 3 and _mid_serial != _serial0
      and pv.property("frame") == round(scr.property("value")), f"frames {_fr}, serial {_serial0} -> {_mid_serial}")
QMetaObject.invokeMethod(pv, "seek", Q_ARG(int, 0)); pump(1.0)
def _walk(it, out=None):
    out = [] if out is None else out
    for c in it.childItems(): out.append(c); _walk(c, out)
    return out
opt = find(page, lambda c: c.objectName() == "mergeOptions")
if page.property("wide"):
    check("wide: Upcoming Jobs sits under Options", q.objectName() == "jobQueue"
          and abs(q.mapToScene(QPointF(0, 0)).x() - opt.mapToScene(QPointF(0, 0)).x()) < 2
          and q.mapToScene(QPointF(0, 0)).y() > opt.mapToScene(QPointF(0, 0)).y())
else:
    check("compact: Upcoming Jobs in the left column (the right is the tallest), taking the room left",
          q.objectName() == "jobQueueCompact" and q.height() >= q.property("implicitHeight"), f"{q.objectName()} h {q.height()}")
check("...and it adds no height to the page (fits a 1080p screen at 85%)", win.property("minimumHeight") * 0.85 <= 1000,
      str(win.property("minimumHeight")))
# at the page's least height the clip row stayed one tick bar below the box (user, 2026-10-10: "the
# alignment is off" in the manual's Merging shot): the list must end inside it
_h0 = win.property("height")
win.setProperty("height", win.property("minimumHeight")); pump(0.8)
_lst = find(q, lambda c: c.objectName() == "jobQueueList")
_qb = q.mapToScene(QPointF(0, q.height())).y()
_lb = _lst.mapToScene(QPointF(0, _lst.height())).y()
check("...at the page's least height the list ends inside the box", _lb <= _qb + 0.5 and _lst.height() >= q.property("minList") - 0.5,
      f"list bottom {_lb:.0f}, box bottom {_qb:.0f}, list {_lst.height():.0f}")
win.setProperty("height", _h0); pump(0.6)
names = [ [x for x in _walk(r) if x.objectName() == "jobQueueName"][0].property("text") for r in rows() ]
check("...a row per clip, the _inpainted_ suffix and extension left off", names == ["mA_320", "mB_320"], str(names))
bp = find(page, lambda c: c.objectName() == "queueBatchProgress")
check("...Batch Progress right above the list, and gone from Current Clip Info", bp is not None and bp.isVisible()
      and not any(c.property("text") == "Batch Progress:" and c.isVisible()
                  for c in _walk(page) if c.objectName() != "queueBatchProgress" and not _is_in(c, q)), "")

# taller rows, each with a still of its clip in place of the camera icon
thumbs = lambda: [[x for x in _walk(r) if x.objectName() == "jobQueueThumb"][0] for r in rows()]
until(lambda: all(t.property("progress") == 1.0 and t.property("implicitWidth") > 2 for t in thumbs()), 20)
check("rows are taller (52), each with a still of its clip", all(r.height() == 52 for r in rows())
      and all(t.property("progress") == 1.0 and t.property("implicitWidth") > 2 for t in thumbs())
      # no camera icon: the tick box's tick and the badges are icons of their own (2026-09-30)
      and not any(x.metaObject().className().startswith("Icon") for r in rows() for x in _walk(r)
                  if not any(_is_in(x, box) for box in _walk(r)
                             if box.objectName() in ("jobQueueCheck", "jobQueueFx", "jobQueueFxUnder"))),
      str([(r.height(), t.property("implicitWidth")) for r, t in zip(rows(), thumbs())]))
from studio import clip_thumbs as CT
im = CT.grab(str(root / "inpainted" / "mA_320_inpainted_right_eye.mp4"))
check("...the still: 72 px tall, from 40% into the clip", im is not None and im.height() == 72 and im.width() == 128,
      f"{im.width()}x{im.height()}" if im is not None else "none")
# the live preview's zoom: wheel zooms, drag pans, Fit (user, 2026-09-27)
import _zoom_checks
_zoom_checks.run(check, pump, settle if "settle" in dir() else (lambda: pump(0.5)), win, page, st, _walk)
before_idx = pv.property("videoIndex")
qlist = [x for x in _walk(q) if x.objectName() == "jobQueueList"][0]
QMetaObject.invokeMethod(qlist, "positionViewAtIndex", Q_ARG(int, 0), Q_ARG(int, 2)); pump(0.3)   # scrolled to, as a user would
click_item([r for r in rows() if r.property("index") == 0][0]); until(lambda: pv.property("videoIndex") == 0, 30); pump(0.5)
check("idle: clicking a row loads that clip into the preview", pv.property("videoIndex") == 0,
      f"{before_idx} -> {pv.property('videoIndex')}")
check("...and marks it", [r.property("selected") for r in sorted(rows(), key=lambda r: r.property("index"))] == [True, False])
# while a job runs: no loading, each row its own bar (done / running / waiting)
ab = find(page, lambda c: c.objectName() == "actionBar")
q.setProperty("busy", True); pump(0.1)
# the page's side, as Merging sets it while a job runs (see MergingPage: running /
# working / progress from mg.clipName and JobStats), driven directly here
q.setProperty("running", "mB_320_inpainted_right_eye.mp4"); pump(0.1)   # the preview's clip, as a job starts
q.setProperty("running", "mA_320_inpainted_right_eye.mp4"); q.setProperty("working", True); q.setProperty("progress", 0.6); pump(0.2)
bars = lambda: [round([x for x in _walk(r) if x.objectName() == "jobQueueBar"][0].property("value"), 2)
                for r in sorted(rows(), key=lambda r: r.property("index"))]
check("job running: the clip being merged follows the render, the next waits (the preview's clip is not 'done')",
      bars() == [0.6, 0.0], str(bars()))
q.setProperty("running", "mB_320_inpainted_right_eye.mp4"); q.setProperty("progress", 0.2); pump(0.2)
check("...moving on: the first is done, the second follows", bars() == [1.0, 0.2], str(bars()))
click_item([r for r in rows() if r.property("index") == 1][0]); pump(1.0)
check("...and a click does nothing while it runs", pv.property("videoIndex") == 0, str(pv.property("videoIndex")))
# the preview lets go of its clips for the job; the list keeps them, bars and all
jc.hold(True)                      # as the controller does while busy
pv.clear(); pump(0.8)
check("...the preview released for the job: the list stays, bars and all", len(rows()) == 2 and bars() == [1.0, 0.2]
      and not pv.property("loaded"), f"{len(rows())} rows, {bars()}")
jc.hold(False)
q.setProperty("busy", False); pump(0.2)
# another chapter (user, 2026-09-27): with a clip previewed, the folders move to
# another chapter; a click on ITS first clip must show it the first time (v1's
# loader kept the old clip open when the path it landed on was "the last one")
import shutil as _sh
chB = tmp / "chapterB"; chB.mkdir(exist_ok=True)
for f in (root / "inpainted").iterdir():
    if f.name.startswith("mB_"): _sh.copy(f, chB / f.name)
click_item([r for r in rows() if r.property("index") == 0][0]); until(lambda: pv.property("videoIndex") == 0, 30); pump(0.8)
was = mg.property("clipName")
st.setProperty("inpainted_folder", str(chB)); pump(0.3)
until(lambda: len(jc.property("clips")) == 1, 20); pump(0.5)
_serial = pv.property("imageSerial")
click_item(rows()[0])
until(lambda: "mB_" in str(mg.property("clipName")) and not pv.property("loading"), 60); pump(0.8)
check("another chapter: its clip shows on the first click", "mA_" in str(was) and "mB_" in str(mg.property("clipName"))
      and pv.property("imageSerial") != _serial and str(chB) in str(list(pv.property("clipPaths"))[pv.property("videoIndex")]),
      f"{was} -> {mg.property('clipName')}")
# ...and the picture is that clip's, not the old one's under the new name: the
# same frame loaded fresh must look the same
import hashlib as _hl
def _pic():
    im = pv.provider._images.get("frame")
    return _hl.md5(bytes(im.constBits())[:im.sizeInBytes()]).hexdigest() if im is not None else None
pv.seek(0); until(lambda: not pv.property("loading"), 20); pump(1.0)
after_click = _pic()
pv.clear(); pump(1.0)
QMetaObject.invokeMethod(pv, "showClip", Q_ARG(str, str(chB / sorted(x.name for x in chB.iterdir() if "_inpainted_" in x.name)[0])))
until(lambda: pv.property("loaded") and not pv.property("loading"), 60); pv.seek(0); pump(1.5)
check("...its own picture, as a fresh load of it draws", after_click is not None and after_click == _pic(),
      f"{after_click} vs {_pic()}")
st.setProperty("inpainted_folder", str(root / "inpainted")); pump(0.5)
pv.clear(); pump(0.5)
# ---- the sidecar badge (user, 2026-09-28, as Splatting's): a clip whose merge
# sidecar exists (<core>.fssidecar in the inpainted folder, as Grain Match saves it)
until(lambda: len(rows()) == 2, 15)
from studio.clip_queue import merge_core_name
_first = sorted(pv.property("jobClips").property("clipPaths"))[0]
_sc = Path(_first).parent / (merge_core_name(_first) + ".fssidecar")
_sc.write_text('{"grain_strength": 0.3}')
pv.property("jobClips").rescan(); pump(1.2)
_badge = lambda r: bool([x for x in _walk(r) if x.objectName() == "fxSidecar"][0].isVisible())
_got = [_badge(r) for r in sorted(rows(), key=lambda r: r.property("index"))]
check("a clip with a merge sidecar shows the sidecar badge, the other none", _got == [True, False], str(_got))
_sc.unlink()
pv.property("jobClips").refreshFx(); pump(0.5)
_got = [_badge(r) for r in sorted(rows(), key=lambda r: r.property("index"))]
check("...gone once the sidecar is", _got == [False, False], str(_got))
studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, " | ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
