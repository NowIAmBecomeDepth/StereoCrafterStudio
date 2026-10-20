"""The keyboard shortcuts added 2026-10-02 (user), pressed for real in the real
app, offscreen: Ctrl+1-5 go to each stage, Ctrl+P opens the project list,
Ctrl+= / Ctrl+- step UI Scale within 75-100%, F1 asks Shell for the manual at
the stage showing (the browser itself is not opened: openManual is recorded),
Escape closes Full Preview, and Settings > Keyboard Shortcuts lists them. Configs and last_jobs.json
redirected.

    python studio/tests/checks/check_shortcuts.py
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
from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from studio import app as A
from studio import shell as S

opened = []
S.StudioShell.openManual = lambda self, where: (opened.append(where), True)[1]   # never a real browser

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
win.show(); win.requestActivate(); pump(0.8)
shell = objs["Shell"]
stage = next(c for c in walk(win.contentItem(), []) if c.objectName() == "stage")
C = Qt.KeyboardModifier.ControlModifier

def press(key, mods=Qt.KeyboardModifier.NoModifier):
    QTest.keyClick(win, key, mods); pump(0.4)

check("the window has the keyboard", shell.property("focusWindow") == win, shell.property("focusWindow"))
for n, key in enumerate((Qt.Key.Key_2, Qt.Key.Key_3, Qt.Key.Key_4, Qt.Key.Key_5, Qt.Key.Key_1)):
    want = (1, 2, 3, 4, 0)[n]
    press(key, C)
    check(f"Ctrl+{want + 1} goes to stage {want}", stage.property("current") == want, stage.property("current"))

picker = next(o for o in win.findChildren(QObject) if o.objectName() == "projectPicker")
press(Qt.Key.Key_P, C)
check("Ctrl+P opens the project list", bool(picker.property("opened")))
press(Qt.Key.Key_Escape)     # a modal popup holds the keys while open: Escape closes it, as every popup
check("...and Escape closes it", not picker.property("opened"))

shell.setProperty("uiScale", "0.9"); pump(0.5)
press(Qt.Key.Key_Equal, C)
check("Ctrl+= steps UI Scale up 5%", shell.property("uiScale") in ("0.95", "auto"), shell.property("uiScale"))
for _ in range(4):
    press(Qt.Key.Key_Equal, C)
check("...never past 100%", shell.property("uiScale") in ("1.0", "1", "auto"), shell.property("uiScale"))
for _ in range(8):
    press(Qt.Key.Key_Minus, C)
def pct():   # the scale in effect: "auto" is the screen's own (the knob stores it so)
    v = shell.property("uiScale")
    return round(win.property("autoZoom") * 100) if v == "auto" else round(float(v) * 100)
check("Ctrl+- steps down, never below 75%", pct() == 75, (shell.property("uiScale"), pct()))
shell.setProperty("uiScale", "auto"); pump(0.5)

press(Qt.Key.Key_2, C)
press(Qt.Key.Key_F1)
check("F1 asks for the manual at the stage showing", opened[-1:] == ["part2.html#splatting"], opened)

# Full Preview: true full screen only, on Studio's screen first, then where it was last;
# Escape, F11 or an X that hides after a moment and comes back with the mouse close it
# (user, 2026-10-13; Escape closes it since 2026-10-11)
from PySide6.QtGui import QWindow
from PySide6.QtCore import QPoint, QMetaObject, Q_ARG
fpw = [o for o in win.findChildren(QWindow) if o.metaObject().className().startswith("FullPreviewWindow")]
check("Splatting and Merging each have a Full Preview", len(fpw) == 2, len(fpw))
pages_fp = [o for o in win.findChildren(QObject) if o.objectName() == "fullPreviewShortcut"]
check("...and F11 opens it from both pages", len(pages_fp) == 2 and all(o.property("sequence") == "F11" for o in pages_fp),
      [o.property("sequence") for o in pages_fp])
# the full-screen icon in each previewer's top-right corner, in place of a Full Preview button
# (user, 2026-10-15): hidden with nothing loaded
_icons = [o for o in win.findChildren(QObject) if o.objectName() == "fullscreenButton"]
_old = [o for o in win.findChildren(QObject) if o.property("text") == "Full Preview"]
check("both previewers have a full-screen icon, hidden with nothing loaded, and no Full Preview button",
      len(_icons) == 2 and not any(o.property("visible") for o in _icons) and not _old, f"{len(_icons)} / {len(_old)}")
fp = fpw[0]
xbtn = next(o for o in fp.findChildren(QObject) if o.objectName() == "fullPreviewClose")
shell.setProperty("fullPreviewScreen", ""); pump(0.1)

def open_fp():
    QMetaObject.invokeMethod(fp, "openFull", Q_ARG("QVariant", win.screen())); pump(0.5)

for how in ("Escape", "F11", "X"):
    open_fp()
    check(f"Full Preview opens full screen ({how} run)", fp.visibility() == QWindow.Visibility.FullScreen, fp.visibility())
    check("...has the keyboard", shell.property("focusWindow") == fp, shell.property("focusWindow"))
    check("...the X shows on opening", xbtn.property("visible") and xbtn.property("opacity") > 0.9)
    if how == "X":
        pump(2.3)
        check("...and is gone after a moment", not xbtn.property("visible"), xbtn.property("opacity"))
        QTest.mouseMove(fp, QPoint(200, 200)); pump(0.05); QTest.mouseMove(fp, QPoint(240, 230)); pump(0.4)
        check("...back when the mouse moves", xbtn.property("visible"))
        c = xbtn.mapToScene(xbtn.boundingRect().center())
        QTest.mouseClick(fp, Qt.LeftButton, Qt.NoModifier, QPoint(int(c.x()), int(c.y()))); pump(0.4)
    else:
        QTest.keyClick(fp, Qt.Key.Key_Escape if how == "Escape" else Qt.Key.Key_F11); pump(0.4)
    check(f"...{how} closes it", not fp.isVisible())
    win.requestActivate(); pump(0.4)
check("...on Studio's screen, and that screen kept for next time",
      fp.screen() == win.screen() and shell.property("fullPreviewScreen") == win.screen().name(),
      (fp.screen().name(), shell.property("fullPreviewScreen")))
# bug sweep 2026-10-14c: closed under a held slider, its release never came -- every render
# after stayed the fast kind
_pv = fp.property("pv")
open_fp()
_pv.setDragging(True)
QTest.keyClick(fp, Qt.Key.Key_Escape); pump(0.4)
check("...closed with its slider held: the preview isn't left dragging", not _pv._dragging)
win.requestActivate(); pump(0.4)

dlg = next(o for o in win.findChildren(QObject) if o.objectName() == "settingsDialog")
dlg.setProperty("current", dlg.property("shortcutsAt")); dlg.setProperty("visible", True); pump(0.6)
# bug sweep 2026-10-09 (A1) said the stage keys fired behind a modal: they don't -- a modal
# Popup blocks window shortcuts outside it (Qt Quick Controls). Kept as a regression check.
_was = stage.property("current")
press(Qt.Key.Key_3, C); press(Qt.Key.Key_Tab, C)
check("behind an open Settings, Ctrl+3 / Ctrl+Tab leave the stage alone", stage.property("current") == _was,
      f"{_was} -> {stage.property('current')}")
rows = [c for c in walk(win.contentItem(), []) if c.objectName() == "shortcutRow" and c.isVisible()]
check("Settings > Keyboard Shortcuts lists them", len(rows) >= 25, len(rows))
dlg.setProperty("visible", False); pump(0.3)

h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
print("last_jobs unchanged:", h0 == h1, flush=True)
print("FAILS", len(fails), fails, flush=True)
# through the fixtures: its temp folders removed, and no DLL teardown crash after a green
# run (bug sweep 2026-10-09, A12)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _fixtures  # noqa: E402
_fixtures.exit_now(1 if fails or h0 != h1 else 0)
