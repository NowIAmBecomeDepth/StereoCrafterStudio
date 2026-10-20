"""Where a Glasses-Free 3D preview render's time goes, against Dubois and Side-by-Side
(HD fixture, config redirected; no SR service: the weaver's side is timed as its own
step). A profile, not a check: it prints, asserts nothing.

    SRC_N=8 .venv\\Scripts\\python.exe studio\\tests\\checks\\profile_gf3d.py
"""
import os, sys, tempfile, time, logging
from pathlib import Path
import _fixtures
os.environ["QT_QPA_PLATFORM"] = "offscreen"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
FIX = _fixtures.copy("hd", Path(tempfile.mkdtemp()) / "fx")
tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
import numpy as np
from PySide6.QtGui import QGuiApplication
app = QGuiApplication(sys.argv)
from studio.stages.splatting import settings as S
from studio.stages.splatting import preview as P
from studio.stages.splatting.preview_engine import SplattingPreviewEngine
from studio import sr_display

N = int(os.environ.get("SRC_N", "8"))
glue = {}


class Hooks:
    """The real glue's per-frame work (preview._Hooks.frame), timed."""
    def frame(self, image, scale=1.0):
        t0 = time.perf_counter()
        stereo = image is not None and bool(image.info.get("sr3d"))
        if stereo:
            t1 = time.perf_counter()
            arr = image.info.get("sr3d_np")
            if arr is None or arr.shape[:2] != (image.height, image.width):
                arr = np.asarray(image if image.mode == "RGB" else image.convert("RGB"))
            glue["offer"] = time.perf_counter() - t1
            glue["pair"] = arr
            h, w = arr.shape[0], arr.shape[1] // 2
            P.QImage(np.ascontiguousarray(arr[:, :w]).tobytes(), w, h, 3 * w, P.QImage.Format.Format_RGB888)
        else:
            P._to_qimage(image)
        glue["hook"] = time.perf_counter() - t0

    def __getattr__(self, n):
        return lambda *a, **k: None


eng = SplattingPreviewEngine(list(S.DEFAULTS), Hooks())
vals = dict(S.DEFAULTS)
vals.update({"input_source_clips": str(FIX / "src"), "input_depth_maps": str(FIX / "depth"),
             "output_splatted": str(FIX / "out"), "multi_map": False, "preview_size": "100%",
             "auto_save_sidecar": False, "max_disp": 23.0})
eng.store.values.update(vals)
logging.basicConfig(level=logging.WARNING)
pv = eng.previewer
pv.load_video_list(eng._find_preview_sources_callback)
pv._frame_buffer = None
from studio.stages.splatting import preview_engine as PE
PE.FAST["on"] = True          # as while a slider is held

# time the render callback and _present_frame separately
_cb, _pf = eng._preview_processing_callback, pv._present_frame
t = {}
def cb(*a, **k):
    t0 = time.perf_counter(); r = _cb(*a, **k); t["render"] = time.perf_counter() - t0; return r
def pf(*a, **k):
    if getattr(pv, "_sr3d_eyes", None) is not None:
        return _pf(*a, **k)
    t0 = time.perf_counter(); r = _pf(*a, **k); t["present"] = time.perf_counter() - t0 - glue.get("hook", 0); return r
eng._preview_processing_callback = cb
pv.processing_callback = cb
pv._present_frame = pf

for src in ("Dubois Anaglyph", "Side-by-Side", "Glasses-Free 3D"):
    eng.store.values["preview_source"] = src
    pv.frame_scrubber_var.set(20); pv.update_preview()
    rows = []
    for i in range(N):
        glue.clear(); t.clear()
        eng.store.values["max_disp"] = 24.0 + i
        t0 = time.perf_counter(); pv.update_preview(); total = time.perf_counter() - t0
        rows.append((total, t.get("render", 0), t.get("present", 0), glue.get("hook", 0), glue.get("offer", 0)))
    med = [sorted(c)[len(c) // 2] * 1000 for c in zip(*rows)]
    print(f"{src:20s} total {med[0]:6.1f} ms | render {med[1]:6.1f} | overlays/present {med[2]:5.1f} | "
          f"glue {med[3]:5.1f} (pair to numpy {med[4]:4.1f})", flush=True)

# the weaver's side of one new pair (render thread): crop + RGBA fill (the upload itself is GPU)
pair = glue.get("pair")
if pair is not None:
    surf = sr_display._Surface.__new__(sr_display._Surface); surf._rgba = None
    ts = []
    for _ in range(N):
        t0 = time.perf_counter()
        sbs = sr_display._crop_pair(pair, (0.0, 0.0, 1.0, 1.0))
        if surf._rgba is None or surf._rgba.shape[:2] != sbs.shape[:2]:
            surf._rgba = np.full(sbs.shape[:2] + (4,), 255, np.uint8)
        import cv2
        cv2.cvtColor(np.ascontiguousarray(sbs), cv2.COLOR_RGB2RGBA, dst=surf._rgba)   # as _Surface.upload
        ts.append(time.perf_counter() - t0)
    print(f"{'weaver: RGBA fill':20s} {sorted(ts)[len(ts) // 2] * 1000:6.1f} ms for {pair.shape[1]}x{pair.shape[0]}", flush=True)
os._exit(0)
