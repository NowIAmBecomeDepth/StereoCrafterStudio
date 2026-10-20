"""The GPU hand-over between stages (audit 2026-09-28 #14 / #15), in the real
app offscreen. A stage's job must not start until every other stage has
ACTUALLY let go: Splatting's preview releases on its worker, behind whatever
it is doing, so the claimant's job waits for that release (not just for the
request) -- with a render in flight, with the very first preview load still
importing, and with a failed preview job ahead of the release (which used to
signal "released" early). Scene Cut and the Depth Map Editor now claim like
every other stage: refused while another stage renders. Inpainting V1 is the
claimant (its engine stood in for); the Splatting preview is real, over the
small fixture. Configs and last_jobs.json redirected."""
import os, sys, tempfile, hashlib, time, types, threading
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, str(Path(__file__).parent))
rec = REPO / "last_jobs.json"; h0 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
tmp = Path(tempfile.mkdtemp())
import _fixtures
FIX = _fixtures.copy("small", tmp / "fx")
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from PySide6.QtCore import QCoreApplication, QEventLoop
from PySide6.QtGui import QGuiApplication

# Inpainting V1's engine, stood in for: it records when its job began
fake = types.ModuleType("studio.stages.inpainting.engine")
starts = []
class FakeEngine:
    def __init__(self, run_settings, run_paths, app_config, hooks, stop_event):
        self.hooks = hooks
    def _configure_logging(self):
        pass
    def run_batch_process(self, input_folder, output_folder, **kw):
        starts.append(time.monotonic())
        time.sleep(0.5)
        from studio.outcome import Outcome
        self.hooks.finished(False, Outcome(done=1))
fake.InpaintingEngine = FakeEngine
sys.modules["studio.stages.inpainting.engine"] = fake

from studio import app as A
qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
win.show()
fails = []
def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok: fails.append(label)
def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.005)
def until(cond, sec=60):
    end = time.time() + sec
    while not cond() and time.time() < end:
        pump(0.05)
    return cond()

pump(0.8)
J = objs["GpuJobs"]
sp, ip, sc, de = objs["Splatting"], objs["Inpainting"], objs["SceneCut"], objs["DepthEdit"]
refusals = []
J.refused.connect(lambda t, m: refusals.append(m))
pv = sp.preview
for k, v in {"input_source_clips": str(FIX / "src").replace("\\", "/"), "input_depth_maps": str(FIX / "depth").replace("\\", "/"),
             "output_splatted": str(FIX / "out").replace("\\", "/"), "multi_map": False, "preview_source": "Splat Result",
             "preview_size": "100%", "auto_save_sidecar": False}.items():
    sp._set(k, v)
(tmp / "ipin").mkdir(); (tmp / "ipout").mkdir()
(tmp / "ipin" / "x_splatted4.mp4").write_bytes(b"x")
ip.settings.setProperty("input_folder", str(tmp / "ipin")); ip.settings.setProperty("output_folder", str(tmp / "ipout"))
ip.settings.setProperty("hires_blend_folder", "")

# the preview's worker, watched: when each release really ran; a render can be slowed or made to fail
released = []
slow = {"render": 0.0, "fail": False}
_do = type(pv)._do
def watched_do(self, kind, arg, changes, fast=False):
    if kind == "render" and slow["render"]:
        time.sleep(slow["render"])
        if slow["fail"]:
            raise RuntimeError("stand-in preview failure")
    result = _do(self, kind, arg, changes, fast)
    if kind == "release":
        released.append(time.monotonic())
    return result
type(pv)._do = watched_do

def inpainting_run():
    starts.clear()
    ip.start()
    ok = until(lambda: starts and not ip.property("busy"), 60)
    pump(0.5)
    return ok

# ---- 1. the very first preview load still in flight (its engine not built yet)
released.clear()
pv.load()
check("a first preview load is in flight, engine not built yet", pv._engine is None or not pv.loaded)
check("Inpainting's job runs", inpainting_run())
check("...the preview was released anyway (not skipped while it was importing)", bool(released), str(released))
check("...before the job began", released and starts and released[-1] <= starts[0],
      f"released {released[-1:] } start {starts[:1]}")
until(lambda: not pv._jobs and not pv._loading, 60)

# ---- 2. a render in flight: the job waits for the release queued behind it
pv.load()
check("the Splatting preview loads", until(lambda: pv.loaded and not pv._jobs, 90))
released.clear(); slow["render"] = 2.0
pv._submit("render")
pump(0.2)
t0 = time.monotonic()
check("Inpainting's job runs", inpainting_run())
slow["render"] = 0.0
check("...only once the preview had let go (behind the 2 s render)",
      released and starts and released[-1] <= starts[0] and starts[0] - t0 >= 1.5,
      f"release at +{(released[-1] - t0) if released else -1:.2f}s, job at +{(starts[0] - t0) if starts else -1:.2f}s")
until(lambda: pv.loaded and not pv._jobs, 60)

# ---- 3. a failed preview job ahead of the release must not signal it early
released.clear(); slow.update(render=1.0, fail=True)
pv._submit("render")
pump(0.2)
check("Inpainting's job runs", inpainting_run())
slow.update(render=0.0, fail=False)
check("...only after the release itself, not the failed render before it",
      released and starts and released[-1] <= starts[0], f"released {released} start {starts[:1]}")
until(lambda: not pv._jobs, 60)

# ---- 4. Scene Cut claims (refused while another stage renders)
sc.ensureLoaded()
check("the Scene Cut engine loads", until(lambda: sc.property("ready"), 90), sc.property("loadError"))
for k, v in {"video_path": str(FIX / "src" / "clipA.mp4").replace("\\", "/"),
             "output_dir": str(tmp / "scout").replace("\\", "/"), "split_by_chapter": False}.items():
    sc.property("settings").setProperty(k, v)
pump(0.3)
refusals.clear()
_fixtures.fake_busy(sp, True)
sc.start(); pump(1.0)
check("Scene Cut is refused while Splatting renders", refusals and "Splatting is currently processing" in refusals[-1]
      and not sc.property("busy"), str(refusals))
_fixtures.fake_busy(sp, False)
claims = []
_claim = J.claim
J.claim = lambda sid, ctl=None: (claims.append(sid), False)[1]
sc.start(); pump(1.0)
check("...and its Start claims the GPU", "scenecut" in claims, str(claims))
J.claim = _claim

# ---- 5. the Depth Map Editor claims before SAM 3 loads; Segmenting counts as busy
de.ensureLoaded()
check("the Depth Map Editor engine loads", until(lambda: de._engine is not None, 90))
refusals.clear()
_fixtures.fake_busy(sp, True)
de.run("_track"); pump(0.8)
check("Track is refused while Splatting renders (SAM 3 would load beside it)",
      refusals and "Splatting is currently processing" in refusals[-1], str(refusals))
de.run("_find_text"); pump(0.5)
check("...and Find", len(refusals) >= 2, str(refusals))
_fixtures.fake_busy(sp, False)
eng = de._engine
eng._busy_job = "Segmenting"
check("a SAM 3 click being segmented counts as busy (no other stage starts under it)", de.is_busy())
eng._busy_job = None

type(pv)._do = _do
print()
_lj = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None) == h0
print("real last_jobs.json unchanged:", _lj)
if not _lj:
    fails.append("real last_jobs.json changed")
print("FAILED:" if fails else "ALL OK", fails if fails else "")
for name in ("DepthCrafter", "Splatting", "Inpainting", "InpaintingV2", "Merging", "SceneCut", "DepthEdit"):
    try: objs[name].shutdown()
    except Exception: pass
_fixtures.exit_now(1 if fails else 0)
