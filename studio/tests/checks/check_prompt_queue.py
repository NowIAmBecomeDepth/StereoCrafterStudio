"""PromptDialog (audit 2026-09-28, low confidence, settled here): a second
ask() while a prompt was open replaced the first one's onDone -- the first
asker was never answered. A worker waiting on its answer (DepthCrafter's
latent-checkpoint question) would wait for ever. Now a second ask waits its
turn. Offscreen; configs and last_jobs.json redirected."""
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
from PySide6.QtCore import QObject, QByteArray, QCoreApplication, QEventLoop, QUrl, qInstallMessageHandler
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
    id: host
    width: 800; height: 600
    property var got: []
    PromptDialog { id: dlg; objectName: "dlg" }
    function askTwo() {
        dlg.ask({ title: "first", message: "a", onDone: (c) => { host.got = host.got.concat(["first:" + c]) } })
        dlg.ask({ title: "second", message: "b", onDone: (c) => { host.got = host.got.concat(["second:" + c]) } })
    }
}
"""
comp = QQmlComponent(engine)
comp.setData(QByteArray(QML), QUrl.fromLocalFile(str(REPO / "studio" / "qml" / "Studio" / "_check.qml")))
root = comp.create()
check("the test item builds", root is not None, str([e.toString() for e in comp.errors()]))
root.setParentItem(win.contentItem()); pump(0.3)
from PySide6.QtCore import QMetaObject, Q_ARG
dlg = [c for c in root.findChildren(QObject) if c.objectName() == "dlg"][0]
QMetaObject.invokeMethod(root, "askTwo"); pump(0.4)
check("the first question shows first", dlg.property("title") == "first", str(dlg.property("title")))
QMetaObject.invokeMethod(dlg, "finish", Q_ARG("QVariant", 1)); pump(0.6)
check("...its asker is answered, then the second question shows", list(root.property("got").toVariant()
      if hasattr(root.property("got"), "toVariant") else root.property("got")) == ["first:1"]
      and dlg.property("title") == "second" and dlg.property("opened"), f"{root.property('got')} / {dlg.property('title')}")
QMetaObject.invokeMethod(dlg, "finish", Q_ARG("QVariant", 0)); pump(0.6)
got = root.property("got"); got = list(got.toVariant() if hasattr(got, "toVariant") else got)
check("...and the second asker is answered too", got == ["first:1", "second:0"], str(got))

studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
