"""A Windows notification when a job finishes while the window is minimized
(user, 2026-10-05; studio.desktop_notify). Real app, offscreen; the
notification itself stood in (no tray icon, nothing on the desktop): a run's
finish -- outcome.verdict(), on a worker thread as the stages call it, and
Scene Cut's own announce -- reaches it only while the window is minimized,
with the toast's title and text; restored, nothing; a click on it brings the
window back. Configs and last_jobs.json redirected."""
import os, sys, tempfile, hashlib, time, threading
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from PySide6.QtCore import QCoreApplication, QEventLoop, Qt
from PySide6.QtGui import QGuiApplication
from studio import app as A
from studio import outcome as O
from studio.desktop_notify import is_minimized

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
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.005)
pump(0.6)

n = objs.get("JobNotifier")
check("the app builds a notifier and listens for finished runs", n is not None and O.FINISH_LISTENERS == [n.job_finished])
shown = []
n._show = lambda kind, title, text: shown.append((kind, title, text))

def finish_on_a_worker(stage, outcome):
    t = threading.Thread(target=lambda: O.verdict(stage, outcome, seconds=125))
    t.start(); t.join(); pump(0.3)

finish_on_a_worker("Splatting", O.Outcome(done=3))
check("window up: no notification (the in-app toast says it)", shown == [], str(shown))

win.showMinimized(); pump(0.4)
check("the window minimized", is_minimized(win))
finish_on_a_worker("Splatting", O.Outcome(done=3))
check("minimized: a finished run shows one, with the toast's title and text",
      len(shown) == 1 and shown[0][0] == "success" and "Splatting" in shown[0][1] and "3" in shown[0][2]
      and "Took 2m 05s" in shown[0][2], str(shown))
finish_on_a_worker("Merging", O.Outcome(done=1, failed=2))
check("...a run with failures says so (a warning)", len(shown) == 2 and shown[1][0] in ("warning", "error")
      and "Merging" in shown[1][1], str(shown[-1:]))
O.announce("success", "Scene Cut finished", "Done! 12 scenes.")
pump(0.3)
check("...Scene Cut's own finish too", shown[-1:] == [("success", "Scene Cut finished", "Done! 12 scenes.")], str(shown[-1:]))

n._bring_back(); pump(0.4)
check("a click on it brings the window back", not is_minimized(win) and win.isVisible())
before = len(shown)
finish_on_a_worker("DepthCrafter", O.Outcome(done=1))
check("...and with it up, no more notifications", len(shown) == before)

# the real tray path, its icon stood in (nothing on the desktop): showMessage must get a
# QIcon -- the MessageIcon form needs the widget style a QGuiApplication lacks and took
# Studio down at the end of an inpaint (user, 2026-10-07)
import PySide6.QtWidgets as QW
from PySide6.QtGui import QIcon
calls = []
class FakeTray(QW.QSystemTrayIcon):
    def show(self): calls.append("show")
    def showMessage(self, *args): calls.append(args)
real_tray, real_avail = QW.QSystemTrayIcon, QW.QSystemTrayIcon.isSystemTrayAvailable
QW.QSystemTrayIcon = FakeTray
FakeTray.isSystemTrayAvailable = staticmethod(lambda: True)
try:
    n._tray = None
    n._show_tray("warning", "Inpainting finished", "1 clip")
finally:
    QW.QSystemTrayIcon = real_tray
    FakeTray.isSystemTrayAvailable = real_avail
msg = [c for c in calls if c != "show"]
check("the tray's showMessage gets a QIcon, never a MessageIcon (no widget style here)",
      len(msg) == 1 and isinstance(msg[0][2], QIcon), str(msg))
if n._tray is not None:
    n._tray.deleteLater(); n._tray = None

h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
check("the real last_jobs.json untouched", h1 == h0)
print("FAILS", len(fails), fails, flush=True)
# through the fixtures: its temp folders removed, and no DLL teardown crash after a green
# run (bug sweep 2026-10-09, A12)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _fixtures  # noqa: E402
_fixtures.exit_now(1 if fails else 0)
