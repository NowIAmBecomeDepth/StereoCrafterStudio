"""Splatting's knobs (user, 2026-09-27), in the real app, offscreen: the sliders
the user named are DialFields; a double-click on one resets it to its default
(as right-click does); the Border Settings box shows / hides the Border Width
and Border Bias knobs and is remembered (studio.json), their values still set.
Configs and last_jobs.json redirected."""
import os, sys, tempfile, hashlib, time, json
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
from PySide6.QtCore import QObject, QCoreApplication, QEventLoop, QMetaObject, QPoint, QPointF, Qt, Q_ARG, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from studio import app as A

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
win.show()
fails = []
def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok: fails.append(label)
def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.005)
def walk(it, out):
    for c in it.childItems(): out.append(c); walk(c, out)
    return out

pump(0.6)
shell = objs["Shell"]
stage = [c for c in walk(win.contentItem(), []) if c.objectName() == "stage"][0]
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 1), Q_ARG("QVariant", True)); pump(1.0)
page = stage.property("pages").property(1).toQObject()
page.setProperty("popOpen", True); page.setProperty("breakOpen", True)
win.setWidth(2000); win.setHeight(1250); pump(1.0)
def dials():
    return {c.property("key"): c for c in walk(page, []) if c.metaObject().className().startswith(("DialField", "SpDial"))
            and c.isVisible()}

want = {"depth_dilate_size_x", "depth_dilate_size_y", "depth_blur_size_x", "depth_blur_size_y", "depth_dilate_left",
        "depth_blur_left", "depth_gamma", "max_disp", "zero_disparity_anchor", "border_width", "border_bias",
        "boost_amount", "boost_thresh", "boost_curve",
        "frame_break_bars", "frame_break_thresh", "fb_erode_left", "fb_erode_right", "fb_erode_global", "fb_edge_blur"}
d = dials()
check("the named sliders are knobs", want <= set(d), f"missing {sorted(want - set(d))}")
# Preview Brightness went back to a plain slider, as Merging's (user, 2026-09-30):
# it only brightens the preview
bright = [c for c in walk(page, []) if c.objectName() == "brightnessSlider" and c.isVisible()]
check("Preview Brightness is a plain slider (not a knob), bound to the setting",
      "preview_brightness" not in d and len(bright) == 1 and bright[0].metaObject().className().startswith(("SliderRow", "SpSlider"))
      and bright[0].property("key") == "preview_brightness", str([b.metaObject().className() for b in bright]))

# a double-click resets to the default
g = d["depth_gamma"]
QMetaObject.invokeMethod(g, "write", Q_ARG("QVariant", 1.7)); pump(0.2)
before = g.property("value")
c = g.mapToScene(QPointF(g.width() / 2, g.property("size") / 2 - g.property("size") * 0.3))
QTest.mouseDClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(c.x()), int(c.y()))); pump(0.3)
check("double-click a knob: back to its default (Gamma 1.70 -> 1.00)", abs(before - 1.7) < 1e-6
      and abs(g.property("value") - 1.0) < 1e-6, f"{before} -> {g.property('value')}")

# a wheel notch over a knob reaches the setting (Dial emits no moved() for it)
from PySide6.QtGui import QWheelEvent
bl = d["depth_blur_size_x"]
QMetaObject.invokeMethod(bl, "write", Q_ARG("QVariant", 3)); pump(0.2)
dl = [c for c in walk(bl, []) if c.metaObject().className().startswith("Dial_")][0]
wc = dl.mapToScene(QPointF(dl.width() / 2, dl.height() / 2))
QTest.mouseMove(win, QPoint(int(wc.x()), int(wc.y()))); pump(0.2)
QCoreApplication.sendEvent(win, QWheelEvent(QPointF(wc), QPointF(wc), QPoint(0, 0), QPoint(0, 120), Qt.NoButton,
                                            Qt.NoModifier, Qt.NoScrollPhase, False)); pump(0.3)
sp_st = objs["Splatting"].property("settings")
check("a wheel notch over Blur X: 3 -> 4, in the setting too", bl.property("value") == 4
      and int(float(sp_st.value("depth_blur_size_x"))) == 4, f"{bl.property('value')} / {sp_st.value('depth_blur_size_x')}")

# Border Settings: the box hides / shows the two border knobs, remembered
box = [c for c in walk(page, []) if c.objectName() == "borderSettingsCheck"][0]
check("Border Settings box, ticked by default, knobs shown", box.property("checked") is True
      and {"border_width", "border_bias"} <= set(dials()))
bw = d["border_width"]
QMetaObject.invokeMethod(bw, "write", Q_ARG("QVariant", 0.5)); pump(0.1)
box.setProperty("checked", False); QMetaObject.invokeMethod(box, "toggled"); pump(0.3)
check("unticked: Border Width and Bias hidden", not ({"border_width", "border_bias"} & set(dials()))
      and shell.property("splatBorderSettings") is False, str(sorted(set(dials()) & {"border_width", "border_bias"})))
check("...their values still set (hidden, not reset)", abs(bw.property("value") - 0.5) < 1e-6, str(bw.property("value")))
saved = json.loads((tmp / "config" / "studio.json").read_text())
check("...and remembered", saved.get("splat_border_settings") is False, str(saved.get("splat_border_settings")))
box.setProperty("checked", True); QMetaObject.invokeMethod(box, "toggled"); pump(0.3)
check("ticked again: both back", {"border_width", "border_bias"} <= set(dials()))

# Stereo Projection (user, 2026-09-27): Disparity, Gamma, Convergence, then the
# estimate; the checkboxes centred -- one row when it fits, else two, each centred
root = win.contentItem()
sd = [c for c in walk(page, []) if c.objectName() == "stereoDials"][0]
order = [c.property("key") or ("estimate" if c.property("text") == "≈" else None) for c in sd.childItems()
         if c.isVisible()]
check("Stereo knobs: Disparity, Gamma, Convergence, Pop-Out, then the estimate button",
      [k for k in order if k][:5] == ["max_disp", "depth_gamma", "zero_disparity_anchor", "pop_amount", "estimate"],
      str(order))
sc = [c for c in walk(page, []) if c.objectName() == "stereoChecks"][0]
sec = sc.parentItem()
while sec is not None and not sec.metaObject().className().startswith("Section_"):
    sec = sec.parentItem()
def mid(it):
    return it.mapToItem(root, QPointF(it.width() / 2, 0)).x(), it.mapToItem(root, QPointF(0, 0)).y()
for w, rows in ((2000, 1), (1460, 2)):
    win.setWidth(w); pump(1.0)
    a_, b_ = [[c for c in walk(page, []) if c.objectName() == n][0] for n in ("stereoChecksA", "stereoChecksB")]
    (ax, ay), (bx, by), (sx, _) = mid(a_), mid(b_), mid(sec)
    got = 1 if abs(ay - by) < 2 else 2
    centred = (abs((a_.mapToItem(root, QPointF(0, 0)).x() + b_.mapToItem(root, QPointF(b_.width(), 0)).x()) / 2 - sx) < 3
               if got == 1 else abs(ax - sx) < 3 and abs(bx - sx) < 3)
    check(f"{w} px: the checkboxes in {rows} row(s), centred in the box", got == rows and centred,
          f"rows {got}; centres {ax:.0f} / {bx:.0f}, box {sx:.0f}")
win.setWidth(2000); pump(0.5)
folds = {c.property("title"): c for c in walk(page, []) if c.metaObject().className().startswith("FoldSection")}
check("Splatting & Output Settings and Dev Tools open with a V, not a box",
      folds["Splatting & Output Settings"].property("chevron") is True and folds["Dev Tools"].property("chevron") is True)
check("Special FX (Pop Out Boost, Keyframe, Frame Break) open with a V too",
      all(folds[t].property("chevron") is True for t in ("Pop Out Boost", "Keyframe", "Frame Break")))
# closing a Special FX fold only hides it (user, 2026-09-27: the clip list needs the room)
bs = [c for c in walk(page, []) if c.property("key") == "boost_amount" and c.metaObject().className().startswith("SpDial")][0]
QMetaObject.invokeMethod(bs, "write", Q_ARG("QVariant", 5)); pump(0.2)
pop = folds["Pop Out Boost"]
QMetaObject.invokeMethod(pop, "toggled", Q_ARG(bool, False)); pump(0.4)
check("...closing Pop Out Boost only hides it: Strength stays, and the heading says it is on",
      pop.property("open") is False and abs(bs.property("value") - 5) < 1e-9 and "on" in pop.property("title"),
      f"open {pop.property('open')} strength {bs.property('value')} title {pop.property('title')!r}")
QMetaObject.invokeMethod(pop, "toggled", Q_ARG(bool, True)); pump(0.4)
QMetaObject.invokeMethod(bs, "write", Q_ARG("QVariant", 0)); pump(0.2)
enc = [c for c in walk(page, []) if c.objectName() == "encodingButton"][0]
check("Encoding Options is a filled (Start-style) button with the video icon", enc.property("kind") == "filled"
      and enc.property("iconGlyph") != "", f"{enc.property('kind')} {enc.property('iconGlyph')!r}")

studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
