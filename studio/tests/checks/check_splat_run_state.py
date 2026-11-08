"""Splatting's run, one state at a time (user, 2026-11-07): idle -> preparing (Single:
the preview reads its clip) -> releasing (the preview lets go of the GPU) -> running ->
idle; a batch starts at releasing, a Map / Splat Test goes straight from preparing to
running. Before, it was _busy plus the Stop event plus the thread, and:
  * Stop while Single prepared was cleared by the start, and the clip rendered
  * Stop while the preview let go kept the page busy until the release landed
  * a stopped Test Render left the next batch marked a test (not handed on)
  * Stop After Clip was offered during the release, from the last run's clip count
Controller and preview only, offscreen, over the ``small`` fixture -- never the
user's clips. Configs and last_jobs.json redirected."""
import os, sys, tempfile, hashlib, time, threading
from pathlib import Path
import _fixtures
os.environ["QT_QPA_PLATFORM"] = "offscreen"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
for _s in (sys.stdout, sys.stderr): _s.reconfigure(encoding="utf-8", errors="backslashreplace")
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
tmp = Path(tempfile.mkdtemp())
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
def until(cond, sec=30):
    end = time.time() + sec
    while not cond() and time.time() < end:
        pump(0.05)
    return cond()

ctl = SplattingController(StudioShell())
pv = ctl.preview
toasts = []
ctl.toast.connect(lambda k, a, b: toasts.append((k, a, b)))
out = FIX / "out"
for k, v in {"input_source_clips": str(FIX / "src").replace("\\", "/"),
             "input_depth_maps": str(FIX / "depth").replace("\\", "/"),
             "output_splatted": str(out).replace("\\", "/"), "multi_map": False,
             "preview_source": "Splat Result", "preview_size": "100%", "border_mode": "Manual",
             "auto_save_sidecar": False, "move_to_finished": False, "high_vram_mode": False,
             "enable_full_res": True, "enable_low_res": False, "dual_output": False, "loop_playback": False,
             "auto_convergence_mode": "Off"}.items():
    ctl._set(k, v)
pv.load()
until(lambda: pv.loaded, 120); pump(1.0)
check("the preview loaded the fixture", pv.loaded)

def rendered():
    return sorted(p.name for p in out.rglob("*.mp4")) if out.exists() else []
def idle():
    return not ctl.property("busy") and ctl._thread is None
def wait_run():
    until(lambda: ctl._thread is not None, 60)
    until(idle, 300)
    pump(1.0)

real_release, real_prepare = pv.release_for_job, pv.prepare_single
held = []
def hold_release():
    pv.release_for_job = lambda then: real_release(lambda: held.append(then))
def let_release():
    pv.release_for_job = real_release
def fire():
    if held:                          # (the old code never asked a second time)
        held.pop(0)()

# ---- Single: Stop while the preview prepares
asked = []
pv.prepare_single = lambda test: asked.append(test)
ctl.startSingle()
check("Single: busy while the preview reads its clip", ctl.property("busy") is True and len(asked) == 1)
ctl.stop(); pump(0.2)
check("...Stop: idle at once", idle())
pv.prepare_single = real_prepare
real_prepare(asked[0])                # the preview's answer lands after the Stop
pump(3.0)
check("...the clip doesn't render when the answer lands", ctl._thread is None and not rendered(), str(rendered()))
check("...and stays idle", idle())

# ---- a batch: Stop while the preview lets go, then a second Start before the first release lands
hold_release()
ctl.start()
until(lambda: len(held) == 1, 30)
check("batch: busy while the preview lets go", ctl.property("busy") is True and ctl._thread is None)
ctl.stop(); pump(0.2)
check("...Stop: idle at once, before the release has landed", idle())
ctl.start()
until(lambda: len(held) == 2, 30)
fire()                         # the stopped Start's release lands now
pump(0.5)
check("...the stopped Start's release starts nothing", ctl._thread is None and ctl.property("busy") is True)
fire()                         # the second Start's release
let_release()
wait_run()
check("...the second Start renders both clips", len([n for n in rendered() if "clip" in n]) == 2, str(rendered()))

# ---- the last run had two clips: Stop After Clip isn't offered while the next one releases
if ctl._clip_total < 2:               # (the old code skipped the run above)
    ctl.start(); wait_run()
check("a two-clip batch ran", ctl._clip_total == 2, str(ctl._clip_total))
hold_release()
ctl.start()
until(lambda: len(held) == 1, 30)
check("Stop After Clip not offered before the batch has started", not ctl.property("multiClip"))
ctl.stop(); fire(); let_release(); pump(0.5)

# ---- a Test Render stopped while the preview let go: the next batch is a normal one
for f in out.rglob("*.mp4"):
    f.unlink()
hold_release()
ctl.testClip(str(FIX / "src" / "clipA.mp4"))
until(lambda: len(held) == 1, 60)
check("Test Render: waiting on the release", ctl.property("busy") is True and len(held) == 1)
ctl.stop(); fire(); let_release(); pump(0.5)
check("...stopped: nothing rendered", idle() and not rendered(), str(rendered()))
n = len(toasts)
ctl.start()
wait_run()
done = [t for t in toasts[n:] if "Splatting" in t[1]]
check("the next batch isn't taken for a test render", done and not done[-1][1].startswith("Test render"), str(done))
check("...and it rendered into the output folder, not Test Renders",
      rendered() and not any("Test Renders" in str(p) for p in out.rglob("*.mp4")), str(rendered()))

ctl.shutdown()
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
