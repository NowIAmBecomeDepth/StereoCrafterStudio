"""Headless: hover tooltips on the real v2 window (offscreen, redirected config)."""
import os, sys, tempfile, hashlib
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
rec = REPO / "last_jobs.json"
before = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None

tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()
import studio.settings_store as ss
ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh
jh._RECORD_PATH = str(tmp / "last_jobs.json")

from PySide6.QtCore import QPoint, Qt, QPointF
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtQuick import QQuickItem
import studio.platform_win as PW
if os.environ.get("NOFRAME"):
    _i = PW.WindowFrame.__init__
    def _init(self, *a, **k):
        _i(self, *a, **k); self._on = False
    PW.WindowFrame.__init__ = _init
from studio import app as A

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
win.show()
QTest.qWait(300)

def tips():
    out = []
    def walk(it):
        for c in it.childItems():
            if c.metaObject().className().startswith("HoverTip"):
                out.append(c)
            walk(c)
    walk(win.contentItem())
    return out

def visible_ok(it):
    p = it.parentItem()
    while p is not None:
        if not p.isVisible() or p.opacity() == 0:
            return False
        p = p.parentItem()
    return True

all_tips = tips()
print("HoverTips:", len(all_tips))
live = [t for t in all_tips if t.property("tip") and visible_ok(t)]
print("visible with text:", len(live))
fails = 0
for t in live:
    row = t.parentItem()
    # hover over the right part of the row (the control, not the label)
    pt = row.mapToScene(QPointF(20, row.height() / 2)).toPoint()   # the label: a click on a Browse button opens a modal dialog
    QTest.mouseMove(win, QPoint(5, 5)); QTest.qWait(150)
    QTest.mouseMove(win, pt); QTest.qWait(250)
    early = t.property("showing")
    print("   dismissed before wait:", t.property("dismissed"))
    QTest.qWait(600)
    shown = t.property("showing")
    QTest.qWait(300)
    sz = t.property("popupSize")
    pw, ph = sz.width(), sz.height()
    # Capped at maxWidth; a tip short enough to fit one line needs no wrap.
    wrapped = pw <= 340
    # Never over the pointer: pushed back into the window it covered it, and
    # the click below landed on the tip, not the setting.
    b = t.property("bubble")                                       # where Qt really drew it,
    tl = b.mapToScene(QPointF(0, 0))                               # both corners through the
    br = b.mapToScene(QPointF(b.width(), b.height()))              # page zoom (Main.qml)
    covers = shown and tl.x() <= pt.x() <= br.x() and tl.y() <= pt.y() <= br.y()
    # Too tall for either side of the row, Qt keeps it in the window, over the
    # pointer -- by design (HoverTip: a click on it hides it). Only a tip that
    # could have fit counts.
    r_top = row.mapToScene(QPointF(0, 0)).y()
    r_bot = row.mapToScene(QPointF(0, row.height())).y()
    if covers and r_bot + 4 + ph > win.height() - 8 and r_top - 4 - ph < 8:
        covers = False
    # A button is never clicked (user, 2026-10-02): its tip is checked by hover
    # alone -- a click on Start started a job on the config seeded from the
    # user's own settings. Rows are clicked on their label, as before.
    is_button = hasattr(row, "clicked") or row.metaObject().className().startswith(("AccentButton", "IconButton", "Btn"))
    if is_button:
        after_click = False
    else:
        QTest.mousePress(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pt); QTest.qWait(50)
        QTest.mouseRelease(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, pt); QTest.qWait(200)
        after_click = t.property("showing")
    QTest.keyClick(win, Qt.Key.Key_Escape); QTest.qWait(100)
    QTest.mouseMove(win, QPoint(5, 5)); QTest.qWait(250)
    gone = t.property("showing")
    ok = wrapped and (not early) and shown and (not covers) and (not after_click) and (not gone)
    fails += not ok
    print(f"{'OK ' if ok else 'BAD'} {pw:.0f}x{ph:.0f} early={early} shown={shown} covers={covers} afterClick={after_click} left={gone}  {t.property('tip')[:50]!r}")

after = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
print("last_jobs.json unchanged:", before == after)
print("FAILS", fails)
import _fixtures
_fixtures.exit_now(0 if not fails and before == after else 1)
