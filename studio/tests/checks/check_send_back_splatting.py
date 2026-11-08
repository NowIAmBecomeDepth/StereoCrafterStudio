"""Splatting's "Rerun in DepthCrafter" / "Rerun with Video Depth Anything" (user, 2026-10-31:
send a clip back to the stage before, a group of ticked clips too, Resume on or off), in the
real app offscreen, on copies of the small fixture laid out as the pipeline leaves them:

Resume on -- DepthCrafter reads dc/, its Resume moved the clips into dc/finished (Splatting's
Input Source Clips), and Splatting's own Resume moved clipA / clipB on into dc/finished/finished
with their maps into depth/finished; clipC is still waiting (dc/finished, depth/):

* the list shows clipC only; Show Finished adds clipA and clipB, dimmed, marked finished;
* right-click a finished row: the reruns only. Rerun in DepthCrafter, one clip: the clip back
  where Splatting lists it (dc/finished), its sidecar back beside the map folder, the old map
  into depth/finished/replaced; DepthCrafter's input is that clip, its output the depth folder,
  its page shown;
* clipB (finished) and clipC (waiting) ticked: "Rerun 2 Selected in DepthCrafter" -- both into
  dc/ (the folder its Resume moves into Splatting's), ticked in DepthCrafter's list and nothing
  else, the output set to the depth folder; clipB's old map aside, clipC's live map untouched;
* Process Selected never takes a Show Finished row.

Resume off -- one folder, nothing moved: "Rerun 2 Selected with Video Depth Anything" moves
nothing, sets nothing aside, VDA's input is that folder and both clips are ticked there.
"""
from _send_back_rig import *
import _fixtures

SMALL = tmp / "small"; _fixtures.copy("small", SMALL)
A_SRC, A_DEP = SMALL / "src" / "clipA.mp4", SMALL / "depth" / "clipA_depth.mp4"

# ---- Resume on: the layout ------------------------------------------------------------------
R = tmp / "on"
dc_in, sp_src, dep = R / "dc", R / "dc" / "finished", R / "depth"
for c in ("clipA", "clipB"):
    video(A_SRC, sp_src / "finished" / f"{c}.mp4")
    video(A_DEP, dep / "finished" / f"{c}_depth.mp4")
(dep / "finished" / "clipA_depth.fssidecar").write_text("{}", encoding="utf-8")
video(A_SRC, sp_src / "clipC.mp4")
video(A_DEP, dep / "clipC_depth.mp4")
(R / "out").mkdir()

dc, sp, vda = objs["DepthCrafter"], objs["Splatting"], objs["VDA"]
dst = dc.property("settings")
for k, v in {"input_dir_or_file_var": str(dc_in), "output_dir": str(R / "elsewhere"), "move_to_finished": True}.items():
    dst.setProperty(k, v)
for k, v in {"input_source_clips": str(sp_src), "input_depth_maps": str(dep), "output_splatted": str(R / "out"),
             "multi_map": False, "move_to_finished": True}.items():
    sp._set(k, v)
sp.save(); dc.save(); pump(0.5)

sq = queue_item(1, "splatJobQueue")
src = sq.property("src")
check("Splatting's list: the clip still waiting only (Resume moved the others)",
      until(lambda: names_of(src) == ["clipC.mp4"]), str(names_of(src)))
check("Show Finished is offered, and turns on", show_finished(sq, True))
check("...the finished clips listed after it, marked finished",
      until(lambda: archived_of(src) == {"clipC.mp4": False, "clipA.mp4": True, "clipB.mp4": True}),
      str(archived_of(src)))
check("...the counter says so", "1 clips" in str(one(sq, "queueClipCount").property("text"))
      and "2 finished" in str(one(sq, "queueClipCount").property("text")),
      str(one(sq, "queueClipCount").property("text")))

opened, texts = menu(sq, "clipA")
check("right-click a finished clip: the reruns only",
      opened and texts == ["Rerun in DepthCrafter", "Rerun with Video Depth Anything", "Rerun with DVD"],
      str(texts))
opened, texts = menu(sq, "clipC")
check("...a waiting clip keeps its own items, the reruns among them",
      "Splat This Clip Now" in texts and "Rerun in DepthCrafter" in texts, str(texts))

# one clip
dc_toasts = []
dc.toast.connect(lambda *a: dc_toasts.append(a))
menu(sq, "clipA", "rerun")
check("Rerun in DepthCrafter, a finished clip: back where Splatting lists it",
      until(lambda: (sp_src / "clipA.mp4").is_file()) and not (sp_src / "finished" / "clipA.mp4").exists())
check("...its sidecar back beside the depth maps", (dep / "clipA_depth.fssidecar").is_file()
      and not (dep / "finished" / "clipA_depth.fssidecar").exists())
check("...its old map into depth/finished/replaced", (dep / "finished" / "replaced" / "clipA_depth.mp4").is_file()
      and not (dep / "finished" / "clipA_depth.mp4").exists(), str(list((dep / "finished").iterdir())))
check("...DepthCrafter's input is the clip, its output the depth folder",
      until(lambda: n(dst.value("input_dir_or_file_var")) == n(sp_src / "clipA.mp4"))
      and n(dst.value("output_dir")) == n(dep), f"{dst.value('input_dir_or_file_var')} {dst.value('output_dir')}")
check("...and DepthCrafter's page is shown", until(lambda: current_stage() == 0), str(current_stage()))
check("...which says it's ready", until(lambda: any("Ready to re-run" in t[1] for t in dc_toasts)), str(dc_toasts[-1:]))

# a group: clipB (finished) and clipC (waiting)
dst.setProperty("input_dir_or_file_var", str(dc_in)); dc.save(); pump(0.5)
sq = queue_item(1, "splatJobQueue"); src = sq.property("src")
until(lambda: "clipC.mp4" in names_of(src) and "clipB.mp4" in names_of(src))
src.checkNone(); tick(sq, "clipB"); tick(sq, "clipC")
check("Process Selected never takes a finished row", [os.path.basename(p) for p in src.checked_paths()] == ["clipC.mp4"],
      str(src.checked_paths()))
opened, texts = menu(sq, "clipB")
check("right-click a ticked clip with another ticked: the rerun says how many",
      "Rerun 2 Selected in DepthCrafter" in texts and "Rerun 2 Selected with Video Depth Anything" in texts, str(texts))
menu(sq, "clipB", "rerun")
check("Rerun 2 Selected: both into DepthCrafter's input (its Resume moves them on into Splatting's)",
      until(lambda: (dc_in / "clipB.mp4").is_file() and (dc_in / "clipC.mp4").is_file())
      and not (sp_src / "finished" / "clipB.mp4").exists() and not (sp_src / "clipC.mp4").exists())
check("...clipB's old map aside, clipC's live map left alone",
      (dep / "finished" / "replaced" / "clipB_depth.mp4").is_file() and (dep / "clipC_depth.mp4").is_file())
check("...DepthCrafter's input unchanged, its output the depth folder",
      n(dst.value("input_dir_or_file_var")) == n(dc_in) and n(dst.value("output_dir")) == n(dep))
dsrc = dc.property("jobClips")
check("...just those two ticked in DepthCrafter's list",
      until(lambda: ticked_of(dsrc) == ["clipB.mp4", "clipC.mp4"]), str(ticked_of(dsrc)))
check("...ready for Process Selected", any("Process Selected" in t[2] for t in dc_toasts), str(dc_toasts[-1:]))

# ---- Resume off: nothing has moved ---------------------------------------------------------
F = tmp / "off"
for c in ("clipD", "clipE"):
    video(A_SRC, F / "clips" / f"{c}.mp4")
    video(A_DEP, F / "depth" / f"{c}_depth.mp4")
vst = vda.property("settings")
for k, v in {"input_dir_or_file_var": str(F / "elsewhere"), "output_dir": str(F / "depth"), "move_to_finished": False}.items():
    vst.setProperty(k, v)
for k, v in {"input_source_clips": str(F / "clips"), "input_depth_maps": str(F / "depth"), "move_to_finished": False}.items():
    sp._set(k, v)
sp.save(); pump(0.5)
sq = queue_item(1, "splatJobQueue"); src = sq.property("src")
show_finished(sq, False)
check("Resume off: both clips listed", until(lambda: names_of(src) == ["clipD.mp4", "clipE.mp4"]), str(names_of(src)))
src.checkAll(); pump(0.2)
menu(sq, "clipD", "rerunVda")
check("Rerun 2 Selected with Video Depth Anything: VDA's input is their folder",
      until(lambda: n(vst.value("input_dir_or_file_var")) == n(F / "clips")), str(vst.value("input_dir_or_file_var")))
check("...nothing moved, nothing set aside", (F / "clips" / "clipD.mp4").is_file() and (F / "clips" / "clipE.mp4").is_file()
      and not list(F.rglob("replaced")) and not list(F.rglob("finished")))
check("...VDA's tab shown", until(lambda: current_stage() == 0 and stage.property("pages").property(0).toQObject()
                                  .property("engine") == 1))
vsrc = vda.property("jobClips")
check("...both ticked in its list", until(lambda: ticked_of(vsrc) == ["clipD.mp4", "clipE.mp4"]), str(ticked_of(vsrc)))

finish()
