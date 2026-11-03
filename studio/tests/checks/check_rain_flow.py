"""check_rain_flow.py -- Rain's Flow dropdown (user, 2026-11-02: "a drop down option so i can see the live
preview of each when i switch back and forth"): DIS or SEA-RAFT for Moving.

On a synthetic panning clip with streaks falling over it (never real footage):
  * the dropdown shows with Moving, not with Static; picking SEA-RAFT sets the clip's rain_flow
  * the preview's frame changes when the flow is switched, and comes back the same on switching back
    (the frame buffer and Rain's own cache key on it)
  * SEA-RAFT is loaded for it and let go by Release GPU Memory
  * the render's log names the flow
Needs SEA-RAFT in the models folder and a GPU. Offscreen; configs and last_jobs.json redirected.

    python studio/tests/checks/check_rain_flow.py
"""
import os, sys, tempfile, hashlib, time, subprocess, threading, glob, json
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, str(Path(__file__).parent))
for _s in (sys.stdout, sys.stderr): _s.reconfigure(encoding="utf-8", errors="backslashreplace")
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
import _fixtures
import logging
logs = []
class _Keep(logging.Handler):
    def emit(self, r): logs.append(r.getMessage())
logging.getLogger().addHandler(_Keep()); logging.getLogger().setLevel(logging.INFO)
import cv2
import numpy as np
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from studio import app as A
from core.splatting import rain_gpu

fails = []
def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok: fails.append(label)

if not rain_gpu.raft_ready():
    print("SKIP: SEA-RAFT isn't in the models folder"); _fixtures.exit_now(0)

# ---- a panning clip with rain -------------------------------------------------------------
W, H, N, PAN = 640, 352, 24, 3
src_dir, dep_dir, out_dir = tmp / "src", tmp / "depth", tmp / "out"
for d in (src_dir, dep_dir, out_dir): d.mkdir()
big = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=size={W + PAN * N}x{H}:rate=24",
                      "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
big = np.frombuffer(big, np.uint8).reshape(H, W + PAN * N, 3).astype(np.float32) * 0.45
rng = np.random.default_rng(11)
frames = []
for t in range(N):
    f = big[:, PAN * t: PAN * t + W].copy(); m = np.zeros((H, W), np.uint8)
    for _ in range(180):
        x, y, L = int(rng.integers(8, W - 8)), int(rng.integers(0, H - 30)), int(rng.integers(10, 26))
        cv2.line(m, (x, y), (x + 2, y + L), 1, 1)
    frames.append(np.clip(f + m[..., None] * 120.0, 0, 255).astype(np.uint8))
def write(path, fr, pix="yuv444p", fmt="rgb24", crf="4"):
    p = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", fmt,
                          "-s", f"{fr[0].shape[1]}x{fr[0].shape[0]}", "-r", "24", "-i", "-", "-c:v", "libx264",
                          "-crf", crf, "-pix_fmt", pix, str(path)], stdin=subprocess.PIPE)
    for x in fr: p.stdin.write(np.ascontiguousarray(x).tobytes())
    p.stdin.close(); p.wait()
write(src_dir / "pan.mp4", frames)
write(dep_dir / "pan_depth.mp4", [np.full((H, W), 77, np.uint8)] * N, pix="gray", fmt="gray", crf="0")
(dep_dir / "pan_depth.fssidecar").write_text(json.dumps({"convergence_plane": "0.5", "max_disparity": "40", "gamma": 1.0}))

# ---- the app ----------------------------------------------------------------------------
qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
objs["Shell"].setProperty("uiScale", "1.0")
win = engine.rootObjects()[0]
win.resize(1600, 1000); win.show()
ctl = objs["Splatting"]
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
def settle(t=180):
    end = time.time() + t
    while time.time() < end:
        pump(0.02)
        with pv._cv:
            idle = not pv._jobs
        if idle and not pv._loading and pv._idle.is_set() and not any(
                getattr(pv, n).isActive() for n in ("_render_timer", "_overlay_timer") if hasattr(pv, n)):
            pump(0.2); return
def walk(it, out):
    for c in it.childItems():
        out.append(c); walk(c, out)
    return out
def named(n):
    return [c for c in walk(win.contentItem(), []) if c.objectName() == n]
def shown():
    img = pv.provider._images.get("frame")
    if img is None:
        return None
    img = img.convertToFormat(img.Format.Format_RGB888)
    return np.frombuffer(img.constBits(), np.uint8, img.sizeInBytes()).reshape(img.height(), img.bytesPerLine())[:, : img.width() * 3].astype(np.int16).copy()

pump(0.8)
stage = named("stage")
if stage:
    QMetaObject.invokeMethod(stage[0], "show", Q_ARG("QVariant", 1), Q_ARG("QVariant", True))
pump(0.8)
for k, v in {"input_source_clips": str(src_dir).replace("\\", "/"), "input_depth_maps": str(dep_dir).replace("\\", "/"),
             "output_splatted": str(out_dir).replace("\\", "/"), "multi_map": False, "preview_source": "Splat Result",
             "preview_size": "100%", "update_slider_from_sidecar": False, "auto_save_sidecar": False,
             "move_to_finished": False, "enable_full_res": True, "enable_low_res": False, "dual_output": False,
             "loop_playback": False, "auto_convergence_mode": "Off", "enable_global_norm": False,
             "max_disp": "40", "zero_disparity_anchor": "0.5", "depth_gamma": "1.0",
             "depth_dilate_size_x": "0", "depth_dilate_size_y": "0", "depth_blur_size_x": "0", "depth_blur_size_y": "0",
             "rain_mode": "static", "rain_particle": "rain", "rain_flow": "dis"}.items():
    ctl._set(k, v)
pump(0.6)

# ---- the dropdown -------------------------------------------------------------------------
drop = named("rainFlow")
check("the Flow dropdown is on the page", bool(drop))
check("...hidden with Static", bool(drop) and not drop[0].isVisible())
ctl._set("rain_mode", "moving"); pump(0.6)
check("...shown with Moving: DIS, SEA-RAFT, SEA-RAFT without Relight", bool(drop) and drop[0].isVisible()
      and list(drop[0].property("model")) == ["DIS", "SEA-RAFT", "SEA-RAFT, no Relight"], str(drop[0].property("model")) if drop else "")
QMetaObject.invokeMethod(drop[0], "activated", Q_ARG(int, 1)); pump(0.4)
check("...picking SEA-RAFT sets the clip's flow", ctl._map.value("rain_flow") == "raft", str(ctl._map.value("rain_flow")))
QMetaObject.invokeMethod(drop[0], "activated", Q_ARG(int, 2)); pump(0.4)
check("...no Relight sets raft_plain, the box short", ctl._map.value("rain_flow") == "raft_plain"
      and drop[0].property("displayText") == "No Relight", str(drop[0].property("displayText")))
QMetaObject.invokeMethod(drop[0], "activated", Q_ARG(int, 0)); pump(0.4)
check("...and DIS back", ctl._map.value("rain_flow") == "dis" and drop[0].property("displayText") == "DIS")

# ---- the live preview ----------------------------------------------------------------------
pv.load(); settle()
check("the clip loads in the preview", pv.loaded, str(getattr(pv, "status", "")))
pv.seek(12); settle()
dis1 = shown()
ctl._set("rain_flow", "raft"); settle()
raft = shown()
check("SEA-RAFT is loaded for the preview", rain_gpu._RAFT is not None)
ctl._set("rain_flow", "dis"); settle()
dis2 = shown()
ok = dis1 is not None and raft is not None and dis2 is not None
# a plain pan: both flows line it up about as well, so the rain differs only here and there
check("switching to SEA-RAFT changes the frame shown", ok and np.abs(dis1 - raft).mean() > 0.001,
      f"{np.abs(dis1 - raft).mean():.3f}" if ok else "no image")
check("...and back to DIS, the same frame as before", ok and np.abs(dis1 - dis2).mean() < 0.02,
      f"{np.abs(dis1 - dis2).mean():.4f}" if ok else "")
ctl._set("rain_flow", "raft"); settle()
raft2 = shown()
check("...and SEA-RAFT again, its frame again", raft2 is not None and ok and np.abs(raft - raft2).mean() < 0.02,
      f"{np.abs(raft - raft2).mean():.4f}" if raft2 is not None and ok else "")
ctl._set("rain_flow", "raft_plain"); settle()
plain = shown()
check("no Relight draws its own frame (Relight off)", plain is not None and raft is not None
      and np.abs(plain - raft).mean() > 0.0005, f"{np.abs(plain - raft).mean():.4f}" if plain is not None and raft is not None else "")
ctl._set("rain_flow", "raft"); settle()
ctl.releaseGpu(); settle()
check("Release GPU Memory lets SEA-RAFT go", rain_gpu._RAFT is None)

# ---- the render names it ---------------------------------------------------------------------
pv.load(); settle()                 # Release GPU Memory leaves the preview unloaded
toasts = []
ctl.toast.connect(lambda k, a, b: toasts.append((k, a, b)))
n0 = len(logs)
ctl.startSingle(); end = time.time() + 600
while time.time() < end and not any("Splatting" in t[1] for t in toasts):
    pump(0.05)
outs = glob.glob(str(out_dir / "**" / "*.mp4"), recursive=True)
check("a render with SEA-RAFT writes its output", bool(outs), str(toasts[-1:]))
check("...its log names SEA-RAFT", any("SEA-RAFT flow" in m for m in logs[n0:]))
check("...and the batch lets it go", rain_gpu._RAFT is None)

studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
check("the real last_jobs.json untouched", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails else 1)
