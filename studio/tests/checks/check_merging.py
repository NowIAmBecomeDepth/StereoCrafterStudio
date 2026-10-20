"""Merging in the real app, offscreen: the page fits its width with every
optional row open and nothing clipped; v1's show/hide rules (grain options,
Split L/R, Delete SBS); the 8-bit/10-bit quick-pick; Reset to Defaults on the
sliders; and a REAL merge started by mouse -- v1's own batch on CPU over a
synthetic finished dual-eye SBS clip (passthrough, no splat partner needed).
Configs and last_jobs.json redirected."""
import os, sys, tempfile, hashlib, time, subprocess, glob
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, QPoint, QPointF, Qt, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from studio import app as A

_src = (Path(__file__).with_name("measure_v2_width.py")).read_text(encoding="utf-8")
exec(_src[_src.index("def eff_visible"):_src.index("def ab_overlaps")])
exec(_src[_src.index("def ab_overlaps"):_src.index("def audit")])
exec(_src[_src.index("def audit"):_src.index("names = {")])

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
def click_item(item):
    c = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(c.x()), int(c.y())))
pump(0.6)
mg = objs["Merging"]
st = mg.property("settings")
stage = find(win.contentItem(), lambda c: c.objectName() == "stage")
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 3), Q_ARG("QVariant", True)); pump(1.2)
page = stage.property("pages").property(3).toQObject()
check("Merging page is the real one", page.metaObject().className().startswith("MergingPage"))
menus = [m.toQObject().property("title") if hasattr(m, "toQObject") else m.property("title")
         for m in [page.property("stageMenus").property(i) for i in range(2)]]
check("v1's stage menus", menus == ["File", "Help"], str(menus))

# ---- everything open: grain on, Full SBS + Split + Delete, import visible
st.setProperty("grain_enable", True); st.setProperty("output_format", "Full SBS (Left-Right)")
st.setProperty("split_lr", True); pump(0.8)
def check_by_key(key):
    return find(page, lambda c: c.property("key") == key and c.metaObject().indexOfProperty("shown") >= 0
                and c.metaObject().indexOfProperty("store") >= 0 and c.metaObject().indexOfProperty("text") >= 0)
check("grain on shows Auto-match", check_by_key("grain_automatch").isVisible())
check("Split + Full SBS shows Delete SBS", check_by_key("delete_sbs_after_split").isVisible())
w = page.property("requiredWidth")
win.setProperty("width", w); pump(0.8)
probs = audit(page) + ab_overlaps(page)
check(f"no clipping / overlaps at {w}", not probs, str(probs[:6]))
content = find(page, lambda c: c.objectName() == "pageContent")
ab = find(page, lambda c: c.objectName() == "actionBar")
gap = ab.mapToItem(page, QPointF(0, 0)).y() - content.mapToItem(page, QPointF(0, content.height())).y()
check("content clears the action bar", gap >= 0, f"gap {gap:.0f}")
st.setProperty("output_format", "Anaglyph (Red/Cyan)"); pump(0.6)
check("non-Full-SBS hides Split + Delete (values kept)", not check_by_key("split_lr").isVisible()
      and not check_by_key("delete_sbs_after_split").isVisible() and st.property("split_lr") is True)
st.setProperty("grain_enable", False); pump(0.6)
check("grain off hides Auto-match", not check_by_key("grain_automatch").isVisible())

# ---- encoding quick-pick
mg.applyEncodingPreset("10-bit"); pump(0.3)
check("10-bit preset detected", mg.property("encodingPreset") == "10-bit", mg.property("encodingPreset"))
crf = st.property("output_crf")
mg.applyEncodingPreset("8-bit"); pump(0.3)
check("8-bit preset, CRF untouched (v1)", mg.property("encodingPreset") == "8-bit" and st.property("output_crf") == crf)
st.setProperty("encoding_quality", "Fastest"); pump(0.3)
check("hand-tuned -> Custom", mg.property("encodingPreset") == "Custom")

# ---- sliders: Reset to Defaults
st.setProperty("shadow_shift", 20.0); st.setProperty("mask_blur_kernel_size", 50.0); pump(0.2)
mg.resetMaskParameters(); pump(0.2)
check("Reset to Defaults restores the eight", st.property("shadow_shift") == 5.0 and st.property("mask_blur_kernel_size") == 5.0)

# ---- a real merge, started by mouse (CPU, dual-eye SBS passthrough)
inp, out = tmp / "inpainted", tmp / "merged"; inp.mkdir()
clip = inp / "shotA_640_inpainted_dual_sbs.mp4"
subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=24:duration=1",
                "-filter_complex", "[0]split[a][b];[a][b]hstack", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)], check=True)
for k, v in {"inpainted_folder": str(inp), "original_folder": str(tmp / "orig"), "mask_folder": str(tmp / "mask"),
             "output_folder": str(out), "encoding_encoder": "Force CPU", "codec": "H.264", "resume": True,
             "output_format": "Full SBS (Left-Right)", "split_lr": False}.items():
    st.setProperty(k, v)
pump(0.3)
start = find(page, lambda c: c.objectName() == "mgStart")
click_item(start); pump(0.2)
end = time.time() + 120
while (mg.property("busy") or mg.property("loading") or not out.exists()) and time.time() < end:
    pump(0.1)
pump(1.5)
made = [os.path.basename(p) for p in glob.glob(str(out / "*.mp4")) + glob.glob(str(out / "*.mov"))]
# .mov when the preset in force splits full-res output to DNxHR (the 8-bit one above does).
check("Start Blending by mouse merged the clip", len(made) == 1 and made[0].startswith("shotA_640_merged_full_sbs."), str(made))
check("v1's finish line", mg.property("status") == "Processing completed.", mg.property("status"))
check("not busy afterwards (cleanup worker ended)", not mg.is_busy())
check("Resume moved the clip to finished/", (inp / "finished" / clip.name).exists())
check("handoff recorded (temp)", '"merged_out"' in (tmp / "last_jobs.json").read_text())

# ---- nothing left: v1's early return must not leave the cleanup worker running
mg_toasts = []
mg.toast.connect(lambda k, t, m: mg_toasts.append((k, t, m)))
click_item(start); pump(0.3)
end = time.time() + 20
while mg.property("busy") and time.time() < end:
    pump(0.1)
pump(0.8)   # the prompt's enter transition, before `opened` reads true
prompt = page.property("prompt")
# The clip moved to finished/, so the input is empty: v1's first early return,
# said once by the finish toast -- not a box and then "completed" (audit #3).
# (the toast's last line is the run's duration -- "Ran for 0.0s." -- since the finish verdict says it)
check("nothing left: v1's message, as a warning toast",
      [(k, t, m.splitlines()[0]) for k, t, m in mg_toasts[-1:]]
      == [("warning", "Merging: nothing processed", "No .mp4 files found in the inpainted video folder.")],
      str(mg_toasts))
check("...and no box as well", not prompt.property("opened"), prompt.property("message"))
pump(1.5)
check("...and the stage is idle again (v1 stayed busy here)", not mg.is_busy())

mg.shutdown()
studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
import _fixtures
_fixtures.exit_now(0 if not fails and _lj else 1)
