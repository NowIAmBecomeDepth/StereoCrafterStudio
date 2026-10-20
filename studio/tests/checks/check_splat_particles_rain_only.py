"""Splatting's Particles is Rain only (user, 2026-10-10: DepthCrafter picks up
most particles itself -- rain is the one it sees through; Sparks and Web stay in
the code but off the page). A clip already set to another particle keeps it, and
the page offers Rain beside it to switch back. Offscreen, config and
last_jobs.json redirected.

    python studio/tests/checks/check_splat_particles_rain_only.py
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
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from studio import app as A

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
objs["Shell"].setProperty("uiScale", "1.0")
win = engine.rootObjects()[0]
win.resize(1600, 1000); win.show()
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


def named(n):
    return [c for c in walk(win.contentItem(), []) if c.objectName() == n]


pump(0.8)
ctl = objs["Splatting"]
stage = named("stage")[0] if named("stage") else None
if stage is not None:
    QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 1), Q_ARG("QVariant", True))
pump(0.8)
ctl._set("rain_mode", "moving"); ctl._set("rain_particle", "rain"); pump(0.6)
drop = named("rainParticle")
sw = named("rainSwitch")
check("the Particles row shows (Rain on)", bool(sw) and sw[0].isVisible())
check("Rain only: no particle to pick", bool(drop) and not drop[0].isVisible())
title = [c for c in named("rainSection")]
check("...the section is called Rain (user, 2026-10-10)", bool(title) and str(title[0].property("title")).startswith("Rain"), str(title[0].property("title")) if title else "")

ctl._set("rain_particle", "sparks"); pump(0.6)
check("a clip already set to Sparks: the choice shows, Rain beside it",
      drop[0].isVisible() and list(drop[0].property("model")) == ["Rain", "Sparks"], str(drop[0].property("model")))
QMetaObject.invokeMethod(drop[0], "activated", Q_ARG(int, 0)); pump(0.6)
check("...Rain picked: the clip is Rain, and the choice goes", ctl._map.value("rain_particle") == "rain"
      and not drop[0].isVisible(), str(ctl._map.value("rain_particle")))

ctl._set("rain_particle", "web"); pump(0.6)
check("a Web clip (personal/): Rain and Web", list(drop[0].property("model")) == ["Rain", "Web"],
      str(drop[0].property("model")))
ctl._set("rain_particle", "rain"); pump(0.3)

studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
check("the real last_jobs.json untouched", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails else 1)
