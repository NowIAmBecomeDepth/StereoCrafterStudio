"""Headless QML: Splatting's Convergence picker (user, 2026-10-29: "click on the area you want the
convergence to be clean on"), in the real app over a TEMP copy of the small fixture. Its depth is a
bright disc (1 at the middle, falling to 0 half a width out), so the depth under a click is known.
The pick button arms it, a click on the picture sets Convergence to the depth there -- with Gamma
applied, as the warp sees it -- turns Auto-Converge Off and does not flip the picture. Configs and
last_jobs.json redirected."""
import os, sys, tempfile, hashlib, time
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
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, QPoint, QPointF, Qt, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
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
sp = objs["Splatting"]; pv = sp.preview
def settle(t=120):
    end = time.time() + t
    while time.time() < end:
        pump(0.05)
        with pv._cv:
            idle = not pv._jobs and not pv._inflight
        if idle and not pv._loading and not pv._render_timer.isActive():
            pump(0.4)
            with pv._cv:
                if not pv._jobs and not pv._inflight: return
def find(it, pred):
    for c in it.childItems():
        if pred(c): return c
        r = find(c, pred)
        if r is not None: return r
pump(0.6)
stage = find(win.contentItem(), lambda c: c.objectName() == "stage")
pages = stage.property("pages")
page = idx = None
for i in range(8):
    p = pages.property(i).toQObject()
    if p is not None and p.metaObject().className().startswith("SplattingPage"):
        page, idx = p, i
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", idx), Q_ARG("QVariant", True)); pump(1.2)
win.setWidth(2000); win.setHeight(1250); pump(0.8)

for k, v in {"input_source_clips": str(FIX / "src").replace("\\", "/"),
             "input_depth_maps": str(FIX / "depth").replace("\\", "/"),
             "output_splatted": str(FIX / "out").replace("\\", "/"), "multi_map": False,
             "preview_source": "Splat Result", "preview_size": "100%", "border_mode": "Manual",
             "auto_save_sidecar": False, "update_slider_from_sidecar": True, "auto_convergence_mode": "Off",
             "depth_gamma": 1.0, "depth_dilate_size_x": 0, "depth_dilate_size_y": 0, "depth_blur_size_x": 0,
             "depth_blur_size_y": 0, "depth_dilate_left": 0, "depth_blur_left": 0, "zero_disparity_anchor": 0.5,
             "enable_global_norm": False}.items():
    sp._set(k, v)
pv.load(); settle()
check("preview loaded", pv.loaded)

btn = find(page, lambda c: c.objectName() == "pickConvButton")
img = find(page, lambda c: c.objectName() == "previewImage")
from PySide6.QtCore import QObject
flip = page.findChild(QObject, "flipCard")
check("the pick button sits by Convergence, enabled", btn is not None and btn.isVisible() and btn.isEnabled())

def conv():
    return float(sp._map.value("zero_disparity_anchor"))
def pick(fx, fy):
    """Arm with the button, click the picture at (fx, fy) of it, wait for the dial."""
    c = btn.mapToScene(QPointF(btn.width() / 2, btn.height() / 2))
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(c.x()), int(c.y()))); pump(0.2)
    armed = bool(page.property("pickingConv"))
    shown = img.parentItem()
    p = shown.mapToScene(QPointF(shown.width() * fx, shown.height() * fy))
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(p.x()), int(p.y())))
    pump(0.6); settle()
    return armed

# frame 0: the disc is centred, brightest in the middle (the map reads 0.50 a quarter in, 0.15 a tenth in)
side0 = (flip.property("backUp"), str(sp._map.value("preview_source")))
armed = pick(0.25, 0.5)
check("armed by the button, the click picks: a quarter in -> Convergence ~0.50", armed
      and abs(conv() - 0.50) <= 0.03 and not page.property("pickingConv"), f"{conv():.3f}")
side1 = (flip.property("backUp"), str(sp._map.value("preview_source")))
check("...the click did not flip the picture", side1 == side0 and not flip.property("busy"), f"{side0} -> {side1}")
pick(0.10, 0.5)
check("a tenth in -> ~0.15", abs(conv() - 0.15) <= 0.03, f"{conv():.3f}")

# Gamma: the depth as the warp sees it, 1 - (1 - d) ** gamma
sp._set("depth_gamma", 2.0); settle()
pick(0.25, 0.5)
check("Gamma 2: the same point -> ~0.75 (the warp's depth, not the raw map)", abs(conv() - 0.75) <= 0.03, f"{conv():.3f}")
sp._set("depth_gamma", 1.0); settle()

# Original (Left Eye) stops before the depth in a render: the picker still reads it
sp._set("preview_source", "Original (Left Eye)"); settle()
pick(0.25, 0.5)
check("on Original (Left Eye) too -> ~0.50", abs(conv() - 0.50) <= 0.03, f"{conv():.3f}")
sp._set("preview_source", "Splat Result"); settle()

# Auto-Converge would put its own value back: a pick turns it Off
sp._set("auto_convergence_mode", "Average"); pump(0.3)
pick(0.10, 0.5)
check("a pick turns Auto-Converge Off", str(sp._map.value("auto_convergence_mode")) == "Off"
      and abs(conv() - 0.15) <= 0.03, f"{sp._map.value('auto_convergence_mode')} {conv():.3f}")

# armed, then the button again: cancelled, the next click flips as before
c = btn.mapToScene(QPointF(btn.width() / 2, btn.height() / 2))
QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(c.x()), int(c.y()))); pump(0.2)
QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(c.x()), int(c.y()))); pump(0.2)
check("the button again cancels", not page.property("pickingConv"))

# C does what the button does (user, 2026-10-30): arms, a click picks; C again cancels
win.requestActivate(); pump(0.2)
QTest.keyClick(win, Qt.Key_C); pump(0.2)
armed_by_key = bool(page.property("pickingConv"))
shown = img.parentItem()
p = shown.mapToScene(QPointF(shown.width() * 0.25, shown.height() * 0.5))
QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(p.x()), int(p.y()))); pump(0.6); settle()
check("C arms the picker, the click picks -> ~0.50", armed_by_key and abs(conv() - 0.50) <= 0.03
      and not page.property("pickingConv"), f"armed {armed_by_key}, {conv():.3f}")
QTest.keyClick(win, Qt.Key_C); pump(0.2)
QTest.keyClick(win, Qt.Key_C); pump(0.2)
check("...C again cancels", not page.property("pickingConv"))

pv.shutdown()
studio_warns = [w for w in warns if "SplattingPage" in w or "DialField" in w or "AccentButton" in w]
check("no QML warnings from the Splatting page", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
