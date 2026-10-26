import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Layouts
import Studio
import "tips.js" as Tips

// Video Depth Anything: DepthCrafter's second tab (user, 2026-10-25), arranged as DepthCrafter's page:
//
//   Input Source (its own folders)
//   Video Depth Anything | Frames & Output | Upcoming
//   Depth Range     | Current Clip Info | Jobs
//   [ action bar: progress, status, Start / Stop, View Output Folder ]
//
// The work is stages/vda (controller, runner) over core/vda. Its maps land where DepthCrafter's do,
// under the same name; a DepthCrafter map of the clip already there moves into DepthCrafter/.
Item {
    id: page
    property var prompt: null
    property var consoleRef: null

    readonly property var vda: VDA
    readonly property var st: vda.settings
    property bool foldersOpen: false
    readonly property bool chapterMode: vda.hasProject && vda.chapterNum >= 0
    readonly property bool manualHidden: !!Shell.foldersHidden["vda"]
    readonly property bool pathsFolded: chapterMode ? !foldersOpen : manualHidden
    function toggleFolders() {
        if (chapterMode) foldersOpen = !foldersOpen
        else Shell.setFoldersHidden("vda", !manualHidden)
    }
    readonly property bool idle: !vda.busy

    readonly property real colMin: 370
    readonly property real listMin: 330
    readonly property int requiredWidth: 36 + 2 * (colMin + Theme.gap) + listMin
    readonly property real requiredHeight: content.y + content.implicitHeight + Theme.gap + actionBar.implicitHeight + 12

    component VBox: TextBox { store: page.st; owner: page.vda }
    component VDrop: Dropdown { store: page.st }
    component VCheck: CheckField { store: page.st }
    component Caption: Text {
        color: Theme.fgMuted
        font.family: Theme.bodyFont
        font.pixelSize: Theme.bodySize
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    }
    function help(k) { return page.vda.help[k] || "" }

    Connections {
        target: page.vda
        function onToast(kind, title, message) { Theme.toast(kind, title, message) }
    }
    function confirm(title, message, yesText, action) {
        page.prompt.ask({
            title: title, message: message,
            buttons: [{ text: yesText, code: 1, kind: "filled" }, { text: "Cancel", code: 0, kind: "ghost" }],
            onDone: (code) => { if (code === 1) action() }
        })
    }

    // The picked size's download, run by Settings > Studio > Models (as the Depth Map Editor's SAM 3)
    readonly property var dl: {
        const d = typeof Models !== "undefined" && Models ? Models.download : ({})
        const key = page.vda.weightsKey
        const running = d.key === key
        const queued = (d.queued || []).indexOf(key) >= 0
        const err = d.error && d.error.key === key ? d.error.text : ""
        return { on: running || queued, running: running, progress: d.progress || 0, text: d.text || "", error: err }
    }
    Connections {
        target: typeof Models !== "undefined" ? Models : null
        ignoreUnknownSignals: true
        function onDownloadChanged() { page.vda.recheckWeights() }
        function onChanged() { page.vda.recheckWeights() }
    }

    FolderPicker {
        id: inputFolderDlg
        title: "Input: a folder of videos, or a single video"
        allowFiles: true
        fileTypes: ["mp4", "avi", "mov", "mkv", "webm", "flv", "gif"]
        currentFolder: page.st.input_dir_or_file_var
        onAccepted: page.st.input_dir_or_file_var = selectedPath
    }
    // the user's own checkpoints (iw3's): the folder, or iw3's own -- its checkpoints folder is found inside
    FolderPicker {
        id: weightsDirDlg
        title: "Video Depth Anything weights: a folder of .pth files, or your iw3 folder"
        currentFolder: Shell.pathToUrl(page.vda.weightsDir)
        onAccepted: page.vda.setWeightsDir(Shell.urlToPath(selectedFolder))
    }
    FolderPicker {
        id: outputFolderDlg
        title: "Output folder"
        currentFolder: Shell.pathToUrl(page.st.output_dir)
        onAccepted: page.st.output_dir = Shell.urlToPath(selectedFolder)
    }
    FileDialog {
        id: loadSettingsDlg
        title: "Load Settings File"
        nameFilters: ["JSON files (*.json)", "All files (*)"]
        onAccepted: page.vda.loadSettingsFrom(Shell.urlToPath(selectedFile))
    }
    FileDialog {
        id: saveSettingsDlg
        title: "Save Settings As"
        fileMode: FileDialog.SaveFile
        defaultSuffix: "json"
        nameFilters: ["JSON files (*.json)", "All files (*)"]
        onAccepted: page.vda.saveSettingsTo(Shell.urlToPath(selectedFile))
    }

    ColumnLayout {
        id: content
        objectName: "vdaContent"
        x: 18
        y: 6
        width: page.width - 36
        spacing: Theme.gap

        Section {
            id: inputSourceSection
            title: "Input Source"
            bodyShown: !page.pathsFolded
            enabled: page.idle
            headerRight: [
                AccentButton {
                    visible: page.vda.sceneCutDescription.length > 0
                    text: "Import from Scene Cut"
                    kind: "tonal"
                    iconGlyph: Theme.icon.importIcon
                    onClicked: page.vda.importFromSceneCut()
                    HoverTip {
                        tip: "Fill Input Folder/File from the most recent Scene Cut split.\n"
                             + "Clicking replaces whatever that field currently holds.\n\n"
                             + "Most recent job:\n" + page.vda.sceneCutDescription
                    }
                },
                ChangeFoldersButton {
                    open: !page.pathsFolded
                    manual: !page.chapterMode
                    onClicked: page.toggleFolders()
                    paths: [{ label: "Input Folder/File", value: page.st.input_dir_or_file_var },
                            { label: "Output Folder", value: page.st.output_dir }]
                },
                ChapterPicker { visible: page.vda.hasProject; dc: page.vda; onPicked: page.foldersOpen = false }
            ]
            FieldRow {
                visible: !page.pathsFolded
                label: "Input Folder/File"
                help: page.help("input_dir_or_file")
                labelWidth: 130
                RowLayout {
                    width: parent.width
                    spacing: 8
                    VBox { objectName: "vdaInput"; key: "input_dir_or_file_var"; numeric: false; Layout.fillWidth: true }
                    AccentButton { text: "Browse"; kind: "outline"; onClicked: inputFolderDlg.open()
                                   HoverTip { tip: "Pick a folder to process every video in it, or a single video to process just that one." } }
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
                    VBox { objectName: "vdaOutput"; key: "output_dir"; numeric: false; Layout.fillWidth: true }
                    AccentButton { text: "Browse Folder"; kind: "outline"; onClicked: outputFolderDlg.open() }
                }
            }
        }

        Item {
            id: grid
            Layout.fillWidth: true
            implicitHeight: Math.max(col0.implicitHeight, col1.implicitHeight)
            readonly property real colW: page.colMin
            readonly property real listX: 2 * (colW + Theme.gap)

            ColumnLayout {
                id: col0
                x: 0
                width: grid.colW
                spacing: Theme.gap

                Section {
                    title: "Video Depth Anything"      // user, 2026-10-25 (was Model)
                    enabled: page.idle
                    FieldRow {
                        label: "Model"
                        help: page.help("model_size")
                        VDrop { objectName: "vdaModelSize"; key: "model_size"; model: page.vda.choices.model_size }
                    }
                    // the weights it uses: Studio's models folder, or a folder of the user's own -- iw3's, which
                    // most users have already (user, 2026-10-25). Shown only until the size picked is found
                    // ("hide the weights folder selector once they're successfully loaded in"); File > VDA
                    // Weights Folder changes it after.
                    FieldRow {
                        objectName: "vdaWeightsRow"
                        shown: !page.vda.weightsInstalled
                        label: "Weights"
                        help: page.help("weights_dir")
                        RowLayout {
                            width: parent.width
                            spacing: 6
                            Text {
                                objectName: "vdaWeightsDir"
                                Layout.fillWidth: true
                                text: page.vda.weightsDir !== "" ? page.vda.weightsDirLabel : "Studio's models folder"
                                elide: Text.ElideRight
                                color: page.vda.weightsDir !== "" ? Theme.fg : Theme.fgMuted
                                font.family: Theme.bodyFont
                                font.pixelSize: Theme.bodySize
                                HoverTip {
                                    tip: page.vda.weightsDir !== ""
                                         ? page.vda.weightsDir.replace(/\//g, "\\") + "\nHas: " + (page.vda.weightsDirSizes || "none now")
                                           + "\nA size not there comes from Studio's models folder."
                                         : "Studio's own models folder (Settings > Studio > Models). Browse to use weights you already have, iw3's."
                                }
                            }
                            AccentButton {
                                objectName: "vdaWeightsBrowse"
                                kind: "outline"; iconGlyph: Theme.icon.folder; implicitWidth: 34
                                onClicked: weightsDirDlg.open()
                                HoverTip { tip: "Pick a folder of Video Depth Anything .pth files, or your iw3 folder: Studio finds its checkpoints folder inside." }
                            }
                            AccentButton {
                                objectName: "vdaWeightsClear"
                                visible: page.vda.weightsDir !== ""
                                kind: "ghost"; iconGlyph: Theme.icon.close; implicitWidth: 30
                                onClicked: page.vda.clearWeightsDir()
                                HoverTip { tip: "Back to Studio's models folder only." }
                            }
                        }
                    }
                    // the picked size not downloaded yet: its download, here
                    ColumnLayout {
                        objectName: "vdaWeightsMissing"
                        visible: !page.vda.weightsInstalled
                        Layout.fillWidth: true
                        spacing: 6
                        Caption {
                            text: page.dl.error !== "" ? page.dl.error
                                  : page.dl.running ? page.dl.text
                                  : page.dl.on ? "Waiting for the download running in Settings > Studio > Models."
                                  : "Video Depth Anything " + page.st.model_size + " isn't in the models folder yet"
                                    + (page.vda.weightsDir !== "" ? " or your weights folder." : ". Have it from iw3? Browse to its folder above.")
                        }
                        RowLayout {
                            spacing: 8
                            Layout.fillWidth: true
                            AccentButton {
                                objectName: "vdaDownload"
                                visible: !page.dl.on
                                text: "Download Weights"
                                kind: "tonal"
                                onClicked: {
                                    const why = Models.start(page.vda.weightsKey)
                                    if (why) Theme.toast("warning", "Download Weights", why)
                                }
                                HoverTip { tip: "Downloads this size's checkpoint from Hugging Face into the models folder "
                                                + "(Small 116 MB, Base 458 MB, Large 1.5 GB). Settings > Studio > Models lists them all." }
                            }
                            ProgressBar {
                                visible: page.dl.running
                                Layout.fillWidth: true
                                from: 0; to: 1; value: page.dl.running ? page.dl.progress : 0
                                background: Rectangle { implicitHeight: 6; radius: 3; color: Theme.trough }
                                contentItem: Item { Rectangle { width: parent.parent.visualPosition * parent.width; height: 6; radius: 3; color: Theme.accent } }
                            }
                            AccentButton {
                                visible: page.dl.on
                                text: "Stop"; kind: "ghost"
                                onClicked: page.dl.running ? Models.stop() : Models.unqueue(page.vda.weightsKey)
                            }
                        }
                    }
                    // typed, or picked from the sizes the model takes (user, 2026-10-25: "a dropdown of compatible
                    // resolution numbers" -- "allow the box to be custom typed too"); a typed size snaps up to a
                    // multiple of 14 (settings.coerce: 512 -> 518)
                    FieldRow {
                        label: "Input Size"
                        help: page.help("input_size")
                        RowLayout {
                            spacing: 6
                            VBox { objectName: "vdaInputSize"; key: "input_size"; boxWidth: 80 }
                            AccentButton {
                                id: sizesBtn
                                objectName: "vdaInputSizes"
                                kind: "outline"
                                iconGlyph: Theme.icon.down
                                implicitWidth: 34
                                onClicked: sizesMenu.opened ? sizesMenu.close() : sizesMenu.popup(sizesBtn, 0, sizesBtn.height + 4)
                                HoverTip { tip: "The sizes the model takes (multiples of 14). 518 is what it's trained at; 392 is iw3's default." }
                            }
                            StudioMenu {
                                id: sizesMenu
                                objectName: "vdaInputSizesMenu"
                                Instantiator {
                                    model: page.vda.choices.input_size
                                    delegate: MenuItem {
                                        required property var modelData
                                        objectName: "vdaInputSizeItem"
                                        text: String(modelData) + (modelData === 518 ? "  (trained)" : modelData === 392 ? "  (iw3 default)" : "")
                                        onTriggered: page.st.input_size = modelData
                                    }
                                    onObjectAdded: (i, item) => sizesMenu.insertItem(i, item)
                                    onObjectRemoved: (i, item) => sizesMenu.removeItem(item)
                                }
                            }
                        }
                    }
                    FieldRow {
                        label: "Mode"
                        help: page.help("run_mode")
                        VDrop { objectName: "vdaRunMode"; key: "run_mode"; model: page.vda.choices.run_mode }
                    }
                    VCheck { key: "use_fp16"; text: "Half Precision (FP16)"; help: page.help("use_fp16") }
                }

                Section {
                    title: "Depth Range"
                    enabled: page.idle
                    FieldRow {
                        label: "Normalise"
                        help: page.help("normalise_mode")
                        VDrop { objectName: "vdaNormalise"; key: "normalise_mode"; model: page.vda.choices.normalise_mode }
                    }
                    FieldRow {
                        label: "   ↳ Decay / Buffer"
                        help: page.help("ema")
                        shown: page.st.normalise_mode === "EMA"
                        RowLayout {
                            spacing: 8
                            VBox { key: "ema_decay"; boxWidth: 72 }
                            VBox { key: "ema_buffer"; boxWidth: 72 }
                        }
                    }
                }
            }

            ColumnLayout {
                id: col1
                x: grid.colW + Theme.gap
                width: grid.colW
                spacing: Theme.gap

                Section {
                    title: "Frames & Output"
                    enabled: page.idle
                    FieldRow { label: "Process Max Frames (-1 All)"; labelWidth: 176; help: page.help("process_length"); VBox { key: "process_length" } }
                    FieldRow { label: "Output Suffix"; labelWidth: 176; help: page.help("output_suffix"); VBox { key: "output_suffix"; numeric: false; boxWidth: 150 } }
                    VCheck { objectName: "vdaResume"; key: "move_to_finished"; text: "Resume"; help: page.help("move_to_finished") }
                }

                ClipInfo {
                    consoleRef: page.consoleRef
                    rows: [
                        { label: "Batch Progress", value: page.vda.progressCount || " ", help: "Which clip of the batch is running, and how many there are." },
                        { label: "Filename", value: page.vda.clipName, help: "" },
                        { label: "Resolution", value: page.vda.clipResolution, help: "" },
                        { label: "Frames", value: page.vda.clipFrames, help: "" }
                    ]
                }
            }

            JobQueue {
                objectName: "vdaJobQueue"
                x: grid.listX
                width: Math.max(0, grid.width - x)
                height: Math.max(grid.height, actionBar.y - Theme.gap - content.y - grid.y)
                clip: true
                source: page.vda.jobClips
                ctl: page.vda
                busy: page.vda.busy
                trustRunning: true
                running: page.vda.busy && page.vda.clipName && page.vda.clipName !== "N/A" ? String(page.vda.clipName) : ""
                progress: page.vda.framesTotal > 0
                          ? (page.vda.framesPhase === "encode" ? 0.85 + 0.15 * page.vda.framesDone / page.vda.framesTotal
                                                               : 0.85 * page.vda.framesDone / page.vda.framesTotal) : 0
                countText: page.vda.progressCount || ""
                rowActions: [{ id: "run", text: "Process This Clip Now" }, { id: "test", text: "Test Render" },
                             { id: "finished", text: "Move to Finished" },
                             { id: "send", text: "Send to Splatting", whenDone: true }]
                onRowAction: (id, path) => {
                    if (id === "run") page.vda.startClip(path)
                    else if (id === "test") page.vda.testClip(path)
                    else if (id === "finished") page.vda.jobClips.moveToFinished(path)
                    else if (id === "send") sendOn(path, page.vda, "splatting", Splatting, 1)
                }
            }
        }
    }

    ActionBar {
        id: actionBar
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: 12
        tips: Tips.forStage("vda")
        busy: page.vda.busy
        // the clip's frames, depth then encode (the batch's count is in Current Clip Info)
        done: page.vda.busy ? page.vda.framesDone : page.vda.progressDone
        total: page.vda.busy ? page.vda.framesTotal : page.vda.progressTotal
        status: page.vda.busy && page.vda.framesTotal > 0
                ? (page.vda.framesPhase === "encode" ? "Encoding " : "Depth ") + page.vda.framesDone + " / "
                  + page.vda.framesTotal + " frames" + (page.vda.progressCount ? "  ·  clip " + page.vda.progressCount : "")
                : page.vda.status

        AccentButton {
            objectName: "vdaStart"
            text: "Start"
            iconGlyph: Theme.icon.play
            enabled: page.idle
            onClicked: page.vda.start()
        }
        AccentButton {
            objectName: "vdaStop"
            text: "Stop"
            kind: "outline"
            enabled: page.vda.busy
            onClicked: (page.prompt ? page.prompt.askStop({ stage: "Video Depth Anything", safeText: "Stop after this clip", safeDetail: "the clip being processed finishes and its map is kept; the rest of the folder is left for the next Start.", onSafe: () => page.vda.stopAfterClip(), onYes: () => page.vda.stop() })
                         : page.vda.stop())
        }

        trailing: [
            AccentButton {
                text: "View Output Folder"
                kind: "outline"
                onClicked: page.vda.openOutputFolder()
            }
        ]
    }

    readonly property var stageMenus: [fileMenu]

    StudioMenu {
        id: fileMenu
        title: "File"
        MenuItem { text: "Load Settings..."; enabled: page.idle; onTriggered: loadSettingsDlg.open() }
        MenuItem { text: "Save Settings As..."; enabled: page.idle; onTriggered: saveSettingsDlg.open() }
        MenuItem {
            text: "Reset to Default"
            property string tip: "Puts every Video Depth Anything setting back to its default. Asks first."
            enabled: page.idle
            onTriggered: page.confirm("Reset Settings", "Are you sure you want to reset all Video Depth Anything settings to their default values?",
                                      "Reset", () => page.vda.resetDefaults())
        }
        MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: Theme.border } }
        // the Weights row folds away once the weights are found: changed or cleared here after (user, 2026-10-25)
        MenuItem {
            objectName: "vdaWeightsMenu"
            text: "VDA Weights Folder..."
            property string tip: page.vda.weightsDir !== ""
                                 ? "Now: " + page.vda.weightsDir.replace(/\//g, "\\") + " (" + (page.vda.weightsDirSizes || "none") + ")"
                                 : "Use Video Depth Anything weights you already have -- pick your iw3 folder. Now: Studio's models folder."
            enabled: page.idle
            onTriggered: weightsDirDlg.open()
        }
        MenuItem {
            objectName: "vdaWeightsMenuClear"
            text: "Use Studio's Models Folder Only"
            enabled: page.idle && page.vda.weightsDir !== ""
            onTriggered: page.vda.clearWeightsDir()
        }
        MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: Theme.border } }
        MenuItem {
            text: "Restore Finished Input Files..."
            enabled: page.idle
            onTriggered: page.confirm("Restore Finished Input Files",
                                      "Move the original input files from the 'Finished' subfolder back to their original input directory?",
                                      "Restore", () => page.vda.restoreInputs("finished"))
        }
        MenuItem {
            text: "Restore Failed Input Files..."
            enabled: page.idle
            onTriggered: page.confirm("Restore Failed Input Files",
                                      "Move the original input files from the 'Failed' subfolder back to their original input directory?",
                                      "Restore", () => page.vda.restoreInputs("failed"))
        }
    }

    DropZones {
        over: inputSourceSection
        store: page.st
        targets: [{ key: "input_dir_or_file_var", label: "Input", folder: true, slash: false,
                    files: ["mp4", "avi", "mov", "mkv", "webm", "flv", "gif"] },
                  { key: "output_dir", label: "Output Folder", folder: true, slash: false }]
    }
}
