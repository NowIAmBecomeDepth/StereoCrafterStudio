"""The Depth Map Editor's 3D view (user, 2026-09-30), in the real app
offscreen: the edited map through Splatting's OWN preview, so an edit is
judged in 3D before the map is rendered. Pins down: the view is offered; with
no edit it matches Splatting's own Dubois preview of the same frame read from
disk (the exact path, not a look-alike); an edit changes it; the right eye
with holes differs from the anaglyph; a clip Splatting's folders don't hold
is drawn on its own with the editor's map (user, 2026-10-12: single edits, a
map not from DepthCrafter), and Splatting is back on its own clip and frame
when shown again. Needs CUDA (Splatting's preview does). Configs
and last_jobs.json redirected; a synthetic clip in a temp folder."""
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
import numpy as np
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from core.depthedit import depth_io
from studio import app as A

W, H, N = 256, 160, 12
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
    frame[40:120, 80:160] = 0.5
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
def until(cond, sec=60):
    end = time.time() + sec
    while not cond() and time.time() < end:
        pump(0.05)
    return cond()
def find(it, pred):
    for c in it.childItems():
        if pred(c): return c
        r = find(c, pred)
        if r is not None: return r
def arr(qimg):
    q = qimg.convertToFormat(qimg.Format.Format_RGB888)
    b = q.constBits()
    a = np.frombuffer(b, np.uint8, q.sizeInBytes()).reshape(q.height(), q.bytesPerLine())[:, :q.width() * 3]
    return a.reshape(q.height(), q.width(), 3).astype(np.int16)

# Splatting pointed at the clip's folder (as Edit Depth Map from Splatting leaves it)
sp = objs["Splatting"]
for k, v in {"input_source_clips": str(clip_dir).replace("\\", "/"), "input_depth_maps": str(clip_dir).replace("\\", "/"),
             "output_splatted": str(tmp / "out").replace("\\", "/"), "multi_map": False, "auto_save_sidecar": False,
             "preview_source": "Dubois Anaglyph", "mask_highlight": False}.items():
    sp._set(k, v)

de = objs["DepthEdit"]
stage = find(win.contentItem(), lambda c: c.objectName() == "stage")
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 4), Q_ARG("QVariant", True)); pump(0.8)
extra = stage.property("pages").property(4).toQObject()
extra.setProperty("tool", 1); pump(0.8)
check("the editor loads", until(lambda: de.property("state").get("ready"), 60))
S = lambda: de.property("state")  # noqa: E731
eng = de._engine
de.setVar("rgb_path", rgb_path); pump(0.3)
de.setVar("output_dir", str(clip_dir / "edited").replace("\\", "/")); pump(0.2)
de.run("_load_clip")
check("the clip loads", until(lambda: S().get("hasClip") and not S().get("busyJob"), 60))
check("the view offers 3D", "3D" in (de.property("choices").get("view") or []), str(de.property("choices").get("view")))

FRAME = 3
de.setFrame(FRAME); pump(0.5)
def view3d(timeout=90):
    s0 = de.property("imageSerial")
    de._render_preview()
    ok = until(lambda: de.property("imageSerial") > s0 or de.property("warpNote"), timeout)
    return ok and not de.property("warpNote"), de.provider._images.get("frame")

de.setVar("view", "3D"); pump(0.3)
ok, img3d = view3d()
check("3D view: a picture from Splatting's preview", ok and img3d is not None, de.property("warpNote"))

# Splatting's own preview of the same frame, read from disk
pv = sp.preview
pv.load(); until(lambda: pv.property("loaded"), 60)
pv.seek(FRAME); pump(0.3)
s0 = pv.property("imageSerial") if pv.property("imageSerial") is not None else 0
pv.refresh(); pump(4.0)
own = pv.provider._images.get("frame")
if own is not None and img3d is not None:
    a, b = arr(img3d), arr(own)
    same_size = a.shape == b.shape
    diff = float(np.abs(a - b).mean()) if same_size else 999.0
else:
    same_size, diff = False, 999.0
check("no edit: the 3D view matches Splatting's own Dubois preview of that frame", same_size and diff < 1.0,
      f"shapes {None if img3d is None else (img3d.width(), img3d.height())} / {None if own is None else (own.width(), own.height())}, "
      f"mean |diff| {diff:.2f}")

# an edit: a layer pulls the square nearer
de.run("_new_layer"); pump(0.3)
layer = eng._layer()
for f in range(N):
    m = np.zeros((H, W), bool); m[40:120, 80:160] = True
    layer.masks.set(f, m)
layer.tracked = True
layer.settings.brightness = 0.35
ok, img_edit = view3d()
d_edit = float(np.abs(arr(img_edit) - arr(img3d)).mean()) if ok and img_edit is not None and img3d is not None else 0.0
check("an edit changes the 3D view", ok and d_edit > 0.5, f"mean |diff| {d_edit:.2f}")

de.setViewOption("warpMode", "holes"); pump(0.2)
ok, img_holes = view3d()
d_holes = float(np.abs(arr(img_holes) - arr(img_edit)).mean()) if ok and img_holes is not None else 0.0
check("Right Eye + Holes differs from the anaglyph", ok and d_holes > 1.0, f"mean |diff| {d_holes:.2f}")
de.setViewOption("warpMode", "anaglyph"); pump(0.2)

# Editing in the 3D view, the masks on the LEFT eye only (user, 2026-10-25: "you can see the shapes and
# masks tracked on the left eye" -- "or even better have it only show in the left eye")
got = {}
def sbs(overlay):
    got.clear()
    codes = depth_io.from_unit(eng._edited_unit(FRAME), eng.info)
    pv.render_external(rgb_path, FRAME, codes, "Side-by-Side", False, lambda img, err: got.update(img=img, err=err),
                       depth=depth_path, left_overlay=overlay)
    until(lambda: "img" in got, 90)
    return got.get("img")
plain = sbs(None)
colour = np.zeros((320, 512, 3), np.float32); colour[..., 0] = 1.0
alpha = np.zeros((320, 512), np.float32); alpha[100:220, 50:200] = 1.0
red = sbs((colour, alpha))
if plain is not None and red is not None and plain.width() == red.width():
    P, R_ = arr(plain), arr(red)
    half = P.shape[1] // 2
    hh, hw = P.shape[0], half
    box = (slice(int(0.35 * hh), int(0.6 * hh)), slice(int(0.15 * hw), int(0.35 * hw)))
    l_box = R_[:, :half][box]
    d_left = float(np.abs(R_[:, :half] - P[:, :half]).mean())
    d_right = float(np.abs(R_[:, half:] - P[:, half:]).mean())
else:
    l_box, d_left, d_right = None, 0.0, 999.0
check("Splatting's render: an overlay given for the left eye lands there, red where it was laid",
      l_box is not None and d_left > 2.0 and float(l_box[..., 0].mean()) > 200 and float(l_box[..., 1].mean()) < 40,
      f"left mean |diff| {d_left:.2f}")
check("...the right eye untouched (made from the clean frame)", d_right < 0.5, f"right mean |diff| {d_right:.2f}")

eng.overlay_var.set(True); pump(0.2)
ov = de._left_overlay(FRAME)
inside = ov is not None and float(ov[1][int(0.5 * ov[1].shape[0]), int(0.47 * ov[1].shape[1])]) > 0.1
outside = ov is not None and float(ov[1][int(0.9 * ov[1].shape[0]), int(0.05 * ov[1].shape[1])]) < 0.01
check("the editor's left-eye overlay: the layer's mask, nothing outside it", inside and outside,
      None if ov is None else (ov[1].shape, float(ov[1].max())))
ok, img_ov = view3d()
eng.overlay_var.set(False); pump(0.2)
ok2, img_no = view3d()
d_ov = float(np.abs(arr(img_ov) - arr(img_no)).mean()) if ok and ok2 and img_ov is not None and img_no is not None else 0.0
check("...the 3D view shows the masks with the overlay on, not with it off", d_ov > 0.2, f"mean |diff| {d_ov:.2f}")
eng.overlay_var.set(True); pump(0.2)

page = find(extra, lambda c: c.objectName() == "depthEditPage")
shown = find(find(page, lambda c: c.objectName() == "dePicture"), lambda c: c.objectName() == "deImage").parentItem()
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtTest import QTest
def mdrag(a, b):
    pa = shown.mapToScene(QPointF(a[0] * shown.width(), a[1] * shown.height()))
    pb = shown.mapToScene(QPointF(b[0] * shown.width(), b[1] * shown.height()))
    QTest.mousePress(win, Qt.LeftButton, Qt.NoModifier, QPoint(round(pa.x()), round(pa.y()))); pump(0.1)
    for k in range(1, 6):
        q = pa + (pb - pa) * (k / 5)
        QTest.mouseMove(win, QPoint(round(q.x()), round(q.y()))); pump(0.05)
    QTest.mouseRelease(win, Qt.LeftButton, Qt.NoModifier, QPoint(round(pb.x()), round(pb.y()))); pump(0.5)
n_shapes = lambda: sum(len(ly.shapes or []) for ly in eng.project.layers)  # noqa: E731
de.setVar("tool", "Shape"); de.setShapeKind("Rectangle"); pump(0.4)
before = n_shapes()
mdrag((0.6, 0.2), (0.85, 0.5))
check("in the 3D view (Anaglyph) a Rectangle drag draws a shape", n_shapes() == before + 1, f"{before} -> {n_shapes()}")
until(lambda: not S().get("busyJob"), 30); ok, _ = view3d()
de.setViewOption("warpMode", "holes"); pump(0.3); ok, _ = view3d()
before = n_shapes()
mdrag((0.1, 0.6), (0.3, 0.9))
check("...Right Eye + Holes (no left eye shown) takes no edit: the drag pans", n_shapes() == before, f"{before} -> {n_shapes()}")
de.setViewOption("warpMode", "anaglyph"); de.setVar("tool", "Add"); pump(0.3)

# Glasses-Free 3D (user, 2026-10-11; studio/sr_display.py): offered only with an SR display
# (faked: offscreen never touches the SR service); the edited pair to the weaver at full size,
# the editor showing its left eye
from studio import sr_display
offered = {}
sr_display.offer_frame = lambda stage, image: offered.__setitem__(stage, np.array(image))
hub = objs["Glasses3D"]
wm = find(win.contentItem(), lambda c: c.objectName() == "deWarpMode")
models = lambda w: list((lambda m: m.toVariant() if hasattr(m, "toVariant") else m)(w.property("model")) or [])  # noqa: E731
dis = lambda w: list((lambda m: m.toVariant() if hasattr(m, "toVariant") else m)(w.property("disabledItems")) or [])  # noqa: E731
check("no SR display: Glasses-Free 3D listed, greyed out", wm is not None and "Glasses-Free 3D" in models(wm)
      and "Glasses-Free 3D" in dis(wm), str(wm and (models(wm), dis(wm))))
check("...with why", "needs a connected glasses-free 3D monitor on Leia's SR platform, with its SR software installed and running" in str(wm.property("glassesWhyNot")))
hub._available = True; hub.availableChanged.emit(); pump(0.3)
hub._screen = win.screen().name(); hub.availableChanged.emit(); pump(0.3)
check("with one (this window on it): usable", "Glasses-Free 3D" in models(wm) and not dis(wm), str((models(wm), dis(wm))))
de.setViewOption("warpMode", "glasses"); pump(0.2)
check("...and picked", wm.property("currentIndex") == 2, str(wm.property("currentIndex")))
ok, img_gf = view3d()
pair = offered.get("depthedit")
check("Glasses-Free 3D: the weaver gets the edited pair, each eye at full size",
      ok and img_gf is not None and pair is not None and pair.shape[:2] == (img_gf.height(), 2 * img_gf.width()),
      f"{None if pair is None else pair.shape} for {None if img_gf is None else (img_gf.width(), img_gf.height())}")
check("...the editor shows its left eye", ok and pair is not None
      and np.array_equal(arr(img_gf), pair[:, :img_gf.width()].astype(np.int16)))
check("...the edit is in it (differs from the unedited anaglyph's frame)", ok and arr(img_gf).shape == arr(img3d).shape
      and float(np.abs(arr(img_gf) - arr(img3d)).mean()) > 0.5)
gfv = find(win.contentItem(), lambda c: c.objectName() == "glassesFreeView" and c.property("stage") == "depthedit")
check("...stereo3d on, the woven window wanted", de.property("stereo3d") is True and gfv is not None
      and gfv.property("on") is True)
de.setViewOption("warpMode", "anaglyph"); pump(0.2)
ok, _img = view3d()
check("Anaglyph again: stereo3d off", ok and de.property("stereo3d") is False and not gfv.property("on"))
hub._available = False; hub.availableChanged.emit(); pump(0.3)

# a clip Splatting doesn't hold (user, 2026-10-12): drawn on its own, with the editor's map;
# Splatting, showing its own clips elsewhere, goes back to them where it was
fx = _fixtures.copy("small", tmp / "elsewhere")
sp._set("input_source_clips", str(fx / "src").replace("\\", "/"))
sp._set("input_depth_maps", str(fx / "depth").replace("\\", "/"))
sp._set("auto_save_sidecar", False)
pv.load(); until(lambda: pv.property("loaded"), 60); pump(1.0)
pv.seek(5); pump(2.0)
home = (pv._engine.previewer.last_loaded_video_path, pv.property("frame"))
de.setVar("view", "3D"); pump(0.3)
ok, img_alone = view3d()
d_alone = float(np.abs(arr(img_alone) - arr(img_edit)).mean()) if ok and img_alone is not None else 999.0
check("a clip not in Splatting's folders: drawn all the same, no note", ok and img_alone is not None,
      de.property("warpNote"))
check("...as it was from Splatting's folders (no sidecar either way: the page's settings)", d_alone < 1.0,
      f"mean |diff| {d_alone:.2f}")
check("...no sidecar written beside the editor's map", not list(clip_dir.glob("*.fssidecar")),
      str(list(clip_dir.glob("*.fssidecar"))))
pv.refreshIfStale(); pump(0.5)
until(lambda: pv._engine.previewer.last_loaded_video_path == home[0] and pv.property("frame") == home[1], 60)
pump(1.0)
names = [Path(e["source_video"]).name for e in pv._engine.previewer.video_list]
import logging
class _Catch(logging.Handler):
    def __init__(self):
        super().__init__(); self.bad = []
    def emit(self, r):
        if "Preview failed" in r.getMessage() or "Error updating preview" in r.getMessage():
            self.bad.append(r.getMessage())
catch = _Catch(); logging.getLogger().addHandler(catch)
s1 = pv.property("imageSerial") or 0
pv.refresh(); until(lambda: (pv.property("imageSerial") or 0) > s1, 60); pump(1.0)
logging.getLogger().removeHandler(catch)
check("...and draws its own picture there, cleanly", (pv.property("imageSerial") or 0) > s1 and not catch.bad,
      str(catch.bad[:2]))
check("Splatting shown again: its own clips, on the clip and frame it was on",
      pv._engine.previewer.last_loaded_video_path == home[0] and pv.property("frame") == home[1]
      and "synthetic.mp4" not in names and len(names) == 2,
      f"{pv._engine.previewer.last_loaded_video_path} @ {pv.property('frame')} vs {home}; {names}")
# no depth map: a note
_dp = eng.depth_path_var.get()
eng.depth_path_var.set(str(tmp / "missing_depth.mp4"))
s0 = de.property("imageSerial")
de._render_preview()
until(lambda: de.property("warpNote"), 60)
check("...a clip with no depth map there: a note, no picture",
      "depth map" in de.property("warpNote") and de.property("imageSerial") == s0, de.property("warpNote"))
note = find(win.contentItem(), lambda c: c.objectName() == "deWarpNote")
check("...shown over the picture", note is not None and note.isVisible())
eng.depth_path_var.set(_dp)

# a map from another tool (user, 2026-10-12: "edit a non dc map"): 8-bit, its own name, no
# Splatting folder -- the editor loads it, and the 3D view draws it, an edit and all
other_dir = tmp / "other"; other_dir.mkdir()
other_rgb = str(other_dir / "shot.mp4").replace("\\", "/")
other_map = str(other_dir / "shot_from_another_tool.mp4").replace("\\", "/")
subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                f"testsrc2=size=512x320:rate=24:duration={N / 24}", "-pix_fmt", "yuv420p", other_rgb], check=True)
subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                f"nullsrc=size=256x160:rate=24:duration={N / 24},geq=lum='clip(40+X/2,0,255)':cb=128:cr=128",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", other_map], check=True)
de.setVar("rgb_path", other_rgb); pump(0.3)
de.setVar("depth_path", other_map); pump(0.3)
de.setVar("output_dir", str(other_dir / "edited").replace("\\", "/")); pump(0.2)
de.run("_load_clip")
_loaded_other = lambda: (os.path.normcase(str(getattr(eng.info, "path", "") or "")) == os.path.normcase(other_map)  # noqa: E731
                         and S().get("hasClip") and not S().get("busyJob"))
check("a map from another tool (8-bit, its own name) loads",
      until(_loaded_other, 60)
      and os.path.normcase(eng.depth_path_var.get()) == os.path.normcase(other_map), eng.depth_path_var.get())
de.setVar("view", "3D"); pump(0.3)
ok, img_other = view3d()
check("...and the 3D view draws it", ok and img_other is not None, de.property("warpNote"))
de.run("_new_layer"); pump(0.3)
layer = eng._layer()
for f in range(N):
    m = np.zeros((H, W), bool); m[40:120, 80:160] = True
    layer.masks.set(f, m)
layer.tracked = True
layer.settings.brightness = 0.35
ok, img_other_edit = view3d()
check("...with the edit in it", ok and img_other_edit is not None
      and float(np.abs(arr(img_other_edit) - arr(img_other)).mean()) > 0.5)
check("...no sidecar written beside it", not list(other_dir.glob("*.fssidecar")), str(list(other_dir.glob("*.fssidecar"))))

de.setVar("view", "Edited"); pump(0.5)
check("back to Edited: no note", not (note and note.isVisible()))
check("no QML warnings", not warns, str(warns[:3]))
h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
check("the real last_jobs.json untouched", h0 == h1)
print("PASS" if not fails else f"FAIL ({len(fails)})")
_fixtures.exit_now(0 if not fails else 1)
