"""Headless: drive SplattingPreview over the synthetic fixture (config redirected)."""
import os, sys, tempfile, hashlib, time
from pathlib import Path
import _fixtures
os.environ["QT_QPA_PLATFORM"] = "offscreen"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
FIX = _fixtures.copy("small", Path(tempfile.mkdtemp()) / "fx")
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
from PySide6.QtGui import QGuiApplication
app = QGuiApplication(sys.argv)
from studio.shell import StudioShell
from studio.stages.splatting.controller import SplattingController
shell = StudioShell()
ctl = SplattingController(shell)
pv = ctl.preview
for k, v in {"input_source_clips": str(FIX / "src").replace("\\", "/"),
             "input_depth_maps": str(FIX / "depth").replace("\\", "/"),
             "output_splatted": str(FIX / "out").replace("\\", "/"),
             "multi_map": False, "preview_source": "Splat Result", "preview_size": "100%",
             "update_slider_from_sidecar": True, "auto_save_sidecar": False, "depth_dilate_left": float(os.environ.get("DL", "0"))}.items():
    ctl._set(k, v)
fails = []
def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok: fails.append(label)
def wait_idle(timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 50)
        with pv._cv:
            empty = not pv._jobs
        if empty and not pv._loading and (pv._thread is None or pv._busy_flag_idle()):
            return True
        time.sleep(0.02)
    return False
def _idle(self):
    return getattr(self, "_idle_evt", None) is None or self._idle_evt.is_set()
# track worker idleness: wrap _do
import threading
pv._idle_evt = threading.Event(); pv._idle_evt.set()
_orig = pv._do
def _do(*a, **k):
    pv._idle_evt.clear()
    try:
        return _orig(*a, **k)
    finally:
        pv._idle_evt.set()
pv._do = _do
type(pv)._busy_flag_idle = _idle
def settle():
    for _ in range(5):
        wait_idle(); QCoreApplication.processEvents(QEventLoop.AllEvents, 100); time.sleep(0.1)
def save(name):
    img = pv.provider._images.get("frame")
    if img is not None:
        img.save(str(tmp / name))
    return img

t = time.time(); pv.load(); settle()
check("load: loaded", pv.loaded, f"{time.time()-t:.1f}s")
check("two clips found", pv.videoCount == 2, str(pv.videoCount))
check("frames counted", pv.frameCount == 48, str(pv.frameCount))
img = save("pv_splat.png")
check("a frame arrived", img is not None and img.width() == 640, f"{img.width() if img else None}x{img.height() if img else None} serial {pv.imageSerial}")
check("clip info filename", ctl._info.get("filename") == "clipA.mp4", ctl._info.get("filename"))
s0 = pv.imageSerial
pv.seek(30); settle()
check("seek renders", pv.imageSerial > s0 and pv.frame == 30, f"frame {pv.frame} serial {pv.imageSerial}")
s0 = pv.imageSerial
pv.step(1); settle()
check("next clip", pv.videoIndex == 1 and pv.imageSerial > s0, f"index {pv.videoIndex}")
check("clip info follows", ctl._info.get("filename") == "clipB.mp4", ctl._info.get("filename"))
s0 = pv.imageSerial
ctl._map.insert("max_disp", 60.0); ctl._on_value_changed("max_disp", 60.0); settle()
check("setting change re-renders", pv.imageSerial > s0, f"serial {pv.imageSerial}")
for src in ("Depth Map", "Dubois Anaglyph", "Occlusion Mask", "Original (Left Eye)", "Side-by-Side"):
    s0 = pv.imageSerial
    ctl._map.insert("preview_source", src); ctl._on_value_changed("preview_source", src); settle()
    img = save("pv_" + src.split()[0].lower() + ".png")
    check(f"source {src}", pv.imageSerial > s0 and img is not None, f"{img.width()}x{img.height()}" if img else "")
ctl._map.insert("preview_source", "Wigglegram"); ctl._on_value_changed("preview_source", "Wigglegram"); settle()
check("wigglegram gives two eyes", pv.wiggling and "left" in pv.provider._images)
s0 = pv.imageSerial
pv.setDragging(True)
for v in (31.0, 33.0, 35.0):
    ctl._map.insert("max_disp", v); ctl._on_value_changed("max_disp", v)
    QCoreApplication.processEvents(QEventLoop.AllEvents, 20); time.sleep(0.03)
settle(); s1 = pv.imageSerial
pv.setDragging(False); settle()
check("drag renders live, release renders exact", s1 > s0 and pv.imageSerial > s1, f"{s0} -> {s1} -> {pv.imageSerial}")
check("display scale follows Preview Scale", abs(pv.displayScale - 1.0) < 1e-6, str(pv.displayScale))
ctl._map.insert("preview_size", "50%"); ctl._on_value_changed("preview_size", "50%"); settle()
check("display scale 50%", abs(pv.displayScale - 0.5) < 1e-6 and pv.imageWidth in (640, 1280), f"{pv.displayScale} {pv.imageWidth}")
import torch
ctl._map.insert("preview_source", "Splat Result"); ctl._on_value_changed("preview_source", "Splat Result"); settle()
pv.seek(12); settle()
before = torch.cuda.memory_allocated(); idx0, fr0 = pv.videoIndex, pv.frame
called = []; print("   reserved before", torch.cuda.memory_reserved()/1e6, flush=True)
pv.release_for_job(lambda: called.append(torch.cuda.memory_allocated())); settle(); print("   reserved after", torch.cuda.memory_reserved()/1e6, flush=True)
import gc as _gc
_gc.collect()
for _o in _gc.get_objects():
    try:
        if torch.is_tensor(_o) and _o.is_cuda:
            _refs = [type(r).__name__ + (":" + ",".join(k for k, v in r.items() if v is _o)[:60] if isinstance(r, dict) else "") for r in _gc.get_referrers(_o)[:4]]
            print("   cuda tensor", tuple(_o.shape), _o.dtype, _refs, flush=True)
    except Exception:
        pass
check("release calls back after freeing", called and called[0] < before and not pv.loaded,
      f"{before/1e6:.1f} MB -> {called[0]/1e6 if called else -1:.1f} MB")
pv.restore_after_job(); settle()
check("restored to the same clip and frame", pv.loaded and pv.videoIndex == idx0 and pv.frame == fr0,
      f"{idx0},{fr0} -> {pv.videoIndex},{pv.frame}")
pv.clear(); settle()
check("clear unloads", not pv.loaded and pv.imageWidth == 0)
check("no sidecar written", not any((FIX / "depth").glob("*.fssidecar")) and not any((FIX / "src").glob("*.fssidecar")))
pv.shutdown()
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
