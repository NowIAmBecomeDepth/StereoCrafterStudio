import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Layouts
import Studio
import "tips.js" as Tips

// Inpainting, v2. Two engines under one tab, as in v1: V1 (SVD) below, V2
// (Wan VACE) in InpaintingV2Page.qml.
//
// V1 keeps v1's arrangement (inpainting_gui.create_widgets) and its pairings,
// two settings to a row:
//
//   Folders
//   Parameters                (Inference Steps | Original Input Bias, ...)
//   Post-Processing           | Current Clip Info
//   [ progress, status, Start / Stop, View Output Folder, menu ]
//
// Encoding lives where v1 keeps it, under the menu (Encoding Settings...).
Item {
    id: page
    property bool active: false
    property real flipAngle: 0
    property var prompt: null
    property var consoleRef: null
    property int engine: 0               // 0 = V1 (SVD), 1 = V2 (Wan VACE), 2 = M2SVid (personal/)
    // M2SVid (personal/m2svid): the user's own engine, not released -- its sub tab only where it is
    readonly property string m2sPage: Shell.personalM2svidPage
    property bool m2sOpened: false       // its page loads the first time its tab is picked, then stays
    onEngineChanged: if (engine === 2) m2sOpened = true

    readonly property var ip: Inpainting
    readonly property var st: ip.settings
    // Studio > Use Beginner Mode: v1's rule key -> hidden (beginner.py).
    readonly property var bh: Beginner.hide
    // v1 hides the V2 sub-tab in Beginner Mode; showing it goes back to V1.
    Connections { target: Beginner; function onChanged() { if (page.bh["inp.v2_tab"] && page.engine > 0) page.engine = 0 } }
    // A picked chapter fills every path, so the rows are put away (v1's
    // collapse); Change Folders brings them back without leaving it.
    property bool foldersOpen: false
    readonly property bool chapterMode: ip.hasProject && ip.chapterNum >= 0
    // Manual paths: put away by Hide Folders, remembered (user, 2026-09-30)
    readonly property bool manualHidden: !!Shell.foldersHidden["inpainting"]
    readonly property bool pathsFolded: chapterMode ? !foldersOpen : manualHidden
    function toggleFolders() {
        if (chapterMode) foldersOpen = !foldersOpen
        else Shell.setFoldersHidden("inpainting", !manualHidden)
    }
    readonly property bool idle: !ip.busy

    // v1 fits 787; Post-Processing's checkbox pairs beside Clip Info's
    // heading and Open Console need a little more (840 left Open Console
    // 4px past the panel's edge).
    // the settings (v1's width) and Upcoming Jobs beside them (user, 2026-10-10)
    readonly property int settingsWidth: 760
    readonly property int listMin: 330
    readonly property int requiredWidth: 36 + settingsWidth + Theme.gap + listMin
    readonly property real requiredHeight: engine === 0
        ? v1.y + content.y + content.implicitHeight + Theme.gap + actionBar.implicitHeight + 12
        : engine === 2 && m2s.item ? m2s.y + m2s.item.requiredHeight
        : v2.y + v2.requiredHeight

    transform: Rotation {
        origin.x: page.width / 2; origin.y: page.height / 2
        axis { x: 0; y: 1; z: 0 }
        angle: page.flipAngle
    }

    component IpBox: TextBox { store: page.st; owner: page.ip }
    component IpDrop: Dropdown { store: page.st }
    component IpCheck: CheckField { store: page.st }
    // One half of a v1 parameter row.
    component Half: FieldRow { labelWidth: 150; Layout.fillWidth: true; Layout.preferredWidth: 1 }
    component Spacer: Item { Layout.fillWidth: true; Layout.preferredWidth: 1 }
    function help(k) { return page.ip.help[k] || "" }

    onActiveChanged: if (active) { ip.refreshImport(); ip.refreshChapters() }

    Connections {
        target: page.ip
        function onToast(kind, title, message) { Theme.toast(kind, title, message) }
    }
    function confirm(title, message, yesText, action) {
        page.prompt.ask({
            title: title, message: message,
            buttons: [{ text: yesText, code: 1, kind: "filled" }, { text: "Cancel", code: 0, kind: "ghost" }],
            onDone: (code) => { if (code === 1) action() }
        })
    }

    // ---- dialogs ----------------------------------------------------------
    // one picker for a folder or a single clip (user, 2026-10-05: FolderPicker, Columns)
    FolderPicker {
        id: loFolderDlg; title: "Lo-Res Blend: a folder, or a single clip to inpaint"
        allowFiles: true
        fileTypes: ["mp4", "mov", "mkv"]
        currentFolder: page.st.input_folder
        onAccepted: page.st.input_folder = selectedPath.replace(/\\/g, "/")
    }
    FolderPicker {
        id: hiFolderDlg; title: "Hi-Res Blend: a folder, or a single clip"
        allowFiles: true
        fileTypes: ["mp4", "mov", "mkv"]
        currentFolder: page.st.hires_blend_folder
        onAccepted: page.st.hires_blend_folder = selectedPath.replace(/\\/g, "/")
    }
    FolderPicker {
        id: outFolderDlg; title: "Output Folder"
        currentFolder: Shell.pathToUrl(page.st.output_folder)
        onAccepted: page.st.output_folder = Shell.urlToPath(selectedFolder).replace(/\\/g, "/")
    }
    FileDialog {
        id: loadSettingsDlg; title: "Load Settings from File"
        nameFilters: ["JSON files (*.json)", "All files (*)"]
        onAccepted: page.ip.loadSettingsFrom(Shell.urlToPath(selectedFile))
    }
    FileDialog {
        id: saveSettingsDlg; title: "Save Settings to File"
        fileMode: FileDialog.SaveFile; defaultSuffix: "json"
        nameFilters: ["JSON files (*.json)", "All files (*)"]
        onAccepted: page.ip.saveSettingsTo(Shell.urlToPath(selectedFile))
    }
    // V2's own (user, 2026-10-11: here in Settings as V1's are, off the V2 page)
    FileDialog {
        id: loadV2SettingsDlg; title: "Load V2 Settings from File"
        nameFilters: ["JSON files (*.json)", "All files (*)"]
        onAccepted: InpaintingV2.loadSettingsFrom(Shell.urlToPath(selectedFile))
    }
    FileDialog {
        id: saveV2SettingsDlg; title: "Save V2 Settings to File"
        fileMode: FileDialog.SaveFile; defaultSuffix: "json"
        nameFilters: ["JSON files (*.json)", "All files (*)"]
        onAccepted: InpaintingV2.saveSettingsTo(Shell.urlToPath(selectedFile))
    }
    EncodingDialog { id: encodingDlg; ctl: page.ip }

    // ---- engine switch: v1's sub-tab strip -----------------------------------
    StageTabBar {
        id: engineSwitch
        x: 14
        y: 0
        implicitHeight: 34
        numbered: false
        // the names: the release's, or the user's own copy's (personal/ui_names.py)
        readonly property var names: Shell.personalInpaintingTabs.length >= 3 ? Shell.personalInpaintingTabs
                                     : ["V1 (SVD)", "V2 (Wan VACE)", "M2SVid"]
        model: page.bh["inp.v2_tab"] ? [names[0]]
               : [names[0], names[1]].concat(page.m2sPage !== "" ? [names[2]] : [])
        currentIndex: page.engine
        onActivated: (i) => page.engine = i
    }

    // ---- V1 ------------------------------------------------------------------
    Item {
        id: v1
        y: engineSwitch.y + engineSwitch.height
        width: page.width
        height: page.height - y
        opacity: page.engine === 0 ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.dur(260) } }
        transform: Translate { x: page.engine === 0 ? 0 : -40; Behavior on x { NumberAnimation { duration: Theme.dur(320); easing.type: Easing.OutCubic } } }

        ColumnLayout {
            id: content
            objectName: "pageContent"
            x: 18
            y: 6
            width: parent.width - 36
            spacing: Theme.gap

            Section {
                id: foldersSection
                title: "Folders"
                bodyShown: !page.pathsFolded
                enabled: page.idle
                headerRight: [
                    AccentButton {
                        visible: page.ip.importDescription.length > 0
                        text: "Import from Splatting"
                        kind: "tonal"
                        iconGlyph: Theme.icon.importIcon
                        onClicked: page.ip.importFromSplatting()
                        HoverTip {
                            tip: "Fill the Lo-Res and Hi-Res Blend Folders from the most recent completed "
                                 + "Splatting job, whichever ran last on this machine.\n"
                                 + "Clicking replaces whatever those two fields currently hold.\n\n"
                                 + "Most recent job:\n" + page.ip.importDescription
                        }
                    },
ChangeFoldersButton {
                        open: !page.pathsFolded
                        manual: !page.chapterMode
                        onClicked: page.toggleFolders()
                        paths: [{ label: "Lo-Res Blend Folder", value: page.st.input_folder },
                                { label: "Hi-Res Blend Folder", value: page.st.hires_blend_folder },
                                { label: "Output Folder", value: page.st.output_folder }]
                    },
                    ChapterPicker { visible: page.ip.hasProject; dc: page.ip; onPicked: page.foldersOpen = false }
                ]
                FieldRow {
                    visible: !page.pathsFolded
                    label: "Lo-Res Blend Folder"
                    help: page.help("input_folder")
                    labelWidth: 150
                    RowLayout {
                        width: parent.width
                        spacing: 8
                        IpBox { key: "input_folder"; numeric: false; Layout.fillWidth: true }
                        AccentButton { objectName: "browseLoRes"; text: "Browse"; kind: "outline"; onClicked: loFolderDlg.open()
                                       HoverTip { tip: "Pick a folder to inpaint every clip in it, or a single clip to inpaint just that one." } }
                    }
                }
                FieldRow {
                    visible: !page.pathsFolded
                    label: "Hi-Res Blend Folder"
                    help: page.help("hires_blend_folder")
                    labelWidth: 150
                    RowLayout {
                        width: parent.width
                        spacing: 8
                        IpBox { key: "hires_blend_folder"; numeric: false; Layout.fillWidth: true }
                        AccentButton { objectName: "browseHiRes"; text: "Browse"; kind: "outline"; onClicked: hiFolderDlg.open()
                                       HoverTip { tip: "Pick the hi-res blend folder, or a single hi-res clip." } }
                    }
                }
                FieldRow {
                    visible: !page.pathsFolded
                    label: "Output Folder"
                    help: page.help("output_folder")
                    labelWidth: 150
                    RowLayout {
                        width: parent.width
                        spacing: 8
                        IpBox { key: "output_folder"; numeric: false; Layout.fillWidth: true }
                        AccentButton { text: "Browse Folder"; kind: "outline"; onClicked: outFolderDlg.open() }
                    }
                }
            }

            // The settings in v1's width on the left, Upcoming Jobs on the right (user,
            // 2026-10-10: the room the wide rows left empty), as tall as the settings
            RowLayout {
                id: settingsRow
                objectName: "settingsAndQueue"
                Layout.fillWidth: true
                spacing: Theme.gap
                ColumnLayout {
                    Layout.preferredWidth: page.settingsWidth
                    Layout.maximumWidth: page.settingsWidth
                    Layout.alignment: Qt.AlignTop
                    spacing: Theme.gap

                    // v1's Parameters grid, same pairings row for row.
                    Section {
                        title: "Parameters"
                        enabled: page.idle
                        PairRow {
                            Half { label: "Inference Steps"; help: page.help("num_inference_steps"); IpBox { key: "num_inference_steps" } }
                            Half { label: "Original Input Bias"; help: page.help("original_input_blend_strength"); IpBox { key: "original_input_blend_strength" } }
                        }
                        // Guidance Scale under Original Input Bias (user, 2026-10-10: on the page, not in
                        // Settings; hidden in Beginner Mode, its cell left empty); the right column moved
                        // down a row, each pair kept together
                        PairRow {
                            Half { label: "Tile Number"; help: page.help("tile_num"); IpBox { key: "tile_num" } }
                            Spacer { visible: !!page.bh["inp.guidance_scale"] }
                            Half {
                                objectName: "guidanceScaleRow"
                                label: "Guidance Scale"; visible: !page.bh["inp.guidance_scale"]; help: page.help("guidance_scale")
                                IpBox { objectName: "guidanceScaleBox"; key: "guidance_scale" }
                            }
                        }
                        PairRow {
                            Half { label: "Frames Chunk"; help: page.help("frames_chunk"); IpBox { key: "frames_chunk" } }
                            Half { label: "Frame Overlap"; help: page.help("frame_overlap"); IpBox { key: "frame_overlap" } }
                        }
                        PairRow {
                            Half {
                                label: "Inpaint Direction"; help: page.help("inpaint_direction")
                                IpDrop { key: "inpaint_direction"; model: page.ip.choices.inpaint_direction; boxWidth: 130 }
                            }
                            Half {
                                label: "CPU Offload"; help: page.help("offload_type")
                                IpDrop { key: "offload_type"; model: page.ip.choices.offload_type; boxWidth: 130 }
                            }
                        }
                        PairRow {
                            // v1 hides Process Length in Beginner Mode: the row goes with it
                            shown: !page.bh["inp.process_length"]
                            Half { label: "Process Length"; help: page.help("process_length"); IpBox { key: "process_length" } }
                            Spacer {}
                        }
                        PairRow {
                            // Torch Compile where Single Clip ID was (user, 2026-10-11: Upcoming Jobs'
                            // Inpaint This Clip Now does one clip better); it was File > Optimise Model.
                            // TensorRT beside it (user, 2026-10-29): the two don't go together, so either
                            // one ticked hides the other (both shown if a saved config has both on)
                            RowLayout {
                                Layout.fillWidth: true; Layout.preferredWidth: 1
                                spacing: 16
                                IpCheck {
                                    key: "use_compile"; text: "Torch Compile"
                                    // hidden with SageAttention 1 (RTX 20): its Triton 3.2 breaks it
                                    shown: !Theme.sage1 && (!page.st.use_tensorrt || !!page.st.use_compile)
                                    help: "Compiles the model for this GPU when a run starts: each chunk is about 13% faster (with SageAttention), but the first chunk of each run takes about 10 minutes longer while it compiles. Worth it for long unattended runs such as a whole chapter, not for a clip or two. TensorRT is hidden while this is on: the two don't go together."
                                }
                                IpCheck {
                                    key: "use_tensorrt"; text: "TensorRT"
                                    shown: Theme.sage1 || !page.st.use_compile || !!page.st.use_tensorrt
                                    // not installed: to Settings > Studio > Models to install it (user, 2026-10-29)
                                    guard: () => Theme.needModel("tensorrt", "TensorRT isn't installed yet",
                                                                 "Install NVIDIA TensorRT here, then tick TensorRT again.")
                                    help: "Runs the model through NVIDIA TensorRT: chunks are about 1.5 to 1.8 times faster, against Torch Compile's 13%. The first chunk at a new frame or tile size waits a few minutes while its engines are built (about 3 GB of disk per size, kept in trt_engines and reused).\n\nThe normal way (PyTorch with SageAttention, compiled or not) is slightly more accurate. TensorRT works in half precision, so its fill comes out a touch different: on average under half a level out of 255 inside the holes, about a fifth of a level along their edges. That is far below what the eye can see, even flipping between the two, and the rest of the frame is untouched.\n\nNeeds TensorRT installed once (Settings > Studio > Models: NVIDIA TensorRT, Install); without it Inpainting runs the normal way. Torch Compile is hidden while this is on: the two don't go together."
                                }
                            }
                            Half { label: "Inpaint Pre Thresh"; shown: !page.bh["inp.pre_thresh"]; help: page.help("inpaint_mask_initial_threshold"); IpBox { key: "inpaint_mask_initial_threshold" } }
                        }
                        PairRow {
                            shown: !page.bh["inp.post_thresh"] || !page.bh["inp.morph_close"]
                            Half { label: "Inpaint Post Thresh"; help: page.help("inpaint_mask_post_threshold"); IpBox { key: "inpaint_mask_post_threshold" } }
                            Half { label: "Inpaint Morph Close"; help: page.help("inpaint_mask_morph_kernel_size"); IpBox { key: "inpaint_mask_morph_kernel_size" } }
                        }
                        PairRow {
                            shown: !page.bh["inp.dilate_kernel"] || !page.bh["inp.blur_kernel"]
                            Half { label: "Inpaint Dilate Kernel"; help: page.help("inpaint_mask_dilate_kernel_size"); IpBox { key: "inpaint_mask_dilate_kernel_size" } }
                            Half { label: "Inpaint Blur Kernel"; help: page.help("inpaint_mask_blur_kernel_size"); IpBox { key: "inpaint_mask_blur_kernel_size" } }
                        }
                    }

                    // Post-Processing beside Current Clip Info (v1's 3 : 2; Clip Info a set width now)
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.gap

                        Section {
                            title: "Post-Processing"
                            enabled: page.idle
                            Layout.alignment: Qt.AlignTop
                            Layout.fillWidth: true
                            // the checkboxes in one column (user, 2026-10-10: side by side they
                            // ran out of room beside Upcoming Jobs)
                            // no Post-Inpainting Blend (user, 2026-10-11): Merging blends the
                            // fill in with more control. Its mask rows went with it.
                            IpCheck { key: "enable_color_transfer"; shown: !page.bh["inp.color_transfer"]; text: "Enable Color Transfer"; help: page.help("enable_color_transfer") }
                            IpCheck { key: "keep_inpaint_cache"; shown: !page.bh["inp.keep_cache"]; text: "Keep Inpaint Cache For Re-Merge"; help: page.help("keep_inpaint_cache") }
                            IpCheck { key: "move_to_finished"; text: "Resume"; help: page.help("move_to_finished") }
                        }

                        ClipInfo {
                            Layout.alignment: Qt.AlignTop
                            // a set width (2026-10-10): beside Upcoming Jobs, a 3 : 2 split cut
                            // Post-Processing's checkboxes off
                            Layout.fillWidth: false
                            Layout.preferredWidth: 330         // its heading and Open Console
                            labelWidth: 108
                            consoleRef: page.consoleRef
                            rows: [
                                { label: "Name", value: page.ip.clipName },
                                { label: "Resolution", value: page.ip.clipResolution },
                                { label: "Frames", value: page.ip.clipFrames },
                                { label: "Overlap", value: page.ip.clipOverlap },
                                { label: "Input Bias", value: page.ip.clipBias },
                                { label: "Batch Progress", value: page.ip.progressCount || " ", help: page.help("processing_clip_count") }
                            ]
                        }
                    }
                }
                // Upcoming Jobs' place in the row: the list itself is laid over it (below), so it
                // can run on down to the Progress box without making the page ask for more height
                Item {
                    id: queueSlot
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumWidth: page.listMin
                }
            }
        }

        JobQueue {
            objectName: "ipJobQueue"
            // over its slot in the row; down to the Progress box when the window has the room
            // (user, 2026-10-10), never shorter than the settings
            x: content.x + settingsRow.x + queueSlot.x
            y: content.y + settingsRow.y
            width: queueSlot.width
            height: Math.max(queueSlot.height, actionBar.y - Theme.gap - y)
            clip: true
            source: page.ip.jobClips
            ctl: page.ip
            busy: page.ip.busy
            // only ever the job's clips ("N/A" between them, which names none)
            trustRunning: true
            running: page.ip.busy && page.ip.clipName && page.ip.clipName !== "N/A" ? String(page.ip.clipName) : ""
            progress: typeof JobStats !== "undefined" && JobStats.stage === "inpainting" && JobStats.owner === "v1"
                      ? JobStats.fraction : 0
            countText: page.ip.progressCount || ""
            rowActions: [{ id: "run", text: "Inpaint This Clip Now" }, { id: "test", text: "Test Render" },
                         { id: "finished", text: "Move to Finished" },
                         { id: "send", text: "Send to Merging", whenDone: true }]
            onRowAction: (id, path) => {
                if (id === "run") page.ip.startClip(path)
                else if (id === "test") page.ip.testClip(path)          // into Test Renders (user, 2026-10-15)
                else if (id === "finished") page.ip.jobClips.moveToFinished(path)
                else if (id === "send") sendOn(path, page.ip, "merging", Merging, 3)
            }
        }

        ActionBar {
            id: actionBar
            statsStage: "inpainting"
            statsOwner: "v1"        // V1's job only: V2 and M2S show their own (user, 2026-10-24)
            framesLabel: "frames inpainted"
            // workflow and troubleshooting, typed out above the bar (user, 2026-09-27)
            tips: Tips.forStage("inpainting")      // studio/qml/Studio/tips.js: Settings > Tips lists them all
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.margins: 12
            busy: page.ip.busy
            done: page.ip.progressDone
            total: page.ip.progressTotal
            status: page.ip.status
            jobTime: false      // "Processing completed in ..." says it already
            title: "Progress"

            AccentButton { text: "Start"; iconGlyph: Theme.icon.play; enabled: page.idle; onClicked: page.ip.start() }
            AccentButton {
                objectName: "ipStop"
                text: "Stop"; kind: "outline"; enabled: page.ip.busy
                // asks, then stops now (audit #13); Stop After Clip is the gentle way
                onClicked: (page.prompt ? page.prompt.askStop({ stage: "Inpainting", safeText: "Stop after this chunk", safeDetail: "the chunk being inpainted finishes and is kept; the next Start on this clip carries on from there. The rest of the folder is left for the next Start.", onSafe: () => page.ip.stopAtChunk(), onYes: () => page.ip.stop() })
                         : page.ip.stop())
            }
            AccentButton {
                objectName: "ipStopAfter"
                text: page.ip.stopAfterClip ? "Stopping After Clip" : "Stop After Clip"
                kind: page.ip.stopAfterClip ? "tonal" : "outline"
                // only while a folder of clips is running (user, 2026-09-28)
                visible: page.ip.busy && page.ip.multiClip
                onClicked: page.ip.toggleStopAfterClip()
                HoverTip {
                    tip: "Let the clip being processed finish, then stop before the next one.\n"
                         + "The finished clips are complete; the rest of the folder is\n"
                         + "left for the next Start. Click again to carry on instead."
                }
            }
            AccentButton { text: "Exit"; kind: "outline"; onClicked: page.Window.window.close() }

            trailing: [
                // Never disabled: checking finished clips mid-batch is the point (v1).
                AccentButton {
                    text: "View Output Folder"
                    kind: "outline"
                    onClicked: page.ip.openOutputFolder()
                    HoverTip { tip: page.help("open_output_folder") }
                }
            ]
        }
    }

    // ---- V2 ------------------------------------------------------------------
    InpaintingV2Page {
        id: v2
        objectName: "inpaintingV2Page"
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

    // M2SVid's page, from personal/m2svid (not released): loaded the first time its tab is picked
    Loader {
        id: m2s
        objectName: "inpaintingM2sPage"
        active: page.m2sPage !== "" && page.m2sOpened
        source: page.m2sPage
        y: engineSwitch.y + engineSwitch.height
        width: page.width
        height: page.height - y
        onLoaded: {
            item.prompt = Qt.binding(() => page.prompt)
            // V1's foot on it (user, 2026-10-24): Inpainting's tips and the console's Open Console
            item.tips = Tips.forStage("inpainting")
            item.consoleRef = Qt.binding(() => page.consoleRef)
        }
        opacity: page.engine === 2 ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.dur(260) } }
        transform: Translate { x: page.engine === 2 ? 0 : 40; Behavior on x { NumberAnimation { duration: Theme.dur(320); easing.type: Easing.OutCubic } } }
    }

    // v1's menus for this stage. Main puts them on the menu bar after the
    // Studio / View / GPU menus while this tab is showing.
    readonly property var stageMenus: [fileMenu, optionsMenu, helpMenu]

    StudioMenu {
        id: fileMenu
        title: "File"
        MenuItem { text: "Load Settings..."; enabled: page.idle; onTriggered: loadSettingsDlg.open() }
        MenuItem { text: "Save Settings..."; enabled: page.idle; onTriggered: saveSettingsDlg.open() }
        MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: Theme.border } }
        MenuItem {
            text: "Use " + Theme.sageName
            checkable: true
            checked: !!page.st.use_sageattention
            onTriggered: page.st.use_sageattention = checked
        }
        MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: Theme.border } }
        MenuItem {
            text: "Reset to Default"
            property string tip: "Puts every setting on this stage back to its default. Asks first."
            enabled: page.idle
            onTriggered: page.confirm("Reset Settings", "Are you sure you want to reset all settings to their default values?",
                                      "Reset", () => page.ip.resetDefaults())
        }
        MenuItem {
            text: "Restore Finished"
            property string tip: "Moves the inputs already processed, from the 'finished' folders, back into their input folders so they can be run again."
            enabled: page.idle
            onTriggered: page.confirm("Restore Finished Files",
                                      "Are you sure you want to move all processed videos from the 'finished' folders back to their respective input directories?",
                                      "Restore", () => page.ip.restoreFinished())
        }
        // v1's shell appends V2's own toggle here ("(for V2)": V1's is above).
        MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: Theme.border } }
        MenuItem {
            text: "Use " + Theme.sageName + " (for V2)"
            checkable: true
            checked: !!InpaintingV2.settings.use_sageattention
            onTriggered: InpaintingV2.settings.use_sageattention = checked
        }
        MenuItem {
            text: "Load V2 Settings..."
            property string tip: "Reads Inpainting V2's settings from a file saved with Save V2 Settings."
            enabled: !InpaintingV2.busy && !InpaintingV2.loading
            onTriggered: loadV2SettingsDlg.open()
        }
        MenuItem {
            text: "Save V2 Settings..."
            property string tip: "Writes Inpainting V2's settings to a file, to keep a setup or take it to another PC."
            enabled: !InpaintingV2.busy && !InpaintingV2.loading
            onTriggered: saveV2SettingsDlg.open()
        }
    }

    StudioMenu {
        id: optionsMenu
        title: "Options"
        MenuItem { text: "Encoding Settings..."; onTriggered: encodingDlg.open() }
    }

    StudioMenu {
        id: helpMenu
        title: "Help"
        MenuItem {
            text: "Enable Debugging"
            checkable: true
            checked: !!page.st.debug_mode
            onTriggered: page.st.debug_mode = checked
        }
        MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: Theme.border } }
        MenuItem {
            text: "About"
            onTriggered: page.prompt.ask({
                title: "About Batch Video Inpainting",
                message: "Batch Video Inpainting Application
Version: 1.0
"
                         + "This tool processes 'splatted' videos to fill occlusions using a Stable Video Diffusion inpainting pipeline.
"
                         + "It supports custom mask processing, color transfer and a hi-res finish for high-quality outputs.",
                buttons: [{ text: "OK", code: 1, kind: "filled" }] })
        }
    }

    // drag and drop (user, 2026-10-25): Drop Zones over the folders
    DropZones {
        over: foldersSection
        store: page.st
        targets: [{ key: "input_folder", label: "Lo-Res Blend", folder: true, files: ["mp4", "mov", "mkv"] },
                  { key: "hires_blend_folder", label: "Hi-Res Blend", folder: true, files: ["mp4", "mov", "mkv"] },
                  { key: "output_folder", label: "Output Folder", folder: true }]
    }
}
