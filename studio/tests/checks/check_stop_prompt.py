"""The Stop prompt (audit 2026-09-28 #13, user 2026-09-29), in the real app,
offscreen. Every stage's Stop asks first: "Stop now" (the item in progress is
cut off), the stage's safe stop where it has one ("Stop after this clip /
chunk / segment / these scenes": work is kept and picked up again), and
"Keep running" -- Enter picks the safe choice. Splatting for real (a render
held inside clip 1): Keep running carries on; the safe choice arms Stop After
Clip; Stop now ends the run with clip 2 never started and no partial file.
DepthCrafter, Inpainting and Scene Cut: each answer reaches the right stop
(their jobs stood in for). Quit during a real Merging run: asked (Quit now /
Keep running, the message names Stop After Clip); Quit now's stop ends the
job and its ffmpeg at once, leaving no output. Configs and last_jobs.json redirected.

    check_stop_prompt.py          every stage
    check_stop_prompt.py sp dc    only these (sp dc ip sc q)
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
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, Q_ARG, QPointF, QPoint, Qt, QObject
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from studio import app as A

WANT = set(a for a in sys.argv[1:] if not a.startswith("-")) or {"sp", "dc", "ip", "sc", "q"}

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

toasts = []
for ctl in objs.values():
    sig = getattr(ctl, "toast", None)
    if sig is not None and hasattr(sig, "connect"):
        sig.connect(lambda k, t, m: toasts.append((k, t, m)))
pump(0.8)
prompt = win.findChild(QObject, "prompt")
def prompt_buttons():
    if prompt is None or not prompt.property("visible"):
        return []
    return [b for b in find_all(prompt.property("contentItem"),
                                lambda c: c.metaObject().className().startswith("AccentButton"))
            if b.isVisible()]
def answer(text):
    b = [b for b in prompt_buttons() if b.property("text") == text]
    if not b:
        return False
    click(b[0]); pump(0.5)
    return True
def asked(stop_btn, label, want):
    click(stop_btn); pump(0.6)
    texts = [b.property("text") for b in prompt_buttons()]
    check(f"{label}: Stop asks first -- {want}", texts == want, str(texts))
    focused = [b.property("text") for b in prompt_buttons() if b.property("activeFocus")]
    safe = want[1] if len(want) == 3 else "Keep running"
    check(f"{label}: ...Enter picks {safe!r}", focused == [safe], str(focused))
    return texts == want

class Alive:
    """Stands in for a job thread: alive until joined."""
    def __init__(self):
        self.done = threading.Event()
        self.t = threading.Thread(target=self.done.wait, daemon=True); self.t.start()
    def is_alive(self): return self.t.is_alive()
    def join(self, timeout=None): self.done.set(); self.t.join(timeout)

def refresh(ctl):
    ctl.stateChanged.emit(); pump(0.3)

# ---- Splatting: a real render, held inside clip 1 ----------------------------------
if "sp" in WANT:
    print("Splatting")
    show(1)
    sp = objs["Splatting"]
    FIX = _fixtures.copy("small", tmp / "sp")
    out = tmp / "sp_out"
    for k, v in {"input_source_clips": str(FIX / "src").replace("\\", "/"),
                 "input_depth_maps": str(FIX / "depth").replace("\\", "/"),
                 "output_splatted": str(out).replace("\\", "/"), "multi_map": False,
                 "auto_save_sidecar": False, "move_to_finished": False, "enable_full_res": False,
                 "enable_low_res": True, "dual_output": False, "auto_convergence_mode": "Off",
                 "low_res_batch_size": "4"}.items():
        sp._set(k, v)
    import core.splatting.render_processor as RP
    _render = RP.RenderProcessor.render_video
    gate = Gate()
    def render(self, *a, **kw):
        gate.at(os.path.basename(str(kw.get("output_video_path_base", ""))))
        return _render(self, *a, **kw)
    RP.RenderProcessor.render_video = render
    stop_btn = find(win.contentItem(), lambda c: c.objectName() == "spStop")
    toasts.clear()
    sp.start()
    check("Splatting: clip 1 is rendering", until(lambda: gate.running.is_set(), 120))
    want = ["Stop now", "Stop after this clip", "Keep running"]
    if asked(stop_btn, "Splatting", want):
        answer("Keep running")
        check("Splatting: Keep running carries on",
              sp.property("busy") and not sp._stop.is_set() and not sp.property("stopAfterClip"))
        asked(stop_btn, "Splatting", want); answer("Stop after this clip")
        check("Splatting: the safe choice arms Stop After Clip", sp.property("stopAfterClip") and not sp._stop.is_set())
        asked(stop_btn, "Splatting", want); answer("Stop now")
        check("Splatting: Stop now stops it", sp._stop.is_set())
    gate.release.set()
    check("Splatting: the run ends", until(lambda: not sp.property("busy"), 120))
    pump(0.8)
    check("Splatting: clip 2 never started", len(gate.clips) == 1, str(gate.clips))
    left = [p.name for p in out.rglob("*") if p.is_file()]
    check("Splatting: no partial file is left", not [n for n in left if n.endswith(".part")], str(left))
    last = toasts[-1] if toasts else ("", "", "")
    check("Splatting: the toast says it stopped", "stop" in (last[1] + last[2]).lower(), str(last))
    RP.RenderProcessor.render_video = _render

# ---- DepthCrafter / Inpainting / Scene Cut: each answer reaches its stop ----------
def answers(label, btn, want, arm, disarm, flags):
    """flags() -> (stop-now set, safe stop set)."""
    for choice, now, safe in (("Keep running", False, False), (want[1], False, True), ("Stop now", True, True)):
        arm(); pump(0.3)
        if asked(btn, label, want):
            answer(choice)
            got = flags()
            check(f"{label}: {choice!r} -> stop now {now}, safe stop {safe}", got == (now, safe), str(got))
        disarm(); pump(0.3)

if "dc" in WANT:
    print("DepthCrafter")
    show(0)
    dc = objs["DepthCrafter"]
    def arm():
        dc._stop.clear(); dc._stop_now.clear(); dc._thread = Alive(); dc._task = "batch"; refresh(dc)
    def disarm():
        dc._thread.join(1); dc._thread = None; dc._task = None; dc._stop.clear(); dc._stop_now.clear(); refresh(dc)
    answers("DepthCrafter", find(win.contentItem(), lambda c: c.objectName() == "dcCancel"),
            ["Stop now", "Stop after this segment", "Keep running"], arm, disarm,
            lambda: (dc._stop_now.is_set(), dc._stop.is_set()))

if "ip" in WANT:
    print("Inpainting")
    show(2)
    ip = objs["Inpainting"]
    def arm():
        ip._stop.clear(); ip._stop_now.clear(); ip._busy = True; refresh(ip)
    def disarm():
        ip._busy = False; ip._stop.clear(); ip._stop_now.clear(); refresh(ip)
    answers("Inpainting", find(win.contentItem(), lambda c: c.objectName() == "ipStop"),
            ["Stop now", "Stop after this chunk", "Keep running"], arm, disarm,
            lambda: (ip._stop_now.is_set(), ip._stop.is_set()))

if "sc" in WANT:
    print("Scene Cut")
    show(4)
    extra = stage_item.property("pages").property(4).toQObject()
    extra.setProperty("tool", 0); pump(0.8)
    sc = objs["SceneCut"]
    sc.ensureLoaded()
    check("Scene Cut: the engine loads", until(lambda: sc.property("ready"), 90), sc.property("loadError"))
    eng = sc._ensure_engine()
    def arm():
        eng.abort_flag.clear(); eng.abort_now.clear(); sc._busy = True; refresh(sc)
    def disarm():
        sc._busy = False; eng.abort_flag.clear(); eng.abort_now.clear(); refresh(sc)
    answers("Scene Cut", find(win.contentItem(), lambda c: c.objectName() == "scAbort"),
            ["Stop now", "Stop after these scenes", "Keep running"], arm, disarm,
            lambda: (eng.abort_now.is_set(), eng.abort_flag.is_set()))


# ---- Quit during a job: asked, then everything stops now --------------------------
if "q" in WANT:
    print("Quit")
    show(3)
    mg = objs["Merging"]; st = mg.property("settings")
    FIX = _fixtures.copy("merge", tmp / "mg")
    out = tmp / "mg_out"
    import studio.stages.merging.engine as ME
    from studio.child_procs import descendants, _snapshot
    _Reader = ME.VideoReader
    encoders = lambda: [n for _, n in descendants(os.getpid(), _snapshot()) if n == "ffmpeg.exe"]  # noqa: E731
    gate = Gate()
    class Reader:
        def __init__(self, path, *a, **kw):
            self._r, self._name = _Reader(path, *a, **kw), os.path.basename(path)
            self._n = 0
        def get_batch(self, idx):
            if "inpainted" in self._name:
                self._n += 1
                if self._n >= 2 and encoders():   # the encoder is running: a chunk is written
                    gate.at(self._name)
            return self._r.get_batch(idx)
        def __getattr__(self, name):
            return getattr(self._r, name)
        def __len__(self):
            return len(self._r)
    ME.VideoReader = Reader
    for k, v in {"inpainted_folder": str(FIX / "inpainted"), "original_folder": str(FIX / "orig"),
                 "mask_folder": str(FIX / "mask"), "output_folder": str(out),
                 "encoding_encoder": "Force CPU", "codec": "H.264", "use_gpu": False, "resume": False,
                 "output_format": "Full SBS", "pad_to_16_9": False, "split_lr": False,
                 "delete_sbs_after_split": False, "dnxhr_fullres_split": False, "batch_chunk_size": "2",
                 "grain_enable": False, "grain_automatch": False}.items():
        st.setProperty(k, v)
    pump(0.3)
    mg.start()
    check("Quit: a Merging run is encoding", until(lambda: gate.running.is_set(), 120))
    before = encoders()
    win.close(); pump(0.6)
    texts = [b.property("text") for b in prompt_buttons()]
    msg = prompt.property("message") if prompt is not None else ""
    check("Quit: closing asks first -- Quit now / Keep running", texts == ["Quit now", "Keep running"], str(texts))
    check("Quit: ...and says it stops now, pointing to Stop After Clip",
          "stops it now" in msg and "Stop After Clip" in msg, msg)
    answer("Keep running")
    check("Quit: Keep running keeps the window and the job", win.isVisible() and mg.property("busy"))
    ran = A.stop_running_jobs_now(objs)      # what Quit now runs before the window goes
    check("Quit: Quit now stops the running stage", ran == ["Merging"], str(ran))
    check("Quit: ...its encoder is ended at once", bool(before) and until(lambda: not encoders(), 3),
          f"{before} -> {encoders()}")
    gate.release.set()
    check("Quit: the run ends", until(lambda: not mg.property("busy"), 60))
    pump(0.8)
    left = [p.name for p in out.rglob("*") if p.is_file()]
    check("Quit: no output and no partial file is left", not left, str(left))
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
