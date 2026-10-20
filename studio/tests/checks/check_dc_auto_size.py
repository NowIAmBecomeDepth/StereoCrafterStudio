"""DepthCrafter's Auto Size (user, 2026-10-05), in the real app, offscreen.

Loading a clip fills Target Width/Height with its aspect ratio's pick (768
wide, the multiple-of-64 height nearest the ratio, unless Settings >
DepthCrafter picks another for that ratio); the Resolution Preset menu then
shows only that shape's sizes, the pick marked 'auto'; Auto Size off leaves
the size alone. In a batch, a clip of another shape runs at its own shape's
pick. Synthetic clips of three shapes, made here; a temp config.
"""
import os, sys, tempfile, hashlib, time, json, subprocess
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
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, QObject, QPoint, QPointF, Q_ARG, Qt, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from studio import app as A
from studio.stages.depthcrafter import settings as S
from studio.stages.depthcrafter import runner as R

fails = []


def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok:
        fails.append(label)


# ---- clips of three shapes (synthetic) ------------------------------------------
clips = tmp / "clips"
clips.mkdir()


def make(name, w, h, sar="1"):
    out = clips / name
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={w}x{h}:rate=24:duration=0.5",
                    "-vf", f"setsar={sar}", "-pix_fmt", "yuv420p", str(out)], check=True)
    return out


scope = make("scope.mp4", 960, 400)          # 2.40
wide = make("wide.mp4", 640, 360)            # 16:9
four3 = make("four3.mp4", 480, 360)          # 4:3
anam = make("anamorphic.mp4", 720, 480, "32/27")   # 16:9 by its pixel aspect

# ---- the rules ------------------------------------------------------------------
check("1920x802 is 2.39:1", S.ratio_class(1920 / 802) == "2.39:1", S.ratio_class(1920 / 802))
check("auto picks 768 wide, the nearest multiple of 64 high",
      [S.default_auto_size(r) for r in ("2.39:1", "16:9", "4:3")] == [(768, 320), (768, 448), (768, 576)],
      str([S.default_auto_size(r) for r in ("2.39:1", "16:9", "4:3")]))
check("a probe reads the pixel aspect too (720x480 at 32:27 is 16:9)",
      S.ratio_class(S.video_ratio(str(anam))) == "16:9", str(S.video_ratio(str(anam))))
check("Auto Size is off by default", S.DEFAULTS["auto_resolution_var"] is False)

# ---- the app --------------------------------------------------------------------
qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
objs["Shell"].setProperty("uiScale", "1.0")
win = engine.rootObjects()[0]
win.resize(1400, 950); win.show()
dc = objs["DepthCrafter"]
st = dc.property("settings")


def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.005)


def walk(it, out):
    for c in it.childItems():
        out.append(c); walk(c, out)
    return out


def everything():
    root = win.contentItem()
    while root.parentItem():
        root = root.parentItem()
    return walk(root, [])


def named(n):
    return [c for c in everything() if c.objectName() == n and c.isVisible()]


def into_view(item):
    """Scroll the Flickable holding `item` (Settings' panel) to show it."""
    it = item.parentItem()
    while it is not None and not it.metaObject().className().startswith("QQuickFlickable"):
        it = it.parentItem()
    if it is not None:
        y = item.mapToItem(it.property("contentItem"), QPointF(0, 0)).y()
        it.setProperty("contentY", max(0.0, min(y - 120, it.property("contentHeight") - it.height())))
        pump(0.3)


def click(item):
    into_view(item)
    p = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(p.x()), int(p.y()))); pump(0.4)


def size():
    return int(st.property("target_width")), int(st.property("target_height"))


def load(path, wait=8.0):
    """As the user does: a write to the input box (QML's write path)."""
    st.setProperty("input_dir_or_file_var", str(path))
    end = time.time() + wait
    while time.time() < end:
        pump(0.1)
        if dc._shape_path == str(path) and dc.property("loadedShape"):
            pump(0.2)
            return


pump(1.0)
stage = named("stage")[0]
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 0), Q_ARG("QVariant", True)); pump(0.8)
dpage = stage.property("pages").property(0).toQObject()
check("the page has the Auto Size switch",
      any(c.property("text") == "Auto Size for the Loaded Clip" and c.property("key") == "auto_resolution_var"
          for c in walk(dpage, [])))
# nothing is built in (user, 2026-10-11): this temp config has no presets of its own
check("nothing loaded: no columns, no note", dc.property("presets") == [] and dc.property("presetNote") == "",
      str([c["category"] for c in dc.property("presets")]))

st.setProperty("target_width", 1152); st.setProperty("target_height", 512); pump(0.2)
check("the switch starts off", st.property("auto_resolution_var") is False)
st.setProperty("auto_resolution_var", True); pump(0.2)
load(scope)
check("loading a 2.40 clip: its shape is found", dc.property("loadedShape") == "2.39:1", dc.property("loadedShape"))
check("...and Target Width/Height become 768x320", size() == (768, 320), str(size()))
cols = dc.property("presets")
check("...the menu shows only that shape's column (its auto pick)", [c["category"] for c in cols] == ["2.39:1"],
      str([c["category"] for c in cols]))
check("...the pick marked auto", any(i["w"] == 768 and i["h"] == 320 and i["ratio"].startswith("auto") for i in cols[0]["items"]),
      str(cols[0]["items"]))
check("...and a note says whose sizes they are", "2.39:1" in dc.property("presetNote"), dc.property("presetNote"))

# the menu itself
dbtn = [c for c in walk(dpage, []) if c.metaObject().className().startswith("PresetButton")][0]
click([c for c in walk(dbtn, []) if c.metaObject().className().startswith("AccentButton")][0]); pump(0.5)
notes = [c for c in everything() if c.objectName() == "presetNote" and c.isVisible()]
check("the open menu shows the note", len(notes) == 1 and "2.39:1" in notes[0].property("text"))
cells = [c.property("text") for c in everything() if c.metaObject().className().startswith("QQuickText") and c.isVisible()
         and "×" in str(c.property("text")) and c.objectName() != "presetNote"]
check("...and only 2.40 sizes, 768×320 marked auto", any(t.startswith("768×320  (auto") for t in cells)
      and not any("×448" in t or "×576" in t for t in cells), str(cells))
for p in [o for o in dbtn.findChildren(QObject) if o.metaObject().className().startswith("QQuickPopup")]:
    QMetaObject.invokeMethod(p, "close")
pump(0.3)

load(wide)
check("loading a 16:9 clip: 768x448, the 16:9 column only",
      size() == (768, 448) and [c["category"] for c in dc.property("presets")] == ["16:9"],
      f"{size()} {[c['category'] for c in dc.property('presets')]}")

# Settings > DepthCrafter: a different pick for 2.39:1
dlg = win.findChild(QObject, "settingsDialog")
QMetaObject.invokeMethod(dlg, "open"); pump(0.5)
dlg.setProperty("current", 0); pump(0.6)          # the stage's section: first
ed = named("dcPresetEditor")[0]
ratio = named("dcPresetRatio")[0]
model = ratio.property("model"); model = model.toVariant() if hasattr(model, "toVariant") else model
QMetaObject.invokeMethod(ratio, "activated", Q_ARG(int, list(model).index("2.39:1"))); pump(0.3)
auto = named("dcPresetAuto")[0]
amodel = auto.property("model"); amodel = list(amodel.toVariant() if hasattr(amodel, "toVariant") else amodel)
check("the editor shows 2.39:1's pick, 768×320", amodel[auto.property("currentIndex")].startswith("768×320"),
      str(amodel[auto.property("currentIndex")] if auto.property("currentIndex") >= 0 else None))
i960 = next(i for i, t in enumerate(amodel) if t.startswith("960×384"))
QMetaObject.invokeMethod(auto, "activated", Q_ARG(int, i960)); pump(0.3)
check("...picking 960×384 makes it dirty", ed.property("dirty") is True)
click(named("dcPresetSave")[0]); pump(0.5)
f = ss.CONFIG_DIR / S.AUTO_SIZES_FILE
data = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
check("Save writes only the changed pick", data == {"auto_sizes": {"2.39:1": [960, 384]}}, str(data))
QMetaObject.invokeMethod(dlg, "close"); pump(0.4)

load(scope)
check("loading the 2.40 clip again: 960x384", size() == (960, 384), str(size()))
check("...marked auto in the menu", any(i["w"] == 960 and i["ratio"].startswith("auto") for c in dc.property("presets") for i in c["items"]),
      str(dc.property("presets")))

st.setProperty("auto_resolution_var", False); pump(0.2)
st.setProperty("target_width", 640); st.setProperty("target_height", 384); pump(0.2)
load(four3)
check("Auto Size off: the size stays", size() == (640, 384), str(size()))
check("...but the menu still narrows to 4:3", [c["category"] for c in dc.property("presets")] == ["4:3"],
      str([c["category"] for c in dc.property("presets")]))
st.setProperty("auto_resolution_var", True); pump(0.3)
check("switching it back on applies the pick at once", size() == (768, 576), str(size()))

# ---- the runner: a mixed folder -----------------------------------------------------
opts = S.snapshot(dict(S.DEFAULTS, target_width=1024, target_height=448, auto_resolution_var=True))
opts.update(auto_loaded_class="16:9", auto_sizes=S.auto_sizes(ss.CONFIG_DIR))
run = R.DepthCrafterRunner(opts, R.Hooks(), __import__("threading").Event())
spec = lambda p: {"path": str(p), "basename": p.stem, "type": "video_file"}  # noqa: E731
check("runner: a clip of the loaded shape runs at Target Width/Height",
      run._size_for_source(spec(wide), (1024, 448)) == (1024, 448))
check("...a 2.40 clip at 2.39:1's pick", run._size_for_source(spec(scope), (1024, 448)) == (960, 384),
      str(run._size_for_source(spec(scope), (1024, 448))))
check("...a 4:3 clip at 4:3's", run._size_for_source(spec(four3), (1024, 448)) == (768, 576))
opts["auto_resolution_var"] = False
check("...Auto Size off: everything at Target Width/Height", run._size_for_source(spec(scope), (1024, 448)) == (1024, 448))

studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
