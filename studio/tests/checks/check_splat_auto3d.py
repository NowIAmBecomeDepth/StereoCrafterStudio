"""Auto 3D on the Splatting page, offscreen (Thomas's patch; user, 2026-11-07): the Auto 3D box under
Auto-Converge, off by default; its Min / Mid / Max boxes (10 / 40 / 80) shown only while it is ticked,
numbers only, their help there; the values reach the batch's settings; CLIP has its row in the Models
window. Configs and last_jobs.json redirected; no clip is loaded, nothing runs on the GPU.

    python studio/tests/checks/check_splat_auto3d.py
"""
import os, sys, tempfile, hashlib, time
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _fixtures  # noqa: E402
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
ss.CONFIG_DIR.mkdir(parents=True)
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG
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
def walk(it, out):
    for c in it.childItems(): out.append(c); walk(c, out)
    return out
def named(root, name):
    return [c for c in walk(root, []) if c.objectName() == name]
def shown(root, name):
    return [c for c in named(root, name) if c.isVisible()]

pump(0.6)
stage = named(win.contentItem(), "stage")[0]
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 1), Q_ARG("QVariant", True)); pump(1.0)
sp = objs["Splatting"]
page = stage.property("pages").property(1).toQObject()
st = page.property("st")
sp._set("output_settings_shown", True); pump(0.5)          # the section Auto 3D sits in, opened

box = shown(page, "autoStrength")
check("Auto 3D is on the page, off by default", len(box) == 1 and st.value("auto_strength") is False,
      str(st.value("auto_strength")))
check("...its help says what it does", "close-up" in str(box[0].property("help")) if box else False)
boxes = ("autoStrengthMin", "autoStrengthMid", "autoStrengthMax")
check("...Min / Mid / Max hidden while it's off", not any(shown(page, n) for n in boxes))
sp._set("auto_strength", True); pump(0.4)
check("...ticked, they show: 10 / 40 / 80", all(shown(page, n) for n in boxes)
      and [shown(page, n)[0].property("text") for n in boxes] == ["10.0", "40.0", "80.0"],
      str([shown(page, n)[0].property("text") for n in boxes if shown(page, n)]))
mid = shown(page, "autoStrengthMid")
if mid:
    mid[0].forceActiveFocus(); mid[0].setProperty("text", "35"); QMetaObject.invokeMethod(mid[0], "commit"); pump(0.3)
check("...typing 35 into Mid sets it", str(st.value("auto_strength_mid")) in ("35", "35.0"), str(st.value("auto_strength_mid")))
mx = shown(page, "autoStrengthMax")
if mx:
    mx[0].setProperty("text", "lots"); QMetaObject.invokeMethod(mx[0], "commit"); pump(0.3)
check("...Max refuses words", str(st.value("auto_strength_max")) in ("80.0", "80"), str(st.value("auto_strength_max")))

from studio.stages.splatting import settings as S
from core.splatting.batch_processing import ProcessingSettings
p = ProcessingSettings.from_config(S.config_for_run({k: st.value(k) for k in st.keys()}))
check("...the batch gets them", (p.auto_strength, p.auto_strength_min, p.auto_strength_mid, p.auto_strength_max)
      == (True, 10.0, 35.0, 80.0), str((p.auto_strength, p.auto_strength_min, p.auto_strength_mid, p.auto_strength_max)))
sp._set("auto_strength", False); pump(0.3)
check("...unticked, the boxes go again", not any(shown(page, n) for n in boxes))
sp._set("auto_strength", True); pump(0.2)
sp.resetDefaults(); pump(0.4)
check("Reset to Defaults: Auto 3D off, 10 / 40 / 80", st.value("auto_strength") is False
      and [float(st.value(k)) for k in ("auto_strength_min", "auto_strength_mid", "auto_strength_max")] == [10.0, 40.0, 80.0],
      str([st.value(k) for k in ("auto_strength", "auto_strength_min", "auto_strength_mid", "auto_strength_max")]))

from studio import model_catalog as M
m = M.MODELS_BY_KEY.get("auto3d")
check("CLIP has a row in the Models window, used by Splatting (Auto 3D)",
      m is not None and "Auto 3D" in m.used_by and m.parts[0].folder == "AutoStrength" and m.parts[0].sha256)

h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
print("last_jobs unchanged:", h0 == h1, flush=True)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(1 if fails or h0 != h1 else 0)
