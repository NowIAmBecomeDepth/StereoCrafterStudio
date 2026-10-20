"""The console in a window of its own, tabbed as v1's (user, 2026-09-30), in
the real app offscreen: Open Console shows it on the tab of the stage showing;
each stage's lines reach only its own tab (and All); shared code follows the
running job; Clear empties one tab; closing hides it and the lines keep
coming; its size and text size are kept; Studio closing takes it along.
Configs and last_jobs.json redirected."""
import os, sys, tempfile, hashlib, time, logging
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
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, QObject, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
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
def find_all_items(it, name, out=None):
    out = [] if out is None else out
    for c in it.childItems():
        if c.objectName() == name: out.append(c)
        find_all_items(c, name, out)
    return out
def find(it, pred):
    for c in it.childItems():
        if pred(c): return c
        r = find(c, pred)
        if r is not None: return r
pump(0.8)

con = win.findChild(QObject, "consoleWindow")
hub = objs["Logs"]
shell = objs["Shell"]
stage = find(win.contentItem(), lambda c: c.objectName() == "stage")
check("a console window, hidden at start (no drawer in the main window)",
      con is not None and not con.isVisible()
      and find(win.contentItem(), lambda c: c.objectName() == "consoleShadow") is None)
names = [t["name"] for t in con.property("tabs")]
check("tabs: the four stages, Extra, All", names == ["DepthCrafter", "Splatting", "Inpainting", "Merging", "Extra", "All"],
      str(names))
check("a window of its own, not kept above Studio's", con.transientParent() is None)
check("the UI thread gets the GIL quickly during a job (switch interval 0.5 ms)",
      sys.getswitchinterval() <= 0.001, str(sys.getswitchinterval()))

def rows(tab):
    pump(0.2)
    m = hub.tab(tab)
    return [m.data(m.index(i), m.TextRole) for i in range(m.rowCount())]

logging.getLogger("splatting.v2.preview").warning("console check: splat line")
logging.getLogger("splatting.v2.preview").info("console check: splat info")
logging.getLogger("depthcrafter.v2").warning("console check: dc line")
logging.getLogger("depthcrafter.v2").info("console check: dc info")
logging.getLogger("core.common.video_io").info("console check: shared idle")
check("a stage's line on its own tab only", [r for r in rows("splatting") if "console check" in r] == ["console check: splat line"]
      and [r for r in rows("depthcrafter") if "console check" in r] == ["console check: dc line"])
check("Splatting's tab as v1's: its INFO on All only (the banner, bars and warnings stay)",
      "console check: splat info" in rows("all"))
check("...and DepthCrafter's (user, 2026-10-04: the window bars and total times stay)",
      "console check: dc info" in rows("all") and not any("dc info" in r for r in rows("depthcrafter")))
check("shared code with no job running: All only",
      "console check: shared idle" in rows("all")
      and not any("shared idle" in r for t in ("depthcrafter", "splatting", "inpainting", "merging", "extra") for r in rows(t)))

# shared code during a job lands on that job's tab
merging = objs["Merging"]
_fixtures.fake_busy(merging, True); pump(0.2)
logging.getLogger("core.common.video_io").info("console check: shared during merge")
_fixtures.fake_busy(merging, False); pump(0.2)
check("shared code during a Merging job: the Merging tab",
      "console check: shared during merge" in rows("merging"))

# Open Console on Splatting shows its tab
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 1), Q_ARG("QVariant", True)); pump(0.8)
# ...and stays clickable while Splatting runs (user, 2026-09-30: it greyed out)
splat_page = stage.property("pages").property(1).toQObject()
open_btn = find(splat_page, lambda c: c.objectName() == "openConsole")
_fixtures.fake_busy(objs["Splatting"], True); pump(0.4)
check("Open Console stays enabled during a Splatting job", open_btn is not None and open_btn.isEnabled())
_fixtures.fake_busy(objs["Splatting"], False); pump(0.3)
con.setProperty("open", True); pump(0.8)
check("Open Console on Splatting: the window shows, on the Splatting tab",
      con.isVisible() and con.property("current") == 1, f"visible {con.isVisible()} tab {con.property('current')}")
lines = find(con.contentItem(), lambda c: c.objectName() == "consoleLines")
check("...listing Splatting's lines", lines is not None and lines.property("count") == len(rows("splatting")),
      f"{lines.property('count') if lines else None} vs {len(rows('splatting'))}")
con.setProperty("current", 5); pump(0.4)
check("every tab stays clickable: All lists everything", lines.property("count") == len(rows("all")))

# v1's style (user, 2026-09-30): records "HH:MM:SS - LEVEL - msg"; a printed bar
# drawn as an accent span over a trough, filled to its fraction
import re as _re
sys.stdout.write("\rEncoding check.mov: |" + "\u2588" * 5 + "-" * 5 + "| 50.0% \n"); sys.stdout.flush()
con.setProperty("current", 5); pump(0.8)
texts = [c.property("text") for c in find_all_items(con.contentItem(), "consoleText") if c.isVisible()]
check("a log record reads as v1's: time - LEVEL - message",
      any(_re.match(r"^\d\d:\d\d:\d\d - WARNING - console check: splat line$", t or "") for t in texts), str(texts[:3]))
bars = [c for c in find_all_items(con.contentItem(), "consoleBar") if c.isVisible()]
tr = find_all_items(bars[-1], "consoleBarTrough")[0] if bars else None
fl = find_all_items(bars[-1], "consoleBarFill")[0] if bars else None
check("a printed bar is drawn as a bar: the fill 50% of its trough, stretched across the pane",
      bool(bars) and abs(fl.width() / tr.width() - 0.5) < 0.02 and tr.width() > lines.width() * 0.3,
      f"{len(bars)} bars, fill {fl.width() if fl else None} / trough {tr.width() if tr else None}")
from PySide6.QtGui import QColor as _QC
pal = shell.property("palettes")[shell.property("paletteIndex")]
check("...the fill in the theme's accent, the trough the ground lifted",
      fl is not None and _QC(fl.property("color")) == _QC(pal["accent"])
      and _QC(tr.property("color")) == _QC(con.property("trough")) and _QC(tr.property("color")) != _QC(pal["bg"]),
      f"fill {_QC(fl.property('color')).name() if fl else None} accent {pal['accent']}")
# Clear empties one tab only
con.setProperty("current", 1); pump(0.3)
hub.tab("splatting").clear(); pump(0.3)
check("Clear empties this tab, All keeps its lines", lines.property("count") == 0 and len(rows("all")) > 0)

# text size
shell.setProperty("consoleFontSize", 15); pump(0.3)
logging.getLogger("splatting.v2").warning("console check: after resize"); pump(0.4)
sizes = {c.property("font").pixelSize() for c in find_all_items(lines, "consoleText") if c.isVisible()}
check("A+ / A-: the lines take the text size", shell.property("consoleFontSize") == 15 and sizes == {15}, str(sizes))
shell.setProperty("consoleFontSize", 99); pump(0.1)
check("...kept within 9-20", shell.property("consoleFontSize") == 20)

# frosted as Studio's window (user, 2026-10-01): the same ground, its own backdrop
check("glass: the console's ground is the main window's (the theme's tint over the glass)",
      con.color() == win.color(), f"{con.color().name(con.color().NameFormat.HexArgb)} vs {win.color().name(win.color().NameFormat.HexArgb)}")
check("...with a backdrop of its own, bound to the console window", objs["ConsoleBackdrop"]._window is con)

# selection across lines (user, 2026-10-01): drag from one line's 4th character
# to another's 6th; that span is picked, and it is what Ctrl+C copies
from PySide6.QtCore import QPoint, QPointF, Qt as _Qt
from PySide6.QtTest import QTest
shell.setProperty("consoleFontSize", 12); pump(0.2)
hub.tab("all")._on_live("console check: [####      ] 40%", False, "00:00:00")   # a live bar above the lines
for i in range(12):
    logging.getLogger("merging.v2").info(f"console check: selectable line number {i:02d}")
con.setProperty("current", 5); pump(0.6)
texts = sorted([c for c in find_all_items(lines, "consoleText") if c.isVisible()
                and "selectable line" in (c.property("text") or "")],
               key=lambda c: c.mapToScene(QPointF(0, 0)).y())
a, b = texts[-6], texts[-3]
def at(t, pos):
    r = t.positionToRectangle(pos)
    p = t.mapToScene(QPointF(r.x() + 1, r.y() + r.height() / 2))
    return QPoint(int(p.x()), int(p.y()))
ra, rb = a.parentItem().property("index"), b.parentItem().property("index")
QTest.mousePress(con, _Qt.LeftButton, _Qt.NoModifier, at(a, 3)); pump(0.1)
QTest.mouseMove(con, at(a, 20)); pump(0.05)
QTest.mouseMove(con, at(b, 5)); pump(0.1)
QTest.mouseRelease(con, _Qt.LeftButton, _Qt.NoModifier, at(b, 5)); pump(0.2)
want = hub.tab("all").textRange(ra, 3, rb, 5)
check("a drag across lines picks from the first line's character to the last's",
      a.property("selectedText") == a.property("text")[3:] and b.property("selectedText") == b.property("text")[:5]
      and want.count("\n") == rb - ra,
      f"{a.property('selectedText')!r} / {b.property('selectedText')!r}")
mid = [t for t in texts if ra < t.parentItem().property("index") < rb]
check("...the lines between are picked whole", mid and all(t.property("selectedText") == t.property("text") for t in mid))
check("...and Follow lets go, so the lines hold still", con.findChild(QObject, "consoleFollow").property("checked") is False)
cb = QGuiApplication.clipboard(); cb.setText("")
QTest.keyClick(con, _Qt.Key_C, _Qt.ControlModifier); pump(0.3)
check("Ctrl+C copies exactly that span (from the log, prefixes included)", cb.text() == want,
      f"{cb.text()[:60]!r} vs {want[:60]!r}")
# bug sweep A1: the bar moving under the lines (removed above them, put at the bottom)
# kept clearing the selection; it moves with its lines now
hub.tab("all")._on_live("console check: [######    ] 60%", False, "00:00:00"); pump(0.3)
cb.setText(""); QTest.keyClick(con, _Qt.Key_C, _Qt.ControlModifier); pump(0.3)
check("...a live bar moving to the bottom keeps the selection on its lines",
      con.property("hasSelection") is True and cb.text() == want, f"{cb.text()[:60]!r}")
QTest.mouseClick(con, _Qt.LeftButton, _Qt.NoModifier, at(b, 2)); pump(0.2)
check("a click clears it", con.property("hasSelection") is False and b.property("selectedText") == "")
QTest.keyClick(con, _Qt.Key_A, _Qt.ControlModifier); pump(0.2)
check("Ctrl+A picks the whole tab", con.property("hasSelection") is True
      and all(t.property("selectedText") == t.property("text") for t in texts))
cb.setText(""); QTest.keyClick(con, _Qt.Key_C, _Qt.ControlModifier); pump(0.3)
_all = hub.tab("all").allText()
check("...and copies every line of it", cb.text() == _all or _all.startswith(cb.text() + "\n"),
      f"{cb.text().count(chr(10)) + 1} vs {_all.count(chr(10)) + 1} lines")
hub.tab("all").clear(); pump(0.3)
check("clearing the tab drops the selection", con.property("hasSelection") is False)
# the scroll bar can be grabbed (user, 2026-10-04: a press on it picked text instead)
for i in range(60):
    logging.getLogger("merging.v2").info(f"console check: filler line {i:02d}")
pump(0.4); lines.setProperty("contentY", lines.property("contentHeight") - lines.height()); pump(0.3)
sbar = find(lines, lambda c: "ScrollBar" in c.metaObject().className())
y0 = lines.property("contentY")
c0 = sbar.mapToScene(QPointF(sbar.width() / 2, sbar.height() * (sbar.property("position") + sbar.property("size") / 2)))
QTest.mouseMove(con, QPoint(int(c0.x()), int(c0.y()))); pump(0.2)
QTest.mousePress(con, _Qt.LeftButton, _Qt.NoModifier, QPoint(int(c0.x()), int(c0.y()))); pump(0.1)
for k in range(1, 7):
    QTest.mouseMove(con, QPoint(int(c0.x()), int(c0.y()) - 15 * k)); pump(0.03)
QTest.mouseRelease(con, _Qt.LeftButton, _Qt.NoModifier, QPoint(int(c0.x()), int(c0.y()) - 90)); pump(0.2)
check("the scroll bar can be dragged: the lines scroll, no text is picked",
      lines.property("contentY") < y0 - 20 and con.property("hasSelection") is False,
      f"contentY {y0:.0f} -> {lines.property('contentY'):.0f}, selection {con.property('hasSelection')}")

# closing hides it; lines keep arriving; reopening on another stage picks its tab
W = con.minimumWidth() + 40; con.setProperty("width", W); con.setProperty("height", 400); pump(0.3)
con.close(); pump(0.5)
check("closing hides it (open goes false)", not con.isVisible() and con.property("open") is False)
check("...and remembers its size", list(shell.property("consoleGeometry"))[2:] == [W, 400],
      str(shell.property("consoleGeometry")))
logging.getLogger("merging.v2").info("console check: while hidden")
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 4), Q_ARG("QVariant", True)); pump(0.8)
con.setProperty("open", True); pump(0.8)
check("reopened from Extra: on the Extra tab, at its kept size",
      con.isVisible() and con.property("current") == 4 and (con.width(), con.height()) == (W, 400),
      f"tab {con.property('current')} {con.width()}x{con.height()}")
check("lines logged while hidden are there", "console check: while hidden" in rows("merging"))
# bug sweep A1: a saved place no screen shows (the second monitor gone) -> over the main window
con.close(); pump(0.5)
shell.setProperty("consoleGeometry", [-40000, -40000, W, 400])
con.setProperty("open", True); pump(0.8)
_on = any(s.virtualGeometry().intersects(con.geometry()) for s in QGuiApplication.screens())
check("a saved place off every screen: it opens where a screen shows it", con.isVisible() and _on,
      f"{con.x()},{con.y()}")

# Scene Cut and Edge Fill have Open Console too (user, 2026-10-18): on Run Process's heading and
# under Fill Settings, live during a job, opening the Extra tab; Scene Cut's run drawn as a bar there
con.close(); pump(0.5)
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 4), Q_ARG("QVariant", True)); pump(0.8)
extra = stage.property("pages").property(4).toQObject()
for tool, page_name, ctl_name in ((0, "sceneCutPage", "SceneCut"), (2, "edgeFillPage", "EdgeFill")):
    extra.setProperty("tool", tool); pump(0.6)
    tool_page = find(extra, lambda c: c.objectName() == page_name)
    btn = find(tool_page, lambda c: c.objectName() == "openConsole") if tool_page else None
    _fixtures.fake_busy(objs[ctl_name], True); pump(0.4)
    check(f"{ctl_name}: Open Console shown and enabled during a job",
          btn is not None and btn.isVisible() and btn.isEnabled())
    if btn is not None:
        QMetaObject.invokeMethod(btn, "clicked"); pump(0.8)
    check(f"...opens the console on the Extra tab", con.isVisible() and con.property("current") == 4,
          f"visible {con.isVisible()} tab {con.property('current')}")
    if ctl_name == "SceneCut":
        sc = objs["SceneCut"]
        sc._console_bar(20.0, "Running AutoShot inference (streaming)...", False); pump(0.4)
        live = rows("extra")[-1] if rows("extra") else ""
        sc._console_bar(45.0, "Splitting scene 3 of 7", False); pump(0.4)
        r = rows("extra")
        check("...Scene Cut's progress redraws one live bar on the Extra tab",
              r and r[-1].startswith("  Scene Cut:") and "45.0%" in r[-1] and "20.0%" not in r[-1]
              and live.startswith("  Scene Cut:") and r.count(live) == 0, str(r[-2:]))
        sc._console_bar(100.0, "Splitting scene 7 of 7", False); sc._console_bar(100.0, "Done!\nTook 1m.", True); pump(0.4)
        r = rows("extra")
        full = [x for x in r if x.startswith("  Scene Cut:") and "100.0%" in x]
        check("...100 % finishes it once (the split's 100 % and Done! make one bar)",
              len(full) == 1 and "Splitting scene 7 of 7" in full[0] and not sc._bar_open, str(full))
        sc._console_bar(30.0, "Splitting scene 2 of 7", False); sc._console_bar(30.0, "Aborted by user.", True); pump(0.4)
        check("...a run stopped short ends its line", not sc._bar_open)
    _fixtures.fake_busy(objs[ctl_name], False); pump(0.3)
    con.close(); pump(0.5)

# Studio closing takes the console along
con.setProperty("open", True); pump(0.6)
win.close(); pump(0.6)
check("Studio closing closes the console too", not con.isVisible())

check("no QML warnings", not warns, str(warns[:3]))
h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
check("the real last_jobs.json untouched", h0 == h1)
print("PASS" if not fails else f"FAIL ({len(fails)})")
_fixtures.exit_now(0 if not fails else 1)
