"""SageAttention 1 for RTX 20 series (an RTX 20 user, 2026-10-30), in the real app. Run as is, Sage 2 is
installed: DepthCrafter's and Inpainting's (V1 and V2) SageAttention switches read "SageAttention 2 (RTX 30
Series and Up)" and Torch Compile is shown. With `sage1` (faked: nothing is installed) they read
"SageAttention 1 (RTX 20 Series)", both Torch Compile boxes hide -- TensorRT stays even with Torch Compile
saved on. Configs redirected.
Usage: check_sage_labels.py [sage1]"""
import os, sys, tempfile, hashlib, time
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
SAGE1 = "sage1" in sys.argv
from studio import sage1
sage1.installed = lambda: SAGE1
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, QObject, Q_ARG, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
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


def texts():
    """Every menu item's text in the app, the closed menus' too."""
    seen = []
    for o in win.findChildren(QObject):
        if o.metaObject().indexOfProperty("checkable") >= 0 and isinstance(o.property("text"), str):
            seen.append(o.property("text"))
    return seen


def boxes(key):
    return [c for c in everything() if c.property("key") == key and c.metaObject().indexOfProperty("shown") >= 0]


pump(1.0)
name = "SageAttention 1 (RTX 20 Series)" if SAGE1 else "SageAttention 2 (RTX 30 Series and Up)"
other = "SageAttention 2" if SAGE1 else "SageAttention 1"
t = texts()
check(f"DepthCrafter's switch: Use {name}", f"Use {name}" in t, str([x for x in t if "Sage" in x]))
check(f"Inpainting V1's: Use {name}", t.count(f"Use {name}") >= 2)
check(f"Inpainting V2's: Use {name} (for V2)", f"Use {name} (for V2)" in t)
check(f"no {other} anywhere, no bare 'Use SageAttention'",
      not [x for x in t if other in x or x in ("Use SageAttention", "Use SageAttention (Faster)",
                                               "Use Sage Attention (for V2)")])

QMetaObject.invokeMethod(next(c for c in everything() if c.objectName() == "stage"), "show",
                         Q_ARG("QVariant", 2), Q_ARG("QVariant", True)); pump(0.8)
st = objs["Inpainting"].property("settings")
st.setProperty("use_compile", True); st.setProperty("use_tensorrt", False); pump(0.6)
v1_compile = [b for b in boxes("use_compile") if b.metaObject().indexOfProperty("guard") >= 0
              and b.objectName() != "v2Compile"]
v2_compile = [c for c in everything() if c.objectName() == "v2Compile"]
trt = boxes("use_tensorrt")
check("V1's Torch Compile and TensorRT found", bool(v1_compile) and bool(trt))
if v1_compile and trt:
    if SAGE1:
        check("V1's Torch Compile hidden (saved on)", not v1_compile[0].property("shown"))
        check("...TensorRT still shown, though Torch Compile is saved on", bool(trt[0].property("shown")))
    else:
        check("V1's Torch Compile shown", bool(v1_compile[0].property("shown")))
        check("...TensorRT hidden while Torch Compile is on (as before)", not trt[0].property("shown"))
check("V2's Torch Compile found", bool(v2_compile))
if v2_compile:
    check(f"V2's Torch Compile {'hidden' if SAGE1 else 'shown'}", bool(v2_compile[0].property("shown")) != SAGE1)

st.setProperty("use_compile", False); pump(0.3)

check("no QML warnings", not [w for w in warns if "EncodingDialog.qml" not in w], str(warns[:3]))
h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
check("the real last_jobs.json untouched", h0 == h1)
print("FAILS", len(fails), fails)
sys.stdout.flush()
os._exit(1 if fails else 0)
