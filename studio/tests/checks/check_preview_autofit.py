"""The live preview's auto-fit (v1 core/ui/preview_autofit.py) on Merging and
Splatting, in the real app, offscreen, over the synthetic ``merge`` and
``small`` clips: once a preview loads, Preview Scale is the largest 1% step
at which the whole frame fits the picture area (10..100%); a smaller window
refits it smaller, a larger one back up; a scale picked by hand stands until
the next resize. Splatting's Fit (user, 2026-10-03) goes past 100% (to
250%), and a percentage picked in its Preview Scale box stands through
resizes until Fit is picked again. Configs and last_jobs.json redirected."""
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
MFX = _fixtures.copy("merge", tmp / "mfx")
SFX = _fixtures.copy("small", tmp / "sfx")
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
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
def until(cond, sec=60):
    end = time.time() + sec
    while not cond() and time.time() < end:
        pump(0.05)
    return cond()
def walk(it, out):
    for c in it.childItems():
        out.append(c); walk(c, out)
    return out
stage = [c for c in walk(win.contentItem(), []) if c.objectName() == "stage"][0]

def pct(st):
    return float(str(st.property("preview_size")).rstrip("%"))

def fits_best(name, page, pv, st):
    area = [c for c in walk(page, []) if c.objectName() == "previewPicture" or c.objectName() == "splatPicture"]
    fit = [c for c in walk(page, []) if c.objectName() == "previewAutoFit"][0]
    area = fit.property("area")
    px = fit.property("pxToItem")
    w, h = pv.property("imageWidth") * px, pv.property("imageHeight") * px
    p = pct(st) / 100
    aw, ah = area.width() - 8, area.height() - 8
    fits = w * p <= aw + 0.5 and h * p <= ah + 0.5
    bigger_fits = w * (p + 0.01) <= aw and h * (p + 0.01) <= ah       # 1% steps now
    cap = fit.property("maxPercent") / 100
    return fits and (p >= cap or not bigger_fits), f"{pct(st):.0f}% of {w:.0f}x{h:.0f} in {aw:.0f}x{ah:.0f} (cap {cap:.0%})"

def run(name, index, ctl, folders, width=None):
    QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", index), Q_ARG("QVariant", True)); pump(1.0)
    page = stage.property("pages").property(index).toQObject()
    st = ctl.property("settings"); pv = ctl.property("previewer") or ctl.preview
    for k, v in folders.items():
        st.setProperty(k, v)
    st.setProperty("preview_size", "37%"); pump(0.3)
    # Merging: its compact shape (the picture grows with the window's height);
    # Splatting: its wide one (compact, its preview is a fixed 420 tall).
    win.setWidth(int(width or page.property("compactWidth"))); pump(0.5)
    win.setHeight(int(win.property("minimumHeight"))); pump(1.0)
    pv.load()
    check(f"{name}: preview loads", until(lambda: pv.property("loaded") and not pv.property("loading")))
    pump(1.0)
    ok, extra = fits_best(name, page, pv, st)
    check(f"{name}: loaded -> the largest scale that fits", ok and pct(st) != 37, extra)
    small = pct(st)
    win.setHeight(int(win.property("minimumHeight")) + 400); pump(1.2)
    ok, extra = fits_best(name, page, pv, st)
    check(f"{name}: a taller window refits it larger", ok and pct(st) > small, extra)
    st.setProperty("preview_size", "20%"); pump(1.0)
    check(f"{name}: a scale picked by hand stands", pct(st) == 20)
    win.setHeight(int(win.property("minimumHeight"))); pump(1.2)
    ok, extra = fits_best(name, page, pv, st)
    check(f"{name}: ...until the next resize", ok and pct(st) == small, extra)

run("Merging", 3, objs["Merging"], {"inpainted_folder": str(MFX / "inpainted"), "original_folder": str(MFX / "orig"),
                                    "mask_folder": str(MFX / "mask"), "output_folder": str(tmp / "mout")})
sp = objs["Splatting"]
for k, v in {"input_source_clips": str(SFX / "src"), "input_depth_maps": str(SFX / "depth"), "output_splatted": str(tmp / "sout")}.items():
    sp._set(k, v)
run("Splatting", 1, sp, {}, width=2000)

# ---- Splatting's Fit (user, 2026-10-03)
from PySide6.QtCore import QMetaObject as _QMO, Q_ARG as _QA
spage = stage.property("pages").property(1).toQObject()
sst = sp.property("settings"); spv = sp.property("previewer") or sp.preview
box = [c for c in walk(spage, []) if c.objectName() == "previewScaleBox"][0]
fitter = [c for c in walk(spage, []) if c.objectName() == "previewAutoFit"][0]
check("Splatting: Preview Scale offers Fit first, and shows it", box.property("model")[0] == "Fit"
      and box.property("displayText") == "Fit", f"{box.property('model')[:3]} {box.property('displayText')!r}")
win.setHeight(int(win.property("minimumHeight")) + 900); pump(1.2)
ok, extra = fits_best("Splatting", spage, spv, sst)
check("Splatting: Fit goes past 100% when the room allows", ok and pct(sst) > 100, extra)
fitted = pct(sst)
i50 = list(box.property("model")).index("50%")
_QMO.invokeMethod(box, "activated", _QA(int, i50)); pump(0.5)
check("Splatting: a percentage picked in the box shows and applies", pct(sst) == 50 and box.property("displayText") == "50%",
      f"{pct(sst)} {box.property('displayText')!r}")
win.setHeight(int(win.property("minimumHeight")) + 500); pump(1.2)
check("Splatting: ...and stands through a resize", pct(sst) == 50, str(pct(sst)))
win.setHeight(int(win.property("minimumHeight")) + 900); pump(0.6)
_QMO.invokeMethod(box, "activated", _QA(int, 0)); pump(0.8)
ok, extra = fits_best("Splatting", spage, spv, sst)
check("Splatting: picking Fit again fits it at once", ok and pct(sst) == fitted and box.property("displayText") == "Fit", extra)

for c in ("DepthCrafter", "Splatting", "Inpainting", "InpaintingV2", "Merging", "SceneCut", "DepthEdit"):
    objs[c].shutdown()
studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
