"""The Merging preview in the real app, offscreen, over the synthetic ``merge``
fixture (never the user's clips): a click on the empty preview renders the first
clip and fills Current Clip Info; a mask slider re-renders a different
picture; Preview Source views (SBS doubles the width, Wigglegram flips two
eyes); Next goes to the quad clip; the scrubber and arrow keys seek; playback
advances; Start gives the preview back before the batch and reloads it
after; the page still fits the 1080p screen at its 0.85 UI scale; nothing is
written into the clip folders. Configs and last_jobs.json redirected."""
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
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, QPoint, QPointF, Qt, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from studio import app as A

root = _fixtures.copy("merge", tmp / "fx")
before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))

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
    # Not QTest.qWait: it holds the GIL and starves the preview's worker.
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
def click_item(item):
    c = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(c.x()), int(c.y())))

pump(0.6)
mg = objs["Merging"]
st = mg.property("settings")
pv = mg.property("previewer")
stage = find(win.contentItem(), lambda c: c.objectName() == "stage")
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 3), Q_ARG("QVariant", True)); pump(1.2)
page = stage.property("pages").property(3).toQObject()
for k, v in {"inpainted_folder": str(root / "inpainted"), "original_folder": str(root / "orig"),
             "mask_folder": str(root / "mask"), "output_folder": str(tmp / "merged"),
             "encoding_encoder": "Force CPU", "codec": "H.264", "use_gpu": False,
             "preview_source": "Blended Image", "preview_size": "100%"}.items():
    st.setProperty(k, v)
pump(0.3)

# ---- fit: the window's least height at 1.0, and what 0.85 makes of it
min_h = win.property("minimumHeight")
check("fits the 1080p screen at 0.85", min_h * 0.85 <= 1000, f"minimumHeight {min_h} -> {min_h * 0.85:.0f} at 0.85")
pref = page.property("preferredHeight") - page.property("requiredHeight")
check("the fit asks for a bigger picture when there is room",
      abs(pref - (page.property("preferredPictureHeight") - page.property("minPictureHeight"))) < 1 and pref > 0, str(pref))

def frame_bytes():
    img = pv.provider._images.get("frame")
    return None if img is None else (img.width(), img.height(), bytes(img.constBits()))

# ---- Load Preview by mouse
# no Load Preview button any more: the empty preview's hint loads the first clip
click_item(find(page, lambda c: c.objectName() == "previewHint"))
check("Load Preview loads", until(lambda: pv.property("loaded") and not pv.property("loading")))
check("two clips, the first shown", pv.property("videoCount") == 2 and pv.property("videoIndex") == 0,
      f"{pv.property('videoIndex')}/{pv.property('videoCount')}")
check("48 frames", pv.property("frameCount") == 48, str(pv.property("frameCount")))
check("a 320x180 blend on screen", (pv.property("imageWidth"), pv.property("imageHeight")) == (320, 180))
img_item = find(page, lambda c: c.objectName() == "previewImage")
check("QML shows it", until(lambda: img_item is not None and img_item.isVisible()
                            and img_item.property("implicitWidth") == 320, 5),
      str(img_item and img_item.property("implicitWidth")))
check("Current Clip Info follows the preview",
      mg.property("clipName") == "mA_320_inpainted_right_eye.mp4" and mg.property("clipResolution") == "320x180"
      and mg.property("clipFrames") == "48" and mg.property("clipFlip") == "No",
      f"{mg.property('clipName')} {mg.property('clipResolution')} {mg.property('clipFrames')}")

# ---- a mask slider re-renders, and differently
a = frame_bytes(); s0 = pv.property("imageSerial")
st.setProperty("mask_dilate_kernel_size", 41.0)
check("Dilate re-renders", until(lambda: pv.property("imageSerial") > s0, 10))
pump(0.3)
check("...a different blend", frame_bytes() != a)
st.setProperty("mask_dilate_kernel_size", 3.0); pump(0.8)
s0 = pv.property("imageSerial")
st.setProperty("output_crf", "20"); pump(0.8)
check("an encoding field does not re-render", pv.property("imageSerial") == s0)

# ---- Preview Source views
st.setProperty("preview_source", "Side-by-Side")
check("Side-by-Side is two eyes wide", until(lambda: pv.property("imageWidth") == 640, 10), str(pv.property("imageWidth")))
st.setProperty("preview_source", "Wigglegram")
check("Wigglegram flips two eyes", until(lambda: pv.property("wiggling"), 10)
      and pv.provider._images.get("left") is not None and pv.provider._images.get("right") is not None)
st.setProperty("preview_source", "Blended Image")
until(lambda: not pv.property("wiggling") and pv.property("imageWidth") == 320, 10); pump(0.8)
blend_img = frame_bytes()                          # the blend, on this frame
st.setProperty("preview_source", "Processed Mask")
check("Processed Mask shows", until(lambda: frame_bytes() != blend_img and pv.property("imageWidth") == 320, 10)
      or pv.property("imageWidth") == 320)
pump(0.5)
# a view of its own, not the blend again (found with bug sweep 2026-10-09, A8: the
# second view chain's else set the blend back over Inpainted / Original / Warped / Mask)
check("...and it is the mask, not the blend", frame_bytes() != blend_img)
st.setProperty("preview_source", "Blended Image"); pump(0.8)

# ---- Highlight Mask redraws the frame on screen (bug sweep 2026-10-09, A8: the
# preview's cache ignored it -- and the view chain threw the tint away)
pv.seek(5); until(lambda: pv.property("frame") == 5, 10); pump(0.8)
pv.seek(6); until(lambda: pv.property("frame") == 6, 10); pump(0.8)
pv.seek(5); until(lambda: pv.property("frame") == 5, 10); pump(0.8)    # frame 5 now cached
a_hl = frame_bytes(); s0 = pv.property("imageSerial")
st.setProperty("mask_highlight", True)
until(lambda: pv.property("imageSerial") > s0, 10); pump(0.5)
check("Highlight Mask changes the frame on screen (a cached one)", frame_bytes() != a_hl)
st.setProperty("mask_highlight", False); pump(0.8)
check("...and unticked, it is back as it was", frame_bytes() == a_hl)

# ---- Save Preview as SBS (bug sweep 2026-10-09, A8): the eyes of the frame on screen,
# not of the last full render -- here frame 30 blended, then frame 10 in Side-by-Side
import cv2, numpy as np
pv.seek(30); until(lambda: pv.property("frame") == 30, 10); pump(0.8)
st.setProperty("preview_source", "Side-by-Side"); pump(0.5)
pv.seek(10); until(lambda: pv.property("frame") == 10, 10); pump(0.8)
shot = tmp / "sbs_frame10.png"
pv.saveTo(True, str(shot))
until(lambda: shot.exists(), 15); pump(0.3)
def orig_frame(n):
    cap = cv2.VideoCapture(str(root / "orig" / "mA.mp4")); cap.set(cv2.CAP_PROP_POS_FRAMES, n)
    ok, f = cap.read(); cap.release()
    return f.astype(np.float32)
saved = cv2.imread(str(shot)).astype(np.float32) if shot.exists() else None
if saved is not None:
    left = saved[:, : saved.shape[1] // 2]
    d10, d30 = (float(np.abs(left - orig_frame(n)).mean()) for n in (10, 30))
    check("Save Preview as SBS: the left eye is frame 10's, the one on screen", d10 < d30,
          f"diff to frame 10 {d10:.1f}, to frame 30 {d30:.1f}")
else:
    check("Save Preview as SBS wrote a file", False, str(shot))
check("...and the view is still Side-by-Side", st.property("preview_source") == "Side-by-Side",
      str(st.property("preview_source")))
st.setProperty("preview_source", "Blended Image"); pump(0.8)

# ---- scrubber / keys
pv.seek(20)
check("seek to frame 21", until(lambda: pv.property("frame") == 20, 10))
win.requestActivate(); pump(0.3)
s0 = pv.property("imageSerial")
QTest.keyClick(win, Qt.Key_Right); pump(0.8)
check("Right arrow steps one frame", pv.property("frame") == 21 and pv.property("imageSerial") > s0,
      str(pv.property("frame")))

# ---- playback
pv.togglePlay()
f0 = pv.property("frame")
until(lambda: pv.property("frame") >= f0 + 5, 10)
pv.pause(); pump(0.3)
check("playback advances", pv.property("frame") >= f0 + 5 and not pv.property("playing"), str(pv.property("frame")))

# ---- Next: the quad clip, whose Depth Map view has something to show
click_item(find(page, lambda c: c.property("text") == "Next ›"))
check("Next loads the quad clip", until(lambda: pv.property("videoIndex") == 1 and not pv.property("loading"), 20)
      and mg.property("clipName") == "mB_320_inpainted_right_eye.mp4", mg.property("clipName"))
# bug sweep 2026-10-09 (A8): it opened at the previous clip's frame (here ~26)
check("...at its first frame, not the previous clip's", pv.property("frame") == 0, str(pv.property("frame")))
st.setProperty("preview_source", "Depth Map"); pump(1.0)
check("quad: Depth Map renders", pv.property("imageWidth") == 320)
st.setProperty("preview_source", "Blended Image"); pump(0.8)

# The preview itself must add nothing beside the clips. (Taken before the
# batch: with the grain auto-match on, v1's batch writes each clip's sidecar.)
after = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
check("the preview wrote nothing into the clip folders", before == after, str(sorted(set(after) - set(before)))[:300])

# ---- Start gives the preview back, the batch runs, the preview comes back
mg.start()
check("Start released the preview", until(lambda: not pv.property("loaded"), 20))
check("...and the batch ran", until(lambda: not mg.property("busy") and not mg.property("loading")
                                    and (tmp / "merged").exists() and any((tmp / "merged").iterdir()), 180))
check("the preview reloads after", until(lambda: pv.property("loaded") and not pv.property("loading"), 30))

# ---- Clear Preview
pv.clear()
check("Clear Preview unloads", until(lambda: not pv.property("loaded"), 10) and pv.property("imageWidth") == 0)

mg.shutdown()
studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
