import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Layouts
import Studio
import "tips.js" as Tips

// DepthCrafter, v2. Same sections, labels, settings, rules and arrangement
// as v1's depthcrafter_gui_seg.create_widgets, sized like v1 (three ~370px
// columns) and fitting the window with no scrolling at all:
//
//   Input Source (full width)
//   Main Parameters     | Frame & Segment Control | Merged Output Options | Upcoming
//   Output Processing   | Secondary Output        |   (only with segments) | Jobs
//                       | Current Clip Info       |                        |
//   [ action bar: progress, status, buttons ]
//
// The settings columns keep v1's width; Upcoming Jobs takes the rest (user,
// 2026-10-10: the room the wide columns left empty).
//
// v1 hides Merged Output Options when "Process as Segments" is off and
// collapses to two columns; here the column slides out and the other two
// widen into the space. Rows that only exist while their checkbox is ticked
// fold away instead of vanishing.
Item {
    id: page
    property bool active: false
    property real flipAngle: 0
    property var prompt: null            // Main's PromptDialog
    property var consoleRef: null        // Main's ConsoleWindow
    // the sub tabs (user, 2026-10-25), as Inpainting's: 0 = DepthCrafter, 1 = Video Depth Anything (VdaPage.qml).
    // Main's openStage(0, 1) picks VDA -- Splatting's "Rerun with Video Depth Anything".
    property int engine: 0

    readonly property var dc: DepthCrafter
    readonly property var st: dc.settings
    // Studio > Use Beginner Mode: v1's rule key -> hidden (beginner.py).
    readonly property var bh: Beginner.hide
    // A picked chapter fills every path, so the rows are put away (v1's
    // collapse); Change Folders brings them back without leaving it.
    property bool foldersOpen: false
    readonly property bool chapterMode: dc.hasProject && dc.chapterNum >= 0
    // Manual paths: put away by Hide Folders, remembered (user, 2026-09-30)
    readonly property bool manualHidden: !!Shell.foldersHidden["depthcrafter"]
    readonly property bool pathsFolded: chapterMode ? !foldersOpen : manualHidden
    function toggleFolders() {
        if (chapterMode) foldersOpen = !foldersOpen
        else Shell.setFoldersHidden("depthcrafter", !manualHidden)
    }
    readonly property bool idle: !dc.busy

    //: Height the page needs to show everything as currently expanded. Main
    //: keeps the window at least this tall, so nothing is ever cut off.
    // v1 fits 787 wide, 1067 with the third column; these are the narrowest
    // widths nothing clips at here. Wide as soon as the column starts in,
    // narrow only once it has gone, so it never animates in a window too small.
    // (2026-10-10: plus Upcoming Jobs' least width)
    readonly property int requiredWidth: engine === 1 ? vdaPage.requiredWidth
                                         : 36 + (grid.mergeFrac > 0 ? 3 : 2) * (grid.colMin + Theme.gap) + grid.listMin
    readonly property real requiredHeight: engine === 1 ? vdaPage.y + vdaPage.requiredHeight
                                           : content.y + content.implicitHeight + Theme.gap + actionBar.implicitHeight + 12

    transform: Rotation {
        origin.x: page.width / 2; origin.y: page.height / 2
        axis { x: 0; y: 1; z: 0 }
        angle: page.flipAngle
    }

    // Controls pre-bound to this stage's settings.
    component DcBox: TextBox { store: page.st; owner: page.dc }
    component DcDrop: Dropdown { store: page.st }
    component DcCheck: CheckField { store: page.st }
    component Caption: Text {
        color: Theme.fgMuted
        font.family: Theme.bodyFont
        font.pixelSize: Theme.bodySize
        Layout.alignment: Qt.AlignVCenter
    }
    function help(k) { return page.dc.help[k] || "" }

    onActiveChanged: if (active) { dc.refreshSceneCut(); dc.refreshChapters(); VDA.refreshSceneCut(); VDA.refreshChapters() }

    // ---- runner prompts and toasts --------------------------------------
    Connections {
        target: page.dc
        function onPromptRequested(title, message, allowCancel) {
            const buttons = [{ text: "Yes", code: 1, kind: "filled" }, { text: "No", code: 0, kind: "outline" }]
            if (allowCancel) buttons.push({ text: "Cancel", code: -1, kind: "ghost" })
            page.prompt.ask({ title: title, message: message, buttons: buttons,
                              onDone: (code) => page.dc.answerPrompt(code) })
        }
        function onToast(kind, title, message) { Theme.toast(kind, title, message) }
    }
    function confirm(title, message, yesText, action) {
        page.prompt.ask({
            title: title, message: message,
            buttons: [{ text: yesText, code: 1, kind: "filled" }, { text: "Cancel", code: 0, kind: "ghost" }],
            onDone: (code) => { if (code === 1) action() }
        })
    }

    // ---- file dialogs (native) ------------------------------------------
    // one picker for a folder or a single clip (user, 2026-10-05: FolderPicker, Columns)
    FolderPicker {
        id: inputFolderDlg
        title: "Input: a folder, or a single video, image or sequence frame"
        allowFiles: true
        fileTypes: ["mp4", "avi", "mov", "mkv", "webm", "flv", "gif", "png", "jpg", "jpeg", "bmp", "tiff", "exr"]
        currentFolder: page.st.input_dir_or_file_var
        onAccepted: page.st.input_dir_or_file_var = selectedPath
    }
    FolderPicker {
        id: outputFolderDlg
        title: "Output folder"
        currentFolder: Shell.pathToUrl(page.st.output_dir)
        onAccepted: page.st.output_dir = Shell.urlToPath(selectedFolder)
    }

    property string pendingMeta: ""
    FileDialog {
        id: remergeMetaDlg
        title: "Select Master Metadata JSON for Re-Merging"
        currentFolder: Shell.pathToUrl(page.st.output_dir)
        nameFilters: ["JSON files (*.json)", "All files (*)"]
        onAccepted: {
            page.pendingMeta = Shell.urlToPath(selectedFile)
            const fmt = page.st.merge_output_format_var
            if (fmt.indexOf("sequence") >= 0) {
                remergeFolderDlg.open()
            } else {
                const base = Shell.baseName(page.pendingMeta).replace("_master_meta", "") + page.st.merge_output_suffix_var
                const ext = fmt === "exr" ? ".exr" : ".mp4"
                remergeSaveDlg.selectedFile = Shell.fileUrl(page.st.output_dir, base + ext)
                remergeSaveDlg.open()
            }
        }
    }
    FileDialog {
        id: remergeSaveDlg
        title: "Save Re-Merged Output As"
        fileMode: FileDialog.SaveFile
        currentFolder: Shell.pathToUrl(page.st.output_dir)
        nameFilters: page.st.merge_output_format_var === "exr" ? ["EXR File (*.exr)", "All files (*)"]
                                                             : ["MP4 (*.mp4)", "All files (*)"]
        onAccepted: page.dc.remerge(page.pendingMeta, Shell.urlToPath(selectedFile))
    }
    FolderPicker {
        id: remergeFolderDlg
        title: "Select Parent Folder for the Re-Merged Sequence"
        currentFolder: Shell.pathToUrl(page.st.output_dir)
        onAccepted: page.dc.remerge(page.pendingMeta, Shell.urlToPath(selectedFolder))
    }
    FileDialog {
        id: visualsMetaDlg
        title: "Select Master Metadata JSON for Segment Visual Generation"
        currentFolder: Shell.pathToUrl(page.st.output_dir)
        nameFilters: ["JSON files (*.json)", "All files (*)"]
        onAccepted: {
            const meta = Shell.urlToPath(selectedFile)
            const fmt = page.st.keep_intermediate_segment_visual_format_var
            if (fmt === "none") {
                Theme.toast("info", "Pick a format", "Segment Format is 'none'. Choose a format under Merged Output Options.")
                return
            }
            page.confirm("Generate / overwrite visuals?",
                         "Generate '" + fmt + "' visuals for the segments in " + Shell.baseName(meta) + "?\nThis may overwrite existing visuals.",
                         "Generate", () => page.dc.generateVisuals(meta))
        }
    }
    FileDialog {
        id: loadSettingsDlg
        title: "Load Settings File"
        nameFilters: ["JSON files (*.json)", "All files (*)"]
        onAccepted: page.dc.loadSettingsFrom(Shell.urlToPath(selectedFile))
    }
    FileDialog {
        id: saveSettingsDlg
        title: "Save Settings As"
        fileMode: FileDialog.SaveFile
        defaultSuffix: "json"
        nameFilters: ["JSON files (*.json)", "All files (*)"]
        onAccepted: page.dc.saveSettingsTo(Shell.urlToPath(selectedFile))
    }

    // ---- engine switch: DepthCrafter | VDA (as Inpainting's V1 | V2) ----
    StageTabBar {
        id: engineSwitch
        objectName: "dcEngineSwitch"
        x: 14
        y: 0
        implicitHeight: 34
        numbered: false
        model: ["DepthCrafter", "VDA"]       // user, 2026-10-25: "rename the tab to vda"
        currentIndex: page.engine
        onActivated: (i) => page.engine = i
    }

    // ---- the page -------------------------------------------------------
    ColumnLayout {
        id: content
        objectName: "pageContent"
        x: 18
        y: engineSwitch.y + engineSwitch.height + 6
        width: page.width - 36
        spacing: Theme.gap
        opacity: page.engine === 0 ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.dur(260) } }
        transform: Translate { x: page.engine === 0 ? 0 : -40; Behavior on x { NumberAnimation { duration: Theme.dur(320); easing.type: Easing.OutCubic } } }

        Section {
            id: inputSourceSection
            title: "Input Source"
            bodyShown: !page.pathsFolded
            enabled: page.idle
            headerRight: [
                AccentButton {
                    visible: page.dc.sceneCutDescription.length > 0
                    text: "Import from Scene Cut"
                    kind: "tonal"
                    iconGlyph: Theme.icon.importIcon
                    onClicked: page.dc.importFromSceneCut()
                    HoverTip {
                        tip: "Fill Input Folder/File from the most recent Scene Cut split.\n"
                             + "Clicking replaces whatever that field currently holds.\n\n"
                             + "Most recent job:\n" + page.dc.sceneCutDescription
                    }
                },
ChangeFoldersButton {
                    open: !page.pathsFolded
                    manual: !page.chapterMode
                    onClicked: page.toggleFolders()
                    paths: [{ label: "Input Folder/File", value: page.st.input_dir_or_file_var },
                            { label: "Output Folder", value: page.st.output_dir }]
                },
                ChapterPicker { visible: page.dc.hasProject; dc: page.dc; onPicked: page.foldersOpen = false }
            ]
            FieldRow {
                visible: !page.pathsFolded
                label: "Input Folder/File"
                help: page.help("input_dir_or_file")
                labelWidth: 130
                RowLayout {
                    width: parent.width
                    spacing: 8
                    DcBox { key: "input_dir_or_file_var"; numeric: false; Layout.fillWidth: true }
                    AccentButton { objectName: "browseInput"; text: "Browse"; kind: "outline"; onClicked: inputFolderDlg.open()
                                   HoverTip { tip: "Pick a folder to process every clip in it, or a single clip to process just that one." } }
                }
            }
            FieldRow {
                visible: !page.pathsFolded
                label: "Output Folder"
                help: page.help("output_dir")
                labelWidth: 130
                RowLayout {
                    width: parent.width
                    spacing: 8
                    DcBox { key: "output_dir"; numeric: false; Layout.fillWidth: true }
                    AccentButton { text: "Browse Folder"; kind: "outline"; onClicked: outputFolderDlg.open() }
                }
            }
        }

        // ---- settings columns ----------------------------------------------
        Item {
            id: grid
            Layout.fillWidth: true
            implicitHeight: Math.max(col0.implicitHeight, col1.implicitHeight, col2.implicitHeight * mergeFrac)

            readonly property real gap: Theme.gap
            property real mergeFrac: page.st.process_as_segments_var ? 1 : 0
            Behavior on mergeFrac { NumberAnimation { duration: Theme.dur(520); easing.type: Easing.InOutCubic } }
            // the columns at v1's ~370 px; Upcoming Jobs takes all the rest (user, 2026-10-10:
            // the settings squeezed over, the room on the right the list's)
            readonly property real colMin: 370
            readonly property real listMin: 330
            readonly property real cols: 2 + mergeFrac
            readonly property real colW: colMin
            readonly property real listX: cols * (colW + gap)

            ColumnLayout {
                id: col0
                x: 0
                width: grid.colW
                spacing: Theme.gap

                Section {
                    title: "Main Parameters"
                    enabled: page.idle
                    FieldRow { label: "Guidance Scale"; shown: !page.bh["dc.guidance_scale"]; help: page.help("guidance_scale"); DcBox { key: "guidance_scale" } }
                    FieldRow { label: "Inference Steps"; help: page.help("inference_steps"); DcBox { key: "inference_steps" } }
                    DcCheck { key: "auto_resolution_var"; text: "Auto Size for the Loaded Clip"; help: page.help("auto_resolution") }
                    FieldRow {
                        label: "Resolution Preset"
                        help: page.help("resolution_preset")
                        PresetButton { dc: page.dc; st: page.st }
                    }
                    FieldRow { label: "Target Width"; help: page.help("target_width"); DcBox { key: "target_width" } }
                    FieldRow { label: "Target Height"; help: page.help("target_height"); DcBox { key: "target_height" } }
                    FieldRow { label: "Seed"; help: page.help("seed"); DcBox { key: "seed" } }
                    FieldRow {
                        label: "CPU Offload Mode"
                        help: page.help("cpu_offload")
                        DcDrop { key: "cpu_offload"; model: page.dc.choices.cpu_offload }
                    }
                }

                Section {
                    title: "Output Processing"
                    enabled: page.idle
                    DcCheck { key: "stabilize_depth_var"; shown: !page.bh["dc.stabilize_depth"]; text: "Stabilize Depth Scale"; help: page.help("stabilize_depth") }
                    FieldRow {
                        label: "   ↳ Reference"
                        help: page.help("stabilize_ref_mode")
                        shown: !!page.st.stabilize_depth_var && !page.bh["dc.stabilize_depth"]
                        RowLayout {
                            spacing: 8
                            DcDrop { key: "stabilize_ref_mode_var"; model: page.dc.choices.stabilize_ref_mode_var; boxWidth: 100 }
                            Caption { text: "Index" }
                            // Stays visible but inactive in "auto": a live option of a
                            // section in use, unlike the section as a whole (v1).
                            DcBox { key: "stabilize_ref_index_var"; boxWidth: 64; enabled: page.st.stabilize_ref_mode_var === "manual" }
                        }
                    }
                    FieldRow {
                        label: "   ↳ Smooth"
                        help: page.help("stabilize_smooth_window")
                        shown: !!page.st.stabilize_depth_var && !page.bh["dc.stabilize_depth"]
                        DcBox { key: "stabilize_smooth_window_var"; boxWidth: 64 }
                    }
                    DcCheck { key: "full_percentile_norm_var"; shown: !page.bh["dc.percentile_norm"]; text: "Percentile Output Norm"; help: page.help("full_percentile_norm") }
                    FieldRow {
                        label: "   ↳ Low % / High %"
                        help: page.help("full_norm_low_perc")
                        shown: !!page.st.full_percentile_norm_var && !page.bh["dc.percentile_norm"]
                        RowLayout {
                            spacing: 8
                            DcBox { key: "full_norm_low_perc_var"; boxWidth: 72 }
                            DcBox { key: "full_norm_high_perc_var"; boxWidth: 72 }
                        }
                    }
                    // Auto top (user, 2026-10-12): the range from the clip's usual frames only
                    DcCheck {
                        key: "full_norm_auto_top_var"
                        shown: !!page.st.full_percentile_norm_var && !page.bh["dc.percentile_norm"]
                        text: "   ↳ Auto Top (let brief close passes clip)"
                        help: page.help("full_norm_auto_top")
                    }
                    FieldRow {
                        label: "Encoder CRF / Preset"
                        shown: !page.bh["dc.output_crf"]
                        help: page.help("output_crf")
                        RowLayout {
                            spacing: 8
                            DcBox { key: "output_crf_var"; numeric: false; boxWidth: 64 }
                            DcDrop { key: "output_preset_var"; model: page.dc.choices.output_preset_var; boxWidth: 110 }
                        }
                    }
                    FieldRow {
                        label: "Output Format"
                        help: page.help("merge_output_format")
                        DcDrop { key: "merge_output_format_var"; model: page.dc.choices.merge_output_format_var }
                    }
                }
            }

            ColumnLayout {
                id: col1
                x: grid.colW + grid.gap
                width: grid.colW
                spacing: Theme.gap

                Section {
                    title: "Frame & Segment Control"
                    enabled: page.idle
                    FieldRow { label: "Window Size"; help: page.help("window_size"); DcBox { key: "window_size" } }
                    FieldRow { label: "Overlap"; help: page.help("overlap"); DcBox { key: "overlap" } }
                    DcCheck { key: "latent_checkpoint_var"; text: "Save Checkpoint"; help: page.help("latent_checkpoint") }
                    FieldRow { label: "Target FPS (-1 Original)"; labelWidth: 176; shown: !page.bh["dc.target_fps"]; help: page.help("target_fps"); DcBox { key: "target_fps" } }
                    FieldRow { label: "Process Max Frames (-1 All)"; labelWidth: 176; shown: !page.bh["dc.process_max_frames"]; help: page.help("process_length"); DcBox { key: "process_length" } }
                    DcCheck { key: "save_final_output_json_var"; shown: !page.bh["dc.save_final_json"]; text: "Save Sidecar JSON for Final Output"; help: page.help("save_final_json") }
                    DcCheck { key: "process_as_segments_var"; text: "Process as Segments (Low VRAM Mode)"; help: page.help("process_as_segments") }
                    // as the other stages' Resume (user, 2026-10-25)
                    DcCheck {
                        objectName: "dcResume"
                        key: "move_to_finished"; text: "Resume"
                        help: page.help("move_to_finished")
                              || "On: each clip whose depth map is done moves into a 'finished' folder inside the input "
                               + "folder (a failed one into 'failed'), so the next Start skips it and an interrupted batch "
                               + "picks up where it stopped. Off: the clips stay where they are, and every Start runs them "
                               + "all again -- and Splatting, which reads them from 'finished', needs its Source Clips "
                               + "pointed at this folder instead. Restore Finished moves them back."
                    }
                }

                Section {
                    title: "Secondary Output"
                    visible: !page.bh["dc.secondary_output"]
                    enabled: page.idle
                    DcCheck { key: "enable_dual_output_robust_norm"; text: "Enable Secondary Output"; help: page.help("enable_secondary_output") }
                    FieldRow {
                        label: "Depth Output Range (0-1)"
                        labelWidth: 176
                        help: page.help("robust_norm_output_min")
                        shown: !!page.st.enable_dual_output_robust_norm
                        RowLayout {
                            spacing: 8
                            DcBox { key: "robust_norm_output_min"; boxWidth: 64 }
                            DcBox { key: "robust_norm_output_max"; boxWidth: 64 }
                        }
                    }
                    FieldRow {
                        label: "Clipped Output % Range"
                        labelWidth: 176
                        help: page.help("robust_norm_low_percentile")
                        shown: !!page.st.enable_dual_output_robust_norm
                        RowLayout {
                            spacing: 8
                            DcBox { key: "robust_norm_low_percentile"; boxWidth: 64 }
                            DcBox { key: "robust_norm_high_percentile"; boxWidth: 64 }
                        }
                    }
                    FieldRow {
                        label: "Output Suffix"
                        labelWidth: 176
                        help: page.help("robust_output_suffix")
                        shown: !!page.st.enable_dual_output_robust_norm
                        DcBox { key: "robust_output_suffix"; numeric: false; boxWidth: 150 }
                    }
                }

                // Stays live during a run: it is what you watch while one goes.
                ClipInfo {
                    consoleRef: page.consoleRef
                    rows: [
                        { label: "Batch Progress", value: page.dc.progressCount || " ", help: page.help("current_video_count") },
                        { label: "Filename", value: page.dc.clipName, help: page.help("current_filename") },
                        { label: "Resolution", value: page.dc.clipResolution, help: page.help("current_resolution") },
                        { label: "Frames", value: page.dc.clipFrames, help: page.help("current_frames") }
                    ]
                }
            }

            ColumnLayout {
                id: col2
                x: 2 * (grid.colW + grid.gap)
                width: grid.colW
                spacing: Theme.gap
                opacity: grid.mergeFrac
                visible: grid.mergeFrac > 0.01
                transform: Translate { x: (1 - grid.mergeFrac) * 60 }

                Section {
                    title: "Merged Output Options"
                    enabled: page.idle
                    DcCheck { key: "keep_intermediate_npz_var"; text: "Keep intermediate NPZ"; help: page.help("keep_npz") }
                    FieldRow {
                        label: "   ↳ Min thresh. to Keep"
                        labelWidth: 160
                        help: page.help("min_frames_npz")
                        enabled: !!page.st.keep_intermediate_npz_var
                        DcBox { key: "min_frames_to_keep_npz_var"; boxWidth: 72 }
                    }
                    FieldRow {
                        label: "   ↳ Segment Format"
                        labelWidth: 160
                        help: page.help("segment_visual_format")
                        enabled: !!page.st.keep_intermediate_npz_var
                        DcDrop { key: "keep_intermediate_segment_visual_format_var"; model: page.dc.choices.keep_intermediate_segment_visual_format_var; boxWidth: 140 }
                    }
                    DcCheck { key: "merge_dither_var"; text: "Dithering"; help: page.help("merge_dither") }
                    FieldRow {
                        label: "   ↳ Strength"
                        labelWidth: 160
                        help: page.help("merge_dither_strength")
                        enabled: !!page.st.merge_dither_var
                        DcBox { key: "merge_dither_strength_var"; boxWidth: 72 }
                    }
                    DcCheck { key: "merge_gamma_correct_var"; text: "Gamma Adjust"; help: page.help("merge_gamma") }
                    FieldRow {
                        label: "   ↳ Value"
                        labelWidth: 160
                        help: page.help("merge_gamma_value")
                        enabled: !!page.st.merge_gamma_correct_var
                        DcBox { key: "merge_gamma_value_var"; boxWidth: 72 }
                    }
                    DcCheck { key: "merge_percentile_norm_var"; text: "Normalization"; help: page.help("merge_percentile_norm") }
                    FieldRow {
                        label: "   ↳ Low / High"
                        labelWidth: 160
                        help: page.help("merge_norm_low_perc")
                        enabled: !!page.st.merge_percentile_norm_var
                        RowLayout {
                            spacing: 8
                            DcBox { key: "merge_norm_low_perc_var"; boxWidth: 64 }
                            DcBox { key: "merge_norm_high_perc_var"; boxWidth: 64 }
                        }
                    }
                    FieldRow {
                        label: "Alignment Method"
                        labelWidth: 160
                        help: page.help("merge_alignment_method")
                        DcDrop { key: "merge_alignment_method_var"; model: page.dc.choices.merge_alignment_method_var; boxWidth: 140 }
                    }
                    FieldRow {
                        label: "Output Suffix"
                        labelWidth: 160
                        help: page.help("merge_output_suffix")
                        DcBox { key: "merge_output_suffix_var"; numeric: false; boxWidth: 140 }
                    }
                }
            }

            // Upcoming Jobs (user, 2026-10-10): the clips a Start would process -- at least as
            // tall as the columns, and down to the action bar when the window has the room. It
            // isn't part of the grid's height, so the page never asks for more for it.
            JobQueue {
                objectName: "dcJobQueue"
                x: grid.listX
                width: Math.max(0, grid.width - x)
                height: Math.max(grid.height, actionBar.y - Theme.gap - content.y - grid.y)
                clip: true
                source: page.dc.jobClips
                ctl: page.dc
                busy: page.dc.busy
                // only ever the job's clips ("N/A" between them, which names none)
                trustRunning: true
                running: page.dc.busy && page.dc.clipName && page.dc.clipName !== "N/A" ? String(page.dc.clipName) : ""
                progress: typeof JobStats !== "undefined" && JobStats.stage === "depthcrafter" ? JobStats.fraction : 0
                countText: page.dc.progressCount || ""
                rowActions: [{ id: "run", text: "Process This Clip Now" }, { id: "test", text: "Test Render" },
                             { id: "finished", text: "Move to Finished" },
                             { id: "send", text: "Send to Splatting", whenDone: true }]
                onRowAction: (id, path) => {
                    if (id === "run") page.dc.startClip(path)
                    else if (id === "test") page.dc.testClip(path)      // into Test Renders (user, 2026-10-15)
                    else if (id === "finished") page.dc.jobClips.moveToFinished(path)
                    else if (id === "send") sendOn(path, page.dc, "splatting", Splatting, 1)
                }
            }
        }
    }

    // ---- action bar -----------------------------------------------------
    ActionBar {
        id: actionBar
        statsStage: "depthcrafter"
        opacity: page.engine === 0 ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.dur(260) } }
        anchors.left: parent.left
        anchors.right: parent.right
        // this stage's own tips, typed out above the bar (user, 2026-09-27)
        tips: Tips.forStage("depthcrafter")      // studio/qml/Studio/tips.js: Settings > Tips lists them all
        anchors.bottom: parent.bottom
        anchors.margins: 12
        busy: page.dc.busy
        done: page.dc.progressDone
        total: page.dc.progressTotal
        status: page.dc.status

        AccentButton {
            text: "Start"
            iconGlyph: Theme.icon.play
            enabled: page.idle
            onClicked: page.dc.start()
            HoverTip { tip: page.help("start_button") }
        }
        AccentButton {
            text: "Cancel"
            kind: "outline"
            objectName: "dcCancel"
            enabled: page.dc.busy
            // asks, then stops now (audit #13)
            onClicked: (page.prompt ? page.prompt.askStop({ stage: "DepthCrafter", safeText: "Stop after this segment", safeDetail: "the segment being processed finishes and is kept; Resume carries on from the next one. (A clip processed whole, without segments, finishes first.)", onSafe: () => page.dc.cancelAtSafePoint(), onYes: () => page.dc.cancel() })
                         : page.dc.cancel())
            HoverTip { tip: page.help("cancel_button") }
        }

        trailing: [
            // Never disabled: checking finished clips mid-batch is the point (v1).
            AccentButton {
                text: "View Output Folder"
                kind: "outline"
                onClicked: page.dc.openOutputFolder()
            }
        ]

        // The segment tools, under the main row and only while segments are on.
        secondaryShown: !!page.st.process_as_segments_var && !page.bh["dc.remerge"]
        secondary: [
            AccentButton {
                text: "Re-Merge Segments"
                kind: "outline"
                enabled: page.idle
                onClicked: remergeMetaDlg.open()
                HoverTip { tip: page.help("remerge_button") }
            },
            AccentButton {
                text: "Generate Seg Visuals"
                kind: "outline"
                enabled: page.idle
                onClicked: visualsMetaDlg.open()
                HoverTip { tip: page.help("generate_visuals_button") }
            }
        ]
    }

    // v1's menus for this stage. Main puts them on the menu bar after the
    // Studio / View / GPU menus while this tab is showing.
    readonly property var stageMenus: engine === 1 ? vdaPage.stageMenus : [fileMenu, helpMenu]
    // ...and after them, the custom resolution presets (user, 2026-10-05)
    readonly property Component settingsExtra: Component { DcPresetEditor { dc: page.dc } }

    StudioMenu {
        id: fileMenu
        title: "File"
        MenuItem { text: "Load Settings..."; enabled: page.idle; onTriggered: loadSettingsDlg.open() }
        MenuItem { text: "Save Settings As..."; enabled: page.idle; onTriggered: saveSettingsDlg.open() }
        MenuItem {
            text: "Reset to Default"
            property string tip: "Puts every setting on this stage back to its default. Asks first."
            enabled: page.idle
            onTriggered: page.confirm("Reset Settings", "Are you sure you want to reset all settings to their default values?",
                                      "Reset", () => page.dc.resetDefaults())
        }
        MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: Theme.border } }
        MenuItem {
            text: "Restore Finished Input Files..."
            enabled: page.idle
            onTriggered: page.confirm("Restore Finished Input Files",
                                      "Are you sure you want to move original input files from the 'Finished' subfolder back to their original input directory?",
                                      "Restore", () => page.dc.restoreInputs("finished"))
        }
        MenuItem {
            text: "Restore Failed Input Files..."
            enabled: page.idle
            onTriggered: page.confirm("Restore Failed Input Files",
                                      "Are you sure you want to move original input files from the 'Failed' subfolder back to their original input directory?",
                                      "Restore", () => page.dc.restoreInputs("failed"))
        }
        MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: Theme.border } }
        MenuItem {
            text: "Use Local Models Only"
            checkable: true
            enabled: page.idle
            checked: !!page.st.use_local_models_only_var
            onTriggered: page.st.use_local_models_only_var = checked
        }
        MenuItem {
            text: "Use SageAttention (Faster)"
            checkable: true
            checked: !!page.st.use_sageattention_var
            onTriggered: page.st.use_sageattention_var = checked
        }
    }

    StudioMenu {
        id: helpMenu
        title: "Help"
        MenuItem {
            text: "Enable Debug Logging"
            checkable: true
            enabled: page.idle
            checked: !!page.st.debug_logging
            onTriggered: page.st.debug_logging = checked
        }
        MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: Theme.border } }
        MenuItem {
            text: "GUI Overview"
            property string tip: "A tour of this stage's settings."
            onTriggered: page.prompt.ask({ title: "GUI Overview", message: page.help("general_gui_overview"),
                                           buttons: [{ text: "OK", code: 1, kind: "filled" }] })
        }
    }

    // ---- Video Depth Anything ------------------------------------------------
    VdaPage {
        id: vdaPage
        objectName: "vdaPage"
        y: engineSwitch.y + engineSwitch.height
        width: page.width
        height: page.height - y
        prompt: page.prompt
        consoleRef: page.consoleRef
        opacity: page.engine === 1 ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.dur(260) } }
        transform: Translate { x: page.engine === 1 ? 0 : 40; Behavior on x { NumberAnimation { duration: Theme.dur(320); easing.type: Easing.OutCubic } } }
    }

    // drag and drop (user, 2026-10-25): Drop Zones over the paths
    DropZones {
        over: inputSourceSection
        store: page.st
        targets: [{ key: "input_dir_or_file_var", label: "Input", folder: true, slash: false,
                    files: ["mp4", "avi", "mov", "mkv", "webm", "flv", "gif", "png", "jpg", "jpeg", "bmp", "tiff", "exr"] },
                  { key: "output_dir", label: "Output Folder", folder: true, slash: false }]
    }
}
