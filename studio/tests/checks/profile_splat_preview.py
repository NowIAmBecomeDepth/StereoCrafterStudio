"""Where a 1080p preview render's time goes (HD fixture, config redirected)."""
import os, sys, tempfile, time, logging, cProfile, pstats, io
from pathlib import Path
import _fixtures
os.environ["QT_QPA_PLATFORM"] = "offscreen"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
FIX = _fixtures.copy("hd", Path(tempfile.mkdtemp()) / "fx")
tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from studio.stages.splatting import settings as S
from studio.stages.splatting.preview_engine import SplattingPreviewEngine
class H:
    def __getattr__(self, n): return lambda *a, **k: None
eng = SplattingPreviewEngine(list(S.DEFAULTS), H())
vals = dict(S.DEFAULTS)
vals.update({"input_source_clips": str(FIX / "src"), "input_depth_maps": str(FIX / "depth"),
             "output_splatted": str(FIX / "out"), "multi_map": False, "preview_size": "75%",
             "preview_source": os.environ.get("SRC", "Dubois Anaglyph"), "strict_ffmpeg_decode": os.environ.get("STRICT") == "1",
             "depth_dilate_size_x": 3.5, "depth_dilate_size_y": 0.5, "depth_blur_size_x": 3, "depth_blur_size_y": 3,
             "depth_dilate_left": 4.0, "depth_blur_left": 5, "auto_save_sidecar": False, "max_disp": 23.0,
             "zero_disparity_anchor": 0.9, "depth_gamma": 1.75})
eng.store.values.update(vals)
logging.getLogger("splatting.v2.preview").setLevel(logging.WARNING)
logging.basicConfig(level=logging.WARNING, format="%(message)s")
pv = eng.previewer
pv.load_video_list(eng._find_preview_sources_callback)
pv._frame_buffer = None           # measure real renders, not v1's display cache
from studio.stages.splatting import preview_engine as PE
def timed(label, fn, n=1):
    ts = []
    for _ in range(n):
        t0 = time.perf_counter(); fn(); ts.append(time.perf_counter() - t0)
    print(f"{label:58s} {min(ts)*1000:7.1f} ms" + (f"  (median {sorted(ts)[len(ts)//2]*1000:.1f})" if n > 1 else ""), flush=True)
def goto(f):
    pv.frame_scrubber_var.set(f); pv.last_loaded_frame_index = f; pv.update_preview()
seq = iter(range(1, 40))
timed("playback-like: next frame, exact (decode+preproc+warp)", lambda: goto(next(seq)), 12)
seq2 = iter(range(13, 40))
PE.FAST["on"] = True
timed("playback-like: next frame, GPU pre-processing", lambda: goto(next(seq2)), 12)
PE.FAST["on"] = False
goto(30)
vals = iter([24.0 + i for i in range(20)])
def disp():
    eng.store.values["max_disp"] = next(vals); pv.update_preview()
timed("same frame, Disparity changed (cached decode + pre-proc)", disp, 6)
pr = cProfile.Profile(); pr.enable(); disp(); pr.disable()
s = io.StringIO(); pstats.Stats(pr, stream=s).sort_stats("tottime").print_stats(22); print(s.getvalue())
os._exit(0)
dl = iter([4.5, 5.0, 5.5, 6.0, 6.5, 7.0])
def dil():
    eng.store.values["depth_dilate_left"] = next(dl); pv.update_preview()
timed("same frame, Dilate Left changed, exact (CPU pre-proc)", dil, 6)
PE.FAST["on"] = True
dl2 = iter([7.5, 8.0, 8.5, 9.0, 9.5, 10.0])
def dil2():
    eng.store.values["depth_dilate_left"] = next(dl2); pv.update_preview()
timed("same frame, Dilate Left changed while dragging (GPU)", dil2, 6)
PE.FAST["on"] = False
os._exit(0)
