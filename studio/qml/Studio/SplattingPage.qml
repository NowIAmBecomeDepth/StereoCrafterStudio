import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Layouts
import Studio
import "glasses.js" as Glasses
import "tips.js" as Tips

// Splatting, v2. v1's compact arrangement (splatting_gui._create_widgets),
// section for section and control for control:
//
//   Input/Output Folders (full width) + Encoding / 8-bit | 10-bit
//   [ preview band -- phase 3 ]
//   Process Resolution          | Depth Map Pre-processing | Special FX
//   Splatting & Output Settings | Stereo Projection        |   Pop Out Boost
//   Dev Tools                   |                          |   Keyframe
//   Current Clip Info           |                          |   Frame Break
//   [ progress, status, SINGLE START STOP ... ]
//
// The preview is stages/splatting/preview.py over v1's generated engine; the
// region editors (Hole Shift, Detect Text) open their own windows, as v1's
// Toplevels did; Edit Depth Map beside them opens the Depth Map Editor.
Item {
    id: page
    property bool active: false
    property real flipAngle: 0
    property var prompt: null
    property var consoleRef: null

    readonly property var sp: Splatting
    readonly property var st: sp.settings
    // Studio > Use Beginner Mode: v1's rule key -> hidden / renamed (beginner.py).
    readonly property var bh: Beginner.hide
    readonly property var bn: Beginner.names
    readonly property bool idle: !sp.busy

    // A picked chapter fills every path, so the rows are put away (v1's
    // collapse); Change Folders brings them back without leaving it.
    property bool foldersOpen: false
    readonly property bool chapterMode: sp.hasProject && sp.chapterNum >= 0
    // Manual paths: put away by Hide Folders, remembered (user, 2026-09-30)
    readonly property bool manualHidden: !!Shell.foldersHidden["splatting"]
    readonly property bool pathsFolded: chapterMode ? !foldersOpen : manualHidden
    function toggleFolders() {
        if (chapterMode) foldersOpen = !foldersOpen
        else Shell.setFoldersHidden("splatting", !manualHidden)
    }

    // The narrowest width nothing clips or overlaps at, every fold open
    // (measured 1460; the Stereo Projection checkbox row sets it). v1 fits
    // this arrangement at 1788 on the 4K screen.
    readonly property int compactWidth: 1460
    // Wide: the rails at their own width, a preview at least v1's
    // _WIDE_MIN_PREVIEW_HEIGHT tall, and the depth band's two boxes side by
    // side under it (Stereo Projection's checkbox row sets that width).
    // The rails the same width, so the preview sits centred (user, 2026-09-27:
    // the left narrower, its rows re-flowed; the right wider for the clip list)
    readonly property int leftRail: 440
    readonly property int rightRail: 440
    readonly property int wideWidth: 1900
    readonly property int widePreviewMin: 460
    readonly property int compactPreviewHeight: 420
    // The screen's room, in page units (the window's zoom taken out).
    readonly property real roomWidth: (Screen.width || 1920) / (Window.window && Window.window.zoom ? Window.window.zoom : 1) - 24
    readonly property bool wide: width >= wideWidth - 1
    readonly property int requiredWidth: roomWidth >= wideWidth ? wideWidth : compactWidth
    readonly property real requiredHeight: content.y + content.implicitHeight + Theme.gap + actionBar.implicitHeight + 12

    transform: Rotation {
        origin.x: page.width / 2; origin.y: page.height / 2
        axis { x: 0; y: 1; z: 0 }
        angle: page.flipAngle
    }

    component SpBox: TextBox { store: page.st; owner: page.sp }
    component SpDrop: Dropdown { store: page.st }
    component SpCheck: AnimCheck {
        property string key
        property string help: ""
        checked: page.st && key ? !!page.st[key] : false
        onToggled: { page.st[key] = checked; checked = Qt.binding(() => !!page.st[key]) }
        HoverTip { tip: parent.help }
    }
    // Held, a slider makes the preview render live on the GPU pre-processing;
    // let go, the exact frame follows (stages/splatting/fast_depth.py).
    component SpSlider: SliderRow {
        store: page.st
        onPressedChanged: page.sp.previewer.setDragging(pressed)
    }
    // The same, as a knob (user, 2026-09-27: dials in place of most sliders,
    // side by side where the sliders took a row each).
    component SpDial: DialField {
        store: page.st
        // small: the point is the height they save (user: "can the knobs be shrunk")
        size: 46
        valueSize: Theme.smallSize
        captionLines: 1
        Layout.fillWidth: true
        Layout.alignment: Qt.AlignTop
        onPressedChanged: page.sp.previewer.setDragging(pressed)
        // A keyframed setting shows its value at the frame on screen, playing too (user,
        // 2026-10-11: as the Depth Map Editor's dials) -- not while turned off a keyframe,
        // where the hand would fight it
        shown: { const v = (page.pv.keyed || {})[key]; return v !== undefined && !(pressed && !page.pv.onKey) ? v : undefined }
        onMoved: (v) => page.keyEdit(key, v)
        // keyed: the keyframe takes the turn, not the clip's base value as well
        writesStore: (page.pv.keyed || {})[key] === undefined
    }
    // A keyframed dial turned: on a keyframe that keyframe takes the value; off one, a dynamic
    // keyframe is added there with the turn (user, 2026-10-25: as video editors do -- the dial
    // used to go back, and a key one frame off looked frozen). Said each time one is added.
    function keyEdit(k, v) {
        if ((pv.keyed || {})[k] === undefined) return
        const wasOn = pv.onKey
        if (pv.setKeyValue(k, v) && !wasOn)
            Theme.toast("info", "Keyframe added",
                        "A dynamic keyframe was added on this frame with your change. Clear Current takes it off "
                        + "again; S on this frame makes it static.")
    }
    component Caption: Text {
        color: parent && parent.enabled === false ? Theme.fgFaint : Theme.fg
        font.family: Theme.bodyFont
        font.pixelSize: Theme.bodySize
        Layout.alignment: Qt.AlignVCenter
    }
    function help(k) { return page.sp.help[k] || "" }
    // The Mesh row (Splatting & Output Settings): preview-only settings, said plainly
    readonly property var meshTips: ({
        bias: "Which eye the 3D shape leans toward. 0 keeps it centred between the eyes; -1 leans it toward the "
              + "left eye, 1 toward the right. Preview only (Mesh Warp / SBS + Mesh). Default 0.0.",
        dolly: "Moves the point the depth stretches toward closer to or further from the virtual cameras, like a "
               + "dolly zoom: it changes how steeply the nearest things come forward. 0 puts it at the cameras. "
               + "Preview only (Mesh Warp / SBS + Mesh). Default 0.0.",
        extrusion: "How far the nearest parts of the picture are pushed out toward you: higher gives a deeper, "
                   + "stronger 3D shape, lower a flatter one. A value below 0 picks it for you from Convergence. "
                   + "Preview only (Mesh Warp / SBS + Mesh). Default 0.5.",
        density: "How fine the 3D mesh is, as a share of the picture's pixels: 0.5 is one point for every other "
                 + "pixel. Lower draws faster but looks blockier; 0.2-0.5 is plenty for judging. Only affects how "
                 + "the Mesh Warp preview is drawn. Default 0.5."
    })

    onActiveChanged: {
        if (active) {
            sp.refreshImport(); sp.refreshChapters()
            // the Depth Map Editor's 3D view rendered through this preview meanwhile
            if (pv) pv.refreshIfStale()
            // the clip list picks up new files each time the stage is shown
            if (idle && pv && pv.jobClips) pv.jobClips.rescan()
        }
        else sp.previewer.pause()        // v1: playback never runs off-screen
    }

    readonly property var pv: sp.previewer
    property bool autoConvRunning: false
    readonly property string kfAtFrame: {
        const k = pv.keyframes
        for (let i = 0; i < k.length; ++i) if (k[i].frame === pv.frame) return k[i].kind
        return ""
    }
    // v1 _kf_refresh_summary ticks the card on when the clip has keyframes.
    Connections {
        target: page.pv
        function onChanged() { if (page.pv.keyframes.length > 0) page.keyOpen = true }
    }
    property int ffStep: 5               // v1 fast_forward_step_var: session-only
    Connections {
        target: page.pv
        function onAutoConvRunning(on) { page.autoConvRunning = on }
    }

    // ---- v1's preview keys (video_previewer: arrows, Ctrl+arrows, Up/Down,
    // Space, Shift+Space). Not while typing in a field.
    // Only while the main window has keyboard focus (Shell.focusWindow, not
    // Window.active -- see shell.py): otherwise an arrow key pressed in the
    // Full Preview or Detect Text window matches this Shortcut too, and a
    // key that matches twice fires neither.
    readonly property bool keysLive: active && pv.loaded && Window.window === Shell.focusWindow && !(Window.activeFocusItem
                                     && Window.activeFocusItem.hasOwnProperty("cursorPosition"))
    // Full Preview, full screen on Studio's screen (user, 2026-10-13); Escape / F11 there close it
    function openFullPreview() { fullPreview.openFull(page.Window.window ? page.Window.window.screen : null) }
    Shortcut { objectName: "fullPreviewShortcut"; enabled: page.keysLive; sequence: "F11"; onActivated: page.openFullPreview() }
    Shortcut { enabled: page.keysLive; sequence: "Left"; onActivated: page.pv.stepFrames(-1) }
    Shortcut { enabled: page.keysLive; sequence: "Right"; onActivated: page.pv.stepFrames(1) }
    Shortcut { enabled: page.keysLive; sequence: "Shift+Left"; onActivated: page.pv.stepFrames(-10) }
    Shortcut { enabled: page.keysLive; sequence: "Shift+Right"; onActivated: page.pv.stepFrames(10) }
    Shortcut { enabled: page.keysLive; sequence: "Ctrl+Left"; onActivated: page.pv.step(-1) }
    Shortcut { enabled: page.keysLive; sequence: "Ctrl+Right"; onActivated: page.pv.step(1) }
    Shortcut { enabled: page.keysLive; sequence: "Up"; onActivated: page.pv.step(1) }
    Shortcut { enabled: page.keysLive; sequence: "Down"; onActivated: page.pv.step(-1) }
    Shortcut { enabled: page.keysLive; sequence: "Space"; onActivated: page.pv.togglePlay() }
    Shortcut { enabled: page.keysLive; sequence: "Shift+Space"; onActivated: page.pv.toggleFastForward(page.ffStep) }
    // (user, 2026-10-02) first / last frame; S / D a static / dynamic keyframe
    // (plain letters: not while typing, as keysLive says); Ctrl+S the sidecar,
    // Ctrl+T Detect Text (Ctrl+M: Edit Depth Map, below)
    Shortcut { enabled: page.keysLive; sequence: "Home"; onActivated: page.pv.seek(0) }
    Shortcut { enabled: page.keysLive; sequence: "End"; onActivated: page.pv.seek(Math.max(0, page.pv.frameCount - 1)) }
    Shortcut { objectName: "keyStaticShortcut"; enabled: page.keysLive; sequence: "S"; onActivated: page.pv.addKeyframe("static") }
    Shortcut { objectName: "keyDynamicShortcut"; enabled: page.keysLive; sequence: "D"; onActivated: page.pv.addKeyframe("dynamic") }
    Shortcut { objectName: "sidecarShortcut"; enabled: page.keysLive && page.idle; sequence: "Ctrl+S"; onActivated: page.updateSidecar() }
    Shortcut { objectName: "detectTextShortcut"; enabled: page.keysLive; sequence: "Ctrl+T"; onActivated: subMaskWin.openFor() }
    Shortcut { objectName: "editDepthShortcut"; enabled: page.keysLive && page.canEditDepth; sequence: "Ctrl+M"; onActivated: page.editDepthMap() }

    // Rerun in DepthCrafter (user, 2026-10-10): the clip in DepthCrafter's input, the
    // folder its depth map lives in as the output, and DepthCrafter's page shown --
    // the settings are the user's to change; Start runs it
    function rerunInDepthCrafter(path) {
        if (DepthCrafter.busy) { Theme.toast("warning", "DepthCrafter is busy", "Wait for its job to finish, then try again."); return }
        DepthCrafter.loadForRerun(path, page.sp.depthFolderFor(path))
        Shell.openStage(0, -1)
    }

    // Edit Depth Map: this clip's depth map, straight into the Depth Map Editor
    // (its render offers the way back) -- the button and Ctrl+M
    readonly property bool canEditDepth: pv.loaded && idle && !!pv.currentDepth
    function editDepthMap() {
        const clip = pv.clipPaths[pv.videoIndex] || ""
        DepthEdit.openFromSplatting(clip, pv.currentDepth)
        Shell.openStage(4, 1)          // Extra, the Depth Map Editor
    }

    // Update Sidecar: v1 asks before overwriting an existing sidecar.
    function updateSidecar() {
        if (!pv.sidecarExists) { pv.updateSidecar(); return }
        const name = String(pv.currentDepth).replace(/\\/g, "/").split("/").pop().replace(/\.[^.]*$/, "")
        confirm("Overwrite Sidecar File?",
                "This will overwrite parameters (Convergence, Disparity, Gamma, Borders) in the existing sidecar "
                + "file:\n\n" + name + ".fssidecar\n\nDo you want to continue?",
                "Overwrite", () => pv.updateSidecar())
    }

    FullPreviewWindow {
        id: fullPreview; pv: page.pv
        stage: "splatting"; glasses: String(page.st.preview_source) === "Glasses-Free 3D"
        // Convergence, Disparity and Gamma under the frame slider (user, 2026-10-14): the page's
        // own dials -- keyframes, Beginner names and the live preview as there
        dials: Component {
            Row {
                objectName: "fullPreviewStereoDials"
                spacing: 22
                SpDial { key: "zero_disparity_anchor"; label: "Convergence:"; from: 0; to: 1; step: 0.01; decimals: 2; defaultValue: 0.5; fillFrom: 0.5; help: page.help("convergence_point"); size: 54; onDark: true; width: 84 }
                SpDial { key: "max_disp"; label: page.bn["splat.depth_strength"] || "Disparity:"; from: 0; to: 100; step: 1; decimals: 0; defaultValue: 30; help: page.help("max_disp"); size: 54; onDark: true; width: 84 }
                SpDial { key: "depth_gamma"; label: "Gamma:"; from: 0.1; to: 3.0; step: 0.05; decimals: 2; defaultValue: 1.0; fillFrom: 1.0; help: page.help("depth_gamma"); size: 54; onDark: true; width: 84 }
            }
        }
    }
    HoleShiftWindow { id: holeShiftWin; objectName: "holeShiftWin"; pv: page.pv; st: page.st; prompt: page.prompt }
    SubtitleMaskWindow { id: subMaskWin; objectName: "subMaskWin"; pv: page.pv; prompt: page.prompt }

    // File > Load Fusion Export / FSExport to custom sidecar: v1's two file
    // prompts (core.splatting.fusion_export), asked here, then the run.
    QtObject { id: fusionFlow; property string mode: ""; property string exportPath: "" }
    FileDialog {
        id: fusionExportDlg; title: "Select Fusion Export (.fsexport) File"
        nameFilters: ["Fusion Export Files (*.fsexport.txt *.fsexport)", "All Files (*)"]
        onAccepted: {
            fusionFlow.exportPath = Shell.urlToPath(selectedFile)
            if (fusionFlow.mode === "generate_sidecars") fusionTargetDlg.open()
            else fusionSaveDlg.open()
        }
    }
    FolderPicker {
        id: fusionTargetDlg; title: "Select Target Depth Map Folder"
        currentFolder: Shell.pathToUrl(page.st.input_depth_maps)
        onAccepted: page.sp.runFusion(fusionFlow.mode, fusionFlow.exportPath, Shell.urlToPath(selectedFolder))
    }
    FileDialog {
        id: fusionSaveDlg; title: "Save Sidecar As"
        fileMode: FileDialog.SaveFile; defaultSuffix: "fssidecar"
        nameFilters: ["Sidecar Files (*.fssidecar)"]
        currentFile: Shell.pathToUrl(fusionFlow.exportPath.replace(/\.fsexport(\.txt)?$/i, "") + ".fssidecar")
        onAccepted: page.sp.runFusion(fusionFlow.mode, fusionFlow.exportPath, Shell.urlToPath(selectedFile))
    }

    // v1 show_encoding_options_popup: the shared dialog over this stage's names.
    EncodingDialog {
        id: encodingDlg
        ctl: page.sp
        dualCrf: true
        keyMap: ({ encoding_encoder: "encoder", color_tags: "color_tags_mode",
                   nvenc_lookahead_enabled: "encoding_nvenc_lookahead_enabled",
                   nvenc_lookahead: "encoding_nvenc_lookahead", nvenc_spatial_aq: "encoding_nvenc_spatial_aq",
                   nvenc_temporal_aq: "encoding_nvenc_temporal_aq",
                   nvenc_aq_strength: "encoding_nvenc_aq_strength" })
    }

    Connections {
        target: page.sp
        function onToast(kind, title, message) { Theme.toast(kind, title, message) }
    }
    function confirm(title, message, yesText, action) {
        page.prompt.ask({
            title: title, message: message,
            buttons: [{ text: yesText, code: 1, kind: "filled" }, { text: "Cancel", code: 0, kind: "ghost" }],
            onDone: (code) => { if (code === 1) action() }
        })
    }

    // ---- Special FX cards: v1's _fx_active / _fx_reset / _fx_follow_value --
    // A card starts ticked if its effect is on, ticks itself when the effect
    // comes on from elsewhere, and unticking resets the effect to off.
    // Preview Scale's Fit (user, 2026-10-03): on until a percentage is picked
    property bool previewFitOn: true
    property bool popOpen: Number(st.boost_amount) > 0
    property bool breakOpen: Number(st.frame_break_bars) > 0
    // Rain (user, 2026-10-06; core.splatting.rain): open while the clip has it on
    readonly property bool rainOn: st.rain_mode === "moving" || st.rain_mode === "static"
    readonly property bool sparksOn: st.rain_particle === "sparks"
    // Web (user, 2026-10-09): the user's own (personal/web), offered only where it is installed
    readonly property bool webOn: st.rain_particle === "web" && Shell.personalWeb
    property bool rainOpen: rainOn
    property bool keyOpen: false
    Connections {
        target: page.st
        function onValueChanged(key, value) {
            if (key === "boost_amount" && Number(value) > 0) page.popOpen = true
            if (key === "frame_break_bars" && Number(value) > 0) page.breakOpen = true
        }
    }
    function resetFx(kind) {
        const resets = kind === "pop_boost"
            ? [["boost_amount", 0.0], ["boost_thresh", 0.85], ["boost_curve", 2.0],
               ["boost_prefilter_radius", 0], ["boost_prefilter_eps", 0.001]]
            : [["frame_break_bars", 0.0], ["frame_break_thresh", 1.0], ["fb_erode_left", 0.0],
               ["fb_erode_right", 0.0], ["fb_erode_global", 0.0], ["fb_edge_blur", 0.0]]
        for (const [k, v] of resets) page.st[k] = v
        if (kind === "frame_break") page.st.fb_preset = "Custom"
    }

    // ---- file dialogs ----------------------------------------------------
    // one picker for a folder or a single clip (user, 2026-10-05: FolderPicker, Columns)
    FolderPicker {
        id: srcFolderDlg; title: "Input Source Clips"
        allowFiles: true
        fileTypes: ["mp4", "avi", "mov", "mkv"]
        currentFolder: page.st.input_source_clips
        onAccepted: page.st.input_source_clips = selectedPath.replace(/\\/g, "/")
    }
    FolderPicker {
        id: depthFolderDlg; title: "Input Depth Maps"
        allowFiles: true
        fileTypes: ["mp4", "npz"]
        currentFolder: page.st.input_depth_maps
        onAccepted: page.st.input_depth_maps = selectedPath.replace(/\\/g, "/")
    }
    FolderPicker {
        id: outFolderDlg; title: "Output Splatted"
        currentFolder: Shell.pathToUrl(page.st.output_splatted)
        onAccepted: page.st.output_splatted = Shell.urlToPath(selectedFolder).replace(/\\/g, "/")
    }
    FileDialog {
        id: loadSettingsDlg; title: "Load Settings from File"
        nameFilters: ["Splatting settings (*.splatcfg *.json)", "All files (*)"]
        onAccepted: page.sp.loadSettingsFrom(Shell.urlToPath(selectedFile))
    }
    FileDialog {
        id: saveSettingsDlg; title: "Save Settings to File"
        fileMode: FileDialog.SaveFile; defaultSuffix: "splatcfg"
        nameFilters: ["Splatting settings (*.splatcfg)", "All files (*)"]
        onAccepted: page.sp.saveSettingsTo(Shell.urlToPath(selectedFile))
    }

    // ---- the page ----------------------------------------------------------
    ColumnLayout {
        id: content
        objectName: "pageContent"
        x: 18
        y: 14
        width: page.width - 36
        height: page.height - y - Theme.gap - actionBar.implicitHeight - 12
        spacing: Theme.gap
        // idle-only per section, not on the whole body: Current Clip Info's
        // Open Console stays live during a job (user, 2026-09-30)

        Section {
            title: "Input/Output Folders"
            enabled: page.idle
            headerRight: [
                AccentButton {
                    visible: page.sp.importDescription.length > 0
                    text: "Import from " + page.sp.importOrigin
                    kind: "tonal"
                    iconGlyph: Theme.icon.importIcon
                    onClicked: page.sp.importFromPrevious()
                    HoverTip {
                        tip: "Fill Input Source Clips and Input Depth Maps from the most recent completed "
                             + page.sp.importOrigin + " job -- whichever ran last, on this machine. There is "
                             + "no picker: to use an older job, set the folders by hand.\n\nClicking replaces "
                             + "whatever those two fields currently hold.\n\nMost recent job:\n"
                             + page.sp.importDescription
                    }
                },
ChangeFoldersButton {
                    open: !page.pathsFolded
                    manual: !page.chapterMode
                    onClicked: page.toggleFolders()
                    paths: [{ label: "Input Source Clips", value: page.st.input_source_clips },
                            { label: "Input Depth Maps", value: page.st.input_depth_maps },
                            { label: "Output Splatted", value: page.st.output_splatted }]
                },
                ChapterPicker { visible: page.sp.hasProject; dc: page.sp; onPicked: page.foldersOpen = false }
            ]
            // A chapter picked from the project fills all three paths, so the
            // fields and their buttons fold away and the dropdown is the
            // whole control; "Manual paths" in it brings them back.
            FieldRow {
                visible: !page.pathsFolded
                label: "Input Source Clips"; labelWidth: 140; help: page.help("input_source_clips")
                RowLayout {
                    width: parent.width
                    spacing: 8
                    SpBox { key: "input_source_clips"; numeric: false; Layout.fillWidth: true }
                    AccentButton { objectName: "browseSource"; text: "Browse"; kind: "outline"; onClicked: srcFolderDlg.open()
                                   HoverTip { tip: "Pick a folder to splat every clip in it, or a single clip to splat just that one." } }
                }
            }
            FieldRow {
                visible: !page.pathsFolded
                label: "Input Depth Maps"; labelWidth: 140; help: page.help("input_depth_maps")
                RowLayout {
                    width: parent.width
                    spacing: 8
                    SpBox { key: "input_depth_maps"; numeric: false; Layout.fillWidth: true }
                    AccentButton { objectName: "browseDepth"; text: "Browse"; kind: "outline"; onClicked: depthFolderDlg.open()
                                   HoverTip { tip: "Pick the folder of depth maps, or a single depth map for a single clip." } }
                }
            }
            FieldRow {
                visible: !page.pathsFolded
                label: "Output Splatted"; labelWidth: 140; help: page.help("output_splatted")
                RowLayout {
                    width: parent.width
                    spacing: 8
                    SpBox { key: "output_splatted"; numeric: false; Layout.fillWidth: true }
                    AccentButton { text: "Browse Folder"; kind: "outline"; onClicked: outFolderDlg.open() }
                    SpCheck { key: "multi_map"; visible: !page.bh["splat.multi_map"]; text: "Multi-Map"; help: page.help("multi_map") }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                AccentButton {
                    // styled as Start, with a film icon (user, 2026-09-27)
                    objectName: "encodingButton"
                    text: "Encoding Options…"; iconGlyph: Theme.icon.video
                    onClicked: encodingDlg.open()
                    HoverTip { tip: "Open Encoding Options to customize the codec, encoder, quality, CRF (full and "
                                    + "low res) and NVENC / DNxHR options. The 8-bit / 10-bit buttons beside it set "
                                    + "the codec side in one click." }
                }
                SegmentedControl {
                    model: page.sp.presetNames
                    controlled: true
                    currentIndex: page.sp.presetNames.indexOf(page.sp.encodingPreset)
                    onActivated: (i) => page.sp.applyEncodingPreset(page.sp.presetNames[i])
                    HoverTip { tip: page.sp.presetTooltip }     // as Merging's (user, 2026-10-25)
                }
                Item { Layout.fillWidth: true }
                // Multi-Map lives on the Output row; with that row folded
                // away it moves here.
                SpCheck {
                    visible: page.pathsFolded && !page.bh["splat.multi_map"]
                    key: "multi_map"; text: "Multi-Map"; help: page.help("multi_map")
                }
            }
        }

        // ---- the body: v1's two shapes (splatting_gui._apply_body_layout) -----
        //   wide:     left rail | preview, Depth + Stereo under it | Special FX
        //   compact:  the preview across the top, then the three columns
        // Wide whenever the page is wide enough for the rails and a usable
        // preview, as v1 decides it (_wide_layout_fits).
        Item {
            id: body
            objectName: "splatBody"
            Layout.fillWidth: true
            Layout.fillHeight: true
            // (the clip list under Special FX asks for no height: it takes what is left)
            implicitHeight: page.wide
                            ? Math.max(leftCol.implicitHeight, fxCol.implicitHeight,
                                       page.widePreviewMin + Theme.gap + band.implicitHeight)
                            : page.compactPreviewHeight + Theme.gap
                              + Math.max(leftCol.implicitHeight, band.implicitHeight, fxCol.implicitHeight)

            // -- left: Process Resolution, Splatting & Output, Dev Tools, Clip Info
            ColumnLayout {
                id: leftCol
                x: 0
                y: page.wide ? 0 : page.compactPreviewHeight + Theme.gap
                width: page.wide ? page.leftRail : (body.width - 2 * Theme.gap) * 7 / 17
                spacing: Theme.gap

                Section {
                    title: "Process Resolution"
                    enabled: page.idle
                    GridLayout {
                        Layout.fillWidth: true
                        columns: 3
                        columnSpacing: 10
                        rowSpacing: Theme.rowGap
                        SpCheck { key: "enable_full_res"; visible: !page.bh["splat.full_res"]; text: "Enable Full Res"; help: page.help("enable_full_res") }
                        RowLayout {
                            visible: !page.bh["splat.full_res_batch"]
                            enabled: !!page.st.enable_full_res
                            Caption { text: "Batch:"; HoverTip { tip: "Batch Size: how many frames go to the GPU at once for the full-resolution splat." } }
                            SpBox { key: "batch_size"; boxWidth: 56 }
                        }
                        SpCheck { key: "dual_output"; visible: !page.bh["splat.dual_output"]; text: "Dual Output Only"; help: page.help("dual_output") }
                        SpCheck { key: "enable_low_res"; visible: !page.bh["splat.low_res"]; text: "Enable Low Res"; help: page.help("enable_low_res") }
                        RowLayout {
                            visible: !page.bh["splat.low_res_batch"]
                            enabled: !!page.st.enable_low_res
                            Caption { text: "Batch:"; HoverTip { tip: "Batch Size: how many frames go to the GPU at once for the low-resolution splat." } }
                            SpBox { key: "low_res_batch_size"; boxWidth: 56 }
                        }
                        SpCheck { key: "strict_ffmpeg_decode"; visible: !page.bh["splat.ffmpeg"]; text: "ffmpeg"; help: page.help("strict_ffmpeg_decode") }
                    }
                    RowLayout {
                        spacing: 8
                        enabled: !!page.st.enable_low_res
                        Caption { text: "Width:" }
                        SpBox { key: "pre_res_width"; boxWidth: 64 }
                        Caption { text: "Height:"; Layout.leftMargin: 4 }
                        SpBox { key: "pre_res_height"; boxWidth: 64 }
                        // DepthCrafter's preset button (user, 2026-10-01): a column per shape;
                        // the columns are this machine's own, made in Settings > Splatting (2026-10-11)
                        PresetButton {
                            objectName: "splatPresetButton"
                            Layout.leftMargin: 4
                            dc: page.sp
                            st: page.st
                            widthKey: "pre_res_width"
                            heightKey: "pre_res_height"
                            apply: (w, h) => page.sp.applyResolutionSize(w, h)
                            emptyText: "No sizes yet. Tick some in Settings > Splatting > Custom Resolution Presets."
                            HoverTip { tip: page.help("resolution_preset") }
                        }
                    }
                    // the loaded clip's shape picks Low Res Width/Height (user, 2026-10-11)
                    SpCheck {
                        objectName: "splatAutoSize"
                        key: "low_res_auto_size"
                        enabled: !!page.st.enable_low_res
                        text: "Auto Size for the Loaded Clip" + (page.sp.loadedShape ? "  (" + page.sp.loadedShape + ")" : "")
                        help: "Fill Low Res Width/Height with the size picked for the loaded clip's aspect ratio "
                              + "(Settings > Splatting > Custom Resolution Presets > Auto Size picks). In a batch, "
                              + "each clip of another shape runs its Low Res pass at its own shape's pick, so a "
                              + "chapter that switches aspect ratios gets the right size for every clip."
                    }
                }

                FoldSection {
                    title: "Splatting & Output Settings"
                    enabled: page.idle
                    chevron: true
                    visible: !page.bh["splat.output_settings"]
                    open: !!page.st.output_settings_shown
                    onToggled: (on) => page.st.output_settings_shown = on
                    help: page.help("output_settings_shown")
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Caption { text: "Process Length:" }
                        SpBox { key: "process_length"; boxWidth: 54 }
                        Item { Layout.fillWidth: true }
                        Caption { text: "Auto-Converge:" }
                        SpDrop {
                            key: "auto_convergence_mode"; model: page.sp.choices.auto_convergence_mode; boxWidth: 110
                            enabled: !page.autoConvRunning
                            // Choosing a mode applies its cached anchor, or runs the pre-pass on the preview clip.
                            onActivated: if (page.pv.loaded) Qt.callLater(page.pv.autoConvergeModeChosen)
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Caption { text: "Mask:" }
                        SpDrop { key: "mask_mode"; model: page.sp.choices.mask_mode; boxWidth: 90 }
                        SpCheck { key: "mask_highlight"; text: "Highlight Holes"; help: page.help("mask_highlight") }
                        Item { Layout.fillWidth: true }
                    }
                    // v1 _on_m2s_backend_changed: sensitivity and taper exist for
                    // Hybrid and Dual only (Dual is Hybrid at full resolution).
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        visible: ["HYBRID", "DUAL"].indexOf(String(page.st.mask_mode).toUpperCase()) >= 0
                        SpCheck { key: "hybrid_mask_taper"; text: "Taper"; help: page.help("hybrid_mask_taper") }
                        SpSlider {
                            key: "hybrid_mask_sensitivity"; label: "Sens:"; labelWidth: 40; valueWidth: 30
                            from: 0; to: 100; step: 1; decimals: 0; help: page.help("hybrid_mask_sensitivity")
                        }
                    }
                    // v1's Warp row: the backend changes the image itself, where
                    // Mask above only changes which pixels count as holes.
                    // The Fix strength exists for M2S+Fix only.
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Caption { text: "Warp:" }
                        SpDrop {
                            key: "splat_backend"; model: page.sp.choices.splat_backend; boxWidth: 100
                            HoverTip { tip: page.help("splat_backend") }
                        }
                        Item { Layout.fillWidth: true }
                        // Border shares the Warp row (the rail is narrower, 2026-09-27)
                        Caption { text: "Border:" }
                        SpDrop { key: "border_mode"; model: page.sp.choices.border_mode; boxWidth: 96 }
                        AccentButton {
                            text: "⟳"; implicitWidth: 34; kind: "outline"
                            enabled: page.pv.loaded
                            onClicked: page.pv.rescanBorders()
                            HoverTip { tip: page.help("border_rescan") }
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: String(page.st.splat_backend).toUpperCase() === "M2S+FIX"
                        SpSlider {
                            key: "m2s_fix_strength"; label: "Fix:"; labelWidth: 30; valueWidth: 34
                            from: 0.5; to: 2.0; step: 0.05; decimals: 2; defaultValue: 1.0
                            Layout.fillWidth: true
                            help: page.help("m2s_fix_strength")
                        }
                    }
                    // two by two (the rail is narrower, 2026-09-27)
                    RowLayout {
                        spacing: 10
                        // plain-language tips (user, 2026-09-27): what each is for and does
                        Caption { text: "Mesh:"; Layout.alignment: Qt.AlignTop | Qt.AlignLeft; Layout.topMargin: 6; HoverTip { tip: "Mesh Warp settings. These only change the \"Mesh Warp\" and \"SBS + Mesh\" preview sources: a second way of building the 3D that turns the depth map into a 3D surface and films it with two virtual cameras, so you can compare it with the normal splat. They never change the render." } }
                        GridLayout {
                            columns: 4
                            columnSpacing: 6
                            rowSpacing: Theme.rowGap
                            Caption { text: "Bias"; HoverTip { tip: page.meshTips.bias } }
                            SpBox { key: "mesh_warp_view_bias"; boxWidth: 52; HoverTip { tip: page.meshTips.bias } }
                            Caption { text: "Dolly"; Layout.leftMargin: 10; HoverTip { tip: page.meshTips.dolly } }
                            SpBox { key: "mesh_warp_dolly_zoom"; boxWidth: 52; HoverTip { tip: page.meshTips.dolly } }
                            Caption { text: "Extrusion"; HoverTip { tip: page.meshTips.extrusion } }
                            SpBox { key: "mesh_warp_extrusion_scale"; boxWidth: 52; HoverTip { tip: page.meshTips.extrusion } }
                            Caption { text: "Density"; Layout.leftMargin: 10; HoverTip { tip: page.meshTips.density } }
                            SpBox { key: "mesh_warp_preview_density"; boxWidth: 52; HoverTip { tip: page.meshTips.density } }
                        }
                    }
                }

                FoldSection {
                    title: "Dev Tools"
                    enabled: page.idle
                    chevron: true
                    visible: !page.bh["splat.dev_tools"]
                    open: !!page.st.dev_tools_shown
                    onToggled: (on) => page.st.dev_tools_shown = on
                    help: page.help("dev_tools_shown")
                    // two by two (the rail is narrower, 2026-09-27)
                    GridLayout {
                        columns: 2
                        columnSpacing: 16
                        rowSpacing: Theme.rowGap
                        SpCheck { key: "skip_lowres_preproc"; text: "Skip Low-Res Pre-proc"; help: page.help("skip_lowres_preproc") }
                        AnimCheck {
                            text: "Map Test"; checked: page.sp.mapTest
                            onToggled: { page.sp.mapTest = checked; checked = Qt.binding(() => page.sp.mapTest) }
                            HoverTip { tip: page.help("map_test") }
                        }
                        AnimCheck {
                            text: "Splat Test"; checked: page.sp.splatTest
                            onToggled: { page.sp.splatTest = checked; checked = Qt.binding(() => page.sp.splatTest) }
                            HoverTip { tip: page.help("splat_test") }
                        }
                        // "True Max" is gone (bug sweep 2026-10-09, A4): nothing ever measured
                        // or stored it, here or in the Prototype
                    }
                }

                ClipInfo {
                    labelWidth: 108
                    labelWidth2: 76
                    consoleRef: page.consoleRef
                    rows: [
                        { label: "Filename:", value: page.sp.info.filename },
                        { label: "Task:", value: page.sp.info.task_name },
                        { label: "Resolution:", value: page.sp.info.resolution, label2: "Disparity:", value2: page.sp.info.disparity },
                        { label: "Frames:", value: page.sp.info.frames, label2: "Converge:", value2: page.sp.info.convergence },
                        { label: "Gamma:", value: page.sp.info.gamma, label2: "Map:", value2: page.sp.info.map }
                    ]
                }
            }

            // -- the preview: v1's VideoPreviewer (the engine is v1's own, see
            //    stages/splatting/preview.py). Scrubber, the picture, and v1's
            //    two lines of controls under it.
            Rectangle {
                id: previewPanel
                objectName: "previewPanel"
                enabled: page.idle
                x: page.wide ? leftCol.width + Theme.gap : 0
                y: 0
                width: page.wide ? body.width - leftCol.width - fxCol.width - 2 * Theme.gap : body.width
                height: page.wide ? body.height - band.height - Theme.gap : page.compactPreviewHeight
                radius: 12
                color: Theme.panel
                border.width: Theme.outlineWidth
                border.color: Theme.outline
                clip: true

                readonly property var pv: page.sp.previewer
                // the Front / Back choices: Glasses-Free 3D always listed, greyed out (with why) unless
                // an SR display is connected and this window is on it (user, 2026-10-11)
                readonly property bool hasGlasses: typeof Glasses3D !== "undefined" && Glasses3D !== null
                readonly property var sources: hasGlasses ? page.sp.choices.preview_source.concat(["Glasses-Free 3D"])
                                                          : page.sp.choices.preview_source
                readonly property string glassesWhyNot: Glasses.whyNot(hasGlasses ? Glasses3D : null, Window.window)
                // Image pixels shown 1:1 on the screen, as v1's canvas shows
                // them: Preview Scale is a percentage of the CLIP, so neither
                // Windows' scaling nor the UI zoom may enlarge it again.
                readonly property real pxToItem: 1 / ((Screen.devicePixelRatio || 1) * (Window.window && Window.window.zoom ? Window.window.zoom : 1))

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 6

                    // Frame scrubber (v1: "Frame: n / N" and the scale)
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 10
                        enabled: previewPanel.pv.loaded
                        Caption {
                            text: "Frame: " + (previewPanel.pv.loaded ? (scrub.pressed ? Math.round(scrub.value) + 1 : previewPanel.pv.frame + 1) : 0)
                                  + " / " + previewPanel.pv.frameCount
                            font.features: { "tnum": 1 }
                            Layout.preferredWidth: 110
                        }
                        Slider {
                            id: scrub
                            objectName: "frameScrubber"
                            Layout.fillWidth: true
                            // v1's keyframe marker strip: a tick per keyframe,
                            // blue static, orange dynamic.
                            Repeater {
                                model: page.pv.keyframes
                                Rectangle {
                                    required property var modelData
                                    x: scrub.leftPadding + (page.pv.frameCount > 1 ? modelData.frame / (page.pv.frameCount - 1) : 0)
                                       * (scrub.availableWidth - 8) + 4 - width / 2
                                    y: scrub.topPadding + scrub.availableHeight / 2 + 5
                                    width: 4; height: 8; radius: 1
                                    color: modelData.kind === "dynamic" ? "#ffb347" : "#4fa8ff"
                                }
                            }
                            from: 0
                            to: Math.max(0, previewPanel.pv.frameCount - 1)
                            stepSize: 1
                            snapMode: Slider.SnapAlways
                            // not while held: a late seek's result pulled the handle back (sweep 2026-10-14c)
                            Binding on value { when: !scrub.pressed; value: previewPanel.pv.frame; restoreMode: Binding.RestoreNone }
                            // Live while held (user, 2026-09-27; v1 rendered only on
                            // release): each new frame is a seek -- the newest wins, so
                            // it keeps up with the hand -- rendered the fast way while
                            // held (setDragging), then exactly where it is let go.
                            property int lastSeek: -1
                            onMoved: {
                                const f = Math.round(value)
                                if (pressed && f !== lastSeek) { lastSeek = f; previewPanel.pv.seek(f) }
                            }
                            onPressedChanged: {
                                previewPanel.pv.setDragging(pressed)
                                if (!pressed) {
                                    const f = lastSeek >= 0 ? lastSeek : Math.round(value)   // where the hand let go
                                    lastSeek = -1
                                    previewPanel.pv.seek(f)
                                }
                            }
                            background: Rectangle {
                                x: scrub.leftPadding
                                y: scrub.topPadding + scrub.availableHeight / 2 - 2
                                width: scrub.availableWidth
                                height: 4
                                radius: 2
                                color: Theme.trough
                                Rectangle { width: scrub.visualPosition * parent.width; height: 4; radius: 2; color: Theme.accent }
                            }
                            handle: Rectangle {
                                x: scrub.leftPadding + scrub.visualPosition * (scrub.availableWidth - width)
                                y: scrub.topPadding + scrub.availableHeight / 2 - height / 2
                                width: 8; height: 18; radius: 3
                                color: Theme.knob
                                border.width: 2
                                border.color: Theme.accent
                            }
                        }
                        // Which side is up -- here, above the picture, not on it (user, 2026-09-27:
                        // the frame fits larger with nothing covering it)
                        Text {
                            objectName: "flipBadge"
                            visible: previewPanel.pv.loaded && !previewPanel.pv.loading
                            text: ((flipCard.backUp ? "Back" : "Front") + "  ·  " + flipCard.facing).toUpperCase() + "  ·  click or drag an edge to flip"
                            color: Theme.fgMuted
                            font.family: Theme.tabFont
                            font.pixelSize: Theme.smallSize
                            font.letterSpacing: 1
                            opacity: flipCard.busy ? 0.4 : 1
                            Behavior on opacity { NumberAnimation { duration: Theme.dur(160) } }
                        }
                    }

                    // v1's control lines, above the picture as in v1 -- first:: source, load, clear, scale, crosshair
                    RowLayout {
                        objectName: "previewSourceRow"
                        Layout.alignment: Qt.AlignHCenter
                        // Never wider than the panel (user, 2026-10-08: just over the wide
                        // layout's edge it pushed the picture over the panel's right border):
                        // the Front / Back boxes give way first.
                        Layout.fillWidth: false     // (the boxes' fillWidth would stretch it off centre)
                        Layout.maximumWidth: parent.width
                        spacing: 6
                        // Preview Source as a flip card's two sides (user, 2026-09-27, as
                        // Merging): the side facing you is the preview source; a click on
                        // the picture turns it over to the other.
                        Caption { text: "Front:" }
                        Dropdown {
                            objectName: "flipFront"
                            store: Shell; key: "splatFlipFront"
                            model: previewPanel.sources; measure: true; boxWidth: fitWidth
                            disabledItems: previewPanel.glassesWhyNot ? ["Glasses-Free 3D"] : []
                            disabledTip: previewPanel.glassesWhyNot
                            Layout.fillWidth: true; Layout.minimumWidth: 112; Layout.maximumWidth: boxWidth
                            HoverTip { tip: "The preview source on the front of the card. Click the picture to turn it over." }
                        }
                        Caption { text: "Back:" }
                        Dropdown {
                            objectName: "flipBack"
                            store: Shell; key: "splatFlipBack"
                            model: previewPanel.sources; measure: true; boxWidth: fitWidth
                            disabledItems: previewPanel.glassesWhyNot ? ["Glasses-Free 3D"] : []
                            disabledTip: previewPanel.glassesWhyNot
                            Layout.fillWidth: true; Layout.minimumWidth: 104; Layout.maximumWidth: boxWidth
                            HoverTip { tip: "The preview source on the back of the card. Click the picture to turn it over." }
                        }
                        CheckField {
                            objectName: "keepTracking"
                            // Keep Eye Tracking On (user, 2026-10-17; sr_display.py): only beside the choice
                            store: Shell; key: "glassesKeepTracking"
                            text: "Keep Tracking"
                            shown: previewPanel.hasGlasses && !previewPanel.glassesWhyNot
                                   && (Shell.splatFlipFront === "Glasses-Free 3D" || Shell.splatFlipBack === "Glasses-Free 3D")
                            Layout.fillWidth: false
                            help: "Keeps the 3D monitor's eye tracking (and its camera) on while a Glasses-Free 3D preview is open: "
                              + "between clips, in and out of Full Preview, and while a job runs -- so it does not have to find "
                              + "you again each time. Off: the camera is on only while the 3D picture is live. Either way it goes "
                              + "off when no preview shows Glasses-Free 3D, on another tab, and when Studio closes."
                        }
                        AccentButton {
                            text: "Clear Preview"
                            kind: "outline"
                            enabled: previewPanel.pv.loaded && !previewPanel.pv.loading
                            onClicked: previewPanel.pv.clear()
                            HoverTip { tip: "Releases the currently loaded preview and frees its GPU memory. Use this to "
                                            + "reclaim VRAM without closing the app -- e.g. before starting a render." }
                        }
                        Caption { text: "Preview Scale:"; Layout.leftMargin: 6 }
                        // Fit (user, 2026-10-03): the largest scale the room allows, past 100%
                        // too, kept as the window or the clip changes; a percentage picked here,
                        // the wheel or a double-click zoom stands instead
                        Dropdown {
                            objectName: "previewScaleBox"
                            boxWidth: 84
                            model: ["Fit"].concat(page.sp.choices.preview_size || [])
                            currentIndex: page.previewFitOn ? 0 : model.indexOf(String(page.st.preview_size))
                            displayText: page.previewFitOn ? "Fit" : String(page.st.preview_size)
                            onActivated: (i) => {
                                if (i === 0) { page.previewFitOn = true; previewFit.apply() }
                                else { page.previewFitOn = false; page.st.preview_size = model[i] }
                            }
                            HoverTip { tip: "Fit sizes the preview to fill the space it has, and keeps it filled as the "
                                            + "window or the clip changes. A percentage stays as picked; the mouse wheel "
                                            + "and a double-click zoom pick one too, and a double-click on a zoomed-in "
                                            + "picture goes back to Fit." }
                        }
                        SpCheck { key: "crosshair_enabled"; visible: !page.bh["splat.crosshair"]; text: "Crosshair"; help: page.help("crosshair_enabled"); Layout.leftMargin: 8 }
                        Repeater {
                            model: page.pv.depthMaps
                            AnimRadio {
                                required property var modelData
                                text: modelData
                                checked: String(page.st.selected_depth_map) === modelData
                                font.bold: checked
                                onToggled: {
                                    if (checked) { page.st.selected_depth_map = modelData; Qt.callLater(page.pv.chooseMap) }
                                    checked = Qt.binding(() => String(page.st.selected_depth_map) === modelData)
                                }
                                HoverTip { tip: "Multi-Map: preview (and save to this clip's sidecar) the depth map in this subfolder." }
                            }
                        }
                        SpCheck { visible: !!page.st.crosshair_enabled && !page.bh["splat.crosshair_white"]; key: "crosshair_white"; text: "White"; help: page.help("crosshair_white") }
                        SpCheck { visible: !!page.st.crosshair_enabled && !page.bh["splat.crosshair_multi"]; key: "crosshair_multi"; text: "Multi"; help: page.help("crosshair_multi") }
                    }
                    // ...and the second: clips, playback, brightness. Never wider
                    // than the panel: the brightness slider gives way first.
                    RowLayout {
                        Layout.alignment: Qt.AlignHCenter
                        Layout.maximumWidth: parent.width
                        spacing: 8
                        AccentButton {
                            text: "‹ Prev"; kind: "outline"
                            enabled: previewPanel.pv.loaded && previewPanel.pv.videoIndex > 0 && !previewPanel.pv.loading
                            onClicked: previewPanel.pv.step(-1)
                            HoverTip { tip: "Load the previous video in the list for preview." }
                        }
                        AccentButton {
                            text: "Next ›"; kind: "outline"
                            enabled: previewPanel.pv.loaded && previewPanel.pv.videoIndex < previewPanel.pv.videoCount - 1 && !previewPanel.pv.loading
                            onClicked: previewPanel.pv.step(1)
                            HoverTip { tip: "Load the next video in the list for preview." }
                        }
                        Caption {
                            text: "Video: " + (previewPanel.pv.videoCount > 0 ? previewPanel.pv.videoIndex + 1 : 0) + " / " + previewPanel.pv.videoCount
                            font.features: { "tnum": 1 }
                        }
                        AccentButton {
                            objectName: "playButton"
                            text: previewPanel.pv.playing && !previewPanel.pv.fastForward ? "❚❚" : "▶"
                            implicitWidth: 40; kind: "outline"
                            enabled: previewPanel.pv.loaded
                            onClicked: previewPanel.pv.togglePlay()
                            HoverTip { tip: "Play/Pause, at the clip's own frame rate or as fast as frames render, whichever "
                                            + "is slower -- every frame is shown. Shortcut: Spacebar" }
                        }
                        AccentButton {
                            text: previewPanel.pv.fastForward ? "❚❚" : "»"
                            implicitWidth: 40; kind: "outline"
                            enabled: previewPanel.pv.loaded
                            onClicked: previewPanel.pv.toggleFastForward(page.ffStep)
                            HoverTip { tip: "Fast Forward (step N frames). Shortcut: Shift+Spacebar (Spacebar pauses)" }
                        }
                        Dropdown {
                            boxWidth: 58
                            model: ["2", "3", "4", "5", "6", "7", "8", "9", "10"]
                            currentIndex: page.ffStep - 2
                            onActivated: (i) => page.ffStep = i + 2
                            HoverTip { tip: "Select the number of frames to skip during Fast Forward." }
                        }
                        AccentButton {
                            text: "⟲"; implicitWidth: 36
                            kind: page.st.loop_playback ? "tonal" : "ghost"
                            onClicked: page.st.loop_playback = !page.st.loop_playback
                            HoverTip { tip: "Loop playback. When reaching the end, wrap to the first frame and continue." }
                        }
                        // a plain slider, as Merging's (user, 2026-09-30): it only
                        // brightens the preview, nothing to be precise about
                        SpSlider {
                            objectName: "brightnessSlider"
                            key: "preview_brightness"; label: "Preview Brightness:"; labelWidth: 130
                            from: 0.25; to: 3.0; step: 0.05; decimals: 2; defaultValue: 1.0; doubleClickResets: true
                            help: page.help("preview_brightness")
                            Layout.fillWidth: true
                            Layout.minimumWidth: 170
                            Layout.maximumWidth: 360
                            Layout.alignment: Qt.AlignVCenter
                            Layout.leftMargin: 8
                        }
                        // Wigglegram only (user, 2026-10-09): how fast it switches eyes
                        SliderRow {
                            objectName: "wiggleSpeedSlider"
                            visible: String(page.st.preview_source) === "Wigglegram"
                            store: Shell; key: "wiggleSpeed"; label: "Wiggle Speed:"; labelWidth: 100
                            from: 2; to: 20; step: 0.5; decimals: 1; defaultValue: 7.5
                            help: "How fast the wigglegram switches between the left and right eye, in switches per second. Right-click resets it."
                            Layout.fillWidth: true
                            Layout.minimumWidth: 170
                            Layout.maximumWidth: 300
                            Layout.alignment: Qt.AlignVCenter
                            Layout.leftMargin: 8
                        }
                    }
                    // The picture
                    Rectangle {
                        id: stageArea
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        radius: 8
                        color: Theme.alpha(Theme.fg, 0.04)
                        clip: true

                        Flickable {
                            id: flick
                            anchors.fill: parent
                            contentWidth: Math.max(width, shown.width)
                            contentHeight: Math.max(height, shown.height)
                            boundsBehavior: Flickable.StopAtBounds
                            interactive: shown.width > width || shown.height > height

                            Item {
                                id: shown
                                // the video stays clean of the window's film grain
                                GrainHole { clipTo: stageArea }
                                // the flip card: a click turns the picture over (Front <-> Back)
                                transform: Rotation {
                                    origin.x: shown.width / 2; origin.y: shown.height / 2
                                    axis { x: 0; y: 1; z: 0 }
                                    angle: flipCard.angle
                                }
                                // On the picture, inside the Flickable (the Flickable takes a
                                // press on the area behind it); a drag still pans.
                                TapHandler {
                                    objectName: "flipTap"
                                    enabled: previewPanel.pv.loaded && !previewPanel.pv.wiggling && !Theme.modalOpen
                                    acceptedButtons: Qt.LeftButton
                                    gesturePolicy: TapHandler.DragThreshold
                                    // One click flips, a double-click zooms (user, 2026-09-27): the flip
                                    // waits out the double-click time, so a double-click never flips first.
                                    onTapped: (ev) => {
                                        if (flipDelay.running) { flipDelay.stop(); zoomAnchor.toggleAt(ev.position.x, ev.position.y) }
                                        else flipDelay.restart()
                                    }
                                }
                                Timer {
                                    id: flipDelay
                                    objectName: "flipDelay"
                                    interval: Math.min(Qt.styleHints.mouseDoubleClickInterval, 350)
                                    onTriggered: flipCard.flip()
                                }
                                width: previewPanel.pv.imageWidth * previewPanel.pv.displayScale * previewPanel.pxToItem
                                height: previewPanel.pv.imageHeight * previewPanel.pv.displayScale * previewPanel.pxToItem
                                x: Math.max(0, (flick.width - width) / 2)
                                y: Math.max(0, (flick.height - height) / 2)
                                visible: previewPanel.pv.imageWidth > 0

                                Image {
                                    id: frameImage
                                    objectName: "previewImage"
                                    anchors.fill: parent
                                    cache: false
                                    smooth: true
                                    mipmap: true
                                    visible: !previewPanel.pv.wiggling
                                    source: previewPanel.pv.imageWidth > 0 && !previewPanel.pv.wiggling
                                            ? "image://splatpreview/frame/" + previewPanel.pv.imageSerial : ""
                                }
                                // Wigglegram: v1 flips the eyes on a timer.
                                Image {
                                    anchors.fill: parent
                                    cache: false
                                    visible: previewPanel.pv.wiggling
                                    property bool leftEye: true
                                    source: previewPanel.pv.wiggling
                                            ? "image://splatpreview/" + (leftEye ? "left" : "right") + "/" + previewPanel.pv.imageSerial : ""
                                    Timer {
                                        objectName: "wiggleTimer"
                                        // not while a job runs: no redraws then (Theme.calm, bug sweep A10)
                                        running: previewPanel.pv.wiggling && page.active && !Theme.calm
                                        interval: Math.round(1000 / Shell.wiggleSpeed)
                                        repeat: true
                                        onTriggered: parent.leftEye = !parent.leftEye
                                    }
                                }
                            }
                        }
                        // the card turned over by hand: drag an edge across (user, 2026-10-10)
                        FlipDrag {
                            anchors.fill: flick
                            card: flipCard
                            pv: previewPanel.pv
                            picX: shown.x - flick.contentX
                            picY: shown.y - flick.contentY
                            picW: shown.width
                            picH: shown.height
                            // not while zoomed in (a drag pans then), nor on the wigglegram
                            allowed: previewPanel.pv.loaded && !previewPanel.pv.wiggling && !flick.interactive
                        }
                        // Glasses-Free 3D: the SR display weaves the visible picture (sr_display.py)
                        GlassesFreeView {
                            objectName: "glassesFreeView"
                            stage: "splatting"
                            pv: previewPanel.pv
                            picture: shown
                            area: stageArea
                            extraHoles: [fullscreenBtn]      // the full-screen icon shows over the 3D
                            // (not while its Full Preview is open: that window's picture is woven then)
                            wanted: page.active && String(page.st.preview_source) === "Glasses-Free 3D"
                                    && flipCard.angle === 0 && !flipCard.busy && !fullPreview.visible
                        }
                        // Full screen: an icon in the picture's top-right corner (user, 2026-10-15: in place of
                        // the Full Preview button)
                        FullscreenButton {
                            id: fullscreenBtn
                            objectName: "fullscreenButton"
                            anchors { top: parent.top; right: parent.right; margins: 8 }
                            z: 50
                            visible: previewPanel.pv.loaded
                            onClicked: page.openFullPreview()
                            tip: "Full screen (F11): the preview on the screen Studio is on, mirroring this one live -- for when the inline panel is too small to judge by. Escape, F11 or the X in the corner (it shows when you move the mouse) brings you back."
                        }
                        // ---- the flip card (MergingPage's): half a turn to edge-on,
                        // Preview Source swapped there, the new frame waited for (at most
                        // ~0.5 s), then the other half. One render.
                        QtObject {
                            id: flipCard
                            objectName: "flipCard"
                            property real angle: 0
                            property bool busy: false
                            property int serialAtSwap: -1
                            // Imperative, not bindings: each writes preview_source, whose
                            // change comes straight back (a binding loop otherwise).
                            property bool backUp: false
                            property string facing: ""
                            property bool applying: false
                            function sideSource(up) { return up ? Shell.splatFlipBack : Shell.splatFlipFront }
                            function show() {
                                facing = sideSource(backUp)
                                if (String(page.st.preview_source || "") !== facing) {
                                    applying = true
                                    page.st.preview_source = facing
                                    applying = false
                                }
                            }
                            // preview_source set elsewhere (Load Settings, a sidecar): the
                            // side holding it turns up, or the facing side takes it.
                            function adopt() {
                                const src = String(page.st.preview_source || "")
                                if (applying || src === "" || src === facing) return
                                if (src === Shell.splatFlipFront) backUp = false
                                else if (src === Shell.splatFlipBack) backUp = true
                                else if (backUp) Shell.splatFlipBack = src
                                else Shell.splatFlipFront = src
                                facing = src
                            }
                            Component.onCompleted: { adopt(); show(); Qt.callLater(() => { show(); live = true }) }
                            // A write from Python (Load Settings, a clip's sidecar) sends no
                            // valueChanged -- the property's own notify still arrives.
                            // Not during start-up: the card opens front up, as it always has.
                            readonly property string stSource: String(page.st.preview_source || "")
                            property bool live: false
                            onStSourceChanged: if (live) adopt()
                            function flip() {
                                if (busy || !previewPanel.pv.loaded || previewPanel.pv.wiggling) return
                                busy = true
                                splatFlipOut.start()
                            }
                            function swapped() {
                                serialAtSwap = previewPanel.pv.imageSerial
                                backUp = !backUp
                                show()
                                splatFlipWait.restart()
                            }
                            function land() {
                                splatFlipWait.stop()
                                angle = -90
                                splatFlipIn.start()
                            }
                            // FlipDrag: the other side up, mid-drag; fresh() once its frame is here
                            function turnOver() {
                                serialAtSwap = previewPanel.pv.imageSerial
                                backUp = !backUp
                                show()
                            }
                            function fresh() { return previewPanel.pv.imageSerial !== serialAtSwap }
                        }
                        Connections {
                            target: Shell
                            function onChanged() { flipCard.show() }        // a side's choice
                        }
                        Connections {
                            target: page.st
                            function onValueChanged(key, value) { if (key === "preview_source") flipCard.adopt() }
                        }
                        NumberAnimation {
                            id: splatFlipOut
                            target: flipCard; property: "angle"; from: 0; to: 90
                            duration: Theme.dur(170); easing.type: Easing.InCubic
                            onFinished: flipCard.swapped()
                        }
                        Timer { id: splatFlipWait; interval: 500; onTriggered: flipCard.land() }
                        Connections {
                            target: previewPanel.pv
                            function onFrameChanged() {          // imageSerial's notify
                                if (splatFlipWait.running && previewPanel.pv.imageSerial !== flipCard.serialAtSwap) flipCard.land()
                            }
                        }
                        NumberAnimation {
                            id: splatFlipIn
                            target: flipCard; property: "angle"; from: -90; to: 0
                            duration: Theme.dur(230); easing.type: Easing.OutCubic
                            onFinished: flipCard.busy = false
                        }
                        // v1 preview auto-fit: the largest Preview Scale the room allows.
                        PreviewAutoFit {
                            id: previewFit
                            objectName: "previewAutoFit"
                            area: stageArea; pv: previewPanel.pv; store: page.st
                            pxToItem: previewPanel.pxToItem
                            maxPercent: 250
                            active: page.previewFitOn
                        }
                        // The wheel ZOOMS the preview (user, 2026-09-27), never scrolls it: the
                        // handler sits on the Flickable itself, whose handlers see the wheel before
                        // the Flickable does -- so a zoomed-in picture no longer scrolls under the
                        // wheel. Each notch is ~12%, about the point under the pointer; a drag pans.
                        WheelHandler {
                            objectName: "previewZoomWheel"
                            parent: flick
                            enabled: previewPanel.pv.loaded && !Theme.modalOpen
                            acceptedModifiers: Qt.NoModifier
                            onWheel: (e) => {
                                const cur = parseFloat(String(page.st.preview_size)) || 100
                                const step = e.angleDelta.y > 0 ? 1.12 : 1 / 1.12
                                const next = Math.max(10, Math.min(250, Math.round(cur * step)))
                                if (next === cur) return
                                // keep the frame point under the pointer where it is (applied once
                                // the picture has its new size -- see zoomAnchor below)
                                zoomAnchor.fx = shown.width > 0 ? (flick.contentX + e.x - shown.x) / shown.width : 0.5
                                zoomAnchor.fy = shown.height > 0 ? (flick.contentY + e.y - shown.y) / shown.height : 0.5
                                zoomAnchor.vx = e.x; zoomAnchor.vy = e.y
                                zoomAnchor.at = Date.now()
                                page.previewFitOn = false
                                page.st.preview_size = next + "%"
                            }
                        }
                        QtObject {
                            id: zoomAnchor
                            property real fx: 0.5
                            property real fy: 0.5
                            property real vx: 0
                            property real vy: 0
                            property double at: 0
                            function apply() {
                                if (Date.now() - at > 2000) return
                                flick.contentX = Math.max(0, Math.min(flick.contentWidth - flick.width, shown.x + fx * shown.width - vx))
                                flick.contentY = Math.max(0, Math.min(flick.contentHeight - flick.height, shown.y + fy * shown.height - vy))
                            }
                            // Double-click (user, 2026-09-27, in place of a Fit button): zoomed in
                            // past the whole frame -> back to the whole frame; otherwise 50% closer
                            // about the point clicked. px/py are in the picture's own coordinates.
                            function toggleAt(px, py) {
                                const cur = parseFloat(String(page.st.preview_size)) || 100
                                const fit = previewFit.pv ? previewFit.percent() : -1
                                if (fit > 0 && Math.round(cur) > fit) {
                                    at = 0; page.previewFitOn = true; previewFit.apply(); flick.contentX = 0; flick.contentY = 0
                                    return
                                }
                                const next = Math.max(10, Math.min(250, Math.round(cur * 1.5)))
                                if (next === cur) return
                                fx = shown.width > 0 ? px / shown.width : 0.5
                                fy = shown.height > 0 ? py / shown.height : 0.5
                                vx = shown.x + px - flick.contentX; vy = shown.y + py - flick.contentY
                                at = Date.now()
                                page.previewFitOn = false
                                page.st.preview_size = next + "%"
                            }
                        }
                        Connections {
                            target: shown
                            function onWidthChanged() { Qt.callLater(zoomAnchor.apply) }
                            function onHeightChanged() { Qt.callLater(zoomAnchor.apply) }
                        }
                        Column {
                            anchors.centerIn: parent
                            spacing: 10
                            visible: !previewPanel.pv.loaded || previewPanel.pv.loading
                            Spinner {
                                anchors.horizontalCenter: parent.horizontalCenter
                                visible: previewPanel.pv.loading
                                running: visible
                            }
                            Text {
                                anchors.horizontalCenter: parent.horizontalCenter
                                objectName: "previewHint"
                                text: previewPanel.pv.loading ? "Loading preview…"
                                      : "Pick a clip in Upcoming Jobs to preview it -- or click here for the first one."
                                // no Load Preview button any more (user, 2026-09-27): the
                                // clip list loads what is picked; this covers a list with
                                // no room to show
                                TapHandler {
                                    enabled: !previewPanel.pv.loading && !previewPanel.pv.loaded && !Theme.modalOpen
                                    onTapped: previewPanel.pv.load()
                                }
                                HoverHandler { cursorShape: previewPanel.pv.loading ? Qt.ArrowCursor : Qt.PointingHandCursor }
                                color: Theme.fgMuted
                                font.family: Theme.bodyFont
                                font.pixelSize: Theme.bodySize
                            }
                        }
                    }
                }
            }

            // -- Depth Map Pre-processing, Stereo Projection
            // Wide: side by side under the preview (v1 _apply_depth_band_layout).
            GridLayout {
                id: band
                enabled: page.idle
                x: page.wide ? previewPanel.x : leftCol.width + Theme.gap
                y: page.wide ? body.height - height : page.compactPreviewHeight + Theme.gap
                width: page.wide ? previewPanel.width : (body.width - 2 * Theme.gap) * 6 / 17
                columns: page.wide ? 2 : 1
                columnSpacing: Theme.gap
                rowSpacing: Theme.gap

                Section {
                    title: page.bn["splat.edge_alignment"] || "Depth Map Pre-processing"
                    Layout.alignment: Qt.AlignTop
                    Layout.preferredWidth: 1
                    GridLayout {
                        objectName: "preprocessDials"
                        Layout.fillWidth: true
                        columns: 7
                        columnSpacing: 0
                        rowSpacing: 6
                        SpDial { key: "depth_dilate_size_x"; label: page.bn["splat.expand_x"] || "Dilate X:"; from: -10; to: 30; step: 0.5; decimals: 1; defaultValue: 0; fillFrom: 0; help: page.help("depth_dilate_size_x") }
                        SpDial { key: "depth_dilate_size_y"; label: (page.bn["splat.expand_x"] || "Dilate X:").replace(/X:?\s*$/, "Y"); from: -10; to: 30; step: 0.5; decimals: 1; defaultValue: 0; fillFrom: 0; help: page.help("depth_dilate_size_y") }
                        SpDial { key: "depth_blur_size_x"; label: "Blur X:"; from: 0; to: 35; step: 1; decimals: 0; defaultValue: 0; help: page.help("depth_blur_size_x") }
                        SpDial { key: "depth_blur_size_y"; label: "Blur Y:"; from: 0; to: 35; step: 1; decimals: 0; defaultValue: 0; help: page.help("depth_blur_size_y") }
                        SpDial { key: "depth_dilate_left"; label: page.bn["splat.expand_left"] || "Dilate Left:"; from: 0; to: 20; step: 0.5; decimals: 1; defaultValue: 0; help: page.help("depth_dilate_left") }
                        SpDial { key: "depth_blur_left"; label: "Blur Left:"; from: 0; to: 20; step: 1; decimals: 0; defaultValue: 0; help: page.help("depth_blur_left") }
                        // how Blur Left mixes in, beside its dial
                        ColumnLayout {
                            Layout.alignment: Qt.AlignVCenter | Qt.AlignHCenter
                            spacing: 2
                            Caption { text: "Blur Left Mix"; font.pixelSize: Theme.smallSize; color: Theme.fgMuted; Layout.alignment: Qt.AlignHCenter }
                            SpDrop { key: "depth_blur_left_mix"; model: page.sp.choices.depth_blur_left_mix; boxWidth: 78; HoverTip { tip: page.help("depth_blur_left") } }
                        }
                    }
                }

                Section {
                    id: stereoSection
                    title: "Stereo Projection"
                    Layout.alignment: Qt.AlignTop
                    Layout.preferredWidth: 1
                    RowLayout {
                        objectName: "stereoDials"
                        Layout.fillWidth: true
                        spacing: 0
                        // Disparity, Gamma, Convergence, then the estimate (user, 2026-09-27)
                        SpDial { key: "max_disp"; label: page.bn["splat.depth_strength"] || "Disparity:"; from: 0; to: 100; step: 1; decimals: 0; defaultValue: 30; help: page.help("max_disp") }
                        SpDial { key: "depth_gamma"; label: "Gamma:"; from: 0.1; to: 3.0; step: 0.05; decimals: 2; defaultValue: 1.0; fillFrom: 1.0; help: page.help("depth_gamma") }
                        SpDial { key: "zero_disparity_anchor"; label: "Convergence:"; from: 0; to: 1; step: 0.01; decimals: 2; defaultValue: 0.5; fillFrom: 0.5; help: page.help("convergence_point") }
                        // one dial for more pop in front of the screen, the rest
                        // of the scene left where it is (user, 2026-10-01)
                        SpDial { objectName: "popOutDial"; key: "pop_amount"; label: "Pop-Out:"; from: 0; to: 60; step: 0.5; decimals: 1; defaultValue: 0; help: page.help("pop_amount") }
                        AccentButton {
                            text: "≈"; implicitWidth: 30; implicitHeight: 28; kind: "outline"
                            Layout.alignment: Qt.AlignTop
                            Layout.topMargin: 9
                            enabled: page.pv.loaded
                            onClicked: page.pv.estimateDepthPop()
                            HoverTip { tip: page.help("estimate_dp_total") }
                        }
                        SpDial { visible: !page.bh["splat.border_width_bias"] && Shell.splatBorderSettings; key: "border_width"; label: "Border Width:"; from: 0; to: 5; step: 0.01; decimals: 2; defaultValue: 0; help: page.help("border_width") }
                        SpDial { visible: !page.bh["splat.border_width_bias"] && Shell.splatBorderSettings; key: "border_bias"; label: "Border Bias:"; from: -1; to: 1; step: 0.01; decimals: 2; defaultValue: 0; fillFrom: 0; help: page.help("border_bias") }
                    }
                    // the region tools, centred. Edit Depth Map took Vertical Warp
                    // Fix's place (user, 2026-09-28): the Depth Map Editor fixes the
                    // warp in the map itself, which the zones only patched over.
                    ColumnLayout {
                        id: editorRow
                        visible: !page.bh["splat.vwf_hole_shift"]
                        Layout.alignment: Qt.AlignHCenter
                        spacing: 2
                        RowLayout {
                            Layout.alignment: Qt.AlignHCenter
                            spacing: 10
                            RowLayout {
                                id: editorButtons
                                spacing: 10
                                // this clip's depth map, straight into the Depth Map Editor;
                                // its render offers the way back
                                AccentButton {
                                    objectName: "editDepthMap"
                                    text: "Edit Depth Map"      // filled, as Start (user, 2026-09-27)
                                    enabled: page.canEditDepth
                                    onClicked: page.editDepthMap()
                                    HoverTip { tip: "Open this clip and its depth map in the Depth Map Editor (Extra tab): select "
                                                    + "an object with SAM 3 and fix its depth -- vertical warp included: pick the "
                                                    + "warped object and pull just its top or bottom edge back into line (Top edge / "
                                                    + "Bottom edge). When the render is done it offers to put the edited map in place "
                                                    + "and bring you back here on the same clip.\n\nShortcut: Ctrl+M" }
                                }
                                AccentButton {
                                    text: "Hole Shift"
                                    onClicked: holeShiftWin.openFor()
                                    HoverTip { tip: "Move individual disocclusion holes along X or Y without changing their shape.\nOpens a window showing the holes on the current frame; click one to select it." }
                                }
                                AccentButton {
                                    text: "Detect Text"
                                    onClicked: subMaskWin.openFor()
                                    HoverTip { tip: page.help("subs_button") + "\n\nShortcut: Ctrl+T" }
                                }
                            }
                        }
                        // the Detect Text mask's own grow / soften (user, 2026-10-02),
                        // apart from the depth map's Dilate / Blur -- only once the
                        // clip has a saved mask
                        RowLayout {
                            objectName: "textMaskDials"
                            visible: !!page.pv && page.pv.hasTextMask
                            Layout.alignment: Qt.AlignHCenter
                            spacing: 0
                            SpDial { objectName: "textGrowDial"; Layout.fillWidth: false; Layout.preferredWidth: 84; key: "submask_grow"; label: "Text Grow:"; from: -10; to: 30; step: 1; decimals: 0; defaultValue: 0; fillFrom: 0; help: page.help("submask_grow") }
                            SpDial { objectName: "textSoftenDial"; Layout.fillWidth: false; Layout.preferredWidth: 84; key: "submask_soften"; label: "Text Soften:"; from: 0; to: 20; step: 0.5; decimals: 1; defaultValue: 0; help: page.help("submask_soften") }
                            SpDial { objectName: "textCleanDial"; Layout.fillWidth: false; Layout.preferredWidth: 84; key: "submask_clean"; label: "Text Clean:"; from: 0; to: 40; step: 1; decimals: 0; defaultValue: 0; help: page.help("submask_clean") }
                            // the text's own Convergence (user, 2026-10-11): as Rain's, an offset from the scene's
                            SpDial { objectName: "textConvDial"; Layout.fillWidth: false; Layout.preferredWidth: 84; key: "submask_conv"; label: "Text Conv:"; from: -1; to: 1; step: 0.01; decimals: 2; defaultValue: 0; fillFrom: 0; help: page.help("submask_conv") }
                        }
                    }
                    // Centred (user, 2026-09-27): one row when it fits, else two, each
                    // centred -- the first three, then Dual Eye and Border Settings.
                    GridLayout {
                        id: stereoChecks
                        objectName: "stereoChecks"
                        Layout.alignment: Qt.AlignHCenter
                        // the box's inner width (Section: 14 each side)
                        readonly property bool oneRow: checksA.implicitWidth + columnSpacing + checksB.implicitWidth
                                                       <= stereoSection.width - 28
                        columns: oneRow ? 2 : 1
                        columnSpacing: 6
                        rowSpacing: 4
                      RowLayout {
                        id: checksA
                        objectName: "stereoChecksA"
                        Layout.alignment: Qt.AlignHCenter | Qt.AlignTop
                        spacing: 6          // 12 overflowed by 11 px in Noto Sans Nandinagari
                        SpCheck { key: "enable_global_norm"; visible: !page.bh["splat.global_norm"]; text: "Enable Global Normalization"; help: page.help("enable_global_normalization"); Layout.alignment: Qt.AlignTop }
                        SpCheck { key: "move_to_finished"; text: "Resume"; help: page.help("move_to_finished_folder"); Layout.alignment: Qt.AlignTop }
                        SpCheck { key: "flip_horizontal"; visible: !page.bh["splat.flip"]; text: "Flip"; help: page.help("flip_horizontal"); Layout.alignment: Qt.AlignTop }
                      }
                      RowLayout {
                        id: checksB
                        objectName: "stereoChecksB"
                        Layout.alignment: Qt.AlignHCenter | Qt.AlignTop
                        spacing: 6
                        ColumnLayout {
                            Layout.alignment: Qt.AlignTop
                            spacing: 2
                            SpCheck { key: "dual_eye"; text: "Dual Eye"; help: page.help("dual_eye") }
                            RowLayout {
                                visible: !!page.st.dual_eye
                                Caption { text: "Bias:" }
                                SpBox { key: "dual_eye_bias"; boxWidth: 56 }
                            }
                        }
                        // shows / hides the Border Width and Border Bias knobs (user,
                        // 2026-09-27); hidden, their values still apply
                        AnimCheck {
                            objectName: "borderSettingsCheck"
                            visible: !page.bh["splat.border_width_bias"]
                            text: "Border Settings"
                            checked: Shell.splatBorderSettings
                            onToggled: { Shell.splatBorderSettings = checked; checked = Qt.binding(() => Shell.splatBorderSettings) }
                            HoverTip { tip: "Show the Border Width and Border Bias knobs. Hidden, whatever they are set to still applies." }
                            Layout.alignment: Qt.AlignTop
                        }
                      }
                    }
                }
            }

            // -- right: Special FX
            Section {
                id: fxCol
                title: "Special FX"
                enabled: page.idle
                x: body.width - width
                y: page.wide ? 0 : page.compactPreviewHeight + Theme.gap
                width: page.wide ? page.rightRail : (body.width - 2 * Theme.gap) * 4 / 17

                FoldSection {
                    // Closing only hides it now (user, 2026-09-27: hide it to see the clip
                    // list while the effect stays on); the heading says so while it is on.
                    title: "Pop Out Boost" + (!page.popOpen && Number(page.st.boost_amount) > 0 ? "  •  on" : "")
                    centred: true; chevron: true
                    open: page.popOpen
                    help: "Use the Pop-Out dial in Stereo Projection first, for a clean enhancement of foreground "
                          + "elements. Pop Out Boost is meant to work in conjunction with the Stereo Projection dial to "
                          + "create \"right in front of your face\" pop when the dial alone isn't enough. Boost has a "
                          + "higher risk of warping the edges of objects. Closing this section only hides it -- whatever "
                          + "it is set to still applies (the heading shows \"on\"). Set Strength to 0 to turn it off."
                    onToggled: (on) => page.popOpen = on
                    GridLayout {
                        objectName: "boostDials"
                        Layout.fillWidth: true
                        columns: 3
                        columnSpacing: 0
                        rowSpacing: 6
                        SpDial { key: "boost_amount"; label: "Strength:"; from: 0; to: 60; step: 0.5; decimals: 1; defaultValue: 0; help: page.help("depth_boost_amount") }
                        SpDial { key: "boost_thresh"; label: "Threshold:"; from: 0.5; to: 1.0; step: 0.01; decimals: 2; defaultValue: 0.85; help: page.help("depth_boost_thresh") }
                        SpDial { key: "boost_curve"; label: "Curve:"; from: 0.5; to: 6.0; step: 0.1; decimals: 1; defaultValue: 2.0; help: page.help("depth_boost_curve") }
                        // no Edge Clean / Edge Sens (user, 2026-10-11: no difference in testing);
                        // the pre-filter is held off (settings.RETIRED, batch_processing)
                    }
                }
                // Rain (user, 2026-10-06): the rain kept apart from the scene -- the scene warped
                // without it, the rain warped on its own in front of the scene through the same
                // controls (Disparity, Convergence, Boost and Pop-Out move it as they move the scene)
                FoldSection {
                    objectName: "rainSection"
                    visible: !page.bh["splat.particles"]     // Beginner Mode: hidden (user, 2026-10-08)
                    // "Rain", not "Particles" (user, 2026-10-10: it is Rain only in the GUI)
                    title: "Rain" + (!page.rainOpen && page.rainOn ? "  •  " + (page.webOn ? "web" : page.sparksOn ? "sparks" : "on") : "")
                    centred: true; chevron: true
                    open: page.rainOpen
                    help: "Keeps falling rain apart from the scene. DepthCrafter struggles to detect rain, so in "
                          + "a plain splat the rain follows the shape of whatever object it falls in front of. With Rain on, each frame's rain is "
                          + "separated (using the frames around it), the scene is warped without it, and the rain is "
                          + "warped on its own in front of the scene -- through the same Disparity, Convergence, "
                          + "Pop Out Boost and Pop-Out, so the nearest rain comes out the way the nearest objects "
                          + "do. The left eye keeps the original frame. Saved with the clip."
                    onToggled: (on) => page.rainOpen = on
                    RowLayout {
                        objectName: "rainModeRow"
                        Layout.fillWidth: true
                        spacing: 8
                        // On/off is a switch (user, 2026-10-07: "like beginner mode"); the dropdown holds
                        // only the methods. The clip still saves one rain_mode (off | moving | static);
                        // rain_method remembers the method while it is off. One row: a row of its own
                        // would make the window taller.
                        readonly property var modes: [{ key: "moving", label: "Moving" },
                                                      { key: "static", label: "Static" }]
                        // Rain only (user, 2026-10-10: DepthCrafter picks up most particles itself --
                        // rain is the one it sees through; Sparks and Web stay in the code, off the page).
                        // A clip already set to another keeps it, and can be switched back to Rain here.
                        readonly property var particles: [{ key: "rain", label: "Rain" }].concat(
                            page.sparksOn ? [{ key: "sparks", label: "Sparks" }]
                            : page.webOn ? [{ key: "web", label: "Web" }] : [])   // not on a release install (sweep 2026-10-14c)
                        readonly property string method: page.rainOn ? page.st.rain_mode : (page.st.rain_method || "moving")
                        Toggle {
                            id: rainSwitch
                            objectName: "rainSwitch"
                            Layout.alignment: Qt.AlignVCenter
                            checked: page.rainOn
                            onToggled: {
                                if (!checked && page.rainOn)          // keep the clip's method for switching back on
                                    page.st.rain_method = page.st.rain_mode
                                page.st.rain_mode = checked ? (page.st.rain_method || "moving") : "off"
                                checked = Qt.binding(() => page.rainOn)
                            }
                            HoverTip { tip: "Rain on or off for this clip: on, the rain is kept apart from the "
                                            + "scene and put back at depths of its own; off, it stays painted on the scene." }
                        }
                        Dropdown {
                            id: rainParticle
                            objectName: "rainParticle"
                            visible: parent.particles.length > 1     // Rain alone: nothing to pick
                            boxWidth: 100
                            model: parent.particles.map(m => m.label)
                            currentIndex: Math.max(0, parent.particles.findIndex(m => m.key === (page.st.rain_particle || "rain")))
                            onActivated: (i) => page.st.rain_particle = rainParticle.parent.particles[i].key
                            HoverTip { tip: "This clip's particle, from before this section became Rain only. Pick Rain to "
                                            + "switch it over." }
                        }
                        Dropdown {
                            id: rainMode
                            objectName: "rainMode"
                            visible: !page.webOn            // the web is found once for a locked-off shot
                            boxWidth: 100
                            model: parent.modes.map(m => m.label)
                            currentIndex: Math.max(0, parent.modes.findIndex(m => m.key === parent.method))
                            onActivated: (i) => {
                                const key = rainMode.parent.modes[i].key
                                page.st.rain_method = key
                                if (page.rainOn)
                                    page.st.rain_mode = key
                            }
                            HoverTip { tip: "The camera. Moving: the neighbouring frames are lined up on each frame before the "
                                            + "particles are separated (any shot, camera or people moving). Static: a "
                                            + "locked-off shot -- simpler and a little cleaner." }
                        }
                        // the layers' own spacing and convergence (user, 2026-10-08: "distance between layers
                        // and such", "convergence for particles") in a popup: a row of their own made the
                        // window's minimum height 67 px taller (1382 against 1315)
                        AccentButton {
                            id: layerBtn
                            objectName: "rainLayerBtn"
                            text: "≡"; kind: "ghost"; implicitWidth: 30
                            Layout.alignment: Qt.AlignVCenter
                            enabled: page.rainOn
                            onClicked: layerPop.opened ? layerPop.close() : layerPop.open()
                            HoverTip { tip: "Layer controls: Spread (the distance between the layers), Spacing (how they share "
                                            + "it) and the particles' own Convergence." }
                            Popup {
                                id: layerPop
                                objectName: "rainLayerPopup"
                                y: layerBtn.height + 4
                                x: Math.min(0, layerBtn.width - width)
                                padding: 12
                                transformOrigin: Popup.Top
                                enter: Transition {
                                    ParallelAnimation {
                                        NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.dur(140) }
                                        NumberAnimation { property: "scale"; from: 0.9; to: 1; duration: Theme.dur(240); easing.type: Easing.OutCubic }
                                    }
                                }
                                exit: Transition { NumberAnimation { property: "opacity"; to: 0; duration: Theme.dur(100) } }
                                background: Rectangle {
                                    PopupGrain { radius: 10 }
                                    radius: 10
                                    color: Theme.field
                                    border.width: 1
                                    border.color: Theme.borderStrong
                                }
                                contentItem: RowLayout {
                                    objectName: "rainLayerDials"
                                    spacing: 0
                                    SpDial { objectName: "rainSpreadDial"; visible: !page.webOn; key: "rain_spread"; label: "Spread:"; from: 0; to: 2; step: 0.05; decimals: 2; defaultValue: 1.0
                                             help: "The distance between the layers: 1 as before; lower draws them together toward the "
                                                   + "nearest plane (0 puts them all on it), higher spreads them further back (2 = twice as "
                                                   + "far). In place: how far the layers lift off the surface." }
                                    SpDial { objectName: "rainSpacingDial"; visible: !page.webOn; key: "rain_spacing"; label: "Spacing:"; from: 0.25; to: 4; step: 0.05; decimals: 2; defaultValue: 1.0
                                             help: "How the layers share that distance: 1 even; higher crowds them toward the nearest "
                                                   + "plane (more particles close to you), lower toward the farthest." }
                                    SpDial { objectName: "rainConvDial"; key: "rain_conv"; label: "Convergence:"; from: -1; to: 1; step: 0.01; decimals: 2; defaultValue: 0
                                             fillFrom: 0
                                             help: "The particles' own Convergence, from the scene's: 0 the scene's; + moves their screen "
                                                   + "plane back, so they sit further behind it and come out less; - brings them out toward "
                                                   + "you. The scene stays where it is." }
                                }
                            }
                        }
                        Item { Layout.fillWidth: true }
                    }
                    RowLayout {
                        objectName: "rainPresetRow"
                        visible: !page.webOn                // no scenario or placement for the web
                        Layout.fillWidth: true
                        spacing: 8
                        enabled: page.rainOn
                        readonly property var presets: [{ key: "downpour", label: "Downpour" },
                                                        { key: "soft", label: "Soft (out of focus)" },
                                                        { key: "mist", label: "Mist (dense fine)" }]
                        readonly property var placements: [{ key: "front", label: "In front" },
                                                           { key: "place", label: "In place" }]
                        // the captions only when wide: at 1460 the row (381 px) overran its section (302)
                        Caption { text: "Scenario:"; visible: !page.sparksOn && page.wide }   // rain only
                        Dropdown {
                            id: rainPreset
                            objectName: "rainPreset"
                            visible: !page.sparksOn
                            boxWidth: 150
                            model: parent.presets.map(m => m.label)
                            currentIndex: Math.max(0, parent.presets.findIndex(m => m.key === (page.st.rain_preset || "downpour")))
                            onActivated: (i) => page.st.rain_preset = rainPreset.parent.presets[i].key
                            HoverTip { tip: "Downpour: sharp streaks -- a storm, a lit downpour. Soft: wide, faint, out-of-focus "
                                            + "rain -- close-ups, rain behind a shallow focus. Mist: a dense sheet of fine rain, as in "
                                            + "a backlit haze, where the rain is most of what changes from frame to frame. Soft and "
                                            + "Mist work best with Moving camera; pair Mist with Guard when people stand in it." }
                        }
                        // user, 2026-10-07: "the bright sparks behind the goblin bomb appear in front of it"
                        Caption { text: "Place:"; visible: page.wide || page.sparksOn }
                        Dropdown {
                            id: rainPlacement
                            objectName: "rainPlacement"
                            boxWidth: 100
                            model: parent.placements.map(m => m.label)
                            currentIndex: Math.max(0, parent.placements.findIndex(m => m.key === (page.st.rain_placement || "front")))
                            onActivated: (i) => page.st.rain_placement = rainPlacement.parent.placements[i].key
                            HoverTip { tip: "Where the particles go in depth. In front: on planes from the scene's middle toward "
                                            + "you, the same all over the frame -- rain in the air between you and the scene. In "
                                            + "place: each one at the scene's own depth where it is, lifted a little toward you "
                                            + "by its layer and the Depth dial -- sparks off something in the middle distance stay "
                                            + "with it instead of floating in front of it." }
                        }
                        Item { Layout.fillWidth: true }
                    }
                    // the two checks stacked, Guard beside them: a row of its own for Guard made the
                    // window's minimum height 56 px taller (1371 against 1315)
                    RowLayout {
                        objectName: "rainChecksGuard"
                        Layout.alignment: Qt.AlignHCenter
                        spacing: 10
                        ColumnLayout {
                            spacing: 4
                            SpCheck {
                                objectName: "rainAnyDirection"
                                Layout.alignment: Qt.AlignLeft
                                key: "rain_any_direction"; text: "Any direction"
                                visible: !page.sparksOn && !page.webOn     // rain only
                                enabled: page.rainOn
                                help: "Rain that doesn't fall one way: looking up into it, or rain coming at the lens. Each "
                                      + "streak is tested in every direction (slower)."
                            }
                            SpCheck {
                                objectName: "rainStrands"
                                Layout.alignment: Qt.AlignLeft
                                key: "rain_strands"; text: "Keep wires & lines out"   // user, 2026-10-10: not "webs" (too specific)
                                visible: !page.sparksOn && !page.webOn     // rain only
                                enabled: page.rainOn
                                help: "Long, thin, bright things that run on through the frame -- wires, cables, ropes, a kerb's "
                                      + "edge -- stay with the scene and its depth instead of being taken for rain."
                            }
                            // user, 2026-10-10: ch.28's lamps taken "even with guard at 1.0" (Guard is for near things)
                            SpCheck {
                                objectName: "rainLights"
                                Layout.alignment: Qt.AlignLeft
                                key: "rain_lights"; text: "Keep lights out"
                                visible: !page.sparksOn && !page.webOn     // rain only
                                enabled: page.rainOn
                                help: "Lamps, lit windows and signs stay with the scene. When the camera moves, a light lands a "
                                      + "little off in the frames around it, and what is left over can be taken for a drop. "
                                      + "Takes no rain on or right around a light."
                            }
                            // user, 2026-10-08: the Goblin's mask "being picked up too much no matter what i try"
                            SpCheck {
                                objectName: "sparksReflections"
                                Layout.alignment: Qt.AlignLeft
                                key: "rain_reflections"; text: "Keep reflections out"
                                visible: page.sparksOn      // sparks only
                                enabled: page.rainOn
                                help: "Fire flickering in something shiny -- eye lenses, a helmet, metal -- is bright and moves "
                                      + "from frame to frame like a spark, but two and three frames on it is still about where it "
                                      + "was; a spark is long gone. On, those stay with the scene. Leave it off when slow floating "
                                      + "embers matter: they stay about where they were too (reads 3 frames either side; slower)."
                            }
                        }
                        SpDial { objectName: "rainGuardDial"; visible: !page.webOn; key: "rain_guard"; label: "Guard:"; from: 0; to: 1; step: 0.05; decimals: 2; defaultValue: 0
                                 enabled: page.rainOn
                                 help: "Foreground guard: take no rain where the scene is near -- people standing in the rain. "
                                       + "What reads as rain on them is mostly their own moving texture (a jacket's reflections); "
                                       + "it stays with them at their depth. 0 = off; higher keeps more of the near part out." }
                        // user, 2026-10-07: "a dilate/erode slider for the particle cutouts"
                        SpDial { objectName: "rainGrowDial"; key: "rain_grow"; label: "Grow:"; from: -10; to: 10; step: 1; decimals: 0; defaultValue: 0
                                 enabled: page.rainOn
                                 help: "Grow (+) or shrink (-) the particle cutouts, in pixels at 1080p. Grown, they take in "
                                       + "their glow and tips -- never more than the frame's own light above the scene there, so "
                                       + "no dark edges are cut into the scene; shrunk, their edges stay painted on the scene. 0 = as found." }
                    }
                    RowLayout {
                        objectName: "rainDials"
                        Layout.alignment: Qt.AlignHCenter
                        spacing: 0
                        enabled: page.rainOn
                        SpDial { objectName: "rainDepthDial"; key: "rain_depth"; label: "Depth:"; from: 0; to: 1; step: 0.05; decimals: 2; defaultValue: 0.5
                                 help: "How strongly the particles are placed at depths of their own: 0 leaves each one at the "
                                       + "scene's depth right where it is -- as if it were painted on, no effect; 1 is the full placement "
                                       + "(In front: the nearest a whole scene's depth in front of its nearest part; In place: lifted "
                                       + "off the surface behind it by its layer). Disparity, Convergence, Pop Out Boost and Pop-Out then "
                                       + "act on it as on the scene." }
                        SpDial { objectName: "rainAmountDial"; key: "rain_amount"; label: "Amount:"; from: 0; to: 2; step: 0.05; decimals: 2; defaultValue: 1.0
                                 help: "How strongly the separated rain goes back in: 1 as it was, less to thin it, more to bring it out." }
                        SpDial { objectName: "rainLayersDial"; visible: !page.webOn; key: "rain_layers"; label: "Layers:"; from: 1; to: 10; step: 1; decimals: 0; defaultValue: 3
                                 help: "How many depth planes the rain is spread over, from mid-scene up to how far Depth brings it. "
                                       + "1 puts all the rain on one plane; more makes a deeper, more continuous curtain. The streaks "
                                       + "are shared out by length -- the longest (nearest) drops on the nearest plane." }
                        SpDial { objectName: "rainPopDial"; key: "rain_pop"; label: "Pop:"; from: 0; to: 30; step: 0.5; decimals: 1; defaultValue: 0
                                 help: "Extra Pop-Out for the rain alone, on top of the scene's, in the same units: the nearest "
                                       + "rain gets all of it, the farther layers less, the scene none. 0 = off." }
                    }
                }
                FoldSection {
                    title: "Keyframe"; centred: true; chevron: true
                    open: page.keyOpen
                    help: page.help("keyframe_enabled")
                    onToggled: (on) => page.keyOpen = on
                    AccentButton {
                        text: "Add Static Keyframe"; kind: "outline"; Layout.fillWidth: true
                        enabled: page.pv.loaded
                        onClicked: page.pv.addKeyframe("static")
                        HoverTip { tip: (page.help("keyframe_add_static") || "Pin every keyframeable slider's current value to this frame; held until the next keyframe.") + "\n\nShortcut: S" }
                    }
                    AccentButton {
                        text: "Add Dynamic Keyframe"; kind: "outline"; Layout.fillWidth: true
                        enabled: page.pv.loaded
                        onClicked: page.pv.addKeyframe("dynamic")
                        HoverTip { tip: (page.help("keyframe_add_dynamic") || "Pin the current values to this frame; they blend toward the next keyframe.") + "\n\nShortcut: D" }
                    }
                    AccentButton {
                        text: "Clear Current"; kind: "outline"; Layout.fillWidth: true
                        enabled: page.pv.loaded && page.kfAtFrame !== ""
                        onClicked: page.pv.clearKeyframe()
                        HoverTip { tip: page.help("keyframe_clear_current") || "Remove the keyframe at the frame on screen." }
                    }
                    AccentButton {
                        text: "Clear All"; kind: "outline"; Layout.fillWidth: true
                        enabled: page.pv.loaded && page.pv.keyframes.length > 0
                        onClicked: page.confirm("Clear All Keyframes", "Remove every keyframe for this clip? This cannot be undone.",
                                                "Clear All", () => page.pv.clearAllKeyframes())
                        HoverTip { tip: page.help("keyframe_clear_all") || "Remove every keyframe for this clip." }
                    }
                    Caption {
                        // v1 _kf_refresh_summary
                        text: {
                            const k = page.pv.keyframes
                            if (!k.length) return "No keyframes"
                            const s = k.filter(x => x.kind === "static").length
                            return s + " static, " + (k.length - s) + " dynamic"
                        }
                    }
                    // a dynamic keyframe's curve: ease / LFO / custom points (user, 2026-10-06)
                    KeyShaper {
                        pv: page.pv
                        visible: page.pv.loaded && page.pv.keyframes.length > 1
                        Layout.fillWidth: true
                    }
                }
                FoldSection {
                    title: "Frame Break" + (!page.breakOpen && Number(page.st.frame_break_bars) > 0 ? "  •  on" : "")
                    centred: true; chevron: true
                    open: page.breakOpen
                    help: "Frame Break adds letterbox bars that near objects can break out of. It is meant for one "
                          + "scene at a time. Closing this section only hides it -- whatever it is set to still applies "
                          + "(the heading shows \"on\"). Set Bar Size to 0 to turn it off."
                    onToggled: (on) => page.breakOpen = on
                    // v1 _fb_refresh_presets: one button per ratio wider than this
                    // clip; lit when Bar Size is exactly that ratio.
                    Flow {
                        Layout.fillWidth: true
                        spacing: 4
                        visible: !page.pv.letterboxClip || page.pv.letterboxPresets.length > 0
                        Caption { text: "Letterbox Size:"; height: 26; verticalAlignment: Text.AlignVCenter }
                        Caption {
                            visible: !page.pv.letterboxClip
                            text: "no clip loaded"; color: Theme.fgFaint
                            height: 26; verticalAlignment: Text.AlignVCenter
                        }
                        Repeater {
                            model: page.pv.letterboxPresets
                            AccentButton {
                                required property var modelData
                                text: modelData
                                implicitHeight: 26
                                kind: String(page.st.fb_preset) === modelData ? "tonal" : "ghost"
                                onClicked: { page.st.fb_preset = modelData; Qt.callLater(page.pv.choosePreset) }
                                HoverTip {
                                    tip: "Snap the Bar Size dial to " + modelData + ": the bars that letterbox THIS "
                                         + "source down to that ratio.
Only ratios WIDER than the source get a button -- "
                                         + "you can't letterbox to a narrower frame -- so a 1.85:1 source has no 1.85 "
                                         + "button, and a 2.39:1 source gets none at all.
No button lit means custom "
                                         + "bars: turn Bar Size freely, it works for any aspect ratio."
                                }
                            }
                        }
                    }
                    GridLayout {
                        objectName: "frameBreakDials"
                        Layout.fillWidth: true
                        columns: 3
                        columnSpacing: 0
                        rowSpacing: 6
                        SpDial { key: "frame_break_bars"; label: "Bar Size:"; from: 0; to: 1; step: 0.01; decimals: 2; defaultValue: 0; help: page.help("frame_break_bars") }
                        SpDial { key: "frame_break_thresh"; label: "Break Thr:"; from: 0; to: 1; step: 0.01; decimals: 2; defaultValue: 1.0; help: page.help("frame_break_thresh") }
                        SpDial { key: "fb_edge_blur"; label: "Edge Blur:"; from: 0; to: 20; step: 1; decimals: 0; defaultValue: 0; help: page.help("frame_break_edge_blur") }
                        SpDial { key: "fb_erode_left"; label: "Shift Left:"; from: -30; to: 30; step: 1; decimals: 0; defaultValue: 0; fillFrom: 0; help: page.help("frame_break_edge_lr") }
                        SpDial { key: "fb_erode_right"; label: "Shift Right:"; from: -30; to: 30; step: 1; decimals: 0; defaultValue: 0; fillFrom: 0; help: page.help("frame_break_edge_lr") }
                        SpDial { key: "fb_erode_global"; label: "Expand/Shrink:"; from: -30; to: 30; step: 1; decimals: 0; defaultValue: 0; fillFrom: 0; help: page.help("frame_break_edge_global") }
                    }
                }
            }

            // -- under Special FX: the clip list (user, 2026-09-27, as Merging's).
            // It only ever takes the room left down to the bottom of the body --
            // the page never grows for it (Special FX's folds open, a small
            // window): short of room it keeps just its heading and counter, and
            // with none it steps aside.
            // Not disabled while a job runs (user, 2026-10-05): a disabled item takes
            // no wheel or drag, so the list couldn't scroll. JobQueue keeps its rows
            // hands-off itself while busy (no ticks, no click-to-load, no buttons).
            JobQueue {
                id: splatQueue
                objectName: "splatJobQueue"
                x: fxCol.x
                y: fxCol.y + fxCol.height + Theme.gap
                width: fxCol.width
                readonly property real room: body.height - y
                height: Math.max(0, room)
                visible: room >= fixedPart
                clip: true
                Layout.fillHeight: false
                pv: page.pv
                ctl: page.sp
                // right-click a clip (user, 2026-10-10)
                rowActions: [{ id: "splat", text: "Splat This Clip Now" }, { id: "test", text: "Test Render" },
                             { id: "rerun", text: "Rerun in DepthCrafter" },
                             { id: "send", text: "Send to Inpainting", whenDone: true }]
                onRowAction: (id, path) => {
                    if (id === "splat") page.sp.startClip(path)
                    else if (id === "test") page.sp.testClip(path)      // into Test Renders (user, 2026-10-15)
                    else if (id === "rerun") page.rerunInDepthCrafter(path)
                    else if (id === "send") sendOn(path, page.sp, "inpainting", Inpainting, 2)
                }
                busy: page.sp.busy
                // the batch blanks the filename as it starts, so any name it reports is its own
                trustRunning: true
                running: page.sp.busy && page.sp.info.filename && page.sp.info.filename !== "N/A"
                         ? String(page.sp.info.filename) : ""
                // a bar per pass, H over L, filled by the frames the renderer reports
                passes: ["H", "L"]
                passOn: [!!page.st.enable_full_res, !!page.st.enable_low_res]
                passFrames: page.sp.passFrames
                passDone: page.sp.passDone
                countText: String(page.sp.clipCount || "—")
                countHelp: "Which clip of the batch is being splatted, and how many there are in total."
                // what the badges mean, under the list (user, 2026-10-03)
                legendKinds: ["sidecar", "edited", "text", "keyframe", "break", "pop", "particles", "erased"]
            }
        }
    }

    // ---- foot --------------------------------------------------------------
    ActionBar {
        id: actionBar
        anchors.left: parent.left
        anchors.right: parent.right
        // this stage's own tips, typed out above the bar (user, 2026-09-27)
        tips: Tips.forStage("splatting")      // studio/qml/Studio/tips.js: Settings > Tips lists them all
        anchors.bottom: parent.bottom
        anchors.margins: 12
        busy: page.sp.busy
        done: page.sp.progressDone
        total: page.sp.progressTotal
        status: page.sp.status

        AccentButton {
            text: "Single"; kind: "outline"
            enabled: page.idle && page.pv.loaded && (!!page.st.enable_full_res || !!page.st.enable_low_res)
            onClicked: page.sp.startSingle()
            HoverTip { tip: page.help("start_single_button") || "Process only the clip loaded in the preview." }
        }
        AccentButton {
            text: "Start"
            iconGlyph: Theme.icon.play
            // v1 toggle_processing_settings_fields: nothing to render with both off.
            enabled: page.idle && (!!page.st.enable_full_res || !!page.st.enable_low_res)
            onClicked: page.sp.start()
            HoverTip { tip: page.help("start_button") }
        }
        AccentButton {
            objectName: "spStop"
            text: "Stop"
            kind: "outline"
            enabled: page.sp.busy
            // asks, then stops now (audit #13); Stop After Clip is the gentle way
            onClicked: (page.prompt ? page.prompt.askStop({ stage: "Splatting", safeText: "Stop after this clip", safeDetail: "the clip being processed finishes, then the batch stops. The rest of the folder is left for the next Start.", onSafe: page.sp.multiClip ? () => { if (!page.sp.stopAfterClip) page.sp.toggleStopAfterClip() } : null, onYes: () => page.sp.stop() })
                         : page.sp.stop())
            HoverTip { tip: page.help("stop_button") }
        }
        AccentButton {
            objectName: "spStopAfter"
            text: page.sp.stopAfterClip ? "Stopping After Clip" : "Stop After Clip"
            kind: page.sp.stopAfterClip ? "tonal" : "outline"
            // only while a folder of clips is running (user, 2026-09-28)
            visible: page.sp.busy && page.sp.multiClip
            onClicked: page.sp.toggleStopAfterClip()
            HoverTip {
                tip: "Let the clip being processed finish, then stop before the next one.\n"
                     + "The finished clips are complete; the rest of the folder is\n"
                     + "left for the next Start. Click again to carry on instead."
            }
        }
        AccentButton {
            text: page.autoConvRunning ? "Stop Auto-Converge" : "Preview Auto-Converge"; kind: "outline"
            // v1 _AUTO_CONV_PREVIEW_MODES: only where it has something to show
            visible: ["Average", "Peak", "Hybrid"].indexOf(String(page.st.auto_convergence_mode)) >= 0
                     && !page.bh["splat.auto_converge_preview"]
            enabled: page.idle && page.pv.loaded
            onClicked: page.autoConvRunning ? page.pv.stopAutoConverge() : page.pv.autoConverge()
            HoverTip { tip: page.help("preview_auto_converge") }
        }
        AccentButton {
            text: page.pv.sidecarExists ? "Update Sidecar" : "Create Sidecar"; kind: "outline"
            enabled: page.idle && page.pv.loaded
            onClicked: page.updateSidecar()
            HoverTip { tip: page.help("update_sidecar_button") + "\n\nShortcut: Ctrl+S" }
        }

        trailing: [
            AccentButton {
                text: "View Output Folder"
                kind: "outline"
                onClicked: page.sp.openOutputFolder()
            }
        ]
    }

    // ---- menus: v1's File and Help (Options was theme + glass: the shell's) --
    // Settings > Splatting: the Low Res presets and Auto Size picks (user, 2026-10-11),
    // DepthCrafter's editor in multiples of 16
    readonly property Component settingsExtra: Component {
        DcPresetEditor {
            dc: page.sp
            stageName: "Splatting"
            presetsFile: "config/splatting_presets.json"
            intro: "Pick an aspect ratio, then tick the sizes you want for Low Res Width/Height. Every size is a "
                   + "multiple of 16, which both Inpainting engines accept, so most can't match the ratio exactly: "
                   + "beside each is how much wider or taller it is than the true ratio. That's only the size the "
                   + "low-res splat and the fill are made at: Inpainting's Hi-Res Blend puts the fill back on the "
                   + "full-size picture, so the finished clip keeps its true shape. Saved sizes appear in Resolution "
                   + "Preset, a column per ratio."
        }
    }
    readonly property var stageMenus: [fileMenu, helpMenu]

    StudioMenu {
        id: fileMenu
        title: "File"
        MenuItem { text: "Load Settings from File..."; enabled: page.idle; onTriggered: loadSettingsDlg.open() }
        MenuItem { text: "Save Settings"; property string tip: "Saves this stage's settings now."; onTriggered: { page.sp.save(); Theme.toast("success", "Settings saved", "") } }
        MenuItem { text: "Save Settings to File..."; onTriggered: saveSettingsDlg.open() }
        MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: Theme.border } }
        MenuItem { text: "Load Fusion Export (.fsexport)..."; enabled: page.idle; onTriggered: { fusionFlow.mode = "generate_sidecars"; fusionExportDlg.open() } }
        MenuItem { text: "FSExport to custom sidecar..."; enabled: page.idle; onTriggered: { fusionFlow.mode = "generate_custom_sidecars"; fusionExportDlg.open() } }
        MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: Theme.border } }
        MenuItem {
            text: "Update Slider from Sidecar"; checkable: true
            checked: !!page.st.update_slider_from_sidecar
            onTriggered: page.st.update_slider_from_sidecar = checked
        }
        MenuItem {
            text: "Auto Save Sidecar on Next"; checkable: true
            checked: !!page.st.auto_save_sidecar
            onTriggered: page.st.auto_save_sidecar = checked
        }
        MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: Theme.border } }
        MenuItem {
            text: "Reset to Default"; enabled: page.idle
            property string tip: "Puts every setting on this stage back to its default. Asks first."
            onTriggered: page.confirm("Reset Settings", "Are you sure you want to reset all settings to their default values?",
                                      "Reset", () => page.sp.resetDefaults())
        }
        MenuItem {
            text: "Restore Finished"; enabled: page.idle
            property string tip: "Moves the inputs already processed, from the 'finished' folders, back into their input folders so they can be run again."
            onTriggered: page.confirm("Restore Finished Files",
                                      "Are you sure you want to move all files from 'finished' folders back to their input directories?",
                                      "Restore", () => page.sp.restoreFinished())
        }
        MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: Theme.border } }
        MenuItem {
            text: "Sidecars to Source"; enabled: page.idle
            property string tip: "Moves the sidecar files from the Depth Maps folder into the Source Clips folder, taking '_depth' off their names. Asks first."
            onTriggered: page.confirm("Sidecars: Depth → Source",
                                      "This will move sidecar files from:\n  " + page.st.input_depth_maps + "\nto:\n  "
                                      + page.st.input_source_clips + "\n\nRemoving '_depth' suffix from filenames.\n\nContinue?",
                                      "Move", () => page.pv.moveSidecars(true))
        }
        MenuItem {
            text: "Sidecars to Depth"; enabled: page.idle
            property string tip: "Moves the sidecar files from the Source Clips folder into the Depth Maps folder, adding '_depth' to their names. Asks first."
            onTriggered: page.confirm("Sidecars: Source → Depth",
                                      "This will move sidecar files from:\n  " + page.st.input_source_clips + "\nto:\n  "
                                      + page.st.input_depth_maps + "\n\nAdding '_depth' suffix to filenames.\n\nContinue?",
                                      "Move", () => page.pv.moveSidecars(false))
        }
    }

    StudioMenu {
        id: helpMenu
        title: "Help"
        MenuItem {
            text: "Debug Logging"; checkable: true
            checked: !!page.st.debug_logging
            onTriggered: page.st.debug_logging = checked
        }
        MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: Theme.border } }
        MenuItem {
            text: "About"
            onTriggered: page.prompt.ask({ title: "About Splatting", message: page.sp.aboutText,
                                           buttons: [{ text: "OK", code: 1, kind: "filled" }] })
        }
    }
}
