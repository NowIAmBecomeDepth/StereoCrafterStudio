"""Start Here in the Depth Map Editor, real app, offscreen (user, 2026-10-05:
"a start here button that can do all previous and ignores the next, or vice
versa"). SAM 3 stood in for (a fake engine yielding a mask per frame, as
Sam3Engine.propagate does for a direction and a start frame): the button and
its direction arrow beside Start Tracking; forward from the frame showing
replaces that frame and the ones after, back replaces the ones before, and
every other frame keeps its mask; the arrow's direction is remembered; Undo
puts the masks back; the progress line spans the panel. The real SAM 3's
directions: check_sam3_directions. Configs and last_jobs.json redirected."""
import os, sys, tempfile, hashlib, time, subprocess
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
import cv2
import numpy as np
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, QObject, Q_ARG, QPoint, QPointF, Qt, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication, QImage
from PySide6.QtTest import QTest
from core.depthedit import depth_io
from studio import app as A

# ---- the synthetic clip (v1's check's recipe) --------------------------------------
W, H, N = 256, 160, 24
clip_dir = tmp / "clip"; clip_dir.mkdir()
rgb_path = str(clip_dir / "synthetic.mp4").replace("\\", "/")
depth_path = str(clip_dir / "synthetic_depth.mp4").replace("\\", "/")
subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                f"testsrc2=size=512x320:rate=24:duration={N / 24}", "-pix_fmt", "yuv420p", rgb_path], check=True)
info = depth_io.ClipInfo(depth_path, W, H, N, "24/1", "yuv420p10le", False)
writer = depth_io.DepthWriter(depth_path, info)
ramp = np.linspace(0.1, 0.6, W, dtype=np.float32)[None, :].repeat(H, axis=0)
for f in range(N):
    frame = ramp.copy()
    frame[40:120, 80 + f:160 + f] = 0.5
    writer.write(depth_io.from_unit(frame, info))
writer.close()

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
def find(it, pred):
    for c in it.childItems():
        if pred(c): return c
        r = find(c, pred)
        if r is not None: return r
def click_in(item):
    QTest.mouseMove(win, QPoint(5, 5)); pump(0.1)
    c = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(c.x()), int(c.y())))

pump(0.6)
de = objs["DepthEdit"]
stage = find(win.contentItem(), lambda c: c.objectName() == "stage")
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 4), Q_ARG("QVariant", True)); pump(0.8)
extra = stage.property("pages").property(4).toQObject()
extra.setProperty("tool", 1); pump(0.8)
page = find(extra, lambda c: c.objectName() == "depthEditPage")
check("the Depth Map Editor is the second sub-tab", page is not None and page.isVisible())
check("it loads on first view", until(lambda: de.property("state").get("ready"), 60), str(de.property("state").get("loadError")))
S = lambda: de.property("state")  # noqa: E731
eng = de._engine

# ---- Start Tracking on every rail page (v1: a transition per page)
track = find(page, lambda c: c.objectName() == "deTrack")
hidden = []

from core.depthedit import sam3_backend as SB
calls = []
class FakeSam:
    """Sam3Engine as _track uses it: loaded, and propagate(direction, start_frame)."""
    loaded, owner, version = True, None, "sam3"
    def propagate(self, direction="both", start_frame=None):
        calls.append((direction, start_frame))
        if direction == "forward":
            frames = range(start_frame, N)
        elif direction == "backward":
            frames = range(start_frame - 1, -1, -1)          # SAM 3: the frame before the start
        else:
            frames = range(N)
        for f in frames:
            m = np.zeros((1, H, W), bool); m[0, 100:140, 10:60] = True   # the "new" mask: bottom-left
            yield f, ([1], m)
    def close_session(self):
        pass

de.setVar("rgb_path", rgb_path); pump(0.3)
de.setVar("output_dir", str(clip_dir / "edited").replace("\\", "/")); pump(0.2)
click_in(find(page, lambda c: c.objectName() == "deLoad"))
check("the clip loads", until(lambda: S().get("hasClip") and not S().get("busyJob"), 60))
eng.engine = FakeSam()
eng._sam_ready = lambda: True
eng._session_for = lambda layer, offload: True
de.run("_new_layer"); pump(0.3)
layer = eng._layer()
from core.depthedit.project import Click
layer.clicks.append(Click(0, 1, 0.3, 0.5, 1))
old = np.zeros((H, W), bool); old[20:60, 150:220] = True            # the "old" mask: top-right
for f in range(N):
    layer.masks.set(f, old)
layer.tracked = True

here = find(page, lambda c: c.objectName() == "deTrackHere")
arrow = find(page, lambda c: c.objectName() == "deTrackDir")
def tip(btn):
    return [str(o.property("tip")) for o in btn.findChildren(QObject) if o.property("tip") is not None][0]
check("Start Here and its arrow sit beside Start Tracking", here is not None and arrow is not None
      and here.isVisible() and arrow.isVisible() and here.property("text") == "Start Here")
check("...forward by default, the tips say which way", de.property("viewOptions").get("trackDir") == "forward"
      and "ON to the end" in tip(here) and "next frames" in tip(arrow), tip(here))

MID = 10
de.setFrame(MID); pump(0.3)
click_in(here)
check("forward: tracks once, from the frame showing", until(lambda: calls and not S().get("busyJob"), 20)
      and calls[-1] == ("forward", MID), str(calls))
pump(0.3)
new_at = lambda f: np.array_equal(layer.masks.get(f), np.pad(np.ones((40, 50), bool), ((100, H - 140), (10, W - 60))))  # noqa: E731
old_at = lambda f: np.array_equal(layer.masks.get(f), old)  # noqa: E731
layer = eng._layer()
check("...this frame and the ones after replaced", all(new_at(f) for f in range(MID, N)))
check("...the ones before keep their masks", all(old_at(f) for f in range(0, MID)))
check("...says so", "tracked forward from frame 11" in S()["status"] and "other frames keep" in S()["status"], S()["status"])
check("...an undo step", S()["undoLabel"] == "Track forward from here", S()["undoLabel"])

QMetaObject.invokeMethod(find(page, lambda c: c.objectName() == "deUndo"), "clicked"); pump(0.5)
layer = eng._layer()
check("Undo puts every frame's mask back", all(old_at(f) for f in range(N)))

QMetaObject.invokeMethod(arrow, "clicked"); pump(0.3)
check("the arrow switches to back, remembered", de.property("viewOptions").get("trackDir") == "backward"
      and ss.load("depthedit_view").get("trackDir") == "backward" and "BACK to the start" in tip(here), tip(here))
click_in(here)
check("back: tracks from the frame showing", until(lambda: calls[-1] == ("backward", MID) and not S().get("busyJob"), 20), str(calls))
pump(0.3)
layer = eng._layer()
check("...the previous frames replaced", all(new_at(f) for f in range(0, MID)))
check("...this frame and the ones after keep their masks", all(old_at(f) for f in range(MID, N)))
check("...says so", "tracked back from frame 11" in S()["status"], S()["status"])
QMetaObject.invokeMethod(arrow, "clicked"); pump(0.3)
check("the arrow back to forward", de.property("viewOptions").get("trackDir") == "forward")

# Start Tracking itself still both ways, every frame
de.run("_track")
until(lambda: calls[-1][0] == "both" and not S().get("busyJob"), 20); pump(0.3)
layer = eng._layer()
check("Start Tracking still tracks every frame both ways", calls[-1] == ("both", None) and all(new_at(f) for f in range(N)), str(calls[-1]))

bar = find(page, lambda c: c.objectName() == "deTrackBar")
lay = find(page, lambda c: c.objectName() == "deTrack")
check("the progress line spans the Layers panel, under the buttons", bar is not None and bar.width() > 300
      and bar.mapToItem(page, QPointF(0, 0)).y() >= lay.mapToItem(page, QPointF(0, lay.height())).y(),
      f"{bar.width():.0f} wide" if bar else "")
_rail = find(page, lambda c: c.objectName() == "deRail")
_r = _rail.mapToItem(page, QPointF(_rail.width(), 0)).x()
_stop_end = max(c.mapToItem(page, QPointF(c.width(), 0)).x() for c in (here, arrow))
check("...and the row stays inside it", _stop_end <= _r, f"{_stop_end:.0f} <= {_r:.0f}")
check("no QML warnings", not warns, "\n".join(warns[:5]))
h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
check("the real last_jobs.json untouched", h1 == h0)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(1 if fails else 0)   # not os._exit: the DLLs' teardown can crash after a green run
