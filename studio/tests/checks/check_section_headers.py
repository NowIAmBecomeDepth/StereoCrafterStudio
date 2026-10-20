"""No section header runs past its section's edge (user, 2026-09-26: the
chapter dropdown crossed DepthCrafter's Input Source outline in a narrow
window). A project is set so every page's header carries its chapter
dropdown; each stage is measured at the window's minimum width and at a
wide one. Configs and last_jobs.json redirected."""
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
# every stage's "Import from ..." button showing, as in the user's window:
# a synthetic handoff record (in the redirected last_jobs.json)
_src = tmp / "project" / "handoff"     # inside the picked project: an Import offers only its jobs
for _stage, _f in (("scenecut", {"source": str(_src / "clips")}), ("depthcrafter", {"depth": str(_src / "depth")}),
                   ("splatting", {"splat_out": str(_src / "splat"), "hires": str(_src / "hi"), "lowres": str(_src / "lo")}),
                   ("inpainting", {"inpainted_out": str(_src / "inp")}),
                   ("inpainting_v2", {"inpainted_out": str(_src / "inp2")})):   # Merging's second button
    jh.record_job(_stage, _f)
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, QPointF, Q_ARG, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from studio import app as A

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
objs["Shell"].setProperty("uiScale", "1.0")
win.show()
fails = []
def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok: fails.append(label)
def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.003)
def walk(it, out):
    for c in it.childItems():
        out.append(c); walk(c, out)
    return out

# a synthetic project (core/ui/chapters.py's layout) with the user's long chapter name
project = tmp / "project"
ch = "Chapter 2 - The Origin"
for d in ("SM Source Clips/" + ch, "SM DepthMaps/" + ch, "SM Splats/" + ch + "/hires", "SM Splats/" + ch + "/lowres",
          "SM Inpainted/" + ch, "SM Converted LR Clips/" + ch):
    (project / d).mkdir(parents=True)
objs["Shell"].setProperty("projectFolder", str(project))
pump(1)
# a chapter picked: the header also carries Change Folders, as in the user's window
for c in ("DepthCrafter", "Splatting", "Inpainting", "Merging"):
    objs[c].pickChapter(2)
pump(1)
stage = [c for c in walk(win.contentItem(), []) if c.objectName() == "stage"][0]
names = ["DepthCrafter", "Splatting", "Inpainting", "Merging", "Extra"]
for i, name in enumerate(names):
    QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", i), Q_ARG("QVariant", True)); pump(1)
    for width in (int(win.property("minimumWidth")), 1900):
        win.setWidth(width); pump(0.6)
        page = stage.property("pages").property(i).toQObject()
        worst, where, pickers, imports = 0.0, "", 0, 0
        for sec in walk(page, []):
            if not sec.metaObject().className().startswith("Section_") or not sec.isVisible():
                continue
            right = sec.width() - 1            # inside the outline
            for c in walk(sec, []):
                if not c.isVisible() or c.width() <= 0:
                    continue
                n = c.metaObject().className()
                if n.startswith("QGfxSourceProxy"):
                    continue            # a filled button's glow (MultiEffect's padded proxy), not content
                if n.startswith("ChapterPicker") and c.property("currentText") == ch:
                    pickers += 1
                if n.startswith("AccentButton") and str(c.property("text")).startswith("Import from"):
                    imports += 1
                p = c.mapToItem(sec, QPointF(c.width(), 0))
                if p.x() - right > worst:
                    worst, where = p.x() - right, f"{sec.property('title')!r}: {n.split('_')[0]}"
        check(f"{name} at {width}px: nothing past a section's edge", worst <= 0.5, f"{worst:.0f}px over at {where}" if worst > 0.5 else "")
        if name != "Extra":
            check(f"{name} at {width}px: the chapter dropdown is there, on the chapter", pickers >= 1)
            check(f"{name} at {width}px: and an Import button", imports >= 1)

studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
