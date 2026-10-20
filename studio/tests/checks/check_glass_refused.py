"""Frosted Glass when Windows refuses the material (bug sweep 2026-10-09, A1): the
switch stayed ON over no glass and the refused mode was saved, so every start
tried it again. Offscreen the window has no real handle, so Backdrop.apply fails
on its own -- the refusal path, not a stand-in. Configs redirected.
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
    if not ok:
        fails.append(label)


def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.005)


pump(1.0)
bd, shell = objs.get("Backdrop"), objs["Shell"]
if bd is None or not bd.property("supported"):
    print("  (glass not supported here: nothing to check)")
else:
    toasts = []
    theme_toasts = []
    QMetaObject.invokeMethod(win, "setGlass", Q_ARG("QVariant", "acrylic"), Q_ARG("QVariant", True)); pump(0.5)
    check("a refused material leaves Frosted Glass OFF", shell.property("glass") == "none", str(shell.property("glass")))
    shell.save()
    import json
    saved = json.loads((tmp / "config" / "studio.json").read_text(encoding="utf-8")).get("glass")
    check("...and saves it off, so the next start doesn't try again", saved in (None, "none"), str(saved))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
