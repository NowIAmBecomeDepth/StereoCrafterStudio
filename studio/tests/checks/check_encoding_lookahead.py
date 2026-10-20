"""Splatting's Encoding dialog (bug sweep 2026-10-09, A10): "Lookahead Frames"
read the unprefixed key, so it stayed greyed out with Enable Lookahead ticked.
Offscreen: open Splatting's dialog, tick it, the row is editable; untick, not.
Inpainting's (plain keys) still works the same way. DNxHR Profile follows
Enable DNxHR on both.
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
from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from studio import app as A

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
objs["Shell"].setProperty("uiScale", "1.0")
win = engine.rootObjects()[0]
win.resize(1400, 950); win.show()
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


def dialog_rows(dlg):
    items = walk(dlg.property("contentItem"), [])
    row = lambda n: [c for c in items if c.objectName() == n][0]
    return row("encLookaheadRow"), row("encDnxhrRow")


pump(1.0)
dialogs = [o for o in win.findChildren(QObject) if o.metaObject().className().startswith("EncodingDialog")]
by_stage = {}
for d in dialogs:
    ctl = d.property("ctl")
    for name in ("Splatting", "Inpainting", "Merging"):
        if ctl is objs.get(name):
            by_stage[name] = d
check("Splatting and Inpainting each have an Encoding dialog", "Splatting" in by_stage and "Inpainting" in by_stage,
      str(sorted(by_stage)))

for name, look_key, dnx_key in (("Splatting", "encoding_nvenc_lookahead_enabled", "dnxhr_fullres_split"),
                                ("Inpainting", "nvenc_lookahead_enabled", "dnxhr_fullres_split")):
    dlg = by_stage[name]
    st = objs[name].property("settings")
    dlg.setProperty("visible", True); pump(0.6)
    look, dnx = dialog_rows(dlg)
    for on in (True, False):
        st.insert(look_key, on); st.insert(dnx_key, on); pump(0.2)
        check(f"{name}: Lookahead Frames {'editable' if on else 'greyed'} with Enable Lookahead {'on' if on else 'off'}",
              look.property("enabled") is on, str(look.property("enabled")))
        check(f"...DNxHR Profile follows Enable DNxHR ({'on' if on else 'off'})",
              dnx.property("enabled") is on, str(dnx.property("enabled")))
    dlg.setProperty("visible", False); pump(0.3)

studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
