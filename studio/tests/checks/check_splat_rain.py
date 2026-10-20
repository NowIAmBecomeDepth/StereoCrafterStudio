"""check_splat_rain.py -- Splatting's Rain (user, 2026-10-06; core.splatting.rain),
end to end through the controller, on a synthetic clip (never real footage):
a still textured scene at ONE depth, with thin bright streaks falling over it
at random every frame.

Two real renders, Rain off and Rain on (Static camera), then in the right eye:
  * Rain off: the streaks sit at the scene's disparity (painted on the scene)
  * Rain on: they sit nearer -- shifted further -- than the scene
  * the scene itself (away from the rain) matches between the two renders
  * the left eye is the source frame, rain and all, in both
plus the Rain section on the page, its settings reaching the render, the
preview rendering with Rain on, and no QML warnings. Configs and
last_jobs.json redirected.
"""
import os, sys, tempfile, hashlib, time, subprocess, threading, glob
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, str(Path(__file__).parent))
for _s in (sys.stdout, sys.stderr): _s.reconfigure(encoding="utf-8", errors="backslashreplace")
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
import logging
logging.basicConfig(level=logging.INFO, format="   log: %(message)s")
import cv2
import numpy as np
from PySide6.QtCore import QCoreApplication, QEventLoop, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if "qml" in m.lower() else None)
from PySide6.QtGui import QGuiApplication

fails = []
def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok: fails.append(label)

# ---- the synthetic rainy clip --------------------------------------------------------
W, H, N, SCENE_D = 640, 352, 32, 0.30
src_dir, dep_dir, out_dir = tmp / "src", tmp / "depth", tmp / "out"
for d in (src_dir, dep_dir, out_dir): d.mkdir()
rng = np.random.default_rng(7)
base = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=size={W}x{H}:rate=24",
                       "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
base = (np.frombuffer(base, np.uint8).reshape(H, W, 3).astype(np.float32) * 0.45)    # a darker scene, as rain shots are
frames, masks = [], []
for t in range(N):
    f = base.copy(); m = np.zeros((H, W), np.uint8)
    for _ in range(180):
        x, y, L = int(rng.integers(8, W - 8)), int(rng.integers(0, H - 30)), int(rng.integers(10, 26))
        cv2.line(m, (x, y), (x + 2, y + L), 1, 1)
    f = np.clip(f + m[..., None] * 120.0, 0, 255)
    frames.append(f.astype(np.uint8)); masks.append(m.astype(bool))
def write(path, fr, pix="yuv444p", fmt="rgb24", crf="4"):
    p = subprocess.Popen(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", fmt,
                          "-s", f"{fr[0].shape[1]}x{fr[0].shape[0]}", "-r", "24", "-i", "-", "-c:v", "libx264",
                          "-crf", crf, "-pix_fmt", pix, str(path)], stdin=subprocess.PIPE)
    for x in fr: p.stdin.write(np.ascontiguousarray(x).tobytes())
    p.stdin.close(); p.wait()
write(src_dir / "rainy.mp4", frames)
write(dep_dir / "rainy_depth.mp4", [np.full((H, W), int(SCENE_D * 255), np.uint8)] * N, pix="gray", fmt="gray", crf="0")
# a sidecar written before Rain existed (user, 2026-10-07: it read as Rain "off" and overrode the
# page, so the rain never reached the render): it must leave Rain as the page sets it
import json as _json
(dep_dir / "rainy_depth.fssidecar").write_text(_json.dumps({"convergence_plane": "0.5", "max_disparity": "40", "gamma": 1.0}))

# ---- the controller, as check_splat_single drives it ----------------------------------
app = QGuiApplication(sys.argv)
from studio.shell import StudioShell
from studio.stages.splatting.controller import SplattingController
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
        if idle and not pv._loading and pv._idle.is_set() and not any(
                getattr(pv, n).isActive() for n in ("_render_timer", "_overlay_timer") if hasattr(pv, n)):
            pump(0.2); return
toasts = []
ctl.toast.connect(lambda k, a, b: toasts.append((k, a, b)))
for k, v in {"input_source_clips": str(src_dir).replace("\\", "/"), "input_depth_maps": str(dep_dir).replace("\\", "/"),
             "output_splatted": str(out_dir).replace("\\", "/"), "multi_map": False, "preview_source": "Splat Result",
             "preview_size": "100%", "update_slider_from_sidecar": False, "auto_save_sidecar": False,
             "move_to_finished": False, "enable_full_res": True, "enable_low_res": False, "dual_output": False,
             "loop_playback": False, "auto_convergence_mode": "Off", "enable_global_norm": False,
             "max_disp": "40", "zero_disparity_anchor": "0.5", "depth_gamma": "1.0",
             "depth_dilate_size_x": "0", "depth_dilate_size_y": "0", "depth_blur_size_x": "0", "depth_blur_size_y": "0",
             "rain_mode": "off", "rain_depth": 0.6, "rain_amount": 1.0}.items():
    ctl._set(k, v)
pv.load(); settle()
check("the clip loads in the preview", pv.loaded, str(getattr(pv, "status", "")))

def run_single(tag):
    n = len(toasts); ctl.startSingle(); end = time.time() + 600
    while time.time() < end and not any("Splatting" in t[1] for t in toasts[n:]):
        pump(0.05)
    outs = sorted(glob.glob(str(out_dir / "**" / "*.mp4"), recursive=True), key=os.path.getmtime)
    if not outs:
        return None, toasts[n:]
    kept = tmp / f"render_{tag}.mp4"
    os.replace(outs[-1], kept)
    return kept, toasts[n:]

def frames_of(path):
    info = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                           "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout.strip().split(",")
    w, h = int(info[0]), int(info[1])
    raw = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path), "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3)

off_path, got = run_single("off"); print("   rain off:", got, flush=True)
check("a render with Rain off writes its output", off_path is not None)
ctl._set("rain_mode", "static"); settle()
check("the preview renders with Rain on", pv.loaded and not any("Rain preview failed" in str(w) for w in warns))
# ---- the preview follows every Rain change, on a frame it has drawn before (user, 2026-10-07:
# v1's frame buffer kept frames by a fixed list of settings without Rain -- Amount snapped back
# on release, Rain off still showed rain on a frame already seen)
def shown():
    img = pv.provider._images.get("frame")
    if img is None:
        return None
    img = img.convertToFormat(img.Format.Format_RGB888)
    return np.frombuffer(img.constBits(), np.uint8, img.sizeInBytes()).reshape(img.height(), img.bytesPerLine())[:, : img.width() * 3].astype(np.int16).copy()
def at(frame):
    pv.seek(frame); settle(); return shown()
ctl._set("rain_amount", 1.0); settle()
a1 = at(10)
ctl._set("rain_amount", 0.2); settle(); a02 = shown()
ctl._set("rain_amount", 1.0); settle(); a1b = shown()
ok = a1 is not None and a02 is not None and a1b is not None
check("the preview: Amount changes the frame shown", ok and np.abs(a1 - a02).mean() > 0.2, f"{np.abs(a1 - a02).mean():.2f}" if ok else "no image")
check("...and back to the same Amount, the same frame", ok and np.abs(a1 - a1b).mean() < 0.05, f"{np.abs(a1 - a1b).mean():.3f}" if ok else "")
ctl._set("rain_mode", "off"); settle(); off_here = shown()
check("...Rain off takes the rain off the frame shown", off_here is not None and np.abs(off_here - a1).mean() > 0.2)
at(20); off_back = at(10)
check("...and stays off on coming back to that frame", off_back is not None and np.abs(off_back - off_here).mean() < 0.05
      and np.abs(off_back - a1).mean() > 0.2, f"{np.abs(off_back - off_here).mean():.3f}" if off_back is not None else "")
ctl._set("rain_mode", "static"); settle()
on_path, got = run_single("on"); print("   rain on:", got, flush=True)
check("a render with Rain on writes its output (an old sidecar without Rain beside the map)", on_path is not None)
# the rain's own Pop (user, 2026-10-07): the rain alone comes further out, the scene stays
ctl._set("rain_pop", 30.0); settle()
pop_path, got = run_single("pop"); print("   rain pop:", got, flush=True)
check("a render with the rain's Pop writes its output", pop_path is not None)
ctl._set("rain_pop", 0.0); settle()

if off_path and on_path:
    A, B = frames_of(off_path), frames_of(on_path)
    h2, w2 = A.shape[1] // 2, A.shape[2] // 2
    check("the output is the 4-panel grid at the clip's size", (h2, w2) == (H, W), f"{A.shape}")
    left_off, left_on = A[:, :h2, :w2].astype(np.int16), B[:, :h2, :w2].astype(np.int16)
    right_off, right_on = A[:, h2:, w2:].astype(np.float32), B[:, h2:, w2:].astype(np.float32)
    src = np.stack(frames).astype(np.int16)
    check("the left eye is the source frame, rain and all, with Rain on",
          np.abs(left_on - src).mean() < 8 and np.abs(left_on - left_off).mean() < 1.0,   # 8: the encoder, on a saturated test pattern
          f"vs source {np.abs(left_on - src).mean():.2f}, vs Rain off {np.abs(left_on - left_off).mean():.2f}")

    def rain_shift(right, t):
        """Where frame t's streaks landed: the x-shift (left eye -> right eye) that lines the
        known streaks up with the right eye's thin bright detail."""
        g = cv2.cvtColor(right[t].astype(np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float32)
        th = cv2.morphologyEx(g, cv2.MORPH_TOPHAT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
        m = masks[t].astype(np.float32)
        scores = {dx: float((np.roll(m, -dx, axis=1)[:, 70:-70] * th[:, 70:-70]).sum()) for dx in range(-60, 61)}
        return max(scores, key=scores.get), scores
    def scene_shift(right, t):
        g = cv2.cvtColor(right[t].astype(np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float32)
        s = cv2.cvtColor(base.astype(np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float32)
        err = {dx: float(np.abs(np.roll(s, -dx, axis=1)[:, 70:-70] - g[:, 70:-70]).mean()) for dx in range(-60, 61)}
        return min(err, key=err.get)
    ts = range(4, N - 4)
    scene_dx = int(np.median([scene_shift(right_off, t) for t in ts]))
    off_dx = int(np.median([rain_shift(right_off, t)[0] for t in ts]))
    on_dx = int(np.median([rain_shift(right_on, t)[0] for t in ts]))
    print(f"   shift left->right eye (+ = toward you): scene {scene_dx} px, rain with Rain off {off_dx} px, with Rain on {on_dx} px", flush=True)
    check("Rain off: the rain is painted on the scene (its shift is the scene's)", abs(off_dx - scene_dx) <= 1)
    check("Rain on: the rain sits nearer than the scene (shifted further)", on_dx >= scene_dx + 3, f"{on_dx} vs {scene_dx}")
    away = np.ones((N, H, W), bool)
    for t in range(N):
        grown = cv2.dilate(masks[t].astype(np.uint8), np.ones((5, 9), np.uint8))
        away[t] = ~(grown.astype(bool) | np.roll(grown, -on_dx, axis=1).astype(bool) | np.roll(grown, -off_dx, axis=1).astype(bool))
    away[:, :, :70] = False; away[:, :, -70:] = False
    diff = np.abs(right_on - right_off).mean(-1)[away[: len(right_on)]]
    check("away from the rain, the scene is the same in both renders", float(np.median(diff)) < 2.0,
          f"median {np.median(diff):.2f}, mean {diff.mean():.2f} (8-bit)")

if off_path and on_path and pop_path:
    P = frames_of(pop_path)
    right_pop = P[:, h2:, w2:].astype(np.float32)
    # the nearest layer gets all of the Pop, the middle one little: the strongest shift
    # (the middle layer's) barely moves -- look at the FARTHEST shift that still lines
    # streaks up (a peak at least a third of the strongest), the nearest layer's
    def nearest_dx(right):
        out = []
        for t in ts:
            _, sc = rain_shift(right, t)
            top = max(sc.values())
            out.append(max(dx for dx, v in sc.items() if v >= top / 3 and sc.get(dx - 1, 0) <= v >= sc.get(dx + 1, 0)))
        return int(np.median(out))
    pop_dx = int(np.median([rain_shift(right_pop, t)[0] for t in ts]))
    near_on, near_pop = nearest_dx(right_on), nearest_dx(right_pop)
    print(f"   rain with Pop 30: strongest {pop_dx} px (Pop 0: {on_dx}); nearest layer {near_pop} px (Pop 0: {near_on})", flush=True)
    check("the rain's Pop brings the nearest rain further out", near_pop >= near_on + 5, f"{near_pop} vs {near_on}")
    away2 = np.ones((N, H, W), bool)
    for t in range(N):
        grown = cv2.dilate(masks[t].astype(np.uint8), np.ones((5, 9), np.uint8))
        away2[t] = ~(grown.astype(bool) | np.roll(grown, -on_dx, axis=1).astype(bool) | np.roll(grown, -pop_dx, axis=1).astype(bool))
    away2[:, :, :70] = False; away2[:, :, -70:] = False
    d2 = np.abs(right_pop - right_on).mean(-1)[away2[: len(right_pop)]]
    check("...and leaves the scene where it was", float(np.median(d2)) < 2.0, f"median {np.median(d2):.2f}")

check("no QML warnings", not warns, " | ".join(warns[:3]))
h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
check("the real last_jobs.json untouched", h0 == h1)
print(f"FAILS {len(fails)} {fails}", flush=True)
# through the fixtures: its temp folders removed, and no DLL teardown crash after a green
# run (bug sweep 2026-10-09, A12)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _fixtures  # noqa: E402
_fixtures.exit_now(1 if fails else 0)
