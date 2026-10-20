"""Beginner Mode (the switch by the Settings gear; was Studio > Use Beginner Mode) (v1 unified_gui/beginner_mode.py) in the real app,
offscreen: the menu switches it; the title says so; v1's rules hide their
settings on every stage (and bring them back), rename Splatting's labels, pin
the forced settings and keep the values they replaced (restored when it goes
off, persisted in studio.json), hide Inpainting's V2 sub-tab, make Merging's
Auto-match the grain switch; hover text turns plain; refused while a stage
runs; the pages still fit. Configs and last_jobs.json redirected."""
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
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from studio import app as A

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
def by_text(page, text):
    return find(page, lambda c: c.property("label") == text or c.property("text") == text)

pump(0.6)
B = objs["Beginner"]; dc, sp, ip, mg = objs["DepthCrafter"], objs["Splatting"], objs["Inpainting"], objs["Merging"]
stage = find(win.contentItem(), lambda c: c.objectName() == "stage")
pages = [stage.property("pages").property(i).toQObject() for i in range(5)]
def show(i):
    QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", i), Q_ARG("QVariant", True)); pump(0.6)
show(0)
check("starts off", not B.property("on") and "Beginner" not in win.title())

# originals the mode will pin, set to the OTHER value first
dc.settings.setProperty("latent_checkpoint_var", False)
dc.settings.setProperty("output_preset_var", "medium")
dc.settings.setProperty("target_fps", 30.0)
sp.settings.setProperty("enable_low_res", False)
ip.settings.setProperty("enable_color_transfer", False)
mg.settings.setProperty("grain_enable", True); mg.settings.setProperty("grain_automatch", False)
pump(0.3)
h_dc_off = win.property("minimumHeight")
# every stage's height need with the mode off (Beginner Mode only ever hides)
heights_off = {}
for i in range(5):
    show(i); heights_off[i] = win.property("minimumHeight")
show(0)
guidance = by_text(pages[0], "Guidance Scale")
check("DC Guidance Scale shows while off", guidance is not None and guidance.property("shown") is not False)

# ---- on, through the menu item
B.setEnabled(True); pump(0.8)
check("on", B.property("on"))
check("the title says Beginner Mode", win.title().endswith("(Beginner Mode)"), win.title())
check("DC: Guidance Scale hidden", guidance.property("shown") is False)
check("DC: the window gets shorter", win.property("minimumHeight") < h_dc_off, f"{h_dc_off} -> {win.property('minimumHeight')}")
check("DC: Encoder preset pinned to veryslow", dc.settings.property("output_preset_var") == "veryslow")
fps_row = by_text(pages[0], "Target FPS (-1 Original)")
# Torch Compile took Single Clip ID's cell (2026-10-11): Pre Thresh hides on its own now
pre = by_text(pages[2], "Inpaint Pre Thresh")
tc = by_text(pages[2], "Torch Compile")
check("Inpainting: Inpaint Pre Thresh hidden, Torch Compile still there",
      pre is not None and pre.property("shown") is False and tc is not None and tc.property("shown") is not False)
check("DC: Target FPS hidden and held at -1", fps_row is not None and fps_row.property("shown") is False
      and float(dc.settings.property("target_fps")) == -1.0, str(dc.settings.property("target_fps")))
# Save Checkpoint stays on the page in the mode, as the user set it (2026-10-11)
ckpt = by_text(pages[0], "Save Checkpoint")
check("DC: Save Checkpoint shown and not pinned", ckpt is not None and ckpt.property("shown") is not False
      and dc.settings.property("latent_checkpoint_var") is False)
ckpt_tech = dc.property("help").get("latent_checkpoint", "")
check("DC: Save Checkpoint's hover text is the plain one",
      ckpt_tech and B.tip(ckpt_tech).startswith("Saves the AI's slow work"), B.tip(ckpt_tech)[:50])
check("Splatting: Low Res pinned on, Dilate X renamed", sp.settings.property("enable_low_res") is True
      and by_text(pages[1], "Expand X:") is not None and by_text(pages[1], "Dilate X:") is None)
check("Splatting: Dev Tools hidden", not find(pages[1], lambda c: c.property("title") == "Dev Tools").isVisible())
check("Inpainting: Color Transfer pinned on", ip.settings.property("enable_color_transfer") is True)
sw = find(pages[2], lambda c: c.property("model") is not None and c.metaObject().className().startswith("StageTabBar"))
check("Inpainting: the V2 sub-tab is gone", sw is not None and len(sw.property("model").toVariant() if hasattr(sw.property("model"), "toVariant") else sw.property("model")) == 1)
check("Merging: grain follows Auto-match (off -> no grain)", mg.settings.property("grain_enable") is False)
mg.settings.setProperty("grain_automatch", True); pump(0.3)
check("...Auto-match on -> grain on", mg.settings.property("grain_enable") is True)
check("Merging: Shadow Shift hidden", not by_text(pages[3], "Shadow Shift:").isVisible())
saved = json.loads((tmp / "config" / "studio.json").read_text())
check("the mode and the replaced values are saved", saved.get("beginner_mode") is True
      and saved["beginner_mode_saved"].get("dc.output_preset") == "medium"
      and saved["beginner_mode_saved"].get("merge.grain_master") is True, str(saved.get("beginner_mode_saved")))

# hover text
tech = dc.property("help").get("inference_steps", "")
plain = B.tip(tech)
check("hover text turns plain", tech and plain != tech and plain.startswith("How many passes"), plain[:50])

# re-pin on activation (v1): a hidden setting changed behind the mode
dc.settings.setProperty("output_preset_var", "fast"); pump(0.2)
show(1); show(0)
check("re-pinned when the stage shows again", dc.settings.property("output_preset_var") == "veryslow")

# fit: hiding only ever takes height away (check_fit measures the real widths)
for i in range(5):
    show(i)
    check(f"stage {i}: no taller than with the mode off", win.property("minimumHeight") <= heights_off[i],
          f"{heights_off[i]} -> {win.property('minimumHeight')}")

# ---- refused while a stage runs
_fixtures.fake_busy(sp, True)
check("refused while a stage runs", B.setEnabled(False) is False and B.property("on"))
_fixtures.fake_busy(sp, False)

# ---- off: everything back
show(0)
B.setEnabled(False); pump(0.8)
check("off", not B.property("on") and "Beginner" not in win.title())
check("Guidance Scale back", guidance.property("shown") is not False)
check("DC originals restored", dc.settings.property("latent_checkpoint_var") is False
      and dc.settings.property("output_preset_var") == "medium"
      and float(dc.settings.property("target_fps")) == 30.0)
check("Splatting / Inpainting originals restored", sp.settings.property("enable_low_res") is False
      and ip.settings.property("enable_color_transfer") is False)
check("Merging's grain master restored", mg.settings.property("grain_enable") is True)
check("Dilate X: again", by_text(pages[1], "Dilate X:") is not None)
check("hover text technical again", B.tip(tech) == tech)
check("the window height is v1's again", win.property("minimumHeight") == h_dc_off)

for c in ("DepthCrafter", "Splatting", "Inpainting", "InpaintingV2", "Merging", "SceneCut", "DepthEdit"):
    objs[c].shutdown()
studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
