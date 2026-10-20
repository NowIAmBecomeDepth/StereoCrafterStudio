"""Headless: phase 3b features over a temp copy of the small fixture."""
import os, sys, json, tempfile, hashlib, time, shutil, threading
from pathlib import Path
import _fixtures
os.environ["QT_QPA_PLATFORM"] = "offscreen"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
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
             "enable_full_res": True, "enable_low_res": False, "dual_output": False, "loop_playback": False,
             "auto_convergence_mode": "Off"}.items():
    ctl._set(k, v)
pv.load(); settle()
check("loaded", pv.loaded and pv.fps > 0, f"fps {pv.fps}")
check("no sidecar yet -> Create", not pv.sidecarExists)

# ---- Keyframes
pv.seek(5); settle(); pv.addKeyframe("static"); settle()
pv.seek(20); settle(); pv.addKeyframe("dynamic"); settle()
check("two keyframes", [(k["frame"], k["kind"]) for k in pv.keyframes] == [(5, "static"), (20, "dynamic")], str(pv.keyframes))
pv.clearKeyframe(); settle()
check("clear current", [(k["frame"], k["kind"]) for k in pv.keyframes] == [(5, "static")], str(pv.keyframes))
pv.clearAllKeyframes(); settle()
check("clear all", pv.keyframes == [], str(pv.keyframes))
# ---- Sidecar moves
(FIX / "depth" / "clipB_depth.fssidecar").write_text('{"convergence_plane": 0.4}')
pv.moveSidecars(True); settle()
check("depth -> source (remove _depth)", (FIX / "src" / "clipB.fssidecar").exists() and not (FIX / "depth" / "clipB_depth.fssidecar").exists())
pv.moveSidecars(False); settle()
check("source -> depth (add _depth)", (FIX / "depth" / "clipB_depth.fssidecar").exists() and not (FIX / "src" / "clipB.fssidecar").exists())
# ---- Help, Fusion
check("about names Studio, not the Prototype (release 2026-10-17)", ctl.aboutText.startswith("Splatting -- StereoCrafter Studio") and "GUI_VERSION" not in ctl.aboutText, ctl.aboutText[:60])
# the old user guide is gone (user, 2026-10-04): Settings links to the manual instead
check("no old user guide", not hasattr(ctl, "userGuide"))
bad = tmp / "bad.fsexport"; bad.write_text("not an export")
n0 = len(toasts); ctl.runFusion("generate_sidecars", str(bad), str(FIX / "depth")); pump(5)
print("   fusion toasts:", toasts[n0:], "status:", ctl._status, flush=True)
# a literal True before (bug sweep 2026-10-09, A12): the worker thread's crash or a silent
# failure passed. A bad file now says so, and nothing claims a success.
_ft = toasts[n0:]
check("fusion: a bad file says so (an error), nothing done",
      any(k == "error" and "Fusion" in a for k, a, _ in _ft) and not any(k == "success" for k, _a, _b in _ft)
      and "failed" in str(ctl._status), f"{_ft} / {ctl._status}")
check("encoding choices present", "encoding_encoder" in ctl.choices and "color_tags" in ctl.choices)
pv.shutdown()
print("toasts:", [(k, a) for k, a, _ in toasts])
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
