"""Headless QML: the mouse's side buttons (user, 2026-10-30), in the real app over a TEMP copy of the small
fixture. Settings > Studio lists both with every action and None by default; a side-button press on the
Splatting page runs the action picked for it, as the button would -- over the picture, a dial or a button
alike -- does nothing while None or on another stage, and a bad saved value reads as None. The cover that
catches them lets a left click and the wheel through. Configs and last_jobs.json redirected."""
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
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, QObject, Q_ARG, QPoint, QPointF, Qt, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from studio import app as A
qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
shell = objs["Shell"]
shell.setProperty("uiScale", "1.0")
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
def walk(it, out):
    for c in it.childItems():
        out.append(c); walk(c, out)
    return out
pump(0.6)

check("both side buttons do nothing to begin with",
      shell.property("mouseBackAction") == "None" and shell.property("mouseForwardAction") == "None")
acts = list(shell.property("mouseActions"))
check("the actions: None first, the flip card and Pick Convergence among them, no job or slider ones",
      acts[0] == "None" and "Flip Preview Card" in acts and "Pick Convergence" in acts
      and not any(w in a for a in acts for w in ("Start", "Stop", "Clear All", "Disparity", "Gamma")), str(acts))
shell.setProperty("mouseBackAction", "Delete Everything"); pump(0.1)
check("a value not on the list reads as None", shell.property("mouseBackAction") == "None")

# Settings > Studio: the panel, its two lists
settings = win.findChild(QObject, "settingsDialog")
QMetaObject.invokeMethod(settings, "open"); pump(0.6)
secs = settings.property("sections").toVariant() if hasattr(settings.property("sections"), "toVariant") else settings.property("sections")
studio_nav = [i for i, s in enumerate(secs) if s["name"] == "Studio"][0]
nav = QMetaObject.invokeMethod  # noqa: F841
settings.setProperty("current", settings.property("appearanceAt") - 1 if studio_nav < settings.property("appearanceAt") else studio_nav + 3)
pump(0.5)
root = settings.property("contentItem")
panel = next((c for c in walk(root, []) if c.objectName() == "mouseButtonsPanel" and c.isVisible()), None)
back_drop = next((c for c in walk(root, []) if c.objectName() == "mouseBackDrop"), None)
fwd_drop = next((c for c in walk(root, []) if c.objectName() == "mouseForwardDrop"), None)
check("Settings > Studio shows Mouse Side Buttons with a list for each", panel is not None
      and back_drop is not None and fwd_drop is not None and back_drop.isVisible())
check("...each list shows None", back_drop is not None and back_drop.property("currentText") == "None")
QMetaObject.invokeMethod(settings, "close"); pump(0.5)

# the Splatting page with the fixture loaded
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
             "auto_save_sidecar": False, "auto_convergence_mode": "Off", "depth_gamma": 1.0,
             "depth_dilate_size_x": 0, "depth_dilate_size_y": 0, "depth_blur_size_x": 0, "depth_blur_size_y": 0,
             "depth_dilate_left": 0, "depth_blur_left": 0, "zero_disparity_anchor": 0.9,
             "enable_global_norm": False}.items():
    sp._set(k, v)
pv.load(); settle()
check("preview loaded", pv.loaded)
img = find(page, lambda c: c.objectName() == "previewImage")
flip = page.findChild(QObject, "flipCard")
dial = find(page, lambda c: c.objectName() == "popOutDial")

def side(button, item):
    p = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(win, button, Qt.NoModifier, QPoint(int(p.x()), int(p.y()))); pump(0.3)

over_pic = img.parentItem()
up0 = bool(flip.property("backUp"))
side(Qt.BackButton, over_pic); pump(1.0)
check("None: the back button does nothing", bool(flip.property("backUp")) == up0 and not page.property("pickingConv"))

shell.setProperty("mouseBackAction", "Flip Preview Card"); pump(0.1)
side(Qt.BackButton, over_pic); pump(1.5); settle()
check("Back = Flip Preview Card: over the picture it turns the card over", bool(flip.property("backUp")) != up0,
      f"{up0} -> {flip.property('backUp')}")
up1 = bool(flip.property("backUp"))
side(Qt.BackButton, dial); pump(1.5); settle()
check("...and over the settings too (back again)", bool(flip.property("backUp")) != up1)

shell.setProperty("mouseForwardAction", "Pick Convergence"); pump(0.1)
side(Qt.ForwardButton, dial)
check("Forward = Pick Convergence: arms the picker", bool(page.property("pickingConv")))
side(Qt.ForwardButton, dial)
check("...again cancels", not page.property("pickingConv"))
# over the picture it picks right there, in one click (user, 2026-10-30): the fixture's disc reads 0.50 a
# quarter in (check_splat_pick_conv)
p = over_pic.mapToScene(QPointF(over_pic.width() * 0.25, over_pic.height() * 0.5))
QTest.mouseClick(win, Qt.ForwardButton, Qt.NoModifier, QPoint(int(p.x()), int(p.y()))); pump(0.6); settle()
conv = float(sp._map.value("zero_disparity_anchor"))
check("...over the picture it sets Convergence there at once, nothing left armed",
      abs(conv - 0.50) <= 0.03 and not page.property("pickingConv"), f"{conv:.3f}")

shell.setProperty("mouseForwardAction", "Next Frame"); pump(0.1)
f0 = int(pv.property("frame")) if pv.property("frame") is not None else None
side(Qt.ForwardButton, over_pic); settle()
f1 = int(pv.property("frame")) if pv.property("frame") is not None else None
check("Forward = Next Frame: one frame on", f0 is not None and f1 == f0 + 1, f"{f0} -> {f1}")

# the cover over the page takes the side buttons only: a left click and the wheel reach what's under it
btn = find(page, lambda c: c.objectName() == "pickConvButton")
page.setProperty("pickingConv", False); pump(0.1)
side(Qt.LeftButton, btn)
check("a left click still reaches the button under it", bool(page.property("pickingConv")))
side(Qt.LeftButton, btn)
side(Qt.ForwardButton, btn)
check("...and a side button over that button runs its own action (Next Frame)", int(pv.property("frame")) == f1 + 1,
      str(pv.property("frame")))
from PySide6.QtGui import QWheelEvent
pop0 = float(sp._map.value("pop_amount") or 0)
c = dial.mapToScene(QPointF(dial.width() / 2, dial.height() / 2))
QTest.mouseMove(win, QPoint(int(c.x()), int(c.y()))); pump(0.2)      # the pointer over it, as a wheel's is
for _ in range(3):
    ev = QWheelEvent(c, win.mapToGlobal(c), QPoint(0, 0), QPoint(0, 120), Qt.NoButton, Qt.NoModifier,
                     Qt.NoScrollPhase, False)
    QCoreApplication.sendEvent(win, ev); pump(0.15)
check("the wheel over a dial still turns it", float(sp._map.value("pop_amount") or 0) != pop0,
      f"{pop0} -> {sp._map.value('pop_amount')}")
sp._set("pop_amount", pop0); pump(0.2)

# another stage showing: the Splatting actions stay put
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 0), Q_ARG("QVariant", True)); pump(1.0)
up2 = bool(flip.property("backUp"))
p = win.contentItem().mapToScene(QPointF(win.width() / 2, win.height() / 2))
QTest.mouseClick(win, Qt.BackButton, Qt.NoModifier, QPoint(int(p.x()), int(p.y()))); pump(1.0)
check("on another stage the side buttons do nothing here", bool(flip.property("backUp")) == up2)

saved = (tmp / "config" / "studio.json")
import json
data = json.loads(saved.read_text(encoding="utf-8")) if saved.exists() else {}
check("the choices are saved with Studio's settings", data.get("mouse_back_action") == "Flip Preview Card"
      and data.get("mouse_forward_action") == "Next Frame", str({k: v for k, v in data.items() if "mouse" in k}))

pv.shutdown()
qml_warns = [w for w in warns if "EncodingDialog.qml" not in w]
check("no QML warnings", not qml_warns, "\n    " + "\n    ".join(qml_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
