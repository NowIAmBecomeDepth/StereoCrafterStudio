"""Upcoming Jobs' right-click menu (user, 2026-10-10), and the preview card
turned over by dragging an edge, in the real app offscreen:

* Splatting: right-click a clip -- Splat This Clip Now runs Single on that
  clip (shown in the preview first): only its splat is written; Rerun in
  DepthCrafter puts the clip in DepthCrafter's input, the folder its depth map
  lives in as the output, and shows DepthCrafter's page;
* Merging: Merge This Clip Now merges that clip only; Move to Finished moves
  that one clip (and what goes with it) into finished/;
* the flip card: a drag from the picture's right edge past halfway turns it to
  the other side (Preview Source follows) and it lands flat; a drag that stops
  short falls back; a drag from the left edge turns it back.

Real runs over TEMP copies of the small and merge fixtures; configs and
last_jobs.json redirected.
"""
import os, sys, tempfile, hashlib, time, glob
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, str(Path(__file__).parent))
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
tmp = Path(tempfile.mkdtemp())
import _fixtures
FIX = tmp / "fx"; _fixtures.copy("small", FIX)
MFX = tmp / "mfx"; _fixtures.copy("merge", MFX)
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, QObject, Q_ARG, QPoint, QPointF, Qt, qInstallMessageHandler
from PySide6.QtTest import QTest
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
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
def one(root, name):
    got = [c for c in find_all(root, lambda c: c.objectName() == name) if c.isVisible()]
    return got[0] if got else None
def centre(item):
    c = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    return QPoint(int(c.x()), int(c.y()))
def rows(q):
    return sorted([r for r in find_all(q, lambda c: c.objectName() == "jobQueueRow") if r.isVisible()],
                  key=lambda r: r.property("index"))
def row_of(q, name):
    lst = one(q, "jobQueueList")
    names = [str(n) for n in q.property("src").property("clips")]
    i = [k for k, n in enumerate(names) if name in n][0]
    QMetaObject.invokeMethod(lst, "positionViewAtIndex", Q_ARG(int, i), Q_ARG(int, 2)); pump(0.3)
    return [r for r in rows(q) if name in str(r.property("modelData"))][0]
def menu_of(q):
    return [o for o in q.findChildren(QObject) if o.objectName() == "jobQueueRowMenu"][0]
def action(q, name, act):
    """Right-click the row, then the menu's item: the texts the menu offered."""
    QTest.mouseClick(win, Qt.RightButton, Qt.NoModifier, centre(row_of(q, name))); pump(0.5)
    m = menu_of(q)
    items = [o for o in m.findChildren(QObject) if o.objectName().startswith("jobQueueAction_")]
    texts = [str(o.property("text")) for o in items]
    opened = m.property("opened") is True
    hit = [o for o in items if o.objectName() == "jobQueueAction_" + act]
    if hit:
        QMetaObject.invokeMethod(hit[0], "click"); pump(0.5)
    return opened, texts
# "Play ..." (user, 2026-10-10): the default player stood in for -- nothing opens
played = []
os.startfile = lambda p, *a, **k: played.append(os.path.normcase(os.path.normpath(p)))
def play(q, name, pick=None):
    """Right-click the row: the Play items offered; with `pick`, choose that one."""
    m = [o for o in q.findChildren(QObject) if o.objectName() == "jobQueueRowMenu"][0]
    end = time.time() + 12
    while True:                     # a run's toast may sit over the list for a moment
        QTest.mouseClick(win, Qt.RightButton, Qt.NoModifier, centre(row_of(q, name))); pump(0.5)
        if m.property("opened") or time.time() > end:
            break
        pump(1.0)
    items = [o for o in m.findChildren(QObject) if o.objectName() == "jobQueuePlay"]
    texts = [str(o.property("text")) for o in items]
    hit = [o for o in items if o.property("text") == pick]
    if hit:
        QMetaObject.invokeMethod(hit[0], "click"); pump(0.5)
    else:
        QMetaObject.invokeMethod(m, "close"); pump(0.3)
    return texts
n = lambda p: os.path.normcase(os.path.normpath(str(p)))
# "Open ... in Explorer" (user, 2026-10-18): Explorer stood in for -- nothing opens
revealed = []
import core.ui.folder_open as _FO
_FO.reveal_file = lambda p: (revealed.append(n(p)), (True, p))[1]
def explore(q, name, pick=None):
    """Right-click the row: the Explorer items offered; with `pick`, choose that one."""
    m = [o for o in q.findChildren(QObject) if o.objectName() == "jobQueueRowMenu"][0]
    end = time.time() + 12
    while True:
        QTest.mouseClick(win, Qt.RightButton, Qt.NoModifier, centre(row_of(q, name))); pump(0.5)
        if m.property("opened") or time.time() > end:
            break
        pump(1.0)
    items = [o for o in m.findChildren(QObject) if o.objectName() == "jobQueueExplore"]
    texts = [str(o.property("text")) for o in items]
    hit = [o for o in items if o.property("text") == pick]
    if hit:
        QMetaObject.invokeMethod(hit[0], "click"); pump(0.5)
    else:
        QMetaObject.invokeMethod(m, "close"); pump(0.3)
    return texts
def run_to_end(ctl, sec=300):
    until(lambda: ctl.property("busy"), 20)
    until(lambda: not ctl.property("busy"), sec)
    pump(1.0)
stage = find_all(win.contentItem(), lambda c: c.objectName() == "stage")[0]
def show(i):
    QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", i), Q_ARG("QVariant", True)); pump(1.0)
win.setProperty("userSized", True); win.setHeight(1600); pump(0.5)

# ================= Splatting =================
sp = objs["Splatting"]; pv = sp.preview
show(1)
for k, v in {"input_source_clips": str(FIX / "src").replace("\\", "/"), "input_depth_maps": str(FIX / "depth").replace("\\", "/"),
             "output_splatted": str(FIX / "out").replace("\\", "/"), "multi_map": False, "auto_save_sidecar": False,
             "move_to_finished": False, "enable_full_res": False, "enable_low_res": True, "dual_output": False,
             "auto_convergence_mode": "Off", "custom_clip_range": False}.items():
    sp._set(k, v)
jc = pv.property("jobClips")
jc.rescan()
sq = find_all(win.contentItem(), lambda c: c.objectName() == "splatJobQueue")[0]
check("Splatting: two clips listed", until(lambda: len(rows(sq)) == 2, 30))
opened, texts = action(sq, "clipB", "splat")
# Rerun with Video Depth Anything beside it (user, 2026-10-25)
check("right-click a clip: Splat This Clip Now, Test Render, Rerun in DepthCrafter and with Video Depth Anything",
      opened and texts == ["Splat This Clip Now", "Test Render", "Rerun in DepthCrafter",
                           "Rerun with Video Depth Anything"], str(texts))
run_to_end(sp)
outs = sorted(os.path.basename(p) for p in glob.glob(str(FIX / "out" / "**" / "*.mp4"), recursive=True))
check("Splat This Clip Now splats that clip only (clipB)", outs and all(o.startswith("clipB") for o in outs), str(outs))
shown = list(pv.property("clipPaths") or [])
check("...shown in the preview first", pv.property("loaded") and
      os.path.basename(shown[int(pv.property("videoIndex"))]) == "clipB.mp4" if shown else False)

# Send to Inpainting (user, 2026-10-11): clipB is green now -- its Lo-Res splat goes on
ip = objs["Inpainting"]; ist = ip.property("settings")
ip_toasts = []
ip.toast.connect(lambda *a: ip_toasts.append(a))
_done = sq.property("done").toVariant() or {}
check("clipB is green (finished this run)", bool(_done.get("clipB")), str(_done))
opened, texts = action(sq, "clipA", "none")
QMetaObject.invokeMethod(menu_of(sq), "close"); pump(0.3)
check("...Send to Inpainting isn't offered on clipA (not run)", "Send to Inpainting" not in texts, str(texts))
opened, texts = action(sq, "clipB", "send"); pump(0.8)
low = glob.glob(str(FIX / "out" / "lowres" / "clipB*_splatted*.mp4")) + glob.glob(str(FIX / "out" / "clipB*_splatted*.mp4"))
check("...on clipB it is", texts[-1:] == ["Send to Inpainting"], str(texts))
check("...its Lo-Res splat is Inpainting's input", len(low) == 1 and n(ist.value("input_folder")) == n(low[0]),
      f"{ist.value('input_folder')} vs {low}")
check("...Inpainting (V1) showing, and a toast says so", stage.property("current") == 2
      and stage.property("pages").property(2).toQObject().property("engine") == 0
      and until(lambda: any(t[1] == "Ready in Inpainting" for t in ip_toasts), 5), str(ip_toasts[-1:]))
show(1)

texts = play(sq, "clipA", "Play Depth Map")
check("right-click also offers Play Source Clip and Play Depth Map", texts == ["Play Source Clip", "Play Depth Map"], str(texts))
check("...Play Depth Map opens the clip's depth map in the default player", played[-1:] == [n(FIX / "depth" / "clipA_depth.mp4")], str(played))
texts = explore(sq, "clipA", "Open Depth Map in Explorer")
check("...and Open Source Clip / Depth Map in Explorer", texts == ["Open Source Clip in Explorer", "Open Depth Map in Explorer"], str(texts))
check("...Open Depth Map in Explorer selects the depth map there", revealed[-1:] == [n(FIX / "depth" / "clipA_depth.mp4")], str(revealed))

# Rerun in DepthCrafter
dc = objs["DepthCrafter"]; dst = dc.property("settings")
# its own size even with Auto Size off (user, 2026-10-11), and the toast says the change
from studio.stages.depthcrafter import settings as DS
dst.setProperty("auto_resolution_var", False); dc.applyPreset(512, 512); pump(0.3)
_shape = DS.ratio_class(DS.video_ratio(str(FIX / "src" / "clipA.mp4")))
_want = DS.auto_sizes(ss.CONFIG_DIR)[_shape]
dc_toasts = []
dc.toast.connect(lambda *a: dc_toasts.append(a))
opened, _t = action(sq, "clipA", "rerun"); pump(0.8)
until(lambda: any(t[1] == "Ready to re-run" for t in dc_toasts), 15)
check("...its own auto size, though Auto Size is off",
      (int(dst.value("target_width")), int(dst.value("target_height"))) == tuple(_want),
      f"{dst.value('target_width')}x{dst.value('target_height')} vs {_want} ({_shape})")
_msg = [t[2] for t in dc_toasts if t[1] == "Ready to re-run"][-1:]
check("...and the toast says the change", bool(_msg) and f"512x512 \u2192 {_want[0]}x{_want[1]}" in _msg[0]
      and _shape in _msg[0], str(_msg))
check("Rerun in DepthCrafter: the clip is DepthCrafter's input",
      os.path.normcase(str(dst.value("input_dir_or_file_var"))) == os.path.normcase(str(FIX / "src" / "clipA.mp4")),
      str(dst.value("input_dir_or_file_var")))
check("...its depth map's folder is the output",
      os.path.normcase(str(dst.value("output_dir"))) == os.path.normcase(str(FIX / "depth")), str(dst.value("output_dir")))
check("...and DepthCrafter's page is showing", stage.property("current") == 0, str(stage.property("current")))
dcpage = stage.property("pages").property(0).toQObject()
check("...on its DepthCrafter tab", dcpage.property("engine") == 0, str(dcpage.property("engine")))
check("...nothing started", dc.property("busy") is False)

# Rerun with Video Depth Anything (user, 2026-10-25): its tab, the clip loaded, its own folders
vda = objs["VDA"]; vst = vda.property("settings")
vda_toasts = []
vda.toast.connect(lambda *a: vda_toasts.append(a))
_dc_in = str(dst.value("input_dir_or_file_var"))
show(1)
opened, _t = action(sq, "clipA", "rerunVda"); pump(0.8)
check("Rerun with Video Depth Anything: the clip is VDA's input",
      n(vst.value("input_dir_or_file_var")) == n(FIX / "src" / "clipA.mp4"), str(vst.value("input_dir_or_file_var")))
check("...its depth map's folder is the output", n(vst.value("output_dir")) == n(FIX / "depth"), str(vst.value("output_dir")))
check("...DepthCrafter's page, on its Video Depth Anything tab", stage.property("current") == 0
      and dcpage.property("engine") == 1, f"{stage.property('current')} / {dcpage.property('engine')}")
check("...a toast says it's ready", any(t[1] == "Ready to re-run" for t in vda_toasts), str(vda_toasts[-1:]))
check("...DepthCrafter's own folders untouched, nothing started", str(dst.value("input_dir_or_file_var")) == _dc_in
      and vda.property("busy") is False)
show(1)
opened, _t = action(sq, "clipA", "rerun"); pump(0.8)
check("...and Rerun in DepthCrafter goes back to the DepthCrafter tab", dcpage.property("engine") == 0)

# ================= the flip card, dragged =================
show(1)
pv.showClip(str(FIX / "src" / "clipB.mp4"))
until(lambda: pv.property("loaded") and pv.property("imageWidth") > 0, 60); pump(1.5)
page = stage.property("pages").property(1).toQObject()
card = [o for o in page.findChildren(QObject) if o.objectName() == "flipCard"][0]
fdrag = one(page, "flipDrag")
spst = sp.property("settings")
def drag(from_frac, to_frac, steps=14):
    """Press on the picture at from_frac of its width, move to to_frac, let go."""
    x0, y0 = fdrag.property("picX"), fdrag.property("picY")
    w, h = fdrag.property("picW"), fdrag.property("picH")
    p = lambda f: QPoint(*[int(v) for v in (lambda s: (s.x(), s.y()))(fdrag.mapToScene(QPointF(x0 + w * f, y0 + h / 2)))])
    QTest.mousePress(win, Qt.LeftButton, Qt.NoModifier, p(from_frac)); pump(0.05)
    angles = []
    for k in range(1, steps + 1):
        QTest.mouseMove(win, p(from_frac + (to_frac - from_frac) * k / steps)); pump(0.06)
        angles.append(round(float(card.property("angle")), 1))
    QTest.mouseRelease(win, Qt.LeftButton, Qt.NoModifier, p(to_frac))
    until(lambda: not card.property("busy"), 5); pump(0.3)
    return angles
check("the flip card has a drag grip on the picture", fdrag is not None and fdrag.property("picW") > 0)
front = str(spst.value("preview_source")); was_back = card.property("backUp")
angles = drag(0.97, 0.15)
check("a drag from the right edge past halfway turns the card as it goes",
      any(10 < abs(a) < 170 for a in angles), str(angles))
check("...the other side is up (Preview Source follows) and it lands flat",
      card.property("backUp") != was_back and str(spst.value("preview_source")) != front
      and abs(float(card.property("angle"))) < 0.5, f"{front} -> {spst.value('preview_source')}, angle {card.property('angle')}")
side_now, src_now = card.property("backUp"), str(spst.value("preview_source"))
drag(0.97, 0.7)
check("a drag that stops short of halfway falls back",
      card.property("backUp") == side_now and str(spst.value("preview_source")) == src_now
      and abs(float(card.property("angle"))) < 0.5)
drag(0.03, 0.85)
check("a drag from the left edge turns it back", card.property("backUp") == was_back
      and str(spst.value("preview_source")) == front and abs(float(card.property("angle"))) < 0.5,
      str(spst.value("preview_source")))

# ================= Merging =================
mg = objs["Merging"]; st = mg.property("settings"); mpv = mg.property("previewer")
show(3)
for k, v in {"inpainted_folder": str(MFX / "inpainted"), "original_folder": str(MFX / "orig"),
             "mask_folder": str(MFX / "mask"), "output_folder": str(tmp / "merged"),
             "encoding_encoder": "Force CPU", "codec": "H.264", "use_gpu": False, "resume": False}.items():
    st.setProperty(k, v)
mjc = mpv.property("jobClips")
mjc.rescan()
mq = [q for q in find_all(win.contentItem(), lambda c: c.metaObject().className().startswith("JobQueue"))
      if q.isVisible() and q.property("ctl") is mg][0]
check("Merging: two clips listed", until(lambda: len(rows(mq)) == 2, 30))
opened, texts = action(mq, "mB", "merge")
# Fill Edges between them since 2026-10-14 (Extra > Edge Fill fills the clip's merged output)
check("right-click a clip: Merge This Clip Now, Test Render, Fill Edges and Move to Finished",
      opened and texts == ["Merge This Clip Now", "Test Render", "Fill Edges", "Move to Finished"], str(texts))
run_to_end(mg)
merged = sorted(os.path.basename(p) for ext in ("mp4", "mov")
                for p in glob.glob(str(tmp / "merged" / "**" / f"*.{ext}"), recursive=True))
check("Merge This Clip Now merges that clip only (mB)", merged and all(m.startswith("mB") for m in merged), str(merged))
texts = play(mq, "mA", "Play Original")
check("Merging: Play Inpainted Clip, Play Original and Play Splat", texts == ["Play Inpainted Clip", "Play Original", "Play Splat"], str(texts))
check("...Play Original opens the clip's original", played[-1:] == [n(MFX / "orig" / "mA.mp4")], str(played[-1:]))
texts = explore(mq, "mA", "Open Original in Explorer")
check("Merging: Open Inpainted Clip / Original / Splat in Explorer",
      texts == ["Open Inpainted Clip in Explorer", "Open Original in Explorer", "Open Splat in Explorer"], str(texts))
check("...Open Original in Explorer selects the original there", revealed[-1:] == [n(MFX / "orig" / "mA.mp4")], str(revealed[-1:]))
action(mq, "mA", "finished")
check("Move to Finished: mA leaves the list",
      until(lambda: [str(r.property("modelData")) for r in rows(mq)] == ["mB_320_inpainted_right_eye.mp4"], 30),
      str([str(r.property("modelData")) for r in rows(mq)]))
check("...its inpainted clip, splat and original are in finished/, mB's are not",
      (MFX / "inpainted" / "finished" / "mA_320_inpainted_right_eye.mp4").exists()
      and (MFX / "orig" / "finished" / "mA.mp4").exists()
      and (MFX / "orig" / "mB.mp4").exists() and (MFX / "inpainted" / "mB_320_inpainted_right_eye.mp4").exists())

check("no QML warnings", not warns, str(warns[:3]))
h1 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
check("the real last_jobs.json untouched", h0 == h1)
print("PASS" if not fails else f"FAIL ({len(fails)})", fails)
_fixtures.exit_now(0 if not fails and h0 == h1 else 1)
