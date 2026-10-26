"""Video Depth Anything, DepthCrafter's second tab (user, 2026-10-25), in the real app offscreen -- a
real run on the GPU (VDA Small at 392, the small fixture's 48-frame clips):

* DepthCrafter's page has the tab strip; its second tab is VDA's page, with its own folders;
* Splatting's preview showing clipA (its DepthCrafter map open), Rerun with Video Depth Anything:
  VDA's tab with the clip loaded; Start -- the DepthCrafter map moves into DepthCrafter/, the new
  map takes its place (tagged, 10-bit, as wide as a multiple of 16, every frame), and the preview
  comes back on clipA with the new map;
* a folder run: both clips; the earlier VDA map is replaced, the DepthCrafter one kept once;
  the job handoff names the output folder (Splatting's Import from DepthCrafter);
* a size whose checkpoint isn't there: the page says so and offers its download; Start refuses.

The checkpoint comes from a temp models folder (STEREOCRAFTER_WEIGHTS) holding a copy of Small --
never the real folder (memory: a check once deleted the real SAM 3 weights). Configs and
last_jobs.json redirected.
"""
import os, sys, tempfile, hashlib, time, glob, json, shutil, subprocess
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, str(Path(__file__).parent))
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
tmp = Path(tempfile.mkdtemp())
import _fixtures
_real = REPO / "weights" / "VideoDepthAnything" / "video_depth_anything_vits.pth"
if not _real.is_file():
    _fixtures.skip("Video Depth Anything Small isn't in the models folder")
W = tmp / "weights" / "VideoDepthAnything"; W.mkdir(parents=True)
shutil.copy2(_real, W / _real.name)
os.environ["STEREOCRAFTER_WEIGHTS"] = str(tmp / "weights")
FIX = tmp / "fx"; _fixtures.copy("small", FIX)
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, QObject, Q_ARG, QPoint, QPointF, Qt, qInstallMessageHandler
from PySide6.QtTest import QTest
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from studio import app as A
from core.vda import output as OUT

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
def find_all(it, pred, out=None):
    out = [] if out is None else out
    for c in it.childItems():
        if pred(c): out.append(c)
        find_all(c, pred, out)
    return out
def one(name):
    got = [c for c in find_all(win.contentItem(), lambda c: c.objectName() == name) if c.isVisible()]
    return got[0] if got else None
n = lambda p: os.path.normcase(os.path.normpath(str(p)))
def probe(p):
    out = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-show_entries",
                          "stream=width,height,pix_fmt,nb_read_frames", "-of", "json", str(p)],
                         capture_output=True, text=True).stdout
    return json.loads(out)["streams"][0]
stage = find_all(win.contentItem(), lambda c: c.objectName() == "stage")[0]
def show(i):
    QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", i), Q_ARG("QVariant", True)); pump(1.0)
win.setProperty("userSized", True); win.setHeight(1600); pump(0.5)

vda = objs["VDA"]; vst = vda.property("settings")
toasts = []
vda.toast.connect(lambda *a: toasts.append(a))
for k, v in {"model_size": "Small", "input_size": 392, "move_to_finished": False}.items():
    vst.setProperty(k, v)
pump(0.3)

# ---- the tab ------------------------------------------------------------------------------
show(0)
dcpage = stage.property("pages").property(0).toQObject()
check("DepthCrafter's page has the DepthCrafter | VDA strip",
      one("dcEngineSwitch") is not None and one("dcEngineSwitch").property("model").toVariant() == ["DepthCrafter", "VDA"]
      if one("dcEngineSwitch") is not None else False)
check("...DepthCrafter's tab first", dcpage.property("engine") == 0 and one("vdaPage") is None and one("pageContent") is not None)
dcpage.setProperty("engine", 1); pump(0.8)
check("...the second shows VDA's page and hides DepthCrafter's", one("vdaPage") is not None and one("pageContent") is None)
check("...with its own Input and Output boxes", one("vdaInput") is not None and one("vdaOutput") is not None)
check("...and the weights it has: no download offered for Small", vda.property("weightsInstalled") is True
      and one("vdaWeightsMissing") is None)

# ---- Rerun with Video Depth Anything, the preview showing the clip --------------------------
sp = objs["Splatting"]; pv = sp.preview
for k, v in {"input_source_clips": str(FIX / "src").replace("\\", "/"), "input_depth_maps": str(FIX / "depth").replace("\\", "/"),
             "output_splatted": str(FIX / "out").replace("\\", "/"), "multi_map": False, "auto_save_sidecar": False,
             "move_to_finished": False}.items():
    sp._set(k, v)
show(1)
pv.showClip(str(FIX / "src" / "clipA.mp4"))
until(lambda: pv.property("loaded") and pv.property("imageWidth") > 0, 90); pump(1.0)
check("Splatting's preview shows clipA, its DepthCrafter map open", pv.property("loaded")
      and n(pv.property("currentDepth")) == n(FIX / "depth" / "clipA_depth.mp4"), str(pv.property("currentDepth")))
vda.loadForRerun(str(FIX / "src" / "clipA.mp4"), sp.depthFolderFor(str(FIX / "src" / "clipA.mp4")))
pump(0.5)
check("the rerun loads the clip and its depth folder into VDA's own boxes",
      n(vst.value("input_dir_or_file_var")) == n(FIX / "src" / "clipA.mp4") and n(vst.value("output_dir")) == n(FIX / "depth"))
vda.start()
check("Start runs", until(lambda: vda.property("busy"), 20))
until(lambda: not vda.property("busy"), 600); pump(1.0)
aside = FIX / "depth" / "DepthCrafter" / "clipA_depth.mp4"
new = FIX / "depth" / "clipA_depth.mp4"
check("the DepthCrafter map moved into DepthCrafter/ (the preview had it open)", aside.is_file(), str(toasts[-1:]))
check("...the VDA map in its place, tagged", new.is_file() and OUT.is_vda_map(str(new)))
info = probe(new)
check("...10-bit, as wide as a multiple of 16, every frame", info.get("pix_fmt") == "yuv420p10le"
      and int(info["width"]) % 16 == 0 and int(info.get("nb_read_frames", 0)) == 48, str(info))
check("...nothing left behind (no .part, no raw file)", not glob.glob(str(FIX / "depth" / "*.part"))
      and not glob.glob(str(FIX / "depth" / ".*vda-raw*")), str(os.listdir(FIX / "depth")))
check("...the toast says where the DepthCrafter map is", bool(toasts) and "DepthCrafter" in toasts[-1][2], str(toasts[-1:]))
check("Splatting's preview comes back on clipA, with the new map",
      until(lambda: pv.property("loaded") and n(pv.property("currentDepth")) == n(new), 90)
      and os.path.basename(list(pv.property("clipPaths"))[int(pv.property("videoIndex"))]) == "clipA.mp4",
      str(pv.property("currentDepth")))
check("the source clip stayed where it was (a single clip isn't moved)", (FIX / "src" / "clipA.mp4").is_file())
# the VDA badge (user, 2026-10-25): on the clip whose map VDA made, in Splatting's list
q = pv.queue
def _vda_rows():
    names = [str(c) for c in q.property("clips")]
    fx = q.property("clipFx")
    return {nm: bool(len(f) > 8 and f[8]) for nm, f in zip(names, fx)}
q.rescan()
check("Splatting's list: the VDA badge on clipA, not on clipB (DepthCrafter's still)",
      until(lambda: _vda_rows() == {"clipA.mp4": True, "clipB.mp4": False}, 30), str(_vda_rows()))
show(1); pump(0.6)
badge = [c for c in find_all(win.contentItem(), lambda c: c.objectName() == "fxVda") if c.isVisible()]
check("...drawn on its row", len(badge) == 1, str(len(badge)))

# ---- a folder run --------------------------------------------------------------------------
vst.setProperty("input_dir_or_file_var", str(FIX / "src")); pump(0.8)
toasts.clear()
vda.start()
until(lambda: vda.property("busy"), 20)
until(lambda: not vda.property("busy"), 600); pump(1.0)
check("a folder run makes both maps", all(OUT.is_vda_map(str(FIX / "depth" / f"{c}_depth.mp4")) for c in ("clipA", "clipB")),
      str(toasts[-1:]))
check("...clipA's earlier VDA map replaced, its DepthCrafter map kept once; clipB's set aside too",
      sorted(os.listdir(FIX / "depth" / "DepthCrafter")) == ["clipA_depth.mp4", "clipB_depth.mp4"],
      str(os.listdir(FIX / "depth" / "DepthCrafter")))
recd = json.loads(Path(jh._RECORD_PATH).read_text(encoding="utf-8")) if Path(jh._RECORD_PATH).exists() else {}
dcrec = recd.get("depthcrafter") or {}
check("the job handoff names the output folder, as DepthCrafter's", n(dcrec.get("depth", "")) == n(FIX / "depth"), str(dcrec))
# its progress in the console's Depth Map tab (user, 2026-10-25: "add vda job progress to the depth map tab"):
# a drawn bar per pass and clip, kept as a line when the pass ends
logs = objs["Logs"]
def _bars(tab):
    return [r for r in logs.tab(tab)._rows if r[0].startswith("VDA ") and r[4]]
dm = _bars("depthcrafter")
check("the Depth Map console tab shows VDA's progress as bars, depth and encode, for each clip",
      all(any(r[0].startswith(f"VDA {ph} {c}.mp4") and r[4][1] >= 0.999 for r in dm)
          for ph in ("depth", "encode") for c in ("clipA", "clipB")), str([r[0] for r in dm][-6:]))
check("...and not in another stage's tab", not _bars("splatting"), str([r[0] for r in _bars("splatting")][:3]))

# ---- a size not downloaded -----------------------------------------------------------------
show(0); dcpage.setProperty("engine", 1); pump(0.5)
vst.setProperty("model_size", "Base"); pump(0.5)
check("Base not in the models folder: the page says so and offers its download",
      vda.property("weightsInstalled") is False and one("vdaWeightsMissing") is not None and one("vdaDownload") is not None)
toasts.clear()
vda.start(); pump(0.5)
check("...and Start refuses, saying why", vda.property("busy") is False and toasts and toasts[-1][0] == "error", str(toasts[-1:]))

check("no QML warnings", not warns, str(warns[:3]))
h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
check("the real last_jobs.json untouched", h0 == h1)
print("PASS" if not fails else f"FAIL ({len(fails)})", fails)
_fixtures.exit_now(0 if not fails and h0 == h1 else 1)
