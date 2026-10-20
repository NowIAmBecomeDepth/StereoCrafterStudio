"""Erase in the Depth Map Editor (user, 2026-10-05), real app, offscreen.

A layer's Mask set to Erase: the depth inside the mask is filled from around
it (the edited depth follows the ramp behind the box, not the box); the
Erase panel shows with Erase from Clip; Erase from Clip runs ROSE (for real,
on the GPU, at 4 steps typed into the Erase panel's Steps) on the synthetic
clip, which is replaced in place with the original kept in edit_history, and
the page says the clip has been erased. Configs and last_jobs.json
redirected. Needs ROSE set up (core.removal.rose_env) and ~5 GB of GPU.
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
import numpy as np
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, QObject, Q_ARG, QPoint, QPointF, Qt, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from core.depthedit import depth_io
from core.removal import rose_env
from studio import app as A

# ---- the synthetic clip: a box in front of a ramp, moving right ----------------------
W, H, N = 256, 160, 24
clip_dir = tmp / "clip"; clip_dir.mkdir()
rgb_path = str(clip_dir / "synthetic.mp4").replace("\\", "/")
depth_path = str(clip_dir / "synthetic_depth.mp4").replace("\\", "/")
subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                f"testsrc2=size=512x320:rate=24:duration={N / 24}", "-pix_fmt", "yuv420p", rgb_path], check=True)
info = depth_io.ClipInfo(depth_path, W, H, N, "24/1", "yuv420p10le", False)
writer = depth_io.DepthWriter(depth_path, info)
ramp = np.linspace(0.1, 0.6, W, dtype=np.float32)[None, :].repeat(H, axis=0)
for f in range(N):
    frame = ramp.copy()
    frame[40:120, 80 + f:160 + f] = 0.9
    writer.write(depth_io.from_unit(frame, info))
writer.close()

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
objs["Shell"].setProperty("uiScale", "1.0")
win.show()
fails = []


def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok:
        fails.append(label)


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
        if pred(c):
            return c
        r = find(c, pred)
        if r is not None:
            return r


def click_in(item):
    QTest.mouseMove(win, QPoint(5, 5)); pump(0.1)
    c = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(c.x()), int(c.y())))


pump(0.6)
de = objs["DepthEdit"]
stage = find(win.contentItem(), lambda c: c.objectName() == "stage")
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 4), Q_ARG("QVariant", True)); pump(0.8)
extra = stage.property("pages").property(4).toQObject()
extra.setProperty("tool", 1); pump(0.8)
page = find(extra, lambda c: c.objectName() == "depthEditPage")
check("the editor loads", until(lambda: de.property("state").get("ready"), 60), str(de.property("state").get("loadError")))
S = lambda: de.property("state")  # noqa: E731
eng = de._engine
check("the Mask modes are Alter, Add and Erase", list(de.property("choices")["mode"]) == ["Alter", "Add", "Erase"],
      str(de.property("choices")["mode"]))

de.setVar("rgb_path", rgb_path); pump(0.3)
de.setVar("output_dir", str(clip_dir / "edited").replace("\\", "/")); pump(0.2)
click_in(find(page, lambda c: c.objectName() == "deLoad"))
check("the clip loads", until(lambda: S().get("hasClip") and not S().get("busyJob"), 60))
de.run("_new_layer"); pump(0.3)
layer = eng._layer()
for f in range(N):
    m = np.zeros((H, W), bool); m[40:120, 80 + f:160 + f] = True
    layer.masks.set(f, m)
layer.tracked = True
de.setVar("page", "Layer"); pump(0.3)
de.setControl("mode", "Erase"); pump(0.5)
check("the layer is Erase", eng._layer().settings.mode == "Erase")
e5 = eng._edited_unit(5)
inside = e5[50:110, 95:175]
check("the box's depth is filled from around it (the ramp behind, not 0.9)",
      float(np.abs(inside - ramp[50:110, 95:175]).mean()) < 0.03 and float(inside.max()) < 0.6,
      f"mean diff {float(np.abs(inside - ramp[50:110, 95:175]).mean()):.3f}, max {float(inside.max()):.3f}")
check("...outside it, as before", float(np.abs(e5[:30] - ramp[:30]).max()) < 0.01)
panel = find(page, lambda c: c.objectName() == "deErasePanel")
check("the Erase panel shows", panel is not None and panel.isVisible())
er = S().get("erase") or {}
check("ROSE is ready here, one Erase layer, a clip", er.get("ready") is True and er.get("layers") == 1 and er.get("clip") is True,
      str(er))
btn = find(page, lambda c: c.objectName() == "deEraseFromClip")
check("Erase from Clip is on", btn is not None and btn.isVisible() and btn.property("enabled"))
opts = find(page, lambda c: c.objectName() == "deEraseOptions")
fields = {k: find(page, lambda c, k=k: c.objectName() == "deErase_" + k) for k in ("steps", "seed", "grow", "feather")}
check("ROSE's settings show: Steps, Seed, Grow, Feather, Size, Window",
      opts is not None and opts.isVisible() and all(f is not None for f in fields.values())
      and find(page, lambda c: c.objectName() == "deEraseSize") is not None
      and find(page, lambda c: c.objectName() == "deEraseWindow") is not None)
check("...with their defaults", (S()["erase"]["opts"]) == {"method": "ROSE", "steps": 50, "seed": 42, "size": "480p", "window": 81,
                                                       "grow": 12, "feather": 6, "static_size": 31,
                                                       "prefill": "Auto", "refine": False,
                                                       "dust_size": 9},      # Dust (2026-10-17)
      str(S()["erase"]["opts"]))
fields["steps"].setProperty("text", "4"); QMetaObject.invokeMethod(fields["steps"], "editingFinished"); pump(0.3)
check("typing 4 into Steps sets it", S()["erase"]["opts"]["steps"] == 4 and fields["steps"].property("text") == "4",
      str(S()["erase"]["opts"]))
de.setEraseOption("steps", "500"); pump(0.2)
check("...a value past the range is held to it", S()["erase"]["opts"]["steps"] == 200)
de.setEraseOption("window", 50); pump(0.2)
check("...a window that isn't offered is refused", S()["erase"]["opts"]["window"] == 81)
de.setEraseOption("steps", 4); pump(0.2)
saved = json.loads((tmp / "config" / "depthedit_erase.json").read_text(encoding="utf-8"))
check("...and they are saved", saved.get("steps") == 4, str(saved))
pump(0.5)
min_h = win.property("minimumHeight")
check("the Layer page in Erase fits the 1080p screen at 0.85", min_h * 0.85 <= 1000, f"{min_h} -> {min_h * 0.85:.0f}")

if rose_env.ready():
    t0 = time.time()
    de.eraseFromClip(); pump(0.5)
    check("while it runs the editor is busy, with progress", de.is_busy() and (S().get("erase") or {}).get("task") == "erase")
    ok = until(lambda: not (S().get("erase") or {}).get("task"), 900)
    print(f"  erase took {time.time() - t0:.0f}s")
    hist = clip_dir / "edit_history"
    check("it finishes", ok)
    check("the original is kept in edit_history", (hist / "synthetic.original.mp4").is_file(),
          str(list(hist.iterdir()) if hist.is_dir() else "no edit_history"))
    check("...the clip replaced in place", Path(rgb_path).is_file() and (hist / "synthetic.mp4.erase.json").is_file())
    check("the page says the clip has been erased", (S().get("erase") or {}).get("erased") is True)
    check("the editor isn't busy after", not de.is_busy())

    # ---- the object put back (user, 2026-10-06; core.removal.putback)
    from core.removal import putback as P
    pump(0.5)
    row = find(page, lambda c: c.objectName() == "dePutBack")
    check("once erased, the Put back row shows", row is not None and row.isVisible())
    pb = (S().get("erase") or {}).get("putback") or {}
    check("...off, Add, at the screen plane", pb.get("enabled") is False and pb.get("mode") == "Add" and pb.get("parallax") == 0.0,
          str(pb))
    de.setPutBack("enabled", True); de.setPutBack("mode", "Over"); pump(0.2)
    par = find(page, lambda c: c.objectName() == "dePutBackParallax")
    par.setProperty("text", "1.5"); QMetaObject.invokeMethod(par, "editingFinished"); pump(0.3)
    saved = P.load_settings(rgb_path)
    check("on, Over, 1.5% typed: saved beside the clip for Merging",
          saved["enabled"] and saved["mode"] == "Over" and abs(saved["parallax"] - 1.5) < 1e-9
          and Path(P.settings_path(rgb_path)).is_file(), str(saved))
    eng.frame_idx = 5
    de.samplePutBack(); pump(0.3)
    check("Sample: the box's depth (0.9) at Splatting's defaults -> +0.80%",
          abs(P.load_settings(rgb_path)["parallax"] - 0.8) < 0.02, str(P.load_settings(rgb_path)))
    check("Merging finds it", P.find(str(clip_dir), "synthetic") is not None)
    min_h = win.property("minimumHeight")
    check("with the Put back row the page still fits the 1080p screen at 0.85", min_h * 0.85 <= 1000,
          f"{min_h} -> {min_h * 0.85:.0f}")

studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
if not fails and _lj and not rose_env.ready():
    # the page passed, but the erase and put-back half never ran: not a pass (bug sweep A12)
    _fixtures.skip("ROSE is not set up, so the erase and put-back half didn't run (the page's steps passed)")
_fixtures.exit_now(0 if not fails and _lj else 1)
