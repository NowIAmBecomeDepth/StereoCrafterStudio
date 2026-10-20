"""The wigglegram's speed slider (user, 2026-10-09), and its timers stay still
during a job (bug sweep 2026-10-09 A10: they ignored Theme.calm), real app,
offscreen, no clip loaded, no GPU:

  * Splatting and Merging: Wiggle Speed shows only while the Preview Source
    facing you is Wigglegram, and hides for any other;
  * the speed (switches per second, Shell.wiggleSpeed) sets every wiggle
    timer's interval (1000 / speed ms); it is clamped to 2..20 and kept;
  * a wiggling preview's timer runs; a job (Theme.calm) stops it, and it runs
    again after.

Configs and last_jobs.json redirected.
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
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, QObject, Q_ARG, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from studio import app as A

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
objs["Shell"].setProperty("uiScale", "1.0")
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


def find_all(it, pred, out):
    for c in it.childItems():
        if pred(c):
            out.append(c)
        find_all(c, pred, out)
    return out


pump(1.0)
shell = objs["Shell"]
stage = find_all(win.contentItem(), lambda c: c.objectName() == "stage", [])[0]
theme = engine.singletonInstance("Studio", "Theme")


def shown_slider():
    return [s for s in find_all(win.contentItem(), lambda c: c.objectName() == "wiggleSpeedSlider", []) if s.isVisible()]


def timers():
    return win.findChildren(QObject, "wiggleTimer")


for index, name in ((1, "Splatting"), (3, "Merging")):
    QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", index), Q_ARG("QVariant", True)); pump(1.0)
    st = objs[name].property("settings")
    st.setProperty("preview_source", "Dubois Anaglyph"); pump(0.4)
    check(f"{name}: no Wiggle Speed for Dubois Anaglyph", not shown_slider())
    st.setProperty("preview_source", "Wigglegram"); pump(0.4)
    check(f"{name}: Wiggle Speed shows for Wigglegram", len(shown_slider()) == 1)
    st.setProperty("preview_source", "Dubois Anaglyph"); pump(0.4)
    check(f"{name}: ...and hides again", not shown_slider())

ts = timers()
check("a wiggle timer on each page", len(ts) >= 2, str(len(ts)))
check("default: about the 130 ms per eye it always had", shell.property("wiggleSpeed") == 7.5
      and all(t.property("interval") == 133 for t in ts), str([t.property("interval") for t in ts]))
shell.setProperty("wiggleSpeed", 10.0); pump(0.2)
check("10 a second: 100 ms", all(t.property("interval") == 100 for t in ts), str([t.property("interval") for t in ts]))
shell.setProperty("wiggleSpeed", 100.0); pump(0.2)
check("clamped to 20 a second", shell.property("wiggleSpeed") == 20.0 and all(t.property("interval") == 50 for t in ts))
shell.setProperty("wiggleSpeed", 4.0); pump(0.2)
import json
saved = json.loads((tmp / "config" / "studio.json").read_text())
check("kept in Studio's own settings", saved.get("wiggle_speed") == 4.0, str(saved.get("wiggle_speed")))

# Splatting's preview wiggling (its two eyes in), page showing: the timer runs; a job stops it
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 1), Q_ARG("QVariant", True)); pump(1.0)
sp = objs["Splatting"]; pv = sp.property("previewer")
pv._wiggle = True; pv.frameChanged.emit(); pump(0.3)
splat_timer = [t for t in timers() if t.property("running")]
check("a wiggling preview: its timer runs", len(splat_timer) == 1, str(len(splat_timer)))
ip = objs["Inpainting"]
ip._busy = True; ip._status = "Processing video 1 of 1"; ip.stateChanged.emit(); ip.infoChanged.emit(); pump(0.6)
check("a job (Theme.calm): it stops", theme.property("calm") is True and not any(t.property("running") for t in timers()))
ip._busy = False; ip.stateChanged.emit(); ip.infoChanged.emit(); pump(0.6)
check("...and runs again after", theme.property("calm") is False and any(t.property("running") for t in timers()))
pv._wiggle = False; pv.frameChanged.emit(); pump(0.2)

studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
