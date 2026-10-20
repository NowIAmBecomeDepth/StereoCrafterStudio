"""Start-up's environment check reaches the user (release check 2026-10-17), real app, offscreen: what the
PC is missing (studio/env_check.problems -- stood in for here: no NVIDIA driver, no ffprobe) shows as warning
toasts once the window has settled, and stays in the notification bell; a second call says nothing again; a
PC with nothing missing gets no toast. Configs and last_jobs.json redirected.

    python studio/tests/checks/check_env_warning.py
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
from PySide6.QtCore import QCoreApplication, QEventLoop, QObject
from PySide6.QtGui import QGuiApplication
from studio import app as A
from studio import env_check as E

FAKE = [("No NVIDIA graphics card found", "stand-in: no driver"), ("FFmpeg not found", "stand-in: no ffprobe")]
calls = []
E.problems = lambda *a, **k: (calls.append(1), list(FAKE))[1]

fails = []
def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok: fails.append(label)
def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.005)

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
win.show(); pump(0.5)
shell = objs["Shell"]
bell = next(o for o in win.findChildren(QObject) if o.objectName() == "notificationBell")
start = bell.property("count")
check("nothing said before the window has settled", not calls and bell.property("count") == start)
end = time.time() + 10
while time.time() < end and bell.property("count") < start + 2:
    pump(0.1)
check("the check ran once after start-up", len(calls) == 1, len(calls))
check("each problem is a toast, kept in the bell", bell.property("count") == start + 2,
      f"{start} -> {bell.property('count')}")
shell.checkEnvironment(); pump(1.0)
check("a second call says nothing again", len(calls) == 1 and bell.property("count") == start + 2, len(calls))

# a PC with nothing missing: a fresh Shell's check -- no toast
FAKE.clear()
from studio import shell as S
quiet = S.StudioShell()
got = []
quiet.envWarning.connect(lambda t, m: got.append(t))
quiet.checkEnvironment(); pump(1.0)
check("nothing missing: no warning", got == [] and len(calls) == 2, got)

h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
print("last_jobs unchanged:", h0 == h1, flush=True)
print("FAILS", len(fails), fails, flush=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _fixtures  # noqa: E402
_fixtures.exit_now(1 if fails or h0 != h1 else 0)
