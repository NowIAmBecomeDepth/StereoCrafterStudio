"""A flipped clip's Merging preview views match what the batch writes (bug sweep
2026-10-09, A8: the blend runs in flipped space and the Side-by-Side, Mesh Warp and
Wigglegram views returned before the flip-back -- both eyes mirrored and swapped).
Real app, offscreen, over the synthetic ``merge`` fixture with mA's inpainted file
named ``..._right_eyeF.mp4`` (the "flipped" mark) -- never the user's clips. Configs
and last_jobs.json redirected.

The batch writes flip([L, R]) = [flip(R), flip(L)]: its left half is the flipped-back
blend, which is what Blended Image shows. So:
  * Side-by-Side's left half is the Blended Image view
  * the Wigglegram's left eye is the Blended Image view"""
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
import numpy as np
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG
from PySide6.QtGui import QGuiApplication, QImage
from studio import app as A

root = _fixtures.copy("merge", tmp / "fx")
inp = root / "inpainted"
(inp / "mA_320_inpainted_right_eye.mp4").rename(inp / "mA_320_inpainted_right_eyeF.mp4")
(inp / "mB_320_inpainted_right_eye.mp4").unlink()          # one clip: mA, flipped

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
def until(cond, sec=30):
    end = time.time() + sec
    while not cond() and time.time() < end:
        pump(0.05)
    return cond()
def find(it, pred):
    if pred(it): return it
    for c in it.childItems():
        r = find(c, pred)
        if r is not None: return r
    return None
def arr(img):
    """The image's pixels as an (h, w, 3) uint8 array."""
    img = img.convertToFormat(QImage.Format_RGB888)
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    return np.frombuffer(bytes(img.constBits()), np.uint8).reshape(h, bpl)[:, : w * 3].reshape(h, w, 3).copy()

pump(0.6)
mg = objs["Merging"]
st = mg.property("settings")
pv = mg.property("previewer")
stage = find(win.contentItem(), lambda c: c.objectName() == "stage")
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 3), Q_ARG("QVariant", True)); pump(1.2)
for k, v in {"inpainted_folder": str(inp), "original_folder": str(root / "orig"),
             "mask_folder": str(root / "mask"), "output_folder": str(tmp / "merged"),
             "encoding_encoder": "Force CPU", "codec": "H.264", "use_gpu": False,
             "preview_source": "Blended Image", "preview_size": "100%"}.items():
    st.setProperty(k, v)
pump(0.3)
pv.load()
check("the flipped clip loads", until(lambda: pv.property("loaded") and not pv.property("loading"), 60)
      and mg.property("clipFlip") == "Yes", str(mg.property("clipFlip")))
until(lambda: pv.provider._images.get("frame") is not None and pv.property("imageWidth") == 320, 20); pump(1.0)
blend = arr(pv.provider._images["frame"])

s0 = pv.property("imageSerial")
st.setProperty("preview_source", "Side-by-Side")
until(lambda: pv.property("imageWidth") == 640 and pv.property("imageSerial") > s0, 20); pump(0.8)
sbs = arr(pv.provider._images["frame"])
diff = int(np.abs(sbs[:, :320].astype(int) - blend.astype(int)).max())
check("Side-by-Side's left half is the flipped-back blend, as the batch writes", sbs.shape[1] == 640 and diff <= 2,
      f"max diff {diff}")

st.setProperty("preview_source", "Wigglegram")
until(lambda: pv.property("wiggling") and pv.provider._images.get("left") is not None, 20); pump(0.5)
left = arr(pv.provider._images["left"])
diff = int(np.abs(left.astype(int) - blend.astype(int)).max()) if left.shape == blend.shape else 999
check("the Wigglegram's left eye is the flipped-back blend", diff <= 2, f"max diff {diff}")

st.setProperty("preview_source", "Blended Image"); pump(0.5)
mg.shutdown()
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
