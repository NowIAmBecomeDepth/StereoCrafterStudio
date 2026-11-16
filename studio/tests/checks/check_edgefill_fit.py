"""Edge Fill: Fit Window to Content leaves room for the whole left column (2026-11-15: the page asked
for a fixed 620 px, its Fill Settings had grown past that since, and after a fit the action bar sat on
top of them -- Match Detail and Open Console showing below it). The page now asks for its column's
height, as the other stages do. Real app, offscreen; configs and last_jobs.json redirected."""
import os, sys, tempfile, hashlib
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
rec = REPO / "last_jobs.json"; h0 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from PySide6.QtCore import QMetaObject, Q_ARG, QPointF
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from studio import app as A

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
objs["Shell"].setProperty("uiScale", "1.0")
objs["Shell"].setProperty("showTips", False)
win.show(); QTest.qWait(600)


def find(it, pred):
    for c in it.childItems():
        if pred(c): return c
        r = find(c, pred)
        if r is not None: return r


def named(n): return find(win.contentItem(), lambda c: c.objectName() == n)


fails = []
def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}")
    if not ok: fails.append(label)


stage = named("stage")
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 4), Q_ARG("QVariant", True)); QTest.qWait(700)
extra = stage.property("pages").property(4).toQObject()
extra.setProperty("tool", 2); QTest.qWait(900)
left = named("edgeFillLeft")
page = left.parentItem()
bar = find(page, lambda c: c.objectName() == "actionBar")


def visible_bottom(col):
    """The lowest edge of the column's visible children, in the page's coordinates."""
    b = 0.0
    for c in col.childItems():
        if c.isVisible() and c.height() > 0:
            b = max(b, c.mapToItem(page, QPointF(0, c.height())).y())
    return b


for label, setup in (("defaults", {}),
                     ("Copy From Other Frames on", {"copy_frames": True})):
    st = page.property("st")
    for k, v in setup.items():
        try: st.setProperty(k, v)
        except Exception: pass
    QTest.qWait(400)
    QMetaObject.invokeMethod(win, "fitToContent"); QTest.qWait(700)
    col_bottom = visible_bottom(left)
    bar_top = bar.mapToItem(page, QPointF(0, 0)).y()
    check(f"{label}: the left column ends above the action bar after a fit",
          col_bottom <= bar_top, f"column bottom {col_bottom:.0f}, bar top {bar_top:.0f}, window {win.width()}x{win.height()},"
          f" page asks {page.property('requiredHeight'):.0f}")

h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
check("real last_jobs.json unchanged", h0 == h1)
print("FAILED:" if fails else "ALL OK", fails)
sys.stdout.flush()
os._exit(1 if fails else 0)
