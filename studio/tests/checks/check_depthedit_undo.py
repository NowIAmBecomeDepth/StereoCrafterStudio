"""Undo / Redo in the Depth Map Editor, real app, offscreen (user, 2026-10-05:
"add undo and redo buttons to depth map editor, and allow ctrl z and ctrl y
too"). The synthetic clip of check_depthedit: the buttons off until there is
something to undo, their tips naming the edit; a dial dragged is one step,
another dial another; Ctrl+Z / Ctrl+Y / Ctrl+Shift+Z and the buttons by
mouse step back and forth, the preview following; Delete layer answered No
leaves no step, Yes is undone with the layer's masks; a layer name typed is
one step; not while a job runs; Ctrl+Z left to a text box being typed in;
Load Clip starts afresh; the buttons fit the view row. Configs and
last_jobs.json redirected; no SAM 3, no GPU."""
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

undo_b = find(page, lambda c: c.objectName() == "deUndo")
redo_b = find(page, lambda c: c.objectName() == "deRedo")
keys = {o.objectName(): o for o in page.findChildren(QObject) if o.objectName() in ("deUndoKey", "deRedoKey")}
def tip(btn):
    return [str(o.property("tip")) for o in btn.findChildren(QObject) if o.property("tip") is not None][0]
def press(name):
    QMetaObject.invokeMethod(keys[name], "activated"); pump(0.3)
def redo_y():
    press("deRedoKey")

check("Undo / Redo buttons after the frame counter, both off before a clip", undo_b is not None and redo_b is not None
      and undo_b.isVisible() and not undo_b.property("enabled") and not redo_b.property("enabled"))
check("Ctrl+Z / Ctrl+Y (and Ctrl+Shift+Z) are there", set(keys) == {"deUndoKey", "deRedoKey"}
      and list(keys["deRedoKey"].property("sequences")) == ["Ctrl+Y", "Ctrl+Shift+Z"]
      and keys["deUndoKey"].property("sequence") == "Ctrl+Z", str([k for k in keys]))

de.setVar("rgb_path", rgb_path); pump(0.3)
de.setVar("output_dir", str(clip_dir / "edited").replace("\\", "/")); pump(0.2)
click_in(find(page, lambda c: c.objectName() == "deLoad"))
check("the clip loads", until(lambda: S().get("hasClip") and not S().get("busyJob"), 60))
check("...nothing to undo yet", not S()["canUndo"] and "Nothing to undo" in tip(undo_b), tip(undo_b))

de.run("_new_layer"); pump(0.3)
check("New layer is a step", S()["canUndo"] and S()["undoLabel"] == "New layer" and "Undo New layer (Ctrl+Z)" == tip(undo_b),
      (S()["undoLabel"], tip(undo_b)))
layer = eng._layer()
for f in range(N):
    m = np.zeros((H, W), bool); m[40:120, 80 + f:160 + f] = True
    layer.masks.set(f, m)
de.setControl("feather", 0); pump(0.2)
n0 = len(de._history._undo)
for v in (0.05, 0.1, 0.15, 0.2):                      # a drag: one change after another
    de.setControl("brightness", v); pump(0.05)
check("a dial dragged is one step", len(de._history._undo) == n0 + 1 and S()["undoLabel"] == "Push / pull",
      (len(de._history._undo) - n0, S()["undoLabel"]))
de.setControl("contrast", 1.5); pump(0.2)
check("another dial is another step", S()["undoLabel"] == "Contrast" and undo_b.property("enabled"))
edited = eng._edited_unit(0).copy()

press("deUndoKey")
check("Ctrl+Z undoes Contrast, Push / pull kept", abs(eng._layer().settings.contrast - 1.0) < 1e-9
      and abs(eng._layer().settings.brightness - 0.2) < 1e-9, (eng._layer().settings.contrast, eng._layer().settings.brightness))
check("...the dial shows it", abs(S()["controls"]["contrast"]["value"] - 1.0) < 1e-9, S()["controls"]["contrast"]["value"])
check("...says so, and Redo is ready", "Undone: Contrast" in S()["status"] and redo_b.property("enabled")
      and tip(redo_b) == "Redo Contrast (Ctrl+Y)", (S()["status"], tip(redo_b)))
press("deUndoKey")
check("Ctrl+Z again: Push / pull back to 0 -- the whole drag at once", abs(eng._layer().settings.brightness) < 1e-9,
      eng._layer().settings.brightness)
check("...the preview follows", not np.allclose(eng._edited_unit(0), edited))
redo_y(); redo_y()
check("Ctrl+Y twice: both back", abs(eng._layer().settings.brightness - 0.2) < 1e-9 and abs(eng._layer().settings.contrast - 1.5) < 1e-9
      and np.allclose(eng._edited_unit(0), edited) and not S()["canRedo"])
click_in(undo_b); pump(0.3)
check("the Undo button by mouse", abs(eng._layer().settings.contrast - 1.0) < 1e-9)
click_in(redo_b); pump(0.3)
check("the Redo button by mouse", abs(eng._layer().settings.contrast - 1.5) < 1e-9)
QMetaObject.invokeMethod(keys["deRedoKey"], "activated"); pump(0.2)
check("Redo with nothing to redo changes nothing", abs(eng._layer().settings.contrast - 1.5) < 1e-9)

# Delete layer asks first: No leaves no step, Yes is undone with the layer's masks
questions = []
de.question.connect(lambda t, m: questions.append(m))
before = len(de._history._undo)
de.run("_delete_layer"); pump(0.3)
de.answer(False); pump(0.3)
check("Delete layer answered No: no step", questions and len(eng.project.layers) == 1 and len(de._history._undo) == before
      and S()["undoLabel"] == "Contrast", (len(questions), S()["undoLabel"]))
de.run("_delete_layer"); pump(0.3)
de.answer(True); pump(0.3)
check("...answered Yes: gone, one step", not eng.project.layers and S()["undoLabel"] == "Delete layer")
press("deUndoKey")
back = eng.project.layers[0] if eng.project.layers else None
check("...undone: the layer back, its masks and settings with it", back is not None and back.masks.get(5) is not None
      and np.array_equal(back.masks.get(5), layer.masks.get(5)) and abs(back.settings.contrast - 1.5) < 1e-9
      and eng.selected == 0 and S()["layers"][0]["name"] == back.name)
check("...SAM 3 told to replay the layer's own clicks next time", eng.engine.owner is None)

# a layer name typed is one step
n1 = len(de._history._undo)
name0 = eng._layer().name
for t in ("R", "Ra", "Rai", "Rail"):
    de.setVar("layer_name", t); pump(0.05)
check("a layer name typed is one step", len(de._history._undo) == n1 + 1 and eng._layer().name == "Rail")
press("deUndoKey")
check("...undone at once", eng._layer().name == name0, (eng._layer().name, name0))

# not while a job runs
de.setControl("flatten", 0.5); pump(0.2)
eng._busy_job = "Tracking"; de._dirty(); pump(0.3)
press("deUndoKey")
check("not while a job runs: nothing changes, says why", abs(eng._layer().settings.flatten - 0.5) < 1e-9
      and "Wait for the current job" in S()["status"] and not undo_b.property("enabled"), S()["status"])
eng._busy_job = None; de._dirty(); pump(0.3)

# typing in a box: its own Ctrl+Z
field = [c for c in find_all(page, lambda c: c.metaObject().className().startswith("Field")) if c.property("varName") == "layer_name"] \
    if "find_all" in globals() else []
if not field:
    def _walk(it):
        for c in it.childItems():
            yield c
            yield from _walk(c)
    field = [c for c in _walk(page) if c.metaObject().className().startswith("Field") and c.property("varName") == "layer_name"]
de.setVar("page", "Layer"); pump(0.3)
if field:
    field[0].forceActiveFocus(); pump(0.2)
check("Ctrl+Z is the text box's while typing in one", bool(field) and page.property("undoKeysLive") is False)
field[0].setProperty("focus", False); pump(0.2)
check("...and the editor's again after", page.property("undoKeysLive") is True)

# the view row: nothing runs under the Layers rail
_rail = find(page, lambda c: c.objectName() == "deRail")
_railx = _rail.mapToItem(page, QPointF(0, 0)).x()
# both views (the 3D view ran 10 px under the panel until its RGB-over-depth
# controls were hidden there, 2026-10-05)
for v in ("Edited", "3D"):
    de.setVar("view", v); pump(0.3)
    ends = {n: (lambda c: c.mapToItem(page, QPointF(c.width(), 0)).x())(find(page, lambda c, n=n: c.objectName() == n))
            for n in ("deRedo", "deTool", "deWarpMode") + (("deRgbOpacity",) if v != "3D" else ())}
    check(f"the buttons, after the frame counter, and the rows above all clear the Layers panel ({v} view)", all(x <= _railx + 0.5 for x in ends.values()), f"rail at {_railx:.0f}: {ends}")
check("the RGB-over-depth controls are hidden in the 3D view only",
      not find(page, lambda c: c.objectName() == "deRgbOverlay").isVisible())
de.setVar("view", "Edited"); pump(0.3)
check("...and back in the Edited view", find(page, lambda c: c.objectName() == "deRgbOverlay").isVisible())

# Load Clip starts afresh
de.setVar("page", "Segment"); pump(0.2)
eng._status("")
de.run("_load_clip")
until(lambda: "Loaded" in str(S().get("status")) and not S().get("busyJob"), 60); pump(0.5)
check("Load Clip starts the history afresh", not S()["canUndo"] and not S()["canRedo"], (S()["canUndo"], S()["canRedo"]))
if os.environ.get("DE_SHOT"): win.grabWindow().save(os.environ["DE_SHOT"])
check("no QML warnings", not warns, "\n".join(warns[:5]))
h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
check("the real last_jobs.json untouched", h1 == h0)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(1 if fails else 0)   # not os._exit: the DLLs' teardown can crash after a green run
