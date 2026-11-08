"""Merging's run, one state at a time (user, 2026-11-07): idle -> releasing (the
preview lets go of the GPU) -> starting (the engine loads and starts) -> running ->
idle, and idle -> merging all -> idle. Before, it was four flags, and the release
step counted as idle. Real app, offscreen, the synthetic ``merge`` fixture -- never
the user's clips. Configs and last_jobs.json redirected.

  * two things asked of the engine while it loads both run (one slot lost the first)
  * while the preview lets go, Merging is busy: the page, is_busy() and the GPU hand-over
  * Stop then cancels the start: nothing merged, the list let go, the stage idle
  * the next Start runs normally
  * a start the engine refuses (no clips) comes back to idle"""
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
out = tmp / "merged"
empty = tmp / "no_clips"; empty.mkdir()

def setup(inpainted):
    for f in (out.glob("*") if out.exists() else []):
        f.unlink()
    for k, v in {"inpainted_folder": str(inpainted), "original_folder": str(root / "orig"),
                 "mask_folder": str(root / "mask"), "output_folder": str(out),
                 "encoding_encoder": "Force CPU", "codec": "H.264", "use_gpu": False, "resume": False,
                 "output_format": "Full SBS (Left-Right)", "pad_to_16_9": False, "split_lr": False,
                 "delete_sbs_after_split": False, "dnxhr_fullres_split": False}.items():
        st.setProperty(k, v)
    pump(0.3)

def made():
    return sorted(p.name for p in out.glob("m*")) if out.exists() else []

def settled():
    return not mg.property("busy") and not mg.property("loading") and not mg.is_busy()

setup(root / "inpainted")

# ---- two asks while the engine loads: both run
check("the engine isn't loaded yet", mg._module is None)
got = []
mg._with_engine(lambda eng: got.append("first"))
mg._with_engine(lambda eng: got.append("second"))
until(lambda: len(got) == 2, 60)
check("both things asked of the loading engine ran, in order", got == ["first", "second"], str(got))
until(settled, 10)

# ---- a slow release: Merging is busy through it, and Stop cancels the start
real_release = mg.preview.release_for_job
held = []
mg.preview.release_for_job = lambda then: real_release(lambda: held.append(then))
mg.start()
until(lambda: bool(held), 20)
check("the preview let go (the start is waiting on it)", bool(held))
check("...while it does, the page shows Merging busy", mg.property("busy") is True)
check("...is_busy() says so (the GPU hand-over asks it)", mg.is_busy())
run = mg.jobs.running() if getattr(mg, "jobs", None) is not None else None
check("...and the hand-over sees Merging running", run is not None and run[2] is mg, str(run and run[1]))
mg.stop()
pump(0.2)
check("Stop: idle at once, before the preview has finished letting go", settled())
held.pop()()                    # the release lands after the Stop
pump(2.0)
mg.preview.release_for_job = real_release
check("...the start doesn't go ahead when the release lands", not (mg._engine is not None and mg._engine.is_processing))
check("...nothing merged", not made(), str(made()))
check("...the clip list let go", not mg.preview.queue._hold)
check("...and the status says it stopped", "stop" in str(mg.property("status")).lower(), str(mg.property("status")))

# ---- the next Start runs normally
mg.start()
until(lambda: mg.property("busy"), 20)
until(lambda: settled() and len(made()) == 2, 240)
pump(1.0)
check("the next Start merges both clips", len(made()) == 2, str(made()))
check("...and ends idle, the list let go", settled() and not mg.preview.queue._hold)

# ---- a start the engine refuses comes back to idle
setup(empty)
mg.start()
until(settled, 30)
pump(0.5)
check("a refused start (no clips) ends idle, the list let go", settled() and not mg.preview.queue._hold)

mg.shutdown()
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
