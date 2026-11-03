"""Upcoming Jobs' Refresh (a tester, 2026-11-02): the list read its folders only when a folder
setting changed, Load Preview was pressed or a job ended, so a clip copied in from outside Studio
didn't show until one of those. Every stage's list now has a Refresh on its heading line.

On each stage with a list (DepthCrafter, Splatting, Inpainting V1 and V2, Merging), in the real app
offscreen over TEMP copies of the small and merge fixtures: the list shows its clips; a clip
dropped into the input folder is not listed on its own; Refresh lists it. Configs and
last_jobs.json redirected.
"""
import os, sys, tempfile, hashlib, time, shutil
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
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, QPoint, QPointF, Qt, qInstallMessageHandler
from PySide6.QtTest import QTest
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from studio import app as A

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
objs["Shell"].setProperty("uiScale", "1.0")
win.show()
win.setProperty("userSized", True); win.setWidth(1500); win.setHeight(1100)
fails = []
def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok: fails.append(label)
def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.005)
def until(cond, sec=20):
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
def click(item):
    c = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    p = QPoint(int(c.x()), int(c.y()))
    QTest.mouseMove(win, p); pump(0.2)
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, p); pump(0.3)
def names(q):
    return [str(x) for x in (q.property("src").property("clips") or [])]
stage = find_all(win.contentItem(), lambda c: c.objectName() == "stage")[0]
def page_of(i, engine_ix=None):
    QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", i), Q_ARG("QVariant", True)); pump(1.0)
    page = stage.property("pages").property(i).toQObject()
    if engine_ix is not None:
        page.setProperty("engine", engine_ix); pump(0.8)
    return page
def video(src, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dest)
clipA = FIX / "src" / "clipA.mp4"

def run(title, page, qname, want, drop, added):
    q = one(page, qname)
    check(f"{title}: its Upcoming Jobs list", q is not None, qname)
    if q is None:
        return
    check(f"...lists the input's clips", until(lambda: names(q) == want), str(names(q)))
    pump(1.5)                   # any scan the settings started has landed
    drop()
    pump(1.5)
    check(f"...a clip copied in from outside isn't listed on its own", added not in names(q), str(names(q)))
    btn = one(q, "queueRefresh")
    check(f"...a Refresh on the list's heading", btn is not None and btn.property("enabled"))
    if btn is None:
        return
    click(btn)
    check(f"...Refresh lists it", until(lambda: added in names(q)), str(names(q)))

pump(0.6)
# ---- DepthCrafter
dc = objs["DepthCrafter"]; dst = dc.property("settings")
page = page_of(0)
for k, v in {"input_dir_or_file_var": str(FIX / "src"), "output_dir": str(tmp / "dc_out")}.items():
    dst.setProperty(k, v)
dc.save()
DSRC = tmp / "dcsrc"; video(clipA, DSRC / "clipA.mp4")
dst.setProperty("input_dir_or_file_var", str(DSRC)); dc.save()
run("DepthCrafter", page, "dcJobQueue", ["clipA.mp4"],
    lambda: video(clipA, DSRC / "clipC.mp4"), "clipC.mp4")

# ---- Splatting
sp = objs["Splatting"]
SS, SD = tmp / "ssrc", tmp / "sdep"
video(clipA, SS / "clipA.mp4"); video(FIX / "depth" / "clipA_depth.mp4", SD / "clipA_depth.mp4")
for k, v in {"input_source_clips": str(SS), "input_depth_maps": str(SD), "output_splatted": str(tmp / "splats"),
             "multi_map": False}.items():
    sp._set(k, v)
sp.save()
page = page_of(1)
run("Splatting", page, "splatJobQueue", ["clipA.mp4"],
    lambda: (video(clipA, SS / "clipC.mp4"), video(FIX / "depth" / "clipA_depth.mp4", SD / "clipC_depth.mp4")),
    "clipC.mp4")

# ---- Inpainting V1 and V2
LOW = tmp / "splats" / "lowres"
video(clipA, LOW / "clipA_640_splatted2.mp4")
for title, engine_ix, ctl_name, qname, added in (("Inpainting", 0, "Inpainting", "ipJobQueue", "clipC_640_splatted2.mp4"),
                                                 ("Inpainting V2", 1, "InpaintingV2", "v2JobQueue",
                                                  "clipD_640_splatted2.mp4")):
    ip = objs[ctl_name]; st = ip.property("settings")
    st.setProperty("input_folder", str(LOW)); st.setProperty("output_folder", str(tmp / "inpainted"))
    ip.save()
    page = page_of(2, engine_ix)
    have = sorted(p.name for p in LOW.glob("*.mp4"))
    run(title, page, qname, have, lambda added=added: video(clipA, LOW / added), added)

# ---- Merging
mg = objs["Merging"]; st = mg.property("settings")
page = page_of(3)
for k, v in {"inpainted_folder": str(MFX / "inpainted"), "original_folder": str(MFX / "orig"),
             "mask_folder": str(MFX / "mask"), "output_folder": str(tmp / "merged")}.items():
    st.setProperty(k, v)
pump(0.3)
mq = next((n for n in ("jobQueue", "jobQueueCompact") if one(page, n) is not None), "jobQueue")
def drop_merge():
    video(MFX / "inpainted" / "mA_320_inpainted_right_eye.mp4", MFX / "inpainted" / "mC_320_inpainted_right_eye.mp4")
    video(MFX / "mask" / "mA_320_splatted2.mp4", MFX / "mask" / "mC_320_splatted2.mp4")
run("Merging", page, mq, ["mA_320_inpainted_right_eye.mp4", "mB_320_inpainted_right_eye.mp4"], drop_merge,
    "mC_320_inpainted_right_eye.mp4")

h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
check("last_jobs.json untouched", h0 == h1)
bad = [w for w in warns if "Refresh" in w or "JobQueue" in w]
check("no QML warnings from the list", not bad, str(bad[:3]))
print("FAILS", len(fails))
sys.stdout.flush()
os._exit(1 if fails else 0)
