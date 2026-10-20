"""Stop After Clip (user, 2026-09-28): on Splatting, Inpainting V1 / V2 and
Merging, a run over a folder of clips shows the button beside Stop; clicked by
mouse while clip 1 is rendering, clip 1 finishes whole, clip 2 never starts,
the toast says it stopped after the clip ("1 done") and the finished clip is
handed on. Clicked twice, it carries on. The button fits the action bar
beside the others, and is gone when the stage is idle or runs one clip.
The real app offscreen; Splatting and Merging render for real (small /
merge fixtures), Inpainting's model calls are stood in for. Configs and
last_jobs.json redirected.

    check_stop_after_clip.py          every stage
    check_stop_after_clip.py sp mg    only these (sp ip ip2 mg)
"""
import os, sys, tempfile, hashlib, time, json, threading
from pathlib import Path
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QPA_FONTDIR"] = "C:/Windows/Fonts"
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
sys.path.insert(0, str(Path(__file__).parent))
rec = REPO / "last_jobs.json"; h0 = hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None
tmp = Path(tempfile.mkdtemp())
import _fixtures
from studio.legacy import install; install()
import studio.settings_store as ss; ss.CONFIG_DIR = tmp / "config"
import core.ui.job_handoff as jh; jh._RECORD_PATH = str(tmp / "last_jobs.json")
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, QPointF, QPoint, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from studio import app as A

WANT = set(a for a in sys.argv[1:] if not a.startswith("-")) or {"sp", "ip", "ip2", "mg"}

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
def find(it, pred):
    for c in it.childItems():
        if pred(c): return c
        r = find(c, pred)
        if r is not None: return r
def find_all(it, pred, out=None):
    out = [] if out is None else out
    for c in it.childItems():
        if pred(c): out.append(c)
        find_all(c, pred, out)
    return out
def click(item):
    c = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(c.x()), int(c.y())))
def handoff(stage):
    try:
        return stage in json.loads(Path(jh._RECORD_PATH).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
stage_item = find(win.contentItem(), lambda c: c.objectName() == "stage")
def show(i):
    QMetaObject.invokeMethod(stage_item, "show", Q_ARG("QVariant", i), Q_ARG("QVariant", True)); pump(1.0)
def fits_beside_the_others(btn):
    """Visible, inside the window, overlapping none of its row's buttons."""
    row = btn.parentItem()
    r = btn.mapRectToScene(btn.boundingRect())
    others = [c for c in row.childItems() if c is not btn and c.isVisible() and c.width() > 0]
    hits = [c.property("text") for c in others
            if c.mapRectToScene(c.boundingRect()).intersects(r)]
    inside = r.left() >= 0 and r.right() <= win.width()
    return btn.isVisible() and inside and not hits, f"x {r.left():.0f}-{r.right():.0f} of {win.width()}, overlaps {hits}"

toasts = []
for ctl in objs.values():
    sig = getattr(ctl, "toast", None)
    if sig is not None and hasattr(sig, "connect"):
        sig.connect(lambda k, t, m: toasts.append((k, t, m)))

class Gate:
    """Held inside clip 1's work until the test has clicked the button."""
    def __init__(self):
        self.running, self.release, self.clips = threading.Event(), threading.Event(), []
    def at(self, clip):
        if clip not in self.clips:
            self.clips.append(clip)
        if len(self.clips) == 1 and not self.release.is_set():
            self.running.set()
            self.release.wait(60)

def exercise(label, ctl, page_btn_name, stage_key, start, gate, twice=False):
    toasts.clear(); Path(jh._RECORD_PATH).unlink(missing_ok=True)
    btn = find(win.contentItem(), lambda c: c.objectName() == page_btn_name)
    check(f"{label}: the button exists, hidden while idle", btn is not None and not btn.isVisible())
    start()
    check(f"{label}: clip 1 is rendering", until(lambda: gate.running.is_set(), 120))
    pump(0.5)
    ok, why = fits_beside_the_others(btn)
    check(f"{label}: shown beside Stop while a folder runs, and fits", ok, why)
    w0 = win.width()
    win.setProperty("userSized", True); win.setWidth(int(win.property("minimumWidth"))); pump(0.8)
    ok, why = fits_beside_the_others(btn)
    check(f"{label}: ...and at the stage's minimum width", ok, why)
    win.setWidth(w0); pump(0.5)
    click(btn); pump(0.3)
    check(f"{label}: armed by mouse", ctl.property("stopAfterClip") and btn.property("text") == "Stopping After Clip",
          btn.property("text"))
    if twice:
        click(btn); pump(0.3)
        check(f"{label}: a second click carries on", not ctl.property("stopAfterClip"))
    gate.release.set()
    ended = until(lambda: not ctl.property("busy") and not ctl.property("loading" if ctl.metaObject().indexOfProperty("loading") >= 0 else "busy"), 300)
    pump(0.8)
    check(f"{label}: the run ends", ended)
    last = toasts[-1] if toasts else ("", "", "")
    if twice:
        check(f"{label}: ...having done both clips", len(gate.clips) == 2 and last[0] == "success" and "2 done" in last[2],
              f"{gate.clips} {last}")
    else:
        check(f"{label}: clip 2 never started", len(gate.clips) == 1, str(gate.clips))
        check(f"{label}: toast says it stopped after the clip, 1 done",
              # "1 done", then the time it took (user, 2026-10-05: every finish says it)
              last[0] == "success" and "stopped after the clip" in last[1] and last[2].split("\n")[0] == "1 done"
              and all(ln.startswith("Took ") for ln in last[2].split("\n")[1:]), str(last))
        if stage_key:
            check(f"{label}: the finished clip is handed on", handoff(stage_key))
    check(f"{label}: disarmed and hidden again", not ctl.property("stopAfterClip") and not btn.isVisible())

pump(0.8)

# ---- Splatting: a real render over the two small clips ----------------------------
if "sp" in WANT:
    print("Splatting")
    show(1)
    sp = objs["Splatting"]
    FIX = _fixtures.copy("small", tmp / "sp")
    for k, v in {"input_source_clips": str(FIX / "src").replace("\\", "/"),
                 "input_depth_maps": str(FIX / "depth").replace("\\", "/"),
                 "output_splatted": str(tmp / "sp_out").replace("\\", "/"), "multi_map": False,
                 "auto_save_sidecar": False, "move_to_finished": False, "enable_full_res": False,
                 "enable_low_res": True, "dual_output": False, "auto_convergence_mode": "Off",
                 "low_res_batch_size": "8"}.items():
        sp._set(k, v)
    import core.splatting.render_processor as RP
    _render = RP.RenderProcessor.render_video
    for twice in (False, True):
        gate = Gate()
        def render(self, *a, **kw):
            gate.at(os.path.basename(str(kw.get("output_video_path_base", ""))))
            return _render(self, *a, **kw)
        RP.RenderProcessor.render_video = render
        exercise("Splatting" + (" (twice)" if twice else ""), sp, "spStopAfter", "splatting", sp.start, gate, twice)
    RP.RenderProcessor.render_video = _render
    out = list((tmp / "sp_out").rglob("*.mp4"))
    check("Splatting: the stopped-after clip is a whole file", any(p.name.startswith("clipA") for p in out), str(out))

# ---- Inpainting V1: the real engine, its model and per-clip render stood in for ----
if "ip" in WANT:
    print("Inpainting V1")
    show(2)
    ip = objs["Inpainting"]
    import studio.stages.inpainting.engine as IE
    FIX = _fixtures.copy("small", tmp / "ip")
    IE.load_inpainting_pipeline = lambda **kw: object()
    for twice in (False, True):
        gate = Gate()
        def process_single_video(self, *a, **kw):
            gate.at(os.path.basename(str(kw.get("input_video_path", a[1] if len(a) > 1 else ""))))
            return True, None
        IE.InpaintingEngine.process_single_video = process_single_video
        out = tmp / f"ip_out_{twice}"; out.mkdir()
        for k, v in {"input_folder": str(FIX / "src"), "output_folder": str(out), "hires_blend_folder": "",
                     "single_clip_id": "", "move_to_finished": False}.items():
            ip.settings.setProperty(k, v)
        exercise("Inpainting V1" + (" (twice)" if twice else ""), ip, "ipStopAfter", "inpainting", ip.start, gate, twice)

# ---- Inpainting V2: v1's runner for real, the Wan model calls stood in for ---------
if "ip2" in WANT:
    print("Inpainting V2")
    pages = stage_item.property("pages")
    for i in range(6):
        p = pages.property(i).toQObject()
        if p is not None and p.metaObject().className().startswith("InpaintingPage"):
            show(i); p.setProperty("engine", 1); pump(1.0)
    wan = objs["InpaintingV2"]; st2 = wan.property("settings")
    import studio.stages.inpainting_v2.engine as WE
    inp = tmp / "ip2_in"; inp.mkdir()
    for n in ("x_splatted2.mp4", "y_splatted2.mp4"):
        (inp / n).write_bytes(b"x")
    (tmp / "ip2_base").mkdir(); (tmp / "ip2_xf").mkdir()
    WE.PRE_TRAINED_PATH, WE.TRANSFORMER_PATH = str(tmp / "ip2_base"), str(tmp / "ip2_xf")
    class WanEngine:
        text_encoder = object(); _prompt_cache = {}
        def release(self): pass
    WE.load_models = lambda *a, **k: WanEngine()
    for twice in (False, True):
        gate = Gate()
        WE.run_inpaint = lambda engine, video, out_dir, **kw: gate.at(os.path.basename(video))
        for k, v in {"input_folder": str(inp).replace("\\", "/"),
                     "output_folder": str(tmp / "ip2_out").replace("\\", "/"),
                     "move_to_finished": False, "frames_overlap": "10"}.items():
            st2.setProperty(k, v)
        pump(0.3)
        exercise("Inpainting V2" + (" (twice)" if twice else ""), wan, "v2StopAfter", "inpainting_v2", wan.start, gate, twice)

# ---- Merging: real CPU merges over the merge fixture -------------------------------
if "mg" in WANT:
    print("Merging")
    show(3)
    mg = objs["Merging"]; st = mg.property("settings")
    FIX = _fixtures.copy("merge", tmp / "mg")
    import studio.stages.merging.engine as ME
    _Reader = ME.VideoReader
    for twice in (False, True):
        gate = Gate()
        class Reader:
            def __init__(self, path, *a, **kw):
                self._r, self._name = _Reader(path, *a, **kw), os.path.basename(path)
            def get_batch(self, idx):
                if "inpainted" in self._name:
                    gate.at(self._name)
                return self._r.get_batch(idx)
            def __getattr__(self, name):
                return getattr(self._r, name)
            def __len__(self):
                return len(self._r)
        ME.VideoReader = Reader
        for k, v in {"inpainted_folder": str(FIX / "inpainted"), "original_folder": str(FIX / "orig"),
                     "mask_folder": str(FIX / "mask"), "output_folder": str(tmp / f"mg_out_{twice}"),
                     "encoding_encoder": "Force CPU", "codec": "H.264", "use_gpu": False, "resume": False,
                     "output_format": "Full SBS", "pad_to_16_9": False, "split_lr": False,
                     "delete_sbs_after_split": False, "dnxhr_fullres_split": False}.items():
            st.setProperty(k, v)
        pump(0.3)
        exercise("Merging" + (" (twice)" if twice else ""), mg, "mgStopAfter", "merging", mg.start, gate, twice)
    ME.VideoReader = _Reader

print()
_lj = (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None) == h0
print("real last_jobs.json unchanged:", _lj)
if not _lj:
    fails.append("real last_jobs.json changed")
print("FAILED:" if fails else "ALL OK", fails if fails else "")
for name in ("DepthCrafter", "Splatting", "Inpainting", "InpaintingV2", "Merging", "SceneCut", "DepthEdit"):
    try: objs[name].shutdown()
    except Exception: pass
_fixtures.exit_now(1 if fails else 0)
