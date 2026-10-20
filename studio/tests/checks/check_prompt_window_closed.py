"""A sub-window closed with its X while its prompt is up (bug sweep 2026-10-09,
A10): the prompt stayed counted in Theme.modals, and every control in the main
window that waits for no modal (flip taps, the job queue, the VRAM pill) stayed
dead. A window like Hole Shift's, with a PromptDialog, is made here; closing it
mid-question un-counts the prompt and answers it as Esc does. Offscreen, config
and last_jobs.json redirected.

    python studio/tests/checks/check_prompt_window_closed.py
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
from PySide6.QtQml import QQmlComponent
from PySide6.QtCore import QUrl
comp = QQmlComponent(engine)
comp.setData(b"""
import QtQuick
import QtQuick.Window
import Studio
Window {
    id: sub
    width: 400; height: 300
    property int answer: -1
    PromptDialog { id: d }
    function answerIt() { d.finish(1) }
    function askIt() {
        d.ask({ title: "Remove the keyframe?", message: "x",
                buttons: [{ text: "Remove", code: 1, kind: "danger" }, { text: "Cancel", code: 0, kind: "ghost" }],
                onDone: (code) => sub.answer = code })
    }
}
""", QUrl.fromLocalFile(str(REPO / "studio/qml/Studio/_check_sub.qml")))   # wherever Studio is
sub = comp.create()
check("a sub-window with a prompt is made", sub is not None, str([e.toString() for e in comp.errors()]))
theme = engine.singletonInstance("Studio", "Theme")
m0 = theme.property("modals")
sub.show(); pump(0.4)
QMetaObject.invokeMethod(sub, "askIt"); pump(0.6)
check("the prompt is up and counted", theme.property("modals") == m0 + 1, f"{m0} -> {theme.property('modals')}")
sub.close(); pump(0.8)
check("the window closed with its X: the prompt isn't counted any more", theme.property("modals") == m0,
      f"{theme.property('modals')} (was {m0})")
check("...and it was answered as Esc (Cancel)", sub.property("answer") == 0, str(sub.property("answer")))
sub.show(); pump(0.4)
QMetaObject.invokeMethod(sub, "askIt"); pump(0.6)
check("asked again in the reopened window: counted once", theme.property("modals") == m0 + 1, str(theme.property("modals")))
QMetaObject.invokeMethod(sub, "answerIt"); pump(0.6)
check("...answered: not counted", theme.property("modals") == m0, str(theme.property("modals")))

studio_warns = [w for w in warns if ("Studio" in w or "qml" in w.lower()) and "_check_sub" not in w]
check("no QML warnings", not studio_warns, " | ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
check("the real last_jobs.json untouched", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails else 1)
