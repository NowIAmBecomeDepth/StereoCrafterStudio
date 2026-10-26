"""The app's name in the top-left corner opens the manual (user, 2026-10-25: "click on the stereocrafter studio
text and hover text says open the manual, and it opens up to the table of contents"), real app, offscreen:
its tip, a click opening the manual's contents (the browser stood in for -- nothing opens), and the name handed to WinFrame as client area so Windows doesn't take the click as a window drag.
The console window's caption stays a plain caption. Configs and last_jobs.json redirected; no GPU.
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
from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, QPoint, QPointF, Qt, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
import studio.shell as shell_mod
from studio import app as A

opened = []
shell_mod.QDesktopServices.openUrl = staticmethod(lambda url: opened.append(url.toLocalFile()) or True)

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
objs["Shell"].setProperty("uiScale", "1.0")
win.show()
fails = []
def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok: fails.append(label)
def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.005)
def find(it, pred):
    for c in it.childItems():
        if pred(c): return c
        r = find(c, pred)
        if r is not None: return r
pump(1.5)

bar = find(win.contentItem(), lambda c: c.objectName() == "titleBar")
name = find(bar, lambda c: c.objectName() == "titleName") if bar is not None else None
check("the top-left name is a button", name is not None and name.isVisible() and bar.property("nameClickable") is True)
tip = [c for c in name.childItems() if c.metaObject().indexOfProperty("tip") >= 0] if name is not None else []
check("...its tip says Open the manual", bool(tip) and tip[0].property("tip") == "Open the manual",
      str([t.property("tip") for t in tip]))
covers = name is not None and name.width() > 100 and name.mapToScene(QPointF(0, 0)).x() < 12
check("...it covers the icon, the name and the glasses", covers, f"{name.width() if name else 0:.0f} px")
c = name.mapToScene(QPointF(name.width() / 2, name.height() / 2))
QTest.mouseMove(win, QPoint(int(c.x()), int(c.y()))); pump(0.3)
QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(c.x()), int(c.y()))); pump(0.5)
want = os.path.normcase(str(REPO / "docs" / "manual" / "index.html"))
check("a click opens the manual at its contents (index.html)",
      len(opened) == 1 and os.path.normcase(os.path.normpath(opened[0])) == want, str(opened))
wf = objs["WinFrame"]
check("...and WinFrame treats it as client area, not the drag caption",
      not getattr(wf, "_on", False) or any(i is name for i in getattr(wf, "_no_drag", [])),
      str([getattr(i, "objectName", lambda: "?")() for i in getattr(wf, "_no_drag", [])]))

studio_warns = [w for w in warns if "EncodingDialog.qml" not in w]   # 34d33d5's, not this check's
check("no QML warnings (but EncodingDialog's known one)", not studio_warns, str(studio_warns[:3]))
h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
check("the real last_jobs.json untouched", h0 == h1)
print("PASS" if not fails else f"FAIL ({len(fails)})", fails)
_fixtures.exit_now(0 if not fails and h0 == h1 else 1)
