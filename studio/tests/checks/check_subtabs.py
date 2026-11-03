"""The sub-tab rows' glow-dot style (user, 2026-11-01, picked in prototypes/subtab_lab): under the stage tabs,
DepthCrafter / VDA / Blend (and Inpainting's, Extra's) have no pill and no line -- the current label is full
size with a glowing dot under it, the others rest at 70 % and dimmer and grow to 86 % under the pointer.
The stage tabs keep their pill and line. Real pointer events (QTest) on the offscreen window. Configs
redirected.
"""
import os, sys, tempfile, hashlib, time
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
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, QObject, QPoint, QPointF, Qt
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
    if not ok:
        fails.append(label)


def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.005)


def find_all(it, pred, out=None):
    out = [] if out is None else out
    for c in it.childItems():
        if pred(c):
            out.append(c)
        find_all(c, pred, out)
    return out


def centre(item):
    p = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    return QPoint(int(p.x()), int(p.y()))


def tabs_of(bar):
    """{label: (tab delegate, its content Row)}"""
    out = {}
    for t in find_all(bar, lambda c: c.metaObject().className().startswith("QQuickText") and c.isVisible()):
        content = t.parentItem()
        if content is not None and content.objectName() == "" and content.parentItem() is not None \
                and content.parentItem().property("current") is not None:
            out[str(t.property("text"))] = (content.parentItem(), content)
    return out


pump(1.5)
root = win.contentItem().parentItem() or win.contentItem()
stage = find_all(root, lambda c: c.objectName() == "stage")[0]
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 0), Q_ARG("QVariant", True)); pump(0.6)

bar = find_all(root, lambda c: c.objectName() == "dcEngineSwitch")[0]
check("DepthCrafter's engine row is a sub-tab row", bar.property("subTabs") is True)
pill = [c for c in bar.childItems() if c.property("radius") == 10 and c.property("y") == 4]
dot = bar.findChildren(QObject, "subTabDot")[0]
check("no pill and no line, a dot", dot.isVisible() and not any(p.isVisible() for p in pill)
      and not any(c.isVisible() and c.property("height") == 3 for c in bar.childItems()))
tabs = tabs_of(bar)
# DepthCrafter | VDA, and Blend on the user's own copy (it isn't released)
ORDER = [n for n in ("DEPTHCRAFTER", "VDA", "BLEND") if n in tabs]
names = sorted(tabs)
check("its tabs found", ORDER[:2] == ["DEPTHCRAFTER", "VDA"] and len(names) == len(ORDER), str(names))
cur = int(bar.property("currentIndex"))
cur_name = ORDER[cur]
for name, (tab, content) in tabs.items():
    want = 1.0 if name == cur_name else 0.70
    check(f"{name} rests at {want:.0%}", abs(content.property("scale") - want) < 1e-3, str(content.property("scale")))
tab0, _c = tabs[cur_name]
dc = dot.mapToScene(QPointF(3, 3)).x()
check("the dot sits under the current tab", abs(dc - centre(tab0).x()) <= 1, f"{dc} vs {centre(tab0).x()}")

other = "VDA" if cur_name != "VDA" else "DEPTHCRAFTER"
otab, ocontent = tabs[other]
QTest.mouseMove(win, centre(otab)); pump(0.3)
check(f"hovered {other} grows to 86%", abs(ocontent.property("scale") - 0.86) < 1e-3, str(ocontent.property("scale")))
x_before = otab.x()
QTest.mouseMove(win, QPoint(5, 5)); pump(0.3)
check(f"{other} shrinks back to 70% when the pointer leaves", abs(ocontent.property("scale") - 0.70) < 1e-3)
check("nothing beside it moved", otab.x() == x_before)
QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, centre(otab)); pump(1.0)
check(f"clicking {other} makes it current, full size", int(bar.property("currentIndex")) == ORDER.index(other)
      and abs(ocontent.property("scale") - 1.0) < 1e-3)
dc = dot.mapToScene(QPointF(3, 3)).x()
check("the dot follows it", abs(dc - centre(otab).x()) <= 1, f"{dc} vs {centre(otab).x()}")
QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, centre(tab0)); pump(0.8)   # back as it was

# the stage strip keeps its pill and glowing line
strip = [b for b in find_all(root, lambda c: c.metaObject().className().startswith("StageTabBar"))
         if b.property("numbered") and b.property("subTabs") is False]
check("the stage tabs are not sub-tabs, their line still shows",
      bool(strip) and any(c.isVisible() and c.property("height") == 3 for c in strip[0].childItems()))
for name in ("extraSwitch",):
    b = find_all(root, lambda c: c.objectName() == name)
    check(f"{name} is a sub-tab row", bool(b) and b[0].property("subTabs") is True)
inp = find_all(root, lambda c: c.objectName() == "inpEngineSwitch")
check("Inpainting's V1 / V2 row is a sub-tab row", bool(inp) and inp[0].property("subTabs") is True)

h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
print("last_jobs unchanged:", h0 == h1)
print("FAILS", len(fails), fails, flush=True)
os._exit(1 if fails or h0 != h1 else 0)
