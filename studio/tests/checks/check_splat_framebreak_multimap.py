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

# ---- Letterbox presets
check("presets offered for 16:9", pv.letterboxPresets == ["1.85:1", "2.2:1", "2.39:1"], str(pv.letterboxPresets))
setv("fb_preset", "2.39:1"); pv.choosePreset(); settle()
bars = float(ctl._map.value("frame_break_bars"))
check("2.39 sets Bar Size", abs(bars - round(1 - (640/360)/2.39, 4)) < 1e-6, str(bars))
check("preset stays lit", ctl._map.value("fb_preset") == "2.39:1", str(ctl._map.value("fb_preset")))
setv("frame_break_bars", 0.3); settle()
check("dragging bars unlights (Custom)", ctl._map.value("fb_preset") == "Custom", str(ctl._map.value("fb_preset")))
setv("frame_break_bars", 0.0); settle()
# ---- Multi-Map
import shutil as _sh
mm = tmp / "mm"; (mm / "mapA").mkdir(parents=True); (mm / "mapB").mkdir()
for c in ("clipA", "clipB"):
    _sh.copy(FIX / "depth" / f"{c}_depth.mp4", mm / "mapA" / f"{c}_depth.mp4")
    _sh.copy(FIX / "depth" / f"{c}_depth.mp4", mm / "mapB" / f"{c}_depth.mp4")
setv("input_depth_maps", str(mm).replace("\\", "/")); settle()
setv("multi_map", True); settle()
check("map subfolders found", pv.depthMaps == ["mapA", "mapB"], str(pv.depthMaps))
pv.load(); settle()
check("multi-map loads", pv.loaded, "")
setv("selected_depth_map", "mapB"); pv.chooseMap(); settle()
dp = pv.currentDepth.replace("\\", "/")
check("chosen map is the one previewed", "/mapB/" in dp, dp)
pv.shutdown()
print("toasts:", [(k, a) for k, a, _ in toasts])
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
