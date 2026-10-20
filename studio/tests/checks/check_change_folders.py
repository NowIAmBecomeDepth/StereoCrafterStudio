"""Change Folders / Hide Folders on every stage (user, 2026-09-30), in the
real app offscreen. On Manual paths the button is there too: Hide Folders
puts the stage's folder fields away (remembered in Studio's settings), Change
Folders brings them back. With a chapter picked the fields fold by themselves,
as before, whatever Manual paths was left at. Configs and last_jobs.json
redirected; the project is a made-up temp one."""
import os, sys, tempfile, hashlib, time
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, str(Path(__file__).parent))
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
tmp = Path(tempfile.mkdtemp())
import _fixtures
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, QPoint, QPointF, Qt, qInstallMessageHandler
from PySide6.QtTest import QTest
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from studio import app as A

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
shell = objs["Shell"]
shell.setProperty("uiScale", "1.0")
shell.setProperty("projectFolder", "")
win.show()
fails = []
def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok: fails.append(label)
def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.005)
def find_all(it, pred, out=None):
    out = [] if out is None else out
    for c in it.childItems():
        if pred(c): out.append(c)
        find_all(c, pred, out)
    return out
def click(item):
    c = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(c.x()), int(c.y()))); pump(0.5)
stage = find_all(win.contentItem(), lambda c: c.objectName() == "stage")[0]
win.setProperty("userSized", True); win.setWidth(1700); win.setHeight(1300); pump(0.8)
pump(0.6)

STAGES = [(0, "DepthCrafter", "depthcrafter"), (1, "Splatting", "splatting"), (2, "Inpainting", "inpainting"),
          (3, "Merging", "merging")]
def norm(t): return str(t or "").rstrip(":").strip()
def state(i):
    page = stage.property("pages").property(i).toQObject()
    btn = [b for b in find_all(page, lambda c: c.objectName() == "changeFolders") if b.isVisible()]
    _paths = btn[0].property("paths") if btn else []
    _paths = _paths.toVariant() if hasattr(_paths, "toVariant") else _paths
    labels = {norm(p["label"]) for p in (_paths or [])}
    fields = [f for f in find_all(page, lambda c: norm(c.property("label")) in labels and c.property("label") is not None)
              if f.isVisible()]
    return page, (btn[0] if btn else None), len(fields)

for i, name, sid in STAGES:
    QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", i), Q_ARG("QVariant", True)); pump(1.0)
    page, btn, n = state(i)
    check(f"{name}, Manual paths: the button is there, reading Hide Folders, fields shown",
          btn is not None and btn.property("text") == "Hide Folders" and n >= 2, f"{btn and btn.property('text')} {n} fields")
    if btn is None:
        continue
    click(btn)
    page, btn, n = state(i)
    check(f"...Hide Folders puts them away (remembered), the button now Change Folders",
          n == 0 and btn.property("text") == "Change Folders" and shell.property("foldersHidden").get(sid) is True,
          f"{n} fields, {btn.property('text')}, {shell.property('foldersHidden')}")
    click(btn)
    page, btn, n = state(i)
    check(f"...Change Folders brings them back", n >= 2 and not shell.property("foldersHidden").get(sid), f"{n} fields")

# remembered: hidden stays hidden when the page comes back
shell.setFoldersHidden("depthcrafter", True)
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 1), Q_ARG("QVariant", True)); pump(0.6)
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 0), Q_ARG("QVariant", True)); pump(0.8)
page, btn, n = state(0)
check("hidden on Manual paths stays hidden (Studio's settings, not the page)", n == 0 and btn.property("text") == "Change Folders")
check("...saved to studio.json", '"depthcrafter": true' in (ss.CONFIG_DIR / "studio.json").read_text(encoding="utf-8"))

# a chapter picked: the fields fold by themselves; Change Folders there as before
proj = tmp / "Demo Movie"
for sub in ("DM Source Clips", "DM DepthMaps", "DM Splats", "DM Inpainted", "DM Converted LR Clips"):
    (proj / sub / "Chapter 1 - Opening").mkdir(parents=True)
shell.setProperty("projectFolder", str(proj).replace("\\", "/")); pump(0.5)
shell.setFoldersHidden("depthcrafter", False)
dc = objs["DepthCrafter"]
QMetaObject.invokeMethod(dc, "refreshChapters"); pump(0.3)
QMetaObject.invokeMethod(dc, "pickChapter", Q_ARG(int, 1)); pump(0.8)
page, btn, n = state(0)
check("a chapter picked: the fields fold by themselves (Manual paths was left shown)",
      page.property("chapterMode") is True and n == 0 and btn.property("text") == "Change Folders",
      f"chapterMode {page.property('chapterMode')} {n} fields")
click(btn)
page, btn, n = state(0)
check("...Change Folders shows them, without touching the Manual paths choice",
      n >= 2 and not shell.property("foldersHidden").get("depthcrafter"))

check("no QML warnings", not warns, str(warns[:3]))
h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
check("the real last_jobs.json untouched", h0 == h1)
print("PASS" if not fails else f"FAIL ({len(fails)})")
_fixtures.exit_now(0 if not fails else 1)
