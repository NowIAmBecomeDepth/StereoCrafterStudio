"""No partial output under a final name (audit 2026-09-28 #5), through each
stage's real Start in the real app, offscreen. Each stage runs once cleanly
(its outputs are "the previous good output"), then again with a Stop or a
failure mid-clip: the previous outputs must be byte-identical and no partial
file left beside them. Splatting: a real render stopped after its first
batch. Merging: a clip whose reader fails on its second chunk -- no leftover,
ffmpeg reaped, and the batch goes on to the next clip. Scene Cut: a scene
whose ffmpeg split fails after writing -- its earlier clip goes to the
(stubbed) Recycle Bin, nothing half-written left. Configs and last_jobs.json redirected.

    check_partial_outputs.py          every stage
    check_partial_outputs.py sp mg    only these (sp mg sc)
"""
import os, sys, tempfile, hashlib, time, json, shutil, subprocess
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

WANT = set(a for a in sys.argv[1:] if not a.startswith("-")) or {"sp", "mg", "sc"}

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
def snapshot(folder):
    """{relative path: sha256} of every file under folder."""
    return {str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(folder).rglob("*")) if p.is_file()}
def run(ctl, start, busy=lambda c: c.property("busy"), sec=300):
    start()
    until(lambda: busy(ctl), 10)
    ok = until(lambda: not busy(ctl), sec)
    pump(0.5)
    return ok
toasts = []
for ctl in objs.values():
    sig = getattr(ctl, "toast", None)
    if sig is not None and hasattr(sig, "connect"):
        sig.connect(lambda k, t, m: toasts.append((k, t, m)))

pump(0.8)

# ---- Splatting: a real render, stopped after its first batch ---------------------
if "sp" in WANT:
    print("Splatting")
    sp = objs["Splatting"]
    FIX = _fixtures.copy("small", tmp / "sp")
    out = tmp / "sp_out"
    for k, v in {"input_source_clips": str(FIX / "src").replace("\\", "/"),
                 "input_depth_maps": str(FIX / "depth").replace("\\", "/"),
                 "output_splatted": str(out).replace("\\", "/"), "multi_map": False, "auto_save_sidecar": False,
                 "move_to_finished": False, "enable_full_res": False, "enable_low_res": True,
                 "dual_output": False, "auto_convergence_mode": "Off", "low_res_batch_size": "4"}.items():
        sp._set(k, v)
    check("a clean run", run(sp, sp.start))
    before = snapshot(out)
    check("...wrote both clips", len([k for k in before if k.endswith(".mp4")]) == 2, str(list(before)))

    import core.splatting.render_processor as RP
    # the encoder's frames come from _grid_frames_gpu since audit O1 (the
    # hook on _write_to_ffmpeg, no longer called, stopped nothing)
    _grid = RP._grid_frames_gpu
    def grid_then_stop(*a, **kw):
        yield from _grid(*a, **kw)
        sp._stop.set()                      # what the Stop button does, after one batch is in
    RP._grid_frames_gpu = grid_then_stop
    toasts.clear()
    check("a re-run, stopped after its first batch", run(sp, sp.start))
    RP._grid_frames_gpu = _grid
    check("...it really was stopped", toasts and "stop" in (toasts[-1][1] + toasts[-1][2]).lower(), str(toasts[-1:]))
    after = snapshot(out)
    check("...the previous outputs are untouched", after == before,
          str({k: (before.get(k, "")[:8], after.get(k, "")[:8]) for k in set(before) | set(after)
               if before.get(k) != after.get(k)}))
    check("...and no partial file is left", not [k for k in after if k.endswith(".part")], str(list(after)))

# ---- Merging: a clip failing mid-encode --------------------------------------------
if "mg" in WANT:
    print("Merging")
    mg = objs["Merging"]; st = mg.property("settings")
    FIX = _fixtures.copy("merge", tmp / "mg")
    out = tmp / "mg_out"
    for k, v in {"inpainted_folder": str(FIX / "inpainted"), "original_folder": str(FIX / "orig"),
                 "mask_folder": str(FIX / "mask"), "output_folder": str(out),
                 "encoding_encoder": "Force CPU", "codec": "H.264", "use_gpu": False, "resume": False,
                 "output_format": "Full SBS", "pad_to_16_9": False, "split_lr": False,
                 "delete_sbs_after_split": False, "dnxhr_fullres_split": False, "batch_chunk_size": 4}.items():
        st.setProperty(k, v)
    pump(0.3)
    check("a clean run", run(mg, mg.start))
    before = snapshot(out)
    check("...wrote both clips", len(before) == 2, str(list(before)))

    import studio.stages.merging.engine as ME
    _Reader = ME.VideoReader
    class FailingReader:
        """mA's inpainted clip: its second chunk cannot be read."""
        def __init__(self, path, *a, **kw):
            self._r, self._fail, self._calls = _Reader(path, *a, **kw), "mA_" in os.path.basename(path) and "inpainted" in path, 0
        def get_batch(self, idx):
            self._calls += 1
            if self._fail and self._calls >= 2:
                raise RuntimeError("stand-in read failure mid-clip")
            return self._r.get_batch(idx)
        def __getattr__(self, name):
            return getattr(self._r, name)
        def __len__(self):
            return len(self._r)
    ME.VideoReader = FailingReader
    mtimes = {p.name: p.stat().st_mtime_ns for p in out.iterdir()}
    toasts.clear()
    check("a re-run, mA failing mid-clip", run(mg, mg.start))
    ME.VideoReader = _Reader
    after = snapshot(out)
    check("...mA's previous output is untouched", {k: v for k, v in after.items() if k.startswith("mA")}
          == {k: v for k, v in before.items() if k.startswith("mA")}, str(list(after)))
    check("...no partial file is left", not [k for k in after if "partial" in k or k.endswith(".part")], str(list(after)))
    mb = [p for p in out.iterdir() if p.name.startswith("mB")]
    check("...and the batch went on: mB merged again", mb and mb[0].stat().st_mtime_ns > mtimes.get(mb[0].name, 0),
          str([p.name for p in mb]))
    check("...said as 1 done, 1 failed", toasts[-1:] and "1 done, 1 failed" in toasts[-1][2], str(toasts[-1:]))

# ---- Scene Cut: a split whose ffmpeg fails after writing ---------------------------
if "sc" in WANT:
    print("Scene Cut")
    sc = objs["SceneCut"]; stsc = sc.property("settings")
    clip = tmp / "shots.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24:duration=1",
                    "-f", "lavfi", "-t", "1", "-i", "mandelbrot=size=320x180:rate=24",
                    "-f", "lavfi", "-i", "smptebars=size=320x180:rate=24:duration=1",
                    "-filter_complex", "[0][1][2]concat=n=3:v=1:a=0,format=yuv420p", "-c:v", "libx264",
                    str(clip)], check=True)
    out = tmp / "sc_out"
    sc.ensureLoaded()
    check("the Scene Cut engine loads", until(lambda: sc.property("ready"), 90), sc.property("loadError"))
    for k, v in {"detector_type": "AutoShot", "video_path": str(clip).replace("\\", "/"),
                 "output_dir": str(out).replace("\\", "/"), "export_csv": False, "split_video": True,
                 "split_by_chapter": False, "save_images": False, "export_html": False, "export_sc": False,
                 "refinements_enabled": True, "snap_cuts": True, "ai_validate": False}.items():
        stsc.setProperty(k, v)
    sc.setDetectorParam("min_scene_len", "6")
    pump(0.3)
    check("a clean run", run(sc, sc.start))
    before = snapshot(out)
    check("...wrote three scenes", len([k for k in before if k.endswith(".mp4")]) == 3, str(list(before)))
    _run = subprocess.run
    def failing_split(args, *a, **kw):
        r = _run(args, *a, **kw)             # ffmpeg writes its file...
        if "-Scene-002" in " ".join(map(str, args)):
            written = Path(str(args[-1]))    # ...then "fails" having written half of it
            if written.exists():
                data = written.read_bytes(); written.write_bytes(data[: len(data) // 2])
            return subprocess.CompletedProcess(args, 1, "", "stand-in failure after writing")
        return r
    # bug sweep batch 6 (A9): the failed scene's clip from the clean run belongs to that
    # cut, not this one -- it goes to the Recycle Bin (stubbed: the user's bin untouched)
    import studio.stages.scenecut.engine as SE
    binned = []
    def fake_bin(path):
        binned.append(Path(path).name); os.remove(path); return True
    _bin = getattr(SE, "send_to_recycle_bin", None)
    SE.send_to_recycle_bin = fake_bin
    subprocess.run = failing_split
    check("a re-run, scene 2's split failing", run(sc, sc.start))
    subprocess.run = _run
    if _bin is not None:
        SE.send_to_recycle_bin = _bin
    after = snapshot(out)
    s2 = [k for k in before if "-Scene-002" in k]
    check("...scene 2's previous clip went to the Recycle Bin, not left half-written",
          s2 and binned == [Path(k).name for k in s2] and not [k for k in after if "-Scene-002" in k],
          f"binned {binned}; left {[k for k in after if '-Scene-002' in k]}")
    check("...scenes 1 and 3 are this run's", all(k in after for k in before if "-Scene-002" not in k), str(list(after)))
    check("...no partial file is left", not [k for k in after if k.endswith(".part")], str(list(after)))

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
