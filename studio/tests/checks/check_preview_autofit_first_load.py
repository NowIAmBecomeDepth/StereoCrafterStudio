"""The first preview after a fresh start fits the window (user, 2026-09-27):
on a fresh engine the first frame lands before the load has finished, and the
auto-fit's one try then found "not loaded" and gave up (the check delays
the load's end to make that race certain) -- the preview stayed
at the saved scale (100%) until a double-click. Offscreen, a fresh app, a
synthetic 1920x1080 clip (made here with ffmpeg, never the user's clips),
the saved Preview Scale 100%: loading the clip from the list must refit it
without any other input. Configs and last_jobs.json redirected."""
import os, sys, tempfile, hashlib, time, json, subprocess
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, str(Path(__file__).parent))
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
tmp = Path(tempfile.mkdtemp())
FX = tmp / "fx"
(FX / "src").mkdir(parents=True); (FX / "depth").mkdir()
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=24",
                "-frames:v", "8", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(FX / "src" / "big.mp4")], check=True)
subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "gradients=size=1920x1080:rate=24,format=gray",
                "-frames:v", "8", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(FX / "depth" / "big_depth.mp4")], check=True)
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
ss.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
(ss.CONFIG_DIR / "splatting.json").write_text(json.dumps({
    "input_source_clips": str(FX / "src").replace("\\", "/"), "input_depth_maps": str(FX / "depth").replace("\\", "/"),
    "output_splatted": str(tmp / "out").replace("\\", "/"), "preview_size": "100%", "multi_map": False}))
import _fixtures
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, Q_RETURN_ARG, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from studio import app as A

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
        QCoreApplication.processEvents(QEventLoop.AllEvents, 20); time.sleep(0.005)
def walk(it, out):
    for c in it.childItems(): out.append(c); walk(c, out)
    return out

stage = [c for c in walk(win.contentItem(), []) if c.objectName() == "stage"][0]
# the window settled first: no resize after the load to refit it by the way
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 1), Q_ARG("QVariant", True)); pump(4)
sp = objs["Splatting"]; pv = sp.preview; st = sp.property("settings")
page = stage.property("pages").property(1).toQObject()
fit = [c for c in walk(page, []) if c.objectName() == "previewAutoFit"][0]
jc = pv.property("jobClips")
t0 = time.time()
while not jc.property("clipPaths") and time.time() - t0 < 15: pump(0.1)
check("the saved Preview Scale is 100%, the list has the clip", st.value("preview_size") == "100%"
      and len(jc.property("clipPaths")) == 1, f"{st.value('preview_size')} {list(jc.property('clips'))}")
# the race, made certain: the load's "done" lands well after its first frame
# (a fresh engine with the user's settings takes that long; here it would not)
from PySide6.QtCore import QTimer
_orig_done = pv._on_done
pv._sig_done.disconnect(pv._on_done)
pv._sig_done.connect(lambda r: QTimer.singleShot(600, lambda: _orig_done(r)))
pv.showClip(jc.property("clipPaths")[0])            # a click on the row, as the user does
t0 = time.time()
while (not pv.property("loaded") or pv.property("loading")) and time.time() - t0 < 90: pump(0.1)
pump(1.5)
want = QMetaObject.invokeMethod(fit, "percent", Q_RETURN_ARG("QVariant"))
check("first load on a fresh start: the preview is fitted, not left at 100%",
      pv.property("loaded") and 10 <= int(want) < 100 and st.value("preview_size") == f"{int(want)}%",
      f"preview_size {st.value('preview_size')}, fit {want}, image {pv.property('imageWidth')}x{pv.property('imageHeight')}")

pv.shutdown()
studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
