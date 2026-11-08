"""Edge Fill's grid keeps its scroll when it looks again at the same folder (user, 2026-11-07: after
every fill the grid jumped to the top) -- a fill ending, Refresh -- and starts at the top for another
folder. Real app, offscreen, made-up clips (16 of them, so the grid scrolls); the fill's end is its busy
going off, as a fill's does. Temp folders; configs and last_jobs.json redirected."""
import os, sys, tempfile, hashlib, time, subprocess
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, str(Path(__file__).parent))
rec = REPO / "last_jobs.json"; h0 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
import _fixtures
import numpy as np
from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, QPointF, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from studio import app as A
from core.removal import rose_env

W, H, N, STRIP = 320, 144, 4, 18
def encode(path, frames, args):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    p = subprocess.Popen(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s",
                          f"{frames[0].shape[1]}x{H}", "-r", "24", "-i", "-", *args, path], stdin=subprocess.PIPE)
    p.stdin.write(b"".join(np.ascontiguousarray(f).tobytes() for f in frames)); p.stdin.close(); assert p.wait() == 0
eyes = [np.full((H, W, 3), 90 + 20 * i, np.uint8) for i in range(N)]
right = [e.copy() for e in eyes]
for r in right:
    r[:, :STRIP] = (20, 10, 40)
mask = np.zeros((H, W, 3), np.uint8); mask[:, :STRIP] = 255
DNX = ["-c:v", "dnxhd", "-profile:v", "dnxhr_hq", "-pix_fmt", "yuv422p"]
def make(folder, masks, count):
    for k in range(count):
        name = f"clip{k:02d}_{W}"
        encode(os.path.join(folder, f"{name}_merged_full_sbs_L.mov"), eyes, DNX)
        encode(os.path.join(folder, f"{name}_merged_full_sbs_R.mov"), right, DNX)
        encode(os.path.join(masks, f"{name}_splatted2.mkv"), [np.hstack([mask, p]) for p in eyes],
               ["-c:v", "ffv1", "-pix_fmt", "bgr0"])
OUT, MASKS = str(tmp / "Merged"), str(tmp / "Splats")
OUT2, MASKS2 = str(tmp / "Merged2"), str(tmp / "Splats2")
make(OUT, MASKS, 16)
make(OUT2, MASKS2, 16)
rose_env.ready = lambda: True

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
def until(cond, sec=30):
    end = time.time() + sec
    while not cond() and time.time() < end:
        pump(0.05)
    return cond()
def _tree(it, acc):
    for c in it.childItems():
        acc.append(c); _tree(c, acc)
    return acc
def shown(name):
    root = win.contentItem().parentItem() or win.contentItem()
    return [o for o in _tree(root, []) if o.objectName() == name and o.isVisible()]
def click(item):
    p = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p.toPoint()); pump(0.3)

pump(0.6)
mg, shell, ef = objs["Merging"], objs["Shell"], objs["EdgeFill"]
mg.settings.setProperty("output_folder", OUT); mg.settings.setProperty("mask_folder", MASKS)
shell.openStage(3, -1); pump(0.6)
click(shown("mgFillEdges")[0])
page = next(o for o in win.findChildren(QObject) if o.objectName() == "edgeFillPage")
check("the grid shows the 16 clips", until(lambda: page.property("phase") == "list" and len(shown("fillEdgesTile")) == 16, 60),
      f"{page.property('phase')} {len(shown('fillEdgesTile'))}")
until(lambda: not ef.property("scanning"), 60); pump(1.0)
lst = shown("fillEdgesList")[0]
room = lst.property("contentHeight") - lst.property("height")
check("...more than fits: it scrolls", room > 100, f"room {room:.0f}")

def scrolled_to(y):
    lst.setProperty("contentY", y); pump(0.3)
    return lst.property("contentY")
def looked_again(trigger):
    n0 = page.property("phase")
    trigger()
    until(lambda: page.property("phase") != "list", 5)          # the look hides the grid...
    ok = until(lambda: page.property("phase") == "list" and len(shown("fillEdgesTile")) == 16, 60)
    until(lambda: not ef.property("scanning"), 60); pump(1.0)
    return ok

y = scrolled_to(room * 0.6)
check("scrolled part way down", abs(y - room * 0.6) < 2, f"{y:.0f}")
def fill_ends():
    ef._set_busy(True); pump(0.3)                     # as a fill does: busy on...
    ef._set_busy(False)                              # ...and off: the grid looks again
check("a fill ends: the grid looks again", looked_again(fill_ends))
got = lst.property("contentY")
check("...and stays where it was scrolled", abs(got - y) < 2, f"{got:.0f} vs {y:.0f}")

y = scrolled_to(room * 0.3)
check("Refresh: the grid looks again", looked_again(lambda: click(shown("edgeFillRefresh")[0])))
got = lst.property("contentY")
check("...and stays where it was scrolled", abs(got - y) < 2, f"{got:.0f} vs {y:.0f}")

scrolled_to(room * 0.8)
def other_folder():
    ef.settings.setProperty("merged_folder", OUT2.replace("\\", "/")); ef.settings.setProperty("mask_folder", MASKS2.replace("\\", "/"))
    ef.foldersChanged.emit()
check("another folder: the grid looks", looked_again(other_folder))
got = lst.property("contentY")
check("...and starts at the top", got < 2, f"{got:.0f}")

for name in ("EdgeFill", "Merging"):
    try: objs[name].shutdown()
    except Exception: pass
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
