import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Layouts
import Studio
import "tips.js" as Tips

// DVD: Depth Map's DVD tab (user, 2026-11-07), VDA's page (VdaPage.qml) for DVD:
//
//   Input Source (its own folders)
//   DVD          | Frames & Output   | Upcoming
//   Depth Range  | Current Clip Info | Jobs
//   [ action bar: progress, status, Start / Stop, View Output Folder ]
//
// The work is stages/dvd (controller, runner) over core/dvd. Its maps land where DepthCrafter's do, under
// the same name; a map of another kind already there moves into its kind's folder (DepthCrafter/, VDA/).
Item {
    id: page
    property var prompt: null
    property var consoleRef: null

    readonly property var dvd: DVD
    readonly property var st: dvd.settings
    property bool foldersOpen: false
    // (no chapter yet in the project, 2026-11-12: folded too -- the picker says "Pick a chapter")
    readonly property bool chapterMode: dvd.hasProject && (dvd.chapterNum >= 0 || dvd.needsChapter)
    readonly property bool manualHidden: !!Shell.foldersHidden["dvd"]
    readonly property bool pathsFolded: chapterMode ? !foldersOpen : manualHidden
    function toggleFolders() {
        if (chapterMode) foldersOpen = !foldersOpen
        else Shell.setFoldersHidden("dvd", !manualHidden)
    }
    readonly property bool idle: !dvd.busy

    readonly property real colMin: 370
    readonly property real listMin: 330
    readonly property int requiredWidth: 36 + 2 * (colMin + Theme.gap) + listMin
    readonly property real requiredHeight: content.y + content.implicitHeight + Theme.gap + actionBar.implicitHeight + 12

    component VBox: TextBox { store: page.st; owner: page.dvd }
    component VDrop: Dropdown { store: page.st }
    component VCheck: CheckField { store: page.st }
    component Caption: Text {
        color: Theme.fgMuted
        font.family: Theme.bodyFont
        font.pixelSize: Theme.bodySize
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    }
    function help(k) { return page.dvd.help[k] || "" }

    Connections {
        target: page.dvd
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
        const key = page.dvd.weightsKey
        const running = d.key === key
        const queued = (d.queued || []).indexOf(key) >= 0
        const err = d.error && d.error.key === key ? d.error.text : ""
        return { on: running || queued, running: running, progress: d.progress || 0, text: d.text || "", error: err }
    }
    Connections {
        target: typeof Models !== "undefined" ? Models : null
        ignoreUnknownSignals: true
        function onDownloadChanged() { page.dvd.recheckWeights() }
        function onChanged() { page.dvd.recheckWeights() }
    }

    FolderPicker {
        id: inputFolderDlg
        title: "Input: a folder of videos, or a single video"
        allowFiles: true
        fileTypes: ["mp4", "avi", "mov", "mkv", "webm", "flv", "gif"]
        currentFolder: page.st.input_dir_or_file_var
        onAccepted: page.st.input_dir_or_file_var = selectedPath
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
        onAccepted: page.dvd.loadSettingsFrom(Shell.urlToPath(selectedFile))
    }
    FileDialog {
        id: saveSettingsDlg
        title: "Save Settings As"
        fileMode: FileDialog.SaveFile
        defaultSuffix: "json"
        nameFilters: ["JSON files (*.json)", "All files (*)"]
        onAccepted: page.dvd.saveSettingsTo(Shell.urlToPath(selectedFile))
    }

    ColumnLayout {
        id: content
        objectName: "dvdContent"
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
                    visible: page.dvd.sceneCutDescription.length > 0
                    text: "Import from Scene Cut"
                    kind: "tonal"
                    iconGlyph: Theme.icon.importIcon
                    onClicked: page.dvd.importFromSceneCut()
                    HoverTip {
                        tip: "Fill this stage's folders from the most recent Scene Cut split: in a project, "
                             + "its chapter's folders, Output too.\n"
                             + "Clicking replaces whatever those fields currently hold.\n\n"
                             + (page.dvd.hasProject ? "Fills:\n" : "Most recent job:\n") + page.dvd.sceneCutDescription
                    }
                },
                ChangeFoldersButton {
                    open: !page.pathsFolded
                    manual: !page.chapterMode
                    onClicked: page.toggleFolders()
                    paths: [{ label: "Input Folder/File", value: page.st.input_dir_or_file_var },
                            { label: "Output Folder", value: page.st.output_dir }]
                },
                ChapterPicker { visible: page.dvd.hasProject; dc: page.dvd; onPicked: page.foldersOpen = false }
            ]
            FieldRow {
                visible: !page.pathsFolded
                label: "Input Folder/File"
                help: page.help("input_dir_or_file")
                labelWidth: 130
                RowLayout {
                    width: parent.width
                    spacing: 8
                    VBox { objectName: "dvdInput"; key: "input_dir_or_file_var"; numeric: false; Layout.fillWidth: true }
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
                    VBox { objectName: "dvdOutput"; key: "output_dir"; numeric: false; Layout.fillWidth: true }
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
                    title: "DVD"
                    enabled: page.idle
                    FieldRow {
                        label: "Model"
                        help: page.help("model")
                        Text {
                            objectName: "dvdModel"
                            text: "DVD v1.1  ·  Wan 2.1 1.3B"
                            color: Theme.fg
                            font.family: Theme.bodyFont
                            font.pixelSize: Theme.bodySize
                        }
                    }
                    // the picked size not downloaded yet: its download, here
                    ColumnLayout {
                        objectName: "dvdWeightsMissing"
                        visible: !page.dvd.weightsInstalled
                        Layout.fillWidth: true
                        spacing: 6
                        Caption {
                            text: page.dl.error !== "" ? page.dl.error
                                  : page.dl.running ? page.dl.text
                                  : page.dl.on ? "Waiting for the download running in Settings > Studio > Models."
                                  : "DVD isn't in the models folder yet."
                        }
                        RowLayout {
                            spacing: 8
                            Layout.fillWidth: true
                            AccentButton {
                                objectName: "dvdDownload"
                                visible: !page.dl.on
                                text: "Download Weights"
                                kind: "tonal"
                                onClicked: {
                                    const why = Models.start(page.dvd.weightsKey)
                                    if (why) Theme.toast("warning", "Download Weights", why)
                                }
                                HoverTip { tip: "Downloads DVD's checkpoint from Hugging Face into the models folder (4.5 GB; "
                                                + "CC-BY-NC-4.0, non-commercial use only). Settings > Studio > Models lists them all." }
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
                                onClicked: page.dl.running ? Models.stop() : Models.unqueue(page.dvd.weightsKey)
                            }
                        }
                    }
                    // typed, or picked from the menu, as VDA's; a typed size snaps up to a multiple of 16
                    FieldRow {
                        label: "Input Size"
                        help: page.help("input_size")
                        RowLayout {
                            spacing: 6
                            VBox { objectName: "dvdInputSize"; key: "input_size"; boxWidth: 80 }
                            AccentButton {
                                id: sizesBtn
                                objectName: "dvdInputSizes"
                                kind: "outline"
                                iconGlyph: Theme.icon.down
                                implicitWidth: 34
                                onClicked: sizesMenu.opened ? sizesMenu.close() : sizesMenu.popup(sizesBtn, 0, sizesBtn.height + 4)
                                HoverTip { tip: "Short sides DVD takes (multiples of 16). 480 is what it's trained at." }
                            }
                            StudioMenu {
                                id: sizesMenu
                                objectName: "dvdInputSizesMenu"
                                Instantiator {
                                    model: page.dvd.choices.input_size
                                    delegate: MenuItem {
                                        required property var modelData
                                        objectName: "dvdInputSizeItem"
                                        text: String(modelData) + (modelData === 480 ? "  (trained)" : "")
                                        onTriggered: page.st.input_size = modelData
                                    }
                                    onObjectAdded: (i, item) => sizesMenu.insertItem(i, item)
                                    onObjectRemoved: (i, item) => sizesMenu.removeItem(item)
                                }
                            }
                        }
                    }
                    // Billy's GUI's Width and Early Downsample (user, 2026-11-07)
                    FieldRow {
                        label: "Width"
                        help: page.help("width")
                        VBox { objectName: "dvdWidth"; key: "width"; boxWidth: 80 }
                    }
                    VCheck { objectName: "dvdEarlyDownsample"; key: "early_downsample"; text: "Early Downsample"; help: page.help("early_downsample") }
                    FieldRow {
                        label: "Window"
                        help: page.help("window")
                        RowLayout {
                            spacing: 6
                            VBox { objectName: "dvdWindow"; key: "window"; boxWidth: 80 }
                            AccentButton {
                                id: windowsBtn
                                objectName: "dvdWindows"
                                kind: "outline"
                                iconGlyph: Theme.icon.down
                                implicitWidth: 34
                                onClicked: windowsMenu.opened ? windowsMenu.close() : windowsMenu.popup(windowsBtn, 0, windowsBtn.height + 4)
                                HoverTip { tip: "Window lengths, 9 to 81 frames. 81 is DVD's own default." }
                            }
                            StudioMenu {
                                id: windowsMenu
                                objectName: "dvdWindowsMenu"
                                Instantiator {
                                    model: page.dvd.choices.window
                                    delegate: MenuItem {
                                        required property var modelData
                                        objectName: "dvdWindowItem"
                                        text: String(modelData) + (modelData === 81 ? "  (default)" : "")
                                        onTriggered: page.st.window = modelData
                                    }
                                    onObjectAdded: (i, item) => windowsMenu.insertItem(i, item)
                                    onObjectRemoved: (i, item) => windowsMenu.removeItem(item)
                                }
                            }
                        }
                    }
                    FieldRow {
                        label: "Overlap"
                        help: page.help("overlap")
                        VBox { objectName: "dvdOverlap"; key: "overlap"; boxWidth: 80 }
                    }
                    // the VAE on TensorRT (user, 2026-11-07); not installed: to Settings > Studio > Models, as DepthCrafter's
                    VCheck {
                        objectName: "dvdTensorRT"; key: "use_tensorrt"; text: "TensorRT"
                        guard: () => Theme.needModel("tensorrt", "TensorRT isn't installed yet",
                                                     "Install NVIDIA TensorRT here, then tick TensorRT again.")
                        help: page.help("use_tensorrt")
                    }
                }

                Section {
                    title: "Depth Range"
                    enabled: page.idle
                    FieldRow {
                        label: "Normalise"
                        help: page.help("normalise_mode")
                        VDrop { objectName: "dvdNormalise"; key: "normalise_mode"; model: page.dvd.choices.normalise_mode }
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
                    VCheck { objectName: "dvdResume"; key: "move_to_finished"; text: "Resume"; help: page.help("move_to_finished") }
                }

                ClipInfo {
                    consoleRef: page.consoleRef
                    rows: [
                        { label: "Batch Progress", value: page.dvd.progressCount || " ", help: "Which clip of the batch is running, and how many there are." },
                        { label: "Filename", value: page.dvd.clipName, help: "" },
                        { label: "Resolution", value: page.dvd.clipResolution, help: "" },
                        { label: "Frames", value: page.dvd.clipFrames, help: "" }
                    ]
                }
            }

            JobQueue {
                objectName: "dvdJobQueue"
                x: grid.listX
                width: Math.max(0, grid.width - x)
                height: Math.max(grid.height, actionBar.y - Theme.gap - content.y - grid.y)
                clip: true
                source: page.dvd.jobClips
                ctl: page.dvd
                busy: page.dvd.busy
                trustRunning: true
                running: page.dvd.busy && page.dvd.clipName && page.dvd.clipName !== "N/A" ? String(page.dvd.clipName) : ""
                progress: page.dvd.framesTotal > 0
                          ? (page.dvd.framesPhase === "encode" ? 0.85 + 0.15 * page.dvd.framesDone / page.dvd.framesTotal
                                                               : 0.85 * page.dvd.framesDone / page.dvd.framesTotal) : 0
                countText: page.dvd.progressCount || ""
                rowActions: [{ id: "run", text: "Process This Clip Now" }, { id: "test", text: "Test Render" },
                             { id: "finished", text: "Move to Finished" },
                             { id: "send", text: "Send to Splatting", whenDone: true }]
                onRowAction: (id, path) => {
                    if (id === "run") page.dvd.startClip(path)
                    else if (id === "test") page.dvd.testClip(path)
                    else if (id === "finished") page.dvd.jobClips.moveToFinished(path)
                    else if (id === "send") sendOn(path, page.dvd, "splatting", Splatting, 1)
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
        tips: Tips.forStage("dvd")
        busy: page.dvd.busy
        // the clip's frames, depth then encode (the batch's count is in Current Clip Info)
        done: page.dvd.busy ? page.dvd.framesDone : page.dvd.progressDone
        total: page.dvd.busy ? page.dvd.framesTotal : page.dvd.progressTotal
        // ...with the pass's speed and its time left (user, 2026-11-01), as the console's progress line
        status: page.dvd.busy && page.dvd.framesTotal > 0
                ? (page.dvd.framesPhase === "encode" ? "Encoding " : "Depth ") + page.dvd.framesDone + " / "
                  + page.dvd.framesTotal + " frames"
                  + (page.dvd.fps > 0 ? "  ·  " + page.dvd.fps.toFixed(1) + " fps" : "")
                  + (page.dvd.timeLeft ? "  ·  " + page.dvd.timeLeft + " left" : "")
                  + (page.dvd.progressCount ? "  ·  clip " + page.dvd.progressCount : "")
                : page.dvd.status

        AccentButton {
            objectName: "dvdStart"
            text: "Start"
            iconGlyph: Theme.icon.play
            enabled: page.idle
            onClicked: page.dvd.start()
        }
        AccentButton {
            objectName: "dvdStop"
            text: "Stop"
            kind: "outline"
            enabled: page.dvd.busy
            onClicked: (page.prompt ? page.prompt.askStop({ stage: "DVD", safeText: "Stop after this clip", safeDetail: "the clip being processed finishes and its map is kept; the rest of the folder is left for the next Start.", onSafe: () => page.dvd.stopAfterClip(), onYes: () => page.dvd.stop() })
                         : page.dvd.stop())
        }

        trailing: [
            AccentButton {
                text: "View Output Folder"
                kind: "outline"
                onClicked: page.dvd.openOutputFolder()
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
            property string tip: "Puts every DVD setting back to its default. Asks first."
            enabled: page.idle
            onTriggered: page.confirm("Reset Settings", "Are you sure you want to reset all DVD settings to their default values?",
                                      "Reset", () => page.dvd.resetDefaults())
        }
        MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: Theme.border } }
        MenuItem {
            text: "Restore Finished Input Files..."
            enabled: page.idle
            onTriggered: page.confirm("Restore Finished Input Files",
                                      "Move the original input files from the 'Finished' subfolder back to their original input directory?",
                                      "Restore", () => page.dvd.restoreInputs("finished"))
        }
        MenuItem {
            text: "Restore Failed Input Files..."
            enabled: page.idle
            onTriggered: page.confirm("Restore Failed Input Files",
                                      "Move the original input files from the 'Failed' subfolder back to their original input directory?",
                                      "Restore", () => page.dvd.restoreInputs("failed"))
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
