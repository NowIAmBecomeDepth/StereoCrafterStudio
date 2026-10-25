import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Layouts
import Studio
import "tips.js" as Tips

// Scene Cut, v2 (Extra tab). v1's arrangement (scenecut_gui._create_widgets),
// section for section:
//
//   1. Input & Output            Video File, Output Folder, Split into chapters
//   2. Detection Configuration   Detector + its fields | FFmpeg Output
//   Optional: Refinements & Validation   (the corner switch folds it)
//   3. Output Actions            CSV / HTML / .sc, thumbnails, split
//   4. Run Process               Start Processing, Abort, the progress
//
// The run is v1's own (stages/scenecut/engine.py, generated); the detector
// list and each detector's fields come from v1's DETECTORS, so they appear
// once the engine has loaded (on a thread, the first time this page shows).
Item {
    id: page
    property bool active: false
    property var prompt: null
    property var consoleRef: null

    readonly property var sc: SceneCut
    readonly property var st: sc.settings
    readonly property bool idle: !sc.busy

    readonly property int requiredWidth: 860
    readonly property real requiredHeight: content.y + content.implicitHeight + Theme.gap + actionBar.implicitHeight + 12

    component SBox: TextBox { store: page.st; owner: page.sc; numeric: false }
    component SCheck: CheckField { store: page.st }
    component SSpin: SpinField {
        store: page.st
        from: (page.sc.spinRanges[key] || [0, 100])[0]
        to: (page.sc.spinRanges[key] || [0, 100])[1]
    }
    component Caption: Text {
        color: parent && parent.enabled === false ? Theme.fgFaint : Theme.fg
        font.family: Theme.bodyFont
        font.pixelSize: Theme.bodySize
        Layout.alignment: Qt.AlignVCenter
    }
    function help(k) { return page.sc.help[k] || "" }

    onActiveChanged: if (active) { sc.refreshChapters(); sc.ensureLoaded() }

    FileDialog {
        id: videoDlg
        title: "Select video file"
        currentFolder: Shell.pathToUrl(page.st.video_path || "")
        nameFilters: ["Video files (*.mp4 *.mkv *.avi *.mov)", "All files (*)"]
        onAccepted: page.sc.videoChosen(Shell.urlToPath(selectedFile).replace(/\\/g, "/"))
    }
    FolderPicker {
        id: outDlg
        title: "Select output directory"
        currentFolder: Shell.pathToUrl(page.st.output_dir || "")
        onAccepted: page.st.output_dir = Shell.urlToPath(selectedFolder).replace(/\\/g, "/")
    }

    ColumnLayout {
        id: content
        objectName: "pageContent"
        x: 18
        y: 6
        width: parent.width - 36
        spacing: Theme.gap
        enabled: page.idle

        Section {
            id: inOutSection
            title: "1. Input & Output"
            // no chapter dropdown (user, 2026-10-11): Scene Cut takes the whole movie, so a
            // project applies Whole Movie on its own (controller refreshChapters)
            FieldRow {
                label: "Video File:"; labelWidth: 110; help: page.help("video_path")
                RowLayout {
                    width: parent.width
                    spacing: 8
                    SBox { key: "video_path"; Layout.fillWidth: true }
                    AccentButton { text: "Browse..."; kind: "outline"; onClicked: videoDlg.open() }
                }
            }
            FieldRow {
                label: "Output Folder:"; labelWidth: 110; help: page.help("output_dir")
                RowLayout {
                    width: parent.width
                    spacing: 8
                    SBox { key: "output_dir"; Layout.fillWidth: true }
                    AccentButton { text: "Browse..."; kind: "outline"; onClicked: outDlg.open() }
                }
            }
            SCheck { key: "split_by_chapter"; text: "Split into chapters"; help: page.help("split_by_chapter") }
        }

        Section {
            title: "2. Detection Configuration"
            RowLayout {
                Layout.fillWidth: true
                spacing: 16
                ColumnLayout {
                    Layout.alignment: Qt.AlignTop
                    Layout.fillWidth: true
                    spacing: 4
                    RowLayout {
                        spacing: 10
                        Caption { text: "Detector:" }
                        Dropdown {
                            objectName: "detectorType"
                            store: page.st; key: "detector_type"; model: page.sc.detectors; boxWidth: 170
                            HoverTip { tip: page.help("detector_type") }
                        }
                        Spinner { visible: page.sc.loading; running: visible }
                    }
                    Caption {
                        visible: page.sc.loadError.length > 0
                        text: "The detectors could not be loaded: " + page.sc.loadError
                        color: Theme.danger || "#e06c6c"
                        wrapMode: Text.Wrap
                        Layout.fillWidth: true
                    }
                    // v1 _build_detector_params: one row per field of the chosen detector.
                    Repeater {
                        model: page.sc.detectorParams
                        RowLayout {
                            required property var modelData
                            spacing: 10
                            Caption { text: modelData.label; Layout.preferredWidth: 170 }
                            Dropdown {
                                visible: modelData.values.length > 0
                                model: modelData.values
                                boxWidth: 150
                                currentIndex: modelData.values.indexOf(modelData.value)
                                onActivated: (i) => page.sc.setDetectorParam(modelData.key, modelData.values[i])
                                HoverTip { tip: modelData.help }
                            }
                            TextField {
                                visible: modelData.values.length === 0
                                objectName: "param_" + modelData.key
                                implicitWidth: 150
                                implicitHeight: Theme.rowHeight
                                text: modelData.value
                                color: Theme.fg
                                font.family: Theme.bodyFont
                                font.pixelSize: Theme.bodySize
                                background: Rectangle { radius: 6; color: Theme.field; border.width: 1; border.color: parent.activeFocus ? Theme.accent : Theme.border }
                                onEditingFinished: page.sc.setDetectorParam(modelData.key, text)
                                HoverTip { tip: modelData.help }
                            }
                        }
                    }
                }
                Section {
                    title: "FFmpeg Output"
                    Layout.alignment: Qt.AlignTop
                    Layout.preferredWidth: 250
                    Layout.maximumWidth: 250
                    RowLayout {
                        spacing: 10
                        Caption { text: "Codec:"; Layout.preferredWidth: 70 }
                        Caption { text: "HEVC 10-bit (NVENC)"; HoverTip { tip: page.help("ffmpeg_codec") } }
                    }
                    RowLayout {
                        spacing: 10
                        Caption { text: "Preset:"; Layout.preferredWidth: 70 }
                        Dropdown { store: page.st; key: "ffmpeg_preset"; model: page.sc.presets; boxWidth: 80; HoverTip { tip: page.help("ffmpeg_preset") } }
                    }
                    RowLayout {
                        spacing: 10
                        Caption { text: "CQ Level:"; Layout.preferredWidth: 70 }
                        SSpin { key: "ffmpeg_cq"; help: page.help("ffmpeg_cq") }
                    }
                }
            }
        }

        // v1: the switch in the card's corner hides the section AND turns its
        // refinements off for a run, keeping the ticks inside.
        FoldSection {
            title: "Optional: Refinements & Validation"
            open: !!page.st.refinements_enabled
            help: page.help("refinements_enabled")
            onToggled: (on) => page.st.refinements_enabled = on
            SCheck { key: "snap_cuts"; text: "Snap cuts to frame-perfect boundary (Fixes +/- 2 frame AI inaccuracies)"; help: page.help("snap_cuts") }
            SCheck { key: "ai_validate"; text: "Validate cuts with DINOv3 AI:"; help: page.help("ai_validate") }
            RowLayout {
                spacing: 8
                Caption { text: "Validation Window (frames):" }
                SSpin { key: "ai_window"; help: page.help("ai_window") }
                Caption { text: "Flash Sensitivity:"; Layout.leftMargin: 16 }
                SSpin { key: "flash_sensitivity"; help: page.help("flash_sensitivity") }
            }
        }

        Section {
            title: "3. Output Actions"
            GridLayout {
                columns: 3
                columnSpacing: 20
                rowSpacing: 2
                SCheck { key: "export_csv"; text: "Export scene list to CSV"; help: page.help("export_csv") }
                SCheck { key: "save_images"; text: "Save scene thumbnails"; help: page.help("save_images") }
                RowLayout {
                    spacing: 6
                    Caption { text: "Images:" }
                    SSpin { key: "num_images"; help: page.help("num_images") }
                    Caption { text: "Margin:"; Layout.leftMargin: 10 }
                    SSpin { key: "frame_margin"; help: page.help("frame_margin") }
                }
                SCheck { key: "export_html"; text: "Export scene list to HTML"; help: page.help("export_html") }
                SCheck { key: "split_video"; text: "Split video into scenes (FFmpeg)"; help: page.help("split_ffmpeg") }
                // user, 2026-10-11: for movies whose aspect ratio switches
                SCheck {
                    objectName: "autoCropCheck"
                    key: "auto_crop"; text: "Auto Crop"
                    enabled: !!page.st.split_video
                    help: "Crops each split shot to its own picture, for movies whose aspect ratio switches -- an IMAX "
                          + "sequence at 1.90:1 inside a 2.39:1 film, a 4:3 flashback in a 16:9 show. It reads the "
                          + "black bars of the whole movie first (a quick pass over its keyframes), finds the few bar "
                          + "sizes the movie really uses, and gives each shot the one that fits it, so a dark shot "
                          + "is never cropped into. The crop happens on the graphics card as each shot is decoded "
                          + "(on the CPU only for a source the card can't decode)."
                }
                SCheck { key: "export_sc"; text: "Export to .sc file"; help: page.help("export_sc") }
                RowLayout {
                    spacing: 6
                    Caption { text: "SC Offset:" }
                    SSpin { key: "sc_offset"; help: page.help("sc_offset") }
                }
                Item { width: 1; height: 1 }
            }
        }
    }

    ActionBar {
        id: actionBar
        objectName: "actionBar"
        anchors.left: parent.left
        anchors.right: parent.right
        // this stage's own tips, typed out above the bar (user, 2026-09-27)
        tips: Tips.forStage("scenecut")      // studio/qml/Studio/tips.js: Settings > Tips lists them all
        anchors.bottom: parent.bottom
        anchors.margins: 12
        title: "4. Run Process"
        consoleRef: page.consoleRef
        busy: page.sc.busy
        done: Math.round(page.sc.progress)
        total: 100
        status: page.sc.status

        AccentButton {
            objectName: "scStart"
            text: "Start Processing"; iconGlyph: Theme.icon.play
            enabled: page.idle && !page.sc.loading
            onClicked: page.sc.start()
            HoverTip { tip: page.help("start_detection") }
        }
        AccentButton {
            objectName: "scAbort"
            text: "Abort"; kind: "outline"
            enabled: page.sc.busy
            // asks, then stops now (audit #13)
            onClicked: (page.prompt ? page.prompt.askStop({ stage: "Scene Cut", detail: "the scenes being cut are cancelled and discarded. Scenes already cut are kept.", safeText: "Stop after these scenes", safeDetail: "the scenes being cut now finish; no more are started.", onSafe: () => page.sc.abortAfterScenes(), onYes: () => page.sc.abort() })
                         : page.sc.abort())
        }
    }

    // v1's Scene Cut has no menubar of its own.
    readonly property var stageMenus: []

    Connections {
        target: page.sc
        function onMessage(kind, title, text) {
            page.prompt.ask({ title: title, message: text, buttons: [{ text: "OK", code: 1, kind: "filled" }] })
        }
        function onToast(kind, title, message) { Theme.toast(kind, title, message) }
    }

    // drag and drop (user, 2026-10-25): Drop Zones over the paths
    DropZones {
        over: inOutSection
        store: page.st
        targets: [{ key: "video_path", label: "Video File", files: ["mp4", "mkv", "avi", "mov"],
                    set: p => page.sc.videoChosen(p) },
                  { key: "output_dir", label: "Output Folder", folder: true }]
    }
}
