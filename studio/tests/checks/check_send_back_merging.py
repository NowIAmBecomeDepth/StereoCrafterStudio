"""Merging's "Rerun in Inpainting" / "Rerun in Inpainting V2" (user, 2026-10-31: send a clip back
to the stage before, a group of ticked clips too, Resume on or off), in the real app offscreen,
on copies of the merge fixture laid out as the pipeline leaves them:

mA, mC -- Resume on: Inpainting moved the Lo-Res splat (and its .spsidecar) into lowres/finished
and the Hi-Res grid into hires/finished (Merging's mask folder); Merging then moved the inpainted
clip, its sidecar and that grid one level further. mB -- Resume off: everything in place.

* the list shows mB only; Show Finished adds mA and mC, marked finished;
* right-click a finished row: the two reruns only;
* Rerun in Inpainting, mA: its Lo-Res splat and .spsidecar back in Inpainting's input, its Hi-Res
  grid back in the Hi-Res folder, the old inpainted clip into finished/replaced, its sidecar (the
  user's Grain Match) back beside Merging's input; Inpainting's output is Merging's inpainted
  folder, V1's page shown, the splat ticked there alone;
* mC (finished) and mB (waiting) ticked: "Rerun 2 Selected in Inpainting V2" -- mC's splat back,
  mB's left where it is, both ticked in V2's list, V2's page shown.
"""
from _send_back_rig import *
import _fixtures

MFX = tmp / "mfx"; _fixtures.copy("merge", MFX)
R = tmp / "m"
P, H, I, ORIG = R / "inpainted", R / "hires", R / "lowres", R / "orig"
M = H / "finished"                                   # where Inpainting's Resume left the grids
for c in ("mA", "mC"):                               # Resume on: everything archived
    video(MFX / "inpainted" / "mA_320_inpainted_right_eye.mp4", P / "finished" / f"{c}_320_inpainted_right_eye.mp4")
    (P / "finished" / f"{c}.fssidecar").write_text("{}", encoding="utf-8")
    video(MFX / "mask" / "mA_320_splatted2.mp4", M / "finished" / f"{c}_320_splatted2.mp4")
    video(MFX / "mask" / "mA_320_splatted2.mp4", I / "finished" / f"{c}_160_splatted2.mp4")
    (I / "finished" / f"{c}_160_splatted2.spsidecar").write_text("{}", encoding="utf-8")
    video(MFX / "orig" / "mA.mp4", ORIG / "finished" / f"{c}.mp4")
video(MFX / "inpainted" / "mB_320_inpainted_right_eye.mp4", P / "mB_320_inpainted_right_eye.mp4")   # Resume off
video(MFX / "mask" / "mB_320_splatted4.mp4", M / "mB_320_splatted4.mp4")
video(MFX / "mask" / "mB_320_splatted4.mp4", I / "mB_160_splatted4.mp4")
video(MFX / "orig" / "mB.mp4", ORIG / "mB.mp4")

mg, ip, wan = objs["Merging"], objs["Inpainting"], objs["InpaintingV2"]
for k, v in {"inpainted_folder": str(P), "mask_folder": str(M), "original_folder": str(ORIG),
             "output_folder": str(R / "final"), "resume": True}.items():
    mg._set(k, v)
mg.save()
for ctl, vals in ((ip, {"input_folder": str(I), "hires_blend_folder": str(H), "output_folder": str(R / "elsewhere")}),
                  (wan, {"input_folder": str(I), "output_folder": str(R / "elsewhere")})):
    st = ctl.property("settings")
    for k, v in vals.items():
        st.setProperty(k, v)
    ctl.save()
pump(0.5)

q = queue_item(3); src = q.property("src")
check("Merging's list: the waiting clip only (Resume moved the others)",
      until(lambda: names_of(src) == ["mB_320_inpainted_right_eye.mp4"]), str(names_of(src)))
check("Show Finished turns on", show_finished(q, True))
check("...the finished clips listed after it, marked finished",
      until(lambda: archived_of(src) == {"mB_320_inpainted_right_eye.mp4": False, "mA_320_inpainted_right_eye.mp4": True,
                                        "mC_320_inpainted_right_eye.mp4": True}), str(archived_of(src)))
opened, texts = menu(q, "mA")
check("right-click a finished clip: the two reruns only",
      opened and texts == ["Rerun in Inpainting", "Rerun in Inpainting V2"], str(texts))
opened, texts = menu(q, "mB")
check("...a waiting clip keeps its own items, the reruns among them",
      "Merge This Clip Now" in texts and "Rerun in Inpainting" in texts and "Rerun in Inpainting V2" in texts, str(texts))

toasts = []
ip.toast.connect(lambda *a: toasts.append(a)); wan.toast.connect(lambda *a: toasts.append(a))
menu(q, "mA", "rerun")
check("Rerun in Inpainting: the Lo-Res splat and its .spsidecar back in Inpainting's input",
      until(lambda: (I / "mA_160_splatted2.mp4").is_file()) and (I / "mA_160_splatted2.spsidecar").is_file()
      and not (I / "finished" / "mA_160_splatted2.mp4").exists())
check("...its Hi-Res grid back in the Hi-Res folder", (H / "mA_320_splatted2.mp4").is_file()
      and not (M / "finished" / "mA_320_splatted2.mp4").exists())
check("...the old inpainted clip into finished/replaced",
      (P / "finished" / "replaced" / "mA_320_inpainted_right_eye.mp4").is_file()
      and not (P / "finished" / "mA_320_inpainted_right_eye.mp4").exists())
check("...its sidecar back beside Merging's input", (P / "mA.fssidecar").is_file())
ist = ip.property("settings")
check("...Inpainting's output is Merging's inpainted folder", until(lambda: n(ist.value("output_folder")) == n(P)),
      str(ist.value("output_folder")))
check("...Inpainting V1's page shown", until(lambda: current_stage() == 2
                                         and stage.property("pages").property(2).toQObject().property("engine") == 0))
check("...the splat ticked there alone", until(lambda: ticked_of(ip.property("jobClips")) == ["mA_160_splatted2.mp4"]),
      str(ticked_of(ip.property("jobClips"))))
check("...ready for Process Selected", until(lambda: any("Process Selected" in t[2] for t in toasts)), str(toasts[-1:]))

q = queue_item(3); src = q.property("src")
until(lambda: "mC_320_inpainted_right_eye.mp4" in names_of(src))
src.checkNone(); tick(q, "mC"); tick(q, "mB")
opened, texts = menu(q, "mC")
check("mC (finished) and mB (waiting) ticked: the reruns say how many",
      texts == ["Rerun 2 Selected in Inpainting", "Rerun 2 Selected in Inpainting V2"], str(texts))
menu(q, "mC", "rerunV2")
check("Rerun 2 Selected in Inpainting V2: mC's splat back, mB's where it was",
      until(lambda: (I / "mC_160_splatted2.mp4").is_file()) and (I / "mB_160_splatted4.mp4").is_file())
check("...mB's live inpainted clip untouched", (P / "mB_320_inpainted_right_eye.mp4").is_file())
wst = wan.property("settings")
check("...V2's output is Merging's inpainted folder", until(lambda: n(wst.value("output_folder")) == n(P)),
      str(wst.value("output_folder")))
check("...V2's page shown", until(lambda: current_stage() == 2
                                  and stage.property("pages").property(2).toQObject().property("engine") == 1))
check("...both ticked in V2's list",
      until(lambda: ticked_of(wan.property("jobClips")) == ["mB_160_splatted4.mp4", "mC_160_splatted2.mp4"]),
      str(ticked_of(wan.property("jobClips"))))

finish()
