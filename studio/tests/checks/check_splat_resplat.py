"""Re-splatting a clip that was just splatted (user, 2026-09-28): its H / L
bars in Upcoming Jobs must start again from empty and fill as the second run
renders, not stay full from the first. The real app offscreen, two real SINGLE
runs over a TEMP copy of the small fixture (both passes on); configs and
last_jobs.json redirected."""
import os, sys, tempfile, hashlib, time
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, str(Path(__file__).parent))
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
tmp = Path(tempfile.mkdtemp())
import _fixtures
FIX = tmp / "fx"; _fixtures.copy("small", FIX)
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG
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
def until(cond, sec=60):
    end = time.time() + sec
    while not cond() and time.time() < end:
        pump(0.05)
    return cond()
def find_all(it, pred, out=None):
    out = [] if out is None else out
    for c in it.childItems():
        if pred(c): out.append(c)
        find_all(c, pred, out)
    return out

pump(0.6)
sp = objs["Splatting"]; pv = sp.preview
stage = find_all(win.contentItem(), lambda c: c.objectName() == "stage")[0]
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 1), Q_ARG("QVariant", True)); pump(1.0)
for k, v in {"input_source_clips": str(FIX / "src").replace("\\", "/"), "input_depth_maps": str(FIX / "depth").replace("\\", "/"),
             "output_splatted": str(FIX / "out").replace("\\", "/"), "multi_map": False, "auto_save_sidecar": False,
             "move_to_finished": False, "enable_full_res": True, "enable_low_res": True, "dual_output": False,
             "auto_convergence_mode": "Off", "preview_source": "Splat Result"}.items():
    sp._set(k, v)
win.setProperty("userSized", True); win.setHeight(1600); pump(0.5)
pv.load()
check("clip A in the preview", until(lambda: pv.property("loaded"), 60))
pump(1.0)
sq = find_all(win.contentItem(), lambda c: c.objectName() == "splatJobQueue")[0]

def bars():
    rows = [r for r in find_all(sq, lambda c: c.objectName() == "jobQueueRow")
            if "clipA" in str(r.property("modelData"))]
    if not rows:
        return None
    return tuple(round(b.property("value"), 2) for b in find_all(rows[0], lambda c: c.objectName() == "jobQueuePassBar"))

def run_single():
    seen = []
    sp.startSingle()
    until(lambda: sp.property("busy"), 10)
    t0 = time.time()
    while (sp.property("busy") or time.time() - t0 < 1) and time.time() - t0 < 300:
        pump(0.03)
        b = bars()
        if b is not None and sp.property("busy") and (not seen or seen[-1] != b):
            seen.append(b)
    pump(0.8)
    return seen, bars()

first, after1 = run_single()
print("   first run:", first, "->", after1, flush=True)
check("the first run starts clip A's bars empty (the preview's clip is not taken as done)",
      bool(first) and first[0] == (0.0, 0.0), str(first[:3]))
check("...fills its H and L bars, and they stay full after it", after1 == (1.0, 1.0), str(after1))
until(lambda: pv.property("loaded"), 60); pump(1.0)
second, after2 = run_single()
print("   second run:", second, "->", after2, flush=True)
check("re-splatting it: the bars start again from empty (H empty as the run begins)",
      bool(second) and second[0] == (0.0, 0.0), str(second[:4]))
check("...H fills part-way and then full, L after it, as the second run renders",
      any(0 < h < 1 for h, _l in second) and any(h == 1.0 and 0 <= l < 1 for h, l in second), str(second))
check("...and both full once it is done", after2 == (1.0, 1.0), str(after2))

# audit 2026-09-28 (low confidence, settled here): a Stop between the passes
# must not mark the clip done -- H full, L not, and the list keeps it that way
until(lambda: pv.property("loaded"), 60); pump(1.0)
sp.startSingle()
until(lambda: sp.property("busy"), 10)
t0 = time.time(); stopped_at = None
while sp.property("busy") and time.time() - t0 < 300:
    pump(0.02)
    b = bars()
    if b is not None and b[0] == 1.0 and stopped_at is None:
        stopped_at = b
        sp.stop()                                  # Stop now, as the prompt's "Stop now" does
until(lambda: not sp.property("busy"), 60); pump(0.8)
after3 = bars()
print("   stopped at:", stopped_at, "->", after3, "| status:", sp.property("status"), "| passDone:", list(sp.property("passDone")), flush=True)
check("a Stop once H was done leaves the clip not done: it keeps its bars, H full and L not",
      stopped_at is not None and after3 is not None and after3[0] == 1.0 and after3[1] < 1.0,
      f"stopped at {stopped_at}, after {after3}")

pv.shutdown()
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
