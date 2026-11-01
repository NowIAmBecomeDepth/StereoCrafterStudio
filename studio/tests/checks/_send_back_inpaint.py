"""The body of check_send_back_inpainting / check_send_back_inpainting_v2 (user, 2026-10-31):
Inpainting's (V1 or V2) "Rerun in Splatting", on copies of the small fixture laid out as the
pipeline leaves them. Run by those two checks after `from _send_back_rig import *`.

clipA, clipC -- Resume on: Splatting moved the clip and its map into finished/, Inpainting its
Lo-Res splat (and, V1, the Hi-Res one) into theirs. clipB -- Resume off: everything in place.

* the list shows clipB only; Show Finished adds clipA and clipC, marked finished;
* right-click a finished row: "Rerun in Splatting" only; a waiting row keeps its own items too;
* Rerun in Splatting, one finished clip: its clip and map back in Splatting's input folders, the
  old splats (and V1's .spsidecar and Hi-Res splat) into finished/replaced, Splatting's output
  the folder whose lowres/ Inpainting reads, its page shown, the clip ticked there alone;
* clipC (finished) and clipB (waiting) ticked: "Rerun 2 Selected in Splatting" -- clipC brought
  back, clipB moved nowhere and its live splat untouched, both ticked in Splatting's list.
"""
import os
import shutil
from pathlib import Path


def run(g, v2: bool) -> None:
    tmp, objs, check, until, pump = g["tmp"], g["objs"], g["check"], g["until"], g["pump"]
    video, queue_item, names_of, archived_of, ticked_of = (g["video"], g["queue_item"], g["names_of"],
                                                         g["archived_of"], g["ticked_of"])
    menu, tick, show_finished, current_stage, stage, n = (g["menu"], g["tick"], g["show_finished"],
                                                          g["current_stage"], g["stage"], g["n"])
    import _fixtures
    small = tmp / "small"
    _fixtures.copy("small", small)
    clip, depth = small / "src" / "clipA.mp4", small / "depth" / "clipA_depth.mp4"
    R = tmp / "inp"
    S, DP, O = R / "src", R / "depth", R / "out"
    I, H = O / "lowres", O / "hires"
    for c in ("clipA", "clipC"):                          # Resume on: everything archived
        video(clip, S / "finished" / f"{c}.mp4")
        video(depth, DP / "finished" / f"{c}_depth.mp4")
        video(clip, I / "finished" / f"{c}_640_splatted2.mp4")
        (I / "finished" / f"{c}_640_splatted2.spsidecar").write_text("{}", encoding="utf-8")
        video(clip, H / "finished" / f"{c}_1280_splatted2.mp4")
    video(clip, S / "clipB.mp4")                            # Resume off: in place
    video(depth, DP / "clipB_depth.mp4")
    video(clip, I / "clipB_640_splatted2.mp4")
    video(clip, H / "clipB_1280_splatted2.mp4")

    sp = objs["Splatting"]
    ip = objs["InpaintingV2" if v2 else "Inpainting"]
    for k, val in {"input_source_clips": str(S), "input_depth_maps": str(DP), "output_splatted": str(R / "elsewhere"),
                   "multi_map": False, "move_to_finished": True}.items():
        sp._set(k, val)
    sp.save()
    st = ip.property("settings")
    vals = {"input_folder": str(I), "output_folder": str(R / "inpainted"), "move_to_finished": True}
    if not v2:
        vals["hires_blend_folder"] = str(H)
    for k, val in vals.items():
        st.setProperty(k, val)
    ip.save()
    pump(0.5)
    page = stage.property("pages").property(2).toQObject()
    page.setProperty("engine", 1 if v2 else 0)
    q = queue_item(2)
    page.setProperty("engine", 1 if v2 else 0)
    pump(0.8)
    q = queue_item(2)
    src = q.property("src")
    title = "Inpainting V2" if v2 else "Inpainting"
    check(f"{title}'s list: the waiting clip only (Resume moved the others)",
          until(lambda: names_of(src) == ["clipB_640_splatted2.mp4"]), str(names_of(src)))
    check("Show Finished turns on", show_finished(q, True))
    check("...the finished splats listed after it, marked finished",
          until(lambda: archived_of(src) == {"clipB_640_splatted2.mp4": False, "clipA_640_splatted2.mp4": True,
                                            "clipC_640_splatted2.mp4": True}), str(archived_of(src)))
    opened, texts = menu(q, "clipA")
    check("right-click a finished clip: Rerun in Splatting only", opened and texts == ["Rerun in Splatting"], str(texts))
    opened, texts = menu(q, "clipB")
    check("...a waiting clip keeps its own items, the rerun among them",
          "Inpaint This Clip Now" in texts and "Rerun in Splatting" in texts, str(texts))

    toasts = []
    sp.toast.connect(lambda *a: toasts.append(a))
    menu(q, "clipA", "rerun")
    check("Rerun in Splatting: the clip and its map back in Splatting's input folders",
          until(lambda: (S / "clipA.mp4").is_file() and (DP / "clipA_depth.mp4").is_file())
          and not (S / "finished" / "clipA.mp4").exists() and not (DP / "finished" / "clipA_depth.mp4").exists())
    rep = I / "finished" / "replaced"
    check("...the old Lo-Res splat into finished/replaced" + ("" if v2 else ", its .spsidecar with it"),
          (rep / "clipA_640_splatted2.mp4").is_file() and (v2 or (rep / "clipA_640_splatted2.spsidecar").is_file())
          and not (I / "finished" / "clipA_640_splatted2.mp4").exists(),
          str(sorted(p.name for p in (I / "finished").rglob("*"))))
    if not v2:
        check("...and its old Hi-Res splat", (H / "finished" / "replaced" / "clipA_1280_splatted2.mp4").is_file())
    spst = sp.property("settings")
    check("...Splatting's output: the folder whose lowres/ Inpainting reads",
          until(lambda: n(spst.value("output_splatted")) == n(O)), str(spst.value("output_splatted")))
    check("...Splatting's page shown", until(lambda: current_stage() == 1), str(current_stage()))
    ssrc = sp.preview.queue
    check("...the clip ticked there alone", until(lambda: ticked_of(ssrc) == ["clipA.mp4"]), str(ticked_of(ssrc)))
    check("...ready for Process Selected", until(lambda: any("Process Selected" in t[2] for t in toasts)), str(toasts[-1:]))

    q = queue_item(2)
    src = q.property("src")
    until(lambda: "clipC_640_splatted2.mp4" in names_of(src))
    src.checkNone()
    tick(q, "clipC")
    tick(q, "clipB")
    opened, texts = menu(q, "clipC")
    check("clipC (finished) and clipB (waiting) ticked: the rerun says how many",
          texts == ["Rerun 2 Selected in Splatting"], str(texts))
    menu(q, "clipC", "rerun")
    check("Rerun 2 Selected: clipC brought back, clipB left where it is",
          until(lambda: (S / "clipC.mp4").is_file() and (DP / "clipC_depth.mp4").is_file())
          and (S / "clipB.mp4").is_file() and (DP / "clipB_depth.mp4").is_file())
    check("...clipB's live splat untouched, clipC's archived one aside",
          (I / "clipB_640_splatted2.mp4").is_file() and (rep / "clipC_640_splatted2.mp4").is_file())
    check("...both ticked in Splatting's list", until(lambda: ticked_of(ssrc) == ["clipB.mp4", "clipC.mp4"]),
          str(ticked_of(ssrc)))
