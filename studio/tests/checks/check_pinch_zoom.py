"""Headless QML: a trackpad's pinch (user, 2026-10-31). Windows sends a precision touchpad's pinch to a
Qt app as the wheel with Ctrl held, in small steps (Qt's Windows plugin has no touchpad gestures --
studio/pinch.py), so this sends exactly that. Over Splatting's, Merging's and the Depth Map Editor's
previews it zooms the picture (as far as the fingers go, not a whole notch a step) and leaves the UI
Scale alone; anywhere else it steps the UI Scale 5% a notch's worth, saved as the theme popout's knob
saves it, and a page's scroll area under it doesn't scroll. Real app over TEMP copies of the fixtures
and a synthetic clip; configs and last_jobs.json redirected."""
import os, sys, tempfile, hashlib, time, subprocess, json
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
import numpy as np
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, QPoint, QPointF, Qt, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication, QWheelEvent
from PySide6.QtTest import QTest
from core.depthedit import depth_io
from studio import app as A

FIX = _fixtures.copy("small", tmp / "fx")
MFX = _fixtures.copy("merge", tmp / "mfx")
# the Depth Map Editor's clip (check_depthedit's recipe, shorter)
W, H, N = 256, 160, 6
clip_dir = tmp / "clip"; clip_dir.mkdir()
rgb_path = str(clip_dir / "synthetic.mp4").replace("\\", "/")
depth_path = str(clip_dir / "synthetic_depth.mp4").replace("\\", "/")
subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                f"testsrc2=size=512x320:rate=24:duration={N / 24}", "-pix_fmt", "yuv420p", rgb_path], check=True)
info = depth_io.ClipInfo(depth_path, W, H, N, "24/1", "yuv420p10le", False)
writer = depth_io.DepthWriter(depth_path, info)
for f in range(N):
    writer.write(depth_io.from_unit(np.linspace(0.1, 0.6, W, dtype=np.float32)[None, :].repeat(H, axis=0), info))
writer.close()

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
shell = objs["Shell"]
shell.setProperty("uiScale", "1.0")
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
def walk(it, out):
    for c in it.childItems():
        out.append(c); walk(c, out)
    return out
def into_view(item):
    p = item.parentItem()
    while p is not None:
        if p.property("contentHeight") is not None and p.property("contentY") is not None \
                and p.property("contentHeight") > p.height():
            ci = p.property("contentItem")
            y = item.mapToItem(ci, QPointF(0, 0)).y()
            top = max(0.0, min(y - (p.height() - item.height()) / 2, p.property("contentHeight") - p.height()))
            p.setProperty("contentY", top); pump(0.2)
        p = p.parentItem()

def pinch(item, fx, fy, units, step=15):
    """A trackpad pinch over `item` at (fx, fy) of it: `units` of the wheel's (120 a notch; + = fingers
    apart), sent as Windows does -- small Ctrl + wheel steps."""
    sp = item.mapToScene(QPointF(item.width() * fx, item.height() * fy))
    QTest.mouseMove(win, QPoint(int(sp.x()), int(sp.y()))); pump(0.05)
    global clock
    clock += 2000                    # a new pinch
    left = units
    while left:
        d = max(-step, min(step, left)); left -= d
        ev = QWheelEvent(sp, win.mapToGlobal(sp), QPoint(0, 0), QPoint(0, d), Qt.NoButton,
                         Qt.ControlModifier, Qt.NoScrollPhase, False)
        clock += 8                   # the touchpad's ~120 Hz
        ev.setTimestamp(clock)
        QGuiApplication.sendEvent(win, ev)
        pump(0.01)
    pump(0.3)
clock = 1000

def swipe(item, units, step=15):
    """A two-finger swipe sideways over `item`'s middle: `units` of the wheel's sideways (+: the fingers
    moving left to right, as the user's touchpad sends it), in small steps."""
    sp = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseMove(win, QPoint(int(sp.x()), int(sp.y()))); pump(0.05)
    left = units
    while left:
        d = max(-step, min(step, left)); left -= d
        QGuiApplication.sendEvent(win, QWheelEvent(sp, win.mapToGlobal(sp), QPoint(0, 0), QPoint(d, 0), Qt.NoButton,
                                                   Qt.NoModifier, Qt.NoScrollPhase, False))
        pump(0.01)
    pump(0.6)

stage = find(win.contentItem(), lambda c: c.objectName() == "stage")
pages = stage.property("pages")
def page_of(prefix):
    for i in range(8):
        p = pages.property(i).toQObject()
        if p is not None and p.metaObject().className().startswith(prefix):
            return p, i
    return None, None
pump(0.6)

# ---- Splatting
sp = objs["Splatting"]; spv = sp.preview
def settle(t=120):
    end = time.time() + t
    while time.time() < end:
        pump(0.05)
        with spv._cv:
            idle = not spv._jobs and not spv._inflight
        if idle and not spv._loading and not spv._render_timer.isActive():
            pump(0.4)
            with spv._cv:
                if not spv._jobs and not spv._inflight: return
page, idx = page_of("SplattingPage")
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", idx), Q_ARG("QVariant", True)); pump(1.2)
win.setWidth(2000); win.setHeight(1250); pump(0.8)
for k, v in {"input_source_clips": str(FIX / "src").replace("\\", "/"),
             "input_depth_maps": str(FIX / "depth").replace("\\", "/"),
             "output_splatted": str(FIX / "out").replace("\\", "/"), "multi_map": False,
             "preview_source": "Splat Result", "preview_size": "100%", "auto_save_sidecar": False}.items():
    sp._set(k, v)
spv.load(); settle()
check("Splatting: preview loaded", spv.loaded)
ssize = lambda: float(str(sp._map.value("preview_size")).rstrip("%"))
img = find(page, lambda c: c.objectName() == "previewImage" and c.isVisible())
flick = img.parentItem().parentItem().parentItem()          # Image -> shown -> contentItem -> Flickable
# from 100% (the auto-fit had set its own)
page.setProperty("previewFitOn", False); sp._set("preview_size", "100%"); settle()
check("Splatting: at 100% to begin", ssize() == 100, str(ssize()))
scale0 = shell.property("uiScale")
pinch(flick, 0.5, 0.5, 240); settle()
z = ssize()
check("Splatting: a pinch out over the preview zooms it in (two notches' worth, ~12% each)", abs(z - 125) <= 1, f"100 -> {z}")
check("...and leaves the UI Scale alone", shell.property("uiScale") == scale0, f"{scale0} -> {shell.property('uiScale')}")
pinch(flick, 0.5, 0.5, 60); settle()
z2 = ssize()
check("...half a notch's worth zooms ~6% (its small steps aren't rounded away)", 130 <= z2 <= 135, f"{z} -> {z2}")
pinch(flick, 0.5, 0.5, -300); settle()
check("...a pinch in zooms back out", abs(ssize() - 100) <= 1.5, f"{z2} -> {ssize()}")
# a sideways two-finger swipe slides through the frames (40 of the wheel's units a frame)
spv.seek(2); settle()
f0, z0 = spv.property("frame"), ssize()
swipe(flick, 240); settle()
check("Splatting: a two-finger swipe left to right over the preview: six frames on, the zoom as it was",
      spv.property("frame") == f0 + 6 and ssize() == z0, f"frame {f0} -> {spv.property('frame')} of {spv.property('frameCount')}, size {z0} -> {ssize()}")
swipe(flick, -160); settle()
check("...right to left: back four", spv.property("frame") == f0 + 2, f"{spv.property('frame')}")
check("...and left nothing held as a drag", not spv._dragging)

# ---- the UI Scale, anywhere else
shell.setProperty("uiScale", "0.9"); pump(0.5)
theme = engine.singletonInstance("Studio", "Theme")
scale_toasts = []
theme.toastRequested.connect(lambda k, ti, m: scale_toasts.append(ti) if "Scale" in str(ti) else None)
strip = find(win.contentItem(), lambda c: c.objectName() == "stripBed")
pinch(strip, 0.5, 0.5, 105)
check("a pinch over the header short of a notch's worth: the UI Scale stays", shell.property("uiScale") == "0.9",
      shell.property("uiScale"))
pinch(strip, 0.5, 0.5, 120)
check("...a notch's worth steps it 5% up", shell.property("uiScale") == "0.95", shell.property("uiScale"))
pinch(strip, 0.5, 0.5, -240)
check("...a pinch in, two notches' worth: two steps down", shell.property("uiScale") == "0.85", shell.property("uiScale"))
check("...with no toast for it (user, 2026-10-31)", theme is not None and not scale_toasts, str(scale_toasts))
shell.save()
saved = {}
for f in (tmp / "config").glob("*.json"):
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        continue
    if isinstance(d, dict) and "ui_scale" in d:
        saved[f.name] = d["ui_scale"]
check("...saved as Settings > Appearance saves it", any(str(v) == "0.85" for v in saved.values()), str(saved))
# over a page's scroll area (Splatting's settings), the pinch scales -- the area doesn't scroll
shell.setProperty("uiScale", "1.0"); pump(0.8)
fc = flick.mapToScene(QPointF(flick.width() / 2, flick.height() / 2))
scrollers = [c for c in walk(page, []) if c.isVisible() and c.property("contentY") is not None
             and c.property("contentHeight") is not None and c.height() > 40
             and not c.contains(c.mapFromScene(fc))]
scrollers.sort(key=lambda c: -(c.property("contentHeight") - c.height()))     # the one that scrolls most
if scrollers:
    sc = scrollers[0]
    y0 = sc.property("contentY")
    pinch(sc, 0.5, 0.5, -120)
    check("over a page's scroll area a pinch scales the UI and nothing scrolls",
          shell.property("uiScale") == "0.95" and sc.property("contentY") == y0,
          f"{shell.property('uiScale')}, contentY {y0} -> {sc.property('contentY')} ({sc.objectName() or sc.metaObject().className()})")
else:
    check("a scroll area to pinch over on Splatting's page", False)
shell.setProperty("uiScale", "1.0"); pump(0.8)
pinch(strip, 0.5, 0.5, -120, step=120)
check("a mouse's Ctrl + wheel notch over the header steps the UI Scale too", shell.property("uiScale") == "0.95",
      shell.property("uiScale"))
shell.setProperty("uiScale", "1.0"); pump(0.8)

# ---- Merging
mg = objs["Merging"]; mst = mg.property("settings"); mpv = mg.property("previewer")
mpage, midx = page_of("MergingPage")
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", midx), Q_ARG("QVariant", True)); pump(1.2)
for k, v in {"inpainted_folder": str(MFX / "inpainted"), "original_folder": str(MFX / "orig"),
             "mask_folder": str(MFX / "mask"), "output_folder": str(tmp / "merged"),
             "encoding_encoder": "Force CPU", "codec": "H.264", "use_gpu": False,
             "preview_source": "Blended Image", "preview_size": "100%"}.items():
    mst.setProperty(k, v)
pump(0.3)
mpv.load()
check("Merging: preview loaded", until(lambda: mpv.property("loaded") and not mpv.property("loading"), 60))
pump(1.0)
mpage.setProperty("previewFitOn", False); mst.setProperty("preview_size", "100%"); pump(1.5)
mimg = find(mpage, lambda c: c.objectName() == "previewImage" and c.isVisible())
mflick = mimg.parentItem().parentItem().parentItem()
pinch(mflick, 0.5, 0.5, 240); pump(1.5)
mz = float(str(mst.property("preview_size")).rstrip("%"))
check("Merging: a pinch out over the preview zooms it in", abs(mz - 125) <= 1, f"100 -> {mz}")
mpv.seek(1); pump(1.0)
mf0 = mpv.property("frame")
swipe(mflick, 120); pump(1.0)
check("Merging: a two-finger swipe left to right: three frames on, the zoom as it was",
      mpv.property("frame") == mf0 + 3 and float(str(mst.property("preview_size")).rstrip("%")) == mz,
      f"frame {mf0} -> {mpv.property('frame')} of {mpv.property('frameCount')}")
check("...and leaves the UI Scale alone", shell.property("uiScale") == "1.0", shell.property("uiScale"))

# ---- the Depth Map Editor
de = objs["DepthEdit"]
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 4), Q_ARG("QVariant", True)); pump(0.8)
extra = pages.property(4).toQObject()
extra.setProperty("tool", 1); pump(0.8)
dpage = find(extra, lambda c: c.objectName() == "depthEditPage")
until(lambda: de.property("state").get("ready"), 60)
de.setVar("rgb_path", rgb_path); pump(0.3)
de.setVar("output_dir", str(clip_dir / "edited").replace("\\", "/")); pump(0.2)
btn = find(dpage, lambda c: c.objectName() == "deLoad")
into_view(btn)
c = btn.mapToScene(QPointF(btn.width() / 2, btn.height() / 2))
QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(c.x()), int(c.y())))
check("Depth Map Editor: clip loaded",
      until(lambda: de.property("state").get("hasClip") and not de.property("state").get("busyJob"), 60))
pump(0.5)
pic = find(dpage, lambda c: c.objectName() == "dePicture")
into_view(pic)
shown = find(pic, lambda c: c.objectName() == "deImage").parentItem()
w0 = shown.width()
pinch(pic, 0.5, 0.5, 180)
check("Depth Map Editor: a pinch out over the picture zooms it (a notch and a half's worth)",
      w0 > 0 and abs(shown.width() / w0 - 1.12 ** 1.5) < 0.01, f"{shown.width() / max(w0, 1):.3f}")
check("...and leaves the UI Scale alone", shell.property("uiScale") == "1.0", shell.property("uiScale"))
de.setFrame(1); pump(0.5)
df0, dz0 = de.property("state")["frame"], pic.property("zoom")
swipe(pic, 120); pump(0.5)
check("Depth Map Editor: a two-finger swipe left to right: three frames on, the zoom as it was",
      de.property("state")["frame"] == df0 + 3 and pic.property("zoom") == dz0,
      f"frame {df0} -> {de.property('state')['frame']} of {de.property('state')['frames']}, zoom {dz0} -> {pic.property('zoom')}")
swipe(pic, -80); pump(0.5)
check("...right to left: back two", de.property("state")["frame"] == df0 + 1, str(de.property("state")["frame"]))

qml_warns = [w for w in warns if "pinch" in w.lower()]
check("no QML warnings from the pinch handlers", not qml_warns, str(qml_warns[:3]))
h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
check("last_jobs.json untouched (a Studio running beside this writes it too)", h0 == h1)
print("FAILED:" if fails else "ALL PASS", fails if fails else "", flush=True)
os._exit(1 if fails else 0)
