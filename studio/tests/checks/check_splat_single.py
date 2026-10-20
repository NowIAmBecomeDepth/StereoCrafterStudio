"""Headless: phase 3b features over a temp copy of the small fixture."""
import os, sys, json, tempfile, hashlib, time, shutil, threading
from pathlib import Path
import _fixtures
os.environ["QT_QPA_PLATFORM"] = "offscreen"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
# as studio.app does: the renderer's console progress bar prints block
# characters, which a piped cp1252 stdout cannot encode (and the task fails)
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
ctl = SplattingController(StudioShell())
pv = ctl.preview
pv._idle = threading.Event(); pv._idle.set()
_o = pv._do
def _do(*a, **k):
    pv._idle.clear()
    try: return _o(*a, **k)
    finally: pv._idle.set()
pv._do = _do
def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.005)
def _timers_pending():
    # A setting change queues its render only after the preview's settle
    # timer (120 ms); until it has fired, the preview is not settled.
    return any(getattr(pv, name).isActive() for name in ("_render_timer", "_overlay_timer")
               if hasattr(pv, name))
def settle(t=120):
    end = time.time() + t
    while time.time() < end:
        pump(0.02)
        with pv._cv:
            idle = not pv._jobs
        if idle and not pv._loading and pv._idle.is_set() and not _timers_pending():
            pump(0.2); return
toasts = []
ctl.toast.connect(lambda k, a, b: toasts.append((k, a, b)))
def setv(k, v):
    ctl._map.insert(k, v); ctl._on_value_changed(k, v)
for k, v in {"input_source_clips": str(FIX / "src").replace("\\", "/"),
             "input_depth_maps": str(FIX / "depth").replace("\\", "/"),
             "output_splatted": str(FIX / "out").replace("\\", "/"), "multi_map": False,
             "preview_source": "Splat Result", "preview_size": "100%", "border_mode": "Manual",
             "update_slider_from_sidecar": True, "auto_save_sidecar": False, "move_to_finished": False,
             "enable_full_res": True, "enable_low_res": True, "dual_output": False, "loop_playback": False,
             "auto_convergence_mode": "Off"}.items():
    ctl._set(k, v)
pv.load(); settle()
check("loaded", pv.loaded and pv.fps > 0, f"fps {pv.fps}")
check("no sidecar yet -> Create", not pv.sidecarExists)

print("STATUS BEFORE", ctl._status, flush=True)
# ---- SINGLE (a real run over clipA)
def run_single():
    n = len(toasts); ctl.startSingle(); end = time.time() + 300
    while time.time() < end and not any("Splatting" in t[1] for t in toasts[n:]):
        pump(0.05)
    return toasts[n:]
passes = []
ctl.infoChanged.connect(lambda: passes.append((tuple(ctl.property("passFrames")), tuple(ctl.property("passDone")))))
got = run_single(); print("   single toasts:", got, flush=True)
# the list's H and L bars (user, 2026-09-28): the renderer's frames per pass, a
# pass done once the next starts, both done as the job ends
seq = [p for i, p in enumerate(passes) if i == 0 or p != passes[i - 1]]
check("the Full-Res pass counts frames, then Low-Res, then both are done",
      any(f[0] > 0 and f[1] == 0 and d == (False, False) for f, d in seq)
      and any(f[1] > 0 and d == (True, False) for f, d in seq) and seq and seq[-1][1] == (True, True), str(seq)[:400])
outs = list((FIX / "out").rglob("*.mp4"))
check("SINGLE rendered clipA only", outs and all("clipA" in o.name for o in outs), str([o.name for o in outs])[:200])
settle()
check("preview came back after SINGLE", pv.loaded, "")

print("STATUS", ctl._status, "busy", ctl._busy, flush=True)
print("out:", list((FIX / "out").rglob("*")), flush=True)
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
