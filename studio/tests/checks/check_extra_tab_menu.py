"""The Extra tab's hover menu (user, 2026-10-18): hovering Extra drops down its three tools; picking one opens
Extra on that tool; a click on Extra itself still opens the one last used; the list closes once the pointer
has left the tab and the list. Real pointer events (QTest) on the offscreen window. Configs redirected.
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
import _fixtures
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, QPoint, QPointF, Qt
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


def move(item):
    QTest.mouseMove(win, centre(item)); pump(0.05)


def click(item):
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, centre(item)); pump(0.3)


pump(1.5)
root = win.contentItem().parentItem() or win.contentItem()
bars = [b for b in find_all(root, lambda c: c.metaObject().className().startswith("StageTabBar"))
        if b.objectName() != "extraSwitch"]
def _model(b):
    m = b.property("model")
    return list(m.toVariant() if hasattr(m, "toVariant") else m or [])
tabs_bar = next(b for b in bars if "Extra" in _model(b))          # the stage strip (not a page's own)
from PySide6.QtCore import QObject
menu = next(m for m in tabs_bar.findChildren(QObject, "tabHoverMenu"))
labels = find_all(tabs_bar, lambda c: c.metaObject().className().startswith("QQuickText") and c.property("text") in ("EXTRA", "Extra", "DEPTHCRAFTER", "DepthCrafter"))
tab = {str(t.property("text")).lower(): t.parentItem().parentItem() for t in labels}
extra_tab, dc_tab = tab["extra"], tab["depthcrafter"]
stage = find_all(root, lambda c: c.objectName() == "stage")[0]
extra = stage.property("pages").property(4).toQObject()

QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 0), Q_ARG("QVariant", True)); pump(0.5)
extra.setProperty("tool", 2); pump(0.2)                      # last used: Edge Fill

move(dc_tab); pump(0.4)
check("hovering a tab without a menu opens nothing", not menu.property("opened"))
move(extra_tab); pump(0.05)
check("...nor at once on Extra (a pass across the strip)", not menu.property("opened"))
pump(0.5)
check("hovering Extra drops down its tools", menu.property("opened"))
rows = [r for r in find_all(root, lambda c: c.objectName() == "tabMenuItem") if r.isVisible()]
names = [str(r.property("modelData")) for r in sorted(rows, key=lambda r: r.mapToScene(QPointF(0, 0)).y())]
check("...the three, in pipeline order", names == ["Scene Cut", "Depth Map Editor", "Edge Fill"], str(names))
check("...the one last used ticked", menu.property("ticked") == 2, str(menu.property("ticked")))
check("...under the Extra tab", rows and abs(rows[0].mapToScene(QPointF(0, 0)).x() - extra_tab.mapToScene(QPointF(0, 0)).x()) < 20)
check("hovering alone switches nothing", stage.property("current") == 0, str(stage.property("current")))

dme = next(r for r in rows if r.property("modelData") == "Depth Map Editor")
move(dme); pump(0.4)
check("the list stays open on the way from the tab into it", menu.property("opened"))
click(dme); pump(0.6)
check("picking Depth Map Editor opens Extra on it", stage.property("current") == 4 and extra.property("tool") == 1,
      f"stage {stage.property('current')} tool {extra.property('tool')}")
check("...and closes the list", not menu.property("opened"))

QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 0), Q_ARG("QVariant", True)); pump(0.5)
click(extra_tab); pump(0.6)
check("a click on Extra opens the tool last used", stage.property("current") == 4 and extra.property("tool") == 1,
      f"stage {stage.property('current')} tool {extra.property('tool')}")

move(extra_tab); pump(0.5)
opened = menu.property("opened")
QTest.mouseMove(win, QPoint(int(win.width() / 2), int(win.height() * 0.8))); pump(0.6)
check("leaving the tab and the list closes it", opened and not menu.property("opened"), f"opened first {opened}")

_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
