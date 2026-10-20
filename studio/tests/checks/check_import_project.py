"""Import buttons and the picked project (user, 2026-10-02), in the real app,
offscreen: the user's case -- Splatting last ran on a show's episode, then
Inpainting on a movie's chapter 6. With the movie picked, Merging's Import
from Inpainting fills Inpainted, Original, Mask AND Output from the movie's
chapter 6 (as the chapter list would); with the show picked, the button is
hidden and an Import fills nothing; Inpainting's Import from Splatting (the
show's Splatting job) is offered for the show only. Temp folders; configs and last_jobs.json redirected.

    python studio/tests/checks/check_import_project.py
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

fails = []
def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok: fails.append(label)
def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.005)
f = lambda p: str(p).replace("\\", "/")   # noqa: E731

CH6 = "Chapter 6 - First Side Effects"
movie = tmp / "Conversions" / "My Film"
show_ep = tmp / "Conversions" / "My Show" / "Season 1" / "Episode 1"
for base, pre, ch in ((movie, "SM", CH6), (show_ep, "S1E1", "Chapter 1")):
    for st in ("Source Clips", "DepthMaps", "Splats", "Inpainted", "Merged"):
        (base / f"{pre} {st}" / ch).mkdir(parents=True)
    (base / f"{pre} Source Clips" / ch / "finished" / "finished").mkdir(parents=True)
    (base / f"{pre} Splats" / ch / "hires" / "finished").mkdir(parents=True)
    (base / f"{pre} Splats" / ch / "lowres").mkdir(parents=True)

h = lambda s: f(show_ep / f"S1E1 {s}" / "Chapter 1")   # noqa: E731
m = lambda s: f(movie / f"SM {s}" / CH6)               # noqa: E731
jh.record_job("splatting", {"source": h("Source Clips"), "depth": h("DepthMaps"), "splat_out": h("Splats"),
                            "hires": h("Splats") + "/hires", "lowres": h("Splats") + "/lowres"})
jh.record_job("inpainting", {"hires": m("Splats") + "/hires", "lowres": m("Splats") + "/lowres",
                             "inpainted_out": m("Inpainted")})

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
shell, mg, ip = objs["Shell"], objs["Merging"], objs["Inpainting"]

shell.setProperty("projectFolder", f(movie)); pump(0.4)
check("movie picked: Import from Inpainting offered", mg.property("importDescription") != "")
mg.importFromInpainting(); pump(0.3)
got = {k: f(mg._map.value(k)).rstrip("/") for k in ("inpainted_folder", "original_folder", "mask_folder", "output_folder")}
want = {"inpainted_folder": m("Inpainted"), "original_folder": m("Source Clips") + "/finished/finished",
        "mask_folder": m("Splats") + "/hires/finished", "output_folder": m("Merged")}
check("...it fills Inpainted, Original, Mask and Output from the movie's chapter 6", got == want, got)
check("...nothing of the show's", "My Show" not in "".join(got.values()))

shell.setProperty("projectFolder", f(tmp / "Conversions" / "My Show")); pump(0.4)
check("show picked: Import from Inpainting hidden (the job was the movie's)", mg.property("importDescription") == "")
before = {k: mg._map.value(k) for k in want}
mg.importFromInpainting(); pump(0.3)
check("...and an Import fills nothing", {k: mg._map.value(k) for k in want} == before)
check("Inpainting's Import from Splatting: offered with the show picked (the last Splatting job was the show's)",
      ip.property("importDescription") != "")
shell.setProperty("projectFolder", f(movie)); pump(0.4)
check("...hidden with the movie picked", ip.property("importDescription") == "")

h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
print("last_jobs unchanged:", h0 == h1, flush=True)
print("FAILS", len(fails), fails, flush=True)
# through the fixtures: its temp folders removed, and no DLL teardown crash after a green
# run (bug sweep 2026-10-09, A12)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _fixtures  # noqa: E402
_fixtures.exit_now(1 if fails or h0 != h1 else 0)
