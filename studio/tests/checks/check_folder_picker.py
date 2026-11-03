"""Studio's folder picker, laid out as Windows Explorer (user, 2026-10-31: "Explorer+Preview";
the Columns picker of 2026-10-05 before it), on Splatting's fields, in the real app, offscreen,
over a made-up folder tree in a temp folder:

* Input Source Clips has ONE Browse button; it opens the picker on the field's folder;
* one click highlights a folder (or a clip, on a field that takes one), the picker stays open; a
  double-click on a folder goes in, on a clip picks it; Select picks what is highlighted, else the
  folder shown;
* back / forward / up (buttons and Alt+keys), a path typed into the address bar (Ctrl+L), search
  through the folders below, sort by a column heading, Details / Tiles / Large icons and the preview
  pane (both kept in Studio's settings);
* a folder or clip dropped on it from Explorer goes there (the window's Drop Zones get no drags
  while it is open);
* Output Splatted is folders only: its clips are listed but can't be picked;
* right-click: New Folder, named in place; Move to Recycle Bin, asked first (Cancel keeps it); the
  bin is a stand-in -- the user's own is never touched; the folder shown goes, and the picker steps
  back to its parent; a refusal is said (2026-10-10);
* the four shortcuts in the navigation pane: an empty one keeps the highlighted folder (saved), a
  set one goes there, right-click removes it (2026-10-10);
* nothing sticks out past its edge on a narrow window; every stage loads without QML warnings.

Configs and last_jobs.json redirected.
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
import studio.folder_browser as FB            # the Recycle Bin: a stand-in, never the user's
binned, bin_ok = [], [True]
def fake_bin(path):
    binned.append(path)
    if not bin_ok[0]:
        return False
    import shutil
    (shutil.rmtree if os.path.isdir(path) else os.remove)(path)
    return True
FB.send_to_recycle_bin = fake_bin
FB.has_recycle_bin = lambda path: True
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, QObject, QPoint, QPointF, Q_ARG, Qt, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from studio import app as A

# the tree: Episode 2 / 1. E2 Source Clips / Chapter 0..2 (clips in 0 and 1), 2. E2 Depth Maps
ep = tmp / "Show" / "Season 1" / "Episode 2"
for st in ("1. E2 Source Clips", "2. E2 Depth Maps", "3. E2 Splats"):
    for c in range(3):
        d = ep / st / f"Chapter {c}"
        d.mkdir(parents=True)
        if st.startswith("1.") and c < 2:
            for k in range(4 + c):
                (d / f"clip_c{c}_{k:02d}.mp4").write_bytes(b"")
junk = ep / "4. Leftovers" / "Old cut"
junk.mkdir(parents=True)
for k in range(5):
    (junk / f"old_{k:02d}.mp4").write_bytes(b"")

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
objs["Shell"].setProperty("uiScale", "1.0")
objs["Shell"].setProperty("projectsRoot", "")
win = engine.rootObjects()[0]
win.resize(1500, 1000); win.show()
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


def shown(name):
    return [c for c in walk(win.contentItem(), []) if c.objectName() == name and c.isVisible()]


def overlay_items(name):
    return [c for c in walk(win.contentItem().parentItem() or win.contentItem(), []) if c.objectName() == name and c.isVisible()]


def centre(item):
    p = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    return QPoint(int(p.x()), int(p.y()))


pump(1.0)
stage = shown("stage")[0]
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 1), Q_ARG("QVariant", True)); pump(1.0)
sp = objs["Splatting"]
src = ep / "1. E2 Source Clips" / "Chapter 0"
sp._set("input_source_clips", str(src).replace("\\", "/")); pump(0.3)
st = sp.property("settings")
page = stage.property("pages").property(1).toQObject()
pickers = [o for o in page.findChildren(QObject) if o.objectName() == "folderPicker"]
check("Splatting's folder fields use Studio's picker (no Windows folder dialog)", len(pickers) >= 3, str(len(pickers)))
btn = [c for c in walk(page, []) if c.objectName() == "browseSource"]
check("Input Source Clips has one Browse button", len(btn) == 1 and btn[0].property("text") == "Browse")
QMetaObject.invokeMethod(btn[0], "clicked"); pump(0.8)
picker = [p for p in pickers if p.property("opened")]
check("Browse opens the picker on the field's folder", len(picker) == 1
      and os.path.normcase(picker[0].property("path")) == os.path.normcase(str(src)), picker[0].property("path") if picker else "")
pk = picker[0]
check("...that takes a folder or a single clip", pk.property("allowFiles") is True)
shell = objs["Shell"]


def rows(kind):
    return [c for c in walk(pk.property("contentItem"), []) if c.objectName() == kind and c.isVisible()]


def rowfor(kind, ending):
    r = [c for c in rows(kind) if str(c.property("path")).endswith(ending)]
    return r[0] if r else None


def same(a, b):
    return os.path.normcase(os.path.normpath(str(a))) == os.path.normcase(os.path.normpath(str(b)))


def dclick(item):
    QTest.mouseDClick(win, Qt.LeftButton, Qt.NoModifier, centre(item)); pump(0.6)


shell.setProperty("pickerView", "details"); shell.setProperty("pickerPreview", True); pump(0.3)
check("...in Details, its clips listed with their type and size",
      len(rows("pickerClip")) == 4 and len(rows("pickerHeading")) == 4)

# one click: highlighted, the picker stays open; Select Clip picks just it
clip = rowfor("pickerClip", "clip_c0_02.mp4")
QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, centre(clip)); pump(0.4)
selb = rows("pickerSelect")[0]
check("one click on a clip highlights it; the button says Select Clip, the picker stays open",
      str(pk.property("sel")).endswith("clip_c0_02.mp4") and selb.property("text") == "Select Clip" and pk.property("opened") is True)
prev = rows("pickerPreview")
check("...the preview pane shows it", bool(prev) and "clip_c0_02.mp4" in str(prev[0].property("d").toVariant().get("name")
                                                                            if hasattr(prev[0].property("d"), "toVariant") else prev[0].property("d")["name"]))
QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, centre(selb)); pump(0.5)
val = str(st.value("input_source_clips"))
check("Select Clip puts just that clip in the field and closes", val.endswith("Chapter 0/clip_c0_02.mp4") and pk.property("opened") is False, val)
QMetaObject.invokeMethod(btn[0], "clicked"); pump(0.8)
check("reopened on that clip: its folder, the clip highlighted", same(pk.property("path"), src)
      and str(pk.property("sel")).endswith("clip_c0_02.mp4"))

# back / up / forward
QTest.keyClick(win, Qt.Key_Up, Qt.AltModifier); pump(0.4)
check("Alt+Up: the folder above, the one it came from highlighted",
      same(pk.property("path"), src.parent) and same(pk.property("sel"), src), pk.property("path"))
ch1 = rowfor("pickerFolder", "Chapter 1")
QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, centre(ch1)); pump(0.4)
check("one click on a folder highlights it -- the picker stays where it is",
      same(pk.property("sel"), src.parent / "Chapter 1") and same(pk.property("path"), src.parent) and pk.property("opened") is True)
dclick(rowfor("pickerFolder", "Chapter 1"))
check("a double-click on a folder goes in", same(pk.property("path"), src.parent / "Chapter 1") and pk.property("opened") is True,
      pk.property("path"))
QMetaObject.invokeMethod(rows("pickerBack")[0], "clicked"); pump(0.4)
check("Back goes back", same(pk.property("path"), src.parent))
QTest.keyClick(win, Qt.Key_Right, Qt.AltModifier); pump(0.4)
check("...Alt+Right forward again", same(pk.property("path"), src.parent / "Chapter 1"))
QMetaObject.invokeMethod(rows("pickerUp")[0], "clicked"); pump(0.4)
check("Up goes up a folder", same(pk.property("path"), src.parent))
# Select with nothing highlighted: the folder shown
pk.setProperty("sel", ""); pump(0.2)
check("nothing highlighted: Select Folder picks the folder shown", rows("pickerSelect")[0].property("text") == "Select Folder"
      and same(pk.property("choice"), src.parent))

# the address bar: Ctrl+L, a typed path, Enter
QTest.keyClick(win, Qt.Key_L, Qt.ControlModifier); pump(0.3)
addr = [c for c in walk(pk.property("contentItem"), []) if c.objectName() == "pickerAddress"][0]
check("Ctrl+L: the path as text, ready to type over", addr.isVisible() and addr.property("activeFocus") is True
      and addr.property("selectedText") == str(src.parent))
addr.setProperty("text", '"' + str(ep / "2. E2 Depth Maps") + '"'); QTest.keyClick(win, Qt.Key_Return); pump(0.4)
check("...a path pasted (quotes and all) and Enter goes there", same(pk.property("path"), ep / "2. E2 Depth Maps") and not addr.isVisible())
QTest.keyClick(win, Qt.Key_L, Qt.ControlModifier); pump(0.3)
addr.setProperty("text", str(ep / "nowhere")); QTest.keyClick(win, Qt.Key_Return); pump(0.4)
check("...a path that isn't there goes nowhere", same(pk.property("path"), ep / "2. E2 Depth Maps") and addr.isVisible())
QTest.keyClick(win, Qt.Key_Escape); pump(0.3)
check("...Esc puts the path back, the picker stays open", not addr.isVisible() and pk.property("opened") is True)

# search through the folders below
QMetaObject.invokeMethod(pk, "goTo", Q_ARG("QVariant", str(ep))); pump(0.4)
pk.setProperty("search", "clip_c1"); pump(0.4)
hits = [str(c.property("path")) for c in rows("pickerClip")]
check("search finds clips in the folders below", len(hits) == 5 and all("clip_c1_" in h for h in hits), str(len(hits)))
pk.setProperty("search", ""); pump(0.3)

# sort: a click on a heading, again the other way
QMetaObject.invokeMethod(pk, "goTo", Q_ARG("QVariant", str(src.parent / "Chapter 1"))); pump(0.4)
first = lambda: str(rows("pickerClip")[0].property("path")) if rows("pickerClip") else ""
heads = rows("pickerHeading")
QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, centre(heads[0])); pump(0.4)
check("a click on Name sorts Z to A", pk.property("asc") is False and first().endswith("clip_c1_04.mp4"), first())
QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, centre(heads[0])); pump(0.4)
check("...again: A to Z", pk.property("asc") is True and first().endswith("clip_c1_00.mp4"), first())

# the views, kept
for view, kind in (("tiles", "Tiles"), ("icons", "Large icons")):
    shell.setProperty("pickerView", view); pump(0.4)
    check(f"{kind}: the clips shown there instead", len(rows("pickerClip")) == 5 and not rows("pickerHeading"))
grid_clip = rows("pickerClip")[0]
QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, centre(grid_clip)); pump(0.3)
check("...one click highlights there too", str(pk.property("sel")) == str(grid_clip.property("path")))
shell.setProperty("pickerPreview", False); pump(0.3)
check("the preview pane hides", not rows("pickerPreview"))
import json
shell.save() if hasattr(shell, "save") else None
cfg = json.loads((tmp / "config" / "studio.json").read_text(encoding="utf-8"))
check("...the view and the pane kept in Studio's settings", cfg.get("picker_view") == "icons" and cfg.get("picker_preview") is False,
      str((cfg.get("picker_view"), cfg.get("picker_preview"))))
shell.setProperty("pickerView", "details"); shell.setProperty("pickerPreview", True); pump(0.3)

# a folder or clip dropped from Explorer
from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtGui import QDropEvent, QDragEnterEvent, QDragMoveEvent
def drop(path):
    md = QMimeData(); md.setUrls([QUrl.fromLocalFile(str(path))])
    at = QPointF(centre(rows("pickerList")[0]))
    QGuiApplication.sendEvent(win, QDragEnterEvent(at.toPoint(), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier)); pump(0.1)
    QGuiApplication.sendEvent(win, QDragMoveEvent(at.toPoint(), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier)); pump(0.1)
    QGuiApplication.sendEvent(win, QDropEvent(at, Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier)); pump(0.4)
drop(ep / "3. E2 Splats")
check("a folder dropped on it from Explorer: it goes there", same(pk.property("path"), ep / "3. E2 Splats"), pk.property("path"))
drop(src / "clip_c0_01.mp4")
check("...a clip: its folder, the clip highlighted", same(pk.property("path"), src) and str(pk.property("sel")).endswith("clip_c0_01.mp4"))

# a double-click on a clip picks it
dclick(rowfor("pickerClip", "clip_c0_03.mp4"))
val = str(st.value("input_source_clips"))
check("a double-click on a clip picks it and closes", val.endswith("Chapter 0/clip_c0_03.mp4") and pk.property("opened") is False, val)
QMetaObject.invokeMethod(btn[0], "clicked"); pump(0.8)

# right-click: New Folder, named in place (user, 2026-10-05)
body = rows("pickerList")[0]
below = body.mapToScene(QPointF(body.width() / 2, body.height() - 20))
QTest.mouseClick(win, Qt.RightButton, Qt.NoModifier, QPoint(int(below.x()), int(below.y()))); pump(0.5)
menu0 = [o for o in pk.findChildren(QObject) if o.objectName() == "pickerNewMenu"][0]
check("right-click on the empty part offers New Folder", menu0.property("opened") is True)
QMetaObject.invokeMethod(menu0, "close"); pump(0.3)
QTest.mouseClick(win, Qt.RightButton, Qt.NoModifier, centre(rows("pickerClip")[-1])); pump(0.5)
imenu = [o for o in pk.findChildren(QObject) if o.objectName() == "pickerItemMenu"][0]
texts = [str(o.property("text")) for o in imenu.findChildren(QObject) if "MenuItem" in o.metaObject().className()]
check("...and right-click on a clip has New Folder too", imenu.property("opened") is True and menu0.property("opened") is False
      and "New Folder" in texts, str(texts))
QMetaObject.invokeMethod(imenu, "close"); pump(0.3)
QMetaObject.invokeMethod(rows("pickerNewFolder")[0], "clicked"); pump(0.5)
box = [c for c in walk(pk.property("contentItem"), []) if c.objectName() == "pickerNewName" and c.isVisible()]
check("New Folder: a name box over the list, the name selected, ready to type",
      len(box) == 1 and box[0].property("activeFocus") is True and box[0].property("selectedText") == "New folder")
box[0].setProperty("text", "Extras"); QTest.keyClick(win, Qt.Key_Return); pump(0.6)
made = src / "Extras"
check("...Enter makes it and highlights it", made.is_dir() and same(pk.property("sel"), made) and pk.property("opened") is True,
      pk.property("sel"))
QMetaObject.invokeMethod(pk, "beginNew"); pump(0.4)
box = [c for c in walk(pk.property("contentItem"), []) if c.objectName() == "pickerNewName" and c.isVisible()]
box[0].setProperty("text", "Extras"); QTest.keyClick(win, Qt.Key_Return); pump(0.4)
check("...a name that is there already is refused, saying why", "already" in pk.property("createError"), pk.property("createError"))
box[0].setProperty("text", "bad:name"); QTest.keyClick(win, Qt.Key_Return); pump(0.4)
check("...so is one Windows won't take", "can't contain" in pk.property("createError"), pk.property("createError"))
QTest.keyClick(win, Qt.Key_Escape); pump(0.4)
check("...Esc cancels the name, the picker stays open", pk.property("creatingIn") == "" and pk.property("opened") is True)

# ---- Move to Recycle Bin (user, 2026-10-10) ------------------------------------
def menu(name):
    return [o for o in pk.findChildren(QObject) if o.objectName() == name][0]
def item(m, name):
    return [o for o in m.findChildren(QObject) if o.objectName() == name][0]
confirm = menu("pickerConfirm")
def answer(text):
    btns = [c for c in walk(confirm.property("contentItem"), []) if c.isVisible()
            and c.metaObject().className().startswith("AccentButton") and c.property("text") == text]
    if btns:
        QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, centre(btns[0])); pump(0.5)
    return bool(btns)
QMetaObject.invokeMethod(pk, "goTo", Q_ARG("QVariant", str(junk))); pump(0.6)
victim = rowfor("pickerClip", "old_03.mp4")
QTest.mouseClick(win, Qt.RightButton, Qt.NoModifier, centre(victim)); pump(0.5)
im, nm = menu("pickerItemMenu"), menu("pickerNewMenu")
check("right-click on a clip: its own menu (and not the list's as well)",
      im.property("opened") is True and nm.property("opened") is False,
      f"item menu {im.property('opened')}, list menu {nm.property('opened')}")
QMetaObject.invokeMethod(item(im, "pickerRecycle"), "click"); pump(0.6)
msg = str(confirm.property("message"))
check("...Move to Recycle Bin asks first, naming it; the picker stays open under it",
      confirm.property("opened") is True and "old_03.mp4" in msg and "Recycle Bin" in msg
      and pk.property("opened") is True, msg[:80])
check("...Cancel answers it", answer("Cancel"))
check("...and keeps the clip; nothing went to the bin",
      (junk / "old_03.mp4").exists() and not binned and pk.property("opened") is True)
QTest.mouseClick(win, Qt.RightButton, Qt.NoModifier, centre(rowfor("pickerClip", "old_03.mp4"))); pump(0.5)
QMetaObject.invokeMethod(item(im, "pickerRecycle"), "click"); pump(0.6)
answer("Move to Recycle Bin")
check("...Move to Recycle Bin sends it to the bin", binned == [str(junk / "old_03.mp4")]
      and not (junk / "old_03.mp4").exists(), str(binned))
names = [str(r.property("path")) for r in rows("pickerClip")]
check("...the list is read again without it, the picker still open",
      len(names) == 4 and not any(n.endswith("old_03.mp4") for n in names) and pk.property("opened") is True, str(len(names)))
# Delete asks too
QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, centre(rows("pickerClip")[0])); pump(0.3)
QTest.keyClick(win, Qt.Key_Delete); pump(0.5)
check("Delete on a highlighted clip asks the same", confirm.property("opened") is True and "Recycle Bin" in str(confirm.property("message")))
answer("Cancel")
# a refusal is said, the clip kept
bin_ok[0] = False
victim = rows("pickerClip")[0]; vpath = str(victim.property("path"))
QTest.mouseClick(win, Qt.RightButton, Qt.NoModifier, centre(victim)); pump(0.5)
QMetaObject.invokeMethod(item(im, "pickerRecycle"), "click"); pump(0.6)
answer("Move to Recycle Bin"); pump(0.3)
check("...when Windows refuses, it says so and the clip stays",
      confirm.property("opened") is True and "couldn't" in str(confirm.property("message")) and os.path.exists(vpath),
      str(confirm.property("message"))[:80])
answer("OK"); bin_ok[0] = True
# the folder shown: gone, the picker steps back to its parent
QMetaObject.invokeMethod(pk, "goTo", Q_ARG("QVariant", str(junk.parent))); pump(0.5)
fold = rowfor("pickerFolder", "Old cut")
QTest.mouseClick(win, Qt.RightButton, Qt.NoModifier, centre(fold)); pump(0.5)
QMetaObject.invokeMethod(item(im, "pickerRecycle"), "click"); pump(0.6)
msg = str(confirm.property("message"))
check("right-click on a folder: asked with what's inside", "everything in it" in msg and "4 clips" in msg, msg[:90])
answer("Move to Recycle Bin")
check("...gone to the bin, no row left for it", not junk.exists() and rowfor("pickerFolder", "Old cut") is None)
QMetaObject.invokeMethod(pk, "goTo", Q_ARG("QVariant", str(made))); pump(0.5)
QMetaObject.invokeMethod(pk, "recycle", Q_ARG("QVariant", str(made))); pump(0.5)
check("...the folder shown going: the picker steps back to its parent", not made.exists() and same(pk.property("path"), src),
      pk.property("path"))

# ---- the four shortcuts (user, 2026-10-10) --------------------------------------
def chips():
    return [c for c in walk(pk.property("contentItem"), []) if c.objectName() == "pickerShortcut" and c.isVisible()]
sc = chips()
check("four shortcuts in the navigation pane, empty to start", len(sc) == 4 and all(c.property("label") == "+ Shortcut" for c in sc),
      str([c.property("label") for c in sc]))
keep = ep / "2. E2 Depth Maps" / "Chapter 2"
QMetaObject.invokeMethod(pk, "goTo", Q_ARG("QVariant", str(keep))); pump(0.6)
QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, centre(chips()[1])); pump(0.5)
saved = list(shell.property("pickerShortcuts"))
check("clicking an empty one keeps the folder there", same(saved[1], keep) and saved[0] == saved[2] == saved[3] == "", str(saved))
cfg = json.loads((tmp / "config" / "studio.json").read_text(encoding="utf-8"))
check("...saved with Studio's settings", same((cfg.get("picker_shortcuts") or ["", ""])[1], keep))
check("...it shows the folder's name", chips()[1].property("label") == "Chapter 2", chips()[1].property("label"))
QMetaObject.invokeMethod(pk, "goTo", Q_ARG("QVariant", str(ep))); pump(0.6)
QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, centre(chips()[1])); pump(0.6)
check("...a click on it goes there", same(pk.property("path"), keep), str(pk.property("path")))
QTest.mouseClick(win, Qt.RightButton, Qt.NoModifier, centre(chips()[1])); pump(0.5)
sm = menu("pickerShortcutMenu")
check("...right-click: change or remove it", sm.property("opened") is True)
QMetaObject.invokeMethod(item(sm, "pickerShortcutClear"), "click"); pump(0.5)
check("...Remove empties it", list(shell.property("pickerShortcuts"))[1] == "" and chips()[1].property("label") == "+ Shortcut")
# the tree: opened out to the folder shown, that row highlighted
tree_on = [c for c in walk(pk.property("contentItem"), []) if c.objectName() == "pickerTreeRow" and c.isVisible() and c.property("on")]
check("the navigation tree is opened out to the folder shown", len(tree_on) == 1 and tree_on[0].property("label") == "Chapter 2",
      str([c.property("label") for c in tree_on]))

# ---- Windows Explorer, as a backup (user, 2026-10-10) -----------------------------
wbtn = rows("pickerWindows")
check("a Windows Explorer button beside Cancel", len(wbtn) == 1)
QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, centre(wbtn[0])); pump(0.5)
wm = menu("pickerWindowsMenu")
check("...on a field that takes a clip too, it asks: a folder or a clip", wm.property("opened") is True)
QMetaObject.invokeMethod(wm, "close"); pump(0.3)
fdlg = menu("pickerWindowsFile")
check("...the clip dialog lists the clip types", "*.mp4" in str(list(fdlg.property("nameFilters"))), str(list(fdlg.property("nameFilters"))))
pickedClip = ep / "1. E2 Source Clips" / "Chapter 1" / "clip_c1_04.mp4"
QMetaObject.invokeMethod(fdlg, "open"); pump(0.5)
fdlg.setProperty("selectedFile", QUrl.fromLocalFile(str(pickedClip)))
# accepted as Windows' dialog sends it (offscreen, Qt's stand-in dialog would replace the
# selection with its own on accept())
QMetaObject.invokeMethod(fdlg, "accepted"); pump(0.3)
QMetaObject.invokeMethod(fdlg, "close"); pump(0.4)
val = str(st.value("input_source_clips"))
check("...a clip Windows' dialog picks goes in the field, the picker closed",
      val.endswith("Chapter 1/clip_c1_04.mp4") and pk.property("opened") is False, val)
QMetaObject.invokeMethod(pk, "close"); pump(0.4)

# Output Splatted: folders only
out = [p for p in pickers if p.property("allowFiles") is False]
check("Output Splatted's picker is folders only", len(out) >= 1)
po = [p for p in pickers if p.property("title") == "Output Splatted"][0]
po.setProperty("currentFolder", str(ep / "1. E2 Source Clips" / "Chapter 1"))
QMetaObject.invokeMethod(po, "open"); pump(0.8)
oclips = [c for c in walk(po.property("contentItem"), []) if c.objectName() == "pickerClip" and c.isVisible()]
if oclips:
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, centre(oclips[0])); pump(0.3)
    QTest.mouseDClick(win, Qt.LeftButton, Qt.NoModifier, centre(oclips[0])); pump(0.4)
check("...its clips are listed but a click doesn't pick one",
      len(oclips) == 5 and po.property("sel") == "" and po.property("opened") is True
      and str(po.property("choice")).endswith("Chapter 1"), po.property("sel"))
pobtn = [c for c in walk(po.property("contentItem"), []) if c.objectName() == "pickerWindows" and c.isVisible()]
podlg = [o for o in po.findChildren(QObject) if o.objectName() == "pickerWindowsFolder"][0]
pomenu = [o for o in po.findChildren(QObject) if o.objectName() == "pickerWindowsMenu"][0]
QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, centre(pobtn[0])); pump(0.6)
check("...its Windows Explorer button opens Windows' folder dialog straight away (no menu)",
      podlg.property("visible") is True and pomenu.property("opened") is False)
QMetaObject.invokeMethod(podlg, "reject"); pump(0.4)
check("...Cancel there leaves the picker open", po.property("opened") is True)
QMetaObject.invokeMethod(podlg, "open"); pump(0.5)
podlg.setProperty("selectedFolder", QUrl.fromLocalFile(str(ep / "3. E2 Splats" / "Chapter 2")))
QMetaObject.invokeMethod(podlg, "accept"); pump(0.5)
val = str(st.value("output_splatted"))
check("...a folder it picks goes in the field", val.endswith("3. E2 Splats/Chapter 2") and po.property("opened") is False, val)
QMetaObject.invokeMethod(po, "close"); pump(0.4)
theme = engine.singletonInstance("Studio", "Theme")
check("closed: nothing left counted as a modal", theme.property("modalOpen") is False)

# a narrow window, a deep folder, a long title (user, 2026-10-13): every row stays inside the picker
deep = ep / "1. E2 Source Clips" / "Chapter 1" / "a" / "b" / "c"
deep.mkdir(parents=True, exist_ok=True)
(deep / "a clip with a very long name that goes on and on and on.mp4").write_bytes(b"\0" * 64)
(deep / "d").mkdir(exist_ok=True)
def clipped(item, top):
    """`item` is drawn inside a parent that clips it (between it and `top`): what sticks out isn't seen."""
    p = item.parentItem()
    while p is not None and p is not top:
        if p.clip():
            return True
        p = p.parentItem()
    return False
for ww, wh, files in ((1000, 760, True), (1500, 1000, True), (1100, 800, False)):
    win.resize(ww, wh); pump(0.6)
    po.setProperty("allowFiles", files)
    po.setProperty("title", "Input: a folder, or a single video, image or sequence frame")
    po.setProperty("currentFolder", str(deep))
    QMetaObject.invokeMethod(po, "open"); pump(1.0)
    bg = po.property("background")
    right = bg.mapToScene(QPointF(bg.width(), 0)).x()
    ci = po.property("contentItem")
    out = sorted({(c.objectName() or c.metaObject().className(), round(c.mapToScene(QPointF(c.width(), 0)).x() - right))
                  for c in walk(ci, []) if c.isVisible() and c.width() > 0 and c.mapToScene(QPointF(c.width(), 0)).x() > right + 0.5
                  and "Shader" not in c.metaObject().className() and "Proxy" not in c.metaObject().className()
                  and not clipped(c, ci)}, key=lambda t: -t[1])
    check(f"{ww}-wide window{'' if files else ' (folders)'}: nothing in the picker sticks out past its edge", not out, str(out[:6]))
    QMetaObject.invokeMethod(po, "close"); pump(0.4)
win.resize(1500, 1000); pump(0.4)

for i in range(5):
    QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", i), Q_ARG("QVariant", True)); pump(0.6)
studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("every stage loads without QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
