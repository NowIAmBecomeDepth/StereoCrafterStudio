"""Each stage's chapter / folders remembered per project (user, 2026-10-02), in
the real app, offscreen: two made-up projects; Splatting on a chapter and
DepthCrafter on hand-set folders in project A, Splatting on another chapter
in B; switching back and forth puts each stage back as it was in that
project, quietly (no "Chapter selected" toasts). Temp folders; configs and
last_jobs.json redirected.

    python studio/tests/checks/check_project_memory.py
"""
import os, sys, tempfile, hashlib, time
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
rec = REPO / "last_jobs.json"; h0 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from PySide6.QtCore import QCoreApplication, QEventLoop
from PySide6.QtGui import QGuiApplication
from studio import app as A

for proj, pre in (("Movie A", "MA"), ("Movie B", "MB")):
    for st in ("Source Clips", "DepthMaps", "Splats", "Inpainted", "Merged"):
        for ch in ("Chapter 1 - One", "Chapter 2 - Two"):
            (tmp / proj / f"{pre} {st}" / ch).mkdir(parents=True)
PA, PB = (tmp / "Movie A").as_posix(), (tmp / "Movie B").as_posix()

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
toasts = []
shell, sp, dc = objs["Shell"], objs["Splatting"], objs["DepthCrafter"]
for c in (sp, dc, objs["Inpainting"], objs["Merging"], objs["SceneCut"]):
    c.toast.connect(lambda *a: toasts.append(a[1]))
def num_of(ctl, name):
    return next(c["num"] for c in ctl.property("chapters") if c["name"].startswith(name))
def norm(p):
    return str(p or "").replace("\\", "/").rstrip("/")

shell.setProperty("projectFolder", PA); pump(0.3)
sp.pickChapter(num_of(sp, "Chapter 1")); pump(0.2)
mine = (tmp / "elsewhere" / "in").as_posix(), (tmp / "elsewhere" / "out").as_posix()
dc.pickChapter(-1)
dc._map.insert("input_dir_or_file_var", mine[0]); dc._map.insert("output_dir", mine[1]); pump(0.2)
check("A: Splatting on chapter 1, DepthCrafter on hand-set folders",
      norm(sp._map.value("output_splatted")).endswith("Movie A/MA Splats/Chapter 1 - One") and dc.property("chapterNum") == -1,
      sp._map.value("output_splatted"))

shell.setProperty("projectFolder", PB); pump(0.3)
check("B, new to it: Splatting left as it was (nothing remembered there)",
      norm(sp._map.value("output_splatted")).endswith("Movie A/MA Splats/Chapter 1 - One") and sp.property("chapterNum") == -1,
      (sp._map.value("output_splatted"), sp.property("chapterNum")))
sp.pickChapter(num_of(sp, "Chapter 2")); pump(0.2)
n_toasts = len(toasts)

shell.setProperty("projectFolder", PA); pump(0.3)
check("back to A: Splatting on its chapter 1 again", sp.property("chapterNum") == num_of(sp, "Chapter 1")
      and norm(sp._map.value("input_depth_maps")).endswith("Movie A/MA DepthMaps/Chapter 1 - One"),
      (sp.property("chapterNum"), sp._map.value("input_depth_maps")))
check("...and DepthCrafter on its hand-set folders", (norm(dc._map.value("input_dir_or_file_var")), norm(dc._map.value("output_dir"))) == mine,
      (dc._map.value("input_dir_or_file_var"), dc._map.value("output_dir")))

shell.setProperty("projectFolder", PB); pump(0.3)
check("to B again: Splatting on B's chapter 2", sp.property("chapterNum") == num_of(sp, "Chapter 2")
      and norm(sp._map.value("output_splatted")).endswith("Movie B/MB Splats/Chapter 2 - Two"), sp._map.value("output_splatted"))
check("the switches put up no 'Chapter selected' toasts", len(toasts) == n_toasts, toasts[n_toasts:])
mem = shell._data.get("project_memory", {})
check("the memory is in Studio's settings (temp), per project", PA.lower() in mem and PB.lower() in mem, list(mem))

h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
print("last_jobs unchanged:", h0 == h1, flush=True)
print("FAILS", len(fails), fails, flush=True)
# through the fixtures: its temp folders removed, and no DLL teardown crash after a green
# run (bug sweep 2026-10-09, A12)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _fixtures  # noqa: E402
_fixtures.exit_now(1 if fails or h0 != h1 else 0)
