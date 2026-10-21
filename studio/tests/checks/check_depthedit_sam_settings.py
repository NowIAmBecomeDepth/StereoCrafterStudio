"""SAM 3 set up -> its page section goes, its settings are in Settings
(user, 2026-09-28): with the packages there and the weights downloaded, the
Depth Map Editor's "SAM 3" section is hidden; Settings shows the Extra stage's
SAM 3 rows (Keep Video Frames in System RAM, Unload SAM 3 Model, Uninstall
SAM 3...); Uninstall sends the checkpoints to the Recycle Bin and the page
section comes back. Offscreen. SAM 3 is pointed at a TEMP weights folder with
dummy checkpoints (the user's weights/SAM3 is never touched) and the Recycle
Bin move is replaced by a plain delete of those dummies (nothing lands in the
user's bin). Configs and last_jobs.json redirected."""
import os, sys, tempfile, hashlib, time
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, str(Path(__file__).parent))
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
tmp = Path(tempfile.mkdtemp())
# the whole models folder is the temp one, and only it is looked in (core.weights_paths): set before
# anything loads. 2026-10-20: with only sam.WEIGHTS_DIR pointed at temp, the app's start put it back on
# the real weights/SAM3 (the models-folder listener) and Uninstall below deleted the user's checkpoints.
os.environ["STEREOCRAFTER_WEIGHTS"] = str(tmp / "weights")
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
import _fixtures

# ---- SAM 3 "set up", in temp: dummy checkpoints big enough to count
from core.depthedit import sam3_backend as sam
import core.common.recycle_bin as rb
sam.WEIGHTS_DIR = tmp / "weights" / "SAM3"; sam.WEIGHTS_DIR.mkdir(parents=True)
real_weights = REPO / "weights" / "SAM3"
before_real = sorted(p.name for p in real_weights.glob("*")) if real_weights.is_dir() else []
ckpt = sam.checkpoint_path(sam.DEFAULT_VERSION)
with open(ckpt, "wb") as f:
    f.truncate(120_000_000)                      # sparse: > the 100 MB "present" floor, no real write
import studio.stages.depthedit.engine as _E
_E._REPO_ROOT = tmp                              # it shows the weights path relative to the repo
sam.missing_packages = lambda: []
sam.cuda_available = lambda: True
binned = []
def _fake_bin(path):
    binned.append(os.path.basename(path)); os.remove(path); return not os.path.exists(path)
rb.send_to_recycle_bin = _fake_bin

from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, QObject, qInstallMessageHandler
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
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.005)
def until(cond, sec=30):
    end = time.time() + sec
    while not cond() and time.time() < end:
        pump(0.05)
    return cond()
def walk(it, out):
    for c in it.childItems(): out.append(c); walk(c, out)
    return out
def find(it, pred):
    return next((c for c in walk(it, []) if pred(c)), None)

pump(0.6)
de = objs["DepthEdit"]
stage = find(win.contentItem(), lambda c: c.objectName() == "stage")
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 4), Q_ARG("QVariant", True)); pump(0.8)
extra = stage.property("pages").property(4).toQObject()
extra.setProperty("tool", 1); pump(0.8)
page = find(extra, lambda c: c.objectName() == "depthEditPage")
print("   ready:", until(lambda: de.property("state").get("ready"), 90), "loadError:", de.property("state").get("loadError"), flush=True)
S = lambda: de.property("state")  # noqa: E731
de.setVar("page", "Segment"); pump(0.5)
sec = find(page, lambda c: c.objectName() == "deSamSection")
check("SAM 3 set up: the page's SAM 3 section is gone", sec is not None and not sec.isVisible()
      and page.property("samInstalled") is True, f"install_btn {S().get('install_btn')} weights_btn {S().get('weights_btn')}")

# ---- its settings in Settings, under the stage showing
dlg = win.findChild(QObject, "settingsDialog")
QMetaObject.invokeMethod(dlg, "open"); pump(0.5)
nav = [c.property("modelData") for c in walk(win.contentItem(), []) if c.objectName() == "settingsNav"]
dlg.setProperty("current", 0); pump(0.5)          # the stage's section: first
rows = {r.property("text"): r for r in walk(win.contentItem(), []) if r.objectName() == "settingsItem" and r.isVisible()}
check("Settings: a SAM 3 section for the Depth Map Editor", {"Keep Video Frames in System RAM", "Unload SAM 3 Model",
      "Uninstall SAM 3"} <= set(rows), str(sorted(rows)))
ram = rows.get("Keep Video Frames in System RAM")
if ram is not None:
    was = bool(S().get("offload"))
    QMetaObject.invokeMethod(ram, "flipped", Q_ARG(bool, not was)); pump(0.5)
    check("...its RAM switch sets the editor's option", bool(S().get("offload")) == (not was), str(S().get("offload")))
    QMetaObject.invokeMethod(ram, "flipped", Q_ARG(bool, was)); pump(0.3)
check("...Uninstall is on, Unload off with no model loaded", rows.get("Uninstall SAM 3") is not None
      and rows["Uninstall SAM 3"].property("enabled") is True and rows["Unload SAM 3 Model"].property("enabled") is False)
QMetaObject.invokeMethod(dlg, "close"); pump(0.4)

# ---- uninstall: the checkpoints to the Recycle Bin, the page section back
toasts = []; de.toast.connect(lambda k, t, m: toasts.append((k, t, m)))
# never past this point unless SAM 3 is looked for in the temp folder: Uninstall deletes what's there
if not str(sam.WEIGHTS_DIR).startswith(str(tmp)):
    print(f"  [FAIL] SAM 3 is looked for in {sam.WEIGHTS_DIR}, not the temp folder: stopping before Uninstall")
    _fixtures.exit_now(1)
de.uninstallSam()
until(lambda: S().get("weights_btn") is True, 15); pump(0.5)
check("Uninstall: the checkpoints go to the Recycle Bin", binned == [ckpt.name] and not ckpt.exists(), str(binned))
check("...the page's SAM 3 section comes back to set it up again", sec.isVisible() and page.property("samInstalled") is False
      and S().get("weights_btn") is True, f"visible {sec.isVisible()}")
check("...and says so", bool(toasts) and toasts[-1][1] == "SAM 3 uninstalled", str(toasts[-1:]))
after_real = sorted(p.name for p in real_weights.glob("*")) if real_weights.is_dir() else []
check("the real weights/SAM3 untouched", after_real == before_real, f"{before_real} -> {after_real}")

# ---- Download Weights: Settings > Studio > Models' download, with a bar and a Stop (user, 2026-10-21) --
# the fetch stood in for (nothing is downloaded): it reports progress until Stop
import threading as _th
import studio.model_catalog as _C
_seen = {"stopped": False}
def _fake_download(parts, progress, stop, procs=None, wait=None):
    while not stop.is_set():
        progress(0.4, "Downloading sam3.pt  1.4 / 3.5 GB"); time.sleep(0.05)
    _seen["stopped"] = True
    raise _C.Stopped()
_C.download = _fake_download
models = objs["Models"]
de.run("_download_weights"); pump(0.6)
dl = lambda: models.property("download") or {}
check("Download Weights runs Models' download of SAM 3", dl().get("key") == "sam3", str(dl()))
row = find(win.contentItem(), lambda c: c.objectName() == "deSamDownload")
stop_btn = find(win.contentItem(), lambda c: c.objectName() == "deSamDownloadStop")
check("...the SAM 3 section shows its bar and a Stop", row is not None and row.isVisible() and stop_btn is not None
      and stop_btn.isVisible(), f"row {row is not None and row.isVisible()}")
check("...and Download Weights greyed while it runs",
      find(win.contentItem(), lambda c: c.property("op") == "_download_weights").property("enabled") is False)
QMetaObject.invokeMethod(stop_btn, "clicked"); pump(0.2)
check("Stop ends the download", until(lambda: _seen["stopped"] and not dl().get("key"), 10), str(dl()))
check("...and the bar goes", until(lambda: not row.isVisible(), 5))

studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
