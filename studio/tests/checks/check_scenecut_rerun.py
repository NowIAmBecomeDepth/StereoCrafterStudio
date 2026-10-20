"""Scene Cut re-run into the same folder (audit 2026-09-28 #11), through the
real Start in the real app, offscreen. A clip cut into three scenes, then the
same-named clip re-cut into two: the old -Scene-003 must not be left beside
the new set (it goes to the Recycle Bin -- stubbed here, so the user's bin is
not touched), the Success message says so, and files that are not this
clip's scenes are left alone. Configs and last_jobs.json redirected.
"""
import os, sys, tempfile, hashlib, time, json, shutil, subprocess
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, str(Path(__file__).parent))
rec = REPO / "last_jobs.json"; h0 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
tmp = Path(tempfile.mkdtemp())
import _fixtures
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from PySide6.QtCore import QCoreApplication, QEventLoop
from PySide6.QtGui import QGuiApplication
from studio import app as A


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
def run(ctl, start, sec=300):
    start()
    until(lambda: ctl.property("busy"), 10)
    ok = until(lambda: not ctl.property("busy"), sec)
    pump(0.5)
    return ok
messages = []
objs["SceneCut"].message.connect(lambda k, t, m: messages.append((k, t, m)))
pump(0.8)

def make_clip(path, sources):
    ins, n = [], len(sources)
    for s in sources:
        ins += ["-f", "lavfi", "-i", s]
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *ins, "-filter_complex",
                    "".join(f"[{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=0,format=yuv420p",
                    "-c:v", "libx264", str(path)], check=True)

THREE = ["testsrc2=size=320x180:rate=24:duration=1", "mandelbrot=size=320x180:rate=24,trim=duration=1",
         "smptebars=size=320x180:rate=24:duration=1"]
sc = objs["SceneCut"]; stsc = sc.property("settings")
clip = tmp / "shots.mp4"
make_clip(clip, THREE)
out = tmp / "sc_out"
sc.ensureLoaded()
check("the Scene Cut engine loads", until(lambda: sc.property("ready"), 90), sc.property("loadError"))
for k, v in {"detector_type": "AutoShot", "video_path": str(clip).replace("\\", "/"),
             "output_dir": str(out).replace("\\", "/"), "export_csv": False, "split_video": True,
             "split_by_chapter": False, "save_images": False, "export_html": False, "export_sc": False,
             "refinements_enabled": True, "snap_cuts": True, "ai_validate": False}.items():
    stsc.setProperty(k, v)
sc.setDetectorParam("min_scene_len", "6")
pump(0.3)
check("the first run (three shots)", run(sc, sc.start))
first = sorted(p.name for p in out.glob("*.mp4"))
check("...wrote three scenes", len(first) == 3, str(first))
# files that only look like scenes, and another clip's scene: never touched
keep = [out / "other-Scene-009.mp4", out / "shots-Scene-004.mp4.txt", out / "shots-Scene-00x.mp4"]
for p in keep:
    p.write_bytes(b"keep")

import studio.stages.scenecut.engine as E
binned = []
def fake_bin(path):
    binned.append(Path(path).name); os.remove(path); return True
_saved = getattr(E, "send_to_recycle_bin", None)
E.send_to_recycle_bin = fake_bin
make_clip(clip, THREE[:2])          # the same name, re-cut: two shots now
messages.clear()
check("the re-run (two shots)", run(sc, sc.start))
if _saved is not None:
    E.send_to_recycle_bin = _saved
second = sorted(p.name for p in out.glob("shots-Scene-[0-9][0-9][0-9].mp4"))
check("...only this run's two scenes are left", second == ["shots-Scene-001.mp4", "shots-Scene-002.mp4"], str(second))
check("...the stale one went to the Recycle Bin", binned == ["shots-Scene-003.mp4"], str(binned))
check("...look-alikes and other clips are untouched", all(p.read_bytes() == b"keep" for p in keep))
said = [m for m in messages if m[0] == "info"]
check("...the Success message says so", said and "Recycle Bin" in said[-1][2], str(messages))

print()
_lj = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None) == h0
print("real last_jobs.json unchanged:", _lj)
if not _lj:
    fails.append("real last_jobs.json changed")
print("FAILED:" if fails else "ALL OK", fails if fails else "")
for name in ("DepthCrafter", "Splatting", "Inpainting", "InpaintingV2", "Merging", "SceneCut", "DepthEdit"):
    try: objs[name].shutdown()
    except Exception: pass
_fixtures.exit_now(1 if fails else 0)
