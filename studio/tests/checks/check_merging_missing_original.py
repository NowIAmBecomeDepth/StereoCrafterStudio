"""Merging and a clip whose original (left eye) is missing. 2026-10-07 the user wanted
the batch refused ("i'd rather it fail so i know i need to fix the path"); 2026-10-09
they met it in the middle: the page ASKS before starting -- the point is that a
right-eye-only merge never happens unnoticed. Real app, offscreen, real batch (CPU
encode) over the synthetic ``merge`` fixture -- mA is a single-eye inpaint (_splatted2:
its left eye comes from orig/mA.mp4), mB a quad grid (_splatted4: its own left eye) --
never the user's clips. Configs and last_jobs.json redirected.

  * mA's original moved away: no error, a question naming mA and the Original folder;
    nothing written until it's answered
  * Cancel: nothing written, Studio idle, the status says to fix the path
  * Continue: the batch runs -- mA as the right eye only, mB in full SBS -- no error
  * the original back: both clips merge in SBS, nothing asked"""
import os, sys, tempfile, hashlib, time, shutil
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
aside = tmp / "aside"; aside.mkdir()
shutil.move(str(root / "orig" / "mA.mp4"), str(aside / "mA.mp4"))       # mA's original: gone

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
out = tmp / "merged"
toasts, asked = [], []
mg.toast.connect(lambda k, t, m: toasts.append((k, t, m)))
mg.missingOriginals.connect(lambda t, m: asked.append((t, m)))

def setup(original_folder):
    for f in (out.glob("*") if out.exists() else []):
        f.unlink()
    for k, v in {"inpainted_folder": str(root / "inpainted"), "original_folder": original_folder,
                 "mask_folder": str(root / "mask"), "output_folder": str(out),
                 "encoding_encoder": "Force CPU", "codec": "H.264", "use_gpu": False, "resume": False,
                 "output_format": "Full SBS (Left-Right)", "pad_to_16_9": False, "split_lr": False,
                 "delete_sbs_after_split": False, "dnxhr_fullres_split": False}.items():
        st.setProperty(k, v)
    pump(0.3)

def made():
    return sorted(p.name for p in out.iterdir()) if out.exists() else []

def wait_merged(n):
    until(lambda: mg.property("busy"), 20)
    until(lambda: not mg.property("busy") and out.exists() and len(list(out.glob("m*"))) == n, 240)
    pump(1.0)

# ---- mA's original missing: a question, not an error
setup(str(root / "orig"))
t0, a0 = len(toasts), len(asked)
mg.start()
until(lambda: len(asked) > a0 and not mg.property("busy"), 60)
pump(0.5)
q = asked[a0:]
check("mA's original missing: the page is asked, not refused", len(q) == 1, str(q))
check("...naming the clip and the Original folder", bool(q) and "mA" in q[-1][1]
      and str(root / "orig") in q[-1][1].replace("/", os.sep), str(q[-1:]))
check("...no error toast", not [t for t in toasts[t0:] if t[0] == "error"], str(toasts[t0:]))
check("...nothing written yet", not made(), str(made()))
check("...and Studio is idle, waiting for the answer", not mg.is_busy())

# ---- Cancel
mg.cancelWithoutOriginals(); pump(0.5)
check("Cancel: nothing written", not made(), str(made()))
check("...and the status says to fix the path", "fix the Original folder" in str(mg.property("status")),
      str(mg.property("status")))

# ---- ask again, then Continue
a0, t0 = len(asked), len(toasts)
mg.start()
until(lambda: len(asked) > a0 and not mg.property("busy"), 60)
pump(0.5)
mg.continueWithoutOriginals()
wait_merged(2)
names = made()
check("Continue: both clips merge", len([m for m in names if m.startswith(("mA_", "mB_"))]) == 2, str(names))
check("...mA as the right eye only", any(m.startswith("mA_") and "right" in m.lower() for m in names), str(names))
check("...mB in full SBS (it has its own left eye)", any(m.startswith("mB_") and "sbs" in m.lower() for m in names),
      str(names))
check("...no error, and no second question", not [t for t in toasts[t0:] if t[0] == "error"]
      and len(asked) == a0 + 1, f"{toasts[t0:]} {asked[a0:]}")

# ---- the original back: no question
shutil.move(str(aside / "mA.mp4"), str(root / "orig" / "mA.mp4"))
setup(str(root / "orig"))
a0, t0 = len(asked), len(toasts)
mg.start()
wait_merged(2)
names = made()
check("the original back: both clips merge in SBS, nothing asked",
      len([m for m in names if m.startswith(("mA_", "mB_")) and "sbs" in m.lower()]) == 2
      and len(asked) == a0 and not [t for t in toasts[t0:] if t[0] == "error"], f"{names} {asked[a0:]}")

mg.shutdown()
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
