"""The Depth Map Editor (Extra tab) in the real app, offscreen, end to end
without SAM 3 -- as v1's scripts/checks/verify_depthedit.py does it: a
synthetic clip (a depth map written through the editor's own lossless writer,
a testsrc2 colour clip), the mask a track would give put in by hand, then the
real path. Pins down: the tool is the second sub-tab and loads; Start
Tracking is on screen on every rail page; picking the clip pairs its depth
map; Load Clip by mouse; every view renders a picture; a slider edits the
selected layer (and right-click puts it back); Render by mouse writes
<clip>_depth.mp4 with every pixel outside the mask BIT-EXACT and the object
moved +20%; the project files beside it; the handoff names the Depth Map
Editor; v1's yes/no questions are asked in the page and answered (render
again -> "Replace?", Delete layer -> No keeps it); fits 1080p at 0.85.
Configs and last_jobs.json redirected."""
import os, sys, tempfile, hashlib, time, subprocess
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
import cv2
import numpy as np
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, QObject, Q_ARG, QPoint, QPointF, Qt, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtTest import QTest
from core.depthedit import depth_io
from studio import app as A

# ---- the synthetic clip (v1's check's recipe) --------------------------------------
W, H, N = 256, 160, 24
clip_dir = tmp / "clip"; clip_dir.mkdir()
rgb_path = str(clip_dir / "synthetic.mp4").replace("\\", "/")
depth_path = str(clip_dir / "synthetic_depth.mp4").replace("\\", "/")
subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                f"testsrc2=size=512x320:rate=24:duration={N / 24}", "-pix_fmt", "yuv420p", rgb_path], check=True)
info = depth_io.ClipInfo(depth_path, W, H, N, "24/1", "yuv420p10le", False)
writer = depth_io.DepthWriter(depth_path, info)
ramp = np.linspace(0.1, 0.6, W, dtype=np.float32)[None, :].repeat(H, axis=0)
for f in range(N):
    frame = ramp.copy()
    frame[40:120, 80 + f:160 + f] = 0.5
    writer.write(depth_io.from_unit(frame, info))
writer.close()

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
def into_view(item):
    """Scroll the page so `item` is on screen, as a user would: since pages
    scroll when they don't fit (76bdf00), the shaper's button sat below this
    window and the click missed it (2026-10-10)."""
    p = item.parentItem()
    while p is not None:
        if p.property("contentHeight") is not None and p.property("contentY") is not None                 and p.property("contentHeight") > p.height():
            ci = p.property("contentItem")
            y = item.mapToItem(ci, QPointF(0, 0)).y()
            top = max(0.0, min(y - (p.height() - item.height()) / 2, p.property("contentHeight") - p.height()))
            p.setProperty("contentY", top); pump(0.2)
        p = p.parentItem()


def click_in(item):
    QTest.mouseMove(win, QPoint(5, 5)); pump(0.1)
    into_view(item)
    c = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(c.x()), int(c.y())))

pump(0.6)
de = objs["DepthEdit"]
stage = find(win.contentItem(), lambda c: c.objectName() == "stage")
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 4), Q_ARG("QVariant", True)); pump(0.8)
extra = stage.property("pages").property(4).toQObject()
extra.setProperty("tool", 1); pump(0.8)
page = find(extra, lambda c: c.objectName() == "depthEditPage")
check("the Depth Map Editor is the second sub-tab", page is not None and page.isVisible())
check("it loads on first view", until(lambda: de.property("state").get("ready"), 60), str(de.property("state").get("loadError")))
S = lambda: de.property("state")  # noqa: E731
eng = de._engine

# ---- Start Tracking on every rail page (v1: a transition per page)
track = find(page, lambda c: c.objectName() == "deTrack")
hidden = []
for p in ("Segment", "Layer", "Gradient", "Global", "Output"):
    de.setVar("page", p); pump(0.3)
    if not (track.isVisible() and S()["page"] == p):
        hidden.append(p)
check("Start Tracking is on screen on every page", not hidden, str(hidden))
de.setVar("page", "Segment"); pump(0.2)

# ---- fit: the tallest rail page decides, and switching pages keeps the size (v1)
heights = {}
for p in ("Segment", "Layer", "Gradient", "Global", "Output"):
    de.setVar("page", p); pump(0.3)
    heights[p] = win.property("minimumHeight")
check("switching rail pages keeps the window's size", len(set(heights.values())) == 1, str(heights))
min_h = max(heights.values())
check("fits the 1080p screen at 0.85", min_h * 0.85 <= 1000, f"{min_h} -> {min_h * 0.85:.0f}")
de.setVar("page", "Segment"); pump(0.2)

# ---- the clip
de.setVar("rgb_path", rgb_path); pump(0.3)
check("picking the clip pairs its depth map", os.path.normcase(S()["depth_path"]) == os.path.normcase(depth_path),
      S()["depth_path"])
de.setVar("output_dir", str(clip_dir / "edited").replace("\\", "/")); pump(0.2)
click_in(find(page, lambda c: c.objectName() == "deLoad"))
check("Load Clip loads", until(lambda: S().get("hasClip") and not S().get("busyJob"), 60), S().get("status"))
# the RGB view finer than the depth map: the source's own 512x320 (under
# 720p), not the map's 256x160 (user, 2026-10-03)
check("...the depth at its size and the RGB view at the source's (up to 720p)",
      eng.codes is not None and eng.codes.shape == (N, H, W) and eng.rgb is not None and eng.rgb.shape == (N, 320, 512, 3),
      str(None if eng.rgb is None else eng.rgb.shape))
# v1 draws the label with the preview, 40 ms after the load (its debounce).
check("the frame slider spans the clip", until(lambda: S()["frameText"] == f"Frame 1 / {N}", 5) and S()["frames"] == N,
      S()["frameText"])

# a layer with the mask a track would give (v1's check does the same)
de.run("_new_layer"); pump(0.3)
layer = eng._layer()
masks = []
for f in range(N):
    m = np.zeros((H, W), bool)
    m[40:120, 80 + f:160 + f] = True
    layer.masks.set(f, m)
    masks.append(m)
layer.tracked = True
check("New makes a layer and lists it", len(S()["layers"]) == 1 and S()["selected"] == 0, str(S()["layers"]))

serials = []
for view in ("RGB", "Depth", "Edited"):
    s0 = de.property("imageSerial")
    de.setVar("view", view)
    until(lambda: de.property("imageSerial") > s0, 5)
    img = de.image_for_test()
    # drawn at a whole multiple of the map's size (the finer mask outline,
    # 2026-09-28), never another aspect
    serials.append((view, img is not None and img.width() % W == 0 and img.height() % H == 0
                    and img.width() // W == img.height() // H))
check("every view renders a picture", all(ok for _v, ok in serials), str(serials))
check("the picture keeps the source's aspect", abs(S()["aspect"] - 512 / 320) < 1e-6, str(S()["aspect"]))

# ---- the picture's options (user, 2026-09-28): masks as outlines only; the
# clip's frame over the depth views at a chosen opacity
def pic():
    q = de.image_for_test().convertToFormat(QImage.Format.Format_RGB888)
    b = np.frombuffer(q.constBits(), np.uint8).reshape(q.height(), q.bytesPerLine())[:, :q.width() * 3]
    return b.reshape(q.height(), q.width(), 3).astype(np.int32)
def show(view, overlay):
    s0 = de.property("imageSerial")
    de.setVar("view", view); de.setVar("overlay", overlay)
    until(lambda: de.property("imageSerial") > s0, 5); pump(0.2)
    return pic()
de.setVar("frame", 0); eng.frame_var.set(0); de._run("_on_frame_slider"); pump(0.3)
bare = show("Depth", False)
k = bare.shape[1] // W
def at(img, r, c):                                 # a depth pixel's colour, at the image's own scale
    f = img.shape[1] // W
    return img[r * f + f // 2, c * f + f // 2] if f > 1 else img[r, c]
def top_edge(img, r, c):                           # the first drawn row of that depth pixel
    f = img.shape[1] // W
    return img[r * f, c * f + f // 2]
tint = show("Depth", True)
de.setViewOption("maskStyle", "outline"); pump(0.3)
outl = pic()
check("Outline only: nothing tinted inside a mask, its border drawn",
      np.array_equal(at(outl, 60, 110), at(bare, 60, 110)) and not np.array_equal(at(tint, 60, 110), at(bare, 60, 110))
      and not np.array_equal(top_edge(outl, 40, 110), at(bare, 40, 110)),
      f"inside {at(outl, 60, 110)} / tinted {at(tint, 60, 110)} / bare {at(bare, 60, 110)}, "
      f"edge {top_edge(outl, 40, 110)} vs {at(bare, 40, 110)}")
de.setViewOption("maskStyle", "tint")
de.setViewOption("rgbOpacity", 0.3); de.setViewOption("rgbOverlay", True); pump(0.3)
over = show("Depth", False)
rgb0 = cv2.resize(eng.rgb[0], (W, H), interpolation=cv2.INTER_AREA).astype(np.float32)   # shrunk to the map, as drawn
b0 = bare[::k, ::k].astype(np.float32)[:H, :W] if k > 1 else bare.astype(np.float32)
o0 = over[::k, ::k].astype(np.float32)[:H, :W] if k > 1 else over.astype(np.float32)
err = float(np.abs(o0 - (b0 * 0.7 + rgb0 * 0.3)).max())
check("RGB over depth: the clip's frame blended over the depth at the slider's opacity (30%)", err <= 2.0, f"max error {err:.1f}")
de.setViewOption("rgbOverlay", False); pump(0.3)
check("...off: the plain depth again", np.array_equal(show("Depth", False), bare))
saved = ss.load("depthedit_view")
check("...the options are remembered", saved.get("rgbOpacity") == 0.3 and saved.get("maskStyle") == "tint"
      and saved.get("rgbOverlay") is False, str(saved))
_row = [find(page, lambda c, n=n: c.objectName() == n) for n in ("deOutlineOnly", "deRgbOverlay", "deRgbOpacity")]
check("...their controls are on the view row", all(c is not None and c.isVisible() for c in _row))
# Dither (user, 2026-10-05: banding in the 8-bit picture of a 10-bit map): on by
# default; with it, the picture averages to the map's own depth between 8-bit
# levels (the synthetic map is a gentle ramp: rows 0-39 are background)
dth = find(page, lambda c: c.objectName() == "deDither")
check("Dither: on the options row, on by default", dth is not None and dth.isVisible() and dth.property("checked") is True
      and de.property("viewOptions").get("dither") is True)
def ramp_error(img):
    """Mean |picture - true depth| in 8-bit levels, each column averaged over rows 0-31."""
    true = eng._unit(0)[:32, :].astype(np.float64) * 255.0
    seen = np.array([[at(img, r, c)[0] for c in range(W)] for r in range(32)], np.float64)
    return float(np.abs(seen.mean(axis=0) - true.mean(axis=0)).mean())
de.setViewOption("dither", True); on = show("Depth", False)
de.setViewOption("dither", False); off = show("Depth", False)
e_on, e_off = ramp_error(on), ramp_error(off)
check("...dithered, the picture keeps the 10-bit depth: its column averages far closer than plain 8-bit",
      e_on < 0.12 and e_off > 0.18 and e_on < e_off / 2, f"{e_on:.3f} vs {e_off:.3f} levels")
check("...off is the plain 8-bit picture (the old one, rounded)",
      np.array_equal(off[..., 0], np.rint(np.clip(eng._unit(0), 0, 1) * 255.0).astype(np.int32).repeat(k, 0).repeat(k, 1))
      if off.shape[0] == H * k else False, str(off.shape))
check("...remembered", ss.load("depthedit_view").get("dither") is False)
de.setViewOption("dither", True); pump(0.2)
check("...and on again", ss.load("depthedit_view").get("dither") is True and dth.property("checked") is True)

# nothing on the left runs under the right rail (Layers): the view row, the new
# options' row and the picture all end before it
_rail = find(page, lambda c: c.objectName() == "deRail")
_railx = _rail.mapToItem(page, QPointF(0, 0)).x() if _rail is not None else page.width()
_ends = {n: (lambda c: c.mapToItem(page, QPointF(c.width(), 0)).x())(find(page, lambda c, n=n: c.objectName() == n))
         for n in ("deTool", "deRgbOpacity", "deDither", "dePicture")}
check("...and nothing runs under the Layers rail (the view row, the options, the picture)",
      _rail is not None and all(v <= _railx + 0.5 for v in _ends.values()), f"rail at {_railx:.0f}: {_ends}")
if os.environ.get("DE_SHOT"): win.grabWindow().save(os.environ["DE_SHOT"])
# ---- zoom (user, 2026-10-13: Splatting's and Merging's): the wheel about the pointer,
# a middle-button drag pans (no SAM click), the Zoom box back to Fit; nothing re-rendered
from PySide6.QtGui import QWheelEvent
from PySide6.QtCore import QEvent
de.setVar("view", "Depth"); pump(0.3)
_pic = find(page, lambda c: c.objectName() == "dePicture")
_shown = find(_pic, lambda c: c.objectName() == "deImage").parentItem()
_zbox = find(page, lambda c: c.objectName() == "deZoomBox")
def _wheel(item, x, y, notches):
    sp = item.mapToScene(QPointF(x, y))
    for _ in range(abs(notches)):
        ev = QWheelEvent(sp, win.mapToGlobal(sp), QPoint(0, 0), QPoint(0, 120 if notches > 0 else -120),
                         Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
        QGuiApplication.sendEvent(win, ev); pump(0.05)
def _frame_at(x, y):          # the frame point (0..1) under a point of the view
    return ((x - _shown.x()) / _shown.width(), (y - _shown.y()) / _shown.height())
into_view(_pic); pump(0.2)
_w0, _serial = _shown.width(), de.property("imageSerial")
_px, _py = _pic.width() * 0.3, _pic.height() * 0.4
_wheel(_pic, _px, _py, 3); pump(0.2)
check("the wheel zooms in, ~12% a notch", abs(_shown.width() / _w0 - 1.12 ** 3) < 0.01, f"{_shown.width() / _w0:.3f}")
check("...nothing re-rendered for it", de.property("imageSerial") == _serial)
check("...and the Zoom box says how far", _zbox is not None and _zbox.property("displayText") == "140%",
      str(_zbox.property("displayText") if _zbox else None))
# (while narrower than the view it stays centred, as Splatting's does; past it, the anchor holds)
QMetaObject.invokeMethod(_pic, "zoomAt", Q_ARG("QVariant", 2.0), Q_ARG("QVariant", _px), Q_ARG("QVariant", _py)); pump(0.2)
_before = _frame_at(_px, _py)
_wheel(_pic, _px, _py, 3); pump(0.2)
_after = _frame_at(_px, _py)
check("...about the point under the pointer", max(abs(_before[0] - _after[0]), abs(_before[1] - _after[1])) < 0.002,
      f"{_before} -> {_after}")
_pts = len(de.property("markers").get("points") or [])
_x0 = _shown.x()
_a = _pic.mapToScene(QPointF(_pic.width() / 2, _pic.height() / 2))
QTest.mousePress(win, Qt.MiddleButton, Qt.NoModifier, QPoint(int(_a.x()), int(_a.y()))); pump(0.05)
QTest.mouseMove(win, QPoint(int(_a.x()) + 40, int(_a.y()))); pump(0.05)
QTest.mouseMove(win, QPoint(int(_a.x()) + 80, int(_a.y()))); pump(0.05)
QTest.mouseRelease(win, Qt.MiddleButton, Qt.NoModifier, QPoint(int(_a.x()) + 80, int(_a.y()))); pump(0.2)
check("a middle-button drag pans it, and clicks nothing", _shown.x() > _x0 + 30
      and len(de.property("markers").get("points") or []) == _pts, f"x {_x0:.0f} -> {_shown.x():.0f}")
QTest.mousePress(win, Qt.MiddleButton, Qt.NoModifier, QPoint(int(_a.x()), int(_a.y()))); pump(0.05)
QTest.mouseMove(win, QPoint(int(_a.x()) + 3000, int(_a.y()))); pump(0.05)
QTest.mouseRelease(win, Qt.MiddleButton, Qt.NoModifier, QPoint(int(_a.x()) + 3000, int(_a.y()))); pump(0.2)
check("...never past the picture's edge", abs(_shown.x()) < 0.5, f"{_shown.x():.1f}")
_wheel(_pic, _px, _py, -40); pump(0.2)
check("the wheel out stops at Fit", abs(_shown.width() - _w0) < 0.5 and _zbox.property("displayText") == "Fit")
_wheel(_pic, _px, _py, 60); pump(0.2)
check("...and in at 800%", abs(_shown.width() / _w0 - 8) < 0.01, f"{_shown.width() / _w0:.2f}")
QMetaObject.invokeMethod(_pic, "fit"); pump(0.2)
check("Fit: the whole frame again, centred", abs(_shown.width() - _w0) < 0.5
      and abs(_shown.x() - (_pic.width() - _shown.width()) / 2) < 0.5)
de.setVar("overlay", True); de.setVar("view", "Edited"); pump(0.3)

# ---- the edit controls
de.setControl("feather", 0)
de.setControl("brightness", 0.2); pump(0.3)
check("a slider edits the layer", abs(layer.settings.brightness - 0.2) < 1e-9 and layer.settings.feather == 0)
check("...with v1's readout", S()["controls"]["brightness"]["readout"] == "+20%", S()["controls"]["brightness"]["readout"])
de.setControl("contrast", 2.0); pump(0.2)
de.resetControl("contrast"); pump(0.2)
check("right-click puts a control back to a new layer's value", abs(layer.settings.contrast - 1.0) < 1e-9,
      str(layer.settings.contrast))

# Alter / Add (user, 2026-10-03): Add replaces the depth inside the mask
de.setVar("page", "Layer"); pump(0.3)
def dial_shown(k):
    d = find(page, lambda c, k=k: c.objectName() == "ctl_" + k)
    return d is not None and d.isVisible()
check("a layer starts in Alter, with its Alter sliders", layer.settings.mode == "Alter"
      and dial_shown("contrast") and not dial_shown("add_depth"))
_h_alter = win.property("minimumHeight")
de.setControl("mode", "Add"); pump(0.4)
# ...with every Alter control too (user, 2026-10-11)
_h_add = win.property("minimumHeight")
check("Add's dials still fit the 1080p screen at 0.85", _h_add * 0.85 <= 1000,
      f"Alter {_h_alter}, Add {_h_add} -> {_h_add * 0.85:.0f}")
_alter = ("brightness", "contrast", "flatten", "smooth", "opacity", "edge_top", "edge_bottom", "edge_left", "edge_right")
check("Add: the layer's mode, Depth shown, and every Alter control", layer.settings.mode == "Add"
      and dial_shown("add_depth") and all(dial_shown(k) for k in _alter),
      (layer.settings.mode, dial_shown("add_depth"), [k for k in _alter if not dial_shown(k)]))
de.setControl("add_depth", 0.1); pump(0.3)          # away from both the default and the answer
de.sampleAddDepth(); pump(0.4)
_m = layer.masks.get(eng.frame_idx)
_want = float(np.percentile(eng._unit(eng.frame_idx)[_m], 90)) if _m is not None else None
check("Sample: Depth from the object's nearest part", _want is not None and abs(layer.settings.add_depth - _want) < 1e-3,
      (layer.settings.add_depth, _want))
de.setControl("mode", "Alter"); pump(0.3)
check("...and back to Alter", layer.settings.mode == "Alter" and dial_shown("contrast"))

# ---- keyframes on the layer (user, 2026-10-05): static holds, dynamic ramps in
_b0 = layer.settings.brightness
de.setFrame(0); pump(0.2)
de.setControl("brightness", 0.0); pump(0.2)
de.addKeyframe("static"); pump(0.3)
de.setFrame(N - 1); pump(0.2)
de.setControl("brightness", 0.4); pump(0.2)
de.addKeyframe("dynamic"); pump(0.3)
check("S / D keys show on the layer's timeline",
      [(k["frame"], k["kind"]) for k in S().get("keyframes")] == [(0, "static"), (N - 1, "dynamic")]
      and S().get("onKey") == "dynamic", str(S().get("keyframes")))
_marks = find(page, lambda c: c.objectName() == "deKeyMarks")
check("...as marks on the frame slider", _marks is not None and _marks.property("count") == 2)
_mid = (N - 1) // 2
de.setFrame(_mid); pump(0.4)
_want = 0.4 * _mid / (N - 1)
check("between them a dynamic key ramps in: the preview's settings and the controls follow",
      abs(eng._layer_stack(_mid)[0][0].brightness - _want) < 1e-6 and abs(S()["controls"]["brightness"]["value"] - _want) < 1e-6
      and S().get("onKey") == "", (eng._layer_stack(_mid)[0][0].brightness, S()["controls"]["brightness"]["value"], _want))
check("...and the frame's edit is that frame's", not np.allclose(eng._edited_unit(0), eng._edited_unit(N - 1)))

# ---- the shaper (user, 2026-10-06: Splatting's, for the layer's keys) -- in a popup
_shape_btn = find(page, lambda c: c.objectName() == "deKeyShapeBtn")
_pop = [o for o in page.findChildren(QObject) if o.objectName() == "deShaperPopup"][0]
check("between a key and a dynamic key the Shape button is on", _shape_btn is not None and _shape_btn.property("enabled"))
click_in(_shape_btn); pump(0.5)
_sh = _pop.property("contentItem")
check("...and opens the shaper", _pop.property("opened") and _sh is not None and _sh.objectName() == "keyShaper"
      and find(_sh, lambda c: c.objectName() == "keyShaperGraph").isVisible())
de.setKeyCurve(N - 1, {"shape": "ease_in"}); pump(0.5)
_t = _mid / (N - 1)
check("an ease shapes the ramp: the preview's settings follow it",
      abs(eng._layer_stack(_mid)[0][0].brightness - 0.4 * _t * _t) < 1e-6
      and (S()["keyframes"][-1]["curve"] or {}).get("shape") == "ease_in", str(S()["keyframes"][-1]))
_g = find(_sh, lambda c: c.objectName() == "keyShaperGraph")
_p = _g.mapToScene(QPointF(_g.width() * 0.5, _g.height() * 0.2))
QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(_p.x()), int(_p.y()))); pump(0.5)
_c = layer.keys.curve_at(N - 1) or {}
check("a click in the graph adds a point (Custom)", _c.get("shape") == "custom" and len(_c.get("points", [])) >= 1, str(_c))
de.undo(); pump(0.4)
layer = eng._layer()                              # Undo puts back a copy of the project
check("Undo puts the shape back", (layer.keys.curve_at(N - 1) or {}).get("shape") == "ease_in",
      str(layer.keys.curve_at(N - 1)))
if os.environ.get("SHOTS_DIR"):                   # a look at the popup (our window only)
    de.setVar("page", "Layer"); QTest.mouseMove(win, QPoint(5, 5)); pump(0.8)
    win.grabWindow().save(os.path.join(os.environ["SHOTS_DIR"], "depthedit_shaper.png"))
_pop.close(); pump(0.3)
de.setFrame(N - 1); pump(0.2)
de.setControl("brightness", 0.45); pump(0.2)
check("changing a keyed setting keeps the key's shape", (layer.keys.curve_at(N - 1) or {}).get("shape") == "ease_in")
de.setKeyCurve(N - 1, None); pump(0.3)
de.setControl("brightness", 0.4); pump(0.2)
de.setFrame(_mid); pump(0.3)
de.setFrame(N - 1); pump(0.3)
de.setControl("brightness", 0.5); pump(0.3)
check("a change while on a key changes that key", abs(layer.keys.frame_at(N - 1)["values"]["brightness"] - 0.5) < 1e-9
      and layer.keys.frame_at(N - 1)["type"] == "dynamic")
de.setFrame(3); pump(0.2)
_sc = [o for o in page.findChildren(QObject) if o.objectName() == "deKeyStatic"][0]
QMetaObject.invokeMethod(_sc, "activated"); pump(0.3)
check("the S shortcut keys this frame", layer.keys.frame_at(3) is not None and layer.keys.frame_at(3)["type"] == "static")
de.removeKeyframe(); pump(0.3)
check("Delete Key removes it", layer.keys.frame_at(3) is None and len(S()["keyframes"]) == 2)
de.clearKeyframes(); pump(0.3)
de.setControl("brightness", _b0); pump(0.3)
check("Clear All Keys: the settings hold on every frame again", layer.keys.is_empty() and S().get("keyframes") == []
      and abs(eng._layer_stack(_mid)[0][0].brightness - _b0) < 1e-9)
de.setFrame(0); pump(0.2)

# ---- Render, by mouse
questions = []
de.question.connect(lambda t, m: questions.append(m))
de.setVar("page", "Output"); pump(0.3)
# at the depth map's own size first: every untouched pixel bit-exact
# (Render at source resolution, on by default, is checked below)
check("Render at source resolution is on by default", S().get("render_hires") is True, S().get("render_hires"))
de.setVar("render_hires", False); pump(0.2)
render_btn = find(page, lambda c: c.objectName() == "deRender")
click_in(render_btn)
out_path = clip_dir / "edited" / "synthetic_depth.mp4"
check("Render writes <clip>_depth.mp4", until(lambda: out_path.exists() and not S().get("busyJob"), 120), S().get("status"))
out_info = depth_io.probe(str(out_path))
out = depth_io.load_codes(out_info)
outside = ~np.stack(masks)
check("same frame count", len(out) == N, str(len(out)))
check("every pixel outside the mask is bit-exact", np.array_equal(out[outside], eng.codes[outside]))
shift = depth_io.to_unit(out, out_info)[~outside] - depth_io.to_unit(eng.codes, info)[~outside]
check("inside, the object moved +20% of the range", abs(float(shift.mean()) - 0.2) < 0.002, f"{float(shift.mean()):.4f}")
check("the project files are saved beside it", (clip_dir / "edited" / "synthetic_depthedit.json").is_file()
      and (clip_dir / "edited" / "synthetic_depthedit_masks.npz").is_file())
check("the handoff names the Depth Map Editor", jh.previous_stage_label("splatting") == "Depth Map Editor",
      str(jh.previous_stage_label("splatting")))
check("no question for a first render of a tracked layer", not questions, str(questions))

# ---- v1's yes/no questions, asked in the page
prompt = extra.property("prompt")
mtime = out_path.stat().st_mtime
click_in(render_btn)
check("rendering again asks v1's Replace question", until(lambda: questions and "Replace the existing" in questions[-1], 10)
      and until(lambda: prompt.property("opened"), 5), str(questions[-1:]))
QMetaObject.invokeMethod(prompt, "finish", Q_ARG("QVariant", 1)); pump(0.3)   # Yes
check("...Yes renders it again", until(lambda: out_path.stat().st_mtime > mtime and not S().get("busyJob"), 120))

# Render at source resolution (user, 2026-10-03): the same edit at the RGB's size
de.setVar("render_hires", True); pump(0.2)
mtime = out_path.stat().st_mtime
click_in(render_btn)
until(lambda: prompt.property("opened"), 10)
QMetaObject.invokeMethod(prompt, "finish", Q_ARG("QVariant", 1)); pump(0.3)   # Replace: Yes
check("Render at source resolution: written at the RGB's size",
      until(lambda: out_path.stat().st_mtime > mtime and not S().get("busyJob"), 120)
      and (lambda i: (i.width, i.height, i.num_frames))(depth_io.probe(str(out_path))) == (512, 320, N),
      (lambda i: (i.width, i.height, i.num_frames))(depth_io.probe(str(out_path))))
_hi = depth_io.load_codes(depth_io.probe(str(out_path)))
_lo_edit = depth_io.to_unit(out, out_info)
_hi_small = np.stack([cv2.resize(depth_io.to_unit(f, depth_io.probe(str(out_path))), (W, H), interpolation=cv2.INTER_AREA)
                      for f in _hi])
check("...the same edit as at the depth map's size, give or take the resampling",
      float(np.abs(_hi_small - _lo_edit).mean()) < 0.02, f"{float(np.abs(_hi_small - _lo_edit).mean()):.4f}")
n_q = len(questions)
de.run("_delete_layer")
check("Delete asks first (the layer has masks)", until(lambda: len(questions) > n_q, 5) and "Delete" in questions[-1],
      str(questions[-1:]))
until(lambda: prompt.property("opened"), 5)
QMetaObject.invokeMethod(prompt, "finish", Q_ARG("QVariant", 0)); pump(0.4)   # No
check("...No keeps the layer", len(eng.project.layers) == 1 and len(S()["layers"]) == 1)
de.run("_delete_layer")
until(lambda: prompt.property("opened"), 5)
QMetaObject.invokeMethod(prompt, "finish", Q_ARG("QVariant", 1)); pump(0.4)   # Yes
check("...Yes deletes it", len(eng.project.layers) == 0 and S()["layers"] == [], str(S()["layers"]))

# audit 2026-09-28 (minor): every state change (a view, a frame, a slider)
# handed the Layers list a new array, and it jumped back to the top.
for _i in range(9):
    de.run("_new_layer"); pump(0.15)
lv = find(page, lambda c: c.objectName() == "deLayers")
until(lambda: lv.property("count") == 9, 5)
lv.setProperty("contentY", lv.property("contentHeight") - lv.property("height")); pump(0.2)
y0 = lv.property("contentY")
for view in ("Depth", "Edited"):
    de.setVar("view", view); pump(0.3)
de.selectLayer(8); pump(0.3)
check("the Layers list keeps its scroll through state changes", y0 > 0 and abs(lv.property("contentY") - y0) < 1,
      f"scrolled to {y0}, now {lv.property('contentY')}")
de.run("_new_layer"); pump(0.4)
check("...and a new layer is scrolled into view", lv.property("count") == 10
      and lv.property("contentY") + lv.property("height") >= lv.property("contentHeight") - 1,
      f"y {lv.property('contentY')} h {lv.property('height')} of {lv.property('contentHeight')}")

check("not busy, and no GPU held without SAM 3 loaded", not de.is_busy() and not de.holds_gpu())
de.releaseGpu()
check("Release GPU is safe with nothing loaded", not de.holds_gpu())

de.shutdown()
studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
