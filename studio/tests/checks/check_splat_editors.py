"""Headless: Splatting phase 4b -- Hole Shift through the
real preview worker, over a TEMP copy of the synthetic fixture."""
import os, sys, json, tempfile, hashlib, time, shutil, threading
from pathlib import Path
import _fixtures
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["PYTHONIOENCODING"] = "utf-8"
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
import numpy as np
from core.common import dilate_regions
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
def settle(t=120):
    end = time.time() + t
    while time.time() < end:
        pump(0.02)
        with pv._cv:
            idle = not pv._jobs
        if idle and not pv._loading and pv._idle.is_set() and not pv._render_timer.isActive():
            pump(0.2); return
states = {"holeshift": []}; refused = []; status = []; toasts = []
pv.editorChanged.connect(lambda n, s: states[n].append(dict(s)))
pv.editorRefused.connect(lambda n, m: refused.append((n, m)))
pv.statusMessage.connect(status.append)
ctl.toast.connect(lambda k, a, b: toasts.append((k, a, b)))
def last(n): return states[n][-1] if states[n] else {}

# ---- refused before any load (no engine)
pv.openEditor("holeshift"); pump(0.2)
check("refused before load", refused and refused[-1][0] == "holeshift" and "No splat" in refused[-1][1], str(refused))

for k, v in {"input_source_clips": str(FIX / "src").replace("\\", "/"),
             "input_depth_maps": str(FIX / "depth").replace("\\", "/"),
             "output_splatted": str(FIX / "out").replace("\\", "/"), "multi_map": False,
             "preview_source": "Splat Result", "preview_size": "100%", "border_mode": "Manual",
             "max_disp": 60, "update_slider_from_sidecar": True, "auto_save_sidecar": False,
             "move_to_finished": False, "enable_full_res": True, "enable_low_res": False,
             "dual_output": False, "loop_playback": False, "auto_convergence_mode": "Off",
             "dilreg_enabled": True}.items():
    ctl._set(k, v)
pv.load(); settle()
check("loaded", pv.loaded)
eng = pv._engine
check("render cached occ/splat/disp", eng._dilreg_last_occ is not None and eng._dilreg_last_splat is not None
      and eng._warp_last_disp is not None)

# ---- Hole Shift
pv.openEditor("holeshift"); settle()
s = last("holeshift")
if not s:
    print("   refused:", refused[-1:])
check("HS opened with a picture", s.get("open") and s.get("imageWidth", 0) > 0 and s.get("serial", 0) > 0,
      str({k: s.get(k) for k in ("open", "imageWidth", "imageHeight", "serial", "info")}))
check("HS provider has the image", pv.provider._images.get("holeshift") is not None)
holes = dilate_regions.find_holes(eng._dilreg_last_occ)
print("   holes on frame:", len(holes))
big = max(holes, key=lambda h: int(h[1].sum()))
ys, xs = np.nonzero(big[1]); i = len(ys) // 2
H, W = eng._dilreg_last_occ.shape[:2]
nx, ny = (xs[i] + 0.5) / W, (ys[i] + 0.5) / H
n0 = len(states["holeshift"])
pv.editorOp("holeshift", "click", [nx, ny]); settle()
s = last("holeshift")
check("click selects the hole", s.get("info", "").startswith("1 selected"), s.get("info"))
check("click re-drew", len(states["holeshift"]) > n0)
kf = eng.dilate_regions.ensure_keyframe(pv._editors["holeshift"].frame)
check("a region with no shift", len(kf.regions) == 1 and kf.regions[0].offset_x == 0.0)
pv.editorOp("holeshift", "nudge", ["offset_x", 1]); pv.editorOp("holeshift", "nudge", ["offset_x", 1])
pv.editorOp("holeshift", "nudge", ["offset_y", 1]); settle()
s = last("holeshift")
check("D-pad readouts", s.get("x") == "+0.2" and s.get("y") == "+0.1", f"x={s.get('x')} y={s.get('y')}")
check("offset_y stored pixel-down", abs(kf.regions[0].offset_y + 0.1) < 1e-9, str(kf.regions[0].offset_y))
pv.editorOp("holeshift", "nudge", ["feather", -1]); settle()
check("feather", last("holeshift").get("feather") == f"{dilate_regions.DEFAULT_FEATHER - 0.1:.1f}", last("holeshift").get("feather"))
pv.editorOp("holeshift", "commit", []); settle()
pv.editorOp("holeshift", "set_enabled", [False]); settle()
check("enable off reaches the engine + settings", not eng.dilreg_enabled_var.get() and ctl._map.value("dilreg_enabled") is False,
      str(ctl._map.value("dilreg_enabled")))
ctl._set("dilreg_enabled", True); pv.editorOp("holeshift", "set_enabled", [True]); settle()
pv.editorOp("holeshift", "nudge", ["offset_x", 1]); settle()
pv.editorOp("holeshift", "bogus", []); settle()
check("unknown op refused, worker alive", any(t[1] == "Preview failed" for t in toasts) and pv._thread.is_alive())
# sidecar round trip
pv.updateSidecar(); settle()
sc = sorted((FIX / "depth").glob("*.fssidecar"))
data = json.loads(sc[0].read_text()) if sc else {}
check("sidecar carries the regions", dilate_regions.SIDECAR_KEY in data, str(list(data)[:8]))
pv.closeEditor("holeshift"); settle()
check("HS closed", last("holeshift").get("open") is False and eng._dilreg_refresh is None)
n_hs = len(states["holeshift"])
pv.refresh(); settle()
check("closed editor not redrawn by renders", len(states["holeshift"]) == n_hs)

# ---- Stretch Paint and Stretch->Hole removed (user, 2026-10-09): a sidecar still
# holding zones renders without them, and the preview's next save drops the key
zoned = {"version": 2, "enabled": True, "feather": 0.0,
         "zones": [{"x": 0.1, "y": 0.1, "w": 0.4, "h": 0.4, "threshold": 1.5, "start": 0, "end": -1, "enabled": True}]}
sc = FIX / "depth" / "clipA_depth.fssidecar"
old = sc.read_text() if sc.exists() else None
data = json.loads(old) if old else {}
data["stretch_corrections"] = zoned
sc.write_text(json.dumps(data))
got = ctl._ensure_processor()._get_video_specific_settings(str(FIX / "src" / "clipA.mp4"), ctl.build_settings(), False)
check("an old sidecar's Stretch Paint zones never reach a render", "stretch_corrections" not in got, str(sorted(got)[:6]))
check("...nor any Stretch->Hole setting", "stretch_to_hole" not in ctl._map.keys() and not hasattr(ctl.build_settings(), "stretch_to_hole"))
pv.step(1); settle(); pv.step(-1); settle()
eng._save_current_sidecar_data(is_auto_save=False); settle()
check("...and the preview's next save drops them from the file", "stretch_corrections" not in json.loads(sc.read_text()))
if old is None: sc.unlink()
else: sc.write_text(old)

# reload: the regions come back from the sidecar
# (Vertical Warp Fix is gone from v2, 2026-09-28: the Depth Map Editor fixes it)
pv.step(1); settle(); pv.step(-1); settle()
check("reload restores the regions", not eng.dilate_regions.is_empty())
pv.shutdown()
print("toasts:", [(k, a, b[:80]) for k, a, b in toasts])
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
