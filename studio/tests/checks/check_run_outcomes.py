"""How each stage's run ends (audit 2026-09-28 #3): a run that produced
nothing -- an empty folder, a model that never loaded, every clip failing --
must not toast green nor hand its (empty) output to the next stage; a partial
run says "N done, M failed"; a clean run still toasts success and records the
handoff. The real app offscreen, each stage's real Start; the model / GPU
parts are stood in for where a real one would be needed. Configs and
last_jobs.json redirected.

    check_run_outcomes.py            every stage
    check_run_outcomes.py dc sp      only these (dc sp ip ip2 mg sc)
"""
import os, sys, tempfile, hashlib, time, types, json, shutil
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
from PySide6.QtCore import QCoreApplication, QEventLoop
from PySide6.QtGui import QGuiApplication
from studio import app as A

WANT = set(a for a in sys.argv[1:] if not a.startswith("-")) or {"dc", "sp", "ip", "ip2", "mg", "sc"}

qapp = QGuiApplication(sys.argv)
engine, objs = A.build(qapp)
win = engine.rootObjects()[0]
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
def handoff(stage):
    try:
        return stage in json.loads(Path(jh._RECORD_PATH).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
def forget_handoffs():
    Path(jh._RECORD_PATH).unlink(missing_ok=True)

toasts = []
for ctl in objs.values():
    sig = getattr(ctl, "toast", None)
    if sig is not None and hasattr(sig, "connect"):
        sig.connect(lambda k, t, m: toasts.append((k, t, m)))
def run_ends(label, ctl, stage, start, want_kind, want_handoff, busy=lambda c: c.property("busy"), fresh=True):
    """Start a run, wait for it to end, and check its toast and handoff."""
    if fresh:
        forget_handoffs()
    toasts.clear()
    start()
    until(lambda: busy(ctl), 5)
    ended = until(lambda: not busy(ctl), 120)
    pump(0.3)
    last = [t for t in toasts if t[1] not in ("Settings saved",)][-1:] or [("", "", "")]
    check(f"{label}: the run ends", ended)
    check(f"{label}: toast is {want_kind}", last[0][0] == want_kind, str(last[0]))
    check(f"{label}: handoff {'recorded' if want_handoff else 'NOT recorded'}", handoff(stage) == want_handoff)
    return last[0]

pump(0.8)

# ---- DepthCrafter: the model and the per-clip job stood in for -----------------
if "dc" in WANT:
    print("DepthCrafter")
    dc = objs["DepthCrafter"]
    FIX = _fixtures.copy("small", tmp / "dc")
    from depthcrafter.utils import get_full_video_output_filename

    class Demo:
        mode = "ok"         # ok / load / fail
        def __init__(self, **kw):
            if Demo.mode == "load":
                raise OSError("weights not found (stand-in)")
            self.pipe = None
        @staticmethod
        def get_latent_checkpoint_path(base_output_folder, basename):
            return os.path.join(base_output_folder, f"{basename}_latents.ckpt")
        def run(self, **kw):
            if Demo.mode == "fail":
                return None, {"status": "failure_stand_in"}
            out = kw["base_output_folder"]; os.makedirs(out, exist_ok=True)
            p = os.path.join(out, get_full_video_output_filename(kw["original_video_basename_override"], "mp4"))
            Path(p).write_bytes(b"mp4")
            return p, {"status": "success", "output_video_filename": os.path.basename(p),
                       "frames_in_output_video": 12, "internal_processing_duration_seconds": 1.0,
                       "processed_height": 64, "processed_width": 64}
    fake = types.ModuleType("depthcrafter.depthcrafter_logic"); fake.DepthCrafterDemo = Demo
    sys.modules["depthcrafter.depthcrafter_logic"] = fake

    def dc_run(label, mode, kind, handed):
        Demo.mode = mode
        src = tmp / f"dc_{mode}"; shutil.copytree(FIX / "src", src)
        dc.settings.setProperty("input_dir_or_file_var", str(src))
        dc.settings.setProperty("output_dir", str(tmp / f"dc_{mode}_out"))
        dc.settings.setProperty("process_as_segments_var", False)
        return run_ends(label, dc, "depthcrafter", dc.start, kind, handed)

    dc_run("model never loads", "load", "error", False)
    t = dc_run("every clip fails", "fail", "error", False)
    check("...says how many failed", "2 failed" in t[2], t[2])
    t = dc_run("every clip done", "ok", "success", True)
    check("...says how many were done", "2 done" in t[2], t[2])

# ---- Splatting: the real core batch over the small fixture ---------------------
if "sp" in WANT:
    print("Splatting")
    sp = objs["Splatting"]
    FIX = _fixtures.copy("small", tmp / "sp")
    base = {"multi_map": False, "auto_save_sidecar": False, "move_to_finished": False,
            "enable_full_res": False, "enable_low_res": True, "dual_output": False,
            "auto_convergence_mode": "Off"}
    def sp_run(label, src, depth, kind, handed):
        out = tmp / ("sp_out_" + "".join(c for c in label if c.isalnum()))
        for k, v in {**base, "input_source_clips": str(src).replace("\\", "/"),
                     "input_depth_maps": str(depth).replace("\\", "/"),
                     "output_splatted": str(out).replace("\\", "/")}.items():
            sp._set(k, v)
        return run_ends(label, sp, "splatting", sp.start, kind, handed)

    (tmp / "sp_empty").mkdir(); (tmp / "sp_nodepth").mkdir()
    t = sp_run("empty input folder", tmp / "sp_empty", FIX / "depth", "error", False)
    check("...not a success", t[0] != "success", str(t))
    t = sp_run("no depth maps", FIX / "src", tmp / "sp_nodepth", "error", False)
    check("...says how many failed", "2 failed" in t[2], t[2])
    bar = []        # the overall bar, sampled as the real run reports (audit #20)
    sample = lambda: bar.append((sp.property("progressDone"), sp.property("progressTotal"), sp.property("busy")))
    sp.infoChanged.connect(sample)
    t = sp_run("both clips render", FIX / "src", FIX / "depth", "success", True)
    sp.infoChanged.disconnect(sample)
    check("...says how many were done", "2 done" in t[2], t[2])
    ratios = [d / tot for d, tot, busy in bar if tot and busy]
    check("...its overall bar stays within 0..100% and never goes back",
          ratios and all(0 <= r <= 1.0 for r in ratios) and ratios == sorted(ratios),
          f"{len(ratios)} samples, max {max(ratios) if ratios else 0:.2f}")
    check("...and fills", ratios and ratios[-1] > 0.99, f"last {ratios[-1] if ratios else 0:.2f}")

# ---- Inpainting V1: the real engine, its model and per-clip render stood in for --
if "ip" in WANT:
    print("Inpainting V1")
    ip = objs["Inpainting"]
    import studio.stages.inpainting.engine as IE
    FIX = _fixtures.copy("small", tmp / "ip")
    plan = {"load": None, "clips": []}      # clips: True / False per clip, in order
    def load_pipeline(**kw):
        if plan["load"]:
            raise OSError(plan["load"])
        return object()
    def process_single_video(self, *a, **kw):
        return (plan["clips"].pop(0) if plan["clips"] else True), None
    IE.load_inpainting_pipeline = load_pipeline
    IE.InpaintingEngine.process_single_video = process_single_video

    def ip_run(label, src, kind, handed, load=None, clips=()):
        plan["load"], plan["clips"] = load, list(clips)
        out = tmp / ("ip_out_" + "".join(c for c in label if c.isalnum())); out.mkdir()
        for k, v in {"input_folder": str(src), "output_folder": str(out), "hires_blend_folder": "",
                     "single_clip_id": "", "move_to_finished": False}.items():
            ip.settings.setProperty(k, v)
        return run_ends(label, ip, "inpainting", ip.start, kind, handed)

    (tmp / "ip_empty").mkdir()
    t = ip_run("empty input folder", tmp / "ip_empty", "warning", False)
    check("...says why", "No .mp4" in t[2], t[2])
    check("...one toast, not an info box and a toast", len([x for x in toasts if x[0] == "info"]) == 0, str(toasts))
    t = ip_run("model never loads", FIX / "src", "error", False, load="weights not found (stand-in)")
    check("...says why", "weights not found" in t[2], t[2])
    t = ip_run("every clip fails", FIX / "src", "error", False, clips=(False, False))
    check("...says how many failed", "2 failed" in t[2], t[2])
    t = ip_run("one of two fails", FIX / "src", "warning", True, clips=(True, False))
    check("...says so", "1 done, 1 failed" in t[2], t[2])
    t = ip_run("both clips done", FIX / "src", "success", True, clips=(True, True))
    check("...says how many were done", "2 done" in t[2], t[2])

# ---- Inpainting V2: v1's runner for real, the Wan model calls stood in for ------
if "ip2" in WANT:
    print("Inpainting V2")
    wan = objs["InpaintingV2"]; st2 = wan.property("settings")
    import studio.stages.inpainting_v2.engine as WE       # ~9 s: torch, diffusers
    inp = tmp / "ip2_in"; inp.mkdir()
    for n in ("x_splatted2.mp4", "y_splatted2.mp4"):
        (inp / n).write_bytes(b"x")
    (tmp / "ip2_base").mkdir(); (tmp / "ip2_xf").mkdir()
    WE.PRE_TRAINED_PATH, WE.TRANSFORMER_PATH = str(tmp / "ip2_base"), str(tmp / "ip2_xf")
    class WanEngine:
        text_encoder = object(); _prompt_cache = {}
        def release(self): pass
    plan2 = {"load": None, "clips": []}
    def load_models(*a, **k):
        if plan2["load"]:
            raise OSError(plan2["load"])
        return WanEngine()
    def run_inpaint(engine, video, out_dir, **kw):
        if not (plan2["clips"].pop(0) if plan2["clips"] else True):
            raise RuntimeError("stand-in clip failure")
    WE.load_models, WE.run_inpaint = load_models, run_inpaint
    v2busy = lambda c: c.property("busy") or c.property("loading")
    def ip2_run(label, kind, load=None, clips=(), fresh=True):
        plan2["load"], plan2["clips"] = load, list(clips)
        wan._runner = None      # a fresh runner: no engine kept from the last run
        out = tmp / ("ip2_out_" + "".join(c for c in label if c.isalnum()))
        for k, v in {"input_folder": str(inp).replace("\\", "/"), "output_folder": str(out).replace("\\", "/"),
                     "move_to_finished": False, "frames_overlap": "10"}.items():
            st2.setProperty(k, v)
        pump(0.3)
        # V2's handoff sits beside V1's (job_handoff.SIDE_STAGES), for Merging's own button
        return run_ends(label, wan, "inpainting_v2", wan.start, kind, kind == "success", busy=v2busy,
                        fresh=fresh)

    t = ip2_run("models never load", "error", load="weights not found (stand-in)")
    check("...says why", "weights not found" in t[2], t[2])
    status = wan.property("status") or ""
    check("...and the status says it failed", status.startswith("Failed"), status)
    t = ip2_run("every clip fails", "error", clips=(False, False))
    check("...says how many failed", "2 failed" in t[2], t[2])
    forget_handoffs()
    jh.record_job("splatting", {"source": str(tmp / "ip2_src"), "hires": str(tmp / "ip2_hires")})
    t = ip2_run("both clips done", "success", clips=(True, True), fresh=False)
    check("...says how many were done", "2 done" in t[2], t[2])
    check("...and it never stands in for V1's record", not handoff("inpainting"))

    # Merging: "Import from Inpainting V2" shows, and fills the folders by mouse
    from PySide6.QtCore import QMetaObject, Q_ARG, QPointF, QPoint, Qt
    from PySide6.QtTest import QTest
    def find(it, pred):
        for c in it.childItems():
            if pred(c): return c
            r = find(c, pred)
            if r is not None: return r
    # no project picked: an Import offers any job (2026-10-02: with one picked, only
    # that project's -- the seeded config may pick the user's, this job is in a temp folder)
    objs["Shell"].setProperty("projectFolder", ""); pump(0.3)
    stage_item = find(win.contentItem(), lambda c: c.objectName() == "stage")
    QMetaObject.invokeMethod(stage_item, "show", Q_ARG("QVariant", 3), Q_ARG("QVariant", True)); pump(1.2)
    mgc = objs["Merging"]; mst = mgc.property("settings")
    btn = find(win.contentItem(), lambda c: c.objectName() == "mgImportV2")
    check("Merging: the V2 import button shows", btn is not None and btn.isVisible())
    check("...V1's stays hidden (no V1 job)", mgc.property("importDescription") == "")
    for k in ("inpainted_folder", "original_folder", "mask_folder"):
        mst.setProperty(k, "")
    c = btn.mapToScene(QPointF(btn.width() / 2, btn.height() / 2))
    QTest.mouseClick(win, Qt.LeftButton, Qt.NoModifier, QPoint(int(c.x()), int(c.y()))); pump(0.5)
    norm = lambda v: str(v).replace("\\", "/")
    out_v2 = norm(tmp / "ip2_out_bothclipsdone")
    check("...fills Inpainted with V2's output", norm(mst.property("inpainted_folder")) == out_v2,
          mst.property("inpainted_folder"))
    check("...Mask with the splats V2 read", norm(mst.property("mask_folder")) == norm(inp), mst.property("mask_folder"))
    check("...Original with Splatting's source", norm(mst.property("original_folder")) == norm(tmp / "ip2_src"),
          mst.property("original_folder"))

# ---- Merging: real CPU merges over the merge fixture ----------------------------
if "mg" in WANT:
    print("Merging")
    mg = objs["Merging"]; st = mg.property("settings")
    FIX = _fixtures.copy("merge", tmp / "mg")
    def mg_run(label, inpainted, mask, kind, handed):
        out = tmp / ("mg_out_" + "".join(c for c in label if c.isalnum()))
        for k, v in {"inpainted_folder": str(inpainted), "original_folder": str(FIX / "orig"),
                     "mask_folder": str(mask), "output_folder": str(out),
                     "encoding_encoder": "Force CPU", "codec": "H.264", "use_gpu": False, "resume": False,
                     "output_format": "Full SBS", "pad_to_16_9": False, "split_lr": False,
                     "delete_sbs_after_split": False, "dnxhr_fullres_split": False}.items():
            st.setProperty(k, v)
        pump(0.3)
        return run_ends(label, mg, "merging", mg.start, kind, handed)

    (tmp / "mg_empty").mkdir(); (tmp / "mg_nomask").mkdir(); (tmp / "mg_halfmask").mkdir()
    shutil.copy(FIX / "mask" / "mA_320_splatted2.mp4", tmp / "mg_halfmask")
    t = mg_run("empty inpainted folder", tmp / "mg_empty", FIX / "mask", "warning", False)
    check("...says why", "No .mp4" in t[2], t[2])
    t = mg_run("no splats to pair", FIX / "inpainted", tmp / "mg_nomask", "error", False)
    check("...says how many failed", "2 failed" in t[2], t[2])
    t = mg_run("one of two has its splat", FIX / "inpainted", tmp / "mg_halfmask", "warning", True)
    check("...says so", "1 done, 1 failed" in t[2], t[2])
    t = mg_run("both clips merge", FIX / "inpainted", FIX / "mask", "success", True)
    check("...says how many were done", "2 done" in t[2], t[2])

# ---- Scene Cut: a real AutoShot run, some / all scene splits failing -----------
if "sc" in WANT:
    print("Scene Cut")
    import subprocess
    sc = objs["SceneCut"]; stsc = sc.property("settings")
    clip = tmp / "shots.mp4"     # three 1 s shots, cut hard (check_scenecut's clip)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=1",
                    "-f", "lavfi", "-t", "1", "-i", "mandelbrot=size=320x180:rate=24",
                    "-f", "lavfi", "-i", "smptebars=size=320x180:rate=24:duration=1",
                    "-filter_complex", "[0][1][2]concat=n=3:v=1:a=0,format=yuv420p", "-c:v", "libx264",
                    str(clip)], check=True)
    dialogs = []
    sc.message.connect(lambda k, t, m: dialogs.append((k, t, m)))
    fail_scenes = set()          # scene numbers whose split ffmpeg "fails"
    _run = subprocess.run
    def run(args, *a, **kw):
        text = " ".join(map(str, args)) if isinstance(args, (list, tuple)) else str(args)
        if any(f"-Scene-{n:03d}" in text for n in fail_scenes):
            return subprocess.CompletedProcess(args, 1, "", "stand-in split failure")
        return _run(args, *a, **kw)
    subprocess.run = run
    sc.ensureLoaded()
    check("the Scene Cut engine loads", until(lambda: sc.property("ready"), 90), sc.property("loadError"))

    def sc_run(label, failing, kind, title, handed):
        fail_scenes.clear(); fail_scenes.update(failing)
        forget_handoffs(); dialogs.clear()
        for k, v in {"detector_type": "AutoShot", "video_path": str(clip).replace("\\", "/"),
                     "output_dir": str(tmp / ("sc_out_" + "".join(c for c in label if c.isalnum()))).replace("\\", "/"),
                     "export_csv": False, "split_video": True, "split_by_chapter": False, "save_images": False,
                     "export_html": False, "export_sc": False, "refinements_enabled": True, "snap_cuts": True,
                     "ai_validate": False}.items():
            stsc.setProperty(k, v)
        sc.setDetectorParam("min_scene_len", "6")
        pump(0.3)
        sc.start()
        until(lambda: sc.property("busy"), 10)
        ended = until(lambda: not sc.property("busy"), 300)
        pump(0.5)
        last = dialogs[-1] if dialogs else ("", "", "")
        check(f"{label}: the run ends", ended, sc.property("status"))
        check(f"{label}: asks '{title}' ({kind})", last[:2] == (kind, title), str(last))
        check(f"{label}: handoff {'recorded' if handed else 'NOT recorded'}", handoff("scenecut") == handed)
        return last

    sc_run("every scene splits", (), "info", "Success", True)
    t = sc_run("one scene fails to split", {2}, "warning", "Scene Cut finished with errors", True)
    check("...says how many", "1 of 3" in t[2], t[2])
    sc_run("no scene splits", {1, 2, 3}, "error", "Scene Cut failed", False)
    subprocess.run = _run

print()
print("real last_jobs.json unchanged:", (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None) == h0)
if (hashlib.sha256(rec.read_bytes()).hexdigest() if rec.exists() else None) != h0:
    fails.append("real last_jobs.json changed")
print("FAILED:" if fails else "ALL OK", fails if fails else "")
for ctl in objs.values():
    if hasattr(ctl, "shutdown"):
        try: ctl.shutdown()
        except Exception: pass
_fixtures.exit_now(1 if fails else 0)
