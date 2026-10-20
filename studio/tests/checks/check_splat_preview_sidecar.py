"""Headless: clip load with a sidecar, under each Border mode (fixture only)."""
import os, sys, json, tempfile, hashlib, time, shutil
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
def _timers_pending():
    # A setting change queues its render only after the preview's settle
    # timer (120 ms); until it has fired, the preview is not settled.
    return any(getattr(pv, name).isActive() for name in ("_render_timer", "_overlay_timer")
               if hasattr(pv, name))
def settle(pv, t=60):
    end = time.time() + t
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 20); time.sleep(0.02)
        with pv._cv:
            idle = not pv._jobs
        if idle and not pv._loading and pv._idle.is_set() and not _timers_pending():
            for _ in range(10):
                QCoreApplication.processEvents(QEventLoop.AllEvents, 20); time.sleep(0.02)
            return
shell = StudioShell()
for mode in os.environ.get("MODES", "Off,Manual,Auto Basic,Auto Adv.").split(","):
    sc = FIX / "depth" / "clipA_depth.fssidecar"
    sc.write_text(json.dumps({"convergence_plane": 0.3, "max_disparity": 40.0, "gamma": 1.2,
                              "left_border": 0.5, "right_border": 0.2, "border_mode": mode}))
    ctl = SplattingController(shell)
    pv = ctl.preview
    import threading
    pv._idle = threading.Event(); pv._idle.set()
    _o = pv._do
    def _do(*a, _o=_o, pv=pv, **k):
        pv._idle.clear()
        try: return _o(*a, **k)
        finally: pv._idle.set()
    pv._do = _do
    errors = []
    pv.toast.connect(lambda k, t, m: errors.append((k, t, m)) if k == "error" else None)
    for k, v in {"input_source_clips": str(FIX / "src").replace("\\", "/"),
                 "input_depth_maps": str(FIX / "depth").replace("\\", "/"),
                 "output_splatted": str(FIX / "out").replace("\\", "/"), "multi_map": False,
                 "preview_source": "Splat Result", "preview_size": "100%", "border_mode": mode,
                 "update_slider_from_sidecar": True, "auto_save_sidecar": False,
                 "zero_disparity_anchor": 0.5, "max_disp": 20.0, "depth_gamma": 1.0}.items():
        ctl._set(k, v)
    pv.settingsWritten.connect(lambda w: print("   writes:", {k: w[k] for k in w if "border" in k}, flush=True))
    pv.load(); settle(pv)
    m = ctl._map
    check(f"[{mode}] loaded, no error", pv.loaded and pv.imageSerial > 0 and not errors, str(errors)[:200])
    check(f"[{mode}] sliders took the sidecar",
          abs(float(m.value("zero_disparity_anchor")) - 0.3) < 1e-6 and abs(float(m.value("max_disp")) - 40) < 1e-6
          and abs(float(m.value("depth_gamma")) - 1.2) < 1e-6,
          f"conv {m.value('zero_disparity_anchor')} disp {m.value('max_disp')} gamma {m.value('depth_gamma')} "
          f"bw {m.value('border_width')} bb {m.value('border_bias')}")
    if mode == "Off":
        check("[Off] border zeroed as v1 does", float(m.value("border_width")) == 0.0, str(m.value("border_width")))
    check(f"[{mode}] border mode kept", m.value("border_mode") == mode, str(m.value("border_mode")))
    pv.step(1); settle(pv)
    check(f"[{mode}] next clip (no sidecar) ok", pv.videoIndex == 1 and not errors, str(errors)[:200])
    pv.shutdown()
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
