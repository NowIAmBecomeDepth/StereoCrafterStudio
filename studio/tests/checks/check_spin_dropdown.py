"""SpinField and Dropdown (audit 2026-09-28, minor), in the real app, offscreen.

SpinField: a field emptied, or given a number below `from`, and then left kept
showing that text while the setting held the old number (the IntValidator
calls such text unfinished, so editingFinished never came). Dropdown: a stored
value not in the list marked item 0 as the current one. Each is made here
bound to a store of its own; configs and last_jobs.json redirected."""
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
from PySide6.QtCore import QByteArray, QCoreApplication, QEventLoop, QUrl, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtQml import QQmlComponent
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


def find(it, name):
    for c in it.childItems():
        if c.objectName() == name:
            return c
        r = find(c, name)
        if r is not None:
            return r


QML = b"""
import QtQuick
import Studio
Item {
    QtObject { id: st; objectName: "store"; property var n: 5; property var pick: "not listed" }
    property alias store: st
    SpinField { objectName: "spin"; store: st; key: "n"; from: 1; to: 10 }
    Dropdown { objectName: "drop"; y: 40; store: st; key: "pick"; model: ["a", "b", "c"] }
}
"""
comp = QQmlComponent(engine)
comp.setData(QByteArray(QML), QUrl.fromLocalFile(str(REPO / "studio" / "qml" / "Studio" / "_check.qml")))
root = comp.create()
check("the test item builds", root is not None, str([e.toString() for e in comp.errors()]))
root.setParentItem(win.contentItem()); pump(0.3)
store = root.property("store")
spin, drop = find(root, "spin"), find(root, "drop")
field = find(spin, "spinText")


win.requestActivate(); pump(0.2)


def type_and_leave(text):
    """Typed, as a user does: select all, delete, type, then click away."""
    field.forceActiveFocus(); pump(0.05)
    QTest.keyClick(win, Qt.Key_A, Qt.ControlModifier); QTest.keyClick(win, Qt.Key_Backspace)
    for ch in text:                                   # digits: keyClicks takes widgets only
        QTest.keyClick(win, getattr(Qt, f"Key_{ch}"))
    pump(0.05)
    field.setProperty("focus", False); pump(0.1)      # focus goes elsewhere
    assert not field.property("activeFocus")


type_and_leave("")
check("SpinField emptied and left shows the setting's number again", field.property("text") == "5" and store.property("n") == 5,
      f"shows {field.property('text')!r}, setting {store.property('n')!r}")
type_and_leave("0")
check("...below `from` and left: clamped to `from`, shown and written", field.property("text") == "1" and store.property("n") == 1,
      f"shows {field.property('text')!r}, setting {store.property('n')!r}")
type_and_leave("7")
check("...an ordinary number is written as before", field.property("text") == "7" and store.property("n") == 7,
      f"shows {field.property('text')!r}, setting {store.property('n')!r}")

check("Dropdown: a stored value the list lacks marks no row current", drop.property("currentIndex") == -1,
      str(drop.property("currentIndex")))
store.setProperty("pick", "b"); pump(0.1)
check("...a listed one marks its row", drop.property("currentIndex") == 1, str(drop.property("currentIndex")))

studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
