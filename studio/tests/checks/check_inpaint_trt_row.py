"""Inpainting's Torch Compile and TensorRT (user, 2026-10-29): on one line, side by side; the two
don't go together, so either one ticked hides the other (both shown when a saved config has both
on, so one can be turned off); TensorRT's tip says the normal way is slightly more accurate and by
how little. Without TensorRT installed (faked), ticking Inpainting's TensorRT or DepthCrafter's
TensorRT VAE leaves it off and opens Settings > Studio > Models at the NVIDIA TensorRT row.
Configs redirected; nothing runs or installs.
"""
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
from studio import model_catalog as C
_installed = [True]                         # TensorRT here or not, as the test says
C.packages_ready = lambda m: _installed[0]
from PySide6.QtCore import QCoreApplication, QObject, QEventLoop, QMetaObject, QPoint, QPointF, Q_ARG, Qt, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from studio import app as A

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
objs["Shell"].setProperty("uiScale", "1.0")
win = engine.rootObjects()[0]
win.resize(1400, 950); win.show()
fails = []


def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok:
        fails.append(label)


def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.005)


def walk(it, out):
    for c in it.childItems():
        out.append(c); walk(c, out)
    return out


def everything():
    root = win.contentItem()
    while root.parentItem():
        root = root.parentItem()
    return walk(root, [])


def named(n):
    return [c for c in everything() if c.objectName() == n and c.isVisible()]


def boxes():
    # IpCheck is an inline component: its class isn't CheckField's name
    return [c for c in everything() if c.property("key") is not None and c.metaObject().indexOfProperty("shown") >= 0
            and c.metaObject().indexOfProperty("help") >= 0]


def field(key):
    # the V1 page's: only V1 has use_tensorrt; its Torch Compile is beside it (V2 has its own use_compile)
    boxes = globals()["boxes"]()
    trt = next(c for c in boxes if c.property("key") == "use_tensorrt")
    return trt if key == "use_tensorrt" else next(c for c in boxes if c.property("key") == key
                                                   and c.parentItem() == trt.parentItem())


def click(item):
    p = item.mapToScene(QPointF(12, item.height() / 2))
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(p.x()), int(p.y()))); pump(0.6)


pump(1.0)
QMetaObject.invokeMethod(named("stage")[0], "show", Q_ARG("QVariant", 2), Q_ARG("QVariant", True)); pump(0.8)
st = objs["Inpainting"].property("settings")
st.setProperty("use_compile", False); st.setProperty("use_tensorrt", False); pump(0.6)
comp, trt = field("use_compile"), field("use_tensorrt")
cy = comp.mapToScene(QPointF(0, comp.height() / 2)).y()
ty = trt.mapToScene(QPointF(0, trt.height() / 2)).y()
cx = comp.mapToScene(QPointF(0, 0)).x()
tx = trt.mapToScene(QPointF(0, 0)).x()
check("both shown, off to begin with", comp.isVisible() and trt.isVisible())
check("...on one line, TensorRT right of Torch Compile", abs(cy - ty) < 1 and tx > cx, f"y {cy:.0f}/{ty:.0f} x {cx:.0f}/{tx:.0f}")

click(comp)
check("Torch Compile ticked: on", st.property("use_compile") is True)
check("...TensorRT hidden", not trt.isVisible())
click(comp)
check("...unticked: TensorRT back", st.property("use_compile") is False and trt.isVisible())

click(trt)
check("TensorRT ticked: on", st.property("use_tensorrt") is True)
check("...Torch Compile hidden", not comp.isVisible())
click(trt)
check("...unticked: Torch Compile back", st.property("use_tensorrt") is False and comp.isVisible())

st.setProperty("use_compile", True); st.setProperty("use_tensorrt", True); pump(0.6)
check("a config with both on: both shown, to turn one off", comp.isVisible() and trt.isVisible())
st.setProperty("use_compile", False); st.setProperty("use_tensorrt", False); pump(0.3)

tip = trt.property("help")
check("TensorRT's tip: the normal way is slightly more accurate", "slightly more accurate" in tip)
check("...by how little", "half a level out of 255" in tip and "far below what the eye can see" in tip)
check("Torch Compile's tip says TensorRT hides", "TensorRT is hidden while this is on" in comp.property("help"))

# ---- not installed: the tick sends you to the Models window (user, 2026-10-29)
dlg = win.findChild(QObject, "modelsDialog")
_installed[0] = False
click(trt); pump(0.6)
check("TensorRT not installed: ticking Inpainting's TensorRT leaves it off",
      st.property("use_tensorrt") is False and not trt.property("checked") is True)
check("...and opens Settings > Studio > Models at its row", dlg.property("visible") and dlg.property("focusKey") == "tensorrt",
      f"{dlg.property('visible')} {dlg.property('focusKey')}")
lst = next(c for c in walk(dlg.property("contentItem"), []) if c.objectName() == "modelsList")
row = next((c for c in walk(lst, []) if c.objectName() == "modelsRow_tensorrt"), None)
check("...the TensorRT row in view, with its Install button",
      row is not None and row.isVisible()
      and next(c for c in walk(lst, []) if c.objectName() == "modelsDownload_tensorrt").isVisible())
QMetaObject.invokeMethod(dlg, "close"); pump(0.6)
check("...closed: the highlight goes", dlg.property("focusKey") == "")
check("...Torch Compile still shown (TensorRT never went on)", comp.isVisible())

QMetaObject.invokeMethod(named("stage")[0], "show", Q_ARG("QVariant", 1), Q_ARG("QVariant", True)); pump(0.8)
dc_st = objs["DepthCrafter"].property("settings")
dc_st.setProperty("use_tensorrt_var", False); pump(0.4)
dc_box = next((c for c in boxes() if c.property("key") == "use_tensorrt_var" and c.isVisible()), None)
if dc_box is None:
    QMetaObject.invokeMethod(named("stage")[0], "show", Q_ARG("QVariant", 0), Q_ARG("QVariant", True)); pump(0.8)
    dc_box = next((c for c in boxes() if c.property("key") == "use_tensorrt_var" and c.isVisible()), None)
check("DepthCrafter's TensorRT VAE found", dc_box is not None)
if dc_box is not None:
    click(dc_box); pump(0.6)
    check("...not installed: ticking it leaves it off", dc_st.property("use_tensorrt_var") is False)
    check("...and opens the Models window at TensorRT", dlg.property("visible") and dlg.property("focusKey") == "tensorrt")
    QMetaObject.invokeMethod(dlg, "close"); pump(0.6)
    _installed[0] = True
    click(dc_box)
    check("...installed: the tick goes in, no window", dc_st.property("use_tensorrt_var") is True and not dlg.property("visible"))
    dc_st.setProperty("use_tensorrt_var", False); pump(0.3)
_installed[0] = True

check("no QML warnings", not [w for w in warns if "EncodingDialog.qml" not in w], str(warns[:3]))
h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
check("the real last_jobs.json untouched", h0 == h1)
print("FAILS", len(fails), fails)
sys.stdout.flush()
os._exit(1 if fails else 0)
