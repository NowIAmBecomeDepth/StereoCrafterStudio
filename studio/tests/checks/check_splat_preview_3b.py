"""Headless: phase 3b features over a temp copy of the small fixture."""
import os, sys, json, tempfile, hashlib, time, shutil, threading
from pathlib import Path
import _fixtures
os.environ["QT_QPA_PLATFORM"] = "offscreen"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
# as studio.app does (and check_splat_single): the renderer's progress bar prints block
# characters a piped cp1252 stdout cannot encode -- the SINGLE run failed on them
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
             "enable_full_res": True, "enable_low_res": False, "dual_output": False, "loop_playback": False,
             "auto_convergence_mode": "Off"}.items():
    ctl._set(k, v)
pv.load(); settle()
check("loaded", pv.loaded and pv.fps > 0, f"fps {pv.fps}")
check("no sidecar yet -> Create", not pv.sidecarExists)

# ---- Update / Create Sidecar
setv("max_disp", 37.0); settle()
pv.updateSidecar(); settle()
sc = FIX / "depth" / "clipA_depth.fssidecar"
data = json.loads(sc.read_text()) if sc.exists() else {}
check("Create Sidecar writes it", sc.exists() and abs(float(data.get("max_disparity", 0)) - 37.0) < 1e-6, str(data.get("max_disparity")))
check("button now says Update", pv.sidecarExists)

# ---- traces: Border mode Off zeroes the border (v1 _on_border_mode_change)
setv("border_width", "1.50"); settle()
setv("border_mode", "Off"); settle()
check("trace: Border Off zeroes width", float(ctl._map.value("border_width")) == 0.0, str(ctl._map.value("border_width")))

# ---- rescan (v1: Off -> Manual with the scan)
pv.rescanBorders(); settle()
check("rescan: Off -> Manual", ctl._map.value("border_mode") == "Manual", str(ctl._map.value("border_mode")))

# ---- the ≈ estimate
n0 = len(toasts); pv.estimateDepthPop(); settle()
check("estimate ran without error", not [t for t in toasts[n0:] if t[0] == "error"], str(toasts[n0:])[:200])

# ---- playback: every frame, paced by fps, and the loop cache
pv.seek(0); settle()
frames_seen = []
pv.changed.connect(lambda: frames_seen.append(pv.frame))
t0 = time.time(); pv.togglePlay()
end = time.time() + 20
while pv.playing and time.time() < end:
    pump(0.02)
el = time.time() - t0
seen = sorted(set(frames_seen))
check("play reaches the end and stops", not pv.playing and pv.frame == 47, f"frame {pv.frame}")
check("every frame shown", seen[:48] == list(range(48)) or set(range(1, 48)) <= set(seen), f"{len(seen)} distinct")
check("never faster than the clip", el >= 47 / pv.fps * 0.95, f"{el:.2f}s for 47 frames at {pv.fps} fps")
setv("loop_playback", True); settle()
pv.seek(0); settle()
pv.togglePlay(); pump(4.5); pv.togglePlay(); settle()
check("loop wraps", len(pv._play_cache) > 0, f"cache {len(pv._play_cache)} frames")
check("pause renders the exact frame", not pv.playing)
setv("loop_playback", False); settle()
pv.stepFrames(3); settle()
check("arrow-key step", pv.frame >= 3, str(pv.frame))

# ---- SINGLE (a real run over clipA)
def run_single():
    n = len(toasts); ctl.startSingle(); end = time.time() + 300
    while time.time() < end and not any("Splatting" in t[1] for t in toasts[n:]):
        pump(0.05)
    return toasts[n:]
got = run_single(); print("   single toasts:", got, flush=True)
outs = list((FIX / "out").rglob("*.mp4"))
check("SINGLE rendered clipA only", outs and all("clipA" in o.name for o in outs), str([o.name for o in outs])[:200])
settle()
check("preview came back after SINGLE", pv.loaded, "")

# ---- Splat Test: a one-frame diagnostic capture
ctl.splatTest = True
pv.seek(10); settle()
got = run_single(); print("   test toasts:", got, flush=True)
settle()
pngs = list((FIX / "out").glob("*splattest*.png"))
check("Splat Test saved its images", len(pngs) >= 2, str([p.name for p in pngs])[:200])
ctl.splatTest = False

# ---- Preview Auto-Converge (Average)
setv("auto_convergence_mode", "Average"); settle()
before = ctl._map.value("zero_disparity_anchor")
n0 = len(toasts); pv.autoConverge(); settle(300)
check("auto-converge ran", not [t for t in toasts[n0:] if t[0] == "error"], f"anchor {before} -> {ctl._map.value('zero_disparity_anchor')} status {pv.status!r}")

pv.shutdown()
print("toasts:", [(k, a) for k, a, _ in toasts])
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
