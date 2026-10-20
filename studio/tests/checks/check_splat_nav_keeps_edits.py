"""Headless: leaving a clip keeps the dials changed on it (fixture only).

User, 2026-10-17: "all my splat settings reset after using a pop out boost and
moving to the next clip". Next ran the navigate callback before the index moved,
with the per-clip restore guard cleared, so it re-loaded the clip being LEFT from
its sidecar: every dial changed on it went back to the saved values (keys the
sidecar lacks to their defaults), and Auto-Save wrote those back over the edits.
Now: the edits carry to the next clip (Boost and Pop-Out still start at 0 per
clip), Auto-Save saves them into the clip that was left, and going back to it
loads what was saved.
"""
import os, sys, json, tempfile, hashlib, time, threading
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
def settle(pv, t=60):
    end = time.time() + t
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 20); time.sleep(0.02)
        with pv._cv:
            idle = not pv._jobs
        if idle and not pv._loading and pv._idle.is_set() and not any(
                getattr(pv, n).isActive() for n in ("_render_timer", "_overlay_timer") if hasattr(pv, n)):
            for _ in range(10):
                QCoreApplication.processEvents(QEventLoop.AllEvents, 20); time.sleep(0.02)
            return
SIDECAR = FIX / "depth" / "clipA_depth.fssidecar"
SAVED = {"convergence_plane": 0.3, "max_disparity": 40.0, "gamma": 1.2}
EDITS = {"max_disp": "35.0", "zero_disparity_anchor": "0.62", "depth_gamma": "1.3",
         "depth_dilate_size_x": "4.0", "depth_blur_size_x": "5",
         "boost_amount": 12.0, "boost_thresh": 0.9, "boost_curve": 3.0, "pop_amount": 6.0}
#: what the next clip (no sidecar) must still show: everything but the per-clip Boost / Pop-Out
KEPT = {k: v for k, v in EDITS.items() if k not in ("boost_amount", "pop_amount")}
shell = StudioShell()
for autosave in (False, True):
    tag = "auto-save on" if autosave else "auto-save off"
    for f in (FIX / "depth").glob("*.fssidecar"):
        f.unlink()
    SIDECAR.write_text(json.dumps(SAVED))
    ctl = SplattingController(shell)
    pv = ctl.preview
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
                 "preview_source": "Splat Result", "preview_size": "100%",
                 "update_slider_from_sidecar": True, "auto_save_sidecar": autosave}.items():
        ctl._set(k, v)
    pv.load(); settle(pv)
    m = ctl._map
    check(f"[{tag}] clip A took its sidecar", pv.videoIndex == 0 and float(m.value("max_disp")) == 40.0,
          f"index {pv.videoIndex} disp {m.value('max_disp')}")
    for k, v in EDITS.items():                     # the user's dials, as QML sets them
        m.insert(k, v); ctl._on_value_changed(k, v)
    settle(pv)
    pv.step(1); settle(pv)
    off = {k: (v, m.value(k)) for k, v in KEPT.items() if float(m.value(k)) != float(v)}
    check(f"[{tag}] next clip keeps the dials changed on clip A", pv.videoIndex == 1 and not off,
          f"index {pv.videoIndex} reset: {off}")
    check(f"[{tag}] Boost and Pop-Out start at 0 on the next clip",
          float(m.value("boost_amount")) == 0.0 and float(m.value("pop_amount")) == 0.0,
          f"boost {m.value('boost_amount')} pop {m.value('pop_amount')}")
    sc = json.loads(SIDECAR.read_text())
    if autosave:
        check("[auto-save on] clip A's sidecar has the edits",
              abs(float(sc.get("max_disparity", 0)) - 35.0) < 1e-6
              and abs(float(sc.get("convergence_plane", 0)) - 0.62) < 1e-6
              and abs(float(sc.get("gamma", 0)) - 1.3) < 1e-6, str({k: sc.get(k) for k in SAVED}))
    else:
        check("[auto-save off] clip A's sidecar untouched", sc == SAVED, str(sc)[:200])
    pv.step(-1); settle(pv)
    want = 35.0 if autosave else 40.0
    check(f"[{tag}] back on clip A: what its sidecar holds",
          pv.videoIndex == 0 and float(m.value("max_disp")) == want and not errors,
          f"index {pv.videoIndex} disp {m.value('max_disp')} {str(errors)[:200]}")
    pv.shutdown()
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
