.pragma library

// Every tip in Studio, by what it's for (user, 2026-10-11). Each stage's tip fader
// (TipTicker, above its progress bar) shows its own list; Settings > Tips lists them all,
// in these groups and in this order. "Everywhere" and the Depth Map Editor have no fader.

var GROUPS = [
    { key: "everywhere", title: "Everywhere", tips: [
        "Ctrl+P opens the project list; each stage goes back to the chapter it last used in that project.",
        "F1 opens the manual at the stage you're on.",
        "Every shortcut is listed in Settings > Keyboard Shortcuts.",
        "Studio minimized during a job? A Windows notification tells you when it finishes.",
        "In the folder picker, right-click for New Folder or Move to Recycle Bin.",
        "Upcoming Jobs: right-click a clip for what you can do with it, such as Play, or Send to the next stage once it's done."
    ] },
    { key: "scenecut", title: "Scene Cut", tips: [
        "Too many cuts, or too few? A lower threshold finds more; AutoShot sits around 0.24–0.30, TransNetV2 around 0.3.",
        "Dissolves and wipes in the film? OmniShotCut knows about transitions; its clean_shot mode folds them into the scene before.",
        "Cuts landing a frame or two off? Snap cuts to frame-perfect boundary moves each one to where the picture really changes.",
        "Flashes counted as cuts? Validate cuts with DINOv3 AI filters out flashes and fast motion (it needs a CUDA GPU).",
        "Cutting in DaVinci Resolve? Export to .sc file puts the cuts on a Resolve timeline.",
        "Working from a Blu-ray or DVD rip? Split into chapters files the scenes into one folder per chapter.",
        "Split video into scenes writes the clips DepthCrafter reads; Import from Scene Cut fills its input afterwards.",
        "Scenes shorter than the minimum length are merged into a neighbour, never dropped.",
        "CQ Level: lower is better quality and bigger files; 16–24 suits the 10-bit HEVC clips.",
        // 2026-10-11
        "A movie whose aspect ratio switches, like IMAX scenes in a widescreen film or a 4:3 flashback? Tick Auto Crop: each shot is cropped to its own picture.",
        "Auto Crop reads the whole movie's black bars first, so a dark shot is never cropped into."
    ] },
    { key: "depthcrafter", title: "DepthCrafter", tips: [
        "Short on VRAM? Tick Process as Segments: the clip runs in Window Size pieces, merged back at the end.",
        "Trying settings on a long clip? Set Process Max Frames to a few dozen to test them on the start of it.",
        "Target Width and Target Height must both be multiples of 64.",
        "Depth slowly breathing toward or away from you over a long clip? Turn on Stabilize Depth Scale.",
        "One stray bright speck squashing the depth range? Percentile Output Norm clips the extreme pixels first.",
        // 10-bit maps, not 8-bit + Dithering (user, 2026-10-05)
        "Banding in smooth gradients? Keep Output Format on main10_mp4: a 10-bit map has four times the depth steps of 8-bit mp4.",
        "Want another output format after a segment run? Re-Merge Segments rebuilds it from the kept NPZ files.",
        "Models already downloaded? Settings › DepthCrafter › Use Local Models Only skips the online check at start-up.",
        "The same Seed and settings give the same depth map again; -1 picks a new seed every run.",
        "View Output Folder stays clickable during a batch, so you can check finished clips as they land.",
        // 2026-10-11
        "Clips of different shapes in one folder? Auto Size for the Loaded Clip runs each clip at the size picked for its own aspect ratio.",
        "Want other sizes for an aspect ratio? Change Auto Size's picks, or add your own presets, in Settings › DepthCrafter.",
        "A long full-video job? Save Checkpoint keeps the finished denoising, so a failed save can resume without running it again.",
        "A clip done? Right-click its row in Upcoming Jobs and choose Send to Splatting to take just that clip on."
    ] },
    // DepthCrafter's Video Depth Anything tab (user, 2026-10-25)
    { key: "vda", title: "Video Depth Anything", tips: [
        "Video Depth Anything is far faster than DepthCrafter: try it first, and keep DepthCrafter for the shots it gets wrong.",
        "Replacing a DepthCrafter map? The old one moves into a DepthCrafter folder beside it, and Splatting shows the new one at once.",
        "Right-click a clip in Splatting's Upcoming Jobs and choose Rerun with Video Depth Anything to redo just that clip's depth.",
        "Small is quickest and Apache-2.0; Base and Large are sharper but for non-commercial use only.",
        "Input Size sets the detail: 518 is what the model is trained at, higher keeps finer edges and costs memory.",
        "A long shot whose depth range really changes? Normalise with EMA lets the range follow it, as iw3 does.",
        "Short on VRAM? Pick a smaller Model or a lower Input Size; Online mode needs the same memory however long the clip."
    ] },
    { key: "splatting", title: "Splatting", tips: [
        "Double-click the preview to zoom in on that spot; double-click again to see the whole frame.",
        "Holes too big for inpainting? Dual Eye shares the shift between both eyes, so each eye gets smaller holes.",
        "Objects moving left to right? Tick Flip: inpainting is tuned for right-side holes, and Merging flips it back.",
        "Setting convergence? Turn on Crosshair: it marks zero parallax at the centre of the preview.",
        "Convergence 1.0 puts everything inside the screen, 0.0 is full pop-out; 0.8 is a good place to start.",
        "Not sure how much Disparity a clip really uses? The ≈ button samples 10 frames and estimates it.",
        "Blocky left edges in the depth? Dilate Left and Blur Left smooth them without touching the right edges.",
        "Burned-in subtitles bending into 3D? Detect Text masks them so they stay flat on the screen.",
        "Only need part of the batch? Tick those clips in Upcoming Jobs and press Process Selected.",
        "Pop Out Boost and Frame Break are per scene: set Strength or Bar Size back to 0 when you move on; closing the section only hides it.",
        "Turned Disparity, Gamma or any other knob too far? Double-click it (or right-click) to put it back to its default.",
        "Click the preview to flip it between its Front and Back sources.",
        "In the preview, Space plays or pauses, Left and Right step a frame, Shift+Left and Shift+Right step ten.",
        // 2026-10-11
        "Want things to come further out of the screen? Try Pop-Out first: for the same pop it warps outlines far less than Pop Out Boost. Keep Boost for the scenes that still need more.",
        "Pop-Out is saved with each clip, and every new clip starts at 0, so a scene's pop never carries over to the next.",
        "No sizes in the Low Res preset menu? Tick the ones you want, per aspect ratio, in Settings › Splatting › Custom Resolution Presets.",
        "A chapter with clips of different shapes? Tick Auto Size for the Loaded Clip: each clip's Low Res pass runs at its own shape's size.",
        "Rain painted flat onto the scene? Turn Rain on for that clip: the falling rain is kept apart and put back at depths of its own.",
        "Rain taking street lamps or lit windows for drops? Tick Keep lights out.",
        "Once a clip has keyframes, the dials show the values at the frame you're on. Turn a dial on a keyframe to change it; S adds a static keyframe, D a dynamic one.",
        "Want a dynamic keyframe to ease in, or swing back and forth? The Shaper under the keyframe buttons sets how the values glide into it.",
        "A depth map wrong in a scene? Edit Depth Map (Ctrl+M) opens it in the Depth Map Editor; Rerun in DepthCrafter, in the clip's right-click menu, makes it again.",
        "Masked subtitles at the wrong depth? Text Conv sets the text behind the screen or out in front of it.",
        "A clip done? Right-click its row in Upcoming Jobs and choose Send to Inpainting to take just that clip on.",
        "A dark scene hard to judge? Raise Preview Brightness: it only changes the preview. Double-click it to put it back to 1.00.",
        "Full Preview opens the preview in its own window; F11 there goes full screen, and Escape closes it."
    ] },
    { key: "inpainting", title: "Inpainting", tips: [
        "Fill going chaotic on a pan, inventing detail that slides against the camera? Keep Frames Chunk at 25.",
        "A seam where two chunks join? Raise Frame Overlap — 10 is common. Those frames are inpainted twice, so it costs time.",
        "Out of VRAM? Try a CPU Offload mode or a higher Tile Number before lowering Frames Chunk.",
        "Trying out settings? Set Process Length to a few dozen frames to test them on the start of a clip.",
        "Only one clip to redo? Right-click it in Upcoming Jobs and choose Inpaint This Clip Now.",
        "Want to redo the finish later (Color Transfer, a new Hi-Res Blend clip)? Keep Inpaint Cache For Re-Merge lets a rerun skip the inpaint.",
        "The inpainted eye's colours drifting from the left eye? Turn on Enable Color Transfer.",
        "Fill making things up? Raise Original Input Bias — less hallucination, at some cost to consistency.",
        "Specks of the hole left at the edges of the fill? Raise Inpaint Dilate Kernel to grow the mask first.",
        "Each chunk runs every inference step once per tile, so Inference Steps and Tile Number multiply the time.",
        // 2026-10-11
        "Guidance Scale 1.0 (the default) fills each step once: fastest, lightest on VRAM. Above 1.0 is 30–45% slower for much the same picture.",
        "A long unattended run? Torch Compile makes each chunk about 13% faster, after a first chunk that takes about 10 minutes longer.",
        "The ETA beside the clip bar learns from your runs; it shows N/A while the models load and warm up.",
        "A clip done? Right-click its row in Upcoming Jobs and choose Send to Merging to take just that clip on."
    ] },
    { key: "merging", title: "Merging", tips: [
        "Checking a seam up close? Double-click it in the preview to zoom in there; double-click again for the whole frame.",
        "A thin dark line along the edge of the fill? Raise Dilate Kernel to cover it.",
        "A hard seam where the fill meets the picture? Raise Blur Kernel for a softer blend.",
        "Fill looking too clean beside grainy film? Add matching grain to inpainted fills, and tune it in Grain Match Preview.",
        "Auto-match grain per clip on render measures each clip's grain and sets the amount for it.",
        "Colours in the fill drifting from the rest of the frame? Turn on Enable Color Transfer.",
        "Running out of RAM on long clips? Lower Batch Chunk Size.",
        "An unusual frame size like 1920x800? Pad to 16:9 adds bars so 3D players accept it.",
        "Testing settings? Process Current Clip merges just the clip in the preview.",
        "Click the preview to flip it between its Front and Back sources, such as the anaglyph and the blended right eye.",
        "Turned Dilate Kernel, Blur Kernel or any other knob too far? Double-click it (or right-click) to put it back to its default.",
        "Resume moves each finished clip into a finished folder and skips it next time, so a stopped batch picks up where it left off.",
        "Short on VRAM? Untick Use GPU for Mask Processing: slower, but it uses very little.",
        // 2026-10-11
        "Every scene merged? Merge All SBS to 1 Video joins them into the whole movie, in order. Set the layout and the movie file for the sound in Encoding Options › Merge All.",
        "Left some scenes out on purpose? Merge All's Merge Anyway puts black of each missing scene's length in its place, so the sound still lines up.",
        "Merge All lists any scene that isn't merged yet, with the stage it still needs: Send to takes it straight there.",
        "Erased an object in the Depth Map Editor with Put back on? Merging lays it back over both eyes, at the depth you set."
    ] },
    { key: "depthedit", title: "Depth Map Editor", tips: [
        "Something the depth map missed, or left as a blob? Set a layer's Mask to Add: the mask becomes a new shape at its own Depth, and Sample takes the Depth from the object.",
        "Add has Alter's other controls too: the edge dials move the shape's own edges, and a gradient gives it some roundness.",
        "A tracked mask shimmering on a locked-off shot? Raise Steady to even it out over the frames around it.",
        "Ctrl+Z undoes the last edit; S and D key the selected layer, static or dynamic.",
        "An object to take out? A layer set to Erase fills its depth from around it; Erase from Clip removes it from the video too.",
        "A bright speck on an object's left edge, spiking in 3D? Turn on Specks to see them, then Despeck in that object's layer.",
        "Check an edit in 3D: the 3D view draws it as Splatting would, through Splatting's own settings for the clip.",
        "Opened from Splatting with Edit Depth Map? Once it's rendered, Use in Splatting puts the edited map in the original's place."
    ] }
]

// One group's tips ([] for a key with none).
function forStage(key) {
    for (var i = 0; i < GROUPS.length; ++i)
        if (GROUPS[i].key === key) return GROUPS[i].tips
    return []
}
