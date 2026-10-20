"""Merging's preview flip card and the dial layout, in the real app,
offscreen, over the synthetic ``merge`` fixture: a click on the picture turns
it over (the Rotation passes edge-on), swaps Preview Source between the last
anaglyph and Blended Image at the halfway point, waits for the new frame and
lands flat; a second click comes back; any two sources; no flip while
unloaded. The layout: wide (1920), the settings in rails beside the preview
(nothing under it, nothing overlapping, Options in two columns, Clip Info in
the left rail); compact, the preview on top with the columns under it. The mask dials: three in Mask
Processing, five in the Shadow fold (a chevron, closed by default and
remembered), and a dial drives its setting. Configs and last_jobs.json
redirected."""
import os, sys, tempfile, hashlib, time, json
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
root = _fixtures.copy("merge", tmp / "fx")
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, QObject, QPoint, QPointF, Q_ARG, Qt, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from studio import app as A

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
        pump(0.02)
    return cond()
def walk(it, out):
    for c in it.childItems():
        out.append(c); walk(c, out)
    return out

pump(0.8)
mg = objs["Merging"]; st = mg.property("settings"); pv = mg.property("previewer"); shell = objs["Shell"]
stage = [c for c in walk(win.contentItem(), []) if c.objectName() == "stage"][0]
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 3), Q_ARG("QVariant", True)); pump(1.2)
page = stage.property("pages").property(3).toQObject()
items = walk(page, [])
pic = [c for c in items if c.objectName() == "previewPicture"][0]
card = page.findChild(QObject, "flipCard")
def click_picture():
    QTest.mouseMove(win, QPoint(5, 5)); pump(0.1)
    c = pic.mapToScene(QPointF(pic.width() / 2, pic.height() / 2)).toPoint()
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, c)
    # one click flips once the double-click time is out (a double-click zooms)
    delay = page.findChild(QObject, "flipDelay")
    t1 = time.time()
    while delay is not None and delay.property("running") and time.time() - t1 < 2:
        pump(0.005)

# ---- the layout: rails beside the preview when wide, under it when compact
from PySide6.QtCore import QRectF
def rect(it):
    p = it.mapToScene(QPointF(0, 0)); return QRectF(p.x(), p.y(), it.width(), it.height())
def by_name(n):
    return [c for c in walk(page, []) if c.objectName() == n][0]
win.setWidth(1920); pump(1.0)
body, prev, lr, rr, opts = (by_name(n) for n in ("mergeBody", "previewPanel", "mergeLeftRail", "mergeRightRail", "mergeOptions"))
check("wide at 1920", page.property("wide") is True, f"wideWidth {page.property('wideWidth'):.0f}")
R = {n: rect(i) for n, i in (("body", body), ("prev", prev), ("left", lr), ("right", rr))}
check("wide: left rail | preview | right rail, side by side", R["left"].right() < R["prev"].left() and R["prev"].right() < R["right"].left()
      and abs(R["left"].top() - R["prev"].top()) < 1 and abs(R["right"].top() - R["prev"].top()) < 1, str(R))
check("wide: nothing under the preview (it runs the body's full height)", abs(prev.height() - body.height()) < 1
      and R["left"].bottom() <= R["body"].bottom() + 1 and R["right"].bottom() <= R["body"].bottom() + 1)
grid = [c for c in walk(opts, []) if c.metaObject().className() == "QQuickGridLayout"][0]
check("wide: Options in two columns", grid.property("columns") == 2)
ci = [c for c in walk(lr, []) if c.metaObject().className().startswith(("MergeClipInfo", "ClipInfo")) and c.isVisible()]
check("wide: Current Clip Info in the left rail", len(ci) == 1)
win.setWidth(int(page.property("compactWidth"))); pump(1.0)
R = {n: rect(i) for n, i in (("prev", prev), ("left", lr), ("right", rr))}
check("compact: the preview on top, the columns under it", page.property("wide") is False
      and R["left"].top() > R["prev"].bottom() and R["right"].top() > R["prev"].bottom()
      and R["left"].right() < R["right"].left(), str(R))
check("compact: Options in one column", grid.property("columns") == 1)
win.setWidth(1920); pump(0.8)

# ---- the dials
dials = [c for c in items if c.metaObject().className().startswith(("DialField", "MaskDial", "MDial"))]
fold = [c for c in items if c.objectName() == "shadowFold"][0]
in_fold = [d for d in dials if fold.isAncestorOf(d)]
# Put Back's own six dials (Convergence ... Opacity, 2026-10-16) aren't mask dials
_putback = {"conv", "disp", "parallax", "grow", "blur", "opacity"}
mask_dials = [d for d in dials if d.property("key") not in _putback]
check("eight mask dials: three in Mask Processing, five in the Shadow fold", len(mask_dials) == 8 and len(in_fold) == 5,
      f"{len(mask_dials)} / {len(in_fold)}")
check("the Shadow fold starts closed, with a chevron (no checkbox)", fold.property("open") is False
      and fold.property("chevron") is True and any(c.objectName() == "foldChevron" and c.isVisible() for c in walk(fold, [])))
QTest.mouseMove(win, QPoint(5, 5)); pump(0.1)
hc = fold.mapToScene(QPointF(fold.width() / 2, 20)).toPoint()
QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, hc); pump(0.6)
check("clicking the fold's heading opens it, and it is remembered", fold.property("open") is True
      and json.loads((tmp / "config" / "studio.json").read_text()).get("merge_shadow_open") is True)
blur = [d for d in dials if d.property("key") == "mask_blur_kernel_size"][0]
before = st.property("mask_blur_kernel_size")
QMetaObject.invokeMethod(blur, "write", Q_ARG("QVariant", 21))
check("a dial writes its setting", st.property("mask_blur_kernel_size") == 21, f"{before} -> {st.property('mask_blur_kernel_size')}")

# ---- the two sides: Front / Back dropdowns (Shell), the facing one is preview_source
check("defaults: Dubois Anaglyph in front, Blended Image behind, front facing",
      shell.property("mergeFlipFront") == "Dubois Anaglyph" and shell.property("mergeFlipBack") == "Blended Image"
      and card.property("backUp") is False and st.property("preview_source") == "Dubois Anaglyph",
      f'{shell.property("mergeFlipFront")} / {shell.property("mergeFlipBack")} backUp={card.property("backUp")} src={st.property("preview_source")}')
check("the Front / Back dropdowns replace Preview Source", page.findChild(QObject, "flipFront") is not None
      and page.findChild(QObject, "flipBack") is not None)
shell.setProperty("mergeFlipFront", "Optimized Anaglyph"); pump(0.2)
check("changing the facing side's source shows it at once", st.property("preview_source") == "Optimized Anaglyph")
shell.setProperty("mergeFlipBack", "Processed Mask"); pump(0.2)
check("changing the hidden side leaves the view alone", st.property("preview_source") == "Optimized Anaglyph")
saved = json.loads((tmp / "config" / "studio.json").read_text())
check("both sides are saved", saved.get("merge_flip_front") == "Optimized Anaglyph" and saved.get("merge_flip_back") == "Processed Mask")
st.setProperty("preview_source", "Side-by-Side"); pump(0.2)
check("a source set elsewhere lands on the facing side", shell.property("mergeFlipFront") == "Side-by-Side" and card.property("backUp") is False)
shell.setProperty("mergeFlipFront", "Dubois Anaglyph"); shell.setProperty("mergeFlipBack", "Blended Image"); pump(0.2)

# ---- no flip before a load
click_picture(); pump(0.6)
check("no flip while nothing is loaded", card.property("busy") is False and st.property("preview_source") == "Dubois Anaglyph")
# that click was Load Preview on the first-run folders (none of them there yet, 2026-10-17): its
# error is a prompt, closed here -- an open prompt holds every later click on the picture
_prompt = win.findChild(QObject, "prompt")
if _prompt is not None and _prompt.property("opened"):
    QMetaObject.invokeMethod(_prompt, "finish", Q_ARG("QVariant", 1)); pump(0.4)

# ---- load, then flip
for k, v in {"inpainted_folder": str(root / "inpainted"), "original_folder": str(root / "orig"),
             "mask_folder": str(root / "mask"), "output_folder": str(tmp / "out"), "preview_size": "100%"}.items():
    st.setProperty(k, v)
pump(0.3); pv.load()
# The worker idle too, not just "loaded": the "no flip" click above lands on the
# empty preview, whose tap is Load Preview -- that load runs first, and a click
# made before this one (the check's own) has finished hit the loading overlay.
check("preview loads", until(lambda: pv.property("loaded") and not pv.property("loading")
                             and pv._worker_idle(), 60))
pump(0.5)
angles = []
t = __import__("threading")
serial0 = pv.property("imageSerial")
click_picture()
t0 = time.time()
print("   busy after click:", card.property("busy"), "angle", card.property("angle"), flush=True)
while time.time() - t0 < 3 and (card.property("busy") or not angles):
    angles.append(card.property("angle")); pump(0.01)
check("the click turns the card: it passes edge-on and lands flat", max(angles) >= 85 and min(angles) <= -40
      and abs(card.property("angle")) < 1e-6, f"max {max(angles):.0f} min {min(angles):.0f} end {card.property('angle')}")
check("...onto Blended Image", st.property("preview_source") == "Blended Image", st.property("preview_source"))
check("...with the new frame on it", pv.property("imageSerial") != serial0)
check("...the back is up", card.property("backUp") is True)
# a drag still pans a picture bigger than the view (the Flickable), and does not flip
flick = [c for c in walk(pic, []) if c.metaObject().className() == "QQuickFlickable"][0]
st.setProperty("preview_size", "500%")                  # a picture bigger than the view
until(lambda: flick.property("interactive"), 10); pump(0.5)
if flick.property("interactive"):
    y0 = flick.property("contentY"); src0 = st.property("preview_source")
    c0 = pic.mapToScene(QPointF(pic.width() / 2, pic.height() / 2)).toPoint()
    QTest.mousePress(win, Qt.LeftButton, Qt.NoModifier, c0)
    for k in range(1, 9):
        QTest.mouseMove(win, QPoint(c0.x(), c0.y() - 6 * k)); pump(0.02)
    QTest.mouseRelease(win, Qt.LeftButton, Qt.NoModifier, QPoint(c0.x(), c0.y() - 48)); pump(0.5)
    check("a drag pans the picture and does not flip", flick.property("contentY") != y0
          and st.property("preview_source") == src0 and not card.property("busy"), f"contentY {y0} -> {flick.property('contentY')}")
else:
    check("a drag pans the picture and does not flip", False, "the picture never outgrew the view")
st.setProperty("preview_size", "100%"); until(lambda: not card.property("busy"), 5); pump(0.5)
click_picture(); until(lambda: not card.property("busy"), 5); pump(0.1)
check("a second click comes back to the front", st.property("preview_source") == "Dubois Anaglyph"
      and card.property("backUp") is False, st.property("preview_source"))
shell.setProperty("mergeFlipBack", "Processed Mask"); pump(0.2)
click_picture(); until(lambda: not card.property("busy"), 5); pump(0.1)
check("any two sources: the back now shows the Processed Mask", st.property("preview_source") == "Processed Mask")

for c in ("DepthCrafter", "Splatting", "Inpainting", "InpaintingV2", "Merging", "SceneCut", "DepthEdit"):
    objs[c].shutdown()
studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
