"""Detect Text open through a Splatting batch (audit 2026-09-28 #29), headless,
the real controller and preview worker over a TEMP copy of the synthetic
fixture. The editor held an OpenCV capture on the source clip for as long as
its window was open, so the batch's move to finished/ failed (WinError 32).
Now the job's start makes it let go: the batch moves both clips, and the
editor, still open, shows frames again -- read from finished/. Configs and
last_jobs.json redirected."""
import os, sys, tempfile, hashlib, time, threading
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["PYTHONIOENCODING"] = "utf-8"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, str(Path(__file__).parent))
rec = REPO / "last_jobs.json"; h0 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
tmp = Path(tempfile.mkdtemp())
import _fixtures
FIX = tmp / "fx"; _fixtures.copy("small", FIX)
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from PySide6.QtCore import QCoreApplication, QEventLoop
from PySide6.QtGui import QGuiApplication
app = QGuiApplication(sys.argv)
from studio.shell import StudioShell
from studio.stages.splatting.controller import SplattingController

fails = []
def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok: fails.append(label)
def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.005)
def until(cond, sec=120):
    end = time.time() + sec
    while not cond() and time.time() < end:
        pump(0.05)
    return cond()

ctl = SplattingController(StudioShell())
pv = ctl.preview
def worker_idle():
    with pv._cv:
        return not pv._jobs and not getattr(pv, "_inflight", False) and not pv._loading
states = []
pv.editorChanged.connect(lambda n, s: states.append((n, dict(s))))
toasts = []
ctl.toast.connect(lambda k, a, b: toasts.append((k, a, b)))

src = FIX / "src"
clips = sorted(p.name for p in src.glob("*.mp4"))
for k, v in {"input_source_clips": str(src).replace("\\", "/"),
             "input_depth_maps": str(FIX / "depth").replace("\\", "/"),
             "output_splatted": str(FIX / "out").replace("\\", "/"), "multi_map": False,
             "auto_save_sidecar": False, "move_to_finished": True, "enable_full_res": False,
             "enable_low_res": True, "dual_output": False, "auto_convergence_mode": "Off",
             "low_res_batch_size": "8"}.items():
    ctl._set(k, v)
pv.load()
check("the preview loads", until(lambda: pv.loaded and worker_idle()))
pv.openEditor("submask")
check("Detect Text opens", until(lambda: any(n == "submask" and s.get("open") for n, s in states) and worker_idle()),
      str(states[-1:]))
ed = pv._editors.get("submask")
check("...holding the clip", ed is not None and ed.cap is not None)

ctl.start()
check("the batch runs", until(lambda: ctl.property("busy"), 20))
check("...and ends", until(lambda: not ctl.property("busy"), 300))
pump(1.0)
moved = sorted(p.name for p in (src / "finished").glob("*.mp4")) if (src / "finished").is_dir() else []
check("both source clips reached finished/", moved == clips, f"{moved} of {clips}")
check("...nothing left behind", not list(src.glob("*.mp4")), str(list(src.glob("*.mp4"))))
check("the toast is a success", toasts and toasts[-1][0] == "success", str(toasts[-1:]))

n0 = len(states)
pv.editorOp("submask", "seek", [5])
until(lambda: len(states) > n0 and worker_idle(), 30)
check("Detect Text shows frames again (from finished/)", ed.frame_idx == 5 and ed.cap is not None
      and "finished" in ed.video_path.replace("\\", "/"), f"{ed.frame_idx} {ed.video_path}")

print()
_lj = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None) == h0
print("real last_jobs.json unchanged:", _lj)
if not _lj:
    fails.append("real last_jobs.json changed")
print("FAILED:" if fails else "ALL OK", fails if fails else "")
try:
    pv.closeEditor("submask"); pump(0.3)
    ctl.shutdown()
except Exception:
    pass
_fixtures.exit_now(1 if fails else 0)
