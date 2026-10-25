"""Drag and drop onto the path boxes (user, 2026-10-25: "Drop Zones", side by side over the section,
its height kept). Real app, offscreen; real drag events sent to the window, carrying TEMP copies of
the fixtures -- never the user's clips. Configs and last_jobs.json redirected. No GPU.

  * a drag over the window shows each page's zones over its path section, the section's height kept
  * letting go on a zone sets that box (a folder, or a clip where the box takes one)
  * a drop the box can't take (a clip on an output folder) leaves it as it was
  * Merge All's zones in Encoding Settings take over while the dialog is open
"""
import os, sys, tempfile, hashlib, time
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
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, QMimeData, QPoint, QPointF, QUrl, Qt
from PySide6.QtGui import QGuiApplication, QDragEnterEvent, QDragMoveEvent, QDragLeaveEvent, QDropEvent
from studio import app as A

fails = []
def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok: fails.append(label)

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
win.show()
def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.005)
def find(it, pred):
    for c in it.childItems():
        if pred(c): return c
        r = find(c, pred)
        if r is not None: return r
def find_all(it, pred, out=None):
    out = [] if out is None else out
    for c in it.childItems():
        if pred(c): out.append(c)
        find_all(c, pred, out)
    return out
def shown(it):
    while it is not None:
        if not it.isVisible() or it.opacity() == 0: return False
        it = it.parentItem()
    return True
objs["Shell"].setProperty("showTips", False)
pump(0.6)
root = win.contentItem()
stage = find(root, lambda c: c.objectName() == "stage")
def show(i):
    QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", i), Q_ARG("QVariant", True)); pump(0.8)
def pg(i): return stage.property("pages").property(i).toQObject()

FIX = _fixtures.copy("small", tmp / "fx")
CLIP = sorted((FIX / "src").glob("*.mp4"))[0]
DEPTH = sorted((FIX / "depth").glob("*.mp4"))[0]
FOLDER = FIX / "src"

def mime(path):
    m = QMimeData(); m.setUrls([QUrl.fromLocalFile(str(path))]); return m
def pt(item):
    p = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)); return QPoint(int(p.x()), int(p.y()))
DRAG_AT = [QPoint(40, 40)]      # where a drag comes in (in an open dialog, for its zones)
def drag_in(path, at=None):
    at = at or DRAG_AT[0]
    m = mime(path)
    QCoreApplication.sendEvent(win, QDragEnterEvent(at, Qt.CopyAction, m, Qt.LeftButton, Qt.NoModifier)); pump(0.15)
    return m
def zone(key):
    top = win.contentItem().parentItem() or win.contentItem()     # the overlay (dialogs) too
    zs = [z for z in find_all(top, lambda c: c.objectName() == "dropZone_" + key) if shown(z)]
    return zs[0] if zs else None
def drop(path, key):
    """Drag `path` in, over the zone for `key`, and let go. The zone (None when none showed)."""
    m = drag_in(path)
    z = zone(key)
    if z is None:
        QCoreApplication.sendEvent(win, QDragLeaveEvent()); pump(0.1)
        return None
    p = pt(z)
    QCoreApplication.sendEvent(win, QDragMoveEvent(p, Qt.CopyAction, m, Qt.LeftButton, Qt.NoModifier)); pump(0.1)
    if os.environ.get("DZ_GRAB"):
        win.grabWindow().save(str(Path(os.environ["DZ_GRAB"]) / f"drag_{key}_{Path(str(path)).suffix or 'folder'}.png"))
    QCoreApplication.sendEvent(win, QDropEvent(QPointF(p), Qt.CopyAction, m, Qt.LeftButton, Qt.NoModifier)); pump(0.9)
    return z
def norm(v): return str(v or "").replace("\\", "/").rstrip("/").lower()
def same(v, path): return norm(v) == norm(path)

def section_of(z):
    it = z
    while it is not None and it.metaObject().className().split("_")[0] != "DropZones":
        it = it.parentItem()
    return it.parentItem() if it is not None else None

# ---- each stage: (stage index, setup, settings store, [(key, what to drop, should take it)])
def st(name): return objs[name].property("settings")
cases = [
    ("DepthCrafter", 0, None, lambda: st("DepthCrafter"),
     [("input_dir_or_file_var", CLIP, True), ("output_dir", FOLDER, True), ("output_dir", CLIP, False)]),
    ("Splatting", 1, None, lambda: st("Splatting"),
     [("input_source_clips", FOLDER, True), ("input_depth_maps", DEPTH, True), ("output_splatted", FOLDER, True),
      ("output_splatted", CLIP, False)]),
    ("Inpainting V1", 2, lambda: pg(2).setProperty("engine", 0), lambda: st("Inpainting"),
     [("input_folder", FOLDER, True), ("hires_blend_folder", CLIP, True), ("output_folder", FOLDER, True),
      ("output_folder", CLIP, False)]),
    ("Inpainting V2", 2, lambda: pg(2).setProperty("engine", 1), lambda: objs["InpaintingV2"].property("settings"),
     [("input_folder", CLIP, True), ("output_folder", FOLDER, True), ("output_folder", CLIP, False)]),
    ("Merging", 3, None, lambda: st("Merging"),
     [("inpainted_folder", FOLDER, True), ("original_folder", CLIP, True), ("mask_folder", FOLDER, True),
      ("output_folder", FOLDER, True), ("output_folder", CLIP, False)]),
    ("Edge Fill", 4, lambda: pg(4).setProperty("tool", 2), lambda: st("EdgeFill"),
     [("merged_folder", FOLDER, True), ("mask_folder", FOLDER, True), ("merged_folder", CLIP, False)]),
    ("Scene Cut", 4, lambda: pg(4).setProperty("tool", 0), lambda: st("SceneCut"),
     [("output_dir", FOLDER, True), ("video_path", FOLDER, False), ("video_path", CLIP, True)]),
]
for name, i, setup, store_of, drops in cases:
    print(f"-- {name}")
    show(i)
    if setup: setup(); pump(0.6)
    store = store_of()
    # the zones show over the section, its height kept
    m = drag_in(FOLDER)
    z0 = zone(drops[0][0])
    sec = section_of(z0) if z0 else None
    check(f"{name}: zones show while dragging", z0 is not None)
    if sec is not None:
        h_drag = sec.height()
        QCoreApplication.sendEvent(win, QDragLeaveEvent()); pump(0.3)
        check(f"{name}: section height kept", abs(sec.height() - h_drag) < 0.5, f"{sec.height()} vs {h_drag}")
        check(f"{name}: zones hide after the drag", zone(drops[0][0]) is None)
    else:
        QCoreApplication.sendEvent(win, QDragLeaveEvent()); pump(0.2)
    for key, what, takes in drops:
        before = store.value(key) if hasattr(store, "value") else store.property(key)
        z = drop(what, key)
        after = store.value(key) if hasattr(store, "value") else store.property(key)
        if takes:
            check(f"{name}: {Path(str(what)).name} -> {key}", z is not None and same(after, what), repr(after))
        else:
            check(f"{name}: {Path(str(what)).name} refused by {key}", z is not None and after == before, repr(after))

# ---- the Depth Map Editor's clip and output folder (set through the controller)
print("-- Depth Map Editor")
show(4); pg(4).setProperty("tool", 1); pump(0.8)
de = objs["DepthEdit"]
def de_var(k):
    s = de.property("state")
    return (s.value(k) if hasattr(s, "value") else (s or {}).get(k))
z = drop(DEPTH, "depth_path"); pump(1.0)
check("DME: depth map -> depth_path", z is not None and same(de_var("depth_path"), DEPTH), repr(de_var("depth_path")))
before = de_var("rgb_path")
z = drop(FOLDER, "rgb_path")
check("DME: a folder refused by rgb_path", z is not None and de_var("rgb_path") == before, repr(de_var("rgb_path")))
de.setVar("page", "Output"); pump(0.6)
z = drop(FOLDER, "output_dir")
check("DME: folder -> output_dir", z is not None and same(de_var("output_dir"), FOLDER), repr(de_var("output_dir")))
de.setVar("page", "Segment"); pump(0.3)

# ---- Merge All in Encoding Settings: only its zones while the dialog is open
print("-- Merge All")
show(3)
mst = st("Merging")
from PySide6.QtCore import QObject
enc = next(o for o in win.findChildren(QObject) if o.metaObject().className().startswith("EncodingDialog")
           and o.property("mergeAll"))
enc.open(); pump(0.5); enc.setProperty("tab", 1); pump(0.5)
ci = enc.property("contentItem")
DRAG_AT[0] = pt(ci)
m = drag_in(FOLDER)
check("Merge All: its zones show", zone("mergeall_output_folder") is not None)
check("Merge All: the page's zones under it don't", zone("inpainted_folder") is None)
QCoreApplication.sendEvent(win, QDragLeaveEvent()); pump(0.2)
z = drop(FOLDER, "mergeall_output_folder")
check("Merge All: folder -> mergeall_output_folder", z is not None and same(mst.value("mergeall_output_folder"), FOLDER),
      repr(mst.value("mergeall_output_folder")))
z = drop(CLIP, "mergeall_audio_file")
check("Merge All: movie -> mergeall_audio_file", z is not None and same(mst.value("mergeall_audio_file"), CLIP),
      repr(mst.value("mergeall_audio_file")))
enc.close(); pump(0.4)
DRAG_AT[0] = QPoint(40, 40)
m = drag_in(FOLDER)
check("Merge All closed: the page's zones are back", zone("inpainted_folder") is not None)
QCoreApplication.sendEvent(win, QDragLeaveEvent()); pump(0.2)

if rec.exists() or h0 is not None:
    check("last_jobs.json untouched", (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None) == h0)
print("FAILED:" if fails else "ALL OK", fails if fails else "")
sys.exit(1 if fails else 0)
