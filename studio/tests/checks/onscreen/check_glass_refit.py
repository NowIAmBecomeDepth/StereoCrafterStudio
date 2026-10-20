"""The acrylic is re-seated once a resize settles (Backdrop.refresh): its
blurred snapshot is sized when the accent is set, so a window grown past
that showed stretched edge pixels (user, 2026-09-26). On-screen (the accent
needs a real HWND), programmatic only -- no capture: a width / height
change, then a pause, calls refresh once (debounced); a drag of many steps
calls it once, not per step; glass off never calls it. Configs redirected;
the window is the one this check launches."""
import os, sys, tempfile, hashlib, time
from pathlib import Path
REPO = Path(__file__).resolve().parents[4]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
rec = REPO / "last_jobs.json"; h0 = hashlib.sha256(rec.read_bytes()).hexdigest()
tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
import _fixtures
from PySide6.QtCore import Qt, QCoreApplication, QEventLoop
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuickControls2 import QQuickStyle
from studio import app as A
QQuickStyle.setStyle("Basic")
QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
fails = []
def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok: fails.append(label)
def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.005)

bd, shell = objs["Backdrop"], objs["Shell"]
if not bd.property("supported"):
    print("  [OK ] (no Windows 11 system backdrop here: nothing to check)")
    _fixtures.exit_now(0)
from PySide6.QtCore import QMetaObject, Q_ARG
QMetaObject.invokeMethod(win, "setGlass", Q_ARG("QVariant", "acrylic"), Q_ARG("QVariant", False))
pump(2.5)                                           # launch fit + the 1.5 s reassert
check("glass is acrylic", bd.property("mode") == "acrylic", bd.property("mode"))
n0 = getattr(bd, "refreshes", 0)
win.setWidth(win.width() + 120); pump(0.6)
n1 = getattr(bd, "refreshes", 0)
check("a resize, once settled, re-seats the acrylic once", n1 == n0 + 1, f"{n0} -> {n1}")
for k in range(12):                                 # a drag: many steps, 30 ms apart
    win.setWidth(win.width() + 10); pump(0.03)
pump(0.6)
n2 = getattr(bd, "refreshes", 0)
check("a drag of many steps re-seats it once, at the end", n2 == n1 + 1, f"{n1} -> {n2}")
win.setHeight(win.height() + 60); pump(0.6)
check("a height change too", getattr(bd, "refreshes", 0) == n2 + 1)
QMetaObject.invokeMethod(win, "setGlass", Q_ARG("QVariant", "none"), Q_ARG("QVariant", False)); pump(1.2)
n3 = getattr(bd, "refreshes", 0)
win.setWidth(win.width() - 100); pump(0.6)
check("glass off: never", getattr(bd, "refreshes", 0) == n3)
_lj = h0 == hashlib.sha256(rec.read_bytes()).hexdigest(); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
