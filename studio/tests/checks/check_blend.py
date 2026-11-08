"""Depth Map's Blend tab (user, 2026-10-26: "let's build a non model approach" to blending DepthCrafter and VDA
maps), real app, offscreen. A synthetic depth folder: clipA's DepthCrafter map where Splatting reads it, its VDA map
(a different size, scaled and shifted) in VDA/ beside it; clipB with a DepthCrafter map only.

* the tab: third under Depth Map; it lists clipA only (clipB has no VDA map);
* the preview: DepthCrafter | VDA | Blend at a frame, redrawn for a frame or a setting changed;
* Blend All: clipA's blend where Splatting reads it, tagged; the DepthCrafter map moved into DepthCrafter/, the
  VDA map left in VDA/; the clip still listed (both its maps found in their folders); a second blend replaces
  the first, nothing more set aside; its progress as bars in the console's Depth Map tab;
* Same as Splatting takes Splatting's folder.

Runs on the CPU (CUDA hidden, libx265 not NVENC): no GPU needed, none touched. Configs and last_jobs.json
redirected.
"""
import os, sys, tempfile, hashlib, time, json
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
os.environ["CUDA_VISIBLE_DEVICES"] = "-1"       # -1, not empty: empty leaves CUDA visible here
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, str(Path(__file__).parent))
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
import _fixtures
import numpy as np
from core.depthedit import depth_io as D
D._NVENC_OK = False                              # libx265 on the CPU
from core.vda import output as OUT

DM = tmp / "depth"; (DM / "VDA").mkdir(parents=True)
def write(path, w, h, n, f, tag=None):
    info = D.ClipInfo(str(path), w, h, n, "24/1", "yuv420p10le", False)
    wr = D.DepthWriter(str(path), info, metadata={"comment": tag} if tag else None)
    for i in range(n):
        wr.write(D.from_unit(f(i, w, h), info))
    wr.close()
def ramp(i, w, h):
    x = np.linspace(0.1, 0.9, w, dtype=np.float32)[None, :].repeat(h, axis=0)
    return x * (1 - 0.01 * i)
write(DM / "clipA_depth.mp4", 256, 160, 24, ramp)                                          # DepthCrafter's
write(DM / "VDA" / "clipA_depth.mp4", 320, 192, 24, lambda i, w, h: 0.5 * ramp(i, w, h) + 0.2, OUT.TAG)
write(DM / "clipB_depth.mp4", 256, 160, 24, ramp)                                          # no VDA map

from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication, QImage
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
def one(name):
    got = [c for c in find_all(win.contentItem(), lambda c: c.objectName() == name) if c.isVisible()]
    return got[0] if got else None
n = lambda p: os.path.normcase(os.path.normpath(str(p)))
pump(1.0)
bl = objs["Blend"]; st = bl.property("settings")
toasts = []
bl.toast.connect(lambda *a: toasts.append(a))
stage = find_all(win.contentItem(), lambda c: c.objectName() == "stage")[0]
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 0), Q_ARG("QVariant", True)); pump(0.5)
dcpage = stage.property("pages").property(0).toQObject()
dcpage.setProperty("engine", 3); pump(0.8)          # Blend: the fourth tab since DVD (2026-11-07)
check("Depth Map's fourth tab is Blend", one("blendPage") is not None and one("pageContent") is None and one("vdaPage") is None
      and one("dvdPage") is None)

st.setProperty("depth_folder", str(DM)); pump(0.5)
check("it lists clipA only (clipB has no VDA map)", list(bl.property("clips")) == ["clipA"], str(list(bl.property("clips"))))
check("the preview: DepthCrafter | VDA | Blend at a frame", until(lambda: bl.property("previewImage") != "" and not bl.property("previewBusy"), 120)
      and bl.property("previewFrames") == 24, f"{bl.property('previewImage')} {bl.property('previewFrames')} {bl.property('previewMessage')}")
img = QImage(bl.property("previewImage").replace("file:///", ""))
check("...three panels side by side", not img.isNull() and abs(img.width() - 3 * img.height() * 320 / 192) < 6,
      f"{img.width()}x{img.height()}")
first = bl.property("previewImage")
bl.setPreviewFrame(20); pump(0.2)
check("...redrawn for another frame", until(lambda: bl.property("previewImage") != first and not bl.property("previewBusy"), 60))
second = bl.property("previewImage")
st.setProperty("mode", "Layout + Detail"); pump(0.2)
check("...and for a setting changed (Layout + Detail)", until(lambda: bl.property("previewImage") != second and not bl.property("previewBusy"), 60)
      and one("blendDetailSize") is not None and one("blendMix") is None)
check("...Layout + Detail shows Edge Keep, Detail Limit and Noise Floor (user, 2026-11-07)",
      all(one(n) is not None for n in ("blendEdgeKeep", "blendDetailLimit", "blendDetailFloor"))
      and one("blendByDepth") is None)
st.setProperty("mode", "Mix"); pump(0.5)
check("...Mix shows Mix by Depth, off: one Mix", one("blendByDepth") is not None and one("blendMix") is not None
      and one("blendMixNear") is None and one("blendEdgeKeep") is None)
third = bl.property("previewImage")
st.setProperty("mix_by_depth", True); pump(0.3)
check("...by depth: Near Mix, Far Mix, Split and Softness instead, and the preview redrawn",
      all(one(n) is not None for n in ("blendMixNear", "blendMixFar", "blendMixSplit", "blendMixSoft"))
      and one("blendMix") is None
      and until(lambda: bl.property("previewImage") != third and not bl.property("previewBusy"), 60))
st.setProperty("mix_by_depth", False); pump(0.3)
check("...Edge Band offered, off; its edges only once it is on", one("blendEdgeBand") is not None
      and one("blendEdgeBandSides") is None)
st.setProperty("edge_band", 24.0); pump(0.3)
check("...on, its edges: Left + Top", one("blendEdgeBandSides") is not None
      and st.value("edge_band_sides") == "Left + Top")
st.setProperty("edge_band", 0.0); pump(0.5)
check("...Levels offered: Black Point, White Point, Gamma (user, 2026-11-07)",
      all(one(n) is not None for n in ("blendLevelBlack", "blendLevelWhite", "blendLevelGamma")))
st.setProperty("mode", "Layout + Detail"); pump(0.3)
fourth = bl.property("previewImage")
st.setProperty("detail_smooth", 2.0); pump(0.3)
check("...Layout + Detail: Smoothing, and the preview redrawn with it (the frames around read)",
      one("blendDetailSmooth") is not None
      and until(lambda: bl.property("previewImage") != fourth and not bl.property("previewBusy"), 60)
      and not bl.property("previewMessage"), str(bl.property("previewMessage")))
st.setProperty("detail_smooth", 0.0); st.setProperty("mode", "Mix"); pump(0.5)

# a clip's own settings (user, 2026-11-07)
own_file = DM / "blend_clips.json"
check("Own Settings offered, off", one("blendOwn") is not None and not bl.property("previewOwn")
      and list(bl.property("clipLabels")) == ["clipA"])
bl.setOwn(True); pump(0.3)
st.setProperty("mix", 0.9); pump(1.0)
check("...on: the panel is clipA's, its Mix kept beside the maps, the shared Mix as it was",
      bl.property("previewOwn") and "clipA" in one("blendSettings").property("title")
      and list(bl.property("clipLabels")) == ["clipA  (own settings)"]
      and own_file.exists() and json.loads(own_file.read_text())["clipA"]["mix"] == 0.9
      and bl._global["mix"] == 0.5, f"{bl._global.get('mix')} {own_file.exists()}")
bl.setOwn(False); pump(0.5)
check("...off: back on the shared settings, the file gone", not bl.property("previewOwn")
      and float(st.value("mix")) == 0.5 and not own_file.exists(), str(st.value("mix")))

toasts.clear()
bl.start()
check("Blend All runs", until(lambda: bl.property("busy"), 20))
until(lambda: not bl.property("busy"), 300); pump(1.0)
check("clipA's blend is where Splatting reads it, tagged", OUT.kind_of(str(DM / "clipA_depth.mp4")) == "blend", str(toasts[-1:]))
check("...its DepthCrafter map moved into DepthCrafter/, the VDA map left in VDA/",
      (DM / "DepthCrafter" / "clipA_depth.mp4").is_file() and (DM / "VDA" / "clipA_depth.mp4").is_file(),
      str(sorted(os.listdir(DM))))
info = D.probe(str(DM / "clipA_depth.mp4"))
check("...the larger map's size, every frame", (info.width, info.height, info.num_frames) == (320, 192, 24),
      f"{info.width}x{info.height} {info.num_frames}")
u = D.to_unit(next(D.iter_codes(info)), info)
check("...two maps of one layout, aligned and mixed: DepthCrafter's depth back (within 2 codes)",
      float(np.abs(u[:, 8:-8] - ramp(0, 320, 192)[:, 8:-8]).max()) < 2.5 / 876, float(np.abs(u - ramp(0, 320, 192)).max()))
check("...clipB untouched", OUT.kind_of(str(DM / "clipB_depth.mp4")) == "dc" and not (DM / "DepthCrafter" / "clipB_depth.mp4").exists())
check("...and clipA still listed: both its maps found in their folders", list(bl.property("clips")) == ["clipA"])
logs = objs["Logs"]
bars = [r for r in logs.tab("depthcrafter")._rows if r[0].startswith("Blend") and r[4]]
check("its progress as a bar in the console's Depth Map tab -- no second measuring: the preview's is reused",
      not any(r[0].startswith("Blend measure clipA") for r in bars) and any(r[0].startswith("Blend clipA") and r[4][1] >= 0.999 for r in bars),
      str([r[0] for r in bars][-3:]))
bl.start(); until(lambda: bl.property("busy"), 20); until(lambda: not bl.property("busy"), 300); pump(1.0)
check("a second blend replaces the first, nothing more set aside",
      OUT.kind_of(str(DM / "clipA_depth.mp4")) == "blend" and sorted(os.listdir(DM / "DepthCrafter")) == ["clipA_depth.mp4"]
      and not (DM / "Blend").exists(), str(sorted(os.listdir(DM))))
# Detail Smoothing through a whole clip (frames written once their neighbours are in) and Levels, as clipA's own
bl.setOwn(True); pump(0.3)
st.setProperty("mode", "Layout + Detail"); st.setProperty("detail_smooth", 3.0); st.setProperty("level_white", 0.5)
pump(1.0)
bl.start(); until(lambda: bl.property("busy"), 20); until(lambda: not bl.property("busy"), 300); pump(1.0)
info = D.probe(str(DM / "clipA_depth.mp4"))
frames = [D.to_unit(c, info) for c in D.iter_codes(info)]
want = [np.clip(ramp(i, 320, 192) / 0.5, 0, 1) for i in (0, 12, 23)]
check("Smoothing 3 and White Point 0.5 as clipA's own: every frame written, each its own (one layout: the "
      "detail is nothing), levels applied", len(frames) == 24 and info.num_frames == 24
      and all(float(np.abs(frames[i][8:-8, 8:-8] - w[8:-8, 8:-8]).max()) < 4.0 / 876 for i, w in zip((0, 12, 23), want)),
      f"{len(frames)} {[round(float(np.abs(frames[i][8:-8, 8:-8] - w[8:-8, 8:-8]).max()) * 876, 1) for i, w in zip((0, 12, 23), want) if len(frames) > i]}")
check("...the shared settings untouched", bl._global["mode"] == "Mix" and bl._global["level_white"] == 1.0)
bl.setOwn(False); pump(0.5)

# Other Map (user, 2026-11-07): DVD's map instead of VDA's
st.setProperty("other", "DVD"); pump(0.6)
check("Other Map DVD: no clip listed while there's no DVD map; Base offers DVD, not VDA",
      list(bl.property("clips")) == [] and list(bl.property("choices")["base"]) == ["DepthCrafter", "DVD"],
      str(list(bl.property("clips"))))
(DM / "DVD").mkdir()
write(DM / "DVD" / "clipA_depth.mp4", 320, 192, 24, lambda i, w, h: 0.4 * ramp(i, w, h) + 0.3, OUT.DVD_TAG)
bl.rescan(); pump(0.4)
check("...a DVD map in DVD/ beside clipA's DepthCrafter map: clipA listed again", list(bl.property("clips")) == ["clipA"],
      str(list(bl.property("clips"))))
st.setProperty("base", "DVD"); pump(0.2)
st.setProperty("other", "VDA"); pump(0.6)
check("...back to VDA: Base follows it (DVD -> VDA)", st.value("base") == "VDA", str(st.value("base")))
st.setProperty("base", "DepthCrafter"); pump(0.2)

other = tmp / "elsewhere"; other.mkdir()
objs["Splatting"]._set("input_depth_maps", str(other)); pump(0.3)
bl.useSplattingFolder(); pump(0.3)
check("Same as Splatting takes Splatting's depth-map folder", n(st.value("depth_folder")) == n(other), str(st.value("depth_folder")))

studio_warns = [w for w in warns if "EncodingDialog.qml" not in w]   # 34d33d5's, not this check's
check("no QML warnings (but EncodingDialog's known one)", not studio_warns, str(studio_warns[:3]))
h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
check("the real last_jobs.json untouched", h0 == h1)
print("PASS" if not fails else f"FAIL ({len(fails)})", fails)
_fixtures.exit_now(0 if not fails and h0 == h1 else 1)
