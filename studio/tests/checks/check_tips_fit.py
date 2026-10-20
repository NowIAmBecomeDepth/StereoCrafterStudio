"""Every stage's typed tips fit its tip line at the stage's fitted window width
-- none is cut off with an ellipsis (the tips are picked at random per run, so
check_fit could only ever see one). Offscreen, each stage fitted, each tip set
whole. Configs and last_jobs.json redirected."""
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
import _fixtures
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG
from PySide6.QtGui import QGuiApplication
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
        QCoreApplication.processEvents(QEventLoop.AllEvents, 20); time.sleep(0.005)
def walk(it, out):
    for c in it.childItems(): out.append(c); walk(c, out)
    return out

stage = [c for c in walk(win.contentItem(), []) if c.objectName() == "stage"][0]
for i, name in enumerate(["DepthCrafter", "Splatting", "Inpainting", "Merging", "Extra"]):
    QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", i), Q_ARG("QVariant", True)); pump(1)
    QMetaObject.invokeMethod(win, "fitToContent"); pump(1)
    page = stage.property("pages").property(i).toQObject()
    cut, count = [], 0
    for t in [c for c in walk(page, []) if c.objectName() == "tipTicker" and c.isVisible()]:
        tips = t.property("tips")
        tips = list(tips.toVariant()) if hasattr(tips, "toVariant") else list(tips)
        body = [c for c in walk(t, []) if c.objectName() == "tipText"][0]
        for k, tip in enumerate(tips):
            t.setProperty("index", k); pump(0.03)
            count += 1
            if body.property("truncated"):
                cut.append(tip[:60])
    check(f"{name} ({win.width()} px): all {count} tips fit their line", count > 0 and not cut, str(cut))

_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
