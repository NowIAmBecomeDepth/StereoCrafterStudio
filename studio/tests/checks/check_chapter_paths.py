"""Chapter paths that follow the clips down "finished" folders (user, 2026-09-27:
a re-run moves the used clips into a NEW finished inside the last one, and
re-picking the chapter pointed one level short). Plain Python over a temp
project -- no app, no real clips.

    SM Source Clips/Chapter 3 - X/finished/finished/clip.mp4   (Splatting ran twice)
    SM DepthMaps/Chapter 3 - X/clip_depth.mp4
    SM Splats/Chapter 3 - X/lowres/finished/finished/...       (Inpainting ran twice)
"""
import os, sys, tempfile
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]; sys.path.insert(0, str(REPO)); os.chdir(REPO)
from studio.legacy import install; install()
from core.ui import chapters
from studio import chapter_paths as CP

fails = []
def check(label, ok, extra=""):
    print(f"  [{'OK ' if ok else 'FAIL'}] {label} {extra}", flush=True)
    if not ok: fails.append(label)
def touch(p):
    p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b"x")
def rel(p):
    return str(Path(p).relative_to(proj)).replace("\\", "/") if p else p

proj = Path(tempfile.mkdtemp()) / "My Film"
import atexit, shutil; atexit.register(shutil.rmtree, str(proj.parent), True)  # its temp folder goes
ch = "Chapter 3 - X"
src, dep, spl = proj / "SM Source Clips" / ch, proj / "SM DepthMaps" / ch, proj / "SM Splats" / ch
(proj / "SM Inpainted" / ch).mkdir(parents=True); (proj / "SM Converted LR Clips" / ch).mkdir(parents=True)
touch(src / "finished" / "finished" / "clip.mp4")          # moved twice: DepthCrafter, then Splatting
touch(src / "finished" / "clip.fssidecar")                # a sidecar left behind is not a clip
touch(dep / "clip_depth.mp4")
touch(spl / "lowres" / "finished" / "finished" / "clip_splatted2.mp4")
(spl / "hires" / "finished").mkdir(parents=True); touch(spl / "hires" / "clip_splatted4.mp4")

raw = chapters.stage_paths("splatting", str(proj), 3)
got = CP.resolve("splatting", raw)
check("v1's rule alone points Splatting one level short", rel(raw["source"]).endswith(ch + "/finished"), rel(raw["source"]))
check("...the clips are found where the re-run put them (finished/finished)",
      rel(got["source"]).endswith(ch + "/finished/finished"), rel(got["source"]))
check("...a folder that already holds the clips is kept (depth maps)", rel(got["depth"]) == "SM DepthMaps/" + ch, rel(got["depth"]))
check("...outputs are left alone", got["output"] == raw["output"])

got = CP.resolve("inpainting", chapters.stage_paths("inpainting", str(proj), 3))
check("Inpainting: lowres found two levels down", rel(got["lowres"]).endswith("lowres/finished/finished"), rel(got["lowres"]))
check("...hires: an EMPTY finished/ below is not followed, the clips stay where they are",
      rel(got["hires"]).endswith(ch + "/hires"), rel(got["hires"]))

got = CP.resolve("merging", chapters.stage_paths("merging", str(proj), 3))
check("Merging: original clips, expected two levels down, found there", rel(got["original"]).endswith("finished/finished"),
      rel(got["original"]))
check("...the hires mask, expected in hires/finished (empty), found up in hires", rel(got["mask"]).endswith(ch + "/hires"),
      rel(got["mask"]))

# nearest level wins; deeper on a tie
base = proj / "tie"
touch(base / "finished" / "a.mp4"); touch(base / "finished" / "finished" / "finished" / "b.mp4")
(base / "finished" / "finished").mkdir(exist_ok=True)
check("nearest level holding clips wins (expected 2: 1 and 3 hold clips -> the deeper, 3)",
      CP.locate(str(base / "finished" / "finished")).replace("\\", "/").endswith("tie/finished/finished/finished"),
      CP.locate(str(base / "finished" / "finished")))
check("no clips anywhere: the path is handed back as it was", CP.locate(str(proj / "SM Inpainted" / ch)) == str(proj / "SM Inpainted" / ch))
check("a file stays a file", CP.locate(str(dep / "clip_depth.mp4")) == str(dep / "clip_depth.mp4"))

# a chapter field is still the chapter's after a job moves the clips down
check("same_place: one finished chain is one place", CP.same_place(str(src / "finished"), str(src / "finished" / "finished"))
      and CP.same_place(str(src), str(src / "finished")))
check("...another chapter is not", not CP.same_place(str(src), str(proj / "SM Source Clips" / "Chapter 4 - Y")))
check("...nor a folder merely inside it", not CP.same_place(str(spl), str(spl / "lowres")))

print("FAILS", len(fails), fails, flush=True)
# through the fixtures: its temp folders removed, and no DLL teardown crash after a green
# run (bug sweep 2026-10-09, A12)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _fixtures  # noqa: E402
_fixtures.exit_now(1 if fails else 0)
