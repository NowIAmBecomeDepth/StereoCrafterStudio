"""Inpainting V2's and Scene Cut's run, one state at a time (user, 2026-11-07): idle ->
loading (the first Start imports the engine) -> running -> idle. Before, the import
counted as idle: Stop was greyed out and did nothing, and the run started when the
import landed. Real app, offscreen; the engine modules are imported first, so the
"loaded" signal waits only for the event loop -- the check holds it by not pumping.
The starts are spied on: nothing is inpainted or cut. Configs and last_jobs.json
redirected.

  * while the engine loads for a Start, the stage is busy, and Stop is on
  * Stop then: idle at once, and the start doesn't go ahead when the import lands
  * Stop After Clip isn't offered then (V2)
  * two things asked of Scene Cut's loading engine both run (one slot lost the first)"""
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

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
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
empty = tmp / "no_clips"; empty.mkdir()

# ---- Inpainting V2
import studio.stages.inpainting_v2.controller as V2C
import studio.stages.inpainting_v2.engine as _v2_engine        # imported now: the load thread is instant
wan = objs["InpaintingV2"]
V2C.weights_installed = lambda root=None: True                # Start's own gate, not this check's subject
wst = wan.property("settings")
wst.setProperty("input_folder", str(empty)); wst.setProperty("output_folder", str(tmp / "v2out"))
pump(0.3)
started = []
_start_now = wan._start_now
wan._start_now = lambda: started.append(1)
check("V2: the engine isn't loaded yet", wan._module is None)
wan.start()                                                    # no pump: the "loaded" signal waits
check("V2: while the engine loads for a Start, it is busy", wan.property("busy") is True)
check("...is_busy() too (the GPU hand-over)", wan.is_busy())
check("...Stop After Clip isn't offered", not wan.property("multiClip"))
wan.stop()
check("...Stop: idle at once", not wan.property("busy") and not wan.is_busy())
until(lambda: wan._module is not None, 30); pump(0.5)
check("...the start doesn't go ahead when the engine lands", not started, str(started))
check("...the clip list let go", not wan.queue._hold)
check("...and the status says it stopped", "stop" in str(wan.property("status")).lower(), str(wan.property("status")))
wan._start_now = _start_now
wan.start(); pump(1.0)                                         # loaded now: an empty folder, refused
check("V2: a refused start (no clips) ends idle, the list let go",
      until(lambda: not wan.property("busy") and not wan.is_busy(), 30) and not wan.queue._hold)

# ---- Scene Cut
import studio.stages.scenecut.engine as SE                    # imported now, as V2's
sc = objs["SceneCut"]
detections = []
_det = SE.SceneCutEngine._start_detection
SE.SceneCutEngine._start_detection = lambda self: detections.append(1)
check("Scene Cut: the engine isn't loaded yet", sc._module is None)
sc.start()
check("Scene Cut: while the engine loads for a Start, it is busy", sc.property("busy") is True)
check("...is_busy() too", sc.is_busy())
sc.abort()
check("...Stop: idle at once", not sc.property("busy") and not sc.is_busy())
until(lambda: sc._module is not None, 30); pump(0.5)
check("...the start doesn't go ahead when the engine lands", not detections, str(detections))
check("...and the status says it stopped", "stop" in str(sc.property("status")).lower(), str(sc.property("status")))

# two asks while it loads: both run
sc._module = None
got = []
sc._with_engine(lambda eng: got.append("first"))
sc._with_engine(lambda eng: got.append("second"))
until(lambda: len(got) == 2, 30)
check("Scene Cut: both things asked of the loading engine ran, in order", got == ["first", "second"], str(got))
SE.SceneCutEngine._start_detection = _det

for name in ("InpaintingV2", "SceneCut"):
    try: objs[name].shutdown()
    except Exception: pass
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
