"""DepthCrafter's custom resolution presets (user, 2026-10-05), in the real
app, offscreen: Settings > DepthCrafter has an aspect-ratio drop-down and a
drop-down of sizes to tick (multiples of 64, up to ~1080p, each with how far
it is from the true ratio); Save writes config/depthcrafter_presets.json
(a temp config here) and the sizes show in the Resolution Preset menu with
that difference; unticking and saving takes them out again.
"""
import os, sys, tempfile, hashlib, time, json
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

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
objs["Shell"].setProperty("uiScale", "1.0")
win = engine.rootObjects()[0]
win.resize(1400, 950); win.show()
fails = []


def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok:
        fails.append(label)


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


def open_sizes():
    # scroll Settings' panel to the editor (it is below the fold in this window), then click the box
    box = named("dcPresetSizesBox")[0]
    it = box.parentItem()
    while it is not None and not it.metaObject().className().startswith("QQuickFlickable"):
        it = it.parentItem()
    if it is not None:
        y = box.mapToItem(it.property("contentItem"), QPointF(0, 0)).y()
        it.setProperty("contentY", max(0.0, min(y - 120, it.property("contentHeight") - it.height())))
        pump(0.3)
    click(box)


def click(item):
    p = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(p.x()), int(p.y()))); pump(0.4)


pump(1.0)
stage = named("stage")[0]
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 0), Q_ARG("QVariant", True)); pump(0.8)
dlg = win.findChild(QObject, "settingsDialog")
QMetaObject.invokeMethod(dlg, "open"); pump(0.5)
dlg.setProperty("current", 0); pump(0.6)          # the stage: DepthCrafter, Studio, Appearance, Shortcuts, GPU
ed = named("dcPresetEditor")
check("Settings > DepthCrafter has the custom presets editor", len(ed) == 1)
ed = ed[0]
ratio = named("dcPresetRatio")[0]
model = ratio.property("model")
model = model.toVariant() if hasattr(model, "toVariant") else model
check("...an aspect-ratio drop-down of the common ratios", "4:3" in model and "2.39:1" in model and "16:9" in model, str(model))
i43 = list(model).index("4:3")
QMetaObject.invokeMethod(ratio, "activated", Q_ARG(int, i43)); pump(0.3)
check("...picking 4:3", ed.property("ratio") == "4:3")
open_sizes(); pump(0.6)
rows = named("dcPresetSizeRow")
keys = [r.property("modelData").toVariant()["key"] if hasattr(r.property("modelData"), "toVariant") else r.property("modelData")["key"] for r in rows]
check("...the sizes drop-down lists multiples of 64 up to ~1080p", len(rows) > 10 and all(
      int(k.split("x")[0]) % 64 == 0 and int(k.split("x")[1]) % 64 == 0 and int(k.split("x")[1]) <= 1088 for k in keys), str(keys[:6]))


def show_row(key):
    lst = named("dcPresetSizeList")[0]
    m = lst.property("model")
    m = m.toVariant() if hasattr(m, "toVariant") else m
    i = [x["key"] for x in m].index(key)
    QMetaObject.invokeMethod(lst, "positionViewAtIndex", Q_ARG(int, i), Q_ARG(int, 1))   # ListView.Beginning
    pump(0.3)
    return row(key)


def row(key):
    return [r for r in named("dcPresetSizeRow") if (r.property("modelData").toVariant() if hasattr(r.property("modelData"), "toVariant") else r.property("modelData"))["key"] == key][0]


texts = [c.property("text") for c in walk(row("640x448"), []) if c.metaObject().className().startswith("QQuickText")]
check("...each with how far it is from the true ratio", "(7.1% wider)" in texts, str(texts))
texts = [c.property("text") for c in walk(row("768x576"), []) if c.metaObject().className().startswith("QQuickText")]
check("...an exact one says so", "(exact)" in texts, str(texts))
click(show_row("640x448")); click(show_row("768x576"))
check("ticking two", ed.property("pickedHere") == 2 and ed.property("dirty") is True, str(ed.property("pickedHere")))
for p in [o for o in ed.findChildren(QObject) if o.objectName() == "dcPresetSizesPopup"]:
    QMetaObject.invokeMethod(p, "close")
pump(0.3)
click(named("dcPresetSave")[0]); pump(0.5)
f = ss.CONFIG_DIR / "depthcrafter_presets.json"
data = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
check("Save writes them to config/depthcrafter_presets.json",
      data.get("presets") == [{"category": "4:3", "sizes": [[640, 448], [768, 576]]}], str(data))
dc = objs["DepthCrafter"]
cols = dc.property("presets")
mine = [c for c in cols if c["category"] == "4:3" and any(i["w"] == 640 and i["ratio"] for i in c["items"])]
check("...and the Resolution Preset menu gets a 4:3 column with the differences",
      len(mine) == 1 and [(i["w"], i["h"], i["ratio"]) for i in mine[0]["items"]] == [(640, 448, "7.1% wider"), (768, 576, "exact")],
      str(mine))
QMetaObject.invokeMethod(dlg, "close"); pump(0.5)
dpage = stage.property("pages").property(0).toQObject()
dbtn = [c for c in walk(dpage, []) if c.metaObject().className().startswith("PresetButton")][0]
click([c for c in walk(dbtn, []) if c.metaObject().className().startswith("AccentButton")][0]); pump(0.5)
cell_texts = [c.property("text") for c in everything() if c.metaObject().className().startswith("QQuickText") and c.isVisible()
              and "640×448" in str(c.property("text"))]
check("...shown there as '640×448  (7.1% wider)'", "640×448  (7.1% wider)" in cell_texts, str(cell_texts))
QTest.keyClick(win, Qt.Key_Escape); pump(0.3)
for p in [o for o in dbtn.findChildren(QObject) if o.metaObject().className().startswith("QQuickPopup")]:
    QMetaObject.invokeMethod(p, "close")
pump(0.3)

# untick and save: the column goes
QMetaObject.invokeMethod(dlg, "open"); pump(0.5)
dlg.setProperty("current", 0); pump(0.6)
ed = named("dcPresetEditor")[0]
check("reopened, what was saved is ticked", ed.property("pickedAll") == 2, str(ed.property("pickedAll")))
ratio = named("dcPresetRatio")[0]
QMetaObject.invokeMethod(ratio, "activated", Q_ARG(int, i43)); pump(0.3)
open_sizes(); pump(0.6)
click(show_row("640x448")); click(show_row("768x576"))
for p in named("dcPresetSizesPopup"):
    QMetaObject.invokeMethod(p, "close")
pump(0.3)
click(named("dcPresetSave")[0]); pump(0.5)
data = json.loads(f.read_text(encoding="utf-8"))
check("unticked and saved: gone from the file and the menu", data.get("presets") == []
      and not any(c["category"] == "4:3" and any(i["ratio"] for i in c["items"]) for c in dc.property("presets")), str(data))
# a short window (user, 2026-10-11: the list ran off the bottom): it stays inside, and scrolls
h_was = win.height()
win.setHeight(int(win.property("minimumHeight"))); pump(0.6)
open_sizes(); pump(0.6)
pop = [o for o in named("dcPresetEditor")[0].findChildren(QObject) if o.objectName() == "dcPresetSizesPopup"][0]
ci = pop.property("contentItem")
bottom = ci.mapToScene(QPointF(0, ci.height())).y() if ci is not None else 1e9
check("in a short window the Sizes list stays inside it", bottom <= win.height() + 0.5,
      f"list bottom {bottom:.0f}, window {win.height()}")
QMetaObject.invokeMethod(pop, "close"); pump(0.3)
win.setHeight(h_was); pump(0.4)
QMetaObject.invokeMethod(dlg, "close"); pump(0.4)
studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
