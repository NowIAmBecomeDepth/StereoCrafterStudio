"""Merging phase 4 in the real app, offscreen, over the synthetic ``merge``
fixture: File > Save Preview Frame / as SBS (v1's suggested name, the file
written at the chosen path); the Grain Match Preview (refused with nothing to
tune on, then opened by mouse: pictures, sigma readout, the suggestion scan,
a scrub, a slider re-render, Auto-Match, the sigma slider driving Amount,
Save Grain to Sidecar, close); Process Current Clip (only the clip on
screen merged); Move to Finish (v1's question first, then the clip's files
moved and the list one shorter). Configs and last_jobs.json redirected."""
import os, sys, tempfile, hashlib, time, json
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, str(Path(__file__).parent))
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
import _fixtures
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, QPoint, QPointF, Qt, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtCore import QObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PIL import Image
from studio import app as A

root = _fixtures.copy("merge", tmp / "fx")
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
    end = time.time() + sec   # not QTest.qWait: it holds the GIL, starving the worker
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.005)
def until(cond, sec=30):
    end = time.time() + sec
    while not cond() and time.time() < end:
        pump(0.05)
    return cond()
def find(it, pred):
    for c in it.childItems():
        if pred(c): return c
        r = find(c, pred)
        if r is not None: return r
def click_in(window, item):
    # From elsewhere: a cursor left resting on a button shows its hover tip,
    # and a click then only dismisses the tip (HoverTip's click-to-dismiss).
    QTest.mouseMove(window, QPoint(5, 5)); pump(0.1)
    c = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, QPoint(int(c.x()), int(c.y())))

pump(0.6)
mg = objs["Merging"]; st = mg.property("settings"); pv = mg.property("previewer")
stage = find(win.contentItem(), lambda c: c.objectName() == "stage")
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 3), Q_ARG("QVariant", True)); pump(1.2)
page = stage.property("pages").property(3).toQObject()
out = tmp / "merged"
for k, v in {"inpainted_folder": str(root / "inpainted"), "original_folder": str(root / "orig"),
             "mask_folder": str(root / "mask"), "output_folder": str(out), "encoding_encoder": "Force CPU",
             "codec": "H.264", "use_gpu": False, "preview_source": "Blended Image", "preview_size": "100%",
             "grain_enable": False, "grain_automatch": False, "resume": False, "output_format": "Full SBS (Left-Right)",
             "split_lr": False, "fill_brightness": 0.0, "grain_amount": 0.035}.items():
    st.setProperty(k, v)
pump(0.3)
prompt = page.property("prompt")
def prompt_text():
    return prompt.property("message") if prompt.property("opened") else ""
def dismiss(code=1):
    QMetaObject.invokeMethod(prompt, "finish", Q_ARG("QVariant", code)); pump(0.8)

# ---- Grain Match with nothing to tune on: v1's refusal
st.setProperty("grain_enable", True); pump(0.4)
gbtn = find(page, lambda c: c.objectName() == "grainPreviewButton")
click_in(win, gbtn)
check("Grain Preview refuses with no clip (v1's text)", until(lambda: "No clip with a usable fill mask" in prompt_text(), 15),
      prompt_text()[:60])
dismiss()

# ---- load the preview
pv.load()
check("preview loads", until(lambda: pv.property("loaded") and not pv.property("loading")))

# ---- File > Save Preview Frame / as SBS. The page's Save dialog opens on the
# suggested name; it is closed here and its answer given straight to saveTo
# (a modal dialog left open would swallow every later click).
got = {}
save_dlg = page.findChild(QObject, "savePreviewDlg")
def close_save_dialog():
    until(lambda: save_dlg.property("visible"), 5)
    QMetaObject.invokeMethod(save_dlg, "reject"); pump(0.3)
pv.saveSuggested.connect(lambda sbs, name: got.__setitem__(bool(sbs), name))
pv.suggestSaveName(False)
check("v1's suggested name", until(lambda: False in got, 10) and got[False] == "mA_320_inpainted_right_eye_frame_00000.png",
      str(got.get(False)))
_sel = save_dlg.property("selectedFile")
_sel = _sel.toString() if hasattr(_sel, "toString") else str(_sel)
check("the Save dialog opens on it", until(lambda: save_dlg.property("visible"), 5) and _sel.endswith(got.get(False, "?")),
      f"visible={save_dlg.property('visible')} selectedFile={_sel}")
close_save_dialog()
pv.saveTo(False, str(tmp / "frame.png"))
check("Save Preview Frame writes the frame", until(lambda: (tmp / "frame.png").exists(), 10)
      and Image.open(tmp / "frame.png").size == (320, 180))
pv.suggestSaveName(True)
check("SBS name", until(lambda: True in got, 10) and got[True].endswith("_frame_00000_SBS.png"), str(got.get(True)))
close_save_dialog()
pv.saveTo(True, str(tmp / "sbs.png"))
check("Save Preview as SBS writes both eyes", until(lambda: (tmp / "sbs.png").exists(), 10)
      and Image.open(tmp / "sbs.png").size == (640, 180))

# ---- the Grain Match Preview
states = []
pv.grainChanged.connect(lambda s: states.append(dict(s)))
click_in(win, gbtn)
check("Grain Preview opens", until(lambda: states and states[-1].get("open"), 30))
gwin = [w for w in qapp.topLevelWindows() if w.objectName() == "grainWin"]
gwin = gwin[0] if gwin else None
check("...as its own window", gwin is not None and gwin.isVisible())
s = states[-1]
check("pictures served", all(pv.provider._images.get("grain_" + n) is not None for n in ("full", "master", "before", "after")))
# v1: a 186 px crop at 1.5x (CROP_DISP 280 / 1.5, even), NEAREST-magnified.
check("crop panes at v1's magnified size", pv.provider._images["grain_after"].width() == 279,
      str(pv.provider._images["grain_after"].width()))
check("sigma readout", "grain \u03c3" in s.get("sigmaText", ""), s.get("sigmaText", "")[:90])
check("the scan suggests frames", until(lambda: states[-1].get("scanning") is False, 30), str(states[-1].get("suggestions")))
sug = states[-1].get("suggestions") or []
check("...with their fill coverage", len(sug) >= 1 and all(0 <= x["coverage"] <= 1 for x in sug), str(sug))
n0 = len(states)
pv.grainOp("seek", [10])
check("scrub to frame 10", until(lambda: len(states) > n0 and states[-1].get("frame") == 10, 20))
n0 = len(states)
g = pv._grain
before0 = g._before
decoded0 = dict(g.ctx["inp_reader"]._cache)
st.setProperty("grain_size", 2.0)
check("a grain slider re-renders the window", until(lambda: len(states) > n0, 20))
check("...reusing the ungrained render and the decoded frame", g._before is before0 and before0 is not None
      and all(g.ctx["inp_reader"]._cache.get(k) is v for k, v in decoded0.items()) and decoded0)
import torch
check("...on the GPU when there is one", before0[1]["blended"].device.type == ("cuda" if torch.cuda.is_available() else "cpu"),
      str(before0[1]["blended"].device))
# live while held: each step renders at once (no 220 ms settle), newest wins
pv.setDragging(True)
n0 = len(states)
for v in (1.2, 1.4, 1.6, 1.8):
    st.setProperty("grain_size", v); pump(0.01)
check("a held slider renders with no settle delay", pv._grain_timer.interval() == 0)
check("...and renders while still held", until(lambda: len(states) > n0, 10))
until(lambda: abs(float(pv._engine.grain_size_var.get()) - 1.8) < 1e-9 and not pv._jobs, 10); pump(0.5)
pv.setDragging(False); pump(0.3)
st.setProperty("grain_size", 2.0); pump(0.01)
check("let go: back to v1's 220 ms settle", pv._grain_timer.interval() == 220)
until(lambda: not pv._grain_timer.isActive() and not pv._jobs, 10); pump(0.5)
pv.grainOp("auto_match", [])
check("Auto-Match reports", until(lambda: bool(states[-1].get("automatchStatus")), 20), states[-1].get("automatchStatus"))
amt0 = st.property("grain_amount")
pv.grainOp("sigma", [0.02])
check("the sigma slider drives Amount", until(lambda: abs(st.property("grain_amount") - amt0) > 1e-6, 20),
      f"{amt0} -> {st.property('grain_amount')}")
until(lambda: not pv._jobs and not pv._grain_timer.isActive(), 10); pump(0.5)
# a render that left before the slider was let go must not throw it back
sig_row = gwin.findChild(QObject, "grainSigmaSlider") if gwin is not None else None
sent = pv.grainOp("sigma", [0.021])
gwin.setProperty("sigmaSent", sent)   # what the sigma timer records
until(lambda: (states[-1].get("seq") or 0) >= sent and not pv._jobs, 20); pump(0.3)
v_now = sig_row.property("value") if sig_row is not None else None
stale = dict(states[-1], seq=sent - 1, sigmaTarget=0.001)
pv.grainChanged.emit(stale); pump(0.2)
check("a stale render leaves the Amount slider alone", sig_row is not None and abs(sig_row.property("value") - v_now) < 1e-9
      and abs(v_now - 0.021) < 1e-4, f"{v_now} -> {sig_row.property('value') if sig_row else None}")
pv.grainChanged.emit(dict(states[-1], seq=sent, sigmaTarget=0.015)); pump(0.2)
check("...and a current one moves it", sig_row is not None and abs(sig_row.property("value") - 0.015) < 1e-9,
      str(sig_row.property("value") if sig_row else None))
pv.grainChanged.emit(dict(states[-1])); pump(0.2)
msgs = []
pv.grainMessage.connect(lambda k, t: msgs.append((k, t)))
pv.grainOp("save_sidecar", [])
check("Save Grain to Sidecar", until(lambda: bool(msgs), 20) and msgs[-1][0] in ("info", "warning"), str(msgs[-1:])[:160])
scs = list((root / "inpainted").glob("*.fssidecar"))
data = json.loads(scs[0].read_text()) if scs else {}
check("...wrote the nine grain keys", abs(float(data.get("grain_size", 0)) - 2.0) < 1e-6 and "fill_contrast" in data, str(scs))
if gwin is not None:
    gwin.close()
check("Close closes", until(lambda: states[-1].get("open") is False, 10))
for p in scs:
    p.unlink()   # so the merges below run on the global settings
st.setProperty("grain_enable", False); pump(0.3)

# ---- Process Current Clip: only the clip on screen
click_in(win, find(page, lambda c: c.objectName() == "mgProcessCurrent"))
check("Process Current Clip merges", until(lambda: not mg.property("busy") and out.exists() and any(out.iterdir()), 180)
      and until(lambda: not mg.property("busy"), 60))
made = sorted(p.name for p in out.iterdir() if p.suffix in (".mp4", ".mov"))
check("...only the clip on screen", len(made) == 1 and made[0].startswith("mA_"), str(made))
check("the preview comes back", until(lambda: pv.property("loaded") and not pv.property("loading"), 30))

# ---- Move to Finish: v1's question, then the move
asked = []
pv.moveAsked.connect(lambda t: asked.append(t))
click_in(win, find(page, lambda c: c.objectName() == "mgMoveToFinish"))
check("v1's question first", until(lambda: bool(asked), 20) and "to finished folder?" in asked[0], asked[:1])
check("...with the preview still showing", until(lambda: pv.property("loaded"), 10))
check("...and nothing moved yet", (root / "inpainted" / "mA_320_inpainted_right_eye.mp4").exists())
check("the page asks", until(lambda: "to finished folder?" in prompt_text(), 5))
dismiss(1)   # Move
check("moved to finished/", until(lambda: (root / "inpainted" / "finished" / "mA_320_inpainted_right_eye.mp4").exists(), 20))
check("v1's result message", until(lambda: "Successfully moved" in prompt_text(), 20), prompt_text()[:60])
dismiss()
check("the list is one shorter, on the next clip", until(lambda: pv.property("videoCount") == 1, 20)
      and mg.property("clipName") == "mB_320_inpainted_right_eye.mp4", mg.property("clipName"))

mg.shutdown()
studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
