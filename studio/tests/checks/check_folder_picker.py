"""Studio's Columns folder picker (user, 2026-10-05) on Splatting's fields, in
the real app, offscreen, over a made-up folder tree in a temp folder:

* Input Source Clips has ONE Browse button; it opens the picker on the
  field's folder;
* one click on a folder highlights it and opens the next column -- the picker
  stays open; a double-click picks the folder and closes;
* a clip can be highlighted and picked with Select Clip (the field gets the
  clip: Splatting then does just that one);
* Output Splatted is folders only: its clips are listed but can't be picked;
* right-click a clip or folder: Move to Recycle Bin, asked first (Cancel keeps
  it); the bin is a stand-in -- the user's own is never touched; a folder that
  is open goes, and the picker steps back to its parent; a refusal is said
  (2026-10-10);
* the four shortcuts under the drives: an empty one keeps the highlighted
  folder (saved), a set one goes there, right-click removes it (2026-10-10);
* every stage's page loads without QML warnings.

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
check("Splatting's folder fields use the Columns picker (no Windows folder dialog)", len(pickers) >= 3, str(len(pickers)))
btn = [c for c in walk(page, []) if c.objectName() == "browseSource"]
check("Input Source Clips has one Browse button", len(btn) == 1 and btn[0].property("text") == "Browse")
QMetaObject.invokeMethod(btn[0], "clicked"); pump(0.8)
picker = [p for p in pickers if p.property("opened")]
check("Browse opens the picker on the field's folder", len(picker) == 1
      and os.path.normcase(picker[0].property("sel")) == os.path.normcase(str(src)), picker[0].property("sel") if picker else "")
pk = picker[0]
check("...that takes a folder or a single clip", pk.property("allowFiles") is True)


def rows(kind):
    return [c for c in walk(pk.property("contentItem"), []) if c.objectName() == kind and c.isVisible()]


# one click on a folder: highlighted and opened, the picker stays open
target = [r for r in rows("pickerFolder") if str(r.property("path")).endswith("Chapter 1")][0]
QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, centre(target)); pump(0.5)
check("one click on a folder opens it in the next column -- the picker stays open",
      pk.property("opened") is True and str(pk.property("leaf")).endswith("Chapter 1"), pk.property("leaf"))
clips = rows("pickerClip")
check("...and its clips are listed", len(clips) == 5, str(len(clips)))
# a clip: highlighted, Select Clip picks just it
QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, centre(clips[2])); pump(0.4)
sel_btn = rows("pickerSelect")[0]
check("one click on a clip highlights it; the button says Select Clip",
      str(pk.property("sel")).endswith("clip_c1_02.mp4") and sel_btn.property("text") == "Select Clip", pk.property("sel"))
QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, centre(sel_btn)); pump(0.5)
val = str(st.value("input_source_clips"))
check("Select Clip puts just that clip in the field and closes", val.endswith("Chapter 1/clip_c1_02.mp4")
      and pk.property("opened") is False, val)
# reopen: on the clip; double-click a folder picks it
QMetaObject.invokeMethod(btn[0], "clicked"); pump(0.8)
check("reopened on that clip", str(pk.property("sel")).endswith("clip_c1_02.mp4"), pk.property("sel"))
ch0 = [r for r in rows("pickerFolder") if str(r.property("path")).endswith("Chapter 0")][0]
QTest.mouseDClick(win, Qt.LeftButton, Qt.NoModifier, centre(ch0)); pump(0.5)
val = str(st.value("input_source_clips"))
check("double-click on a folder picks it and closes", val.endswith("1. E2 Source Clips/Chapter 0") and pk.property("opened") is False, val)

# the open folder with no folders in it: no empty "No folders" column before its clips
QMetaObject.invokeMethod(btn[0], "clicked"); pump(0.8)
heads = [c.property("text") for c in walk(pk.property("contentItem"), []) if c.isVisible()
         and c.metaObject().className().startswith("QQuickText") and c.property("text") in ("Chapter 0", "CHAPTER 0", "Clips inside")]
cols_shown = pk.property("colsShown")
cols_shown = cols_shown.toVariant() if hasattr(cols_shown, "toVariant") else cols_shown
check("an open folder with no folders inside has no empty column of its own",
      not any(str(c).endswith("Chapter 0") for c in cols_shown), str(cols_shown))

# right-click a column: New Folder, named in place (user, 2026-10-05)
# (2026-10-10: a row's right-click has its own menu -- New Folder is on it too)
clipcol = [c for c in walk(pk.property("contentItem"), []) if c.objectName() == "pickerColumn" and c.property("last") is True][0]
below = clipcol.mapToScene(QPointF(clipcol.width() / 2, clipcol.height() - 30))
QTest.mouseClick(win, Qt.RightButton, Qt.NoModifier, QPoint(int(below.x()), int(below.y()))); pump(0.5)
menu = [o for o in pk.findChildren(QObject) if o.objectName() == "pickerNewMenu"][0]
check("right-click in a column offers New Folder", menu.property("opened") is True)
QMetaObject.invokeMethod(menu, "close"); pump(0.3)
colarea = [c for c in walk(pk.property("contentItem"), []) if c.objectName() == "pickerClip" and c.isVisible()]
QTest.mouseClick(win, Qt.RightButton, Qt.NoModifier, centre(colarea[-1])); pump(0.5)
imenu = [o for o in pk.findChildren(QObject) if o.objectName() == "pickerItemMenu"][0]
texts = [str(o.property("text")) for o in imenu.findChildren(QObject) if "MenuItem" in o.metaObject().className()]
check("...and right-click on a clip has New Folder too", imenu.property("opened") is True and menu.property("opened") is False
      and "New Folder" in texts, str(texts))
QMetaObject.invokeMethod(imenu, "close"); pump(0.3)
QMetaObject.invokeMethod(pk, "beginNew", Q_ARG("QVariant", str(ep / "1. E2 Source Clips" / "Chapter 0"))); pump(0.5)
box = [c for c in walk(pk.property("contentItem"), []) if c.objectName() == "pickerNewName" and c.isVisible()]
check("...a name box appears in that folder's column, the name selected, ready to type",
      len(box) == 1 and box[0].property("activeFocus") is True and box[0].property("selectedText") == "New folder")
box[0].setProperty("text", "Extras"); QTest.keyClick(win, Qt.Key_Return); pump(0.6)
made = ep / "1. E2 Source Clips" / "Chapter 0" / "Extras"
check("...Enter makes it, and opens it", made.is_dir() and str(pk.property("leaf")).endswith("Extras")
      and pk.property("opened") is True, pk.property("leaf"))
QMetaObject.invokeMethod(pk, "beginNew", Q_ARG("QVariant", str(ep / "1. E2 Source Clips" / "Chapter 0"))); pump(0.4)
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
ch1 = junk
QMetaObject.invokeMethod(pk, "goTo", Q_ARG("QVariant", str(ch1))); pump(0.6)
victim = [r for r in rows("pickerClip") if str(r.property("path")).endswith("old_03.mp4")][0]
QTest.mouseClick(win, Qt.RightButton, Qt.NoModifier, centre(victim)); pump(0.5)
im, nm = menu("pickerItemMenu"), menu("pickerNewMenu")
check("right-click on a clip: its own menu (and not the column's as well)",
      im.property("opened") is True and nm.property("opened") is False,
      f"item menu {im.property('opened')}, column menu {nm.property('opened')}")
QMetaObject.invokeMethod(item(im, "pickerRecycle"), "click"); pump(0.6)
msg = str(confirm.property("message"))
check("...Move to Recycle Bin asks first, naming it; the picker stays open under it",
      confirm.property("opened") is True and "old_03.mp4" in msg and "Recycle Bin" in msg
      and pk.property("opened") is True, msg[:80])
check("...Cancel answers it", answer("Cancel"))
check("...and keeps the clip; nothing went to the bin",
      (ch1 / "old_03.mp4").exists() and not binned and pk.property("opened") is True)
QTest.mouseClick(win, Qt.RightButton, Qt.NoModifier, centre(victim)); pump(0.5)
QMetaObject.invokeMethod(item(im, "pickerRecycle"), "click"); pump(0.6)
answer("Move to Recycle Bin")
check("...Move to Recycle Bin sends it to the bin", binned == [str(ch1 / "old_03.mp4")]
      and not (ch1 / "old_03.mp4").exists(), str(binned))
names = [str(r.property("path")) for r in rows("pickerClip")]
check("...the list is read again without it, the picker still open",
      len(names) == 4 and not any(n.endswith("old_03.mp4") for n in names) and pk.property("opened") is True, str(len(names)))
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
# a folder that is open: gone, the picker steps back to its parent
fold = [r for r in rows("pickerFolder") if str(r.property("path")).endswith("Old cut")][0]
QTest.mouseClick(win, Qt.RightButton, Qt.NoModifier, centre(fold)); pump(0.5)
QMetaObject.invokeMethod(item(im, "pickerRecycle"), "click"); pump(0.6)
msg = str(confirm.property("message"))
check("right-click on a folder: asked with what's inside", "everything in it" in msg and "4 clips" in msg, msg[:90])
answer("Move to Recycle Bin")
check("...gone to the bin, and the picker steps back to its parent",
      not ch1.exists() and os.path.normcase(str(pk.property("leaf"))) == os.path.normcase(str(ch1.parent)),
      str(pk.property("leaf")))
check("...no folder row left for it", not [r for r in rows("pickerFolder") if str(r.property("path")).endswith("Old cut")])

# ---- the four shortcuts (user, 2026-10-10) --------------------------------------
shell = objs["Shell"]
def chips():
    return [c for c in walk(pk.property("contentItem"), []) if c.objectName() == "pickerShortcut" and c.isVisible()]
sc = chips()
check("four shortcuts under the drives, empty to start", len(sc) == 4 and all(c.property("label") == "+ Shortcut" for c in sc),
      str([c.property("label") for c in sc]))
drive_y = [c for c in walk(pk.property("contentItem"), []) if c.isVisible() and c.metaObject().className().startswith("Chip")
           and str(c.property("label")).endswith(":")]
if sc and drive_y:
    check("...on the row below the drive letters", sc[0].mapToScene(QPointF(0, 0)).y() > drive_y[0].mapToScene(QPointF(0, 0)).y() + 10)
keep = ep / "2. E2 Depth Maps" / "Chapter 2"
QMetaObject.invokeMethod(pk, "goTo", Q_ARG("QVariant", str(keep))); pump(0.6)
QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, centre(chips()[1])); pump(0.5)
saved = list(shell.property("pickerShortcuts"))
check("clicking an empty one keeps the highlighted folder there", os.path.normcase(saved[1]) == os.path.normcase(str(keep))
      and saved[0] == saved[2] == saved[3] == "", str(saved))
import json
cfg = json.loads((tmp / "config" / "studio.json").read_text(encoding="utf-8"))
check("...saved with Studio's settings", os.path.normcase((cfg.get("picker_shortcuts") or ["", ""])[1]) == os.path.normcase(str(keep)))
check("...it shows the folder's name", chips()[1].property("label") == "Chapter 2", chips()[1].property("label"))
QMetaObject.invokeMethod(pk, "goTo", Q_ARG("QVariant", str(ep))); pump(0.6)
QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, centre(chips()[1])); pump(0.6)
check("...a click on it goes there", os.path.normcase(str(pk.property("leaf"))) == os.path.normcase(str(keep)), str(pk.property("leaf")))
QTest.mouseClick(win, Qt.RightButton, Qt.NoModifier, centre(chips()[1])); pump(0.5)
sm = menu("pickerShortcutMenu")
check("...right-click: change or remove it", sm.property("opened") is True)
QMetaObject.invokeMethod(item(sm, "pickerShortcutClear"), "click"); pump(0.5)
check("...Remove empties it", list(shell.property("pickerShortcuts"))[1] == "" and chips()[1].property("label") == "+ Shortcut")

# ---- Windows Explorer, as a backup (user, 2026-10-10) -----------------------------
wbtn = rows("pickerWindows")
check("a Windows Explorer button beside Cancel", len(wbtn) == 1)
QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, centre(wbtn[0])); pump(0.5)
wm = menu("pickerWindowsMenu")
check("...on a field that takes a clip too, it asks: a folder or a clip", wm.property("opened") is True)
QMetaObject.invokeMethod(wm, "close"); pump(0.3)
fdlg = menu("pickerWindowsFile")
check("...the clip dialog lists the clip types", "*.mp4" in str(list(fdlg.property("nameFilters"))), str(list(fdlg.property("nameFilters"))))
from PySide6.QtCore import QUrl
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
# Output Splatted: folders only
out = [p for p in pickers if p.property("allowFiles") is False]
check("Output Splatted's picker is folders only", len(out) >= 1)
po = [p for p in pickers if p.property("title") == "Output Splatted"][0]
po.setProperty("currentFolder", str(ep / "1. E2 Source Clips" / "Chapter 1"))
QMetaObject.invokeMethod(po, "open"); pump(0.8)
oclips = [c for c in walk(po.property("contentItem"), []) if c.objectName() == "pickerClip" and c.isVisible()]
if oclips:
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, centre(oclips[0])); pump(0.3)
check("...its clips are listed but a click doesn't pick one",
      len(oclips) == 5 and str(po.property("sel")).endswith("Chapter 1"), po.property("sel"))
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
# (the one-line hint and a horizontal list's content width made them all as wide as themselves),
# and the columns scroll to whole ones (fixed 260-px columns left part of one at the left edge)
deep = ep / "1. E2 Source Clips" / "Chapter 1" / "a" / "b" / "c"
deep.mkdir(parents=True, exist_ok=True)
(deep / "clip.mp4").write_bytes(b"\0" * 64)
(deep / "d").mkdir(exist_ok=True)                       # four columns at the open folder, as the user's
for ww, wh, files in ((880, 700, True), (1500, 1000, True), (1100, 800, False)):
    win.resize(ww, wh); pump(0.6)
    po.setProperty("allowFiles", files)
    po.setProperty("title", "Input: a folder, or a single video, image or sequence frame")
    po.setProperty("currentFolder", str(deep / "clip.mp4") if files else str(deep))
    QMetaObject.invokeMethod(po, "open"); pump(1.0)
    bg = po.property("background")
    right = bg.mapToScene(QPointF(bg.width(), 0)).x()
    ci = po.property("contentItem")
    out = sorted({(c.objectName() or c.metaObject().className(), round(c.mapToScene(QPointF(c.width(), 0)).x() - right))
                  for c in walk(ci, []) if c.isVisible() and c.width() > 0 and c.mapToScene(QPointF(c.width(), 0)).x() > right + 0.5
                  and "Shader" not in c.metaObject().className() and "Proxy" not in c.metaObject().className()}, key=lambda t: -t[1])
    check(f"{ww}-wide window{'' if files else ' (folders)'}: nothing in the picker sticks out past its edge", not out, str(out[:6]))
    cv = [c for c in walk(ci, []) if c.objectName() == "pickerColumns"][0]
    x0 = cv.mapToScene(QPointF(0, 0)).x()
    spans = [(round(c.mapToScene(QPointF(0, 0)).x() - x0), round(c.width())) for c in walk(cv, []) if c.objectName() == "pickerColumn"]
    cut = [s for s in spans if s[0] < -1 and s[0] + s[1] > 1]
    check(f"...no column cut off at the left edge", not cut, str(spans))
    QMetaObject.invokeMethod(po, "close"); pump(0.4)
win.resize(1500, 1000); pump(0.4)

for i in range(5):
    QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", i), Q_ARG("QVariant", True)); pump(0.6)
studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("every stage loads without QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
