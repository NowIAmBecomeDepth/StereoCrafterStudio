"""DVD, Depth Map's DVD tab (user, 2026-11-07), in the real app offscreen -- a real run on the GPU (Input
Size 256, windows of 25 overlapping by 9: three windows on the small fixture's 48-frame clips):

* the Depth Map strip is DepthCrafter | VDA | DVD | Blend; the third tab is DVD's page, with its own folders,
  Input Size and Window menus;
* Splatting's preview showing clipA (its DepthCrafter map open), Rerun with DVD (send_back): DVD's tab with
  the clip loaded; Start -- the DepthCrafter map moves into DepthCrafter/, the new map takes its place
  (DVD's tag, 10-bit, every frame), and the preview comes back on clipA with the new map;
* Splatting's list: the DVD badge on clipA (not the VDA one), not on clipB; drawn on its row; the legend
  names it;
* the console's Depth Map tab shows DVD's progress bars;
* the checkpoint gone: the page offers its download and Start refuses.

The checkpoint is hard-linked into a temp models folder on the same drive (STEREOCRAFTER_WEIGHTS) -- never
the real folder (memory: a check once deleted the real SAM 3 weights); removing the link leaves the real
file. Configs and last_jobs.json redirected.
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
_real = REPO / "weights" / "DVD" / "dvd_1.1.safetensors"
if not _real.is_file():
    _fixtures.skip("DVD isn't in the models folder")
# a hard link needs the same drive as the real file: a temp folder beside the repo, removed at the end
wroot = Path(tempfile.mkdtemp(prefix=".dvd_check_", dir=str(REPO / ".claude")))
W = wroot / "DVD"; W.mkdir(parents=True)
os.link(_real, W / _real.name)
os.environ["STEREOCRAFTER_WEIGHTS"] = str(wroot)
FIX = tmp / "fx"; _fixtures.copy("small", FIX)
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, QObject, Q_ARG, qInstallMessageHandler
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

try:
    dvd = objs["DVD"]; dst = dvd.property("settings")
    toasts = []
    dvd.toast.connect(lambda *a: toasts.append(a))
    for k, v in {"input_size": 256, "width": 256, "early_downsample": False, "window": 25, "overlap": 9,
                 "move_to_finished": False}.items():
        dst.setProperty(k, v)
    pump(0.3)

    # ---- the tab ------------------------------------------------------------------------------
    show(0)
    dcpage = stage.property("pages").property(0).toQObject()
    sw = one("dcEngineSwitch")
    _blend = os.path.isfile("studio/qml/Studio/BlendPage.qml")      # Blend: the user's own copy, not released
    _dm = ["DepthCrafter", "VDA", "DVD"] + (["Blend"] if _blend else [])
    check("Depth Map's strip is " + " | ".join(_dm), sw is not None and sw.property("model").toVariant() == _dm)
    dcpage.setProperty("engine", 2); pump(0.8)
    check("...the third tab shows DVD's page, not DepthCrafter's, VDA's or Blend's",
          one("dvdPage") is not None and one("pageContent") is None and one("vdaPage") is None and one("blendPage") is None)
    check("...with its own Input and Output boxes", one("dvdInput") is not None and one("dvdOutput") is not None)
    if _blend:
        dcpage.setProperty("engine", 3); pump(0.8)
        check("...and Blend is the fourth", one("blendPage") is not None and one("dvdPage") is None)
        dcpage.setProperty("engine", 2); pump(0.8)
    from studio.stages.dvd import settings as DS
    sm = next(o for o in win.findChildren(QObject) if o.objectName() == "dvdInputSizesMenu")
    QMetaObject.invokeMethod(one("dvdInputSizes"), "clicked"); pump(0.6)
    _items = [o for o in win.findChildren(QObject) if o.objectName() == "dvdInputSizeItem"]
    _texts = [str(o.property("text")) for o in _items]
    check("Input Size's button lists the sizes, 480 marked as trained",
          sm.property("opened") and [int(t.split()[0]) for t in _texts] == DS.INPUT_SIZES
          and any(t.startswith("480") and "trained" in t for t in _texts), str(_texts))
    sm.close(); pump(0.3)
    dst.setProperty("input_size", "300"); pump(0.4)
    check("...a size typed snaps up to a multiple of 16 (300 -> 304)", int(dst.value("input_size")) == 304, str(dst.value("input_size")))
    dst.setProperty("input_size", 256)
    check("Width box and Early Downsample switch on the page (Billy's GUI's)",
          one("dvdWidth") is not None and one("dvdEarlyDownsample") is not None)
    check("the TensorRT box on the page, off by default (user, 2026-11-07)",
          one("dvdTensorRT") is not None and dst.value("use_tensorrt") in (False, "false"), str(dst.value("use_tensorrt")))
    dst.setProperty("window", "30"); pump(0.4)
    check("Window takes any length 9..81 (30 stays 30)", int(dst.value("window")) == 30, str(dst.value("window")))
    dst.setProperty("window", 25); pump(0.3)
    check("the weights there: no download offered", dvd.property("weightsInstalled") is True and one("dvdWeightsMissing") is None)

    # ---- Rerun with DVD, the preview showing the clip -------------------------------------------
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
    q = pv.queue
    q.sendBack("dvd", [str(FIX / "src" / "clipA.mp4")], dvd)
    check("Rerun with DVD loads the clip and its depth folder into DVD's own boxes",
          until(lambda: n(dst.value("input_dir_or_file_var")) == n(FIX / "src" / "clipA.mp4")
                and n(dst.value("output_dir")) == n(FIX / "depth"), 30),
          f"{dst.value('input_dir_or_file_var')} / {dst.value('output_dir')}")
    dvd.start()
    check("Start runs", until(lambda: dvd.property("busy"), 20))
    until(lambda: not dvd.property("busy"), 900); pump(1.0)
    aside = FIX / "depth" / "DepthCrafter" / "clipA_depth.mp4"
    new = FIX / "depth" / "clipA_depth.mp4"
    check("the DepthCrafter map moved into DepthCrafter/ (the preview had it open)", aside.is_file(), str(toasts[-1:]))
    check("...the DVD map in its place, DVD's", new.is_file() and OUT.kind_of(str(new)) == "dvd")
    info = probe(new) if new.is_file() else {}
    check("...10-bit, both sides multiples of 16, every frame", info.get("pix_fmt") == "yuv420p10le"
          and int(info.get("width", 1)) % 16 == 0 and int(info.get("height", 1)) % 16 == 0
          and int(info.get("nb_read_frames", 0)) == 48, str(info))
    check("...nothing left behind (no .part, no raw file)", not glob.glob(str(FIX / "depth" / "*.part"))
          and not glob.glob(str(FIX / "depth" / ".*dvd-raw*")), str(os.listdir(FIX / "depth")))
    check("...the toast says where the old map is", bool(toasts) and "DepthCrafter" in toasts[-1][2], str(toasts[-1:]))
    check("Splatting's preview comes back on clipA, with the new map",
          until(lambda: pv.property("loaded") and n(pv.property("currentDepth")) == n(new), 90),
          str(pv.property("currentDepth")))
    check("the source clip stayed where it was (a single clip isn't moved)", (FIX / "src" / "clipA.mp4").is_file())
    def _rows():
        names = [str(c) for c in q.property("clips")]
        fx = q.property("clipFx")
        return {nm: (bool(len(f) > 8 and f[8]), bool(len(f) > 9 and f[9])) for nm, f in zip(names, fx)}
    q.rescan()
    check("Splatting's list: the DVD badge on clipA (not VDA's), none on clipB",
          until(lambda: _rows() == {"clipA.mp4": (False, True), "clipB.mp4": (False, False)}, 30), str(_rows()))
    show(1); pump(0.6)
    badge = [c for c in find_all(win.contentItem(), lambda c: c.objectName() == "fxDvd") if c.isVisible()]
    check("...drawn on its row", len(badge) == 1, str(len(badge)))
    logs = objs["Logs"]
    dm = [r for r in logs.tab("depthcrafter")._rows if r[0].startswith("DVD ") and r[4]]
    check("the Depth Map console tab shows DVD's progress as bars, depth and encode",
          all(any(r[0].startswith(f"DVD {ph} clipA.mp4") and r[4][1] >= 0.999 for r in dm) for ph in ("depth", "encode")),
          str([r[0] for r in dm][-4:]))
    recd = json.loads(Path(jh._RECORD_PATH).read_text(encoding="utf-8")) if Path(jh._RECORD_PATH).exists() else {}
    check("the job handoff names the output folder, as DepthCrafter's",
          n((recd.get("depthcrafter") or {}).get("depth", "")) == n(FIX / "depth"), str(recd.get("depthcrafter")))

    # ---- the checkpoint gone ------------------------------------------------------------------------
    os.remove(W / _real.name)
    dvd.recheckWeights(); pump(0.3)
    show(0); dcpage.setProperty("engine", 2); pump(0.6)
    check("no checkpoint: the page says so and offers its download",
          dvd.property("weightsInstalled") is False and one("dvdWeightsMissing") is not None and one("dvdDownload") is not None)
    toasts.clear()
    dvd.start(); pump(0.5)
    check("...and Start refuses, saying why", dvd.property("busy") is False and toasts and toasts[-1][0] == "error", str(toasts[-1:]))
    check("the real checkpoint is untouched", _real.is_file() and _real.stat().st_size == 4491827302)

    check("no QML warnings", not warns, str(warns[:3]))
finally:
    shutil.rmtree(wroot, ignore_errors=True)
h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
check("the real last_jobs.json untouched", h0 == h1)
print("PASS" if not fails else f"FAIL ({len(fails)})", fails)
_fixtures.exit_now(0 if not fails and h0 == h1 else 1)
