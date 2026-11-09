"""Encoding Settings' Color Tags (user, 2026-11-09: "make auto a check box. if unchecked the user can select a
specific one"), real app, offscreen, configs and last_jobs.json redirected; no clips read.

  * each stage's dialog has an Auto checkbox and a tag picker; Auto ticked for the default "Auto"
  * the picker is greyed while Auto is ticked and lists no "Auto"
  * unticking Auto stores a tag set (BT.709 L first time); picking one stores it; ticking Auto stores "Auto";
    unticking again brings back the last one picked
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
from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, qInstallMessageHandler, QMetaObject, Q_ARG
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from studio import app as A

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
win.resize(1400, 950); win.show()
fails = []
def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok: fails.append(label)
def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.005)
def tick(b):         # a user's click: toggle() and the toggled() a click sends
    QMetaObject.invokeMethod(b, "toggle"); QMetaObject.invokeMethod(b, "toggled")
def walk(it, out):
    for c in it.childItems():
        out.append(c); walk(c, out)
    return out

pump(1.0)
dialogs = {}
for d in (o for o in win.findChildren(QObject) if o.metaObject().className().startswith("EncodingDialog")):
    for name in ("Splatting", "Inpainting", "Merging"):
        if d.property("ctl") is objs.get(name):
            dialogs[name] = d
check("Splatting, Inpainting and Merging each have an Encoding dialog", sorted(dialogs) == ["Inpainting", "Merging", "Splatting"],
      str(sorted(dialogs)))
KEY = {"Splatting": "color_tags_mode"}
for name, dlg in sorted(dialogs.items()):
    st = objs[name].property("settings"); key = KEY.get(name, "color_tags")
    dlg.setProperty("visible", True); pump(0.6)
    items = walk(dlg.property("contentItem"), [])
    one = lambda n: next((c for c in items if c.objectName() == n), None)
    auto, pick = one("encColorTagsAuto"), one("encColorTagsPick")
    check(f"{name}: Auto checkbox and tag picker there", auto is not None and pick is not None)
    if auto is None or pick is None:
        continue
    st.insert(key, "Auto"); pump(0.2)
    model = list(pick.property("model") or [])
    check(f"{name}: Auto ticked for Auto, picker greyed", auto.property("checked") is True and pick.property("enabled") is False)
    check(f"...picker lists the tag sets without Auto", "Auto" not in model and "BT.709 L" in model and "BT.2020 PQ" in model, str(model))
    check(f"...greyed picker shows BT.709 L, not Auto", pick.property("displayText") == "BT.709 L", str(pick.property("displayText")))
    tick(auto); pump(0.2)
    check(f"...unticked: stores BT.709 L, picker editable", st.value(key) == "BT.709 L" and pick.property("enabled") is True, str(st.value(key)))
    i = model.index("BT.2020 HLG")
    QMetaObject.invokeMethod(pick, "activated", Q_ARG(int, i)); pump(0.2)
    check(f"...picking BT.2020 HLG stores it", st.value(key) == "BT.2020 HLG", str(st.value(key)))
    tick(auto); pump(0.2)
    check(f"...ticked again: stores Auto, picker greyed on BT.2020 HLG",
          st.value(key) == "Auto" and pick.property("enabled") is False and pick.property("displayText") == "BT.2020 HLG",
          f"{st.value(key)} {pick.property('displayText')}")
    tick(auto); pump(0.2)
    check(f"...unticked again: BT.2020 HLG back", st.value(key) == "BT.2020 HLG", str(st.value(key)))
    st.insert(key, "BT.709 F"); pump(0.2)
    check(f"...set from elsewhere (BT.709 F): Auto unticks itself", auto.property("checked") is False, str(auto.property("checked")))
    st.insert(key, "Auto"); pump(0.2)
    check(f"...and Auto ticks itself again", auto.property("checked") is True)
    dlg.setProperty("visible", False); pump(0.3)

studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
