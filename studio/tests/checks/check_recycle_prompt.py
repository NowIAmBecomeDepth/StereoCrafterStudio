"""Too big for the Recycle Bin (user, 2026-10-14), in the real app, offscreen: something over its
drive's bin limit isn't handed to Windows (which would delete it for good, its own warning box
coming up ownerless from a job thread) -- Studio asks, as Windows does: "Too big for the Recycle
Bin ... Deleting it removes it permanently", Delete Permanently / Keep It.

* from a job thread: the question comes up, the thread waits; Delete Permanently deletes it,
  Keep It keeps it;
* from the UI thread (the folder picker, SAM 3's uninstall): the same, the window still live;
* quitting answers an open question no.

The bin's limit is faked small and Windows' own call never runs: nothing reaches the real bin.
Temp folders; configs and last_jobs.json redirected.

    python studio/tests/checks/check_recycle_prompt.py
"""
import os, sys, tempfile, hashlib, time, threading
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
from core.common import recycle_bin as R
R.has_recycle_bin = lambda p: True
R.bin_capacity = lambda p: 1000                       # anything over 1000 bytes is "too big"
windows_calls = []
class NoShell:
    def SHFileOperationW(self, *a):
        windows_calls.append(a)
        return 1                                     # never reached for a too-big file
R.ctypes.WinDLL = lambda name: NoShell()
from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, QPointF, QTimer, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from studio import app as A

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
def until(cond, sec=10):
    end = time.time() + sec
    while not cond() and time.time() < end:
        pump(0.05)
    return cond()
prompt = next(o for o in win.findChildren(QObject) if o.objectName() == "prompt")
def button(text):
    def walk(it):
        for c in it.childItems():
            yield c
            yield from walk(c)
    for it in walk(win.contentItem().parentItem() or win.contentItem()):
        if it.property("text") == text and it.isVisible():
            return it
    return None
def click(item):
    p = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p.toPoint())
def big(name):
    p = tmp / name
    p.write_bytes(b"x" * 5000)
    return str(p)
pump(0.6)

# from a job thread
for answer, want_gone in (("Delete Permanently", True), ("Keep It", False)):
    path, out = big(f"clip_{want_gone}.mov"), {}
    t = threading.Thread(target=lambda: out.setdefault("r", R.send_to_recycle_bin(path)), daemon=True)
    t.start()
    up = until(lambda: prompt.property("visible") and "Too big for the Recycle Bin" in str(prompt.property("title")))
    msg = str(prompt.property("message"))
    check(f"a job thread's too-big file: Studio asks ({answer})", up and "removes it permanently" in msg
          and os.path.basename(path) in msg, msg[:120])
    check("...the job waits for the answer", t.is_alive())
    b = button(answer)
    if b:
        click(b)
    t.join(10)
    check(f"...{answer}: {'deleted for good' if want_gone else 'kept'}", out.get("r") is want_gone
          and os.path.exists(path) is (not want_gone), str(out))
    pump(0.5)
check("Windows' own delete never ran for a too-big file", windows_calls == [], str(windows_calls))

# from the UI thread (the folder picker): the window stays live while it asks
path = big("from_the_picker.mov")
QTimer.singleShot(800, lambda: click(button("Keep It")) if button("Keep It") else None)
res = objs["Folders"].recycle(path)
check("the folder picker (UI thread): asked, Keep It -> kept, no error", res == {"kept": True} and os.path.exists(path), str(res))
pump(0.5)

# quitting answers an open question no
path, out = big("at_quit.mov"), {}
t = threading.Thread(target=lambda: out.setdefault("r", R.send_to_recycle_bin(path)), daemon=True)
t.start()
until(lambda: prompt.property("visible"))
objs["Shell"].answer_all_no()
t.join(5)
check("quitting with the question up: answered no, the file kept", out.get("r") is False and os.path.exists(path), str(out))

h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
print("last_jobs unchanged:", h0 == h1, flush=True)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(1 if fails or h0 != h1 else 0)
