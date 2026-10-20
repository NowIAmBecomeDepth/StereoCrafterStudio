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
