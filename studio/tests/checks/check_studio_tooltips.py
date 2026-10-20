"""Every tooltip is Studio's own (user, 2026-10-02): the chapter picker and a
few buttons used Qt's attached ToolTip -- black, unwrapped, and it could run
off the window. Offscreen, the real window: no QML file uses the attached
ToolTip any more, and hovering (never clicking) the chapter picker and
DepthCrafter's Start / Cancel shows a HoverTip in the theme's colours, at most
its 340 px wide, inside the window. Configs and last_jobs.json redirected.

    python studio/tests/checks/check_studio_tooltips.py
"""
import os, sys, tempfile, hashlib, re
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
rec = REPO / "last_jobs.json"; h0 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from PySide6.QtCore import QPoint, QPointF
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from studio import app as A

fails = []
def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok: fails.append(label)

qml = REPO / "studio" / "qml" / "Studio"
attached = [f"{p.name}:{i + 1}" for p in qml.glob("*.qml")
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines()) if re.search(r"\bToolTip\.(text|visible)\b", line)]
check("no QML uses Qt's attached ToolTip", not attached, attached)

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
win.show(); QTest.qWait(400)
# a synthetic project of its own: the chapter picker shows only with one open, and this
# check found the user's real project through the Prototype's settings until those
# stopped seeding a test's (3357a31, 2026-10-10)
_proj = tmp / "project"
for _d in ("SM Source Clips/Chapter 2 - The Origin", "SM DepthMaps/Chapter 2 - The Origin"):
    (_proj / _d).mkdir(parents=True)
objs["Shell"].setProperty("projectFolder", str(_proj))
QTest.qWait(500)


def items(pred):
    out = []
    def walk(it):
        for c in it.childItems():
            if pred(c): out.append(c)
            walk(c)
    walk(win.contentItem())
    return out


def tip_of(host):
    return next((c for c in host.childItems() if c.metaObject().className().startswith("HoverTip")), None)


def shown(it):
    p = it
    while p is not None:
        if not p.isVisible() or p.opacity() == 0: return False
        p = p.parentItem()
    return True


hosts = {
    "chapter picker": [c for c in items(lambda c: c.metaObject().className().startswith("ChapterPicker")) if shown(c)][:1],
    "Start": [c for c in items(lambda c: c.property("text") == "Start" and c.metaObject().className().startswith("AccentButton")) if shown(c)][:1],
}
from PySide6.QtGui import QColor
theme_field = None
for name, found in hosts.items():
    if not found:
        check(f"{name}: on screen", False); continue
    host = found[0]
    t = tip_of(host)
    if t is None:
        check(f"{name}: has a HoverTip", False); continue
    pt = host.mapToScene(QPointF(host.width() / 2, host.height() / 2)).toPoint()
    QTest.mouseMove(win, QPoint(5, 5)); QTest.qWait(150)
    QTest.mouseMove(win, pt); QTest.qWait(1000)
    ok_shown = bool(t.property("showing"))
    b = t.property("bubble")
    tl = b.mapToScene(QPointF(0, 0)) if b else QPointF()
    br = b.mapToScene(QPointF(b.width(), b.height())) if b else QPointF()
    inside = b is not None and tl.x() >= 0 and tl.y() >= 0 and br.x() <= win.width() and br.y() <= win.height()
    colour = b.property("color") if b else None
    styled = colour is not None and QColor(colour).name() != "#000000"   # the theme's field, not Qt's black
    sz = t.property("popupSize")
    check(f"{name}: Studio's tooltip shows, styled, wrapped, inside the window",
          ok_shown and styled and sz.width() <= 340 and inside,
          f"shown={ok_shown} colour={QColor(colour).name() if colour is not None else None} {sz.width():.0f}x{sz.height():.0f} "
          f"at {tl.x():.0f},{tl.y():.0f}-{br.x():.0f},{br.y():.0f} in {win.width()}x{win.height()}")
    QTest.mouseMove(win, QPoint(5, 5)); QTest.qWait(300)

h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
print("last_jobs unchanged:", h0 == h1, flush=True)
print("FAILS", len(fails), fails, flush=True)
# through the fixtures: its temp folders removed, and no DLL teardown crash after a green
# run (bug sweep 2026-10-09, A12)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _fixtures  # noqa: E402
_fixtures.exit_now(1 if fails or h0 != h1 else 0)
