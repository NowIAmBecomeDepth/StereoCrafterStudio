"""Resolution presets in the real app, offscreen (user, 2026-10-11): nothing is
built in -- Splatting's Low Res menu shows this machine's own columns
(config/splatting_presets.json), in multiples of 16, with each saved tag;
DepthCrafter's says where to make some when it has none, and a column of its
own scrolls inside itself. Splatting's Auto Size for the Loaded Clip fills
Low Res Width/Height with the loaded clip's shape's pick and narrows the menu
to it; Settings > Splatting carries the Custom Resolution Presets editor in
multiples of 16; the clip list names each clip's shape. Configs and
last_jobs.json redirected; the clips are the synthetic fixtures.

    python studio/tests/checks/check_splat_presets.py [shot.png]
"""
import os, sys, tempfile, hashlib, time, json
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _fixtures  # noqa: E402
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
ss.CONFIG_DIR.mkdir(parents=True)
(ss.CONFIG_DIR / "splatting_presets.json").write_text(
    json.dumps({"presets": [{"category": "2.2:1", "sizes": [[848, 384, "480p"], [1280, 576, "720p"]]}]}), "utf-8")
fx = _fixtures.copy("small", tmp / "fx")
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, QObject, QPoint, Qt, Q_ARG
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
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
def until(fn, sec=10):
    end = time.time() + sec
    while time.time() < end and not fn():
        pump(0.1)
    return fn()
def walk(it, out):
    for c in it.childItems(): out.append(c); walk(c, out)
    return out
def click(item):
    p = item.mapToScene(item.boundingRect().center())
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(p.x()), int(p.y()))); pump(0.5)
def cells():
    return [c for c in walk(win.contentItem(), []) if c.metaObject().className().startswith("QQuickRectangle")
            and c.property("isCurrent") is not None and c.isVisible()]
def close_popups(item):
    for p in [o for o in item.findChildren(QObject) if o.metaObject().className().startswith("QQuickPopup")]:
        QMetaObject.invokeMethod(p, "close")
    pump(0.3)

pump(0.6)
stage = [c for c in walk(win.contentItem(), []) if c.objectName() == "stage"][0]
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 1), Q_ARG("QVariant", True)); pump(1.0)
sp = objs["Splatting"]
# Low Res on: its preset button is disabled while Low Res is off (the default)
sp._set("enable_low_res", True); pump(0.5)
page = stage.property("pages").property(1).toQObject()
st = page.property("st")
btn = [c for c in walk(page, []) if c.objectName() == "splatPresetButton"]
check("Splatting has DepthCrafter's preset button", len(btn) == 1)
btn = btn[0]
cols = sp.property("presets")
check("nothing built in: only this machine's own column", [c["category"] for c in cols] == ["2.2:1"],
      [c["category"] for c in cols])
check("...with its saved tags", [i["ratio"] for i in cols[0]["items"]] == ["480p", "720p"], cols[0]["items"])
btn_inner = [c for c in walk(btn, []) if c.metaObject().className().startswith("AccentButton")][0]
click(btn_inner)
cs = cells()
check("the menu opens with its two sizes", len(cs) == 2, len(cs))
if len(sys.argv) > 1:
    win.grabWindow().save(sys.argv[1])
pick = [c for c in cs if c.property("modelData")["w"] == 1280][0]
click(pick)
check("picking fills Width / Height", (str(st.value("pre_res_width")), str(st.value("pre_res_height"))) == ("1280", "576"),
      (st.value("pre_res_width"), st.value("pre_res_height")))
check("the button names it", btn.property("current") == "1280×576", btn.property("current"))
close_popups(btn)

# Auto Size: a 2.39:1 clip loaded (its shape as the probe would report it)
sp._set("low_res_auto_size", True); pump(0.3)
sp._shape_path = "clip.mp4"
sp._on_shape("clip.mp4", "2.39:1"); pump(0.4)
check("Auto Size: a 2.39:1 clip sets its pick, 1840x768",
      (str(st.value("pre_res_width")), str(st.value("pre_res_height"))) == ("1840", "768"),
      (st.value("pre_res_width"), st.value("pre_res_height")))
cols = sp.property("presets")
check("...and the menu narrows to its shape, the pick marked 'auto'",
      [c["category"] for c in cols] == ["2.39:1"] and cols[0]["items"][0]["ratio"].startswith("auto"), cols)
check("...and the checkbox names the shape", any(c.objectName() == "splatAutoSize" and "(2.39:1)" in str(c.property("text"))
                                                 for c in walk(page, [])))
fn = sp._size_for_clip_fn({})
check("in a batch: a clip of another shape gets its own pick, one of the loaded shape none",
      fn(str(fx / "src" / "clipA.mp4")) == (1360, 768) and sp._shape == "2.39:1", fn(str(fx / "src" / "clipA.mp4")))

# the clip list: each clip's shape
sp._set("input_source_clips", str(fx / "src")); sp._set("input_depth_maps", str(fx / "depth")); pump(0.2)
sp.save(); pump(0.2)
q = sp.preview.queue
q.rescan()
check("the clip list names each clip's shape", until(lambda: q.property("clipRatios") == ["16:9", "16:9"], 15),
      q.property("clipRatios"))
pump(0.6)
texts = [str(c.property("text")) for c in walk(page, []) if c.objectName() == "jobQueueFrames" and c.isVisible()]
check("...beside its frames", any(t.endswith("·  16:9") for t in texts), texts)

# the real path: the preview loads a 16:9 clip -> its shape is read -> Auto Size applies
sp.preview.load()
check("loading the preview reads the clip's shape", until(lambda: sp._shape == "16:9", 60), sp._shape)
check("...and Auto Size fills its pick, 1360x768",
      until(lambda: (str(st.value("pre_res_width")), str(st.value("pre_res_height"))) == ("1360", "768"), 5),
      (st.value("pre_res_width"), st.value("pre_res_height")))

# Settings > Splatting: the editor, in multiples of 16
dlg = win.findChild(QObject, "settingsDialog")
QMetaObject.invokeMethod(dlg, "open"); pump(0.6)
dlg.setProperty("current", 0); pump(0.6)
eds = [o for o in win.contentItem().findChildren(QObject) if o.objectName() == "dcPresetEditor"]
eds = eds or [c for c in walk(win.contentItem(), []) if c.objectName() == "dcPresetEditor"]
check("Settings > Splatting has the Custom Resolution Presets editor", len(eds) == 1 and eds[0].property("stageName") == "Splatting",
      len(eds))
if eds:
    check("...in multiples of 16", "multiple of 16" in str(eds[0].property("intro")))
    ed = eds[0]
    keys = [s["key"] for s in sp.customSizes("2.2:1")]
    check("...four suggestions plus the saved sizes and the Auto pick", keys == ["848x384", "1120x512", "1280x576",
          "1408x640", "1696x768", "2112x960"], keys)
    # a size typed in by hand: ticked and listed; one that isn't a multiple of 16 refused
    ed.setProperty("ratio", "2.2:1"); pump(0.3)
    QMetaObject.invokeMethod(ed, "addTyped", Q_ARG("QVariant", "1008"), Q_ARG("QVariant", "464"))
    QMetaObject.invokeMethod(ed, "addTyped", Q_ARG("QVariant", "1001"), Q_ARG("QVariant", "464")); pump(0.3)
    picked = ed.property("picked").toVariant() if hasattr(ed.property("picked"), "toVariant") else ed.property("picked")
    check("a typed size (1008x464) is ticked; 1001x464 (not a multiple of 16) refused",
          "1008x464" in picked.get("2.2:1", []) and "1001x464" not in picked.get("2.2:1", []), picked)
    save = [c for c in walk(win.contentItem(), []) if c.objectName() == "dcPresetSave"][0]
    QMetaObject.invokeMethod(save, "clicked"); pump(0.5)        # below the dialog's fold: no mouse
    saved = json.loads((ss.CONFIG_DIR / "splatting_presets.json").read_text("utf-8"))
    check("...and saved with the column's tags kept", saved["presets"] == [{"category": "2.2:1", "sizes":
          [[848, 384, "480p"], [1008, 464], [1280, 576, "720p"]]}], saved)
    # Remove All Presets: a second click confirms
    rm = [c for c in walk(win.contentItem(), []) if c.objectName() == "dcPresetRemoveAll"][0]
    QMetaObject.invokeMethod(rm, "clicked"); pump(0.3)
    check("Remove All Presets asks for a second click", "Again" in str(rm.property("text"))
          and len(json.loads((ss.CONFIG_DIR / "splatting_presets.json").read_text("utf-8"))["presets"]) == 1)
    QMetaObject.invokeMethod(rm, "clicked"); pump(0.5)
    check("...and the second empties the menu (the loaded clip's Auto pick alone left)",
          json.loads((ss.CONFIG_DIR / "splatting_presets.json").read_text("utf-8"))["presets"] == []
          and len(sp.property("presets")) == 1 and sp.property("presets")[0]["items"][0]["ratio"].startswith("auto"),
          sp.property("presets"))
QMetaObject.invokeMethod(dlg, "close"); pump(0.5)

# DepthCrafter: nothing built in either -- the empty menu says where to make some
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 0), Q_ARG("QVariant", True)); pump(1.0)
dc = objs["DepthCrafter"]
dpage = stage.property("pages").property(0).toQObject()
dbtn = [c for c in walk(dpage, []) if c.metaObject().className().startswith("PresetButton")][0]
check("DepthCrafter: no columns", dc.property("presets") == [], dc.property("presets"))
click([c for c in walk(dbtn, []) if c.metaObject().className().startswith("AccentButton")][0])
empty = [c for c in walk(win.contentItem(), []) if c.objectName() == "presetEmpty" and c.isVisible()]
check("...and its menu says where to make some", len(empty) == 1 and "Settings > DepthCrafter" in str(empty[0].property("text")))
close_popups(dbtn)
# a column of five scrolls inside itself, three showing
(ss.CONFIG_DIR / "depthcrafter_presets.json").write_text(
    json.dumps({"presets": [{"category": "4:3", "sizes": [[512, 384], [640, 448], [768, 576], [960, 704], [1152, 896]]}]}),
    "utf-8")
dc.presetsChanged.emit(); pump(0.4)
click([c for c in walk(dbtn, []) if c.metaObject().className().startswith("AccentButton")][0])
lists = [c for c in walk(win.contentItem(), []) if c.objectName() == "presetSizes" and c.isVisible()]
check("a five-size column shows three and scrolls",
      len(lists) == 1 and abs(lists[0].height() - (3 * 30 - 2)) < 1 and lists[0].property("interactive") is True,
      str([(l.property("count"), l.height()) for l in lists]))
small = [c for c in cells() if c.property("modelData")["w"] == 640]
if small:
    click(small[0])
    dst = dpage.property("st")
    check("...and picking fills Target Width / Height", (dst.value("target_width"), dst.value("target_height")) == (640, 448),
          (dst.value("target_width"), dst.value("target_height")))
else:
    check("...640x448 is there", False)

h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
print("last_jobs unchanged:", h0 == h1, flush=True)
print("FAILS", len(fails), fails, flush=True)
# through the fixtures: its temp folders removed, and no DLL teardown crash after a green
# run (bug sweep 2026-10-09, A12)
_fixtures.exit_now(1 if fails or h0 != h1 else 0)
