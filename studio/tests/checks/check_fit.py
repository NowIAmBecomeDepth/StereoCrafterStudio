"""v2 window fit: launch, segments on/off, stage switch, user size, Fit Window."""
import os, sys, tempfile, hashlib, importlib.util
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO))
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from PySide6.QtCore import QMetaObject, Q_ARG
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from studio import app as A

# reuse the overflow audit
spec = importlib.util.spec_from_file_location("mw", str(Path(__file__).with_name("measure_v2_width.py")))
src = Path(spec.origin).read_text()
audit_src = src[src.index("def eff_visible"):src.index("names = {")]

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
print("auto zoom on the offscreen screen:", win.property("autoZoom"), "screen h", win.property("screenHeight"))
objs["Shell"].setProperty("uiScale", "1.0")
win.show(); QTest.qWait(600)
from PySide6.QtCore import QPointF
def find(it, pred):
    for c in it.childItems():
        if pred(c): return c
        r = find(c, pred)
        if r is not None: return r
stage = find(win.contentItem(), lambda c: c.objectName() == "stage")
exec(audit_src)

def buttons(pg):
    out = []
    def walk(it):
        for ch in it.childItems():
            if ch.metaObject().className().startswith("AccentButton") and eff_visible(ch, pg) and ch.width() > 0:
                tl = ch.mapToItem(pg, QPointF(0, 0))
                out.append((ch.property("text"), tl.x(), tl.y(), ch.width(), ch.height()))
            walk(ch)
    ab = find(pg, lambda c: c.objectName() == "actionBar")
    walk(ab)
    return out
def overlaps(bs):
    bad = []
    for i in range(len(bs)):
        for j in range(i + 1, len(bs)):
            a, b = bs[i], bs[j]
            if a[1] < b[1] + b[3] and b[1] < a[1] + a[3] and a[2] < b[2] + b[4] and b[2] < a[2] + a[4]:
                bad.append((a[0], b[0]))
    return bad
def names(bs): return sorted(b[0] for b in bs)
fails = []
def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}")
    if not ok: fails.append(label)
def size(): return (win.width(), win.height())
def page(i): return stage.property("pages").property(i).toQObject()
def show(i):
    QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", i), Q_ARG("QVariant", True)); QTest.qWait(700)
def settle(ms=900): QTest.qWait(ms)

# The typed tips are random per run: one caught mid-resize read as clipped
# (seen once, under the parallel suite). Whether every tip fits its stage is
# check_tips_fit.py's job; here they are off.
objs["Shell"].setProperty("showTips", False)
show(0); settle()
st = page(0).property("st")
# the top row (stage tabs, Beginner Mode, the gear, the VRAM pill) is never
# squeezed: a page narrower than it fits to the row instead (2026-09-27)
import math
class _Fit(int):
    """== within 4 px of max(need, the header row now) -- the row's width
    moves a few px with the selected tab's bold label."""
    def __eq__(self, other): return abs(int(self) - int(other)) <= 4
    __hash__ = int.__hash__
fit = lambda need: _Fit(max(need, math.ceil(win.property("headerWidth"))))
print("   header row needs", math.ceil(win.property("headerWidth")), flush=True)
st.setProperty("process_as_segments_var", False); settle()
QMetaObject.invokeMethod(win, "fitToContent"); settle(300)
w, h = size()
check("DepthCrafter fits v1-ish width (or the top row)", fit(790) == w, f"{w}x{h} (v1 787x737)")
check("height is the page's need", h == win.property("minimumHeight"), f"min {win.property('minimumHeight')}")
check("no clipping", not audit(page(0)), str(audit(page(0))[:3]))

bs = buttons(page(0))
check("segments off: no segment buttons", "Re-Merge Segments" not in names(bs), str(names(bs)))
check("segments off: no overlapping buttons", not overlaps(bs), str(overlaps(bs)))
h_off = size()[1]
st.setProperty("process_as_segments_var", True); settle(1200)
w, h = size()
bs = buttons(page(0))
check("segments on: segment buttons shown", {"Re-Merge Segments", "Generate Seg Visuals"} <= set(names(bs)), str(names(bs)))
check("segments on: no overlapping buttons", not overlaps(bs), str(overlaps(bs)))
start_y = next(b[2] for b in bs if b[0] == "Start"); seg_y = next(b[2] for b in bs if b[0] == "Re-Merge Segments")
check("segment row is beneath Start", seg_y > start_y + 20, f"start y {start_y:.0f}, seg y {seg_y:.0f}")
check("window grew by the row", h > h_off, f"{h_off} -> {h}")
# (2026-10-10: Upcoming Jobs beside the columns -- the page asks for its own width)
seg_w = page(0).property("requiredWidth")
check("segments on widens for the third column", fit(seg_w) == w and seg_w > 1120, f"{w}x{h} (asks {seg_w}; v1 1067)")
check("no clipping, three columns", not audit(page(0)), str(audit(page(0))[:3]))
st.setProperty("process_as_segments_var", False); settle(1200)
check("segments off shrinks back", fit(790) == size()[0], str(size()))

show(2); settle()
w, h = size()
check("Inpainting fits", fit(850) == w, f"{w}x{h} (v1 787x789)")
check("no clipping on Inpainting", not audit(page(2)), str(audit(page(2))[:3]))
check("no overlapping buttons on Inpainting", not overlaps(buttons(page(2))), str(overlaps(buttons(page(2)))))
show(0); settle()
check("back on DepthCrafter re-fits", fit(790) == size()[0], str(size()))

drag_w = int(fit(850)) + 40
win.setProperty("width", drag_w); settle(300)
check("a dragged size is the user's", win.property("userSized") is True)
show(2); settle()
check("and is kept across a switch", size()[0] == drag_w, str(size()))
st.setProperty("process_as_segments_var", True); show(0); settle(1200)
check("but grown to what the page needs", _Fit(max(int(fit(seg_w)), drag_w)) == size()[0], str(size()))
st.setProperty("process_as_segments_var", False); settle(1200)
QMetaObject.invokeMethod(win, "fitToContent"); settle(300)
check("Fit Window to Content returns to the fit", fit(790) == size()[0] and not win.property("userSized"), str(size()))

# audit 2026-09-28 (minor): a HEIGHT-only drag was never the user's -- only a
# width change set userSized -- so the next stage switch snapped it back.
_w0, _h0 = size()
drag_h = _h0 + 60 if _h0 + 60 < win.property("screenRoomHeight") - 40 else _h0 - 60
win.setProperty("height", drag_h); settle(300)
check("a height-only drag is the user's", win.property("userSized") is True, f"{_h0} -> {size()[1]}")
show(2); settle(); show(0); settle()
check("...and is kept across a switch (grown, like a dragged width, only where a page needs more)",
      size()[1] >= drag_h and win.property("userSized") is True, f"dragged {drag_h}, now {size()}")
QMetaObject.invokeMethod(win, "fitToContent"); settle(300)

# The zoom: at 0.85 the fit is the 1.00 fit scaled, and pages keep their layout size.
QMetaObject.invokeMethod(win, "fitToContent"); QTest.qWait(300)
w1, h1 = size()
objs["Shell"].setProperty("uiScale", "0.85"); QTest.qWait(600)
w2, h2 = size()
check("0.85 scales the fit", abs(w2 - w1 * 0.85) <= 2 and abs(h2 - h1 * 0.85) <= 2, f"{(w1, h1)} -> {(w2, h2)}")
zr = find(win.contentItem(), lambda c: c.objectName() == "zoomRoot")
check("page keeps its 1.00 width", abs(stage.width() - w1) <= 2, f"stage {stage.width()} vs {w1}")
objs["Shell"].setProperty("uiScale", "auto"); QTest.qWait(300)
# audit 2026-09-28 (minor): UI Scale changed during a job refitted the window
# mid-job; the scale applies at once, the refit waits for the job to end
theme = engine.singletonInstance("Studio", "Theme")
objs["Shell"].setProperty("uiScale", "1.0"); QTest.qWait(400)
QMetaObject.invokeMethod(win, "fitToContent"); QTest.qWait(300)
before = size()                                  # a scale DOWN: the window may keep its size
theme.setProperty("calm", True); QTest.qWait(100)            # a job is running
objs["Shell"].setProperty("uiScale", "0.85"); QTest.qWait(600)
during = size()
theme.setProperty("calm", False); QTest.qWait(600)           # the job ended
after = size()
QMetaObject.invokeMethod(win, "fitToContent"); QTest.qWait(300)
check("UI Scale during a job: no refit until it ends, then the refit", during == before and after != before
      and after == size(), f"{before} -> during {during} -> after {after} (fit {size()})")
objs["Shell"].setProperty("uiScale", "auto"); QTest.qWait(300)
# maximised on a wide stage, another stage picked, then restored (user,
# 2026-09-28): Windows puts back the wide stage's size -- that is no size the
# user chose, so the window refits to the stage showing and keeps refitting
from PySide6.QtGui import QWindow
# the wide stage is Splatting (2026-10-10): DepthCrafter with segments on was, but
# the top row now sets every narrower stage's width -- DepthCrafter's three
# columns included -- so it was no wider than Inpainting and the check proved nothing
show(1); QMetaObject.invokeMethod(win, "fitToContent"); QTest.qWait(1200)
wide = size()
show(2); QMetaObject.invokeMethod(win, "fitToContent"); QTest.qWait(1200)
narrow = win.property("fitWidth")
show(1); QMetaObject.invokeMethod(win, "fitToContent"); QTest.qWait(1200)
check("Splatting fits wider than Inpainting (the next check's premise)", wide[0] > narrow + 20,
      f"Splatting {wide[0]}, Inpainting {narrow}")
win.showMaximized(); QTest.qWait(600)
show(2); QTest.qWait(600)
maxed = win.visibility() == QWindow.Visibility.Maximized
win.showNormal(); QTest.qWait(900)
check("maximised, stage changed, restored: refits to the stage showing" + ("" if maxed else " (platform could not maximise)"),
      not maxed or (abs(size()[0] - win.property("fitWidth")) <= 2 and size()[0] < wide[0] and not win.property("userSized")),
      f"wide {wide} -> {size()} (fit {win.property('fitWidth')}), userSized {win.property('userSized')}")
show(0); QTest.qWait(900)
check("...and keeps refitting on the next switch", not maxed or abs(size()[0] - win.property("fitWidth")) <= 2,
      f"{size()} (fit {win.property('fitWidth')})")
# moved to another screen (user, 2026-09-28): Windows resizes the window for the
# new screen's scale -- sometimes just before the move is reported -- and that
# is no size the user chose: once the move settles the window refits (and a
# size the user DID pick before the move is kept)
QMetaObject.invokeMethod(win, "fitToContent"); QTest.qWait(1200)
fw = win.property("fitWidth")
win.setProperty("width", fw + 137); QTest.qWait(100)          # Windows' DPI resize...
win.screenChanged.emit(win.screen()); QTest.qWait(1200)       # ...then the move is reported
check("moved to another screen: the window refits, the move's resize not taken as the user's",
      abs(win.width() - win.property("fitWidth")) <= 2 and not win.property("userSized"),
      f"{win.width()} (fit {win.property('fitWidth')}), userSized {win.property('userSized')}")
show(2); QTest.qWait(900); show(0); QTest.qWait(900)
check("...and keeps refitting on stage switches", abs(win.width() - win.property("fitWidth")) <= 2, f"{win.width()} vs {win.property('fitWidth')}")
win.setProperty("width", win.property("fitWidth") + 150); QTest.qWait(1200)   # the user drags it
dragged = win.width()
win.screenChanged.emit(win.screen()); QTest.qWait(1200)
check("...a size the user dragged before the move is kept", win.property("userSized") is True and win.width() == dragged,
      f"{dragged} -> {win.width()}, userSized {win.property('userSized')}")
QMetaObject.invokeMethod(win, "fitToContent"); QTest.qWait(300)
# maximised on one screen, then dragged by the title bar to another (user,
# 2026-09-28: 1080p -> 4K, Splatting): the drag restores the window, and it
# crosses to the other screen well within a second -- the screen move must not
# bring back a "user-sized" left over from the maximise, or no stage refits
win.setProperty("userSized", False); QTest.qWait(1000)
for order in ("echo first", "state first", "maximise size first"):
    if order == "maximise size first":                         # the maximised size landing before the state
        win.setProperty("width", win.property("fitWidth") + 500); QTest.qWait(10)
    win.showMaximized(); QTest.qWait(1200)                    # held maximised a while
    maxed = win.visibility() == QWindow.Visibility.Maximized
    if order != "state first":                                 # Windows' restore size lands first...
        win.setProperty("width", win.property("fitWidth") + 260); win.showNormal()
    else:                                                      # ...or the state change does
        win.showNormal(); QTest.qWait(30); win.setProperty("width", win.property("fitWidth") + 260)
    QTest.qWait(250)                                           # the drag reaches the other screen
    win.setProperty("width", win.property("fitWidth") + 90)    # its DPI resize
    win.screenChanged.emit(win.screen()); QTest.qWait(1500)
    show(2); QTest.qWait(900); show(1); QTest.qWait(900)
    check(f"maximised, dragged to another screen ({order}): stage switches still refit"
          + ("" if maxed else " (platform could not maximise)"),
          not maxed or (abs(win.width() - win.property("fitWidth")) <= 2 and not win.property("userSized")),
          f"{win.width()} (fit {win.property('fitWidth')}), userSized {win.property('userSized')}")
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails)
import _fixtures
_fixtures.exit_now(0 if not fails and _lj else 1)
