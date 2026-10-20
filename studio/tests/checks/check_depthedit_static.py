"""Static reflections and a put-back depth that follows the surface (user,
2026-10-06), real app, offscreen, CPU -- no SAM, no ROSE.

A synthetic locked-off shot: a moving test pattern (the picture on a screen)
with small white shapes that never move (reflections on its glass), and an
Erase layer over the screen. Then:

  * Method: ROSE / Static in the Erase panel; Static needs no ROSE and shows
    only its Size;
  * Erase from Clip with Static: the still shapes come off (the erased clip
    is the picture there), the picture elsewhere is untouched, the original
    kept; the layer is exactly what was still -- with another stage holding
    the clip open until the erase's claim asks it to let go (bug sweep #11);
  * Put back: the depth factor field; Sample makes a parallax for EVERY frame
    from the map under the mask x the factor (the map's screen moves nearer
    over the clip, so the track does too); typing a parallax drops the track;
  * the layer follows the track frame by frame (putback.apply);
  * the Put back row fits the panel, the page fits a 1080p screen at 0.85.

Configs and last_jobs.json redirected.
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
import torch
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, QObject, Q_ARG, QPoint, QPointF, Qt, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from core.depthedit import depth_io
from core.removal import erase as E, putback as P
from studio import app as A

# ---- the shot: a moving pattern, still white shapes on a "screen" ---------------------------
W, H, N = 512, 320, 24
DW, DH = 256, 160
clip_dir = tmp / "clip"; clip_dir.mkdir()
rgb_path = str(clip_dir / "screen.mp4").replace("\\", "/")
depth_path = str(clip_dir / "screen_depth.mp4").replace("\\", "/")
shapes = [(200, 120, 10, 26), (300, 140, 8, 20), (260, 200, 12, 12)]       # x, y, w, h: still on the glass
draw = ",".join(f"drawbox=x={x}:y={y}:w={w}:h={h}:color=white@0.8:t=fill" for x, y, w, h in shapes)
# the picture moves everywhere (nothing of it is still): a scrolling, drifting cloud of colour
PIC = (f"cellauto=s={W}x{H}:rate=24:rule=110:random_fill_ratio=0.5,format=rgb24,"
       f"gblur=sigma=6,scroll=horizontal=0.013:vertical=0.007,hue=h=t*40:s=1.2")
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", PIC, "-frames:v", str(N),
                "-vf", draw, "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "4", rgb_path], check=True)
plain = str(tmp / "plain.mp4")
subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", PIC, "-frames:v", str(N),
                "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "4", plain], check=True)
info = depth_io.ClipInfo(depth_path, DW, DH, N, "24/1", "yuv420p10le", False)
writer = depth_io.DepthWriter(depth_path, info)
for f in range(N):
    frame = np.full((DH, DW), 0.2, np.float32)
    frame[40:120, 80:180] = 0.6 + 0.3 * f / (N - 1)            # the screen comes nearer over the clip
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


def frames(path):
    return np.stack([f.astype(np.float32) for f in E._reader(path, W, H, "rgb24")])


pump(0.6)
de = objs["DepthEdit"]
stage = find(win.contentItem(), lambda c: c.objectName() == "stage")
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 4), Q_ARG("QVariant", True)); pump(0.8)
extra = stage.property("pages").property(4).toQObject()
extra.setProperty("tool", 1); pump(0.8)
page = find(extra, lambda c: c.objectName() == "depthEditPage")
check("the editor loads", until(lambda: de.property("state").get("ready"), 60))
S = lambda: de.property("state")  # noqa: E731
eng = de._engine
de.setVar("rgb_path", rgb_path); pump(0.3)
de.setVar("output_dir", str(clip_dir / "edited").replace("\\", "/")); pump(0.2)
click_in(find(page, lambda c: c.objectName() == "deLoad"))
check("the clip loads", until(lambda: S().get("hasClip") and not S().get("busyJob"), 60))
de.run("_new_layer"); pump(0.3)
layer = eng._layer()
for f in range(N):
    m = np.zeros((DH, DW), bool); m[40:120, 80:180] = True              # the screen
    layer.masks.set(f, m)
layer.tracked = True
de.setVar("page", "Layer"); pump(0.3)
de.setControl("mode", "Erase"); pump(0.5)

er = lambda: S().get("erase") or {}  # noqa: E731
check("Method offers ROSE, Static and Dust (2026-10-17), ROSE first", er().get("methods") == ["ROSE", "Static", "Dust"] and er()["opts"]["method"] == "ROSE",
      str(er().get("methods")))
from core.depthedit import edit_ops as _EO
for _m, _fills in (("Dust", False), ("Static", False), ("ROSE", True), ("Static", False)):
    de.setEraseOption("method", _m); pump(0.2)
    check(f"Method {_m}: an Erase layer {'fills' if _fills else 'leaves'} the depth (2026-10-17)", _EO.ERASE_FILLS_DEPTH is _fills)
de.setEraseOption("method", "Static"); pump(0.4)
opts_row = find(page, lambda c: c.objectName() == "deEraseOptions")
shown = {n for n in ("deErase_static_size", "deErase_steps", "deErase_seed", "deEraseSize", "deEraseWindow", "deEraseMethod")
         if find(page, lambda c, n=n: c.objectName() == n and c.isVisible()) is not None}
check("Static shows the Method and only its Size", er()["opts"]["method"] == "Static"
      and shown == {"deEraseMethod", "deErase_static_size"}, str(sorted(shown)))
btn = find(page, lambda c: c.objectName() == "deEraseFromClip")
check("...and Erase from Clip is on without ROSE", btn is not None and btn.isVisible() and btn.property("enabled"))

# another stage's preview holds the clip open (bug sweep 2026-10-09 #11): the
# Static erase claims like ROSE, so it lets go before the clip is replaced
import threading
from decord import VideoReader, cpu


class Holder:
    def __init__(self, path):
        self.reader = VideoReader(path, ctx=cpu(0))
        self.reader.get_batch([0])

    def holds_gpu(self):
        return self.reader is not None

    def release_gpu(self):
        self.reader = None
        done = threading.Event(); done.set()
        return done


holder = Holder(rgb_path)
de.jobs.register("holder", "Holder", holder)
before = frames(rgb_path)
de.eraseFromClip(); pump(0.3)
check("Static erases", until(lambda: not er().get("task"), 120) and er().get("erased") is True, str(er().get("text")))
after = frames(rgb_path)
ref = frames(plain)
inside = np.zeros((H, W), bool)
for x, y, w, h in shapes:
    inside[y:y + h, x:x + w] = True
screen = np.zeros((H, W), bool); screen[80:240, 160:360] = True
d_before = np.abs(before - ref)[:, inside].mean()
d_after = np.abs(after - ref)[:, inside].mean()
check("the still shapes come off (the erased clip is the picture there)", d_after < d_before * 0.35,
      f"vs the plain picture: {d_before:.1f} before, {d_after:.1f} after (0-255)")
away = screen & ~np.pad(inside, 0)
for x, y, w, h in shapes:
    away[max(0, y - 20):y + h + 20, max(0, x - 20):x + w + 20] = False
# outside the screen the layer is zero: what differs there is the re-encode alone (HEVC 10-bit
# of a noisy synthetic picture); on the screen away from the shapes it must be no more than that
reenc = np.abs(after - before)[:, ~screen].mean()
onscr = np.abs(after - before)[:, away].mean()
check("...outside the screen untouched (the re-encode only)", reenc < 3.0, f"{reenc:.2f}")
check("...the moving picture elsewhere on the screen stays (no more change than the re-encode)", onscr < reenc + 0.3,
      f"{onscr:.2f} vs {reenc:.2f}")
check("the original is kept", (clip_dir / "edit_history" / "screen.original.mp4").is_file())
check("...the stage holding the clip was asked to let go", holder.reader is None)
de.jobs._stages = [e for e in de.jobs._stages if e[2] is not holder]

# ---- Put back at a depth that follows the surface
de.setPutBack("enabled", True); pump(0.2)
fac = find(page, lambda c: c.objectName() == "dePutBackFactor")
check("the depth factor field shows x1.0", fac is not None and fac.isVisible() and fac.property("text") == "×1.0",
      str(fac.property("text") if fac else None))
fac.setProperty("text", "0.5"); QMetaObject.invokeMethod(fac, "editingFinished"); pump(0.3)
check("...0.5 typed is kept", abs(P.load_settings(rgb_path)["factor"] - 0.5) < 1e-9)
_before = P.load_settings(rgb_path)["track"]
de.samplePutBack()                         # a worker job since bug sweep A9: wait for its result
until(lambda: P.load_settings(rgb_path)["track"] != _before and not eng._job_pending())
pump(0.3)
s = P.load_settings(rgb_path)
want = [P.parallax_for_depth((0.6 + 0.3 * f / (N - 1)) * 0.5, 0.5, 20.0) for f in range(N)]
check("Sample: a parallax per frame, x0.5 of the screen's depth (it nears, so the track rises)",
      len(s["track"]) == N and max(abs(a - b) for a, b in zip(s["track"], want)) < 0.03 and s["track"][-1] > s["track"][0],
      f"{s['track'][0]:+.3f}..{s['track'][-1]:+.3f}, want {want[0]:+.3f}..{want[-1]:+.3f}")
check("...the page says it's per frame", (er().get("putback") or {}).get("tracked") is True)
pb = P.find(str(clip_dir), "screen")
# a Static erase is Add only (bug sweep A9: Over laid the whole glass area over both eyes)
check("a Static erase offers put-back Add only", (er().get("putback") or {}).get("modes") == ["Add"]
      and pb.modes == ("Add",), str((er().get("putback") or {}).get("modes")))
pb.settings["mode"] = "Add"
bar = np.zeros((1, H, W, 3), np.uint8); bar[:, :, 250:262] = 255            # the same bar on every frame
_none = np.zeros_like(bar)
# Add puts back original - erased under the repaint mask: the bar, from an erased clip without it
pb._read = lambda path, idx, w, h: np.repeat(_none if path == pb.clip else bar, len(idx), 0)
z = torch.zeros(1, 3, H, W)
_l, r0 = pb.apply(z.clone(), z.clone(), 0)
_l, r1 = pb.apply(z.clone(), z.clone(), N - 2)
cx = lambda t: float((t[0].mean(0).sum(0) * torch.arange(W)).sum() / t[0].mean(0).sum().clamp(min=1e-6))  # noqa: E731
check("the layer follows the track frame by frame", abs((cx(r0) - cx(r1)) - (s["track"][N - 2] - s["track"][0]) / 100 * W) < 0.2,
      f"moved {cx(r0) - cx(r1):.2f} px, track says {(s['track'][N - 2] - s['track'][0]) / 100 * W:.2f}")
par = find(page, lambda c: c.objectName() == "dePutBackParallax")
par.setProperty("text", "0.4"); QMetaObject.invokeMethod(par, "editingFinished"); pump(0.3)
check("typing a parallax: one value, the track dropped", P.load_settings(rgb_path)["track"] == []
      and abs(P.load_settings(rgb_path)["parallax"] - 0.4) < 1e-9)

row = find(page, lambda c: c.objectName() == "dePutBack")
panel = find(page, lambda c: c.objectName() == "deErasePanel")
check("the Put back row fits the panel", row is not None and row.implicitWidth() <= panel.width() + 0.5,
      f"{row.implicitWidth():.0f} in {panel.width():.0f}")
min_h = win.property("minimumHeight")
check("the page fits the 1080p screen at 0.85", min_h * 0.85 <= 1000, f"{min_h} -> {min_h * 0.85:.0f}")
de.setEraseOption("method", "ROSE"); pump(0.5)
check("the clip's own Static erase decides: ROSE chosen, its Erase layer still leaves the depth (review 2026-10-17)",
      _EO.ERASE_FILLS_DEPTH is False)
min_h = win.property("minimumHeight")
check("...with ROSE's settings showing too", min_h * 0.85 <= 1000, f"{min_h} -> {min_h * 0.85:.0f}")
meth = find(page, lambda c: c.objectName() == "deEraseMethod")
check("the Method choice is beside Erase from Clip, always there", meth is not None and meth.isVisible())

studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
