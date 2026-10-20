"""Despeck and Highlight Specks in the Depth Map Editor (user, 2026-10-12), real app, offscreen.

A synthetic clip: two boxes drifting right, each with a bright speck just inside
its LEFT edge (a stand-in for DepthCrafter's). A layer on the first box, Alter with
Despeck (off until turned on; it was a Mask mode of its own until the user asked for it
in Alter): in the Edited view its speck comes down to the box's depth on every frame,
the other box's speck stays, nothing else moves; it stays on through Add and back;
Strength 50% takes it half way; off brings it back. Specks (Highlight
Specks): the covered speck orange, the uncovered one cyan, the rest of the picture
as it was; off, the picture is back. The page still fits. Configs and
last_jobs.json redirected; no GPU model.
"""
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
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, QPoint, QPointF, Qt, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from core.depthedit import depth_io
from studio import app as A

# ---- the synthetic clip -------------------------------------------------------------------
W, H, N = 512, 256, 12
BG, BOX, SPECK = 0.10, 0.50, 0.75
clip_dir = tmp / "clip"; clip_dir.mkdir()
rgb_path = str(clip_dir / "synthetic.mp4").replace("\\", "/")
depth_path = str(clip_dir / "synthetic_depth.mp4").replace("\\", "/")
subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                f"testsrc2=size={W}x{H}:rate=24:duration={N / 24}", "-pix_fmt", "yuv420p", rgb_path], check=True)
info = depth_io.ClipInfo(depth_path, W, H, N, "24/1", "yuv420p10le", False)
writer = depth_io.DepthWriter(depth_path, info)
A0, B0 = 80, 320                                   # the boxes' left edges at frame 0


def box_x(f, x0):
    return x0 + 2 * f


for f in range(N):
    frame = np.full((H, W), BG, np.float32)
    for x0 in (A0, B0):
        x = box_x(f, x0)
        frame[60:200, x:x + 120] = BOX
        frame[110:150, x:x + 5] = SPECK
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


def picture():
    img = de.image_for_test().convertToFormat(de.image_for_test().Format.Format_RGB888)
    a = np.frombuffer(img.constBits(), np.uint8, img.bytesPerLine() * img.height()).reshape(img.height(), img.bytesPerLine())
    a = a[:, : img.width() * 3].reshape(img.height(), img.width(), 3).copy()
    return a, (lambda x, y: a[int(y * a.shape[0] / H), int(x * a.shape[1] / W)].astype(int))


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

de.setVar("rgb_path", rgb_path); pump(0.3)
de.setVar("output_dir", str(clip_dir / "edited").replace("\\", "/")); pump(0.2)
click_in(find(page, lambda c: c.objectName() == "deLoad"))
check("the clip loads", until(lambda: S().get("hasClip") and not S().get("busyJob"), 60))
de.run("_new_layer"); pump(0.3)
layer = eng._layer()
for f in range(N):
    m = np.zeros((H, W), bool); m[60:200, box_x(f, A0):box_x(f, A0) + 120] = True
    layer.masks.set(f, m)
layer.tracked = True
layer.settings.edge_snap = 0
de.setVar("page", "Layer"); pump(0.3)
check("the Mask modes are Alter, Add and Erase (Despeck is in Alter)",
      list(de.property("choices")["mode"]) == ["Alter", "Add", "Erase"], str(de.property("choices")["mode"]))
row = find(page, lambda c: c.objectName() == "deDespeckRow")
sw = find(page, lambda c: c.objectName() == "deDespeckOn")
dial = find(page, lambda c: c.property("ctlKey") == "despeck_strength")
check("Alter shows Despeck, off, with its Strength (greyed) beside it",
      row is not None and row.isVisible() and not sw.property("checked") and dial is not None
      and not dial.property("enabled") and eng._layer().settings.mode == "Alter")
check("...off: nothing changes", float(np.abs(eng._edited_unit(6) - eng._unit(6)).max()) < 1e-6)
de.setControl("despeck_on", True); pump(0.5)          # what the switch sends
check("turned on: the switch on, Strength live at 100%", sw.property("checked") and dial.property("enabled")
      and eng._layer().settings.despeck_strength == 1.0)
de.setControl("mode", "Add"); pump(0.4)
check("in Add the row is hidden", not row.isVisible())
de.setControl("mode", "Alter"); pump(0.4)
check("back in Alter it is still on (it went off as a mode of its own)",
      eng._layer().settings.despeck_on and sw.property("checked") and row.isVisible())

for f in (0, 6, 11):
    raw, ed = eng._unit(f), eng._edited_unit(f)
    xa, xb = box_x(f, A0), box_x(f, B0)
    check(f"frame {f}: the covered speck down to its box", abs(float(ed[115:145, xa:xa + 5].max()) - BOX) < 0.03,
          f"{float(raw[115:145, xa:xa + 5].max()):.3f} -> {float(ed[115:145, xa:xa + 5].max()):.3f}")
    check(f"frame {f}: the other box's speck stays (no layer there)",
          abs(float(ed[115:145, xb:xb + 5].max()) - float(raw[115:145, xb:xb + 5].max())) < 1e-4)
    away = np.ones((H, W), bool); away[100:160, xa - 4:xa + 14] = False
    check(f"frame {f}: nothing else moves", float(np.abs(ed - raw)[away].max()) < 2e-3,
          f"{float(np.abs(ed - raw)[away].max()):.4f}")
eng.frame_idx = 6
check("...with Alter's own dials there too", find(page, lambda c: c.property("ctlKey") == "brightness"
      and c.isVisible()) is not None)
de.setControl("despeck_strength", 0.5); pump(0.4)
ed = eng._edited_unit(6); xa = box_x(6, A0)
check("Strength 50%: half way", abs(float(ed[115:145, xa:xa + 5].max()) - (SPECK + BOX) / 2) < 0.04,
      f"{float(ed[115:145, xa:xa + 5].max()):.3f}")
de.setControl("despeck_strength", 1.0); pump(0.4)
de.setControl("despeck_on", False); pump(0.5)          # what the switch sends
ed = eng._edited_unit(6)
check("switched off: the speck is back", not eng._layer().settings.despeck_on
      and abs(float(ed[115:145, xa:xa + 5].max()) - SPECK) < 0.01 and not sw.property("checked"),
      f"{float(ed[115:145, xa:xa + 5].max()):.3f}, switch {sw.property('checked')}")
de.setControl("despeck_on", True); pump(0.5)
check("...and on again", eng._layer().settings.despeck_on
      and abs(float(eng._edited_unit(6)[115:145, xa:xa + 5].max()) - BOX) < 0.03)

# ---- Highlight Specks ------------------------------------------------------------------
de.setVar("overlay", False); de.setVar("view", "Depth"); de.setViewOption("dither", False); pump(0.5)
toggle = find(page, lambda c: c.objectName() == "deSpecks")
check("the Specks toggle shows", toggle is not None and toggle.isVisible())
plain, at = picture()
xa, xb = box_x(6, A0), box_x(6, B0)
de.setViewOption("specks", True); pump(0.6)
lit, at = picture()
pa, pb, pin = at(xa + 2, 130), at(xb + 2, 130), at(xa + 60, 130)
check("Specks: the covered speck orange", pa[0] > 200 and 90 < pa[1] < 190 and pa[2] < 80, str(pa))
check("...the uncovered one cyan", pb[0] < 80 and pb[1] > 150 and pb[2] > 200, str(pb))
check("...the rest of the picture as it was", np.abs(lit.astype(int) - plain.astype(int))[:, :].max(axis=2)[
      int(170 * lit.shape[0] / H):].max() <= 1 and abs(pin[0] - plain[int(130 * lit.shape[0] / H), int((xa + 60) * lit.shape[1] / W)][0]) <= 1)
de.setVar("view", "RGB"); pump(0.6)
_rgb, at = picture()
check("...in the RGB view too", at(xb + 2, 130)[2] > 200 and at(xb + 2, 130)[0] < 80, str(at(xb + 2, 130)))
de.setVar("view", "Depth"); de.setViewOption("specks", False); pump(0.6)
back, _at = picture()
check("off: the picture is back", int(np.abs(back.astype(int) - plain.astype(int)).max()) <= 1)
pump(0.5)
# ---- Level to edge (user, 2026-10-12): down to the grey round the mask's edge ---------------------
de.setControl("despeck_on", False); pump(0.3)
le = find(page, lambda c: c.objectName() == "deLevelEdge")
check("Level to edge shows beside Despeck, off", le is not None and le.isVisible() and not le.property("checked"))
de.setControl("feather", 0); pump(0.3)      # the speck sits on the mask's edge: no feather there
de.setControl("level_edge", True); pump(0.5)
ed = eng._edited_unit(6); xa = box_x(6, A0)
check("on: the box's speck down to the grey round its edge (the box's)", le.property("checked")
      and abs(float(ed[115:145, xa:xa + 5].max()) - BOX) < 0.03 and dial.property("enabled"),
      f"{float(ed[115:145, xa:xa + 5].max()):.3f}")
de.setControl("level_edge", False); pump(0.4)
de.setControl("feather", 2); pump(0.3)

# ---- the Global Despeck (user, 2026-10-12): the whole frame, no layer ----------------------
de.setControl("despeck_on", False); pump(0.3)          # the layer's off: only the global one acts
de.setVar("page", "Global"); pump(0.5)
grow = find(page, lambda c: c.objectName() == "deGlobalDespeckRow")
gsw = find(page, lambda c: c.objectName() == "deGlobalDespeckOn")
gdial = find(page, lambda c: c.property("ctlKey") == "global.despeck_strength")
check("the Global page has Despeck the whole frame, off, its Strength greyed",
      grow is not None and grow.isVisible() and not gsw.property("checked") and gdial is not None
      and not gdial.property("enabled"))
de.setControl("global.despeck_on", True); pump(0.5)
ed = eng._edited_unit(6); xa, xb = box_x(6, A0), box_x(6, B0)
check("on: both boxes' specks come down, no layer needed",
      abs(float(ed[115:145, xa:xa + 5].max()) - BOX) < 0.03 and abs(float(ed[115:145, xb:xb + 5].max()) - BOX) < 0.03
      and gsw.property("checked") and gdial.property("enabled"),
      f"{float(ed[115:145, xa:xa + 5].max()):.3f} / {float(ed[115:145, xb:xb + 5].max()):.3f}")
check("...saved with the project's whole-frame settings", eng.project.global_settings.despeck_on is True)
de.setVar("overlay", False); de.setVar("view", "Depth"); de.setViewOption("specks", True); pump(0.6)
_g, at = picture()
pb = at(xb + 2, 130)
check("Specks: the other box's speck orange now (covered)", pb[0] > 200 and pb[2] < 80, str(pb))
de.setViewOption("specks", False)
# its zone (user, 2026-10-12): Left at 50% -- only the right-hand box
de.setVar("page", "Global"); pump(0.3)
de.setControl("global.despeck_left", 0.5); pump(0.6)
ed = eng._edited_unit(6)
check("Left 50%: the right-hand box's speck down, the left one's as it was",
      abs(float(ed[115:145, xb:xb + 5].max()) - BOX) < 0.03
      and abs(float(ed[115:145, xa:xa + 5].max()) - SPECK) < 0.01,
      f"{float(ed[115:145, xa:xa + 5].max()):.3f} / {float(ed[115:145, xb:xb + 5].max()):.3f}")
zone = find(page, lambda c: c.objectName() == "deDespeckZone")
z = de.property("markers").get("zone")
rows = [find(page, lambda c, n=n: c.objectName() == n) for n in ("deGlobalDespeckDials", "deGlobalDespeckZone")]
keys_in = lambda r: [c.property("ctlKey") for c in r.childItems() if c.property("ctlKey")]  # noqa: E731
check("its dials in two rows: Strength / Threshold / Reach / Edge, then Left / Right / Top / Bottom",
      all(rows) and keys_in(rows[0]) == ["global.despeck_strength", "global.despeck_threshold", "global.despeck_reach", "global.despeck_step"]
      and keys_in(rows[1]) == ["global.despeck_left", "global.despeck_right", "global.despeck_top", "global.despeck_bottom"]
      and rows[1].y() > rows[0].y(), str([keys_in(r) for r in rows if r]))
check("...the zone drawn on the picture", zone is not None and zone.isVisible() and z is not None
      and abs(z[0] - 0.5) < 1e-6, str(z))
de.setViewOption("specks", True); pump(0.6)
_g, at = picture()
pa, pb = at(xa + 2, 130), at(xb + 2, 130)
check("...Specks: the left one cyan (outside), the right one orange",
      pa[2] > 200 and pa[0] < 80 and pb[0] > 200 and pb[2] < 80, f"{pa} / {pb}")
de.setViewOption("specks", False)
de.setControl("global.despeck_left", 0.0); pump(0.4)
de.setControl("global.despeck_on", False); pump(0.5)
check("off: back as it was", float(np.abs(eng._edited_unit(6) - eng._unit(6)).max()) < 1e-6)
pump(0.5)
# ---- Local Match (user, 2026-10-13): every frame's coarse depth matched to a picked frame -----
de.setVar("page", "Global"); pump(0.4)
mon, muse, mlab = (find(page, lambda c, n=n: c.objectName() == n) for n in ("deMatchOn", "deMatchUse", "deMatchFrame"))
check("the Global page has Local Match, off, Use This Frame greyed",
      mon is not None and muse is not None and mlab is not None and not muse.property("enabled")
      and eng.project.global_settings.match_on is False)
de.setControl("global.match_on", True); pump(0.4)
eng.frame_idx = 3
de.matchUseThisFrame(); pump(0.6)
g = eng.project.global_settings
check("...on, Use This Frame on frame 4: the reference is that frame",
      g.match_on and g.match_frame == 3 and muse.property("enabled") and mlab.property("text") == "Reference: frame 4",
      f"{g.match_frame} / {mlab.property('text')!r}")
de._ensure_match_ref()
check("...its depth loaded from the map", getattr(g, "_match_ref_frame", None) == 3
      and np.array_equal(g._match_ref, eng._unit(3)))
check("...the reference frame itself is unchanged", float(np.abs(eng._edited_unit(3) - eng._unit(3)).max()) < 1e-6)
_moved = float(np.abs(eng._edited_unit(6) - eng._unit(6)).max())
check("...another frame is drawn through it (no error)", np.isfinite(_moved), f"{_moved:.4f}")
# bug sweep 2026-10-14c: the Specks view reads the map as Despeck gets it -- Local Match first
_E = de._module.edit_ops
_E.set_match_reference(g, 3, np.clip(eng._unit(3) + 0.2, 0, 1))   # a reference this frame differs from
_seen, _real = [], _E.speck_highlight
_E.speck_highlight = lambda v, *a, **k: (_seen.append(np.array(v)), _real(v, *a, **k))[1]
try:
    de._speck_tint(np.zeros((eng.info.height, eng.info.width, 3), np.uint8), 6)
finally:
    _E.speck_highlight = _real
_want = _E.local_match(eng._unit(6), g._match_ref, float(g.match_strength))
check("...the Specks view reads the frame through Local Match too", _seen and np.allclose(_seen[-1], _want)
      and not np.allclose(_want, eng._unit(6)), f"moved {float(np.abs(_want - eng._unit(6)).max()):.3f}")
_E.set_match_reference(g, None, None); de._ensure_match_ref()             # the real reference back
eng.frame_idx = 6
de.matchUseThisFrame(); pump(0.4)
de.undo(); pump(0.4)
de._ensure_match_ref()
check("...Undo puts the previous reference back", g.match_frame == 3 or eng.project.global_settings.match_frame == 3,
      str(eng.project.global_settings.match_frame))
de.setControl("global.match_on", False); pump(0.5)
check("...off: back as it was", float(np.abs(eng._edited_unit(6) - eng._unit(6)).max()) < 1e-6)
check("...and nothing of it saved in the project file but its three settings",
      not any(k.startswith("_") for k in de._module.edit_ops.settings_to_dict(eng.project.global_settings)))
min_h = win.property("minimumHeight")
check("the Layer page with Despeck in Alter fits the 1080p screen at 0.85", min_h * 0.85 <= 1000, f"{min_h} -> {min_h * 0.85:.0f}")

studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
