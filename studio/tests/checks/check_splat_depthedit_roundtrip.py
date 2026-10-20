"""Splatting <-> the Depth Map Editor (user, 2026-09-28), in the real app,
offscreen, over a TEMP copy of the ``small`` fixture (never the user's clips):
Splatting's Edit Depth opens the Extra tab's Depth Map Editor on the clip
showing, with its depth map; a render there asks "Back to Splatting?"; Use in
Splatting puts the rendered map in the original's place (the original kept in
edited/originals), switches back to Splatting and reloads that clip; Edit Depth
again reads the kept original, not the edited map. Configs and last_jobs.json
redirected."""
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
FIX = _fixtures.copy("small", tmp / "fx")
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, QObject, QPoint, QPointF, Qt, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
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
def until(cond, sec=30):
    end = time.time() + sec
    while not cond() and time.time() < end:
        pump(0.05)
    return cond()
def walk(it, out):
    for c in it.childItems(): out.append(c); walk(c, out)
    return out
def find(it, pred):
    return next((c for c in walk(it, []) if pred(c)), None)
def click(item):
    QTest.mouseMove(win, QPoint(5, 5)); pump(0.1)
    c = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(c.x()), int(c.y())))
def md5(p):
    return hashlib.md5(Path(p).read_bytes()).hexdigest()

stage = find(win.contentItem(), lambda c: c.objectName() == "stage")
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 1), Q_ARG("QVariant", True)); pump(1)
sp = objs["Splatting"]; pv = sp.preview; de = objs["DepthEdit"]
for k, v in {"input_source_clips": str(FIX / "src").replace("\\", "/"), "input_depth_maps": str(FIX / "depth").replace("\\", "/"),
             "output_splatted": str(FIX / "out").replace("\\", "/"), "multi_map": False}.items():
    sp._set(k, v)
jc = pv.property("jobClips")
until(lambda: len(jc.property("clipPaths")) >= 1, 15)
# the SECOND clip of the batch: coming back must land on it, not the first (user, 2026-09-28)
clip = [p for p in jc.property("clipPaths") if "clipB" in p][0]
pv.showClip(clip)
until(lambda: pv.property("loaded") and not pv.property("loading"), 90); pump(0.5)
live = str(pv.property("currentDepth"))
check("Splatting has the clip loaded, with its depth map", pv.property("loaded") and live.endswith("clipB_depth.mp4"), live)
original = md5(live)

# ---- Edit Depth: the Depth Map Editor on this clip
btn = find(stage.property("pages").property(1).toQObject(), lambda c: c.objectName() == "editDepthMap")
check("an Edit Depth button, enabled", btn is not None and btn.property("enabled") is True)
click(btn)
extra = stage.property("pages").property(4).toQObject()
until(lambda: de.property("state").get("hasClip"), 90); pump(0.5)
S = lambda: de.property("state")  # noqa: E731
norm = lambda p: str(p).replace("\\", "/").lower()  # noqa: E731
check("...it opens the Extra tab's Depth Map Editor", stage.property("current") == 4 and extra.property("tool") == 1,
      f"stage {stage.property('current')} tool {extra.property('tool')}")
hist = os.path.join(os.path.dirname(live), "edit_history")
check("...on that clip and its depth map, the output the maps' edit_history folder",
      S().get("hasClip") and norm(S()["rgb_path"]) == norm(clip) and norm(S()["depth_path"]) == norm(live)
      and norm(S()["output_dir"]) == norm(hist),
      f"{S().get('rgb_path')} | {S().get('depth_path')} | {S().get('output_dir')}")

# ---- render -> "Back to Splatting?" -> Use in Splatting
prompts = [o for o in win.findChildren(QObject) if o.metaObject().className().startswith("PromptDialog")]
de.run("_render")
until(lambda: any(p.property("visible") and "Splatting" in str(p.property("title")) for p in prompts), 90)
ask = next((p for p in prompts if p.property("visible") and "Splatting" in str(p.property("title"))), None)
written = os.path.join(hist, "clipB_depth.mp4")
check("a render there asks \"Back to Splatting?\"", ask is not None and os.path.isfile(written),
      str([p.property("title") for p in prompts if p.property("visible")]))
rendered = md5(written) if os.path.isfile(written) else None
if ask is not None:
    QMetaObject.invokeMethod(ask, "finish", Q_ARG("QVariant", 1)); pump(0.5)
until(lambda: stage.property("current") == 1 and pv.property("loaded") and not pv.property("loading"), 90); pump(0.8)
backup = os.path.join(hist, "clipB_depth.original.mp4")
check("Use in Splatting: the rendered map is now the one Splatting reads", md5(live) == rendered)
check("...the original kept as edit_history/clipB_depth.original.mp4, the render moved out of it",
      os.path.isfile(backup) and md5(backup) == original and not os.path.exists(written))
until(lambda: norm(list(pv.property("clipPaths"))[pv.property("videoIndex")]) == norm(clip), 20); pump(0.5)
check("...back on Splatting, the SAME clip reloaded (the batch's second, not its first)", stage.property("current") == 1
      and pv.property("videoIndex") == 1
      and norm(list(pv.property("clipPaths"))[pv.property("videoIndex")]) == norm(clip) and pv.property("loaded"),
      f"stage {stage.property('current')}")

# ---- Edit Depth again: the editor reads the kept original, not the edited map
click(btn)
until(lambda: norm(de.property("state").get("depth_path")) == norm(backup) and de.property("state").get("hasClip"), 90)
check("Edit Depth again reads the kept original (the edits are not applied twice)",
      norm(S().get("depth_path")) == norm(backup), str(S().get("depth_path")))

# ---- a second trip (user 2026-09-29: each one nested edited/edited/originals/...)
first_edit = md5(live)
de.run("_render")
until(lambda: any(p.property("visible") and "Splatting" in str(p.property("title")) for p in prompts), 90)
ask = next((p for p in prompts if p.property("visible") and "Splatting" in str(p.property("title"))), None)
if ask is not None:
    QMetaObject.invokeMethod(ask, "finish", Q_ARG("QVariant", 1)); pump(0.5)
until(lambda: stage.property("current") == 1 and pv.property("loaded") and not pv.property("loading"), 90); pump(0.8)
depth_dir = os.path.dirname(live)
subdirs = sorted(os.path.relpath(os.path.join(r, d), depth_dir).replace("\\", "/")
                 for r, ds, _f in os.walk(depth_dir) for d in ds)
check("a second trip: the first edit kept as edit_history/clipB_depth.edit-01.mp4, the original untouched",
      os.path.isfile(os.path.join(hist, "clipB_depth.edit-01.mp4"))
      and md5(os.path.join(hist, "clipB_depth.edit-01.mp4")) == first_edit and md5(backup) == original,
      str(sorted(os.listdir(hist))))
check("...and no folder but edit_history beside the maps", [d for d in subdirs if d != "sidecars"] == ["edit_history"],
      str(subdirs))

pv.shutdown()
studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
