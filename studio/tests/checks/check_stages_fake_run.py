"""Headless check of Studio v2: DepthCrafter + Inpainting, and the no-scroll rule.

No screenshots; reads object state only. Settings AND last_jobs.json are
redirected to a temp folder, and the real files are compared before/after.
Every step is a check() that sets the exit code (audit 2026-09-28 #31: the
Inpainting settings rules and the whole fake run were only printed).
"""
import os
import re
import sys
import tempfile
import threading
import time
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
REPO = str(__import__("pathlib").Path(__file__).resolve().parents[3])
sys.path.insert(0, REPO)

from PySide6.QtCore import QEventLoop, QElapsedTimer, QMetaObject, QPointF, Q_ARG, Qt, qInstallMessageHandler  # noqa: E402
from PySide6.QtGui import QFont, QFontMetrics, QGuiApplication  # noqa: E402
from PySide6.QtQuickControls2 import QQuickStyle  # noqa: E402

messages = []
qInstallMessageHandler(lambda mode, ctx, msg: messages.append(msg))

from studio import settings_store  # noqa: E402

tmp = tempfile.mkdtemp(prefix="v2check_")
settings_store.CONFIG_DIR = type(settings_store.CONFIG_DIR)(tmp)

from studio import app as v2app  # noqa: E402
import core.ui.job_handoff as _jh  # noqa: E402

_jh._RECORD_PATH = os.path.join(tmp, "last_jobs.json")
_real = os.path.join(REPO, "last_jobs.json")
_real_before = open(_real, "rb").read() if os.path.exists(_real) else None

# A stand-in for the generated engine (the real one needs torch), so the
# controller's thread / hooks / finish path runs for real.
fake_engine_mod = types.ModuleType("studio.stages.inpainting.engine")


class FakeEngine:
    def __init__(self, run_settings, run_paths, app_config, hooks, stop_event):
        self.rs, self.paths, self.cfg, self.hooks, self.stop = run_settings, run_paths, app_config, hooks, stop_event
        self._job_started = None
        FakeEngine.last = self

    def _configure_logging(self):
        pass

    def run_batch_process(self, input_folder, output_folder, **kw):
        FakeEngine.kwargs = kw
        self.hooks.progress(0, 2)
        for i in range(2):
            self.hooks.status(f"Processing video {i + 1} of 2")
            self.hooks.clip(f"clip{i}.mp4", "1920x1080", "240", "5", "0.0")
            time.sleep(0.05)
            self.hooks.progress(i + 1, 2)
        self.hooks.message("info", "Info", "fake engine done")
        self.hooks.status("Processing completed in 0:00:01.")
        from studio.outcome import Outcome
        self.hooks.finished(False, Outcome(done=2))


fake_engine_mod.InpaintingEngine = FakeEngine
sys.modules["studio.stages.inpainting.engine"] = fake_engine_mod

QQuickStyle.setStyle("Basic")
qapp = QGuiApplication(sys.argv)
engine, objs = v2app.build(qapp)
roots = engine.rootObjects()
print("roots:", len(roots))
if not roots:
    for m in messages:
        print("MSG", m)
    sys.exit(1)
win = roots[0]
dc, ip = objs["DepthCrafter"], objs["Inpainting"]


fails = []


def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok:
        fails.append(label)


def pump(ms):
    t = QElapsedTimer()
    t.start()
    while t.elapsed() < ms:
        qapp.processEvents(QEventLoop.AllEvents, 20)
        time.sleep(0.005)


def qml_write(store, key, value):
    store.insert(key, value)
    store.valueChanged.emit(key, value)
    pump(30)


pump(900)
stage = win.findChild(object, "stage")
print("seeded:", sorted(os.listdir(tmp)))
print("  inpainting input:", ip.property("settings").value("input_folder"))
print("  inpainting chunk/overlap/bias:", [ip.property("settings").value(k) for k in ("frames_chunk", "frame_overlap", "original_input_blend_strength")])


def show(i):
    QMetaObject.invokeMethod(stage, "show", Qt.DirectConnection, Q_ARG("QVariant", i), Q_ARG("QVariant", False))
    pump(700)


def page_fit(page_index, label):
    page = stage.property("pages").property(page_index).toQObject()
    content = [c for c in page.findChildren(object) if c.objectName() == "pageContent"][0]
    bar = [c for c in page.findChildren(object) if c.objectName() == "actionBar"][0]
    c_bottom = content.mapToItem(stage, QPointF(0, content.property("implicitHeight"))).y()
    b_top = bar.mapToItem(stage, QPointF(0, 0)).y()
    need = page.property("requiredHeight")
    print(f"  {label}: content bottom {c_bottom:.0f}, action bar top {b_top:.0f}, gap {b_top - c_bottom:.0f}px "
          f"| page needs {need:.0f} of {stage.property('height'):.0f} | window min {win.property('minimumHeight')}")
    return b_top - c_bottom


# --- no-scroll: DepthCrafter, everything expanded -----------------------------------
show(0)
st = dc.property("settings")
worst_dc = {"process_as_segments_var": True, "stabilize_depth_var": True, "full_percentile_norm_var": True,
            "enable_dual_output_robust_norm": True}
print("DepthCrafter fit at default size:")
gap_min = page_fit(0, "defaults")
for k, v in worst_dc.items():
    qml_write(st, k, v)
pump(900)
gap_worst_dc = page_fit(0, "all rows expanded")

# --- no-scroll: Inpainting, everything expanded ---------------------------------------
show(2)
ist = ip.property("settings")
print("Inpainting fit at default size:")
# the tallest Inpainting page: the blend rows went with Post-Inpainting Blend (2026-10-11)
gap_worst_ip = page_fit(2, "defaults")

# At the minimum size the window allows, the worst case must still fit.
win.setProperty("height", win.property("minimumHeight"))
pump(500)
print("At the window's minimum height (%s):" % win.property("height"))
gap_min_ip = page_fit(2, "inpainting")
show(0)
gap_min_dc = page_fit(0, "depthcrafter, all expanded")
win.setProperty("height", 980)
pump(300)

# --- v1 arrangement: menus per stage, buttons clear of the trailing one ---------------
# (the menus are Settings sections now: the stage's own come from its stageMenus)
def layout_report(i, label):
    show(i)
    page = stage.property("pages").property(i).toQObject()
    js = page.property("stageMenus")
    if hasattr(js, "toVariant"):
        titles = [m.property("title") for m in js.toVariant()]
    elif isinstance(js, list):
        titles = [m.property("title") for m in js]
    else:
        titles = [js.property(k).toQObject().property("title") for k in range(js.property("length").toInt())]
    btns = [c for c in page.findChildren(object) if c.objectName() == "abButtons"][0]
    trail = [c for c in page.findChildren(object) if c.objectName() == "abTrailing"][0]
    b_right = btns.mapToItem(stage, QPointF(btns.property("width"), 0)).x()
    t_left = trail.mapToItem(stage, QPointF(0, 0)).x()
    print(f"  {label}: menus {titles} | centre buttons end {b_right:.0f}, trailing starts {t_left:.0f} "
          f"({'clear' if b_right < t_left else 'OVERLAP'})")
    return b_right < t_left


print("window:", win.property("width"), "x", win.property("height"))
clear = [layout_report(0, "DepthCrafter"), layout_report(2, "Inpainting")]
check("no page scrolls: every worst case fits, at the minimum height too",
      min(gap_worst_dc, gap_worst_ip, gap_min_ip, gap_min_dc) >= 0,
      str([gap_worst_dc, gap_worst_ip, gap_min_ip, gap_min_dc]))
check("the action bar's centre buttons clear its trailing ones", all(clear))

# --- Inpainting settings rules -------------------------------------------------------------
chunk_before = ist.value("frames_chunk")
qml_write(ist, "frames_chunk", "abc")
check("Inpainting: a bad number is refused, the old value kept",
      ist.value("frames_chunk") == chunk_before, repr(ist.value("frames_chunk")))
qml_write(ist, "frames_chunk", "21")
check("...a good one stored as v1's string", ist.value("frames_chunk") == "21", repr(ist.value("frames_chunk")))
ip.applyEncodingPreset("10-bit")
pump(100)
check("the 10-bit preset sets its codec and pixel format",
      ip.property("encodingPreset") == "10-bit" and ist.value("codec") == "H.265" and "10" in str(ist.value("pix_fmt")),
      f"{ip.property('encodingPreset')} {ist.value('codec')} {ist.value('pix_fmt')}")
qml_write(ist, "codec", "H.264")
check("...and editing the codec makes it Custom", ip.property("encodingPreset") == "Custom",
      ip.property("encodingPreset"))

# --- Inpainting run through the controller with the fake engine ------------------------------
lo = tempfile.mkdtemp(prefix="lo_")
out = tempfile.mkdtemp(prefix="out_")
open(os.path.join(lo, "a-0001_640_splatted4.mp4"), "wb").write(b"x")
qml_write(ist, "input_folder", lo)
qml_write(ist, "output_folder", out)
qml_write(ist, "hires_blend_folder", lo)
toasts = []
ip.toast.connect(lambda k, t, m: toasts.append((k, t)))
busy_seen = []
ip.stateChanged.connect(lambda: busy_seen.append(ip.property("busy")))
FakeEngine.kwargs = None
ip.start()
pump(1400)
check("Inpainting: Start runs the batch (busy, then idle)", True in busy_seen and not ip.property("busy"),
      str(busy_seen[:6]))
check("...status, progress 2 / 2", "completed" in str(ip.property("status"))
      and (ip.property("progressDone"), ip.property("progressTotal")) == (2, 2),
      f"{ip.property('status')} {ip.property('progressDone')}/{ip.property('progressTotal')}")
info = [ip.property(p) for p in ("clipName", "clipResolution", "clipFrames", "clipOverlap", "clipBias")]
check("...Clip Info shows the engine's last clip", info == ["clip1.mp4", "1920x1080", "240", "5", "0.0"], str(info))
kw = FakeEngine.kwargs or {}
check("...the engine gets the run's numbers", kw.get("frames_chunk") == 21 and "num_inference_steps" in kw,
      str({k: kw.get(k) for k in ("frames_chunk", "num_inference_steps")}))
rs = getattr(FakeEngine, "last", None)
check("...and v1's setting names", rs is not None and rs.rs.get("frames_chunk_var") == "21",
      str(rs and {k: rs.rs.get(k) for k in ("frames_chunk_var", "enable_color_transfer", "debug_mode_var")}))
check("...the toast says it finished", ("success", "Inpainting finished") in toasts, str(toasts))
import json  # noqa: E402
rec = json.load(open(_jh._RECORD_PATH)).get("inpainting", {}) if os.path.exists(_jh._RECORD_PATH) else {}
check("...the handoff is recorded (temp)", all(rec.get(k) for k in ("inpainted_out", "lowres", "hires")),
      str({k: bool(rec.get(k)) for k in ("inpainted_out", "lowres", "hires")}))

# Validation failure path
qml_write(ist, "frames_chunk", "0")
toasts.clear()
FakeEngine.kwargs = None
ip.start()
pump(300)
check("Chunk 0 is refused at Start", not ip.property("busy") and toasts and toasts[0][0] == "error"
      and FakeEngine.kwargs is None, str(toasts))

# --- glyphs ----------------------------------------------------------------------------
fonts = objs["Shell"].property("fonts")
fm = QFontMetrics(QFont(fonts["icons"]))
src = open(os.path.join(REPO, "studio", "qml", "Studio", "Theme.qml"), encoding="utf-8").read()
missing = [(n, c) for n, c in re.findall(r'(\w+): "\\u([0-9A-Fa-f]{4})"', src) if not fm.inFontUcs4(int(c, 16))]
check("every icon glyph is in the icon font", not missing, str(missing))

print("config written only to temp:", sorted(os.listdir(tmp)))
check("settings went to the temp folder", "inpainting.json" in os.listdir(tmp))
print("---- QML/Qt messages ----")
seen = set()
for m in messages:
    if m not in seen and "FFmpeg" not in m:
        seen.add(m)
        print(" ", m)
qml_errors = [m for m in seen if ".qml" in m and ("Error" in m or "Unable" in m or "undefined" in m)]
check("no QML errors", not qml_errors, str(qml_errors[:3]))
dc.shutdown()
ip.shutdown()
_real_ok = (open(_real, "rb").read() if os.path.exists(_real) else None) == _real_before
print("real last_jobs.json unchanged:", _real_ok)
if not _real_ok:
    fails.append("real last_jobs.json changed")
print("FAILED:" if fails else "ALL OK", fails if fails else "")
import _fixtures
_fixtures.exit_now(1 if fails else 0)
