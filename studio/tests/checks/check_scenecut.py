"""Scene Cut (Extra tab) in the real app, offscreen: the page is the real one
and fits; the engine loads on first view and the detector's own fields
appear (and follow a detector change); and a REAL run started by mouse -- v1's AutoShot over a
synthetic three-shot clip -- writes the CSV and three split clips, reports
v1's "Done!" line and the Success dialog, and records the handoff (to the
redirected record). Configs and last_jobs.json redirected; the clip is built
from ffmpeg test sources in a temp folder."""
import os, sys, tempfile, hashlib, time, subprocess, csv
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

# Three shots, 1 s each: three very different test sources, cut hard.
clip = tmp / "shots.mp4"
srcs = ["testsrc2=size=320x180:rate=24:duration=1", "mandelbrot=size=320x180:rate=24",
        "smptebars=size=320x180:rate=24:duration=1"]
subprocess.run(["ffmpeg", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", srcs[0], "-f", "lavfi", "-t", "1", "-i", srcs[1], "-f", "lavfi", "-i", srcs[2],
                "-filter_complex", "[0][1][2]concat=n=3:v=1:a=0,format=yuv420p", "-c:v", "libx264", str(clip)], check=True)

_src = (Path(__file__).with_name("measure_v2_width.py")).read_text(encoding="utf-8")
exec(_src[_src.index("def eff_visible"):_src.index("def ab_overlaps")])
exec(_src[_src.index("def ab_overlaps"):_src.index("def audit")])
exec(_src[_src.index("def audit"):_src.index("names = {")])

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
def click_in(item):
    QTest.mouseMove(win, QPoint(5, 5)); pump(0.1)
    c = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(c.x()), int(c.y())))

pump(0.6)
sc = objs["SceneCut"]; st = sc.property("settings")
stage = find(win.contentItem(), lambda c: c.objectName() == "stage")
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 4), Q_ARG("QVariant", True)); pump(1.0)
extra = stage.property("pages").property(4).toQObject()
check("the Extra tab is the real one", extra.metaObject().className().startswith("ExtraPage"))
check("its name is v1's", win.property("stageNames").toVariant()[4] == "Extra" if hasattr(win.property("stageNames"), "toVariant")
      else list(win.property("stageNames"))[4] == "Extra")
page = find(extra, lambda c: c.objectName() == "sceneCutPage")
check("Scene Cut shows first", page is not None and page.isVisible())
check("the engine loads on first view", until(lambda: sc.property("ready"), 60), sc.property("loadError"))
for k, v in {"detector_type": "AutoShot", "video_path": str(clip).replace("\\", "/"),
             "output_dir": str(tmp / "out").replace("\\", "/"), "export_csv": True, "split_video": True,
             "split_by_chapter": False, "save_images": False, "export_html": False, "export_sc": False,
             "refinements_enabled": True, "snap_cuts": True, "ai_validate": False}.items():
    st.setProperty(k, v)
pump(0.5)
params = [p["key"] for p in sc.property("detectorParams")]
check("AutoShot's own fields", params == ["device", "threshold", "min_scene_len"], str(params))
st.setProperty("detector_type", "OmniShotCut"); pump(0.3)
params = [p["key"] for p in sc.property("detectorParams")]
check("...and OmniShotCut's after a switch", "mode" in params and "threshold" not in params, str(params))
st.setProperty("detector_type", "AutoShot"); pump(0.3)
sc.setDetectorParam("min_scene_len", "6")

# ---- fit
w = extra.property("requiredWidth")
win.setProperty("width", w); pump(0.8)
probs = audit(page) + ab_overlaps(page)
check(f"no clipping / overlaps at {w}", not probs, str(probs[:6]))
content = find(page, lambda c: c.objectName() == "pageContent")
ab = find(page, lambda c: c.objectName() == "actionBar")
gap = ab.mapToItem(page, QPointF(0, 0)).y() - content.mapToItem(page, QPointF(0, content.height())).y()
check("content clears the action bar", gap >= 0, f"gap {gap:.0f}")
min_h = win.property("minimumHeight")
check("fits the 1080p screen at 0.85", min_h * 0.85 <= 1000, f"{min_h} -> {min_h * 0.85:.0f}")

# ---- a real run, by mouse
prompt = extra.property("prompt")
click_in(find(page, lambda c: c.objectName() == "scStart"))
check("Start runs", until(lambda: sc.property("busy"), 10))
check("...and finishes", until(lambda: not sc.property("busy"), 300), sc.property("status"))
check("v1's finish line", "Done!" in sc.property("status"), sc.property("status"))
check("v1's Success dialog", until(lambda: prompt.property("opened") and prompt.property("title") == "Success", 10),
      prompt.property("title"))
QMetaObject.invokeMethod(prompt, "finish", Q_ARG("QVariant", 1)); pump(0.6)
out = tmp / "out"
csvs = list(out.rglob("*.csv"))
rows = list(csv.reader(open(csvs[0], encoding="utf-8"))) if csvs else []
clips = sorted(p.name for p in out.rglob("*.mp4"))
check("the CSV lists three scenes", len(rows) >= 4, f"{len(rows)} rows in {csvs}")
check("three split clips", len(clips) == 3, str(clips))
check("handoff recorded (temp)", '"scenecut"' in (tmp / "last_jobs.json").read_text() if (tmp / "last_jobs.json").exists() else False)
check("the model stays cached (v1)", sc.holds_gpu())
sc.releaseGpu(); pump(0.2)
check("Release GPU drops it", not sc.holds_gpu())

sc.shutdown()
studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
