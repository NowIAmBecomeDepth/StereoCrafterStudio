"""A Merging batch over clips of different heights (audit 2026-09-28, blocker):
each clip's output must be at ITS OWN height. v1 set the output height only
once per batch ("if not in locals()"), so every clip after the first was
declared -- and then force-resized -- at the first clip's height: a 16:9 clip
followed by a 2.39:1 one came out stretched. Real app, offscreen, real batch
(CPU encode), over the synthetic ``merge`` fixture plus a 320x136 clip built
here -- never the user's clips. Configs and last_jobs.json redirected."""
import os, sys, tempfile, hashlib, time, json, subprocess
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

# the fixture's two 320x180 clips (mA dual grid, mB quad), plus mC: 320x136
# (2.39:1), sorted last so it follows a 180-high clip in the batch
root = _fixtures.copy("merge", tmp / "fx")
_S = "size=320x136:rate=24:duration=2"
_MASK = f"nullsrc={_S},geq=lum='255*lt(hypot(X-W/2-50*sin(T*3),Y-H/2),40)':cb=128:cr=128"
enc = ("-c:v", "libx264", "-pix_fmt", "yuv420p")
def ff(*a): subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *a], check=True)
ff("-f", "lavfi", "-i", f"testsrc2={_S}", *enc, str(root / "orig" / "mC.mp4"))
ff("-f", "lavfi", "-i", f"testsrc2={_S},hue=h=150:s=2", *enc, str(root / "inpainted" / "mC_320_inpainted_right_eye.mp4"))
ff("-f", "lavfi", "-i", _MASK, "-f", "lavfi", "-i", f"testsrc2={_S},hue=h=60",
   "-filter_complex", "[0][1]hstack", *enc, str(root / "mask" / "mC_320_splatted2.mp4"))

def probe(p):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "json", str(p)], capture_output=True, text=True).stdout
    s = json.loads(out)["streams"][0]
    return int(s["width"]), int(s["height"])

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
def until(cond, sec=30):
    end = time.time() + sec
    while not cond() and time.time() < end:
        pump(0.05)
    return cond()

pump(0.6)
mg = objs["Merging"]
st = mg.property("settings")
out = tmp / "merged"

def run(fmt):
    for f in (out.glob("*") if out.exists() else []):
        f.unlink()
    for k, v in {"inpainted_folder": str(root / "inpainted"), "original_folder": str(root / "orig"),
                 "mask_folder": str(root / "mask"), "output_folder": str(out),
                 "encoding_encoder": "Force CPU", "codec": "H.264", "use_gpu": False, "resume": False,
                 "output_format": fmt, "pad_to_16_9": False, "split_lr": False,
                 "delete_sbs_after_split": False, "dnxhr_fullres_split": False}.items():
        st.setProperty(k, v)
    pump(0.3)
    toasts = []
    mg.toast.connect(lambda k, t, m: toasts.append((k, t, m)))
    mg.start()
    until(lambda: mg.property("busy"), 20)
    ok = until(lambda: not mg.property("busy") and out.exists() and len(list(out.glob("mC*"))) == 1, 240)
    pump(1.0)
    if not ok: print('   DBG', mg.property('status'), toasts, list(out.glob('*')) if out.exists() else 'no out', flush=True)
    return ok, {p.name.split("_")[0]: probe(p) for p in sorted(out.iterdir()) if p.suffix in (".mp4", ".mov", ".mkv")}

ok, sizes = run("Full SBS (Left-Right)")
check("Full SBS batch of three clips ran", ok and len(sizes) == 3, str(sizes))
check("...each at its own height: the 2.39:1 clip after a 16:9 one keeps 136, not 180",
      sizes.get("mA") == (640, 180) and sizes.get("mB") == (640, 180) and sizes.get("mC") == (640, 136), str(sizes))

ok, sizes = run("Double SBS")
check("Double SBS: every clip at twice its own height", ok and sizes.get("mA") == (640, 360)
      and sizes.get("mC") == (640, 272), str(sizes))

mg.shutdown()
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
