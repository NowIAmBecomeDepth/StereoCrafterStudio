"""The window's film grain on every popup's card (user, 2026-10-02): popups
sit above Main's grain layer, so they were clean. Offscreen, the real window:
nothing is built for a closed popup (hundreds of dropdowns); Settings, the
theme popout, a prompt and a dropdown's list each show the grain tile while
open; Film Grain off takes it away. Configs and last_jobs.json redirected.

    python studio/tests/checks/check_popup_grain.py
"""
import os, sys, tempfile, hashlib, time
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
rec = REPO / "last_jobs.json"; h0 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, QMetaObject, Q_ARG
from PySide6.QtGui import QGuiApplication
from studio import app as A

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

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
win.show(); pump(0.8)
# a synthetic project of its own: the chapter picker shows only with one open, and this
# check found the user's real project through the Prototype's settings until those
# stopped seeding a test's (3357a31, 2026-10-10)
_proj = tmp / "project"
for _d in ("SM Source Clips/Chapter 2 - The Origin", "SM DepthMaps/Chapter 2 - The Origin"):
    (_proj / _d).mkdir(parents=True)
objs["Shell"].setProperty("projectFolder", str(_proj))
pump(0.5)
shell = objs["Shell"]
shell.setProperty("filmGrain", True); pump(0.2)

def tiles(item=None):
    root = item if item is not None else win.contentItem()
    return [c for c in walk(root, []) if c.objectName() == "popupGrainTile" and c.isVisible() and c.implicitWidth() > 0]

check("no popup open: no grain built for any card", not tiles(), len(tiles()))

def popup(name):
    return next(o for o in win.findChildren(QObject) if o.objectName() == name)

for name in ("settingsDialog", "themePopup"):
    p = popup(name)
    QMetaObject.invokeMethod(p, "open"); pump(0.6)
    t = tiles(p.property("background"))
    check(f"{name}: the grain on its card while open", len(t) == 1, len(t))
    QMetaObject.invokeMethod(p, "close"); pump(0.5)

prompt = popup("prompt")
QMetaObject.invokeMethod(prompt, "ask", Q_ARG("QVariant", {"title": "Grain", "message": "A prompt card."})); pump(0.6)
check("a prompt: the grain on its card", len(tiles(prompt.property("background"))) == 1)
shell.setProperty("filmGrain", False); pump(0.3)
check("...and none with Film Grain off", not tiles(prompt.property("background")))
shell.setProperty("filmGrain", True); pump(0.3)
QMetaObject.invokeMethod(prompt, "finish", Q_ARG("QVariant", 0)); pump(0.5)

boxes = [c for c in walk(win.contentItem(), []) if c.metaObject().className().startswith("ChapterPicker") and c.isVisible()]   # its list is built as a Dropdown's
box = boxes[0]
from PySide6.QtQml import QQmlExpression
def qml(expr):   # PySide cannot hand a QQuickPopup* to Python
    return QQmlExpression(engine.rootContext(), box, expr).evaluate()
qml("popup.open()"); pump(0.6)
check("a dropdown's list: the grain while it is open", len(tiles()) == 1, len(tiles()))
qml("popup.close()"); pump(0.5)
check("...and gone once it closes", not tiles())

h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
print("last_jobs unchanged:", h0 == h1, flush=True)
print("FAILS", len(fails), fails, flush=True)
# through the fixtures: its temp folders removed, and no DLL teardown crash after a green
# run (bug sweep 2026-10-09, A12)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _fixtures  # noqa: E402
_fixtures.exit_now(1 if fails or h0 != h1 else 0)
