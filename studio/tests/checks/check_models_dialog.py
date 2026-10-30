"""Settings > Studio > Models (user, 2026-10-20), in the real app offscreen: the item is in Settings'
Studio section; the window lists every model with its state; the Hugging Face sign-in (faked: no
network, the real token store untouched); a Download (the child's fetch faked: nothing comes from
the network) turns a row Installed; during a job the folder can't change and a download waits;
a new folder is saved to studio.json and every stage follows it. The models folder is a temp one,
and only it is looked in. Configs and last_jobs.json redirected."""
import os, sys, tempfile, hashlib, time, json
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, str(Path(__file__).parent))
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
tmp = Path(tempfile.mkdtemp())
# an empty models folder, and the only place looked in (core.weights_paths)
os.environ["STEREOCRAFTER_WEIGHTS"] = str(tmp / "weights")
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
import _fixtures
# Hugging Face faked: no network, the real token store never written
import studio.hf_account as HF
_acct = {"name": None}
HF.whoami = lambda: _acct["name"]
def _sign_in(token):
    if token != "hf_good":
        raise ValueError("Hugging Face didn't accept that token.")
    _acct["name"] = "tester"; return "tester"
HF.sign_in = _sign_in
HF.sign_out = lambda: _acct.__setitem__("name", None)
HF.access = lambda repo: ("signin" if _acct["name"] is None else "request" if repo == "facebook/sam3" else "ok")
from studio import model_catalog as C
C._FETCH = ("import sys, os\nrepo, rev, folder, *files = sys.argv[1:]\nfor f in files:\n"
            "    p = os.path.join(folder, f); os.makedirs(os.path.dirname(p) or folder, exist_ok=True)\n"
            "    open(p, 'wb').write(b'x' * 1000)\n")
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, QObject, qInstallMessageHandler
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
def wait(pred, sec=10.0):
    end = time.time() + sec
    while not pred() and time.time() < end:
        pump(0.05)
    return pred()
def find(it, pred):
    for c in it.childItems():
        if pred(c): return c
        r = find(c, pred)
        if r is not None: return r
pump(0.8)

models = objs["Models"]
from core import weights_paths as WP
check("the models folder is the temp one, looked in alone", WP.roots() == [tmp / "weights"], str(WP.roots()))
item = win.findChild(QObject, "modelsItem")
check("Settings > Studio has Models", item is not None and item.property("text") == "Models")

dlg = win.findChild(QObject, "modelsDialog")
QMetaObject.invokeMethod(dlg, "open"); pump(0.8)
root = dlg.property("contentItem")
def named(n): return find(root, lambda c: c.objectName() == n)
def row(key): return next(r for r in models.property("rows") if r["key"] == key)
check("a row for every model", named("modelsList").property("count") == len(C.MODELS)
      and all(named("modelsRow_" + m.key) is not None for m in C.MODELS if m.key in ("depthcrafter", "svd", "sam3")),
      str(named("modelsList").property("count")))
check("the folder shown", named("modelsFolderPath").property("text") == str(tmp / "weights"))
check("nothing here yet: DepthCrafter missing, 3.0 GB",
      row("depthcrafter")["state"] == "missing" and row("depthcrafter")["size"] == "3.0 GB"
      and named("modelsState_depthcrafter").property("text") == "Missing")
check("TransNetV2 needs no download: its package's copy", row("transnetv2")["state"] == "installed")

# ---- Hugging Face
check("not signed in (checked as it opened)", wait(lambda: named("modelsAccount").property("text") == "Not signed in"),
      named("modelsAccount").property("text"))
models.signIn("hf_bad")
check("a bad token: said, still signed out",
      wait(lambda: "didn't accept" in (models.property("account").get("message") or "")))
models.signIn("hf_good")
check("signed in", wait(lambda: named("modelsAccount").property("text") == "Signed in as tester"))
check("the gated rows know their access", wait(lambda: row("sam3")["access"] == "request" and row("svd")["access"] == "ok"),
      f"{row('sam3')['access']} {row('svd')['access']}")
pump(0.3)
sam_dl = named("modelsDownload_sam3")
check("no Download where access must be asked for first", sam_dl is not None and not sam_dl.isVisible())

# ---- Sign Out asks first: it signs the whole PC out (user, 2026-10-20)
from PySide6.QtCore import Q_ARG
prompt = win.findChild(QObject, "prompt")
so = named("modelsSignOut")
QMetaObject.invokeMethod(so, "clicked"); pump(0.5)
check("Sign Out asks first, saying every token on the PC goes",
      prompt.property("opened") and "every Hugging Face token" in str(prompt.property("message"))
      and named("modelsAccount").property("text") == "Signed in as tester", str(prompt.property("message"))[:80])
QMetaObject.invokeMethod(prompt, "finish", Q_ARG("QVariant", 0)); pump(0.4)
check("...Cancel keeps you signed in", named("modelsAccount").property("text") == "Signed in as tester")
QMetaObject.invokeMethod(so, "clicked"); pump(0.5)
QMetaObject.invokeMethod(prompt, "finish", Q_ARG("QVariant", 1))
check("...Sign Out signs out", wait(lambda: named("modelsAccount").property("text") == "Not signed in"))
models.signIn("hf_good")
check("...and signing in again works", wait(lambda: named("modelsAccount").property("text") == "Signed in as tester"))

# ---- a download
# the list builds only the rows in view: scroll to it
_ix = [m.key for m in C.MODELS].index("omnishotcut")
QMetaObject.invokeMethod(named("modelsList"), "positionViewAtIndex", Q_ARG(int, _ix), Q_ARG(int, 0)); pump(0.4)
dl = named("modelsDownload_omnishotcut")
check("OmniShotCut has a Download", dl is not None and dl.isVisible())
check("...which fetches it", models.start("omnishotcut") == ""
      and wait(lambda: row("omnishotcut")["state"] == "installed"), str(row("omnishotcut")))
check("...into the models folder", (tmp / "weights" / "SceneCut" / "OmniShotCut_ckpt.pth").is_file())
from core.scenecut import detection as D
check("...where Scene Cut now finds it", D.DETECTORS["OmniShotCut"].weights_path
      == str(tmp / "weights" / "SceneCut" / "OmniShotCut_ckpt.pth"))
pump(0.3)
check("...and the row says Installed", named("modelsState_omnishotcut").property("text") == "Installed")

# ---- TensorRT (2026-10-29): packages, not weights -- an Install, never run here
_real_ready = C.packages_ready
C.packages_ready = lambda m: False
models.refresh(); pump(0.3)
_ix = [m.key for m in C.MODELS].index("tensorrt")
QMetaObject.invokeMethod(named("modelsList"), "positionViewAtIndex", Q_ARG(int, _ix), Q_ARG(int, 0)); pump(0.4)
inst = named("modelsDownload_tensorrt")
check("TensorRT missing: an Install button", inst is not None and inst.isVisible() and inst.property("text") == "Install",
      str(inst and inst.property("text")))
check("...the row says Missing, 2.3 GB", named("modelsState_tensorrt").property("text") == "Missing"
      and row("tensorrt")["size"] == "2.3 GB")
C.packages_ready = _real_ready
models.refresh(); pump(0.3)

# ---- during a job
mg = objs["Merging"]
_fixtures.fake_busy(mg, True); pump(0.4)
check("a job running: Change… is off", not named("modelsChangeFolder").property("enabled"))
check("...and a new folder is refused", "running a job" in models.setFolder(str(tmp / "elsewhere")))
models.start("dinov3")
check("...a download waits for it", wait(lambda: "Waiting:" in (models.property("download").get("text") or "")),
      models.property("download").get("text"))
_fixtures.fake_busy(mg, False)
check("...and runs once it's done", wait(lambda: row("dinov3")["state"] == "installed"))

# ---- a new folder (the environment's override lifted, <repo>/weights a temp one: only temp folders looked in)
del os.environ["STEREOCRAFTER_WEIGHTS"]
WP.DEFAULT = tmp / "repo weights"
WP.set_root(tmp / "weights")
other = tmp / "other models"
check("a new folder taken", models.setFolder(str(other)) == "" and WP.root() == other)
pump(0.3)
check("...shown", named("modelsFolderPath").property("text") == str(other))
objs["Shell"].save()
saved = json.loads((ss.CONFIG_DIR / "studio.json").read_text(encoding="utf-8"))
check("...and saved in studio.json", saved.get("weights_dir") == str(other), str(saved.get("weights_dir")))
from studio.stages.inpainting_v2 import engine as V2E
check("...every stage follows it (Inpainting V2's engine)", V2E.PRE_TRAINED_PATH.startswith(str(other)), V2E.PRE_TRAINED_PATH)
check("...and the downloaded rows are missing there", row("omnishotcut")["state"] == "missing")

# a look at it, for the eye (our own window only)
out = os.environ.get("STUDIO_CHECK_SHOT")
if out:
    models.setFolder(str(tmp / "weights")); pump(0.6)
    win.grabWindow().save(out)

check("no QML warnings", not warns, str(warns[:3]))
h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
check("the real last_jobs.json untouched", h0 == h1)
print("PASS" if not fails else f"FAIL ({len(fails)})")
_fixtures.exit_now(0 if not fails else 1)
