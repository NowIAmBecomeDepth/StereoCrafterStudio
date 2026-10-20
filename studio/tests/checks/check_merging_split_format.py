"""Merging's Split L/R Output (user, 2026-09-27): one Output Format choice for
Full SBS -> split into _L / _R -> SBS to the Recycle Bin. The engine (v1's)
is handed Full SBS with Split L/R and Delete SBS Videos on; the old boxes are
not shown for it. Real app, offscreen; configs and last_jobs.json redirected."""
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
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
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

pump(0.6)
mg = objs["Merging"]
st = mg.property("settings")
check("Output Format lists Split L/R Output", "Split L/R Output" in list(mg.property("choices")["output_format"]))
stage = [c for c in walk(win.contentItem(), []) if c.objectName() == "stage"][0]
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 3), Q_ARG("QVariant", True)); pump(1.0)
page = stage.property("pages").property(3).toQObject()
def boxes():
    return {c.property("key"): c.isVisible() for c in walk(page, [])
            if c.property("key") in ("split_lr", "delete_sbs_after_split")}

mg._set("output_format", "Full SBS (Left-Right)"); mg._set("split_lr", True); pump(0.3)
check("Full SBS: the Split L/R and Delete SBS boxes are there, as before",
      boxes() == {"split_lr": True, "delete_sbs_after_split": True}, str(boxes()))
mg._set("output_format", "Split L/R Output"); mg._set("split_lr", False); mg._set("delete_sbs_after_split", False)
pump(0.3)
check("Split L/R Output: those boxes are not needed, so not shown", not any(boxes().values()), str(boxes()))

# what the engine is handed at the start of a job: v1's three settings, on
seen = {}
class _Eng:
    def __init__(self, values, hooks):
        seen["values"] = values
mg._module = type("M", (), {"MergingEngine": _Eng})
mg._engine = None
mg._ensure_engine()
v = seen.get("values", {})
got = (v.get("output_format"), v.get("split_lr"), v.get("delete_sbs_after_split"))
check("...the engine gets Full SBS (Left-Right) with Split L/R and Delete SBS Videos on",
      got == ("Full SBS (Left-Right)", True, True), str(got))
check("...while the setting itself stays Split L/R Output", st.value("output_format") == "Split L/R Output",
      str(st.value("output_format")))
mg._engine = None
mg._module = None
tip = mg.property("help")["output_format"]
check("the Output Format tooltip explains it", "Split L/R Output:" in tip and "Recycle Bin" in tip, tip[-120:])
mg._set("output_format", "Full SBS (Left-Right)")

studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, " | ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
