"""Inpainting V2's FP8 Math switch (user, 2026-10-31; stages/inpainting_v2/fp8_matmul.py) in the real app,
offscreen: off to begin with; offered under MMGP streaming only; it and Torch Compile hide each other; Start
switches the FP8 path on for the run only when it's ticked under MMGP without Torch Compile, and its chunk
checkpoints say so; nothing clipped on the page. Model calls faked; configs and the models folder redirected."""
import os, sys, tempfile, hashlib, time
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")     # nothing here needs the GPU
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, str(Path(__file__).parent))
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
tmp = Path(tempfile.mkdtemp())
os.environ["STEREOCRAFTER_WEIGHTS"] = str(tmp / "weights")
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from studio.stages.inpainting_v2 import weights as W, fp8_matmul
from core import weights_paths
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, QPoint, QPointF, Qt, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from studio import app as A

_src = (Path(__file__).with_name("measure_v2_width.py")).read_text(encoding="utf-8")
exec(_src[_src.index("def eff_visible"):_src.index("def ab_overlaps")])
exec(_src[_src.index("def audit"):_src.index("names = {")])

# every V2 weight file there (empty stand-ins), so Start gets past "download them first"
for part in W.REQUIRED:
    for path, _n in part.files:
        f = weights_paths.root() / part.folder / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"1")
weights_paths.refresh()
fp8_matmul.supported = lambda: True          # the GPU is hidden here: an RTX 40 / 50 stood in for

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
objs["Shell"].setProperty("uiScale", "1.0")
win.show()
fails = []
def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok: fails.append(label)
def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.005)
def find(it, pred):
    for c in it.childItems():
        if pred(c): return c
        r = find(c, pred)
        if r is not None: return r
pump(0.6)
wan = objs["InpaintingV2"]
st = wan.property("settings")
stage = find(win.contentItem(), lambda c: c.objectName() == "stage")
pages = stage.property("pages")
for i in range(6):
    p = pages.property(i).toQObject()
    if p is not None and p.metaObject().className().startswith("InpaintingPage"):
        page, idx = p, i
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", idx), Q_ARG("QVariant", True)); pump(1.0)
page.setProperty("engine", 1); pump(1.0)
v2 = find(page, lambda c: c.objectName() == "inpaintingV2Page")
fp8 = find(v2, lambda c: c.objectName() == "v2Fp8")
comp = find(v2, lambda c: c.objectName() == "v2Compile")

st.setProperty("offload_mode", "MMGP streaming (int8)"); pump(0.5)
check("FP8 Math on V2's page under MMGP streaming, off to begin with",
      fp8 is not None and fp8.isVisible() and fp8.property("text") == "FP8 Math" and st.property("use_fp8_matmul") is False)
check("...Torch Compile beside it", comp is not None and comp.isVisible())
box = find(fp8, lambda c: c.metaObject().indexOfProperty("checked") >= 0 and c.metaObject().indexOfSignal("toggled()") >= 0)
c = (box or fp8).mapToScene(QPointF((box or fp8).width() / 2 if box else 12, (box or fp8).height() / 2))
QTest.mouseMove(win, c.toPoint()); pump(0.2)
QTest.mouseClick(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, c.toPoint()); pump(0.4)
check("...a click ticks it", st.property("use_fp8_matmul") is True, st.property("use_fp8_matmul"))
check("...and Torch Compile hides", not comp.isVisible())
probs = audit(v2)
check("nothing clipped on V2 with it", not probs, str(probs[:5]))
st.setProperty("use_fp8_matmul", False); st.setProperty("use_compile", True); pump(0.4)
check("Torch Compile ticked: FP8 Math hides", not fp8.isVisible() and comp.isVisible())
st.setProperty("use_compile", False); pump(0.3)
for mode in ("None", "Group offload", "FP8 resident"):
    st.setProperty("offload_mode", mode); pump(0.3)
    check(f"{mode}: no FP8 Math, Torch Compile shown", not fp8.isVisible() and comp.isVisible())
st.setProperty("offload_mode", "MMGP streaming (int8)"); pump(0.3)

# ---- Start: the FP8 path on for the run, as ticked
import studio.stages.inpainting_v2.engine as E
from studio.stages.inpainting_v2 import resume as R
inp, out = tmp / "in", tmp / "out"; inp.mkdir()
(inp / "x_splatted2.mp4").write_bytes(b"x")
class FakeEngine:
    text_encoder = object(); _prompt_cache = {}; vae_scale_factor_temporal = 4
    def release(self): pass
seen = []
def fake_run(engine, video, out_dir, **kw):
    sig = R.signature(video, frames_chunk=81, frames_overlap=10, tile_num=2, tile_overlap=64, inference_steps=5,
                      seed=0, prompt="", is_dual_input=True, total_frames=81, model={})
    seen.append((fp8_matmul.enabled(), sig.get("fp8_matmul")))
    kw["progress_cb"]("denoise", 1, 1, "done")
E.load_models = lambda *a, **k: FakeEngine()
E.run_inpaint = fake_run
st.setProperty("input_folder", str(inp).replace("\\", "/")); st.setProperty("output_folder", str(out).replace("\\", "/"))
st.setProperty("move_to_finished", False)
def run(**vals):
    for k, v in vals.items():
        st.setProperty(k, v)
    n = len(seen)
    wan.start(); pump(0.3)
    end = time.time() + 60
    while (wan.property("busy") or wan.property("loading") or len(seen) == n) and time.time() < end:
        pump(0.05)
    pump(0.3)
    return seen[-1] if len(seen) > n else None
check("Start with FP8 Math ticked: the run's multiplies in FP8, its checkpoints marked",
      run(use_fp8_matmul=True) == (True, True), str(seen[-1:]))
check("...unticked: the normal way, checkpoints as before", run(use_fp8_matmul=False) == (False, None), str(seen[-1:]))
check("...ticked with Torch Compile too (an older file): compiled, no FP8",
      run(use_fp8_matmul=True, use_compile=True) == (False, None), str(seen[-1:]))
check("...ticked under another offload mode: no FP8",
      run(use_compile=False, offload_mode="Group offload") == (False, None), str(seen[-1:]))
fp8_matmul.uninstall()

check("no QML warnings", not warns, str(warns[:3]))
h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
check("the real last_jobs.json untouched", h0 == h1)
print("PASS" if not fails else f"FAIL ({len(fails)})", fails)
import _fixtures
_fixtures.exit_now(0 if not fails and h0 == h1 else 1)
