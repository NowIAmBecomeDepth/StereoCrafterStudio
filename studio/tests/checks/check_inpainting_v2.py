"""Inpainting V2 (Wan VACE) in the real app, offscreen: the page fits and does
not clip, v1's MMGP rule hides "Free text encoder", Start runs v1's own runner
end to end (model calls faked -- the 14B model is never loaded), v1's
validation dialog shows in the page, the V1/V2 guard works both ways, and the
"(for V2)" File-menu toggle drives V2's setting. Configs redirected."""
import os, sys, tempfile, hashlib, time
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
rec = REPO / "last_jobs.json"; h0 = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None)
tmp = Path(tempfile.mkdtemp())
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
# V2's weights looked for in an empty folder: the download row shows (user, 2026-10-18)
from studio.stages.inpainting_v2 import weights as W; W.WEIGHTS = tmp / "weights"
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, QPoint, QPointF, Qt, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from studio import app as A

# the overflow audit, shared with check_fit
_src = (Path(__file__).with_name("measure_v2_width.py")).read_text(encoding="utf-8")
exec(_src[_src.index("def eff_visible"):_src.index("def ab_overlaps")])
exec(_src[_src.index("def audit"):_src.index("names = {")])

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
def find(it, pred):
    for c in it.childItems():
        if pred(c): return c
        r = find(c, pred)
        if r is not None: return r
def click_item(item):
    c = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(c.x()), int(c.y())))
pump(0.6)
wan, ip = objs["InpaintingV2"], objs["Inpainting"]
stage = find(win.contentItem(), lambda c: c.objectName() == "stage")
pages = stage.property("pages")
for i in range(6):
    p = pages.property(i).toQObject()
    if p is not None and p.metaObject().className().startswith("InpaintingPage"):
        page, idx = p, i
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", idx), Q_ARG("QVariant", True)); pump(1.0)
page.setProperty("engine", 1); pump(1.0)
v2 = find(page, lambda c: c.objectName() == "inpaintingV2Page")
check("V2 page shown", v2 is not None and v2.isVisible() and v2.opacity() > 0.99)
from studio.stages.inpainting_v2.controller import weights_installed
note = find(v2, lambda c: c.objectName() == "weightsStorageNote")
dl_btn = find(v2, lambda c: c.objectName() == "v2DownloadWeights")
check("weights missing: the note says what's to download, 46.6 GB, with Download V2 Weights beside it",
      not weights_installed() and note is not None and note.isVisible() and "46.6 GB" in note.property("text")
      and dl_btn is not None and dl_btn.isVisible() and dl_btn.isEnabled(), str(note.property("text") if note else None))
_msgs = []
wan.message.connect(lambda k, t, m: _msgs.append(m))
wan.start(); pump(0.3)
check("...Start says to download them first, and starts nothing",
      _msgs and "Download V2 Weights" in _msgs[-1] and not wan.property("busy") and not wan.property("loading"),
      str(_msgs[-1:]))
_pr = page.property("prompt"); pump(0.4)                 # its open transition
check("...asked in the page", _pr.property("opened") and "Download V2 Weights" in str(_pr.property("message")),
      f"opened {_pr.property('opened')} message {_pr.property('message')!r}")
QMetaObject.invokeMethod(_pr, "finish", Q_ARG("QVariant", 1)); pump(0.3)
need = wan.weightsNeed()
check("...the dialog's numbers: 11.9 GB + 34.7 GB, and the free space",
      [p["size"] for p in need["parts"]] == ["11.9 GB", "34.7 GB"] and need["size"] == "46.6 GB" and need["free"],
      str(need))
# a download running: its bar, its text and Stop (the state set as the thread sets it)
wan._dl = {"what": "weights", "progress": 0.4, "text": "StereoCrafter2: 18.6 GB of 46.6 GB, about 20 min left"}
wan.downloadChanged.emit(); pump(0.3)
bar = find(v2, lambda c: c.objectName() == "v2DownloadProgress")
stat = find(v2, lambda c: c.objectName() == "v2DownloadStatus")
check("...while it downloads: the bar, the text and Stop; the button gone",
      bar is not None and bar.isVisible() and abs(bar.property("value") - 0.4) < 1e-6 and stat.isVisible()
      and "18.6 GB of 46.6 GB" in stat.property("text") and not dl_btn.isVisible()
      and find(v2, lambda c: c.objectName() == "v2DownloadStop").isVisible())
wan.cancelDownload(); pump(0.2)
check("...Stop asks the download to stop", wan._dl_stop.is_set() and wan.property("download")["text"] == "Stopping...")
wan._dl = {"what": "", "progress": 0.0, "text": ""}; wan._dl_stop.clear()
# every file there: the row goes; FP8 resident picked without its model: the FP8 row
for part in W.REQUIRED:
    for path, _n in part.files:
        f = W.WEIGHTS / part.folder / path
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"1")
wan.downloadChanged.emit(); pump(0.3)
block = find(v2, lambda c: c.objectName() == "v2WeightsBlock")
check("weights all there: the download row is gone", weights_installed() and not block.isVisible())
_off = wan.settings.property("offload_mode")
wan.settings.setProperty("offload_mode", "FP8 resident"); pump(0.3)
fp8note = find(v2, lambda c: c.objectName() == "fp8StorageNote")
check("FP8 resident picked, its model missing: Download FP8 Model, 17.4 GB",
      block.isVisible() and fp8note.isVisible() and "17.4 GB" in fp8note.property("text")
      and find(v2, lambda c: c.objectName() == "v2DownloadFp8").isVisible(), str(fp8note.property("text")))
wan.settings.setProperty("offload_mode", _off); pump(0.3)
check("...another offload mode: no FP8 row", not block.isVisible())

# ---- fit: the window grows to the page's need; nothing clipped at the stage width
need = page.property("requiredHeight")
check("V2 page needs less than the V1 page", 0 < need < 900, f"{need:.0f}")
probs = audit(v2)
check("no clipping / elision on V2", not probs, str(probs[:5]))
content = find(v2, lambda c: c.objectName() == "pageContent")
bottom = content.mapToItem(v2, QPointF(0, content.height())).y()
check("content fits the page height", bottom <= v2.height() + 0.5, f"bottom {bottom:.0f} page {v2.height():.0f}")

# ---- v1's MMGP rule: Free text encoder hidden, value kept
st = wan.property("settings")
def free_te():
    return find(v2, lambda c: c.metaObject().className().startswith("CheckField") and c.property("key") == "free_text_encoder")
st.setProperty("offload_mode", "MMGP streaming (int8)"); pump(0.6)
check("MMGP hides Free text encoder", not free_te().isVisible())
st.setProperty("offload_mode", "Group offload"); pump(0.6)
check("other modes show it, value kept", free_te().isVisible() and st.property("free_text_encoder") is True)

# ---- Torch Compile where Use SageAttention was (user, 2026-10-11: that one is in Settings)
def field(key):
    return find(v2, lambda c: c.metaObject().className().startswith("CheckField") and c.property("key") == key)
comp = field("use_compile")
check("Torch Compile on the page, Use SageAttention not", comp is not None and comp.isVisible()
      and comp.property("text") == "Torch Compile" and field("use_sageattention") is None)
check("...off to begin with", st.property("use_compile") is False)
if comp is not None:
    box = find(comp, lambda c: c.metaObject().indexOfProperty("checked") >= 0 and c.metaObject().indexOfSignal("toggled()") >= 0)
    p = (box or comp).mapToScene(QPointF((box or comp).width() / 2 if box else 12, (box or comp).height() / 2))
    QTest.mouseClick(win, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, p.toPoint()); pump(0.4)
    check("...a click turns it on", st.property("use_compile") is True, st.property("use_compile"))
    st.setProperty("use_compile", False); pump(0.2)

# ---- Start, with the model calls faked (v1's runner itself runs)
import studio.stages.inpainting_v2.engine as E       # ~9 s: torch, diffusers
inp, out = tmp / "in", tmp / "out"; inp.mkdir()
for n in ("x_splatted2.mp4", "y_splatted2.mp4"):
    (inp / n).write_bytes(b"x")
(tmp / "base").mkdir(); (tmp / "xf").mkdir()
E.PRE_TRAINED_PATH, E.TRANSFORMER_PATH = str(tmp / "base"), str(tmp / "xf")
class FakeEngine:
    text_encoder = object(); _prompt_cache = {}; released = 0
    def release(self): FakeEngine.released += 1
calls = []
def fake_run(engine, video, out_dir, **kw):
    kw["progress_cb"]("denoise", 1, 1, "done")
    calls.append(os.path.basename(video)); time.sleep(0.2)
E.load_models = lambda *a, **k: FakeEngine()
E.run_inpaint = fake_run
st.setProperty("input_folder", str(inp).replace("\\", "/")); st.setProperty("output_folder", str(out).replace("\\", "/"))
st.setProperty("move_to_finished", False)
start = find(v2, lambda c: c.objectName() == "v2Start"); stop = find(v2, lambda c: c.objectName() == "v2Stop")
click_item(start); pump(0.3)
end = time.time() + 60
while (wan.property("busy") or wan.property("loading") or len(calls) < 2) and time.time() < end:
    pump(0.05)
pump(0.4)
status = find(v2, lambda c: c.objectName() == "v2Status").property("text")
check("Start by mouse ran v1's batch", calls == ["x_splatted2.mp4", "y_splatted2.mp4"], str(calls))
check("v1's finish line in the status", status.startswith("Finished") and "2 done, 0 failed" in status, status)
check("progress full, Start back, Stop off", wan.property("progress") == 100.0 and start.isEnabled() and not stop.isEnabled())
check("engine kept loaded (v1)", wan.holds_gpu())

# ---- Upcoming Jobs (user, 2026-10-11: V1's clip list for V2 too)
jq = find(v2, lambda c: c.objectName() == "v2JobQueue")
def wait_idle(n):
    end = time.time() + 60
    while (wan.property("busy") or wan.property("loading") or len(calls) < n) and time.time() < end:
        pump(0.05)
    pump(0.4)
q = wan.property("jobClips")
end = time.time() + 10
while sorted(os.path.basename(x) for x in (q.property("clipPaths") or [])) != ["x_splatted2.mp4", "y_splatted2.mp4"] and time.time() < end:
    pump(0.1)
check("Upcoming Jobs on V2, listing the input's clips", jq is not None and jq.isVisible()
      and sorted(os.path.basename(x) for x in (q.property("clipPaths") or [])) == ["x_splatted2.mp4", "y_splatted2.mp4"],
      str(q.property("clipPaths")))
if jq is not None:
    jx, sx = jq.mapToScene(QPointF(0, 0)), find(v2, lambda c: c.property("title") == "Parameters").mapToScene(QPointF(0, 0))
    check("...beside the settings, not under them", jx.x() > sx.x() + 100 and abs(jx.y() - sx.y()) < 60, f"{jx} {sx}")
calls.clear()
wan.startClip(str(inp / "y_splatted2.mp4")); wait_idle(1)
check("Inpaint This Clip Now runs just that clip", calls == ["y_splatted2.mp4"], str(calls))
calls.clear()
q.setChecked(str(inp / "x_splatted2.mp4"), True); pump(0.1)
picked = q.checked_paths()
wan.startSelected(); wait_idle(1)
check("Process Selected runs only the ticked clip", picked and calls == ["x_splatted2.mp4"], f"{picked} -> {calls}")
calls.clear()
click_item(start); wait_idle(2)
check("...and Start still runs them all", calls == ["x_splatted2.mp4", "y_splatted2.mp4"], str(calls))

# ---- v1's validation dialog, in the page
prompt = page.property("prompt")
st.setProperty("frames_overlap", "90"); pump(0.3)
click_item(start); pump(0.6)
check("v1 validation error asked in the page", prompt.property("opened")
      and prompt.property("message") == "'frames_overlap' must be smaller than 'frames_chunk'.", prompt.property("message"))
QMetaObject.invokeMethod(prompt, "finish", Q_ARG("QVariant", 1)); pump(0.3)
# bug sweep 2026-10-09 (A7): a chunk of 82 holds 81 frames (4n + 1): an overlap of 81
# passed and failed the clip after its first chunk; now it is refused up front
_chunk = st.property("frames_chunk")
st.setProperty("frames_chunk", "82"); st.setProperty("frames_overlap", "81"); pump(0.3)
n_calls = len(calls)
click_item(start); pump(0.6)
check("an overlap the real (4n + 1) chunk can't hold is refused up front", prompt.property("opened")
      and "holds 81 frames" in prompt.property("message") and "at most 80" in prompt.property("message")
      and len(calls) == n_calls, prompt.property("message"))
QMetaObject.invokeMethod(prompt, "finish", Q_ARG("QVariant", 1)); pump(0.3)
st.setProperty("frames_chunk", _chunk)
st.setProperty("frames_overlap", "10"); pump(0.3)

# ---- the V1 / V2 guard (v1's shell _sibling_guard)
ip._busy = True
click_item(start); pump(0.6)
check("V2 refused while V1 renders", prompt.property("opened") and "Inpainting V1 is currently processing" in prompt.property("message")
      and len(calls) == 2, prompt.property("message"))
QMetaObject.invokeMethod(prompt, "finish", Q_ARG("QVariant", 1)); pump(0.3)
ip._busy = False
toasts = []; ip.toast.connect(lambda k, t, m: toasts.append((k, t, m)))
ipst = ip.property("settings"); ipst.setProperty("input_folder", str(tmp / "nowhere"))
ip.start(); pump(0.3)
# audit 2026-09-28 (minor): a V1 start refused by its own checks left V2
# unloaded; V2 is released only once V1 is really starting
check("a refused V1 Start leaves V2's engine loaded", FakeEngine.released == 0 and wan.holds_gpu()
      and toasts and toasts[-1][1] == "Cannot start" and not ip.property("busy"), f"{FakeEngine.released} {toasts[-1:]}")
ipst.setProperty("input_folder", str(tmp)); ipst.setProperty("output_folder", str(tmp)); pump(0.3)
ip._claim = lambda: False            # the GPU claim refused (another stage running)
ip.start(); pump(0.3)
# bug sweep 2026-10-09 (A7): V2 is released only once V1 has the GPU -- a start the
# claim refused used to unload it anyway (test_inpainting covers the granted claim)
check("a V1 Start the GPU claim refuses leaves V2's engine loaded",
      FakeEngine.released == 0 and wan.holds_gpu(), f"{FakeEngine.released} {toasts[-1:]}")
del ip._claim

# ---- File menu: "Use Sage Attention (for V2)" drives V2's setting, not V1's
menu = page.property("stageMenus").property(0).toQObject()
from PySide6.QtCore import QObject
item = None
for ch in menu.findChildren(QObject):
    if ch.property("text") == "Use Sage Attention (for V2)" and ch.metaObject().indexOfSignal("triggered()") >= 0:
        item = ch
v1_sage = ipst.property("use_sageattention")
check("(for V2) item present", item is not None)
before = st.property("use_sageattention")
QMetaObject.invokeMethod(item, "toggle"); QMetaObject.invokeMethod(item, "triggered"); pump(0.3)
check("it toggles V2's Sage, V1's untouched", st.property("use_sageattention") is (not before)
      and ipst.property("use_sageattention") == v1_sage, f"{before} -> {st.property('use_sageattention')}")

# ---- bug sweep 2026-10-09 (A7): Restore Finished / Load / Save Settings not during a batch
import _fixtures
def v2_item(name):
    found = []
    def walk(it):
        for c in it.childItems():
            if c.objectName() == name:
                found.append(c)
            walk(c)
    walk(win.contentItem())
    return found[0] if found else None
names = ("v2RestoreFinished",)
# Load / Save V2 Settings moved into Settings > Inpainting, as V1's are (user, 2026-10-11)
v2_menu = {ch.property("text"): ch for ch in menu.findChildren(QObject)
           if ch.property("text") in ("Load V2 Settings...", "Save V2 Settings...")
           and ch.metaObject().indexOfSignal("triggered()") >= 0}
check("Load / Save V2 Settings in Settings, not on the page", len(v2_menu) == 2
      and v2_item("v2LoadSettings") is None and v2_item("v2SaveSettings") is None, str(list(v2_menu)))
check("V2's Restore and the Settings items are live while idle", all(v2_item(n) is not None and v2_item(n).property("enabled")
                                                                     for n in names)
      and all(m.property("enabled") for m in v2_menu.values()))
_fixtures.fake_busy(wan, True); pump(0.2)
check("...and greyed out while V2 runs a batch", not any(v2_item(n).property("enabled") for n in names)
      and not any(m.property("enabled") for m in v2_menu.values()))
fin = tmp / "v2_restore_in"; (fin / "finished").mkdir(parents=True, exist_ok=True)
(fin / "finished" / "clip_640_splatted2.mp4").write_bytes(b"x")
was_in = wan.settings.value("input_folder")
wan._map.insert("input_folder", str(fin)); pump(0.1)
wan.restoreFinished(); pump(0.3)
check("...Restore Finished called anyway moves nothing", (fin / "finished" / "clip_640_splatted2.mp4").exists()
      and not (fin / "clip_640_splatted2.mp4").exists())
cfg = tmp / "v2_load.json"; cfg.write_text('{"tile_num": 7}')
before_tiles = wan.settings.value("tile_num")
wan.loadSettingsFrom(str(cfg)); pump(0.2)
check("...nor does Load Settings change the page", wan.settings.value("tile_num") == before_tiles,
      f"{before_tiles} -> {wan.settings.value('tile_num')}")
_fixtures.fake_busy(wan, False); pump(0.2)
wan._map.insert("input_folder", was_in)

wan.shutdown()
studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
check("config written to temp only", (tmp / "config" / "inpainting_v2.json").exists())
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
import _fixtures
_fixtures.exit_now(0 if not fails and _lj else 1)
