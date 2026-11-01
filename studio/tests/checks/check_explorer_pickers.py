"""Settings > Studio > Pick Folders with Windows Explorer (user, 2026-10-31: off by default; some
prefer the basic window). In the real app, offscreen, over a made-up folder tree in a temp folder:

* off by default, and its item is in Settings > Studio;
* off: Browse opens Studio's own picker, as before;
* on: Browse on a folder-or-clip field asks Pick a folder / Pick a clip at the pointer, and the
  one picked opens Windows' dialog -- Studio's picker never shows; what it picks goes in the field;
* on: a folders-only field opens Windows' folder dialog straight away, on the field's folder;
* no QML warnings.

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
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, QObject, QUrl, Q_ARG, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from studio import app as A

src = tmp / "Show" / "Source" / "Chapter 0"
outd = tmp / "Show" / "Splats" / "Chapter 0"
for d in (src, outd):
    d.mkdir(parents=True)
(src / "clip_00.mp4").write_bytes(b"")

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
shell = objs["Shell"]
shell.setProperty("uiScale", "1.0")
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


def named(parent, name):
    return [o for o in parent.findChildren(QObject) if o.objectName() == name]


pump(1.0)
check("off by default", shell.property("explorerPickers") is False)
item = named(win, "explorerPickersItem")
check("...its item is in Settings > Studio", len(item) == 1
      and item[0].property("text") == "Pick Folders with Windows Explorer"
      and item[0].property("checkable") is True and item[0].property("checked") is False)

stage = [c for c in walk(win.contentItem(), []) if c.objectName() == "stage" and c.isVisible()][0]
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 1), Q_ARG("QVariant", True)); pump(1.0)
sp = objs["Splatting"]
sp._set("input_source_clips", str(src).replace("\\", "/"))
sp._set("output_splatted", str(outd).replace("\\", "/")); pump(0.3)
st = sp.property("settings")
page = stage.property("pages").property(1).toQObject()
pickers = named(page, "folderPicker")
btn = [c for c in walk(page, []) if c.objectName() == "browseSource"][0]
pk = [p for p in pickers if p.property("allowFiles") is True][0]
po = [p for p in pickers if p.property("title") == "Output Splatted"][0]

# off: Studio's own picker
QMetaObject.invokeMethod(btn, "clicked"); pump(0.8)
opened = [p for p in pickers if p.property("opened")]
check("off: Browse opens Studio's own picker", len(opened) == 1)
for p in opened:
    QMetaObject.invokeMethod(p, "close")
pump(0.5)

# on: the item turns it on, and it is saved
QMetaObject.invokeMethod(item[0], "toggle"); QMetaObject.invokeMethod(item[0], "triggered"); pump(0.3)
check("the item turns it on", shell.property("explorerPickers") is True)

menu = named(pk, "pickerExplorerMenu")[0]
fdlg = named(pk, "pickerWindowsFolder")[0]
cdlg = named(pk, "pickerWindowsFile")[0]
QMetaObject.invokeMethod(btn, "clicked"); pump(0.8)
check("on: Browse on a folder-or-clip field asks folder or clip -- Studio's picker never shows",
      menu.property("opened") is True and not any(p.property("opened") for p in pickers))
clip_item = named(menu, "pickerExplorerClip")[0]
QMetaObject.invokeMethod(clip_item, "triggered"); pump(0.6)
check("...Pick a clip opens Windows' file dialog", cdlg.property("visible") is True and pk.property("opened") is False)
cdlg.setProperty("selectedFile", QUrl.fromLocalFile(str(src / "clip_00.mp4")))
# accepted as Windows' dialog sends it (offscreen, Qt's stand-in dialog replaces the selection on accept())
QMetaObject.invokeMethod(cdlg, "accepted"); pump(0.3)
QMetaObject.invokeMethod(cdlg, "close"); pump(0.4)
val = str(st.value("input_source_clips"))
check("...the clip it picks goes in the field", val.endswith("Chapter 0/clip_00.mp4") and pk.property("opened") is False, val)

QMetaObject.invokeMethod(btn, "clicked"); pump(0.8)
QMetaObject.invokeMethod(named(menu, "pickerExplorerFolder")[0], "triggered"); pump(0.6)
check("...Pick a folder opens Windows' folder dialog", fdlg.property("visible") is True)
fdlg.setProperty("selectedFolder", QUrl.fromLocalFile(str(src.parent)))
QMetaObject.invokeMethod(fdlg, "accept"); pump(0.5)
val = str(st.value("input_source_clips"))
check("...the folder it picks goes in the field", val.endswith("Show/Source"), val)

podlg = named(po, "pickerWindowsFolder")[0]
pomenu = named(po, "pickerExplorerMenu")[0]
QMetaObject.invokeMethod(po, "open"); pump(0.8)
check("on: a folders-only field opens Windows' folder dialog straight away",
      podlg.property("visible") is True and pomenu.property("opened") is False and po.property("opened") is False)
check("...on the field's folder", os.path.normcase(QUrl(podlg.property("currentFolder")).toLocalFile())
      == os.path.normcase(str(outd)), str(podlg.property("currentFolder")))
QMetaObject.invokeMethod(podlg, "reject"); pump(0.4)
check("...Cancel there changes nothing", str(st.value("output_splatted")).endswith("Splats/Chapter 0")
      and po.property("opened") is False)
theme = engine.singletonInstance("Studio", "Theme")
check("nothing left counted as a modal", theme.property("modalOpen") is False)

shell.setProperty("explorerPickers", False); pump(0.2)
QMetaObject.invokeMethod(po, "open"); pump(0.8)
check("off again: Studio's own picker", po.property("opened") is True)
QMetaObject.invokeMethod(po, "close"); pump(0.4)

studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
