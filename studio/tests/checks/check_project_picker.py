"""The caption's project button (user, 2026-10-01), in the real app, offscreen:
Settings' Projects Folder lists its projects (natural order, a show marked, a
poster where the folder has one) as tiles in the ProjectPicker popup under the caption, the current one marked; a click
switches the project and the caption follows; Settings has the Projects Folder
row. Made-up projects in a temp folder; configs and last_jobs.json redirected.

    python studio/tests/checks/check_project_picker.py [shot-prefix]
"""
import os, sys, tempfile, time, hashlib
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
rec = REPO / "last_jobs.json"; h0 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, QObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlProperty
from PySide6.QtCore import qInstallMessageHandler
qmlwarns = []
qInstallMessageHandler(lambda t, ctx, m: qmlwarns.append(m) if "qml" in m.lower() else None)
from studio import app as A

root = tmp / "Conversions"
for d in ("Example Movie/EM Source Clips/Chapter 1 - Opening", "Example Movie/EM DepthMaps",
          "Example Show/Season 1/Episode 1/1. S1E1 Source Clips/Chapter 1", "Example Show/Season 1/Episode 1/2. S1E1 Depth Maps",
          "Another Film/AF Source Clips",
          "Nested Film/Movie/NF Source Clips/Chapter 1", "Nested Film/Movie/NF DepthMaps"):   # a movie in a subfolder: not a show
    (root / d).mkdir(parents=True)
# a poster in the movie's folder (a made-up gradient), none for the others
import numpy as np
from PIL import Image
g = np.zeros((300, 200, 3), np.uint8)
g[..., 0] = np.linspace(40, 220, 300)[:, None]; g[..., 2] = np.linspace(200, 60, 200)[None, :]
Image.fromarray(g).save(root / "Example Movie" / "poster.jpg")
(root / "Example Movie" / "notes.txt").write_text("not a picture")

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
def walk(it, out):
    for c in it.childItems(): out.append(c); walk(c, out)
    return out
def unlayer():
    for c in walk(win.contentItem(), []):
        try:
            if QQmlProperty(c, "layer.enabled").read(): QQmlProperty(c, "layer.enabled").write(False)
        except RuntimeError:
            pass
pump(0.6)
shell = objs["Shell"]
shell.setProperty("projectsRoot", str(root))
shell.setProperty("projectFolder", str(root / "Example Movie"))
win.setWidth(1400); win.setHeight(800)
bar = [c for c in walk(win.contentItem(), []) if c.objectName() == "titleBar"][0]
bar.setProperty("visible", True); bar.setProperty("height", 32)
pump(0.8)
lst = shell.listProjects()
check("projects listed in natural order", [p["name"] for p in lst] == ["Another Film", "Example Movie", "Example Show", "Nested Film"], [p["name"] for p in lst])
check("a show is one with an Episode folder inside (a movie kept in a subfolder is not)", [p["show"] for p in lst] == [False, False, True, False])
check("the movie's poster is found, the others have none",
      [bool(p["poster"]) for p in lst] == [False, True, False, False] and lst[1]["poster"].endswith("poster.jpg"), lst)
centre = [c for c in walk(bar, []) if c.objectName() == "titleCenter"][0]
bar.centerClicked.emit()
pump(1.0)
rows = [c for c in walk(win.contentItem(), []) if c.objectName() == "projectRow" and c.isVisible()]
from PySide6.QtCore import QObject
picker = [o for o in win.findChildren(QObject) if o.objectName() == "projectPicker"][0]
check("it opens on Movies (the current project's tab) with the movies", picker.property("kind") == 0 and len(rows) == 3, len(rows))
check("the current one is marked", [r.property("current") for r in rows] == [False, True, False])
pump(0.6)
posters = [c for c in walk(win.contentItem(), []) if c.objectName() == "projectPoster"]
check("the poster is drawn, the others show the folder", [c.isVisible() for c in posters] == [False, True, False],
      [c.isVisible() for c in posters])
# the window's film grain on the card too (user, 2026-10-02); off with the switch
card = picker.property("background")
grain = [c for c in walk(card, []) if c.objectName() == "popupGrainTile"]
tile = grain[0] if grain else None
src = tile.property("source") if tile else None
src = src.toString() if hasattr(src, "toString") else str(src)
check("the card has the film grain", bool(grain) and grain[0].isVisible() and tile.implicitWidth() > 0
      and src.startswith("image://grain/"), (src, tile.implicitWidth() if tile else None))
shell.setProperty("filmGrain", False); pump(0.3)
check("...and none with Film Grain off", not [c for c in walk(card, []) if c.objectName() == "popupGrainTile"])
shell.setProperty("filmGrain", True); pump(0.3)
unlayer(); pump(0.3)
img = win.grabWindow()
if len(sys.argv) > 1:
    img.copy(int(img.width() / 2 - 330), 0, 660, 420).save(sys.argv[1] + "_picker.png")
# TV Shows: just the show; pick it
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt, QPoint
picker.setProperty("kind", 1); pump(0.6)
rows = [c for c in walk(win.contentItem(), []) if c.objectName() == "projectRow" and c.isVisible()]
check("TV Shows lists the show alone", len(rows) == 1 and rows[0].property("modelData")["name"] == "Example Show", len(rows))
r = rows[0]
p = r.mapToScene(r.boundingRect().center())
QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(p.x()), int(p.y()))); pump(0.8)
check("a click switches the project", shell.property("projectFolder").endswith("Example Show"), shell.property("projectFolder"))
check("the caption shows it", bar.property("centerText") == "Example Show", bar.property("centerText"))

# ---- 2026-11-12: the Add Project tile, the "−" on a poster, Project Folders on hover ----------
import json, shutil
from PySide6.QtCore import QPointF, Q_ARG
GRABS = os.environ.get("STUDIO_UI_GRABS", "")
if GRABS:
    os.makedirs(GRABS, exist_ok=True)
def grab(name, rect=None):
    if not GRABS:
        return
    unlayer(); pump(0.3)
    im = win.grabWindow()
    if rect is not None:
        x, y, w, h = (int(v) for v in rect)
        x, y = max(0, x), max(0, y)
        im = im.copy(x, y, min(w, im.width() - x), min(h, im.height() - y))
    im.save(os.path.join(GRABS, name + ".png"))
def rect_of(it, pad=0):
    tl = it.mapToScene(QPointF(0, 0))
    return (tl.x() - pad, tl.y() - pad, it.width() + 2 * pad, it.height() + 2 * pad)
def popup_rect(p, pad=0):
    return (p.property("x") - pad, p.property("y") - pad, p.property("width") + 2 * pad, p.property("height") + 2 * pad)
def overlaps(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]
def inside_window(r):
    return r[0] >= 0 and r[1] >= 0 and r[0] + r[2] <= win.width() + 0.5 and r[1] + r[3] <= win.height() + 0.5
def centre_of(it):
    c = it.mapToScene(QPointF(it.width() / 2, it.height() / 2)); return QPoint(int(c.x()), int(c.y()))
def move(pt):
    QTest.mouseMove(win, pt); pump(0.05)
def tiles_now():
    return [c for c in walk(win.contentItem(), []) if c.objectName() == "projectRow" and c.isVisible()]
def tile_named(name):
    return next((t for t in tiles_now() if t.property("modelData")["name"] == name), None)
def stored():
    f = tmp / "config" / "studio.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
host = [c for c in walk(win.contentItem(), []) if c.objectName() == "toastHost"][0]
def norm(p):
    return str(p or "").replace("\\", "/").rstrip("/").lower()
out = tmp / "Elsewhere"
spider = out / "Spider-Man With A Very Long Name For The Tile"
for d in ("SM Source Clips/Chapter 1", "SM DepthMaps/Chapter 1"):
    (spider / d).mkdir(parents=True)
bluey = out / "Bluey"
(bluey / "Season 1" / "Episode 1" / "S1E1 Source Clips" / "Chapter 1").mkdir(parents=True)
gone = out / "Gone Film"
(gone / "GF Source Clips").mkdir(parents=True)
empty_new = out / "Brand New"
empty_new.mkdir(parents=True)
panel = win.findChild(QObject, "projectFoldersPanel")
dlg_add = win.findChild(QObject, "projectDlg")
refreshes = []
shell.projectRefreshed.connect(lambda: refreshes.append(1))

bar.centerClicked.emit(); pump(1.0)
picker.setProperty("kind", 0); pump(0.5)
add = [c for c in walk(win.contentItem(), []) if c.objectName() == "projectAddTile" and c.isVisible()]
rows = tiles_now()
check("the Add Project tile is there, after the last poster, the size of a poster", len(add) == 1 and rows
      and add[0].width() == rows[-1].width() and add[0].height() == rows[-1].height()
      and (add[0].y() > rows[-1].y() or add[0].x() > rows[-1].x()), (len(add), len(rows)))
check("Open Other Project… is gone; Change Projects Folder… stays",
      not [c for c in walk(win.contentItem(), []) if c.objectName() == "projectBrowse"]
      and [c for c in walk(win.contentItem(), []) if c.objectName() == "projectChooseRoot" and c.isVisible()])
grab("picker_add_tile", popup_rect(picker, 16))
# the tile, by mouse: the folder picker, the list still open; the pick added for good and opened
QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, centre_of(add[0])); pump(0.6)
check("a click on Add Project opens the folder picker over the list", dlg_add.property("opened") and picker.property("opened"))
before = host.property("serial")
QMetaObject.invokeMethod(dlg_add, "pick", Q_ARG("QVariant", str(spider))); pump(1.2)
check("the added folder is opened as the project", norm(shell.property("projectFolder")) == norm(spider), shell.property("projectFolder"))
check("...with one toast", host.property("serial") - before == 1 and host.property("lastTitle") == "Project set",
      (host.property("serial") - before, host.property("lastTitle")))
st = stored()
check("...remembered in studio.json (added_projects, forward slashes)",
      st.get("added_projects") == [spider.as_posix()], st.get("added_projects"))
t = tile_named(spider.name)
check("the list stays open on Movies with its tile, marked current", picker.property("opened") and picker.property("kind") == 0
      and t is not None and t.property("current"))
if t is not None:
    cap = [c for c in walk(t, []) if c.metaObject().className().startswith("QQuickText") and c.property("text") == spider.name]
    check("a long name is cut short inside its tile", bool(cap) and cap[0].property("truncated")
          and cap[0].width() <= t.width(), cap[0].width() if cap else None)
QMetaObject.invokeMethod(win, "addProject", Q_ARG("QVariant", str(spider).upper() + "\\")); pump(0.6)
check("adding it again (other case, slashes) adds no second tile", len([r for r in tiles_now() if r.property("modelData")["name"] == spider.name]) == 1
      and len(stored().get("added_projects", [])) == 1)
# a show: the list turns to TV Shows
QMetaObject.invokeMethod(win, "addProject", Q_ARG("QVariant", str(bluey))); pump(1.2)
check("an added show: TV Shows shown, its tile there", picker.property("kind") == 1 and tile_named("Bluey") is not None)
grab("picker_added_show", popup_rect(picker, 16))
# a full row: four tiles and the Add tile on the next, inside the card
QMetaObject.invokeMethod(win, "addProject", Q_ARG("QVariant", str(gone))); pump(1.2)
picker.setProperty("kind", 0); pump(0.5)
rows = tiles_now()
add = [c for c in walk(win.contentItem(), []) if c.objectName() == "projectAddTile" and c.isVisible()][0]
lst_item = [c for c in walk(win.contentItem(), []) if c.objectName() == "projectTiles"][0]
check("a full row: the Add tile wraps below, inside the list's width", len(rows) == 5
      and add.x() + add.width() <= lst_item.width() + 0.5 and add.y() >= rows[-1].y(), (len(rows), add.x(), add.y()))
check("the card is not wider than the list", picker.property("width") == 460)
# the missing folder: dimmed, not opened
shutil.rmtree(gone)
picker.close(); pump(0.5); bar.centerClicked.emit(); pump(1.0)
picker.setProperty("kind", 0); pump(0.5)
g = tile_named("Gone Film")
check("a missing added folder is still listed, marked missing", g is not None and g.property("missing"))
if g is not None:
    cur = shell.property("projectFolder")
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, centre_of(g)); pump(0.6)
    check("...a click there opens nothing", shell.property("projectFolder") == cur and picker.property("opened"))
    frame = [c for c in walk(g, []) if c.metaObject().className().startswith("QQuickRectangle") and c.width() == 66][0]
    check("...dimmed", frame.opacity() < 0.6, frame.opacity())
    grab("picker_missing_tile", rect_of(g, 6))
move(QPoint(5, win.height() - 5)); pump(1.4)          # nothing hovered

# the "−" on a poster: hover shows it, a click takes the tile off the list only
QMetaObject.invokeMethod(win, "switchProject", Q_ARG("QVariant", spider.as_posix())); pump(1.0)
a = tile_named("Another Film")
rm = [c for c in walk(a, []) if c.objectName() == "projectRemove"][0]
check("no remove button until hovered", not rm.isVisible() or rm.opacity() == 0)
move(centre_of(a)); pump(0.5)
check("hovering a poster shows its remove button", rm.isVisible() and rm.opacity() > 0.9, rm.opacity())
rr = rect_of(rm); ar = rect_of(a)
check("...in its top-right corner, inside the tile", rr[0] + rr[2] <= ar[0] + ar[2] + 0.5 and rr[1] >= ar[1] - 0.5
      and rr[0] > ar[0] + ar[2] / 2 and rr[1] < ar[1] + 30, (rr, ar))
grab("picker_remove_button", rect_of(a, 10))
cur = shell.property("projectFolder")
before = host.property("serial")
QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, centre_of(rm)); pump(0.8)
check("a click on − takes the tile off the list", tile_named("Another Film") is None)
check("...and does not open that project", shell.property("projectFolder") == cur, shell.property("projectFolder"))
check("...the folder is untouched", (root / "Another Film" / "AF Source Clips").is_dir())
check("...kept as hidden in studio.json", [norm(p) for p in stored().get("hidden_projects", [])] == [norm(root / "Another Film")])
check("...one toast says so", host.property("serial") - before == 1 and host.property("lastTitle") == "Removed from the list"
      and host.property("lastMessage") == "Another Film", (host.property("lastTitle"), host.property("lastMessage")))
# the open project's tile: removed, the project stays open
s = tile_named(spider.name)
rm = [c for c in walk(s, []) if c.objectName() == "projectRemove"][0]
move(centre_of(s)); pump(0.5)
QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, centre_of(rm)); pump(0.8)
check("removing the open project's tile keeps it open", tile_named(spider.name) is None
      and norm(shell.property("projectFolder")) == norm(spider) and stored().get("added_projects") == [bluey.as_posix(), gone.as_posix()],
      stored().get("added_projects"))
# Add brings a hidden one back
QMetaObject.invokeMethod(win, "addProject", Q_ARG("QVariant", str(root / "Another Film"))); pump(1.2)
check("adding a removed project again brings it back", tile_named("Another Film") is not None and not stored().get("hidden_projects"))
check("the remove clicks refreshed no stage", not refreshes, len(refreshes))
# the keyboard: a focused tile shows its remove button too
move(QPoint(5, win.height() - 5)); pump(1.4)
e = tile_named("Example Movie")
e.forceActiveFocus(); pump(0.4)
rm = [c for c in walk(e, []) if c.objectName() == "projectRemove"][0]
check("a tile with keyboard focus shows its remove button", e.hasActiveFocus() and rm.opacity() > 0.9, (e.hasActiveFocus(), rm.opacity()))
picker.property("contentItem").forceActiveFocus(); pump(0.3)

# ---- Project Folders on hover ----------------------------------------------------------------
picker.close(); pump(0.5)
win.setWidth(1800); pump(0.6)
bar.centerClicked.emit(); pump(1.0)
picker.setProperty("kind", 0); pump(0.5)
move(QPoint(5, win.height() - 5)); pump(0.3)
e = tile_named("Example Movie"); n = tile_named("Nested Film")
move(centre_of(e)); pump(0.15)
check("a sweep across a poster opens nothing (350 ms first)", not panel.property("visible"))
move(centre_of(n)); pump(0.15); move(centre_of(e)); pump(0.9)
check("resting on a poster shows Project Folders for it", panel.property("opened") and panel.property("hoverMode")
      and norm(panel.property("target")) == norm(root / "Example Movie"), panel.property("target"))
check("...titled with that project", panel.property("title") == "Project Folders · Example Movie", panel.property("title"))
pr = popup_rect(panel)
check("...beside the list, inside the window, not over the poster", inside_window(pr) and not overlaps(pr, rect_of(e))
      and not overlaps(pr, popup_rect(picker)), (pr, popup_rect(picker)))
check("...its folder rows are that project's", [r["path"] for r in shell.projectFoldersFor(panel.property("target"), "")][0].endswith("EM Source Clips"))
check("the list keeps the keyboard", not panel.property("focus") and picker.property("opened"))
tips = [c.property("tip") for c in walk(e, []) if c.metaObject().className().startswith("HoverTip")]
check("the poster's own path tip gives way to the panel", "" in tips and any(t.startswith("Remove from this list") for t in tips), tips)
grab("hover_panel", (min(pr[0], picker.property("x")) - 16, picker.property("y") - 16,
                     abs(pr[0] - picker.property("x")) + max(pr[2], picker.property("width")) + 32,
                     max(pr[3], picker.property("height")) + 32))
move(centre_of(n)); pump(0.1)
check("another poster retargets it at once", norm(panel.property("target")) == norm(root / "Nested Film"), panel.property("target"))
# into the panel: it stays; out of both: gone a second later
body = panel.property("contentItem")
move(centre_of(body)); pump(1.6)
check("the pointer on the panel keeps it open", panel.property("opened"))
move(QPoint(5, win.height() - 5)); pump(0.5)
check("left: still there half a second later", panel.property("opened"))
pump(0.9)
check("...and closed after a second", not panel.property("visible"))
check("the list stays open", picker.property("opened"))
# its own folder picker open: it stays; a folder of the hovered project changed leaves the open one alone
move(centre_of(e)); pump(0.9)
prow = [r for r in walk(win.contentItem(), []) if r.objectName() == "projectFolderRow" and r.isVisible()]
chg = [c for c in walk(prow[1], []) if c.objectName() == "projectFolderChange"][0]
QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, centre_of(chg)); pump(0.6)
fp = win.findChild(QObject, "projectFolderPicker")
check("Change… there opens its folder picker", fp.property("opened") and picker.property("opened"))
move(QPoint(5, win.height() - 5)); pump(1.6)
check("while it is open the panel stays", panel.property("opened"))
maps = root / "Example Movie" / "maps"; maps.mkdir()
refreshes.clear()
QMetaObject.invokeMethod(fp, "pick", Q_ARG("QVariant", maps.as_posix())); pump(0.6)
check("the pick is kept for the hovered project, not the open one",
      (root / "Example Movie" / ".studio_project.json").is_file()
      and shell.projectFoldersFor((root / "Example Movie").as_posix(), "")[1]["overridden"]
      and not shell.projectFolders("")[1]["overridden"])
check("...and the open project's stages were not refreshed", not refreshes, len(refreshes))
pump(1.4)
check("its picker closed, the pointer away: the panel goes a second later", not panel.property("visible"))
# the list closes: the panel with it
move(centre_of(e)); pump(0.9)
check("(open again)", panel.property("opened"))
picker.close(); pump(0.6)
check("closing the list closes the hover panel", not panel.property("visible"))
# the window's edge: no room beside the list -> below it / the poster, never over it
win.setWidth(1100); pump(0.6)
bar.centerClicked.emit(); pump(1.0)
picker.setProperty("kind", 0); pump(0.5)
e = tile_named("Example Movie")
move(centre_of(e)); pump(0.9)
pr = popup_rect(panel)
check("a narrow window: the panel still clear of the poster, inside the window",
      panel.property("opened") and inside_window(pr) and not overlaps(pr, rect_of(e)), (pr, rect_of(e), win.width()))
grab("hover_panel_narrow")
move(QPoint(5, win.height() - 5)); pump(1.4)
picker.close(); pump(0.5)
win.setWidth(1400); pump(0.6)
# pinned: the footer's Project Folders… is the open project's, no timeout
bar.centerClicked.emit(); pump(1.0)
pf = [c for c in walk(win.contentItem(), []) if c.objectName() == "projectFoldersButton" and c.isVisible()][0]
QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, centre_of(pf)); pump(0.6)
move(QPoint(5, win.height() - 5)); pump(1.6)
check("the footer's Project Folders… is pinned on the open project", panel.property("opened") and not panel.property("hoverMode")
      and panel.property("title") == "Project Folders · " + shell.property("projectName"), panel.property("title"))
QMetaObject.invokeMethod(panel, "close"); pump(0.5)
# an empty project added: the panel opens by itself, once
before = host.property("serial")
bar.centerClicked.emit(); pump(1.0)
QMetaObject.invokeMethod(win, "addProject", Q_ARG("QVariant", str(empty_new))); pump(1.4)
check("an empty project added: one toast, Project Folders opens by itself",
      host.property("serial") - before == 1 and panel.property("opened") and panel.property("fresh")
      and not panel.property("hoverMode") and not picker.property("opened"), (host.property("serial") - before, panel.property("fresh")))
QMetaObject.invokeMethod(panel, "close"); pump(0.5)
# no Projects Folder: the empty text names the tile, and the tile works
shell.setProperty("projectsRoot", "")
bar.centerClicked.emit(); pump(1.0)
txt = [c for c in walk(win.contentItem(), []) if c.metaObject().className().startswith("QQuickText") and c.isVisible()
       and "Add Project tile" in str(c.property("text"))]
add = [c for c in walk(win.contentItem(), []) if c.objectName() == "projectAddTile" and c.isVisible()]
check("no Projects Folder: the text mentions the Add Project tile, the tile is there", bool(txt) and bool(add))
grab("picker_no_root", popup_rect(picker, 16))
if add:
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, centre_of(add[0])); pump(0.6)
check("...and opens the folder picker", dlg_add.property("opened"))
QMetaObject.invokeMethod(dlg_add, "close"); pump(0.4)
picker.close(); pump(0.5)
check("no QML warnings", not qmlwarns, qmlwarns[:10])
shell.setProperty("projectsRoot", str(root))
# Settings' row
dlg = win.findChild(QObject, "settingsDialog")
QMetaObject.invokeMethod(dlg, "open"); pump(0.5)
dlg.setProperty("current", dlg.property("appearanceAt")); pump(0.6)
row = [c for c in walk(win.contentItem(), []) if c.objectName() == "projectsRootRow" and c.isVisible()]
if row:
    unlayer(); pump(0.3)
    r0 = row[0]; tl = r0.mapToScene(r0.boundingRect().topLeft())
    if len(sys.argv) > 1:
        win.grabWindow().copy(int(tl.x()) - 20, int(tl.y()) - 60, int(r0.width()) + 40, 110).save(sys.argv[1] + "_settings.png")
check("Settings has the Projects Folder row", len(row) == 1)
h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
print("last_jobs unchanged:", h0 == h1, flush=True)
print("FAILS", len(fails), fails, flush=True)
# through the fixtures: its temp folders removed, and no DLL teardown crash after a green
# run (bug sweep 2026-10-09, A12)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _fixtures  # noqa: E402
_fixtures.exit_now(1 if fails or h0 != h1 else 0)
