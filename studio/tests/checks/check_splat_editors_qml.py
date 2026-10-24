"""Headless QML: the real app, Splatting page, the Hole Shift window driven
by mouse input over a TEMP copy of the fixture. Configs redirected."""
import os, sys, tempfile, hashlib, time, shutil
from pathlib import Path
import _fixtures
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
tmp = Path(tempfile.mkdtemp()); FIX = tmp / "fx"; _fixtures.copy("small", FIX)
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, Q_RETURN_ARG, QObject, QPoint, QPointF, Qt, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from studio import app as A
import numpy as np
import types
from core.common import dilate_regions
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
sp = objs["Splatting"]; pv = sp.preview
def settle(t=120):
    end = time.time() + t
    while time.time() < end:
        pump(0.05)
        with pv._cv:
            idle = not pv._jobs
        if idle and not pv._loading and not pv._render_timer.isActive():
            pump(0.4)
            with pv._cv:
                if not pv._jobs: return
def find(it, pred):
    for c in it.childItems():
        if pred(c): return c
        r = find(c, pred)
        if r is not None: return r
def find_all(it, pred, out=None):
    out = [] if out is None else out
    for c in it.childItems():
        if pred(c): out.append(c)
        find_all(c, pred, out)
    return out
pump(0.6)
stage = find(win.contentItem(), lambda c: c.objectName() == "stage")
pages = stage.property("pages")
page = None
for i in range(6):
    p = pages.property(i).toQObject()
    if p is not None and p.metaObject().className().startswith("SplattingPage"):
        page, idx = p, i
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", idx), Q_ARG("QVariant", True)); pump(1.2)
hs = page.findChild(QObject, "holeShiftWin")
check("the Hole Shift window exists, Vertical Warp Fix's is gone", hs is not None
      and page.findChild(QObject, "warpFixWin") is None)
btn = lambda t: find(page, lambda c: c.metaObject().className().startswith("AccentButton") and c.property("text") == t and c.isVisible())
check("page buttons are live", btn("Hole Shift") is not None and btn("Hole Shift").isEnabled())
# Edit Depth Map in Vertical Warp Fix's place (user, 2026-09-28): first of the
# region tools, its tip naming vertical warp; the preview bar's copy is gone
_edm = [c for c in find_all(page, lambda c: c.objectName() == "editDepthMap")]
_row = _edm[0].parentItem() if _edm else None
_kids = [c for c in _row.childItems() if c.isVisible()] if _row else []
_tip = find(_edm[0], lambda c: c.property("tip") is not None) if _edm else None
check("Edit Depth Map replaces Vertical Warp Fix: one button, first before Hole Shift, filled",
      len(_edm) == 1 and btn("Vertical Warp Fix") is None and _kids and _kids[0] is _edm[0]
      and _edm[0].property("text") == "Edit Depth Map" and _edm[0].property("kind") == "filled",
      f"{len(_edm)} buttons, first {(_kids[0].property('text') if _kids else None)!r}")
check("...its tip says it can fix vertical warp", _tip is not None and "vertical warp" in str(_tip.property("tip")).lower())

def click_item(window, item):
    c = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(int(c.x()), int(c.y())))

# refused before a load -> the prompt in the main window
click_item(win, btn("Hole Shift")); pump(0.5)
prompt = page.property("prompt")
check("refused: HS window stays hidden, v1's message asked", not hs.isVisible() and hs.property("wanted") is False
      and prompt.property("opened") and "No splat" in prompt.property("message"), prompt.property("title"))
QMetaObject.invokeMethod(prompt, "finish", Q_ARG("QVariant", 1)); pump(0.5)

for k, v in {"input_source_clips": str(FIX / "src").replace("\\", "/"),
             "input_depth_maps": str(FIX / "depth").replace("\\", "/"),
             "output_splatted": str(FIX / "out").replace("\\", "/"), "multi_map": False,
             "preview_source": "Splat Result", "preview_size": "100%", "border_mode": "Manual", "max_disp": 60,
             "auto_save_sidecar": False, "update_slider_from_sidecar": True, "auto_convergence_mode": "Off"}.items():
    sp._set(k, v)
# Upcoming Jobs from the folders (user, 2026-09-27): listed before any Load Preview
_t0 = __import__("time").time()
while not pv.property("jobClips").property("clips") and __import__("time").time() - _t0 < 15:
    pump(0.1)
check("the clip list fills from the folders, before Load Preview", len(pv.property("jobClips").property("clips")) >= 1
      and not pv.loaded, str(pv.property("jobClips").property("clips")))
pv.load(); settle()
check("preview loaded", pv.loaded)
_txt = [str(c.property("text")) for c in find_all(page, lambda c: True) if c.property("text") is not None]
check("no Load Preview button and no Jump to box (the clip list loads a clip when picked)",
      not any(t in ("Load Preview", "Jump to:") for t in _txt), "")
# Current Clip Info fills for the clip shown (user, 2026-09-27: all but Filename read N/A)
_inf = dict(sp.property("info"))
check("Current Clip Info: every row filled for the preview's clip", all(v not in ("N/A", "") for v in _inf.values())
      and "x" in _inf["resolution"] and _inf["disparity"] == f"{float(sp._map.value('max_disp')):g}", str(_inf))
_md = sp._map.value("max_disp"); sp._set("max_disp", 42); pump(0.2)
check("...Disparity follows the knob", sp.property("info")["disparity"] == "42", sp.property("info")["disparity"])
sp._set("max_disp", _md); settle()

# ---- the frame scrubber is live while held (user, 2026-09-27): frames render during
# the drag (the fast way), and exactly where it is let go
scr = find(page, lambda c: c.objectName() == "frameScrubber")
_n = pv.property("frameCount")
_serials = []; pv.frameChanged.connect(lambda: _serials.append(pv.property("imageSerial")))
_seeks = []; _orig_submit = pv._submit
def _spy(kind, arg=None):
    if kind == "seek": _seeks.append((arg, pv._dragging))
    return _orig_submit(kind, arg)
pv._submit = _spy
_a = scr.mapToScene(QPointF(scr.property("leftPadding") + 6, scr.height() / 2))
_b = scr.mapToScene(QPointF(scr.width() * 0.8, scr.height() / 2))
QTest.mousePress(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(_a.x()), int(_a.y()))); pump(0.2)
for k in range(1, 13):
    QTest.mouseMove(win, QPoint(int(_a.x() + (_b.x() - _a.x()) * k / 12), int(_a.y()))); pump(0.08)
_during = list(_seeks); _frames_during = len(_serials)
QTest.mouseRelease(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(_b.x()), int(_b.y()))); settle()
pv._submit = _orig_submit
check("scrubber held: it seeks as it moves, the fast way (%d frames in the clip)" % _n,
      _n > 2 and len(_during) >= 2 and all(d for _f, d in _during) and _frames_during >= 2,
      f"{len(_during)} seeks while held, {_frames_during} frames shown, {_during[:4]}")
check("...let go: one exact seek where it stopped", _seeks[-1][1] is False and _seeks[-1][0] == round(scr.property("value"))
      and pv.property("frame") == _seeks[-1][0] and not pv._dragging, f"{_seeks[-2:]} frame {pv.property('frame')}")
pv.seek(0); settle()

# ---- a click in a modal dialog must not reach the page under it (user, 2026-09-27:
# picking a theme in Settings over the preview also flipped the preview)
fc0 = page.findChild(QObject, "flipCard")
up0 = fc0.property("backUp")
theme_ = engine.singletonInstance("Studio", "Theme")
dlg = find(win.contentItem(), lambda c: c.objectName() == "themePicker")     # the header's theme button
QMetaObject.invokeMethod(dlg, "open"); pump(0.8)
check("a modal dialog open is counted (Theme.modalOpen)", theme_.property("modalOpen") is True)
picm = find(page, lambda c: c.objectName() == "previewImage" and c.isVisible())
prm = picm.mapToScene(QPointF(0, 0))
over = None
for c in find_all(win.contentItem(), lambda c: c.objectName() == "paletteSwatch"):
    if not c.isVisible(): continue
    pt_ = c.mapToScene(QPointF(c.width() / 2, c.height() / 2))
    if prm.x() < pt_.x() < prm.x() + picm.width() and prm.y() < pt_.y() < prm.y() + picm.height():
        over = pt_; break
idx0 = objs["Shell"].property("paletteIndex")
if over is not None:
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(over.x()), int(over.y()))); pump(1.2); settle()
check("a theme picked from the theme button over the preview: the theme changes, the preview does not flip",
      over is not None and objs["Shell"].property("paletteIndex") != idx0 and fc0.property("backUp") == up0,
      f"swatch over picture {over is not None}, flipped {fc0.property('backUp') != up0}")
objs["Shell"].setProperty("paletteIndex", idx0)
QMetaObject.invokeMethod(dlg, "close"); pump(0.5)
check("...closed: nothing counted any more", theme_.property("modals") == 0, str(theme_.property("modals")))

# the live preview's zoom: wheel zooms, drag pans, Fit (user, 2026-09-27)
import _zoom_checks
_zoom_checks.run(check, pump, settle if "settle" in dir() else (lambda: pump(0.5)), win, page, sp._map, lambda it: find_all(it, lambda c: True))
# ---- the clip list under Special FX (user, 2026-09-27, as Merging's)
sq = page.findChild(QObject, "splatJobQueue")
names = list(pv.property("jobClips").property("clips"))
check("Splatting lists its clips under Special FX", sq is not None and len(names) >= 1
      and names[0].lower().endswith((".mp4", ".mov", ".mkv", ".avi")), str(names))
fxc = find(page, lambda c: c.metaObject().className().startswith("Section_") and c.property("title") == "Special FX")
check("...right under Special FX, never making the page taller", sq.property("x") == fxc.property("x")
      and sq.property("y") > fxc.property("y") + fxc.property("height") - 1, f"{sq.property('y')} vs {fxc.property('y')}+{fxc.property('height')}")
sq.setProperty("busy", True); sq.setProperty("running", names[0].rsplit(".", 1)[0]); pump(0.2)   # the batch reports it bare
rows_ = [c for c in find_all(sq, lambda c: c.objectName() == "jobQueueRow")]
check("...a running clip reported without its extension is found and marked", any(r.property("current") for r in rows_))
# an H and an L bar per clip, filled by the renderer's frames as each pass runs
# (user, 2026-09-28): the batch's own messages through the controller's drain
sp._set("enable_full_res", True); sp._set("enable_low_res", True); pump(0.2)
n0 = int(pv.property("jobClips").property("clipFrames")[0])
def pass_bars():
    r = [r for r in find_all(sq, lambda c: c.objectName() == "jobQueueRow") if r.property("index") == 0][0]
    return [round(b.property("value"), 2) for b in find_all(r, lambda c: c.objectName() == "jobQueuePassBar")]
def feed(*msgs):
    for m in msgs: sp._queue.put(m)
    sp._drain(); pump(0.15)
feed(("update_info", {"filename": names[0].rsplit(".", 1)[0]}), ("status", f"Processing Full-Resolution for {names[0]}"),
     ("processed", n0 // 2))
got_h = pass_bars()
feed(("processed", n0))
got_h2 = pass_bars()
feed(("status", f"Processing Low-Resolution for {names[0]}"), ("processed", n0 // 4))
got_l = pass_bars()
check("...each clip has an H bar over an L bar, filling with that pass's frames",
      n0 > 0 and got_h == [round(n0 // 2 / n0, 2), 0] and got_h2 == [1, 0] and got_l == [1, round(n0 // 4 / n0, 2)],
      f"{n0} frames: {got_h} {got_h2} {got_l}")
sp._set("enable_low_res", False); pump(0.2)
r0 = [r for r in find_all(sq, lambda c: c.objectName() == "jobQueueRow") if r.property("index") == 0][0]
check("...a pass the job doesn't run is faded and stays empty", pass_bars()[1] == 0
      and [p.property("opacity") for p in find_all(r0, lambda c: c.objectName() == "jobQueuePass")] == [1, 0.35])
sp._set("enable_low_res", True)
feed(("update_info", {"filename": "next"}))
check("...the next clip starts both passes over", list(sp.property("passFrames")) == [0, 0]
      and list(sp.property("passDone")) == [False, False])
sq.setProperty("running", ""); sq.setProperty("busy", False); pump(0.2)
# ---- the flip card (user, 2026-09-27): Front / Back in place of Preview Source;
# a click on the picture turns it over, and the side facing you is the source
shell = objs["Shell"]
fc = page.findChild(QObject, "flipCard")
front, back = shell.property("splatFlipFront"), shell.property("splatFlipBack")
check("flip card: two sides, the facing one is the preview source", fc is not None and front != back
      and sp._map.value("preview_source") == (back if fc.property("backUp") else front),
      f"front {front!r} back {back!r} source {sp._map.value('preview_source')!r}")
was_up = fc.property("backUp")
tap = find(page, lambda c: c.objectName() == "flipTap")
shown_item = tap.parent() if tap is not None else None
pic0 = find(page, lambda c: c.objectName() == "previewImage" and c.isVisible())
click_item(win, pic0); pump(1.2); settle()
check("...a click on the picture turns it over: the other side is the source, one render",
      fc.property("backUp") != was_up and not fc.property("busy") and abs(fc.property("angle")) < 1e-6
      and sp._map.value("preview_source") == (back if not was_up else front), f"{sp._map.value('preview_source')!r}")
shell.setProperty("splatFlipBack" if not was_up else "splatFlipFront", "Depth Map"); pump(0.6); settle()
check("...changing the side facing you changes the preview source", sp._map.value("preview_source") == "Depth Map")
click_item(win, pic0); pump(1.2); settle()
check("...and a click turns it back", fc.property("backUp") == was_up
      and sp._map.value("preview_source") == (front if not was_up else back), f"{sp._map.value('preview_source')!r}")

# ---- Hole Shift, by mouse
_ev = []; pv.editorChanged.connect(lambda n, s: _ev.append((n, dict(s).get("open"), dict(s).get("serial"))))
pv.editorRefused.connect(lambda n, m: _ev.append(("refused", n, m[:30])))
_b = btn("Hole Shift"); _c = _b.mapToScene(QPointF(_b.width() / 2, _b.height() / 2))
print("   button at", _c, "window", win.width(), win.height(), "zoom", win.property("zoom"))
click_item(win, btn("Hole Shift")); settle()
print("   wanted", hs.property("wanted"), "events", _ev[:5], "visible", hs.isVisible())
check("HS window shown", hs.isVisible() and hs.property("serial") > 0, f"serial {hs.property('serial')}")
check("HS size v1's", (hs.width(), hs.height()) == (1500, 950), f"{hs.width()}x{hs.height()}")
pic = find(hs.contentItem(), lambda c: c.metaObject().className().startswith("QQuickImage"))
check("HS picture drawn", pic is not None and pic.property("paintedWidth") > 0 and pic.property("implicitWidth") == 640,
      f"painted {pic.property('paintedWidth') if pic else None} source {pic.property('source') if pic else None}")
eng = pv._engine
holes = dilate_regions.find_holes(eng._dilreg_last_occ)
big = max(holes, key=lambda h: int(h[1].sum())); ys, xs = np.nonzero(big[1]); i = len(ys) // 2
H, W = eng._dilreg_last_occ.shape[:2]
pw, ph = pic.property("paintedWidth"), pic.property("paintedHeight")
ox, oy = (pic.width() - pw) / 2, (pic.height() - ph) / 2
pt = pic.mapToScene(QPointF(ox + (xs[i] + 0.5) / W * pw, oy + (ys[i] + 0.5) / H * ph))
QTest.mouseClick(hs, Qt.LeftButton, Qt.NoModifier, QPoint(int(pt.x()), int(pt.y()))); settle()
info = find(hs.contentItem(), lambda c: c.objectName() == "hsInfo")
check("click on a hole selects it", info.property("text").startswith("1 selected"), info.property("text"))
# the X / Y knobs (in place of v1's D-pad): the arrow keys turn one 0.1 px a notch
def knob(name):
    k = find(hs.contentItem(), lambda c: c.objectName() == name)
    return k, find(k, lambda c: c.metaObject().className().startswith("Dial_"))
xk, xd = knob("hsXKnob"); yk, yd = knob("hsYKnob")
check("X and Y knobs, at the middle (no shift), live with a hole selected", xk is not None and yk is not None
      and abs(xk.property("value")) < 1e-9 and xk.property("enabled") and yk.property("enabled"))
xd.forceActiveFocus(); QTest.keyClick(hs, Qt.Key_Right); QTest.keyClick(hs, Qt.Key_Right); settle()
yd.forceActiveFocus(); QTest.keyClick(hs, Qt.Key_Right); settle()
kf = eng.dilate_regions.ensure_keyframe(pv._editors["holeshift"].frame)
check("X two notches right: the hole moves 0.2 px right", abs(kf.regions[0].offset_x - 0.2) < 1e-9
      and abs(xk.property("value") - 0.2) < 1e-9, f"{kf.regions[0].offset_x} knob {xk.property('value')}")
check("Y one notch up: the hole moves up 0.1 px (v1's up = offset_y -0.1)", abs(kf.regions[0].offset_y + 0.1) < 1e-9
      and abs(yk.property("value") - 0.1) < 1e-9 and hs.property("s")["y"] == "+0.1",
      f"{kf.regions[0].offset_y} knob {yk.property('value')} shown {hs.property('s')['y']}")
# A heavy clip (user, 2026-09-27: one job per notch, the queue crawled and the
# knob jumped back; no live preview). Slowed on purpose: a 0.3 s editor redraw,
# a 0.4 s preview render. A 2 s mouse turn must not queue a job per notch,
# must re-render WHILE held, and the knob must stay where it was let go.
import math
from studio.stages.splatting import editors as ED
ran = {"set_offset": 0, "commit": 0}
_r, _so, _c = ED.HoleShift.render, ED.HoleShift.set_offset, ED.HoleShift.commit
def _slow_render(self): time.sleep(0.3); return _r(self)
def _count_so(self, *a): ran["set_offset"] += 1; return _so(self, *a)
def _slow_commit(self): ran["commit"] += 1; time.sleep(0.4); return _c(self)
ED.HoleShift.render, ED.HoleShift.set_offset, ED.HoleShift.commit = _slow_render, _count_so, _slow_commit
def _at(deg):
    r = xd.width() * 0.4; a = math.radians(deg - 90)
    q = xd.mapToScene(QPointF(xd.width() / 2 + r * math.cos(a), xd.height() / 2 + r * math.sin(a)))
    return QPoint(int(q.x()), int(q.y()))
QTest.mousePress(hs, Qt.LeftButton, Qt.NoModifier, _at(0)); pump(0.03)
for k in range(1, 61):
    QTest.mouseMove(hs, _at(k * 100 / 60)); pump(0.033)
held_commits = ran["commit"]
QTest.mouseRelease(hs, Qt.LeftButton, Qt.NoModifier, _at(100))
let_go = round(xk.property("value"), 1)
after = []
_end = time.time() + 3
while time.time() < _end:
    pump(0.05); after.append(round(xk.property("value"), 1))
settle()
ED.HoleShift.render, ED.HoleShift.set_offset, ED.HoleShift.commit = _r, _so, _c
kf = eng.dilate_regions.ensure_keyframe(pv._editors["holeshift"].frame)
check("slow clip, 2 s turn: the worker keeps only the newest offset (not a job per notch)", ran["set_offset"] < 10,
      f"{ran['set_offset']} set_offset jobs")
check("...the preview re-renders while the knob is held", held_commits >= 1, f"{held_commits} commits while held")
check("...the knob stays where it was let go, and the hole is there", let_go > 1 and set(after) == {let_go}
      and abs(kf.regions[0].offset_x - let_go) < 1e-6, f"let go {let_go}, after {sorted(set(after))}, region {kf.regions[0].offset_x}")
hs.close(); settle()
check("HS closed -> editor closed", not hs.isVisible() and eng._dilreg_refresh is None)

# ---- Special FX badges on the clip list (user, 2026-09-27): from each clip's saved sidecar
import json as _json
(FIX / "depth" / "clipA_depth.fssidecar").write_text(_json.dumps({"keyframe_track": [{"frame": 0, "kind": "static"}],
                                                                  "boost_amount": 2.5}))
(FIX / "depth" / "clipB_depth.fssidecar").write_text(_json.dumps({"frame_break_bars": 0.3, "boost_amount": 0}))
pv.jumpTo(2); settle(); pv.jumpTo(1); settle()
fxs = [list(x)[:5] for x in pv.property("jobClips").property("clipFx")]
check("each clip's Special FX from its sidecar: A keyframes + pop boost, B frame break",
      fxs == [[True, False, True, True, False], [False, True, False, True, False]], str(fxs))
page.setProperty("popOpen", False); page.setProperty("breakOpen", False); page.setProperty("keyOpen", False)
# room for the list under Special FX: sized as a user would, or the window
# fits itself back to its content (it did not before only because the Vertical
# Warp Fix steps, removed 2026-09-28, happened to leave it user-sized)
def keep_tall(h=1600, sec=8):
    """The window user-sized at `h`, until it sticks. Main.qml's own timers (the
    restore settle, a screen move) can refit it after start-up and drop
    userSized -- then the list's room went to 0 and the badge steps failed at
    random (2026-10-09/10: pass, fail, pass on the same code)."""
    end = time.time() + sec
    while time.time() < end:
        win.setProperty("userSized", True)
        if win.height() != h:
            win.setHeight(h)
        pump(0.3)
        if win.height() == h and win.property("userSized"):
            pump(0.9)                      # past the 800 ms userSized commit
            if win.height() == h and win.property("userSized"):
                return True
    return False
check("the window holds a tall test size for the list", keep_tall(), f"{win.height()} {win.property('userSized')}")
print("   list shown:", sq.isVisible(), sq.height(), "win", win.height(), win.property("minimumHeight"), "room", sq.property("room"), "fixed", sq.property("fixedPart"), "y", sq.property("y"), flush=True)
sqr = sorted([c for c in find_all(sq, lambda c: c.objectName() == "jobQueueRow")], key=lambda r: r.property("index"))
def badges(r):
    return tuple(bool(find(r, lambda c, n=n: c.objectName() == n).isVisible())
                 for n in ("fxKeyframe", "fxFrameBreak", "fxPopBoost", "fxSidecar"))
got = [badges(r) for r in sqr]
check("...shown as little badges on the rows, a sidecar badge on each (both have one)",
      got == [(True, False, True, True), (False, True, False, True)], str(got))
# the badges' layout (user, 2026-10-17): the sidecar's beside the bars, the rest on a line under the name,
# and a row with any of those a little taller
def _y(it, r):
    return it.mapToItem(r, QPointF(0, 0)).y()
def _x(it, r):
    return it.mapToItem(r, QPointF(0, 0)).x()
_r0 = sqr[0]
_nm, _pb0, _sc0 = (find(_r0, lambda c, n=n: c.objectName() == n) for n in ("jobQueueName", "fxPopBoost", "fxSidecar"))
_bar0 = [b for b in (find(_r0, lambda c, n=n: c.objectName() == n) for n in ("jobQueueBar", "jobQueuePasses"))
         if b is not None and b.isVisible()][0]          # one bar, or one per pass (Splatting's)
check("...the effects' badges on a line under the clip's name", _y(_pb0, _r0) >= _y(_nm, _r0) + _nm.height(),
      f"badge y {_y(_pb0, _r0):.0f}, name bottom {_y(_nm, _r0) + _nm.height():.0f}")
check("...the sidecar's badge beside the bar, before it", _x(_sc0, _r0) + _sc0.width() <= _x(_bar0, _r0)
      and abs((_y(_sc0, _r0) + _sc0.height() / 2) - (_y(_bar0, _r0) + _bar0.height() / 2)) <= 3,
      f"sidecar x {_x(_sc0, _r0):.0f}..{_x(_sc0, _r0) + _sc0.width():.0f}, bar x {_x(_bar0, _r0):.0f}")
check("...a row with them taller, by the badges' line", _r0.height() == 52 + 16, str(_r0.height()))
check("...and the badges inside the row", _y(_pb0, _r0) + _pb0.height() <= _r0.height(),
      f"{_y(_pb0, _r0) + _pb0.height():.0f} in {_r0.height()}")
# hovering a badge shows just the effect's name, not the clip name's tip on top of it
_pb = find(sqr[0], lambda c: c.objectName() == "fxPopBoost")
_c = _pb.mapToScene(QPointF(_pb.width() / 2, _pb.height() / 2))
QTest.mouseMove(win, QPoint(int(_c.x()), int(_c.y()))); pump(1.0)
_tips = [c for c in find_all(sqr[0], lambda c: c.property("showing") is not None and c.property("tip") is not None)]
_shown = [str(c.property("tip")) for c in _tips if c.property("showing")]
check("...hovering a badge shows only its effect's name", _shown == ["Pop Out Boost"], str(_shown))
QTest.mouseMove(win, QPoint(5, 5)); pump(0.4)
(FIX / "depth" / "clipB_depth.fssidecar").write_text(_json.dumps({"frame_break_bars": 0, "boost_amount": 0}))
import os as _os, time as _t
_os.utime(FIX / "depth" / "clipB_depth.fssidecar", (_t.time() + 5, _t.time() + 5))
pv.jumpTo(2); settle()
# the clip list re-reads a sidecar on its own schedule, not when the preview goes idle:
# wait for the badges (flaky before, 2026-10-09 -- read too early, it saw the old ones)
def fx_b():
    return [list(x) for x in pv.property("jobClips").property("clipFx")][1][:5]
def wait_fx(want, sec=15):
    end = time.time() + sec
    while fx_b() != want and time.time() < end:
        pump(0.1)
    return fx_b() == want
check("...a sidecar saved again updates its badges (still a sidecar)",
      wait_fx([False, False, False, True, False]),
      str(list(pv.property("jobClips").property("clipFx"))))
pump(0.3)
check("...a row with only the sidecar's badge keeps the usual height", sqr[1].height() == 52, str(sqr[1].height()))
(FIX / "depth" / "clipB_depth.fssidecar").unlink()
pv.jumpTo(1); settle(); pv.jumpTo(2); settle()
check("...no sidecar: no sidecar badge", wait_fx([False, False, False, False, False])
      and not find(sqr[1], lambda c: c.objectName() == "fxSidecar").isVisible(),
      str(list(pv.property("jobClips").property("clipFx"))))
# a depth map edited in the Depth Map Editor (user, 2026-09-30): its original
# kept in edit_history marks the clip with a pencil badge; gone, no badge
_hist = FIX / "depth" / "edit_history"; _hist.mkdir(exist_ok=True)
_sh0 = __import__("shutil")
_sh0.copy(FIX / "depth" / "clipA_depth.mp4", _hist / "clipA_depth.original.mp4")
pv.jumpTo(2); settle(); pv.jumpTo(1); settle()
keep_tall()
_ed = [bool(find(r, lambda c: c.objectName() == "fxDepthEdited").isVisible()) for r in sqr]
check("...an edited depth map: a pencil badge on that clip only",
      [list(x)[4] for x in pv.property("jobClips").property("clipFx")] == [True, False] and _ed == [True, False], str(_ed))
_sh0.rmtree(_hist)
pv.jumpTo(2); settle(); pv.jumpTo(1); settle()
check("...its original gone: no pencil",
      not any(bool(find(r, lambda c: c.objectName() == "fxDepthEdited").isVisible()) for r in sqr))
# Detect Text saved a mask for a clip (user, 2026-10-03): its <clip>_submask.mp4
# beside the source gives that clip a T badge; gone, no badge
_srcA = Path(str(pv.property("jobClips").property("clipPaths")[0]))
_subA = _srcA.with_name(_srcA.stem + "_submask.mp4")
_subA.write_bytes(b"mask")
pv.jumpTo(2); settle(); pv.jumpTo(1); settle()
keep_tall()
_tx = [bool(find(r, lambda c: c.objectName() == "fxTextMask").isVisible()) for r in sqr]
check("...a Detect Text mask: a T badge on that clip only",
      [list(x)[5] for x in pv.property("jobClips").property("clipFx")] == [True, False] and _tx == [True, False], str(_tx))
_subA.unlink()
pv.jumpTo(2); settle(); pv.jumpTo(1); settle()
# room for a whole badge row AND the legend under it: in less the legend now steps aside rather
# than run out of the box (user, 2026-10-24: it was cut off at the bottom)
keep_tall(2000)
_lg = find(sq, lambda c: c.objectName() == "jobQueueLegend")
_li = [c for c in find_all(sq, lambda c: c.objectName() == "jobQueueLegendItem")]
check("...a legend of the eight badges under the list (user, 2026-10-03; Particles 2026-10-08; Object removed 2026-10-16)", _lg is not None and _lg.isVisible() and len(_li) == 8
      and _lg.mapToItem(sq, QPointF(0, 0)).y() >= find(sq, lambda c: c.objectName() == "jobQueueList").mapToItem(sq, QPointF(0, 0)).y()
      + find(sq, lambda c: c.objectName() == "jobQueueList").height() - 0.5
      and _lg.mapToItem(sq, QPointF(0, _lg.height())).y() <= sq.height() + 0.5,
      f"{len(_li)} items; box {sq.height():.0f}, room {sq.property('listRoom') or 0:.0f}, min row {sq.property('minList') or 0:.0f}, "
      f"legend {_lg.property('implicitHeight'):.0f} / {_lg.height():.0f}" if _lg is not None else "no legend")
# squeezed (user, 2026-10-24: "the splat clip list gets cut off on the bottom when the little indicators
# are on any of the clips"): a badge row is taller than a plain one, and the legend sat under a list
# kept at one plain row -- both ran out of the box. Now nothing shown runs past its bottom.
keep_tall(1600)
_lst = find(sq, lambda c: c.objectName() == "jobQueueList")
_inside = lambda it: it.mapToItem(sq, QPointF(0, it.height())).y() <= sq.height() + 0.5
_tall = sq.property("rowHeight") + sq.property("fxLineHeight")      # a row with its badge line
check("...squeezed, nothing cut off at the bottom: a whole badge row or no list, the legend inside or put away",
      (not _lst.isVisible() or (_inside(_lst) and _lst.height() >= _tall - 0.5))
      and (not _lg.isVisible() or _inside(_lg)),
      f"box {sq.height():.0f}; list {_lst.isVisible()} {_lst.height():.0f} (a badge row {_tall}); "
      f"legend {_lg.isVisible()} ends {_lg.mapToItem(sq, QPointF(0, _lg.height())).y():.0f}")
check("...the mask gone: no T",
      not any(bool(find(r, lambda c: c.objectName() == "fxTextMask").isVisible()) for r in sqr))
# ---- another chapter (user, 2026-09-27): with a clip previewed, the folders move to
# another chapter; a click on ITS first clip must show it the first time -- v1's
# loader kept the old clip's readers when the path it landed on was "the last one"
import shutil as _sh
src2 = FIX / "src2"; src2.mkdir(exist_ok=True)
for f in (FIX / "src").iterdir():
    if f.name.startswith("clipB"): _sh.copy(f, src2 / f.name)
pv.showClip([p for p in pv.property("jobClips").property("clipPaths") if "clipA" in p][0]); settle()
# which files the preview opens: the engine's reader hook, spied on
_epv = pv._engine.previewer
_opened = []; _orig_fast = _epv._fast_reader
def _spy_fast(key, path, reader):
    _opened.append(str(path)); return _orig_fast(key, path, reader)
_epv._fast_reader = _spy_fast
sp._set("input_source_clips", str(src2).replace("\\", "/")); pump(0.8)
_t0 = time.time()
while len(pv.property("jobClips").property("clips")) != 1 and time.time() - _t0 < 15: pump(0.1)
pv.showClip(pv.property("jobClips").property("clipPaths")[0]); settle()
_epv._fast_reader = _orig_fast
# Clip Info follows the load a moment later: wait for it (a stale one still fails)
_t0 = time.time()
while "clipB" not in str(sp.property("info")["filename"]) and time.time() - _t0 < 3: pump(0.05)
check("another chapter: its clip opens on the first click (not the old clip under the new name)",
      any("src2" in o.replace("\\", "/") and "clipB" in o for o in _opened) and "clipB" in str(sp.property("info")["filename"]),
      f"opened {[o[-30:] for o in _opened]} info {dict(sp.property('info'))}")
sp._set("input_source_clips", str(FIX / "src").replace("\\", "/")); settle()
pv.shutdown()
studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
