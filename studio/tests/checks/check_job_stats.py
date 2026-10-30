"""The live job numbers (job_stats.py) in the real app, offscreen: the chunk
count equals Inpainting's own loop; the step bars the pipelines print give
DepthCrafter's window and the s/it (it/s turned round), ONE sample per step
however often tqdm redraws it; Inpainting's chunk hook reports each chunk and
puts the engine back; the rows under DepthCrafter's and Inpainting's progress
bars show them; the VRAM pill (NVML) shows and publishes only on a change;
the stage strip numbers 1-4 and not Extra. Configs and last_jobs.json
redirected."""
import os, sys, tempfile, hashlib, time, types
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
from PySide6.QtCore import QCoreApplication, QEventLoop, QMetaObject, QObject, QPointF, Q_ARG, qInstallMessageHandler
warns = []
qInstallMessageHandler(lambda t, ctx, m: warns.append(m) if (".qml" in m or "qml" in m.lower()) else None)
from PySide6.QtGui import QGuiApplication
from studio import app as A
from studio import job_stats as J

fails = []
def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok: fails.append(label)
def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        QCoreApplication.processEvents(QEventLoop.AllEvents, 10); time.sleep(0.005)
def walk(it, out):
    for c in it.childItems():
        out.append(c); walk(c, out)
    return out

# ---- the chunk count is Inpainting's loop, counted
def loop(frames, chunk, overlap):
    stride, n = max(1, chunk - overlap), 0
    for i in range(0, frames, stride):
        if i > 0 and overlap > 0 and (min(i + chunk, frames) - i) <= overlap:
            continue
        n += 1
    return n
cases = [(171, 25, 3), (25, 25, 3), (26, 25, 3), (47, 25, 3), (48, 25, 3), (1, 25, 3), (500, 23, 5), (60, 25, 0)]
bad = [(c, J.chunk_count(*c), loop(*c)) for c in cases if J.chunk_count(*c) != loop(*c)]
check("chunk_count == the engine loop", not bad, str(bad))

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
objs["Shell"].setProperty("uiScale", "1.0")
win.show(); pump(0.8)
S = objs["JobStats"]
emits = [0]
S.changed.connect(lambda: emits.__setitem__(0, emits[0] + 1))

# ---- DepthCrafter: bars through the real stderr (StreamTee)
S.begin("depthcrafter"); pump(0.1); emits[0] = 0
bar = "\rWindow 2/5:  40%|####      | 2/5 [00:05<00:07,  2.50s/it]"
for _ in range(30):
    sys.stderr.write(bar)          # tqdm redraws the same step many times
pump(0.3)
check("DC: the window and the s/it from the bar", S.property("chunk") == 2 and S.property("chunks") == 5
      and abs(S.property("rate") - 2.5) < 1e-9, f"{S.property('chunk')}/{S.property('chunks')} {S.property('rate')}")
check("...one publish for 30 redraws of one step", emits[0] == 1, str(emits[0]))
sys.stderr.write("\rWindow 2/5: 100%|##########| 5/5 [00:12<00:00,  2.40s/it]\n"); pump(0.3)
check("...a finished window counts as done", S.property("done") == 2, str(S.property("done")))
sys.stderr.write("\r 60%|######    | 3/5 [00:01<00:00,  4.00it/s]"); pump(0.3)
check("it/s is turned into s/it", abs(S.property("rate") - 0.25) < 1e-9, str(S.property("rate")))

stage = [c for c in walk(win.contentItem(), []) if c.objectName() == "stage"][0]
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 0), Q_ARG("QVariant", True)); pump(1.0)
dcp = stage.property("pages").property(0).toQObject()
def shown(page, name):
    items = [c for c in walk(page, []) if c.objectName() == name and c.isVisible()]
    return items[0].property("text") if len(items) == 1 else f"<{len(items)} visible>"
bars = [c for c in walk(dcp, []) if c.objectName() != "clipBar" and c.metaObject().className().startswith("ShimmerBar")
        and c.isVisible()]
check("DC page: ring, s/it, iterations and the clip bar -- no batch bar", not bars
      and all(not shown(dcp, n).startswith("<") for n in ("jobRingChunk", "jobRateText", "jobIterText", "clipPercent")),
      f"batch bars {len(bars)}")
# window 2 of 5 finished, then step 3/5 of an unlabelled bar: the ring is window 2
# (done: 100%), the clip 2 of 5 windows (40%), the rate 4 it/s, iteration 3 / 5
got = (shown(dcp, "jobRingUnit") + " " + shown(dcp, "jobRingChunk"), shown(dcp, "jobRingPercent"), shown(dcp, "jobRateText"), shown(dcp, "jobRateUnit"),
       shown(dcp, "jobIterText"), shown(dcp, "clipPercent"))
check("...the ring is the window, the bar the clip, then the rate and the iteration",
      got == ("window 2 / 5", "100%", "4.00", "it/sec", "Inference Step 3 / 5", "40%"), str(got))

# ---- Inpainting: the chunk hook, on a stand-in engine module
calls = []
fake = types.SimpleNamespace()
fake.spatial_tiled_process = lambda *a, **k: calls.append("tile") or "latents"
class FakeEngine:
    def process_single_video(self, **kw):
        fake.spatial_tiled_process(); fake.spatial_tiled_process(); fake.spatial_tiled_process()
        return True, None
fake.InpaintingEngine = FakeEngine
orig = (fake.spatial_tiled_process, FakeEngine.process_single_video)
S.begin("inpainting", "v1"); pump(0.1)
# the ring moves with each step: a chunk = 2 x 2 tile passes x 5 steps = 20 steps
S.steps_per_chunk(20, 5); pump(0.05)
S.chunk_event("inpainting", 1, 4, False); pump(0.05)
for k in range(1, 6):
    sys.stderr.write(f"\r {k * 20}%|##        | {k}/5 [00:0{k}<00:05,  2.9{k}s/it]"); pump(0.02)
pump(0.2)
check("Inpainting: the chunk ring and the clip bar climb with the steps (5 of 20 steps: chunk 25%, clip 6%)",
      abs(S.property("part") - 0.25) < 1e-6 and abs(S.property("fraction") - (0 + 5 / 20) / 4) < 1e-6,
      f"part {S.property('part'):.4f} clip {S.property('fraction'):.4f}")
check("...and the iteration counter follows the bar", (S.property("step"), S.property("steps")) == (5, 5))
S.begin("inpainting", "v1"); pump(0.1)
seen = []
S.changed.connect(lambda: seen.append((S.property("chunk"), S.property("done"), S.property("chunks"))))
hook = J.InpaintingChunks(fake, S)
FakeEngine().process_single_video(input_video_path=str(tmp / "none.mp4"), frames_chunk=25, overlap=3, process_length=60)
pump(0.3)
check("Inpainting: the clip's chunk total up front", any(x[0] == 0 and x[2] == J.chunk_count(60, 25, 3) for x in seen), str(seen[:2]))
check("...each chunk reported running, then done", S.property("chunk") == 3 and S.property("done") == 3
      and (2, 1, 3) in seen and (2, 2, 3) in seen, str(seen))
hook.restore()
check("...and the engine put back afterwards", (fake.spatial_tiled_process, FakeEngine.process_single_video) == orig)
bare = types.SimpleNamespace(InpaintingEngine=type("E", (), {}))
try:
    J.InpaintingChunks(bare, S).restore(); stepped_aside = True
except Exception:
    stepped_aside = False
check("...and with nothing to hook it steps aside (never fails a job)", stepped_aside)
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 2), Q_ARG("QVariant", True)); pump(1.0)
ipp = stage.property("pages").property(2).toQObject()
got = (shown(ipp, "jobRingUnit") + " " + shown(ipp, "jobRingChunk"), shown(ipp, "jobRingPercent"), shown(ipp, "clipPercent"))
check("Inpainting page: chunk 3 / 3 done, the clip 100%", got == ("chunk 3 / 3", "100%", "100%"), str(got))

# ---- the tip ticker: Inpainting only, the whole tip, held; click for the next,
# which fades in rising from below (user, 2026-09-28: no more typewriter)
tickers = [c for c in walk(ipp, []) if c.metaObject().className().startswith("TipTicker") and c.isVisible()]
check("Inpainting has the tip ticker, DepthCrafter doesn't", len(tickers) == 1 and not any(
      c.objectName() == "tipText" and c.isVisible() for c in walk(dcp, [])), f"{len(tickers)}")
if tickers:
    tk = tickers[0]
    tt = [c for c in walk(tk, []) if c.objectName() == "tipText"][0]
    tk.setProperty("holdMs", 60000); pump(0.2)
    check("...the whole tip at once, no caret", tt.property("text") == tk.property("full") and tt.property("opacity") == 1,
          repr(tt.property("text")[-20:]))
    check("...inside the ring's height (the bar sits under it)", tk.property("height") < 30, str(tk.property("height")))
    from PySide6.QtTest import QTest
    from PySide6.QtCore import QPoint, QPointF, Qt
    i0 = tk.property("index")
    pt = tk.mapToScene(QPointF(tk.width() / 3, tk.height() / 2))
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(pt.x()), int(pt.y()))); pump(0.1)
    check("...a click fades the tip out", tt.property("opacity") < 1 and tk.property("index") == i0,
          f"{tt.property('opacity'):.2f}")
    pump(0.25)
    mid = tt.property("opacity")
    check("...then the next one fades in", tk.property("index") != i0 and mid < 1, f"{i0} -> {tk.property('index')}, {mid:.2f}")
    pump(0.5)
    check("...and settles, whole and in place", tt.property("opacity") == 1 and tt.property("text") == tk.property("full"))

# ---- Inpainting frames: the engine's own chunk lines place each chunk, so the
# line under the bar counts frames inpainted and the rate column shows the range
import logging as _logging
flog = _logging.getLogger("check.fake_inpaint")
flog.setLevel(_logging.INFO)
flog.propagate = False
fk = types.SimpleNamespace(logger=flog)
fk.spatial_tiled_process = lambda *a, **k: "latents"
marks = []
class FakeEngine2:
    def process_single_video(self, **kw):
        # 60 frames, chunk 25, overlap 10: 0-25 (resumed from cache), 15-40, 30-55, 45-60
        flog.info("Reused cached inference chunk 0-25 from x.pt")
        for a, b in ((15, 40), (30, 55), (45, 60)):
            flog.info(f"Starting inference for chunk {a}-{b} ({b - a} frames)...")
            pump(0.05)
            marks.append(("running", S.property("framesDone"), S.property("rangeFrom"), S.property("rangeTo"),
                          S.property("chunk")))
            fk.spatial_tiled_process()
            pump(0.05)
            marks.append(("done", S.property("framesDone"), S.property("chunk"), S.property("done")))
        return True, None
fk.InpaintingEngine = FakeEngine2
S.begin("inpainting", "v1"); pump(0.1)
hook2 = J.InpaintingChunks(fk, S)
FakeEngine2().process_single_video(input_video_path=str(tmp / "none.mp4"), frames_chunk=25, overlap=10,
                                   process_length=60)
pump(0.2)
check("Inpainting frames: a resumed chunk counts as inpainted, each chunk's range from the engine's line",
      marks[0] == ("running", 25, 15, 40, 2) and marks[2] == ("running", 40, 30, 55, 3),
      str(marks[:3]))
check("...frames inpainted = the end of the last finished chunk (overlap not counted twice)",
      [m[1] for m in marks if m[0] == "done"] == [40, 55, 60] and S.property("frames") == 60, str(marks))
check("...and the chunk ring still follows (chunk 4 of 4 done)", (S.property("chunk"), S.property("done"),
      S.property("chunks")) == (4, 4, 4), str((S.property("chunk"), S.property("done"), S.property("chunks"))))
hook2.restore()
check("...its log handler taken off afterwards", not flog.handlers, str(flog.handlers))
# Reverse runs the loop over the reversed clip: the range is shown in real frame numbers
marks.clear()
S.begin("inpainting", "v1"); pump(0.1)
hook2 = J.InpaintingChunks(fk, S)
FakeEngine2().process_single_video(input_video_path=str(tmp / "none.mp4"), frames_chunk=25, overlap=10,
                                   process_length=60, inpaint_direction="Reverse")
pump(0.2); hook2.restore()
check("...Reverse: the loop's 15-40 is the clip's frames 20-45", marks[0][2:4] == (20, 45), str(marks[0]))
# after the chunks, the render: the engine's "Hi-Res chunk k/n: frames a-b" lines,
# then the encode (encode_frames_to_mp4, wrapped for the run)
enc_calls = []
fk.encode_frames_to_mp4 = lambda *a, **k: enc_calls.append(S.property("phase")) or True
phases = []
class FakeEngine3:
    def process_single_video(self, **kw):
        phases.append(S.property("phase"))
        flog.info("Starting inference for chunk 0-25 (25 frames)...")
        fk.spatial_tiled_process()
        for k, (a, b) in enumerate(((0, 25), (25, 50), (50, 60)), start=1):
            flog.info(f"Hi-Res chunk {k}/3: frames {a}-{b}")
            pump(0.03)
            phases.append((S.property("phase"), S.property("renderDone"), S.property("renderTotal"),
                           S.property("rangeFrom"), S.property("rangeTo")))
        flog.info("\nHi-Res streaming finalize complete: 60 frames written.")
        pump(0.03)
        phases.append((S.property("phase"), S.property("renderDone")))
        fk.encode_frames_to_mp4("x")
        pump(0.03)
        return True, None
fk.InpaintingEngine = FakeEngine3
S.begin("inpainting", "v1"); pump(0.05)
hook3 = J.InpaintingChunks(fk, S)
FakeEngine3().process_single_video(input_video_path=str(tmp / "none.mp4"), frames_chunk=25, overlap=0, process_length=60)
pump(0.1)
hook3.restore()
check("Render: a new clip starts in the inpaint phase", phases[0] == "inpaint", str(phases[:1]))
check("...each Hi-Res chunk line starts the bar again: frames rendered before it, of the clip, and its range",
      phases[1:4] == [("render", 0, 60, 0, 25), ("render", 25, 60, 25, 50), ("render", 50, 60, 50, 60)], str(phases[1:4]))
check("...'complete' fills it", phases[4] == ("render", 60), str(phases[4]))
check("...then the encode (the engine's encode call wrapped)", enc_calls == ["encode"] and S.property("phase") == "encode",
      str(enc_calls))
check("...and the encode call put back afterwards", fk.encode_frames_to_mp4.__name__ == "<lambda>")
ab = [c for c in walk(ipp, []) if c.objectName() == "actionBar"][0]
st = [c for c in walk(ab, []) if c.objectName() == "abStatus"][0]
cb = [c for c in walk(ab, []) if c.objectName() == "clipBar"][0]
ab.setProperty("status", "Processing video 1 of 3"); ab.setProperty("busy", True)
S.begin("inpainting", "v1"); S.chunk_event("inpainting", 3, 3, True); S.frames_event(60, 60, 50, 60)
S.phase_event("render", 15, 60); pump(0.2)
check("Page, render: the bar is the render's own (25%), the line says so",
      abs(cb.property("value") - 0.25) < 1e-6 and st.property("text") == "Hi-res blend: 15 / 60 frames rendered"
      and shown(ipp, "clipPercent") == "render 25%", f"{cb.property('value')} {st.property('text')!r} {shown(ipp, 'clipPercent')}")
S.phase_event("encode"); pump(0.2)
check("Page, encode: a full bar and 'Encoding the final video…'", abs(cb.property("value") - 1) < 1e-6
      and st.property("text") == "Encoding the final video…" and shown(ipp, "clipPercent") == "encode",
      f"{cb.property('value')} {st.property('text')!r}")
ab.setProperty("busy", False); pump(0.05)

# the page: the line under the bar and the range under the iteration counter
S.begin("inpainting", "v1"); pump(0.05)
S.chunk_event("inpainting", 3, 4, False); S.frames_event(60, 40, 30, 55); pump(0.1)
ab = [c for c in walk(ipp, []) if c.objectName() == "actionBar"][0]
st = [c for c in walk(ab, []) if c.objectName() == "abStatus"][0]
ab.setProperty("status", "Processing video 1 of 3"); ab.setProperty("busy", False); pump(0.05)
idle_text = st.property("text")
ab.setProperty("busy", True); pump(0.1)
check("Inpainting: while it runs the line under the bar is frames inpainted, not the batch",
      st.property("text") == "40 / 60 frames inpainted" and idle_text == "Processing video 1 of 3",
      f"{st.property('text')!r} / idle {idle_text!r}")
ab.setProperty("status", "Stopping..."); pump(0.1)
check("...but Stopping... still shows", st.property("text") == "Stopping...", st.property("text"))
ab.setProperty("busy", False); pump(0.05)
check("...and the chunk's frames under the iteration counter", shown(ipp, "jobRangeText") == "Frames 30–55",
      shown(ipp, "jobRangeText"))
# the ETA (user, 2026-10-09): unknown before a step; on the first clip without the render
check("Inpainting ETA: N/A before the first step", shown(ipp, "jobEtaText") == "Clip ETA N/A", shown(ipp, "jobEtaText"))
S._on_per_chunk(10, 10); S._on_bar("1/10 [00:30<04:30, 30.00s/it]"); pump(0.1)
check("...and after it: the first step of a pass carries its setup", shown(ipp, "jobEtaText") == "Clip ETA N/A",
      shown(ipp, "jobEtaText"))
S._on_bar("2/10 [00:60<04:00, 30.00s/it]"); pump(0.1)
check("...then the chunks left from the steps' s/it, '+ render' on the first clip",   # 8 x 30 s + 1 chunk x 300 s
      shown(ipp, "jobEtaText") == "Clip ETA 9 min + render", shown(ipp, "jobEtaText"))
S._eta_tail_per_frame = 1.5; S._on_bar("3/10 [00:90<03:30, 30.00s/it]"); pump(0.1)   # 7 x 30 + 300 + 60 frames x 1.5 s
_eta = shown(ipp, "jobEtaText")
check("...and a finish time once the render is known", _eta.startswith("Clip ETA 10 min · ") and len(_eta) > 13, _eta)
# its batch's ETA on the Progress heading's right end (user, 2026-10-24: under the bar a long status
# line -- "Hi-res blend: 0 / 20 frames rendered" -- ran into it)
S.batch_begin("inpainting", [("D:/in/A_640_splatted4.mp4", 60), ("D:/in/B_640_splatted4.mp4", 60)])
S.batch_clip("inpainting", "A_640_splatted4"); pump(0.1)
_bh = [c for c in walk(ab, []) if c.objectName() == "batchEtaHeader"]   # V1's bar (V2's has its own)
_cb = [c for c in walk(ipp, []) if c.objectName() == "clipBar"][0]
check("Inpainting's Batch ETA on the Progress heading, not on the status line",
      len(_bh) == 1 and _bh[0].isVisible() and str(_bh[0].property("text")).startswith("Batch ")
      and not any(c.objectName() == "batchEtaText" and c.isVisible() for c in walk(ipp, [])),
      str(_bh[0].property("text")) if _bh else "no heading item")
_hy, _by = _bh[0].mapToItem(ipp, 0, 0).y(), _cb.mapToItem(ipp, 0, 0).y()
_ab = [c for c in walk(ipp, []) if c.objectName() == "actionBar"][0]
check("...on the heading's line, above the bar, at its right end", _hy < _by
      and abs((_bh[0].mapToItem(_ab, _bh[0].width(), 0).x()) - _ab.width()) < 40,
      f"y {_hy:.0f} vs bar {_by:.0f}; right {_bh[0].mapToItem(_ab, _bh[0].width(), 0).x():.0f} of {_ab.width():.0f}")
check("...not on DepthCrafter's page while Inpainting runs",
      not any(c.objectName() == "jobEtaText" and c.isVisible() for c in walk(dcp, [])))
# DepthCrafter's ETA (user, 2026-10-12): head + r x window frames to run + tail, r from the bar
S.begin("depthcrafter"); pump(0.05)
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 0), Q_ARG("QVariant", True)); pump(1.0)
S.dc_event("clip"); S.dc_event("plan", {"frames": 100, "steps": 4, "window": 120, "overlap": 100, "key": "640x384"})
pump(0.1)
check("DepthCrafter ETA: N/A before a step", shown(dcp, "jobEtaText") == "Clip ETA N/A", shown(dcp, "jobEtaText"))
S._on_bar("Window 1/1:  25%|#   | 1/4 [00:90<04:30, 90.00s/it]")
S._on_bar("Window 1/1:  50%|##  | 2/4 [02:30<02:00, 60.00s/it]"); pump(0.1)
check("...then the steps left x r, '+ encode/decode' while the size has no timed clip",   # 2 steps x 60 s
      shown(dcp, "jobEtaText") == "Clip ETA 2 min + encode/decode", shown(dcp, "jobEtaText"))
S._dc.rates["tail"]["640x384"] = 0.6; S._on_bar("Window 1/1:  75%|### | 3/4 [03:30<01:00, 60.00s/it]"); pump(0.1)
_eta = shown(dcp, "jobEtaText")                                  # 1 step x 60 s + 100 frames x 0.6 s
check("...and a finish time once its tail is known", _eta.startswith("Clip ETA 2 min · ") and len(_eta) > 13, _eta)
check("...not on Inpainting's page", not any(c.objectName() == "jobEtaText" and c.isVisible() for c in walk(ipp, [])))
# the batch's ETA beside it (user, 2026-10-21): the clip's 2 min + 200 frames after it at the
# 1.2 s a frame this clip projects (its 120 s / 100 frames)
check("Batch ETA: none for a run of one clip", not any(c.objectName() == "batchEtaText" and c.isVisible()
      for c in walk(dcp, [])))
S.batch_begin("depthcrafter", [("D:/in/A.mp4", 100), ("D:/in/B.mp4", 200)]); S.batch_clip("depthcrafter", "A"); pump(0.1)
_b = shown(dcp, "batchEtaText")
check("...with clips after this one, the whole batch's beside the clip's", _b.startswith("Batch 6 min · "), _b)
_be = [c for c in walk(dcp, []) if c.objectName() == "batchEtaText"][0]
_je = [c for c in walk(dcp, []) if c.objectName() == "jobEtaText"][0]
_cp = [c for c in walk(dcp, []) if c.objectName() == "clipPercent"][0]
_tt = [c for c in walk(dcp, []) if c.objectName() == "tipTicker"][0]
_bp, _cpp = _be.mapToScene(QPointF(0, 0)), _cp.mapToScene(QPointF(0, 0))
_tr = _tt.mapToScene(QPointF(_tt.property("width"), 0)).x()
# user 2026-10-27: above the percentage at the tip line's end, not beside the clip's (it crowded the status)
check("...above the percentage, right-aligned with it, clear of the tip", _bp.y() + _be.property("height") <= _cpp.y()
      and abs(_bp.x() + _be.property("width") - (_cpp.x() + _cp.property("width"))) < 1 and _tr < _bp.x(),
      f"batch {_bp.x()},{_bp.y()} w{_be.property('width')} / percent {_cpp.x()},{_cpp.y()} / tip ends {_tr}")
S.batch_clip("depthcrafter", "B"); pump(0.1)
check("...and gone on the last clip", not _be.isVisible())
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 2), Q_ARG("QVariant", True)); pump(1.0)   # back, as before
# how long the job took, after the finish status (user, 2026-10-04: every stage, as
# Inpainting's "Processing completed in ...")
import time as _time
dab = [c for c in walk(dcp, []) if c.objectName() == "actionBar"][0]
dst = [c for c in walk(dab, []) if c.objectName() == "abStatus"][0]
def _job(status_end, took_s):
    dab.setProperty("status", "Processing video 1 of 2"); dab.setProperty("busy", True); pump(0.05)
    dab.setProperty("jobStartedAt", _time.time() * 1000 - took_s * 1000)
    dab.setProperty("busy", False); dab.setProperty("status", status_end); pump(0.1)
    return dst.property("text")
_t1 = _job("Batch complete.", 725)
check("Job time: the finish status says how long the job took", _t1 == "Batch complete.  ·  Completed in 12m 05s", repr(_t1))
_t2 = _job("Processing stopped by user.", 3733)
check("...a stopped one says 'Stopped after'", _t2 == "Processing stopped by user.  ·  Stopped after 1h 02m 13s", repr(_t2))
dab.setProperty("jobEndedAt", _time.time() * 1000 - 10000)
dab.setProperty("status", "Loaded clip_a"); pump(0.1)
check("...and goes once the status moves on", dst.property("text") == "Loaded clip_a", repr(dst.property("text")))
_ipab = [c for c in walk(ipp, []) if c.objectName() == "actionBar"][0]
check("...Inpainting keeps its own ('Processing completed in ...')", _ipab.property("jobTime") is False)
check("DepthCrafter has no frame range", not any(c.objectName() == "jobRangeText" and c.isVisible()
      for c in walk(dcp, [])))
labels = [c.property("text") for c in walk(ipp, []) if c.metaObject().className().startswith("QQuickText")
          and c.isVisible()]
check("Current Clip Info has Batch Progress, like the other stages", any(str(t).startswith("Batch Progress")
      for t in labels))

# ---- Merging (user, 2026-09-27): the grain match's bar first, then the render's,
# each from 0, read off the engine's own console bars (cli_utils.draw_progress_bar)
from core.common.cli_utils import draw_progress_bar
S.begin("merging"); pump(0.05)
QMetaObject.invokeMethod(stage, "show", Q_ARG("QVariant", 3), Q_ARG("QVariant", True)); pump(0.8)
mgp = stage.property("pages").property(3).toQObject()
mab = [c for c in walk(mgp, []) if c.objectName() == "actionBar"][0]
mst = [c for c in walk(mab, []) if c.objectName() == "abStatus"][0]
mbar = [c for c in walk(mab, []) if c.objectName() == "batchBar"][0]
mab.setProperty("busy", True); mab.setProperty("status", "Processing 1/3: clip_a_inpainted_right_eye.mp4"); pump(0.1)
seen_m = []
S.changed.connect(lambda: seen_m.append((S.property("mergePhase"), round(S.property("mergePercent")))))
for k in range(0, 13):
    draw_progress_bar(k, 30, prefix="  Grain match:", suffix="clip_a"); pump(0.01)
pump(0.2)
check("Merging, grain match: the bar is the match's own, the line says so",
      S.property("mergePhase") == "grain" and abs(mbar.property("value") - 0.4) < 0.02
      and shown(mgp, "phasePercent") == "grain match 40%" and mst.property("text") == "Matching grain: clip_a",
      f"{S.property('mergePhase')} {mbar.property('value'):.2f} {shown(mgp, 'phasePercent')!r} {mst.property('text')!r}")
n_before = len(seen_m)
for _ in range(20):
    draw_progress_bar(12, 30, prefix="  Grain match:", suffix="clip_a")
pump(0.2)
check("...a redraw that does not move publishes nothing", len(seen_m) == n_before, f"{len(seen_m) - n_before}")
draw_progress_bar(30, 30, prefix="  Grain match:", suffix="clip_a"); pump(0.1)
mab.setProperty("status", "Processing frames 1-35/400..."); pump(0.05)
draw_progress_bar(35, 400, prefix="  Encoding clip_a_merged.mp4:"); pump(0.2)
check("...then the render: the bar starts again, and the engine's frame line stays",
      S.property("mergePhase") == "render" and abs(mbar.property("value") - 0.0875) < 0.02
      and shown(mgp, "phasePercent") == "render 9%" and mst.property("text") == "Processing frames 1-35/400...",
      f"{S.property('mergePhase')} {mbar.property('value'):.3f} {shown(mgp, 'phasePercent')!r} {mst.property('text')!r}")
check("...the grain bar went 0 -> 100 first, then the render from 0",
      [p for p, _ in seen_m][:1] == ["grain"] and ("grain", 100) in seen_m and seen_m[-1][0] == "render",
      str(seen_m[-4:]))
draw_progress_bar(3, 10, prefix="  Splitting into L/R videos:"); pump(0.2)
check("...and the L/R split after it", shown(mgp, "phasePercent") == "split L/R 30%"
      and mst.property("text").startswith("Splitting"), f"{shown(mgp, 'phasePercent')!r} {mst.property('text')!r}")
mab.setProperty("busy", False); pump(0.1)
check("...idle: the clips bar again, no phase label", not any(c.objectName() == "phasePercent" and c.isVisible()
      for c in walk(mgp, [])))

# ---- VRAM pill
V = objs["Vram"]
pill = win.findChild(QObject, "vramPill")
if V.property("available"):
    vt = [c for c in walk(win.contentItem(), []) if c.objectName() == "vramText"][0]
    check("VRAM pill shows used / total", pill.property("visible") and vt.property("text").endswith(" GB")
          and "/" in vt.property("text"), vt.property("text"))
    n = [0]
    V.changed.connect(lambda: n.__setitem__(0, n[0] + 1))
    for _ in range(20):
        QMetaObject.invokeMethod(V, "poll")
    check("...and publishes only when the figure moves (not per poll)", n[0] <= 1, str(n[0]))
    # bug sweep 2026-10-09 (A1): 9.9 -> 10.0 GB gained a digit, widened the pill and
    # with it the window's minimum width, mid-job
    sizes = []
    for used in (9.9, 10.0, 15.9, 0.0):
        V._publish(used, 16.0); pump(0.1)
        sizes.append((vt.property("text"), round(pill.property("width"), 2), win.property("minimumWidth")))
    check("...the pill and the window's minimum width stay put as the figure gains a digit",
          len({w for _t, w, _m in sizes}) == 1 and len({m for _t, _w, m in sizes}) == 1, str(sizes))
else:
    check("(no NVML here: the pill stays hidden)", not pill.property("visible"))

# ---- the stage strip: 1.-4., no number on Extra
nums = [c for c in walk(win.contentItem(), []) if c.objectName() == "tabNumber" and c.isVisible()]
texts = sorted(c.property("text") for c in nums)
check("stage strip: 1. to 4., Extra unnumbered", texts[:4] == ["1.", "2.", "3.", "4."] and "5." not in texts, str(texts))

studio_warns = [w for w in warns if "Studio" in w or "qml" in w.lower()]
check("no QML warnings", not studio_warns, "\n    " + "\n    ".join(studio_warns[:12]))
_lj = h0 == (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None); print("last_jobs unchanged:", _lj)
print("FAILS", len(fails), fails, flush=True)
_fixtures.exit_now(0 if not fails and _lj else 1)
