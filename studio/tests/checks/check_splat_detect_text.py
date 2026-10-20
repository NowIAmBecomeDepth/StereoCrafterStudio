"""Headless QML: Detect Text (the Subtitle Mask editor) in the real app over a
TEMP copy of the fixture: open, controls, drawn rectangle, zoom, Save (mask
video + sidecar), bad From/To, reopen from the sidecar, cancel mid-render."""
import os, sys, json, tempfile, hashlib, time, shutil
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
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, QObject, QPoint, QPointF, Qt, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from studio import app as A
import cv2
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
def click_item(window, item):
    c = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(int(c.x()), int(c.y())))
pump(0.6)
stage = find(win.contentItem(), lambda c: c.objectName() == "stage")
pages = stage.property("pages")
for i in range(6):
    p = pages.property(i).toQObject()
    if p is not None and p.metaObject().className().startswith("SplattingPage"):
        page, idx = p, i
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", idx), Q_ARG("QVariant", True)); pump(1.2)
sm = page.findChild(QObject, "subMaskWin")
btn = find(page, lambda c: c.metaObject().className().startswith("AccentButton") and c.property("text") == "Detect Text")
check("Detect Text button live", btn is not None and btn.isEnabled() and btn.isVisible())
prompt = page.property("prompt")
click_item(win, btn); pump(0.5)
check("refused before a load", not sm.isVisible() and prompt.property("opened")
      and prompt.property("message") == "Load a clip in the previewer first.", prompt.property("message"))
QMetaObject.invokeMethod(prompt, "finish", Q_ARG("QVariant", 1)); pump(0.4)

for k, v in {"input_source_clips": str(FIX / "src").replace("\\", "/"),
             "input_depth_maps": str(FIX / "depth").replace("\\", "/"),
             "output_splatted": str(FIX / "out").replace("\\", "/"), "multi_map": False,
             "preview_source": "Splat Result", "preview_size": "100%", "border_mode": "Manual",
             "auto_save_sidecar": False, "update_slider_from_sidecar": True, "auto_convergence_mode": "Off"}.items():
    sp._set(k, v)
pv.load(); settle()
pv.seek(7); settle()
clip = Path(pv._engine.previewer.video_list[pv._engine.previewer.current_video_index]["source_video"])
n_clip = int(cv2.VideoCapture(str(clip)).get(cv2.CAP_PROP_FRAME_COUNT))
print("   clip", clip.name, "frames", n_clip)

click_item(win, btn); settle()
s = sm.property("s").toVariant() if hasattr(sm.property("s"), "toVariant") else sm.property("s")
check("opened on the preview's clip and frame", sm.isVisible() and s["frame"] == 7 and clip.name in s["title"], s.get("title"))
check("v1's size", (sm.width(), sm.height()) == (1180, 820))
# Text Grow / Soften: only once the clip has a saved mask (user, 2026-10-02)
dials = find(page, lambda c: c.objectName() == "textMaskDials")
def dials_shown():
    return bool(dials.property("visible")) and bool(pv.property("hasTextMask"))
check("no mask yet: Text Grow / Soften hidden", dials is not None and not dials.property("visible")
      and not pv.property("hasTextMask"))
check("defaults (no sidecar)", (s["core"], s["edge"], s["r"], s["edge_mode"], s["color"]) == (200, 165, 2, "Hard", "Any"))
toBox = find(sm.contentItem(), lambda c: c.objectName() == "submaskTo")
check("To = last frame", toBox.property("text") == str(n_clip - 1), toBox.property("text"))
ed = pv._editors["submask"]

# Core slider (SliderRow.write), then the debounce
core = find(sm.contentItem(), lambda c: c.metaObject().className().startswith("SliderRow") and c.property("key") == "core")
QMetaObject.invokeMethod(core, "write", Q_ARG("QVariant", 120)); pump(0.2); settle()
check("Core reaches the editor", ed.settings["core"] == 120, str(ed.settings["core"]))
m120 = int((ed.mask > 0).sum())
QMetaObject.invokeMethod(core, "write", Q_ARG("QVariant", 250)); pump(0.2); settle()
check("a higher Core keys less", int((ed.mask > 0).sum()) <= m120, f"{m120} -> {int((ed.mask > 0).sum())}")
QMetaObject.invokeMethod(core, "write", Q_ARG("QVariant", 100)); pump(0.2); settle()

# drag the detection rectangle
pic = find(sm.contentItem(), lambda c: c.metaObject().className().startswith("QQuickImage"))
pw, ph = pic.property("paintedWidth"), pic.property("paintedHeight")
ox, oy = (pic.width() - pw) / 2, (pic.height() - ph) / 2
a = pic.mapToScene(QPointF(ox + 0.1 * pw, oy + 0.7 * ph)); b = pic.mapToScene(QPointF(ox + 0.9 * pw, oy + 0.95 * ph))
QTest.mousePress(sm, Qt.LeftButton, Qt.NoModifier, QPoint(int(a.x()), int(a.y())))
for t in (0.3, 0.6, 1.0):
    QTest.mouseMove(sm, QPoint(int(a.x() + (b.x() - a.x()) * t), int(a.y() + (b.y() - a.y()) * t))); pump(0.03)
QTest.mouseRelease(sm, Qt.LeftButton, Qt.NoModifier, QPoint(int(b.x()), int(b.y()))); settle()
r = ed.settings["rect"]; vw, vh = ed.vw, ed.vh
check("rectangle in video px", r is not None and abs(r[0] - 0.1 * vw) <= 2 and abs(r[3] - 0.95 * vh) <= 2, f"{r} of {vw}x{vh}")
check("mask limited to it", ed.mask[: max(0, r[1] - 1)].max() == 0 if r and r[1] > 1 else True)
QMetaObject.invokeMethod(sm, "openFor"); pump(0.1)  # already open: raise only
click_item(sm, find(sm.contentItem(), lambda c: c.property("text") == "Zoom Rect")); settle()
s = sm.property("s").toVariant() if hasattr(sm.property("s"), "toVariant") else sm.property("s")
check("Zoom Rect crops to the rectangle + pad", s["zoomed"] and s["view"][2] < vw and s["view"][0] == max(0, r[0] - 24), str(s["view"]))
click_item(sm, find(sm.contentItem(), lambda c: c.property("text") == "Fit")); settle()
s = sm.property("s").toVariant() if hasattr(sm.property("s"), "toVariant") else sm.property("s")
check("Fit", not s["zoomed"] and s["view"] == [0, 0, vw, vh], str(s["view"]))
fromBox_ = find(sm.contentItem(), lambda c: c.objectName() == "submaskFrom")
sm.requestActivate(); pump(0.3)
_amb = []
for o in [o for o in page.findChildren(QObject) if "Shortcut" in o.metaObject().className()] +          [o for o in sm.findChildren(QObject) if "Shortcut" in o.metaObject().className()]:
    o.activatedAmbiguously.connect(lambda o=o: _amb.append(o.property("sequence")))
QTest.keyClick(sm, Qt.Key_Right); settle()
check("Right key steps the editor (not the main preview)", ed.frame_idx == 8 and pv.frame == 7 and not _amb, f"editor {ed.frame_idx} preview {pv.frame} ambiguous {_amb}")
QTest.keyClick(sm, Qt.Key_Left, Qt.ShiftModifier); settle()
check("Shift+Left steps 10", ed.frame_idx == 0, str(ed.frame_idx))

# While Save renders (bug sweep 2026-10-09, A6): the editor refuses edits and steps (the
# render shares its capture, and the sidecar must get what was rendered), and the window
# greys the controls out. A stand-in render: a real one on this clip is over in a blink.
import threading as _th
_core0 = ed.settings["core"]
ed._render_thread = _th.current_thread()
pv.editorOp("submask", "set", [dict(ed.settings, core=17), False]); settle()
pv.editorOp("submask", "seek", [5]); settle()
check("while Save renders, the editor takes no edit or seek", ed.settings["core"] == _core0 and ed.frame_idx == 0,
      f"core {ed.settings['core']} frame {ed.frame_idx}")
_s = sm.property("s").toVariant() if hasattr(sm.property("s"), "toVariant") else sm.property("s")
sm.setProperty("s", dict(_s, rendering=True)); pump(0.2)
_scrub = find(sm.contentItem(), lambda c: c.objectName() == "submaskScrub")
_core = find(sm.contentItem(), lambda c: c.property("label") == "Core")
QTest.keyClick(sm, Qt.Key_Right); settle()
check("...and the window greys the scrubber, the sliders and the From box; Right does nothing",
      _scrub is not None and not _scrub.isEnabled() and _core is not None and not _core.isEnabled()
      and not fromBox_.isEnabled() and ed.frame_idx == 0,
      f"scrub {_scrub and _scrub.isEnabled()} core {_core and _core.isEnabled()} from {fromBox_.isEnabled()} frame {ed.frame_idx}")
ed._render_thread = None
sm.setProperty("s", _s); pump(0.2)
pv.editorOp("submask", "seek", [0]); settle()
# the main window's own keys, and Full Preview's, now that each needs its window active
win.requestActivate(); pump(0.3)
QTest.keyClick(win, Qt.Key_Right); settle()
check("main page Right steps the preview", pv.frame == 8 and not _amb, f"{pv.frame} {_amb}")
fp = find(page, lambda c: False)
fpw = [o for o in page.findChildren(QObject) if o.metaObject().className().startswith("FullPreviewWindow")][0]
for o in [o for o in fpw.findChildren(QObject) if "Shortcut" in o.metaObject().className()]:
    o.activatedAmbiguously.connect(lambda o=o: _amb.append("fp:" + o.property("sequence")))
fpw.show(); fpw.requestActivate(); pump(0.4)
QTest.keyClick(fpw, Qt.Key_Right); settle()
check("Full Preview Right steps the preview", pv.frame == 9 and not _amb, f"{pv.frame} {_amb}")
fpw.close(); sm.requestActivate(); pump(0.3)
cur0 = stage.property("current")
QTest.keyClick(sm, Qt.Key_Tab, Qt.ControlModifier); pump(0.3)
QTest.keyClick(sm, Qt.Key_F4, Qt.AltModifier); pump(0.3)
check("Ctrl+Tab / Alt+F4 in the editor leave the main window alone", win.isVisible() and stage.property("current") == cur0,
      f"main visible {win.isVisible()} stage {cur0}->{stage.property('current')}")
win.requestActivate(); pump(0.3)
QTest.keyClick(win, Qt.Key_Tab, Qt.ControlModifier); pump(0.6)
cur1 = stage.property("current")
QTest.keyClick(win, Qt.Key_Tab, Qt.ControlModifier | Qt.ShiftModifier); pump(0.6)
check("Ctrl+Tab still works in the main window", cur1 != cur0 and stage.property("current") == cur0, f"{cur0}->{cur1}->{stage.property('current')}")
QTest.keyClick(win, Qt.Key_F11); pump(0.4)
# F11 in the main window opens Full Preview (user, 2026-10-13: full screen on Studio's screen)
check("F11 in the main window opens Full Preview", fpw.isVisible())
fpw.close(); pump(0.3)
sm.requestActivate(); pump(0.3)

# bad From/To -> v1's error in the editor
fromBox = find(sm.contentItem(), lambda c: c.objectName() == "submaskFrom")
fromBox.setProperty("text", "x")
save = find(sm.contentItem(), lambda c: c.property("text") == "Save")
click_item(sm, save); settle(); pump(0.3)
alert = find(sm.contentItem(), lambda c: False)
alerts = [o for o in sm.findChildren(QObject) if o.metaObject().className().startswith("PromptDialog")]
check("bad From/To -> error in the editor", alerts and alerts[0].property("opened")
      and alerts[0].property("message") == "Bad From/To frame numbers.", alerts[0].property("message") if alerts else None)
QMetaObject.invokeMethod(alerts[0], "finish", Q_ARG("QVariant", 1)); pump(0.3)

# Save 5..20 -> mask video + sidecar, window gone
fromBox.setProperty("text", "5"); toBox.setProperty("text", "20")
status = []; pv.statusMessage.connect(status.append)
click_item(sm, save); pump(0.3)
end = time.time() + 60
while sm.isVisible() and time.time() < end:
    pump(0.1)
settle()
out = clip.with_name(clip.stem + "_submask.mp4")
cap = cv2.VideoCapture(str(out)); n_out = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
ok0, f0 = cap.read()
cap.release()          # held open, the next Save could not replace the file (WinError 5)
check("saved: the window closes", not sm.isVisible())
check("...and Text Grow / Soften appear", dials_shown())
# Text Clean (user, 2026-10-02): the preview takes the text out of the depth
# before Dilate / Blur, as the render does, at the clip's distance in its size
import core.splatting.subtitle_mask_adjust as _SMA
_real_clean, _cleans = _SMA.clean_depth, []
_SMA.clean_depth = lambda d, m, r: (_cleans.append((r, d.shape)), _real_clean(d, m, r))[1]
sp._set("submask_clean", 12); settle()
_SMA.clean_depth = _real_clean
check("Text Clean dial shown with the others", find(page, lambda c: c.objectName() == "textCleanDial").property("visible"))
check("Text Clean runs in the preview", bool(_cleans) and _cleans[-1][0] > 0, _cleans[-1:])
sp._set("submask_clean", 0); settle()
# Text Conv (user, 2026-10-11): the text's own Convergence, beside the others; the preview
# redraws with it and the text moves
check("Text Conv dial shown with the others", find(page, lambda c: c.objectName() == "textConvDial").property("visible"))
def _frame():
    from PySide6.QtCore import QSize
    return pv.provider.requestImage("frame/" + str(pv.property("imageSerial")), QSize(), QSize())
_before, _serial = _frame(), pv.property("imageSerial")
sp._set("submask_conv", -0.4); settle()
_after = _frame()
check("Text Conv redraws the preview, and the picture changes",
      pv.property("imageSerial") != _serial and _after != _before, f"serial {_serial} -> {pv.property('imageSerial')}")
sp._set("submask_conv", 0); settle()
check("mask video: black before From, To+1 frames", out.exists() and n_out == 21 and ok0 and f0.max() <= 16, f"frames {n_out} first max {f0.max() if ok0 else None}")
sc = sorted((FIX / "depth").glob(clip.stem + "*.fssidecar"))
data = json.loads(sc[0].read_text()) if sc else {}
check("sidecar: submask_* + enabled", data.get("submask_enabled") is True and data.get("submask_core") == 100
      and data.get("submask_rect") == list(r), str({k: v for k, v in data.items() if k.startswith("submask")}))
check("v1's status line", status and status[-1] == f"Subtitle mask saved: {out.name}", status[-1:])

# reopen: settings from the sidecar
click_item(win, btn); settle()
s = sm.property("s").toVariant() if hasattr(sm.property("s"), "toVariant") else sm.property("s")
check("reopen reads the sidecar", sm.isVisible() and s["core"] == 100 and s["rect"] == list(r), f"core {s['core']} rect {s['rect']}")

# selections (user, 2026-10-02): + Add makes a second, its own range and Core;
# the chips switch which one the controls edit; Save renders both, black between
def walk(it, out):
    for c in it.childItems(): out.append(c); walk(c, out)
    return out
def sget():
    v = sm.property("s")
    return v.toVariant() if hasattr(v, "toVariant") else v
def chips():
    return [c for c in walk(sm.contentItem(), []) if c.objectName() == "submaskSegment" and c.isVisible()]
add = find(sm.contentItem(), lambda c: c.objectName() == "submaskAddSegment")
pv.editorOp("submask", "seek", [30]); settle()
click_item(sm, add); settle(); pump(0.3)
check("+ Add makes a second selection from the frame showing", len(chips()) == 2 and sget()["current"] == 1
      and (sget()["from"], sget()["to"]) == (30, n_clip - 1), (len(chips()), sget().get("current"), sget().get("from")))
check("...From / To follow it", (fromBox.property("text"), toBox.property("text")) == ("30", str(n_clip - 1)))
# Set From / Set To (and I / O) mark the frame showing
pv.editorOp("submask", "seek", [32]); settle()
click_item(sm, find(sm.contentItem(), lambda c: c.objectName() == "submaskMarkIn")); settle()
pv.editorOp("submask", "seek", [40]); settle()
sm.requestActivate(); pump(0.2)
QTest.keyClick(sm, Qt.Key_O); settle()
check("Set From / O mark the frame showing", (sget()["from"], sget()["to"]) == (32, 40)
      and (fromBox.property("text"), toBox.property("text")) == ("32", "40"), (sget().get("from"), sget().get("to")))
fromBox.setProperty("text", "30"); toBox.setProperty("text", "40")
pv.editorOp("submask", "set_range", ["30", "40"]); settle()
QMetaObject.invokeMethod(core, "write", Q_ARG("QVariant", 180)); pump(0.2); settle()
check("its own Core and range", (ed.segments[0]["core"], ed.segments[1]["core"]) == (100, 180)
      and (ed.segments[1]["from"], ed.segments[1]["to"]) == (30, 40), [(sg["core"], sg["from"], sg["to"]) for sg in ed.segments])
marks = [c for c in walk(sm.contentItem(), []) if c.objectName() == "submaskRangeMarks"]
check("both ranges marked on the scrubber",
      marks and len([c for c in marks[0].childItems() if c.metaObject().className().startswith("QQuickRectangle") and c.isVisible()]) == 2)
# freeze on selection 2 (user, 2026-10-02; fades removed the same day)
def byname(n):
    return find(sm.contentItem(), lambda c: c.objectName() == n)
pv.editorOp("submask", "seek", [35]); settle()
click_item(sm, byname("submaskFreeze")); settle(); pump(0.2)
check("Freeze Here freezes the current selection", sget()["hold"] == 35 and byname("submaskHoldMark").isVisible()
      and byname("submaskFreeze").property("text").startswith("Frozen on 35"), sget().get("hold"))
check("no fade controls any more", byname("submaskFadeIn") is None and byname("submaskClearFades") is None)
click_item(sm, byname("submaskFreeze")); settle(); pump(0.2)
check("the Frozen button unfreezes", sget()["hold"] == -1 and not byname("submaskHoldMark").isVisible())
pv.editorOp("submask", "seek", [35]); settle()
click_item(sm, byname("submaskFreeze")); settle()
click_item(sm, chips()[0]); settle(); pump(0.3)
check("a chip switches the controls to its selection", sget()["current"] == 0 and sget()["core"] == 100
      and fromBox.property("text") == "5" and core.property("value") == 100, (sget().get("current"), sget().get("core"), fromBox.property("text")))
click_item(sm, save); pump(0.3)
end = time.time() + 60
while sm.isVisible() and time.time() < end:
    pump(0.1)
settle()
cap = cv2.VideoCapture(str(out)); n_out = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
cap.set(cv2.CAP_PROP_POS_FRAMES, 25); okg, fg = cap.read(); cap.release()
check("two selections: one video to the last To, black in the gap", n_out == 41 and okg and fg.max() <= 16, f"frames {n_out}")
data = json.loads(sorted((FIX / "depth").glob(clip.stem + "*.fssidecar"))[0].read_text())
check("sidecar: both selections (the second frozen)",
      [(g["from"], g["to"], g["core"], g.get("hold")) for g in data.get("submask_segments", [])]
      == [(5, 20, 100, None), (30, 40, 180, 35)],
      data.get("submask_segments"))
click_item(win, btn); settle()
check("reopen: both selections back", len(chips()) == 2 and sget()["current"] == 0, len(chips()))

# Remove takes the last selection too; Save then reads Remove Mask and takes
# the clip's mask off (user, 2026-10-02)
rm = byname("submaskRemoveSegment")
click_item(sm, rm); settle(); pump(0.2)
click_item(sm, rm); settle(); pump(0.3)
savebtn = byname("submaskSave")
check("no selections: Save reads Remove Mask", len(chips()) == 0 and savebtn.property("text") == "Remove Mask"
      and not fromBox.isVisible(), (len(chips()), savebtn.property("text")))
click_item(sm, savebtn); pump(0.3)
end = time.time() + 30
while sm.isVisible() and time.time() < end:
    pump(0.1)
settle()
data = json.loads(sorted((FIX / "depth").glob(clip.stem + "*.fssidecar"))[0].read_text())
check("Remove Mask: the video gone, the sidecar's mask off", not out.exists() and data.get("submask_enabled") is False
      and data.get("submask_segments") == [], (out.exists(), data.get("submask_enabled"), data.get("submask_segments")))
check("...and Text Grow / Soften hide again", not dials.property("visible") and not pv.property("hasTextMask"))
click_item(win, btn); settle()
check("reopen: no selections", len(chips()) == 0 and sm.isVisible())
click_item(sm, add); settle(); pump(0.3)
check("+ Add brings one back", len(chips()) == 1 and fromBox.isVisible())

# Reset All (user, 2026-10-02): confirmed, everything back to a fresh start and
# the sidecar's Detect Text settings erased
pv.editorOp("submask", "seek", [10]); settle()
click_item(sm, byname("submaskFreeze")); settle()
QMetaObject.invokeMethod(core, "write", Q_ARG("QVariant", 140)); pump(0.2); settle()
click_item(sm, byname("submaskResetAll")); pump(0.4)
alerts = [o for o in sm.findChildren(QObject) if o.metaObject().className().startswith("PromptDialog")]
check("Reset All asks first", alerts and alerts[0].property("opened") and alerts[0].property("title") == "Reset All")
QMetaObject.invokeMethod(alerts[0], "finish", Q_ARG("QVariant", 1)); pump(0.3); settle(); pump(0.3)
st = sget()
check("...then everything is back to a fresh start",
      len(chips()) == 1 and (st["from"], st["to"], st["core"], st["hold"], st["rect"]) == (0, n_clip - 1, 200, -1, [])
      and core.property("value") == 200, (st.get("from"), st.get("to"), st.get("core"), st.get("hold"), st.get("rect"), core.property("value")))
data = json.loads(sorted((FIX / "depth").glob(clip.stem + "*.fssidecar"))[0].read_text())
left = {k: v for k, v in data.items() if k.startswith("submask")
        and k not in ("submask_enabled", "submask_grow", "submask_soften", "submask_clean", "submask_conv")}   # those: Splatting's Text dials
check("...and the sidecar's Detect Text settings erased", not left and data.get("submask_enabled") is False, left)
if os.environ.get("SUBMASK_SHOT"):          # a picture of the window, for a look
    pv.editorOp("submask", "select_segment", [1]); settle(); pump(0.4)
    from PySide6.QtQml import QQmlProperty
    for c in walk(sm.contentItem(), []):          # layers draw nothing offscreen
        try:
            if QQmlProperty(c, "layer.enabled").read(): QQmlProperty(c, "layer.enabled").write(False)
        except RuntimeError:
            pass
    pump(0.3)
    sm.grabWindow().save(os.environ["SUBMASK_SHOT"])

# cancel mid-render: slow the key down, Save, then close the window
import studio.stages.splatting.submask as SM
_cm = SM.compute_mask
SM.compute_mask = lambda f, st: (time.sleep(0.15), _cm(f, st))[1]
fromBox.setProperty("text", "0"); toBox.setProperty("text", str(n_clip - 1))
click_item(sm, save); pump(0.6)
check("Save disabled while rendering", not save.isEnabled())
sm.close(); pump(0.3)
check("closing mid-render only stops it", sm.isVisible())
end = time.time() + 30
while ed._render_thread is not None and time.time() < end:
    pump(0.1)
settle(); pump(0.3)
st_lbl = find(sm.contentItem(), lambda c: c.objectName() == "submaskStatus")
check("Cancelled.", st_lbl.property("text") == "Cancelled." and save.isEnabled(), st_lbl.property("text"))
SM.compute_mask = _cm
sm.close(); settle()
check("closed", not sm.isVisible() and not ed.open and ed.cap is None)

# Flatten Layers (user, 2026-10-02: opening credits need many masks): the
# selections so far saved and set aside, the window open for fresh ones
click_item(win, btn); settle()
check("reopened, a selection to flatten", sm.isVisible() and len(chips()) >= 1, len(chips()))
fromBox.setProperty("text", "5"); toBox.setProperty("text", "12")
click_item(sm, byname("submaskFlatten")); pump(0.4)
end = time.time() + 60
while ed._render_thread is not None and time.time() < end:
    pump(0.1)
settle(); pump(0.4)
flat_marks = [c for c in walk(sm.contentItem(), []) if c.objectName() == "submaskFlatMark" and c.isVisible()]
check("Flatten Layers: the window stays open, the list empty, the range kept as flattened",
      sm.isVisible() and len(chips()) == 0 and [dict(f) for f in sget()["flat"]][-1:] == [{"from": 5, "to": 12}] and flat_marks,
      (sm.isVisible(), len(chips()), sget().get("flat"), len(flat_marks)))
check("...Save stays Save (the flattened ones are the mask)", byname("submaskSave").property("text") == "Save",
      byname("submaskSave").property("text"))
data = json.loads(sorted((FIX / "depth").glob(clip.stem + "*.fssidecar"))[0].read_text())
check("...the sidecar: flattened kept, the list empty, the mask on",
      len(data.get("submask_flat") or []) >= 1 and data.get("submask_segments") == [] and data.get("submask_enabled") is True
      and out.exists(), {k: data.get(k) for k in ("submask_flat", "submask_segments", "submask_enabled")})
sm.close(); settle()

# Splatting's keys (user, 2026-10-02), pressed on the main window
win.requestActivate(); pump(0.4)
def key(k, mods=Qt.NoModifier):
    QTest.keyClick(win, k, mods); settle()
key(Qt.Key_End)
check("End: the last frame", pv.frame == n_clip - 1, pv.frame)
key(Qt.Key_Home)
check("Home: the first frame", pv.frame == 0, pv.frame)
pv.clearAllKeyframes(); settle()
pv.seek(4); settle(); key(Qt.Key_S)
pv.seek(9); settle(); key(Qt.Key_D)
check("S / D: a static and a dynamic keyframe", [(k["frame"], k["kind"]) for k in pv.keyframes] == [(4, "static"), (9, "dynamic")],
      pv.keyframes)
pv.clearAllKeyframes(); settle()
sidecars_before = len(list((FIX / "depth").glob(clip.stem + "*.fssidecar")))
key(Qt.Key_S, Qt.ControlModifier); pump(0.4)
asked = prompt.property("opened") and "Overwrite" in str(prompt.property("title") or prompt.property("message"))
if prompt.property("opened"):
    QMetaObject.invokeMethod(prompt, "finish", Q_ARG("QVariant", 1)); settle()
check("Ctrl+S: the sidecar saved (asking first, as the button does, when one exists)",
      pv.sidecarExists and (asked or sidecars_before == 0), (sidecars_before, asked))
key(Qt.Key_T, Qt.ControlModifier); pump(0.6)
check("Ctrl+T: Detect Text opens", sm.isVisible())
sm.close(); settle()
DE = objs["DepthEdit"]
win.requestActivate(); pump(0.4)          # Detect Text had the keyboard
key(Qt.Key_M, Qt.ControlModifier); pump(1.0)
got = Path((getattr(DE, "_from_splat", None) or {}).get("rgb", "")).name
check("Ctrl+M: Edit Depth Map -- this clip, into the Extra tab",
      got == clip.name and stage.property("current") == 4, (got, stage.property("current")))
pv.shutdown()
studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
