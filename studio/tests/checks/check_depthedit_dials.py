"""The Depth Map Editor's controls as dials, in video pixels (user, 2026-10-01:
"is it possible for the adjustment controls to be finer?"). In the real app
offscreen, without SAM 3 (a mask is drawn straight into the layer): every edit
control is a dial; the edge and size settings read in VIDEO pixels in
half-pixel steps and are stored in depth-map pixels (here the source is 2.5x
the map, so -2.5 px shown is -1.0 stored); a fraction renders between its two
whole-pixel neighbours; a dial's reset goes back to the default; the
percentage controls keep their own readout. Configs and last_jobs.json
redirected; synthetic clips in a temp folder."""
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
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from core.depthedit import depth_io, edit_ops
from studio import app as A

# a depth map 256x160 and its source at 2.5x (640x400)
W, H, N = 256, 160, 6
clip_dir = tmp / "clip"; clip_dir.mkdir()
rgb_path = str(clip_dir / "synthetic.mp4").replace("\\", "/")
depth_path = str(clip_dir / "synthetic_depth.mp4").replace("\\", "/")
subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                f"testsrc2=size=640x400:rate=24:duration={N / 24}", "-pix_fmt", "yuv420p", rgb_path], check=True)
info = depth_io.ClipInfo(depth_path, W, H, N, "24/1", "yuv420p10le", False)
writer = depth_io.DepthWriter(depth_path, info)
base = np.linspace(0.1, 0.5, H, dtype=np.float32)[:, None].repeat(W, axis=1)
for f in range(N):
    frame = base.copy(); frame[60:110, 90:170] = 0.85
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

de = objs["DepthEdit"]
stage = find_all(win.contentItem(), lambda c: c.objectName() == "stage")[0]
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 4), Q_ARG("QVariant", True)); pump(0.8)
stage.property("pages").property(4).toQObject().setProperty("tool", 1); pump(0.8)
check("the editor loads", until(lambda: de.property("state").get("ready"), 60))
S = lambda: de.property("state")  # noqa: E731
de.setVar("rgb_path", rgb_path); pump(0.3)
de.setVar("depth_path", depth_path); pump(0.2)
de.setVar("output_dir", str(clip_dir / "edited").replace("\\", "/")); pump(0.2)
de.run("_load_clip")
check("the clip loads", until(lambda: S().get("hasClip") and not S().get("busyJob"), 60))
eng = de._engine
de.run("_new_layer"); pump(0.4)
layer = eng._layer()
for f in range(N):
    m = np.zeros((H, W), bool); m[60:110, 90:170] = True
    layer.masks.set(f, m)
layer.tracked = True
de.setVar("page", "Layer"); pump(0.6)

dials = {c.objectName()[4:]: c for c in find_all(win.contentItem(), lambda c: c.objectName().startswith("ctl_"))}
keys = list(de.property("tables")["layer"]) + list(de.property("tables")["edge"])
check("every layer control is a dial", keys and all(k in dials and dials[k].metaObject().className().startswith("Ctl")
                                                    for k in keys), str(sorted(dials)[:6]))
check("...shown on the Layer page", all(dials[k].isVisible() for k in keys if k in dials))

ctl = de.property("state")["controls"]
check("edge / size settings in video pixels, half-pixel steps",
      all(ctl[k]["step"] == 0.5 and ctl[k]["decimals"] == 1 for k in ("edge_top", "edge_bottom", "grow", "feather", "smooth")),
      str({k: (ctl[k]["from"], ctl[k]["to"], ctl[k]["step"]) for k in ("edge_top", "grow")}))
check("...the feather default (2 depth px) reads 5.0 px at 2.5x", ctl["feather"]["readout"] == "5.0 px", ctl["feather"]["readout"])

def turn(key, video_px):
    dials[key].property("proxy").setProperty("v", video_px); pump(0.4)

turn("edge_top", -2.5)
check("Top edge -2.5 px on the dial is -1.0 depth px stored", abs(layer.settings.edge_top + 1.0) < 1e-6, str(layer.settings.edge_top))
check("...and reads -2.5 px", de.property("state")["controls"]["edge_top"]["readout"] == "-2.5 px",
      de.property("state")["controls"]["edge_top"]["readout"])
turn("edge_top", -3.0)
check("-3.0 px is a fraction of a depth pixel (-1.2)", abs(layer.settings.edge_top + 1.2) < 1e-6, str(layer.settings.edge_top))
unit = eng._unit(0)
mask0 = layer.masks.get(0)
def render(top):
    s = edit_ops.layer_settings_from_dict({**edit_ops.settings_to_dict(layer.settings), "edge_top": top})
    return edit_ops.apply_layer(unit, mask0, s, guide=unit)
r1, r12, r2 = render(-1.0), render(-1.2), render(-2.0)
between = bool(np.all((r12 >= np.minimum(r1, r2) - 1e-6) & (r12 <= np.maximum(r1, r2) + 1e-6)))
check("...rendered between the -1 and -2 pixel edits", between and not np.allclose(r12, r1) and not np.allclose(r12, r2))

turn("grow", 1.5)
check("Grow 1.5 px is 0.6 depth px stored", abs(layer.settings.grow - 0.6) < 1e-6, str(layer.settings.grow))
QMetaObject.invokeMethod(dials["grow"], "reset"); pump(0.4)
check("a dial's reset puts it back to the default", layer.settings.grow == 0.0, str(layer.settings.grow))
turn("brightness", 0.1)
check("a percentage control keeps its own readout", de.property("state")["controls"]["brightness"]["readout"].endswith("%")
      and abs(layer.settings.brightness - 0.1) < 1e-6, de.property("state")["controls"]["brightness"]["readout"])
check("the dial's middle shows the readout", dials["brightness"].property("valueText") == de.property("state")["controls"]["brightness"]["readout"])

# Steady (user, 2026-10-11): whole frames either side, not pixels; off at 0
st = de.property("state")["controls"]["steady"]
check("Steady: a Mask Edge dial in whole frames, off by default",
      "steady" in de.property("tables")["edge"] and st["step"] == 1 and st["decimals"] == 0 and st["readout"] == "off",
      str({k: st[k] for k in ("from", "to", "step", "decimals", "readout")}))
turn("steady", 4)
check("...4 is \u00b14 frames, stored whole",
      layer.settings.steady == 4 and de.property("state")["controls"]["steady"]["readout"] == "\u00b14 fr",
      f"{layer.settings.steady!r} {de.property('state')['controls']['steady']['readout']}")
QMetaObject.invokeMethod(dials["steady"], "reset"); pump(0.4)

check("no QML warnings", not warns, str(warns[:3]))
h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
check("the real last_jobs.json untouched", h0 == h1)
print("PASS" if not fails else f"FAIL ({len(fails)})")
_fixtures.exit_now(0 if not fails else 1)
