"""Glasses-Free 3D (user, 2026-10-11; studio/sr_display.py), real app, offscreen,
over the synthetic fixtures -- never the SR service (offscreen, sr_display leaves
it alone), so the display is faked where it is needed:

  * no SR display: neither page offers "Glasses-Free 3D"; one connected: both do;
  * known to be gone: a Front / Back side holding it takes its default;
  * Merging and Splatting render it as the full side-by-side handed to the
    weaver (each eye at the clip's full width), the page showing its left eye
    (the picture's width is the clip's, stereo3d on); another view: off again;
  * the woven window is placed over the visible picture, hidden when the card
    shows another source, and kept (frozen) while a job runs;
  * no QML warnings. Configs and last_jobs.json redirected.
"""
import os, sys, tempfile, hashlib, time
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
os.environ.pop("STUDIO_SR_CHECKS", None)
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, str(Path(__file__).parent))
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
import _fixtures
import numpy as np
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, QObject, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from studio import app as A
from studio import sr_display

merge_fx = _fixtures.copy("merge", tmp / "mfx")
splat_fx = _fixtures.copy("small", tmp / "sfx")
GF = sr_display.SOURCE

# what the weaver would be handed (the renderer is not running offscreen)
offered = {}
sr_display.offer_frame = lambda stage, image: offered.__setitem__(
    stage, np.array(image if isinstance(image, np.ndarray) else image.convert("RGB")))

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
shell = objs["Shell"]
shell.setProperty("uiScale", "1.0")
win.show()
hub = objs["Glasses3D"]
fails = []


def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok:
        fails.append(label)


def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.005)


def until(cond, sec=30):
    end = time.time() + sec
    while not cond() and time.time() < end:
        pump(0.05)
    return cond()


def find_all(it, pred, out):
    for c in it.childItems():
        if pred(c):
            out.append(c)
        find_all(c, pred, out)
    return out


pump(1.0)
stage = find_all(win.contentItem(), lambda c: c.objectName() == "stage", [])[0]


def page(index):
    QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", index), Q_ARG("QVariant", True)); pump(1.0)
    return stage.property("pages").property(index).toQObject()


def offers(pg):
    boxes = find_all(pg, lambda c: c.objectName() in ("flipFront", "flipBack"), [])
    return [GF in list(b.property("model") or []) for b in boxes]


def set_available(on):
    hub._available = bool(on)
    hub.availableChanged.emit()
    pump(0.3)


def jsl(v):
    return list((v.toVariant() if hasattr(v, "toVariant") else v) or [])


def greyed(pg):
    """per Front / Back box: (listed, greyed out, its tip)"""
    boxes = find_all(pg, lambda c: c.objectName() in ("flipFront", "flipBack"), [])
    return [(GF in jsl(b.property("model")), GF in jsl(b.property("disabledItems")), b.property("disabledTip"))
            for b in boxes]


# ---- listed always; usable only with a display, on its screen (user, 2026-10-11) ----------
check("offscreen: the SR service is left alone", hub._renderer is None and not hub.property("available"))
for index, name in ((1, "Splatting"), (3, "Merging")):
    g = greyed(page(index))
    check(f"{name}: no SR display: listed, greyed out", len(g) == 2 and all(l and d for l, d, _ in g), str(g))
    check(f"...the tip says it needs a compatible display", all("needs a connected glasses-free 3D monitor on Leia's SR platform, with its SR software installed and running" in t for _, _, t in g))
# picking it anyway changes nothing
fb = find_all(page(3), lambda c: c.objectName() == "flipFront", [])[0]
before = shell.property("mergeFlipFront")
QMetaObject.invokeMethod(fb, "activated", Q_ARG(int, jsl(fb.property("model")).index(GF))); pump(0.3)
check("...and a pick of it is not taken", shell.property("mergeFlipFront") == before, str(shell.property("mergeFlipFront")))
set_available(True)
hub._screen, hub._display_name = "elsewhere", "Odyssey G90XF"; hub.availableChanged.emit(); pump(0.3)
g = greyed(page(1))
check("display connected, this window on another screen: greyed, 'move this window'",
      all(d for _, d, _ in g) and all("Move this window" in t and "Odyssey G90XF" in t for _, _, t in g), str(g))
hub._screen = win.screen().name(); hub.availableChanged.emit(); pump(0.3)
for index, name in ((1, "Splatting"), (3, "Merging")):
    g = greyed(page(index))
    check(f"{name}: on its screen: usable", len(g) == 2 and all(l and not d for l, d, _ in g), str(g))

# ---- the Front / Back boxes fit every option's text (user, 2026-10-11) -----------------
for index, name, keys in ((1, "Splatting", ("splatFlipFront", "splatFlipBack")),
                          (3, "Merging", ("mergeFlipFront", "mergeFlipBack"))):
    pg = page(index)
    boxes = find_all(pg, lambda c: c.objectName() in ("flipFront", "flipBack"), [])
    keep = [shell.property(k) for k in keys]
    cut = []
    for box in boxes:
        text = box.property("contentItem")
        for opt in list(box.property("model")):
            shell.setProperty(box.property("key"), opt); pump(0.05)
            if text.property("truncated"):
                cut.append(opt)
    for k, v in zip(keys, keep):
        shell.setProperty(k, v)
    pump(0.3)
    check(f"{name}: no option's text cut short in Front / Back", not cut, str(sorted(set(cut))))
    check(f"{name}: the boxes at their full width", all(b.width() == b.property("fitWidth") for b in boxes),
          str([(b.width(), b.property("fitWidth")) for b in boxes]))
    row = boxes[0].parentItem()
    panel = find_all(pg, lambda c: c.objectName() == "previewPanel", []) or [row.parentItem()]
    check(f"{name}: the row stays inside the preview panel", row.width() <= panel[0].width(),
          f"{row.width():.0f} in {panel[0].width():.0f}")

# ---- gone: a side holding it takes its default
shell.setProperty("mergeFlipBack", GF); shell.setProperty("splatFlipFront", GF); pump(0.3)
hub._probed = True; hub._info = None
hub._match(); pump(0.5)
check("display gone: greyed out again", not hub.property("available") and all(d for _, d, _ in greyed(page(3))))
check("...and the sides holding it took their defaults",
      shell.property("mergeFlipBack") == "Blended Image" and shell.property("splatFlipFront") == "Dubois Anaglyph",
      f"{shell.property('mergeFlipBack')} / {shell.property('splatFlipFront')}")
set_available(True)
hub._screen = win.screen().name(); hub.availableChanged.emit(); pump(0.3)

# ---- Merging: the full pair to the weaver, the left eye on the page -----------------
mpage = page(3)
mg = objs["Merging"]; mst = mg.property("settings"); mpv = mg.property("previewer")
for k, v in {"inpainted_folder": str(merge_fx / "inpainted"), "original_folder": str(merge_fx / "orig"),
             "mask_folder": str(merge_fx / "mask"), "output_folder": str(tmp / "merged"),
             "encoding_encoder": "Force CPU", "codec": "H.264", "use_gpu": False,
             "preview_source": "Side-by-Side", "preview_size": "100%"}.items():
    mst.setProperty(k, v)
pump(0.3)
mpv.load()
check("Merging: preview loads", until(lambda: mpv.property("loaded") and not mpv.property("loading"), 60))
check("Merging: Side-by-Side is two eyes wide", until(lambda: mpv.property("imageWidth") == 640, 15))
until(lambda: not mpv.property("loading"), 5); pump(0.5)
sbs = mpv.provider._images.get("frame")
sbs_left = np.frombuffer(sbs.constBits(), np.uint8).reshape(sbs.height(), sbs.bytesPerLine())[:, :320 * 3].reshape(180, 320, 3).copy()
shell.setProperty("mergeFlipFront", GF); pump(0.3)
check("Merging: Glasses-Free 3D shows the left eye (the clip's width)",
      until(lambda: mpv.property("stereo3d") and mpv.property("imageWidth") == 320, 15),
      f"{mpv.property('imageWidth')} stereo3d={mpv.property('stereo3d')}")
pair = offered.get("merging")
check("Merging: the weaver gets the full side-by-side (each eye 320 wide)",
      pair is not None and pair.shape == (180, 640, 3), str(None if pair is None else pair.shape))
img = mpv.provider._images.get("frame")
shown = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine())[:, :320 * 3].reshape(180, 320, 3)
check("Merging: the page's picture is that pair's left eye", pair is not None and np.array_equal(shown, pair[:, :320]))
check("...the same left eye as the Side-by-Side view's", np.array_equal(shown, sbs_left))

view = find_all(mpage, lambda c: c.objectName() == "glassesFreeView", [])
check("Merging: the view is on the page", len(view) == 1)
gfv = view[0]
check("Merging: the view wants the picture", gfv.property("wanted") is True and gfv.property("on") is True)
# offscreen is not the SR display's screen: placing it would be refused, so say it is
hub._screen = win.screen().name()
QMetaObject.invokeMethod(gfv, "update"); pump(0.4)
pic = find_all(mpage, lambda c: c.objectName() == "previewImage", [])[0]
r = pic.mapRectToScene(pic.boundingRect())
geo = hub._geo
check("Merging: the woven window covers the picture", hub._shown and geo is not None
      and abs(geo.width() - r.width()) <= 1 and abs(geo.height() - r.height()) <= 1
      and abs(geo.x() - r.x()) <= 1 and abs(geo.y() - r.y()) <= 1,
      f"{geo} vs {r}")
check("...the whole picture (no crop)", np.allclose(hub._crop, (0.0, 0.0, 1.0, 1.0), atol=1e-6), str(hub._crop))
_fixtures.fake_busy(objs["Inpainting"], True); pump(0.6)
QMetaObject.invokeMethod(gfv, "update"); pump(0.3)
check("a job: frozen (kept, the hub calm)", hub._shown and hub.property("calm") is True)
_fixtures.fake_busy(objs["Inpainting"], False); pump(0.6)
check("...and live after", hub._shown and hub.property("calm") is False)

# Keep Tracking (user, 2026-10-17: the camera kept on between clips / Full Preview / jobs)
def keep_box(pg):
    boxes = find_all(pg, lambda c: c.objectName() == "keepTracking", [])
    return boxes[0] if len(boxes) == 1 else None
kt = keep_box(mpage)
check("Keep Tracking: beside Merging's choice while Glasses-Free 3D is chosen", kt is not None and kt.property("shown") is True)
_prow = kt.parentItem() if kt is not None else None
_ppanel = find_all(mpage, lambda c: c.objectName() == "previewPanel", [])
check("...the row still inside the preview panel", _prow is not None and (not _ppanel or _prow.width() <= _ppanel[0].width()),
      f"{_prow.width() if _prow else 0:.0f} in {_ppanel[0].width() if _ppanel else 0:.0f}")
check("...off by default, the hub not holding", shell.property("glassesKeepTracking") is False and hub._keep is False)
check("...the page's view wants it at the hub", any(k.startswith("merging#") for k in hub._wanting), str(hub._wanting))
shell.setProperty("glassesKeepTracking", True); pump(0.2)
check("...ticked: the hub keeps tracking", hub._keep is True)
_mf = shell.property("mergeFlipFront")
shell.setProperty("mergeFlipFront", "Blended Image"); pump(0.4)
check("...another source: the box hidden, the view no longer wanting",
      kt.property("shown") is False and not any(k.startswith("merging#") for k in hub._wanting), str(hub._wanting))
shell.setProperty("mergeFlipFront", _mf); pump(0.4)
_sp = page(1)
_skt = keep_box(_sp)
_sf = shell.property("splatFlipFront"), shell.property("splatFlipBack")
shell.setProperty("splatFlipFront", "Dubois Anaglyph"); shell.setProperty("splatFlipBack", "Splat Result"); pump(0.3)
check("Keep Tracking: hidden on Splatting without the choice", _skt is not None and _skt.property("shown") is False)
shell.setProperty("splatFlipBack", GF); pump(0.3)
check("...shown with it on the back", _skt.property("shown") is True)
shell.setProperty("splatFlipFront", _sf[0]); shell.setProperty("splatFlipBack", _sf[1]); pump(0.3)
mpage = page(3)

# Full Preview in Glasses-Free 3D (user, 2026-10-11): its picture is woven instead of the page's
from PySide6.QtQuick import QQuickWindow
fps = [w for w in mpage.findChildren(QQuickWindow) if w.property("stage") == "merging"]
check("Merging: its Full Preview knows the stage and the view", len(fps) == 1 and fps[0].property("glasses") is True)
fp = fps[0]
QMetaObject.invokeMethod(fp, "openFull", Q_ARG("QVariant", win.screen())); pump(0.8)   # full screen (2026-10-13)
_fgv = find_all(fp.contentItem(), lambda c: c.objectName() == "glassesFreeView", [])[0]
QMetaObject.invokeMethod(_fgv, "update"); pump(0.4)
fitted = [c for c in fp.contentItem().childItems() if c.property("aspect") is not None]
fr = fitted[0].mapRectToScene(fitted[0].boundingRect()) if fitted else None
check("Full Preview open: the woven window moves into it", hub._shown and sr_display._same_window(hub._child.parent(), fp)
      and not gfv.property("on"))
check("...over the fitted picture", fr is not None and hub._geo is not None and abs(hub._geo.width() - fr.width()) <= 1
      and abs(hub._geo.height() - fr.height()) <= 1, f"{hub._geo} vs {fr}")
check("...Full Preview's view wants it too (tracking held across)", sum(k.startswith("merging#") for k in hub._wanting) >= 1
      and hub._keep is True, str(hub._wanting))
# its X and frame slider show over the 3D (user, 2026-10-13: the woven window hid the X): holes while shown
_x = find_all(fp.contentItem(), lambda c: c.objectName() == "fullPreviewClose", [])[0]
_bar = find_all(fp.contentItem(), lambda c: c.objectName() == "fullPreviewScrub", [])[0]
_ft = find_all(fp.contentItem(), lambda c: c.objectName() == "fullPreviewFrameText", [])[0]
QMetaObject.invokeMethod(_fgv, "update"); pump(0.3)
# over a picture filling the screen (16:9 on 16:9) both are on it: asked over the whole window,
# the holes are theirs. (Offscreen the "screen" is 800x800, the picture letterboxed, and they sit
# in the bands -- the woven window then needs no hole for them, and cuts none.)
from PySide6.QtCore import Q_RETURN_ARG
_all = QMetaObject.invokeMethod(_fgv, "holes", Q_RETURN_ARG("QVariant"), Q_ARG("QVariant", 0), Q_ARG("QVariant", 0),
                                Q_ARG("QVariant", fp.width()), Q_ARG("QVariant", fp.height()))
_rects = [_x.mapRectToScene(_x.boundingRect()), _bar.mapRectToScene(_bar.boundingRect())]
_all_list = _all.toVariant() if hasattr(_all, "toVariant") else list(_all or [])
def _has(rect):
    return any(abs(float(h.x()) - rect.x()) < 1 and abs(float(h.y()) - rect.y()) < 1 for h in _all_list)
check("...the X and the frame slider show, each a hole in the 3D where they're over it",
      _x.isVisible() and _bar.isVisible() and _has(_rects[0]) and _has(_rects[1]), f"{_all}")
_over = sum(1 for r in _rects if hub._geo is not None and r.intersects(hub._geo))
check("...and the woven window cuts exactly those", len(hub._holes or ()) == _over, f"{hub._holes} / {_over}")
check("...the slider says which frame", str(_ft.property("text")).startswith("Frame 1 / "), _ft.property("text"))
pump(2.4)
QMetaObject.invokeMethod(_fgv, "update"); pump(0.3)
check("...a moment later both gone, and the holes with them (3D up to the edges)",
      not _x.isVisible() and not _bar.isVisible() and not (hub._holes or []), f"{hub._holes}")
fp.close(); pump(0.6)
QMetaObject.invokeMethod(gfv, "update"); pump(0.4)
check("Full Preview closed: back on the page", hub._shown and sr_display._same_window(hub._child.parent(), win))
check("...the Full Preview window still there to open again", fp.isVisible() is False)
# Highlight Mask on Glasses-Free 3D: the right eye tinted, the left as it was (user, 2026-10-11)
before = offered["merging"].copy(); s0 = mpv.property("imageSerial")
mst.setProperty("mask_highlight", True)
until(lambda: mpv.property("imageSerial") > s0, 10); pump(0.6)
after = offered["merging"]
check("Merging: Highlight Mask shows on the right eye",
      not np.array_equal(after[:, 320:], before[:, 320:]) and np.array_equal(after[:, :320], before[:, :320]))
mst.setProperty("mask_highlight", False); pump(0.8)
shell.setProperty("mergeFlipFront", "Blended Image"); pump(0.3)
check("Merging: another view: stereo3d off",
      until(lambda: not mpv.property("stereo3d") and mpv.property("imageWidth") == 320, 15))
check("...and the woven window hidden", until(lambda: not hub._shown, 3))

# ---- Splatting: the same --------------------------------------------------------------
spage = page(1)
sp = objs["Splatting"]; sst = sp.property("settings"); spv = sp.preview
for k, v in {"input_source_clips": str(splat_fx / "src").replace("\\", "/"),
             "input_depth_maps": str(splat_fx / "depth").replace("\\", "/"),
             "output_splatted": str(splat_fx / "out").replace("\\", "/"),
             "multi_map": False, "preview_size": "100%", "auto_save_sidecar": False}.items():
    sst.setProperty(k, v)
shell.setProperty("splatFlipFront", GF); pump(0.3)
spv.load()
check("Splatting: preview loads", until(lambda: spv.property("loaded") and not spv.property("loading"), 90))
check("Splatting: Glasses-Free 3D shows the left eye (the clip's width)",
      until(lambda: spv.property("stereo3d") and spv.property("imageWidth") == 640, 30),
      f"{spv.property('imageWidth')} stereo3d={spv.property('stereo3d')}")
pair = offered.get("splatting")
check("Splatting: the weaver gets the full side-by-side (each eye 640 wide)",
      pair is not None and pair.shape[1] == 1280 and pair.shape[0] == spv.property("imageHeight"),
      str(None if pair is None else pair.shape))

# the overlays on Glasses-Free 3D (user, 2026-10-11): the crosshair on each eye alike (the screen plane)
h, w2 = pair.shape[:2]; w = w2 // 2
plain = pair.copy()
sst.setProperty("crosshair_white", True); sst.setProperty("crosshair_enabled", True)
until(lambda: not np.array_equal(offered["splatting"], plain), 10); pump(0.6)
cross = offered["splatting"]
check("Splatting: the crosshair on the left eye's centre", tuple(cross[h // 2, w // 2]) == (255, 255, 255),
      str(tuple(cross[h // 2, w // 2])))
check("...and on the right eye's, at the same place (the screen plane)",
      tuple(cross[h // 2, w + w // 2]) == (255, 255, 255) and np.array_equal(
          (cross[:, :w] == 255).all(axis=2) & ~(plain[:, :w] == 255).all(axis=2),
          (cross[:, w:] == 255).all(axis=2) & ~(plain[:, w:] == 255).all(axis=2)))
img = spv.provider._images.get("frame")
shown = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine())[:, :w * 3].reshape(h, w, 3)
check("...and on the page's picture (the left eye)", np.array_equal(shown, cross[:, :w]))
sst.setProperty("crosshair_enabled", False); sst.setProperty("crosshair_white", False)
# (a re-render may differ by a level here and there: the warp's own GPU noise, not an overlay)
same = lambda a, b: np.abs(a.astype(int) - b).max() <= 2
until(lambda: same(offered["splatting"], plain), 10)
check("Splatting: crosshair off: gone from both eyes", same(offered["splatting"], plain),
      f"max difference {np.abs(offered['splatting'].astype(int) - plain).max()}")
# Highlight Holes: on the right eye, as on Splat Result (a strong Disparity, so there are holes)
s0 = spv.property("imageSerial")
sst.setProperty("max_disp", 60.0)
until(lambda: spv.property("imageSerial") > s0, 15); pump(1.0)
plain = offered["splatting"].copy()
s0 = spv.property("imageSerial")
sst.setProperty("mask_highlight", True)
until(lambda: spv.property("imageSerial") > s0, 15); pump(0.8)
hl = offered["splatting"]
check("Splatting: Highlight Holes shows on the right eye",
      not same(hl[:, w:], plain[:, w:]) and same(hl[:, :w], plain[:, :w]),
      f"right eye max diff {np.abs(hl[:, w:].astype(int) - plain[:, w:]).max()}, left {np.abs(hl[:, :w].astype(int) - plain[:, :w]).max()}")
sst.setProperty("mask_highlight", False); pump(0.8)

# Beginner Mode keeps the crosshair (user, 2026-10-11)
objs["Beginner"].setEnabled(True)
pump(0.5)
cb = [c for c in find_all(spage, lambda c: c.property("key") == "crosshair_enabled", []) if c.isVisible()]
check("Beginner Mode: Crosshair still offered", objs["Beginner"].property("on") is True and len(cb) == 1,
      f"on={objs['Beginner'].property('on')} shown={len(cb)}")
objs["Beginner"].setEnabled(False); pump(0.3)

shell.setProperty("splatFlipFront", "Dubois Anaglyph"); pump(0.3)
check("Splatting: another view: stereo3d off", until(lambda: not spv.property("stereo3d"), 30))

studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
