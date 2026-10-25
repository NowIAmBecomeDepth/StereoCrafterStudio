"""Encoding Settings' Container (user, 2026-10-25: "a dropdown to choose mp4 or mkv when dnxhr isnt
selected"), real app, offscreen, real batches over TEMP copies of the fixtures -- never the user's clips.
Configs and last_jobs.json redirected.

  * Splatting with Container MKV writes its splats as Matroska .mkv, and Inpainting's clip list takes them
  * Merging reads .mkv splats and inpainted clips, and with Container MKV writes Matroska .mkv
  * the dialog's Container row: there, MP4 by default, greyed out while Enable DNxHR is on
"""
import os, sys, tempfile, hashlib, time, subprocess, json
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
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG
from PySide6.QtGui import QGuiApplication
from studio import app as A
from studio import clip_queue as Q

fails = []
def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok: fails.append(label)
def fmt(p):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=format_name", "-of", "json", str(p)],
                         capture_output=True, text=True)
    return (json.loads(out.stdout or "{}").get("format") or {}).get("format_name", "")

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
win.show()
def pump(sec):
    end = time.time() + sec
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
pump(0.6)
toasts = []
for ctl in objs.values():
    sig = getattr(ctl, "toast", None)
    if sig is not None and hasattr(sig, "connect"):
        sig.connect(lambda k, t, m: toasts.append((k, t, m)))

# ---- Splatting: Container MKV (GPU: CHECK_SKIP_SPLAT=1 leaves it out while the user's Studio holds the card)
sp = objs["Splatting"]
if os.environ.get("CHECK_SKIP_SPLAT"):
    print("  (Splatting part skipped)")
else:
  pass
if not os.environ.get("CHECK_SKIP_SPLAT"):
    FIX = _fixtures.copy("small", tmp / "sp")
    out = tmp / "sp_out"
    for k, v in {"multi_map": False, "auto_save_sidecar": False, "move_to_finished": False, "enable_full_res": False,
                 "enable_low_res": True, "dual_output": True, "auto_convergence_mode": "Off", "dnxhr_fullres_split": False,
                 "output_container": "MKV", "input_source_clips": str(FIX / "src").replace("\\", "/"),
                 "input_depth_maps": str(FIX / "depth").replace("\\", "/"),
                 "output_splatted": str(out).replace("\\", "/")}.items():
        sp._set(k, v)
    pump(0.3)
    sp.start()
    until(lambda: sp.property("busy"), 10)
    until(lambda: not sp.property("busy"), 300)
    pump(0.5)
    made = sorted(out.rglob("*_splatted*.*"))
    check("Splatting, Container MKV: its splats are .mkv files, Matroska inside",
          len(made) == 2 and all(p.suffix == ".mkv" and "matroska" in fmt(p) for p in made),
          f"{[p.name for p in made]} {[fmt(p) for p in made]} {toasts[-2:]}")
    lowres = made[0].parent if made else out
    check("...and Inpainting's clip list takes them", sorted(Path(p).name for p in Q.inpaint_clips({"input_folder": str(lowres)}))
          == sorted(p.name for p in made), Q.inpaint_clips({"input_folder": str(lowres)}))

# ---- Merging: .mkv inputs, Container MKV out
mg = objs["Merging"]
root = _fixtures.copy("merge", tmp / "mg")
for sub in ("inpainted", "mask"):
    for f in list((root / sub).glob("*.mp4")):
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(f), "-c", "copy",
                        str(f.with_suffix(".mkv"))], check=True)
        f.unlink()
mout = tmp / "mg_out"
st = mg.property("settings")
for k, v in {"inpainted_folder": str(root / "inpainted"), "original_folder": str(root / "orig"),
             "mask_folder": str(root / "mask"), "output_folder": str(mout),
             "encoding_encoder": "Force CPU", "codec": "H.264", "use_gpu": False, "resume": False,
             "output_format": "Full SBS (Left-Right)", "pad_to_16_9": False, "split_lr": False,
             "delete_sbs_after_split": False, "dnxhr_fullres_split": False, "output_container": "MKV"}.items():
    st.setProperty(k, v)
pump(0.3)
toasts.clear()
mg.start()
until(lambda: mg.property("busy"), 20)
until(lambda: not mg.property("busy"), 300)
pump(1.0)
merged = sorted(mout.glob("*")) if mout.exists() else []
errs = [t for t in toasts if t[0] == "error"]
check("Merging: .mkv splats and inpainted clips read, Container MKV: the merged clips are .mkv, Matroska inside",
      len(merged) == 2 and all(p.suffix == ".mkv" and "matroska" in fmt(p) for p in merged) and not errs,
      f"{[p.name for p in merged]} {toasts[-3:]}")

# ---- the dialog's Container row
stage = find(win.contentItem(), lambda c: c.objectName() == "stage")
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 3), Q_ARG("QVariant", True)); pump(0.8)
page = stage.property("pages").property(3).toQObject()
from PySide6.QtCore import QObject
dlg = next((o for o in win.findChildren(QObject) if o.metaObject().className().startswith("EncodingDialog")
            and o.property("ctl") is mg), None)          # Merging's own (each stage has one)
check("the Encoding dialog is there", dlg is not None)
if dlg is not None:
    QMetaObject.invokeMethod(dlg, "open"); pump(0.6)
    row = None
    from PySide6.QtCore import QObject
    row = next((o for o in dlg.findChildren(QObject) if o.objectName() == "encContainerRow"), None)
    check("...a Container row", row is not None and row.property("visible"))
    st.setProperty("dnxhr_fullres_split", False); pump(0.2)
    check("...live with DNxHR off", row is not None and row.property("enabled") is True)
    st.setProperty("dnxhr_fullres_split", True); pump(0.2)
    check("...greyed out with Enable DNxHR on (the output is .mov then)", row is not None and row.property("enabled") is False)
    st.setProperty("dnxhr_fullres_split", False); pump(0.2)
    QMetaObject.invokeMethod(dlg, "close"); pump(0.3)

mg.shutdown()
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
