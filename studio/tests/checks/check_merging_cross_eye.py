"""Full SBS Cross-eye (Right-Left) in Merging (tester report 2026-11-09: it
came out as a plain SBS): the cross-eye output's left half must be the Full
SBS output's right half and its right half the left, on the CPU and the GPU
path. Real app, offscreen, real batch (CPU encode), over the synthetic
``merge`` fixture -- never the user's clips. Configs and last_jobs.json
redirected."""
import os, sys, tempfile, hashlib, time, subprocess
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
import numpy as np
from PySide6.QtCore import QCoreApplication, QEventLoop
from PySide6.QtGui import QGuiApplication
from studio import app as A

root = _fixtures.copy("merge", tmp / "fx")


def frames(p):
    """The clip's first frames as rgb24, (n, h, w, 3)."""
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "csv=p=0", str(p)], capture_output=True, text=True).stdout
    w, h = (int(x) for x in out.strip().split(",")[:2])
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(p), "-frames:v", "8", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w, 3).astype(np.int16)


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


def run(fmt, gpu, out):
    for k, v in {"inpainted_folder": str(root / "inpainted"), "original_folder": str(root / "orig"),
                 "mask_folder": str(root / "mask"), "output_folder": str(out),
                 "encoding_encoder": "Force CPU", "codec": "H.264", "use_gpu": gpu, "resume": False,
                 "output_format": fmt, "pad_to_16_9": False, "split_lr": False, "output_crf": 0,
                 "delete_sbs_after_split": False, "dnxhr_fullres_split": False}.items():
        st.setProperty(k, v)
    pump(0.3)
    mg.start()
    until(lambda: mg.property("busy"), 20)
    ok = until(lambda: not mg.property("busy") and out.exists()
               and len([p for p in out.iterdir() if ".partial" not in p.name]) >= 2, 240)
    pump(1.0)
    return ok, {p.name.split("_")[0]: p for p in sorted(out.iterdir()) if p.suffix in (".mp4", ".mkv")}


import torch
for gpu in ([False, True] if torch.cuda.is_available() else [False]):
    tag = "GPU" if gpu else "CPU"
    ok1, full = run("Full SBS (Left-Right)", gpu, tmp / f"full_{tag}")
    ok2, cross = run("Full SBS Cross-eye (Right-Left)", gpu, tmp / f"cross_{tag}")
    check(f"{tag}: both batches ran", ok1 and ok2 and set(full) == set(cross) and full, f"{list(full)} / {list(cross)}")
    for clip in sorted(full):
        check(f"{tag} {clip}: the cross-eye file is named _sbsx", "_merged_full_sbsx" in cross[clip].name, cross[clip].name)
        a, b = frames(full[clip]), frames(cross[clip])
        w = a.shape[2] // 2
        same = float(np.abs(a - b).mean())
        swapped = max(float(np.abs(a[:, :, :w] - b[:, :, w:]).mean()), float(np.abs(a[:, :, w:] - b[:, :, :w]).mean()))
        check(f"{tag} {clip}: the halves are swapped (left = Full SBS's right eye)", swapped < 3.0 and same > 3 * swapped,
              f"swapped diff {swapped:.2f}, as-is diff {same:.2f}")

mg.shutdown()
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
