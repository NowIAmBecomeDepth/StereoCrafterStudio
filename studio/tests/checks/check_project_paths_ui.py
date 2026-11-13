"""The project / chapter / folder UI (2026-11-12), in the real app offscreen: a flat
temp project (input, depth, splat, inpaint, merged, a fixture clip in input), a
chaptered one (two chapters, nothing remembered) and an empty one, in a temp
Projects Folder. Switching by the project list puts every picker on "Whole
project" for the flat one, with ONE toast summing the landing up; the title bar's
chapter chip shows the chapter all stages share and its popup moves them all; the
chaptered project shows "Pick a chapter" (amber) until then; Project Folders lists
the five folders, Change… keeps an override the stages follow, Reset undoes it,
Create Missing Folders makes them (projectEmpty false), and an empty project opens
the panel by itself on a switch; a field holding the other project's folder is
tagged "In <project>" and a click on the tag puts the stage back. Configs and
last_jobs.json redirected; temp folders and _fixtures clips only.

    python studio/tests/checks/check_project_paths_ui.py
    STUDIO_UI_GRABS=<dir>  also saves a PNG of each new piece (Qt grabWindow of Studio's own window)
"""
import os, sys, tempfile, hashlib, time, shutil
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, str(Path(__file__).parent))
rec = REPO / "last_jobs.json"; h0 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
tmp = Path(tempfile.mkdtemp())
import _fixtures
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, QObject, Q_ARG, QPoint, QPointF, Qt, qInstallMessageHandler
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlProperty
from PySide6.QtTest import QTest
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if ("qml" in m.lower()) else None)
from studio import app as A

GRABS = os.environ.get("STUDIO_UI_GRABS", "")
if GRABS:
    os.makedirs(GRABS, exist_ok=True)

# ---- the projects ------------------------------------------------------------------------------
root = tmp / "Projects"
flat = root / "LaCasa"
for n in ("input", "depth", "splat/hires/finished", "splat/lowres", "inpaint", "merged"):
    (flat / n).mkdir(parents=True)
shutil.copy(_fixtures.path("small") / "src" / "clipA.mp4", flat / "input" / "clipA.mp4")
film = root / "My Film"
for st in ("Source Clips", "DepthMaps", "Splats", "Inpainted", "Merged"):
    for ch in ("Chapter 6 - Six", "Chapter 7 - Seven"):
        (film / f"SM {st}" / ch).mkdir(parents=True)
for ch in ("Chapter 6 - Six", "Chapter 7 - Seven"):
    (film / "SM Splats" / ch / "hires").mkdir()
    (film / "SM Splats" / ch / "lowres").mkdir()
empty = root / "Brand New"
empty.mkdir(parents=True)

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
shell = objs["Shell"]
shell.setProperty("uiScale", "1.0")
shell.setProperty("projectFolder", "")
shell.setProperty("projectsRoot", root.as_posix())
win.show()
fails = []
def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok: fails.append(label)
def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.005)
def walk(it, out=None):
    out = [] if out is None else out
    for c in it.childItems(): out.append(c); walk(c, out)
    return out
def named(name, under=None):
    return [c for c in walk(under if under is not None else win.contentItem()) if c.objectName() == name]
def shown(it):
    p = it
    while p is not None:
        if not p.isVisible() or p.opacity() == 0: return False
        p = p.parentItem()
    return True
def popup(name):
    return next(o for o in win.findChildren(QObject) if o.objectName() == name)
def click(item):
    c = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(c.x()), int(c.y()))); pump(0.6)
def norm(p):
    return str(p or "").replace("\\", "/").rstrip("/")
def unlayer():
    for c in walk(win.contentItem()):
        try:
            if QQmlProperty(c, "layer.enabled").read(): QQmlProperty(c, "layer.enabled").write(False)
        except RuntimeError:
            pass
def grab(name, item=None, pad=12, rect=None):
    """Studio's own window, rendered by Qt (grabWindow), cropped to `item` (or `rect`)."""
    if not GRABS:
        return
    unlayer(); pump(0.3)
    img = win.grabWindow()
    if item is not None:
        tl = item.mapToScene(QPointF(0, 0)); br = item.mapToScene(QPointF(item.width(), item.height()))
        rect = (tl.x() - pad, tl.y() - pad, br.x() - tl.x() + 2 * pad, br.y() - tl.y() + 2 * pad)
    if rect is not None:
        x, y, w, h = (int(v) for v in rect)
        x, y = max(0, x), max(0, y)
        img = img.copy(x, y, min(w, img.width() - x), min(h, img.height() - y))
    img.save(os.path.join(GRABS, name + ".png"))

win.setProperty("userSized", True); win.setWidth(1500); win.setHeight(1000); pump(0.8)
bar = named("titleBar")[0]
bar.setProperty("visible", True); bar.setProperty("height", 32)       # the custom caption, offscreen
stage = named("stage")[0]
host = named("toastHost")[0]
chip = named("titleChapterChip")[0]
sp, dc, ip, mg = objs["Splatting"], objs["DepthCrafter"], objs["Inpainting"], objs["Merging"]
pump(0.6)

def pickers():
    return [c for c in walk(win.contentItem()) if c.metaObject().className().startswith("ChapterPicker")]
def picker_of(ctl):
    return next((p for p in pickers() if p.property("dc") is ctl), None)
def toasts_during(fn, settle=1.2):
    before = host.property("serial")
    fn(); pump(settle)
    return host.property("serial") - before, host.property("lastTitle"), host.property("lastMessage")
def switch(path):
    QMetaObject.invokeMethod(win, "switchProject", Q_ARG("QVariant", Path(path).as_posix()))
def show_stage(i):
    QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", i), Q_ARG("QVariant", True)); pump(0.8)

panel = popup("projectFoldersPanel")
check("no Project Folders panel at start-up", not panel.property("visible"))

# ---- 1. the flat project, picked from the project list ----------------------------------------
def pick_flat():
    bar.centerClicked.emit(); pump(1.0)
    tile = next(r for r in named("projectRow") if r.isVisible() and r.property("modelData")["name"] == "LaCasa")
    click(tile)
n, title, msg = toasts_during(pick_flat)
check("the flat project: one toast for the switch, its landing summed up",
      n == 1 and title == "Project set" and msg.startswith("LaCasa:") and "Whole project" in msg, (n, title, msg))
texts = {c: (picker_of(c).property("displayText") if picker_of(c) else None) for c in (dc, sp, ip, mg)}
check("every stage's picker reads Whole project", all(t == "Whole project" for t in texts.values()), list(texts.values()))
check("Splatting's paths are the flat folders", norm(sp._map.value("input_depth_maps")).endswith("LaCasa/depth")
      and norm(sp._map.value("output_splatted")).endswith("LaCasa/splat"), sp._map.value("input_depth_maps"))
check("the chip shows the common chapter", shown(chip) and chip.property("visible")
      and bar.property("chipText") == "Whole project" == shell.property("commonChapter"), bar.property("chipText"))
centre = named("titleCenter")[0]
grab("chip", rect=(centre.mapToScene(QPointF(0, 0)).x() - 40, 0, centre.width() + chip.width() + 120, 34))

# the chip gives way in a narrow window, before the name does
win.setWidth(560); pump(0.6)
check("a narrow window: the chip hides, the name stays", not chip.isVisible() and centre.isVisible(),
      (chip.isVisible(), win.width()))
win.setWidth(1500); pump(0.6)
check("...and it comes back", chip.isVisible())

# ---- 2. the chip's popup, and Project Folders from it ----------------------------------------
chip_popup = popup("chapterChipPopup")
bar.chipClicked.emit(); pump(0.8)
rows = [r for r in named("chapterChipRow") if r.isVisible()]
check("the chip's popup lists the project's one entry, marked",
      chip_popup.property("opened") and len(rows) == 1 and rows[0].property("current"), len(rows))
grab("chip_popup", chip_popup.property("contentItem"), pad=16)
btn = next(b for b in named("chipProjectFolders") if b.isVisible())
click(btn); pump(0.6)
check("Project Folders… there opens the panel", panel.property("opened") and not chip_popup.property("opened"))
prow = [r for r in named("projectFolderRow") if r.isVisible()]
check("the panel lists five folders", len(prow) == 5, len(prow))
counts = [next(c for c in walk(r) if c.objectName() == "projectFolderCount").property("text") for r in prow]
check("the clip counts: 1 clip in input, the rest empty", counts == ["1 clip", "empty", "empty", "empty", "empty"], counts)
check("nothing missing: no Create Missing Folders", not any(b.isVisible() for b in named("createProjectFolders")))
grab("project_folders", panel.property("contentItem"), pad=16)

# Change… on Depth Maps: an override the stages follow
maps = flat / "maps"; maps.mkdir()
change = next(c for c in walk(prow[1]) if c.objectName() == "projectFolderChange")
click(change); pump(0.6)
fp = popup("projectFolderPicker")
check("Change… opens the folder picker", fp.property("opened"))
QMetaObject.invokeMethod(fp, "pick", Q_ARG("QVariant", maps.as_posix())); pump(0.8)
rows_now = shell.projectFolders("")
check("the pick is kept as the project's Depth Maps", rows_now[1]["overridden"] and norm(rows_now[1]["path"]).endswith("LaCasa/maps"),
      rows_now[1])
check("...and the stages follow", norm(dc._map.value("output_dir")).endswith("LaCasa/maps")
      and norm(sp._map.value("input_depth_maps")).endswith("LaCasa/maps"), (dc._map.value("output_dir"), sp._map.value("input_depth_maps")))
check("the panel stays open, its row marked with Reset", panel.property("opened")
      and any(b.isVisible() for b in named("projectFolderReset")))
reset = next(b for b in named("projectFolderReset") if b.isVisible())
click(reset); pump(0.6)
check("Reset finds it by name again", not shell.projectFolders("")[1]["overridden"]
      and norm(dc._map.value("output_dir")).endswith("LaCasa/depth"), dc._map.value("output_dir"))
QMetaObject.invokeMethod(panel, "close"); pump(0.5)

# ---- 3. the chaptered project: nothing remembered, two chapters ------------------------------
n, title, msg = toasts_during(lambda: switch(film))
check("the chaptered project: one toast", n == 1 and title == "Project set" and msg.startswith("My Film:"), (n, msg))
check("a stage there waits for a chapter", sp.property("needsChapter") and picker_of(sp).property("displayText") == "Pick a chapter",
      (sp.property("needsChapter"), picker_of(sp).property("displayText")))
check("no common chapter: the chip hides", not chip.isVisible() and bar.property("chipText") == "", bar.property("chipText"))
check("no panel for a project that has its folders", not panel.property("visible"))
show_stage(1)
page = stage.property("pages").property(1).toQObject()
spk = picker_of(sp)
txt = next(c for c in walk(spk) if c.objectName() == "chapterPickerText")
from PySide6.QtGui import QColor
check("'Pick a chapter' in the theme's warning colour (Theme.warning, dark / light)",
      QColor(txt.property("color")).name() in ("#e3b341", "#9a6700"), QColor(txt.property("color")).name())
folders = [c for c in walk(page) if c.objectName() == "changeFolders" and shown(c)]
fields = [c for c in walk(page) if c.property("key") == "input_source_clips" and c.property("projectField") is not None]
check("the empty folder fields are folded away (the picker is the whole control)",
      bool(fields) and not shown(fields[0]) and bool(folders), (bool(fields), bool(folders)))
sec = spk.parentItem()
while sec is not None and not sec.metaObject().className().startswith("Section"):
    sec = sec.parentItem()
grab("pick_a_chapter", sec if sec is not None else spk, pad=8)

# the chip popup moves every stage to one chapter
n6 = next(c["num"] for c in shell.listChapters() if c["name"].startswith("Chapter 6"))
shell.pickChapterForAll(-99); pump(0.2)                                    # an unknown key: nothing moves
check("an unknown chapter moves nobody", sp.property("chapterNum") == -1)
def pick6():
    # the chip is hidden (no common chapter); its popup opens the same way from Main
    QMetaObject.invokeMethod(chip_popup, "open"); pump(0.8)
    grab("chip_popup_chapters", chip_popup.property("contentItem"), pad=16)
    row = next(r for r in named("chapterChipRow") if r.isVisible() and r.property("modelData")["num"] == n6)
    click(row)
n, title, msg = toasts_during(pick6)
check("picking Chapter 6 there: one toast", n == 1 and title == "All stages on Chapter 6 - Six", (n, title))
check("...every chaptered stage on it", all(c.property("chapterNum") == n6 for c in (dc, sp, ip, mg))
      and not sp.property("needsChapter"), [c.property("chapterNum") for c in (dc, sp, ip, mg)])
check("...and the chip shows it", chip.isVisible() and bar.property("chipText") == "Chapter 6 - Six", bar.property("chipText"))
n7 = next(c["num"] for c in shell.listChapters() if c["name"].startswith("Chapter 7"))
sp.pickChapter(n7); pump(0.3)
check("Splatting's own picker still overrides: the chip says Mixed", sp.property("chapterNum") == n7
      and bar.property("chipText") == "Mixed", bar.property("chipText"))
shell.pickChapterForAll(n6); pump(0.3)

# ---- 4. a field holding the other project's folder -------------------------------------------
sp.pickChapter(-1); pump(0.2)
other = (flat / "depth").as_posix()
if hasattr(sp, "_set"):
    sp._set("input_depth_maps", other)
else:
    sp._map.insert("input_depth_maps", other)
pump(0.6)
tags = [t for t in walk(page) if t.objectName() == "foreignFolderTag" and shown(t)]
check("the field is tagged with the project it belongs to", len(tags) == 1 and tags[0].property("other") == "LaCasa",
      [t.property("other") for t in tags])
check("...and only that field", len([t for t in walk(page) if t.objectName() == "foreignFolderTag" and t.isVisible()]) == 1)
if tags:
    box = tags[0].parentItem().parentItem()     # the Loader, then the TextBox
    grab("foreign_marker", box.parentItem(), pad=10)
    click(tags[0]); pump(0.6)
check("a click on the tag puts Splatting back on My Film's folders",
      sp.property("chapterNum") == n6 and "My Film" in norm(sp._map.value("input_depth_maps")), sp._map.value("input_depth_maps"))
check("...and the tag goes", not [t for t in walk(page) if t.objectName() == "foreignFolderTag" and t.isVisible()])

# ---- 5. an empty project: Project Folders by itself, once ------------------------------------
n, title, msg = toasts_during(lambda: switch(empty), settle=1.4)
check("the empty project: one toast", n == 1 and title == "Project set", (n, title, msg))
check("...and Project Folders opens by itself", panel.property("opened") and panel.property("fresh") and shell.property("projectEmpty"))
note = [c for c in named("projectFoldersFresh") if shown(c)]
create = [b for b in named("createProjectFolders") if shown(b)]
check("it says the folder has no stage folders, Create Missing Folders prominent",
      bool(note) and bool(create) and create[0].property("kind") == "filled")
prow = [r for r in named("projectFolderRow") if r.isVisible()]
counts = [next(c for c in walk(r) if c.objectName() == "projectFolderCount").property("text") for r in prow]
check("five rows, none made yet", counts == ["not made yet"] * 5, counts)
grab("project_folders_empty", panel.property("contentItem"), pad=16)
if create:
    click(create[0]); pump(0.8)
check("Create Missing Folders makes them", not shell.property("projectEmpty")
      and all(r["exists"] for r in shell.projectFolders("")) and not any(b.isVisible() for b in named("createProjectFolders")))
QMetaObject.invokeMethod(panel, "close"); pump(0.5)

# a switch not made by the user (a property set, as at start-up) never opens it
(root / "Another Empty").mkdir()
shell.setProperty("projectFolder", (root / "Another Empty").as_posix()); pump(1.0)
check("a switch not made by the user opens no panel", not panel.property("visible"))

# ---- the Project Folders… button in the project list -----------------------------------------
bar.centerClicked.emit(); pump(0.8)
pf = [b for b in named("projectFoldersButton") if shown(b)]
check("the project list has Project Folders…", bool(pf))
grab("project_list_footer", popup("projectPicker").property("contentItem"), pad=16)
if pf:
    # the pointer goes there first (it rested on a poster: that poster's tip and its
    # Project Folders, 2026-11-12, are up until it leaves)
    c = pf[0].mapToScene(QPointF(pf[0].width() / 2, pf[0].height() / 2))
    QTest.mouseMove(win, QPoint(int(c.x()), int(c.y()))); pump(0.5)
    click(pf[0]); pump(0.6)
check("...which opens the panel", panel.property("opened") and not panel.property("hoverMode")
      and panel.property("title") == "Project Folders · " + shell.property("projectName"), panel.property("title"))
QMetaObject.invokeMethod(panel, "close"); pump(0.4)

check("no QML warnings", not warns, warns[:12])
h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
print("last_jobs unchanged:", h0 == h1, flush=True)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(1 if fails or h0 != h1 else 0)
