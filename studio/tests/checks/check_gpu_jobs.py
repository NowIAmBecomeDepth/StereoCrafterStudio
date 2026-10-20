"""v1's shell request_job in v2 (studio/gpu_jobs.py), in the real app,
offscreen: a start is refused while another stage renders (v1's warning,
shown by Main); granted, every OTHER stage holding GPU state is released
(never the claimant, never its same-stage sibling), and an evicted preview
comes back once the job has finished. One real hand-over: a loaded
Splatting preview over the synthetic ``small`` clips lets go when
Inpainting claims, and reloads after. Plus each stage's claim point:
validation failures never evict. Configs and last_jobs.json redirected."""
import os, sys, tempfile, hashlib, time
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
FIX = _fixtures.copy("small", tmp / "fx")
from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from studio import app as A
from studio.gpu_jobs import _busy

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
dc, sp, ip, ip2, mg, sc, de = (objs[k] for k in ("DepthCrafter", "Splatting", "Inpainting", "InpaintingV2",
                                                 "Merging", "SceneCut", "DepthEdit"))
refusals = []
J.refused.connect(lambda t, m: refusals.append((t, m)))
prompt = win.findChild(QObject, "prompt") or None

# ---- refused while another stage renders (v1's text)
_fixtures.fake_busy(sp, True)
check("refused while Splatting renders", J.claim("depthcrafter", dc) is False)
check("...with v1's warning", refusals and refusals[-1] == ("Another stage is running",
      "Splatting is currently processing. Wait for it to finish before starting another stage."), str(refusals[-1:]))
check("a stage's own claim is not refused by itself", J.claim("splatting", sp) is True)
_fixtures.fake_busy(sp, False)

# ---- granted: every other holder released, never the claimant or its sibling
calls = []
def fake(ctl, name, holds=True):
    ctl.holds_gpu = lambda: holds
    ctl.release_gpu = lambda: calls.append(name)
for ctl, name in ((sp, "splatting"), (ip2, "inpainting_v2"), (mg, "merging"), (sc, "scenecut"), (de, "depthedit")):
    fake(ctl, name)
calls.clear()
check("Merging's claim granted", J.claim("merging", mg) is True)
check("...releases every other holder, not Merging itself", sorted(calls) == ["depthedit", "inpainting_v2", "scenecut", "splatting"], str(calls))
calls.clear(); J.claim("inpainting", ip)
check("Inpainting V1's claim leaves V2 to the sibling guard (v1: same stage)", "inpainting_v2" not in calls and "merging" in calls, str(calls))
calls.clear(); fake(sc, "scenecut", holds=False); J.claim("depthcrafter", dc)
check("a stage holding nothing is not asked", "scenecut" not in calls, str(calls))
for ctl in (sp, ip2, mg, sc, de):          # back to the real methods
    for m in ("holds_gpu", "release_gpu"):
        if m in ctl.__dict__: del ctl.__dict__[m]
J._poll.stop(); J._restore = []

# ---- validation failures never claim (the claim is the last step of a start)
claims = []
_claim = J.claim
J.claim = lambda sid, ctl=None: (claims.append(sid), _claim(sid, ctl))[1]
dc.settings.setProperty("input_dir_or_file_var", str(tmp / "nowhere")); dc.start(); pump(0.3)
ip.settings.setProperty("input_folder", str(tmp / "nowhere")); ip.start(); pump(0.3)
check("DepthCrafter / Inpainting starts that fail validation claim nothing", claims == [], str(claims))
J.claim = _claim

# ---- each stage's real Start asks for the claim (valid inputs; the claim refused here)
MFX = _fixtures.copy("merge", tmp / "mfx")
claims = []
J.claim = lambda sid, ctl=None: (claims.append(sid), False)[1]
dc.settings.setProperty("input_dir_or_file_var", str(FIX / "src")); dc.settings.setProperty("output_dir", str(tmp / "dcout"))
dc.start(); pump(0.5)
check("DepthCrafter's Start claims, and a refusal stops it", claims[-1:] == ["depthcrafter"] and not _busy(dc), str(claims))
for k, v in {"input_source_clips": str(FIX / "src"), "input_depth_maps": str(FIX / "depth"), "output_splatted": str(tmp / "spout")}.items():
    sp._set(k, v)
sp.start(); pump(0.5)
check("Splatting's Start claims, and a refusal stops it", claims[-1:] == ["splatting"] and not _busy(sp), str(claims))
(tmp / "ipin").mkdir(); (tmp / "ipout").mkdir()
ip.settings.setProperty("input_folder", str(tmp / "ipin")); ip.settings.setProperty("output_folder", str(tmp / "ipout"))
ip.settings.setProperty("hires_blend_folder", "")
ip.start(); pump(0.5)
check("Inpainting's Start claims, and a refusal stops it", claims[-1:] == ["inpainting"] and not _busy(ip), str(claims))
st = mg.property("settings")
for k, v in {"inpainted_folder": str(MFX / "inpainted"), "original_folder": str(MFX / "orig"), "mask_folder": str(MFX / "mask"),
             "output_folder": str(tmp / "mgout")}.items():
    st.setProperty(k, v)
pump(0.3)
mpv = mg.property("previewer")
mpv.load()
check("the Merging preview loads", until(lambda: mpv.property("loaded") and not mpv.property("loading"), 60))
seen = []
mpv.changed.connect(lambda: seen.append(bool(mpv.property("loaded"))))
mg.start()
check("Merging's Start claims (at v1's own claim point), and a refusal stops it",
      until(lambda: claims[-1:] == ["merging"], 20) and not _busy(mg), str(claims))
check("...and a refused Merging start gives its preview back (released, then reloaded)",
      until(lambda: False in seen and seen[-1] is True and mpv.property("loaded"), 30), str(seen[-6:]))
J.claim = _claim

# ---- one real hand-over: Splatting's loaded preview lets go, then comes back
for k, v in {"input_source_clips": str(FIX / "src").replace("\\", "/"), "input_depth_maps": str(FIX / "depth").replace("\\", "/"),
             "output_splatted": str(FIX / "out").replace("\\", "/"), "multi_map": False, "preview_source": "Splat Result",
             "preview_size": "100%", "auto_save_sidecar": False}.items():
    sp._set(k, v)
pv = sp.preview
pv.load()
check("the Splatting preview loads", until(lambda: pv.loaded and not pv._jobs, 90))
check("...and Splatting now holds the GPU", sp.holds_gpu())
ip._busy = True; ip.stateChanged.emit()            # Inpainting "starts": its claim first
ip._busy = False
check("Inpainting's claim granted", J.claim("inpainting", ip) is True)
_fixtures.fake_busy(ip, True)
check("the preview let go", until(lambda: not pv.loaded and not pv._jobs, 30))
pump(2.5)
check("...and stays released while the job runs", not pv.loaded)
_fixtures.fake_busy(ip, False)
check("job finished: the preview comes back", until(lambda: pv.loaded, 60))

for c in ("DepthCrafter", "Splatting", "Inpainting", "InpaintingV2", "Merging", "SceneCut", "DepthEdit"):
    objs[c].shutdown()
studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
