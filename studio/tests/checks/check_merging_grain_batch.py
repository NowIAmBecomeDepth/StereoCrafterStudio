"""An open Grain Match window lets go of its clip's files for a batch (bug sweep
2026-10-09, A8: its three readers stayed open, so a Resume batch reaching that clip
couldn't move them into finished/ -- Windows keeps open files where they are). Real
app, offscreen, real batch (CPU encode) over the synthetic ``merge`` fixture -- never
the user's clips. Configs and last_jobs.json redirected.

  * Grain Match opens on mA and stays open
  * a Resume batch moves mA's inpainted file, splat and original into finished/
  * the window then closes with a note (its clip is gone), not an error"""
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
from PySide6.QtCore import QCoreApplication, QEventLoop
from PySide6.QtGui import QGuiApplication
from studio import app as A

root = _fixtures.copy("merge", tmp / "fx")
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

pump(0.6)
mg = objs["Merging"]
st = mg.property("settings")
pv = mg.property("previewer")
out = tmp / "merged"
for k, v in {"inpainted_folder": str(root / "inpainted"), "original_folder": str(root / "orig"),
             "mask_folder": str(root / "mask"), "output_folder": str(out),
             "encoding_encoder": "Force CPU", "codec": "H.264", "use_gpu": False, "resume": True,
             "output_format": "Full SBS (Left-Right)", "pad_to_16_9": False, "split_lr": False,
             "delete_sbs_after_split": False, "dnxhr_fullres_split": False, "grain_enable": True,
             "preview_source": "Blended Image"}.items():
    st.setProperty(k, v)
pump(0.3)
states, notes = [], []
pv.grainChanged.connect(lambda s: states.append(dict(s)))
pv.grainMessage.connect(lambda kind, text: notes.append((kind, text)))
toasts = []
pv.toast.connect(lambda k, t, m: toasts.append((k, t, m)))
pv.load()
check("the preview loads mA", until(lambda: pv.property("loaded") and not pv.property("loading"), 60))
pv.openGrain()
check("Grain Match opens on mA", until(lambda: states and states[-1].get("open"), 60))

mg.start()
until(lambda: mg.property("busy"), 20)
until(lambda: not mg.property("busy"), 300)
# moved: in finished/ AND gone from where it was (a held file is copied but not removed)
done = lambda: all((root / sub / "finished" / name).exists() and not (root / sub / name).exists() for sub, name in (
    ("inpainted", "mA_320_inpainted_right_eye.mp4"), ("mask", "mA_320_splatted2.mp4"), ("orig", "mA.mp4")))
check("mA's inpainted file, splat and original moved into finished/ (and not left behind)", until(done, 60),
      str({s: sorted(p.name for p in (root / s).rglob("mA*")) for s in ("inpainted", "mask", "orig")}))
check("Grain Match closed with a note, its clip gone",
      until(lambda: states and states[-1].get("open") is False, 30) and any("finished" in t for _, t in notes),
      f"{states[-1:]} {notes}")
check("no preview error", not [t for t in toasts if t[0] == "error"], str(toasts))

mg.shutdown()
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
