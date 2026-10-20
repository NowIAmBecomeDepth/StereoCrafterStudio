"""Upcoming Jobs on DepthCrafter and Inpainting V1 (user, 2026-10-10), in the
real app offscreen, the models stood in for (as check_run_outcomes does):

* the list sits right of the settings, as tall as them, and lists what a Start
  would process (DepthCrafter: its input's clips; Inpainting: the Lo-Res splats);
* Process Selected runs the ticked clips only; right-click -> Process / Inpaint
  This Clip Now runs that clip only; a row's click loads nothing (no preview);
* right-click -> Move to Finished moves that clip -- and for Inpainting its
  .spsidecar and Hi-Res splat -- into finished/, and it leaves the list;
* DepthCrafter's batch moves the clips it finished, and they leave the list.

Configs and last_jobs.json redirected; TEMP copies of the small and merge fixtures.
"""
import os, sys, tempfile, hashlib, time, shutil, types
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")     # nothing here needs the GPU
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, str(Path(__file__).parent))
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
tmp = Path(tempfile.mkdtemp())
import _fixtures
FIX = tmp / "fx"; _fixtures.copy("small", FIX)
MFX = tmp / "mfx"; _fixtures.copy("merge", MFX)
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, QObject, Q_ARG, QPoint, QPointF, Qt, qInstallMessageHandler
from PySide6.QtTest import QTest
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from studio import app as A

# ---- the stand-ins: DepthCrafter's model, Inpainting's model and per-clip render
from depthcrafter.utils import get_full_video_output_filename
dc_ran = []
class Demo:
    def __init__(self, **kw):
        self.pipe = None
    @staticmethod
    def get_latent_checkpoint_path(base_output_folder, basename):
        return os.path.join(base_output_folder, f"{basename}_latents.ckpt")
    def run(self, **kw):
        dc_ran.append(kw["original_video_basename_override"])
        out = kw["base_output_folder"]; os.makedirs(out, exist_ok=True)
        p = os.path.join(out, get_full_video_output_filename(kw["original_video_basename_override"], "mp4"))
        Path(p).write_bytes(b"mp4")
        return p, {"status": "success", "output_video_filename": os.path.basename(p),
                   "frames_in_output_video": 12, "internal_processing_duration_seconds": 1.0,
                   "processed_height": 64, "processed_width": 64}
fake = types.ModuleType("depthcrafter.depthcrafter_logic"); fake.DepthCrafterDemo = Demo
sys.modules["depthcrafter.depthcrafter_logic"] = fake
import studio.stages.inpainting.engine as IE
ip_ran = []
IE.load_inpainting_pipeline = lambda **kw: object()
def process_single_video(self, *a, **kw):
    ip_ran.append(os.path.basename(kw.get("input_video_path") or a[1]))
    return True, None
IE.InpaintingEngine.process_single_video = process_single_video

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
def one(root, name):
    got = [c for c in find_all(root, lambda c: c.objectName() == name) if c.isVisible()]
    return got[0] if got else None
def centre(item):
    c = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    return QPoint(int(c.x()), int(c.y()))
def click(item):
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, centre(item)); pump(0.3)
def names(q):
    return [str(n) for n in (q.property("src").property("clips") or [])]
def row_of(q, name):
    lst = one(q, "jobQueueList")
    i = [k for k, n in enumerate(names(q)) if name in n][0]
    QMetaObject.invokeMethod(lst, "positionViewAtIndex", Q_ARG(int, i), Q_ARG(int, 2)); pump(0.3)
    return [r for r in find_all(q, lambda c: c.objectName() == "jobQueueRow")
            if r.isVisible() and name in str(r.property("modelData"))][0]
def action(q, name, act):
    """Right-click the row, pick the menu's item; the texts it offered. A run's toast
    drops in over the top right for a few seconds: wait it out if it took the click."""
    m = [o for o in q.findChildren(QObject) if o.objectName() == "jobQueueRowMenu"][0]
    end = time.time() + 12
    while True:
        QTest.mouseClick(win, Qt.RightButton, Qt.NoModifier, centre(row_of(q, name))); pump(0.5)
        if m.property("opened") or time.time() > end:
            break
        pump(1.0)
    items = [o for o in m.findChildren(QObject) if o.objectName().startswith("jobQueueAction_")]
    texts = [str(o.property("text")) for o in items]
    hit = [o for o in items if o.objectName() == "jobQueueAction_" + act]
    if hit:
        QMetaObject.invokeMethod(hit[0], "click"); pump(0.5)
    return texts
# "Play ..." (user, 2026-10-10): the default player stood in for -- nothing opens
played = []
os.startfile = lambda p, *a, **k: played.append(os.path.normcase(os.path.normpath(p)))
def play(q, name, pick=None):
    """Right-click the row: the Play items offered; with `pick`, choose that one."""
    m = [o for o in q.findChildren(QObject) if o.objectName() == "jobQueueRowMenu"][0]
    end = time.time() + 12
    while True:                     # a run's toast may sit over the list for a moment
        QTest.mouseClick(win, Qt.RightButton, Qt.NoModifier, centre(row_of(q, name))); pump(0.5)
        if m.property("opened") or time.time() > end:
            break
        pump(1.0)
    items = [o for o in m.findChildren(QObject) if o.objectName() == "jobQueuePlay"]
    texts = [str(o.property("text")) for o in items]
    hit = [o for o in items if o.property("text") == pick]
    if hit:
        QMetaObject.invokeMethod(hit[0], "click"); pump(0.5)
    else:
        QMetaObject.invokeMethod(m, "close"); pump(0.3)
    return texts
n = lambda p: os.path.normcase(os.path.normpath(str(p)))
def run_to_end(ctl, sec=120):
    until(lambda: ctl.property("busy"), 20)
    until(lambda: not ctl.property("busy"), sec)
    pump(1.0)
stage = find_all(win.contentItem(), lambda c: c.objectName() == "stage")[0]
def show(i):
    QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", i), Q_ARG("QVariant", True)); pump(1.0)
win.setProperty("userSized", True); win.setWidth(1215); win.setHeight(1100); pump(0.5)
pump(0.5)

# ================= DepthCrafter =================
dc = objs["DepthCrafter"]; dst = dc.property("settings")
show(0)
page = stage.property("pages").property(0).toQObject()
for k, v in {"input_dir_or_file_var": str(FIX / "src"), "output_dir": str(tmp / "dc_out"),
             "process_as_segments_var": False}.items():
    dst.setProperty(k, v)
dc.save()
dq = one(page, "dcJobQueue")
check("DepthCrafter: Upcoming Jobs lists the input's clips", dq is not None and until(lambda: names(dq) == ["clipA.mp4", "clipB.mp4"], 20),
      str(names(dq)) if dq else "no list")
cols = [c for c in find_all(page, lambda c: c.metaObject().className().startswith("Section") and c.isVisible()
                            and c.property("title") in ("Main Parameters", "Frame & Segment Control"))]
right = max(c.mapToScene(QPointF(c.width(), 0)).x() for c in cols)
top = min(c.mapToScene(QPointF(0, 0)).y() for c in cols)
qx, qy = dq.mapToScene(QPointF(0, 0)).x(), dq.mapToScene(QPointF(0, 0)).y()
check("...on the right of the settings, level with their top", qx > right and abs(qy - top) < 2, f"list x {qx:.0f} > {right:.0f}, y {qy:.0f} vs {top:.0f}")
check("...as tall as the settings, and wide enough to read", dq.height() > 300 and dq.width() >= 300, f"{dq.width():.0f}x{dq.height():.0f}")
def reaches_bar(pg, q):
    """In a tall window the list runs down to the action bar (user, 2026-10-10)."""
    bar = [c for c in find_all(pg, lambda c: c.metaObject().className().startswith("ActionBar") and c.isVisible())][0]
    gap = bar.mapToScene(QPointF(0, 0)).y() - q.mapToScene(QPointF(0, q.height())).y()
    return 0 <= gap <= 14, f"{gap:.0f} px above the bar"
h_was = win.height()
win.setHeight(h_was + 300); pump(0.8)
ok, why = reaches_bar(page, dq)
check("...in a taller window it runs down to the action bar", ok and dq.height() > 600, why)
win.setHeight(h_was); pump(0.8)
texts = play(dq, "clipA", "Play Source Clip")
check("right-click: Play Source Clip, which opens it in the default player",
      texts == ["Play Source Clip"] and played[-1:] == [n(FIX / "src" / "clipA.mp4")], f"{texts} {played[-1:]}")
click(one(row_of(dq, "clipB"), "jobQueueCheck"))
click(one(dq, "queueProcessSelected"))
run_to_end(dc)
check("Process Selected runs the ticked clip only", dc_ran == ["clipB"], str(dc_ran))
check("...the batch moved it to finished, and it left the list",
      (FIX / "src" / "finished" / "clipB.mp4").exists() and until(lambda: names(dq) == ["clipA.mp4"], 20), str(names(dq)))
dc_ran.clear()
texts = action(dq, "clipA", "run")
check("right-click: Process This Clip Now, Test Render and Move to Finished",           # Test Render: 2026-10-15
      texts == ["Process This Clip Now", "Test Render", "Move to Finished"], str(texts))
run_to_end(dc)
check("Process This Clip Now runs that clip", dc_ran == ["clipA"], str(dc_ran))
# Move to Finished: a fresh clip
shutil.copy(FIX / "src" / "finished" / "clipB.mp4", FIX / "src" / "clipC.mp4")
dc.save()
until(lambda: "clipC.mp4" in names(dq), 20)
action(dq, "clipC", "finished")
check("Move to Finished moves that clip into finished/, and it leaves the list",
      until(lambda: (FIX / "src" / "finished" / "clipC.mp4").exists() and "clipC.mp4" not in names(dq), 20), str(names(dq)))

# Send to Splatting (user, 2026-10-11): on a finished (green) row only. A batch moves its
# clips on, so the green row is stood in for: clipD listed, its depth map written.
sp = objs["Splatting"]; sst = sp.property("settings")
sp_toasts = []
sp.toast.connect(lambda *a: sp_toasts.append(a))
shutil.copy(FIX / "src" / "finished" / "clipB.mp4", FIX / "src" / "clipD.mp4")
dc.save()
until(lambda: "clipD.mp4" in names(dq), 20)
(tmp / "dc_out" / "clipD_depth.mp4").write_bytes(b"mp4")
texts = action(dq, "clipD", "none")
QMetaObject.invokeMethod([o for o in dq.findChildren(QObject) if o.objectName() == "jobQueueRowMenu"][0], "close"); pump(0.3)
check("Send to Splatting isn't offered on a clip not done", "Send to Splatting" not in texts, str(texts))
dq.setProperty("done", {"clipD": True}); pump(0.3)
sp_out_was = str(sst.value("output_splatted"))
texts = action(dq, "clipD", "send"); pump(0.8)
check("...on a finished (green) clip it is", texts[-1:] == ["Send to Splatting"], str(texts))
check("...the clip and its new depth map are Splatting's inputs",
      n(sst.value("input_source_clips")) == n(FIX / "src" / "clipD.mp4")
      and n(sst.value("input_depth_maps")) == n(tmp / "dc_out" / "clipD_depth.mp4"),
      f"{sst.value('input_source_clips')} | {sst.value('input_depth_maps')}")
check("...the output left as it was (no chapter)", str(sst.value("output_splatted")) == sp_out_was, str(sst.value("output_splatted")))
check("...Splatting's page showing, and a toast says so", stage.property("current") == 1
      and until(lambda: any(t[1] == "Ready in Splatting" for t in sp_toasts), 5), str(sp_toasts[-1:]))
show(0)

# ================= Inpainting V1 =================
ip = objs["Inpainting"]; ist = ip.property("settings")
ip_toasts = []
ip.toast.connect(lambda *a: ip_toasts.append(a))
show(2)
ipage = stage.property("pages").property(2).toQObject()
lo = MFX / "mask"
hires = tmp / "hires"; hires.mkdir()
shutil.copy(lo / "mA_320_splatted2.mp4", hires / "mA_1280_splatted2.mp4")
(lo / "mA_320_splatted2.spsidecar").write_text("{}", encoding="utf-8")
for k, v in {"input_folder": str(lo), "hires_blend_folder": "", "output_folder": str(tmp / "ip_out"),
             "single_clip_id": "", "move_to_finished": False}.items():
    ist.setProperty(k, v)
(tmp / "ip_out").mkdir()
ip.save()
iq = one(ipage, "ipJobQueue")
splats = sorted(p.name for p in lo.iterdir() if p.suffix in (".mp4", ".mov"))
check("Inpainting: Upcoming Jobs lists the Lo-Res splats", iq is not None and until(lambda: names(iq) == splats, 20),
      f"{names(iq) if iq else 'no list'} vs {splats}")
params = [c for c in find_all(ipage, lambda c: c.metaObject().className().startswith("Section") and c.isVisible()
                              and c.property("title") == "Parameters")][0]
check("...on the right of the settings", iq.mapToScene(QPointF(0, 0)).x() > params.mapToScene(QPointF(params.width(), 0)).x())
check("...level with the Parameters' top", abs(iq.mapToScene(QPointF(0, 0)).y() - params.mapToScene(QPointF(0, 0)).y()) < 2)
win.setHeight(win.height() + 300); pump(0.8)
ok, why = reaches_bar(ipage, iq)
check("...in a taller window it runs down to the Progress box", ok, why)
win.setHeight(win.height() - 300); pump(0.8)
pp = [c for c in find_all(ipage, lambda c: "Check" in c.metaObject().className() and c.isVisible()
                          and str(c.property("text")) in ("Enable Color Transfer",
                                                          "Keep Inpaint Cache For Re-Merge", "Resume"))]
xs = {round(c.mapToScene(QPointF(0, 0)).x()) for c in pp}
check("...Post-Processing's checkboxes in one column", len({str(c.property("text")) for c in pp}) == 3 and len(xs) == 1,
      f"{len(pp)} boxes at x {sorted(xs)}")
mB = [n for n in splats if n.startswith("mB")][0]
click(one(row_of(iq, "mB"), "jobQueueCheck"))
click(one(iq, "queueProcessSelected"))
run_to_end(ip)
check("Process Selected inpaints the ticked clip only", ip_ran == [mB], str(ip_ran))
ip_ran.clear()
texts = action(iq, "mA", "run")
check("right-click: Inpaint This Clip Now, Test Render and Move to Finished",
      texts == ["Inpaint This Clip Now", "Test Render", "Move to Finished"], str(texts))
_m = [o for o in iq.findChildren(QObject) if o.objectName() == "jobQueueRowMenu"][0]
_ex = [str(o.property("text")) for o in _m.findChildren(QObject) if o.objectName() == "jobQueueExplore"]
check("...and Open Lo-Res / Hi-Res Splat in Explorer (user, 2026-10-18)",
      _ex[:1] == ["Open Lo-Res Splat in Explorer"], str(_ex))
run_to_end(ip)
check("Inpaint This Clip Now inpaints that clip", ip_ran == ["mA_320_splatted2.mp4"], str(ip_ran) + " " + str(ip_toasts[-2:]))

# Send to Merging (user, 2026-10-11): mA is green now; its inpainted clip goes on
mg = objs["Merging"]; mst = mg.property("settings")
mg_toasts = []
mg.toast.connect(lambda *a: mg_toasts.append(a))
shutil.copy(MFX / "inpainted" / "mA_320_inpainted_right_eye.mp4", tmp / "ip_out" / "mA_320_inpainted_right_eye.mp4")
texts = action(iq, "mB", "none")
QMetaObject.invokeMethod([o for o in iq.findChildren(QObject) if o.objectName() == "jobQueueRowMenu"][0], "close"); pump(0.3)
check("Send to Merging isn't offered on a clip not done this run", "Send to Merging" not in texts, str(texts))
# the stand-in render never names its clip, so the row's green is stood in for too
iq.setProperty("done", {"mA_320_splatted2": True}); pump(0.3)
texts = action(iq, "mA", "send"); pump(0.8)
check("...on the finished (green) clip it is", texts[-1:] == ["Send to Merging"], str(texts))
check("...its inpainted clip is Merging's input",
      n(mst.value("inpainted_folder")) == n(tmp / "ip_out" / "mA_320_inpainted_right_eye.mp4"), str(mst.value("inpainted_folder")))
check("...Merging's page showing, and a toast says so", stage.property("current") == 3
      and until(lambda: any(t[1] == "Ready in Merging" for t in mg_toasts), 5), str(mg_toasts[-1:]))
show(2)
ist.setProperty("hires_blend_folder", str(hires)); ip.save(); pump(0.5)
texts = play(iq, "mA", "Play Hi-Res Splat")
check("Inpainting: Play Lo-Res Splat and Play Hi-Res Splat", texts == ["Play Lo-Res Splat", "Play Hi-Res Splat"], str(texts))
check("...Play Hi-Res Splat opens the clip's Hi-Res splat", played[-1:] == [n(hires / "mA_1280_splatted2.mp4")], str(played[-1:]))
action(iq, "mA", "finished")
check("Move to Finished moves the splat, its .spsidecar and its Hi-Res splat into finished/",
      until(lambda: (lo / "finished" / "mA_320_splatted2.mp4").exists(), 20)
      and (lo / "finished" / "mA_320_splatted2.spsidecar").exists()
      and (hires / "finished" / "mA_1280_splatted2.mp4").exists())
check("...and it leaves the list", until(lambda: not any(n.startswith("mA") for n in names(iq)), 20), str(names(iq)))

check("no QML warnings", not warns, str(warns[:3]))
h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
check("the real last_jobs.json untouched", h0 == h1)
print("PASS" if not fails else f"FAIL ({len(fails)})", fails)
_fixtures.exit_now(0 if not fails and h0 == h1 else 1)
