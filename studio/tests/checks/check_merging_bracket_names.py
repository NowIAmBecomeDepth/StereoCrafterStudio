"""Merging finds the splat of a clip whose name -- or folder -- has [ ] in it (bug sweep
2026-10-09: the globs took `Show [1080p]` as a pattern, so a single-eye clip was skipped
as "missing splatted file" and a dual-eye one written as a raw passthrough). Real app,
offscreen, real batch (CPU encode) over the synthetic ``merge`` fixture copied into a
folder named ``fx [2020]``, mA renamed ``mA [1080p]`` -- never the user's clips. Configs
and last_jobs.json redirected.

  * the preflight sees mA [1080p]'s single-eye splat (so it needs its original)
  * both clips merge, mA [1080p] from its _splatted2 + original, mB from its quad grid"""
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
from PySide6.QtCore import QCoreApplication, QEventLoop
from PySide6.QtGui import QGuiApplication
from studio import app as A
from core.common import merge_preflight

root = _fixtures.copy("merge", tmp / "fx [2020]")
NAME = "mA [1080p]"
for sub, old, new in (("orig", "mA.mp4", f"{NAME}.mp4"),
                      ("inpainted", "mA_320_inpainted_right_eye.mp4", f"{NAME}_320_inpainted_right_eye.mp4"),
                      ("mask", "mA_320_splatted2.mp4", f"{NAME}_320_splatted2.mp4")):
    (root / sub / old).rename(root / sub / new)

fails = []
def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok: fails.append(label)

check("the preflight finds the bracketed clip's single-eye splat",
      merge_preflight.needs_original(f"{NAME}_320_inpainted_right_eye.mp4", NAME, str(root / "mask")))

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

pump(0.6)
mg = objs["Merging"]
st = mg.property("settings")
out = tmp / "merged [out]"
toasts = []
mg.toast.connect(lambda k, t, m: toasts.append((k, t, m)))
for k, v in {"inpainted_folder": str(root / "inpainted"), "original_folder": str(root / "orig"),
             "mask_folder": str(root / "mask"), "output_folder": str(out),
             "encoding_encoder": "Force CPU", "codec": "H.264", "use_gpu": False, "resume": False,
             "output_format": "Full SBS (Left-Right)", "pad_to_16_9": False, "split_lr": False,
             "delete_sbs_after_split": False, "dnxhr_fullres_split": False}.items():
    st.setProperty(k, v)
pump(0.3)
mg.start()
until(lambda: mg.property("busy"), 20)
until(lambda: not mg.property("busy") and out.exists() and len(list(out.glob("m*"))) == 2, 240)
pump(1.0)
made = sorted(p.name for p in out.iterdir()) if out.exists() else []
errs = [t for t in toasts if t[0] == "error"]
check("both clips merge, the bracketed one included",
      any(m.startswith(f"{NAME}_") for m in made) and any(m.startswith("mB_") for m in made) and not errs,
      f"{made} {toasts}")

mg.shutdown()
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
