"""Closing the main window quits Studio, whatever a tool window says (user,
2026-10-02): Studio stayed running, hidden, after its window closed. Qt's own
quit asks every window to close first, and Detect Text refuses while it thinks
its mask is rendering -- that cancelled the quit, so no aboutToQuit: no
shutdown, no exit watchdog. In the real app, offscreen: Detect Text shown,
the main window closed. Without app.quit_with_main_window and with Detect Text
"rendering", Studio stays running (the bug, shown first); with it, Studio
quits, rendering or not. Configs and last_jobs.json redirected.

    python studio/tests/checks/check_quit_with_tool_window.py
"""
import os, sys, tempfile, hashlib
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
rec = REPO / "last_jobs.json"; h0 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from PySide6.QtCore import QTimer
from PySide6.QtGui import QGuiApplication
from studio import app as A

fails = []
def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok: fails.append(label)

qapp = QGuiApplication(sys.argv)
STUCK = 7


def run(with_fix, rendering, saved=False):
    """Close the main window with Detect Text open: the exec() return code,
    STUCK if the app was still running 2.5 s later."""
    engine, objs = A.build(qapp)
    main = engine.rootObjects()[0]
    main.show()
    tool = next(w for w in qapp.topLevelWindows() if w.objectName() == "subMaskWin" and w.transientParent() == main)
    tool.show()
    if rendering:
        tool.setProperty("s", {"rendering": True})
    if saved:   # the render done: the window told it is closed, as after a Save
        tool.setProperty("wanted", True)
        objs["Splatting"].property("previewer").editorChanged.emit("submask", {"open": False, "saved": True})
    if with_fix:
        A.quit_with_main_window(qapp, engine)
    shut = []
    def on_quit(): shut.append(1)
    qapp.aboutToQuit.connect(on_quit)
    close, give_up = QTimer(), QTimer()
    for t, ms, fn in ((close, 300, main.close), (give_up, 2500, lambda: qapp.exit(STUCK))):
        t.setSingleShot(True); t.timeout.connect(fn); t.start(ms)
    code = qapp.exec()
    close.stop(); give_up.stop()
    qapp.aboutToQuit.disconnect(on_quit)
    tool.setProperty("s", {}); tool.close()
    return code, bool(shut)


code, shut = run(with_fix=False, rendering=True)
check("without the fix, Detect Text 'rendering': the main window closed, Studio still running (the bug)",
      code == STUCK, code)
code, shut = run(with_fix=True, rendering=False)
check("with it: closing the main window quits Studio (Detect Text open)", code == 0 and shut, (code, shut))
code, shut = run(with_fix=True, rendering=True)
check("...and while Detect Text is rendering", code == 0 and shut, (code, shut))
code, shut = run(with_fix=False, rendering=True, saved=True)
check("Detect Text hidden after a Save no longer says 'rendering' (quits even without the fix)", code == 0 and shut, (code, shut))

h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
print("last_jobs unchanged:", h0 == h1, flush=True)
print("FAILS", len(fails), fails, flush=True)
# through the fixtures: its temp folders removed, and no DLL teardown crash after a green
# run (bug sweep 2026-10-09, A12)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _fixtures  # noqa: E402
_fixtures.exit_now(1 if fails or h0 != h1 else 0)
