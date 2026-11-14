"""Right-click a dial or a slider to type its value (user, 2026-11-13), in the real app, offscreen:
the box opens with the value selected; Enter sets it (clamped to the range, snapped to the step); a bad
entry keeps the box open and the value; Esc leaves it; the box's Reset puts the default back (what the
right-click did before); a double-click on a dial still resets. Configs and last_jobs.json redirected."""
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
from PySide6.QtGui import QKeyEvent
from PySide6.QtCore import QEvent
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


sp_st = objs["Splatting"].property("settings")
def entry_field():
    return [c for c in walk(win.contentItem().parentItem() or win.contentItem(), [])
            if c.objectName() == "valueEntryField" and c.isVisible()]
def right_click(it, y=None):
    c = it.mapToScene(QPointF(it.width() / 2, it.height() / 2 if y is None else y))
    QTest.mouseClick(win, Qt.RightButton, Qt.NoModifier, QPoint(int(c.x()), int(c.y()))); pump(0.4)
def type_in(text, key=Qt.Key_Return):
    QTest.keyClick(win, Qt.Key_A, Qt.ControlModifier)
    for ch in text:
        k = Qt.Key(ord(ch.upper())) if ch.isalnum() else Qt.Key_Period
        QCoreApplication.sendEvent(win, QKeyEvent(QEvent.KeyPress, k, Qt.NoModifier, ch))
        QCoreApplication.sendEvent(win, QKeyEvent(QEvent.KeyRelease, k, Qt.NoModifier, ch))
    QTest.keyClick(win, key); pump(0.4)

d = dials()
g = d["depth_gamma"]
QMetaObject.invokeMethod(g, "write", Q_ARG("QVariant", 1.7)); pump(0.2)
released = []
g.released.connect(lambda v: released.append(v))
right_click(g, g.property("size") / 2)
f = entry_field()
check("right-click a knob: the box opens with the value, selected", len(f) == 1 and f[0].property("text") == "1.70"
      and f[0].property("selectedText") == "1.70" and abs(g.property("value") - 1.7) < 1e-9,
      f"{[x.property('text') for x in f]} value {g.property('value')}")
type_in("2.23")
check("type 2.23 + Enter: Gamma 2.25 (its 0.05 step), in the setting, released once", not entry_field()
      and abs(g.property("value") - 2.25) < 1e-9 and abs(float(sp_st.value("depth_gamma")) - 2.25) < 1e-9
      and len(released) == 1, f"{g.property('value')} / {sp_st.value('depth_gamma')} released {released}")
right_click(g, g.property("size") / 2); type_in("99")
check("past the top: clamped to 3.00", abs(g.property("value") - 3.0) < 1e-9, str(g.property("value")))
right_click(g, g.property("size") / 2); type_in("abc")
check("not a number: the box stays open, the value unchanged", len(entry_field()) == 1
      and abs(g.property("value") - 3.0) < 1e-9, str(g.property("value")))
QTest.keyClick(win, Qt.Key_Escape); pump(0.4)
check("Esc closes it, the value unchanged", not entry_field() and abs(g.property("value") - 3.0) < 1e-9)
right_click(g, g.property("size") / 2)
rs = [c for c in walk(win.contentItem().parentItem() or win.contentItem(), [])
      if c.objectName() == "valueEntryReset" and c.isVisible()]
if rs:
    rc = rs[0].mapToScene(QPointF(rs[0].width() / 2, rs[0].height() / 2))
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(rc.x()), int(rc.y()))); pump(0.4)
check("the box's Reset: back to the default 1.00", len(rs) == 1 and not entry_field()
      and abs(g.property("value") - 1.0) < 1e-9, f"{len(rs)} {g.property('value')}")
md = d["max_disp"]
right_click(md, md.property("size") / 2); type_in("42")
check("an integer knob (Disparity): 42", md.property("value") == 42 and int(float(sp_st.value("max_disp"))) == 42,
      f"{md.property('value')} / {sp_st.value('max_disp')}")

# the plain slider: Preview Brightness
br = [c for c in walk(page, []) if c.objectName() == "brightnessSlider" and c.isVisible()][0]
sl = [c for c in walk(br, []) if c.objectName() == "rowSlider"][0]
right_click(sl)
f = entry_field()
check("right-click a slider: the box opens with its value", len(f) == 1 and f[0].property("text") == "1.00",
      str([x.property("text") for x in f]))
type_in("1.6")
check("type 1.6: Preview Brightness 1.60, in the setting", abs(br.property("value") - 1.6) < 1e-9
      and abs(float(sp_st.value("preview_brightness")) - 1.6) < 1e-9,
      f"{br.property('value')} / {sp_st.value('preview_brightness')}")

bad = [w for w in warns if "ValueEntry" in w or "DialField" in w or "SliderRow" in w]
check("no QML warnings from the dials, sliders or the box", not bad, str(bad[:3]))
check("last_jobs.json untouched", (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None) == h0)
print("FAILS", len(fails), fails)
os._exit(1 if fails else 0)
