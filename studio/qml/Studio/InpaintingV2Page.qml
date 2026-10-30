import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Layouts
import Studio
import "tips.js" as Tips

// Inpainting V2 (Wan VACE), v1's arrangement (inpainting_gui_v2.create_widgets):
//
//   Experimental   (the notice about hours and VRAM)
//   Paths          Input file/folder [Browse Folder][Select File], Output folder
//   Parameters     Frames / chunk | Frames overlap, Tile overlap | Tiles / axis,
//                  Inference steps | Seed, Resume
//   VRAM / Performance   Offload mode, Torch Compile, Free text encoder
//   Progress     V1's ActionBar: ring, sec/it, the clip's bar + ETA, frames inpainted, status,
//                [Start][Stop]   [Restore Finished]   (Load / Save V2 Settings: Settings > Inpainting)
//
// The work is v1's own (stages/inpainting_v2: the generated runner). v1's
// modal dialogs arrive as InpaintingV2.message and are asked in the page.
Item {
    id: page
    property var prompt: null
    property var consoleRef: null   // the console window: the Progress heading's Open Console
    readonly property var wan: InpaintingV2
    readonly property var st: wan.settings
    readonly property var tips: wan.tips
    readonly property real requiredHeight: content.y + content.implicitHeight + 16

    component WBox: TextBox { store: page.st; owner: page.wan }
    component Half: FieldRow { labelWidth: 130; Layout.fillWidth: true; Layout.preferredWidth: 1 }
    component Body: Text {
        color: Theme.fg
        font.family: Theme.bodyFont
        font.pixelSize: Theme.bodySize
        wrapMode: Text.WordWrap
    }

    Connections {
        target: page.wan
        function onMessage(kind, title, text) {
            page.prompt.ask({ title: title, message: text, buttons: [{ text: "OK", code: 1, kind: "filled" }] })
        }
        function onToast(kind, title, text) { Theme.toast(kind, title, text) }
    }

    readonly property string videoFilter: "Video files (*.mp4 *.mkv *.mov *.avi *.webm)"
    // one picker for a folder or a single clip (user, 2026-10-05: FolderPicker, Columns)
    FolderPicker {
        id: inFolderDlg; title: "Input: a folder, or a single video"
        allowFiles: true
        fileTypes: ["mp4", "mkv", "mov", "avi", "webm"]
        currentFolder: page.st.input_folder
        onAccepted: page.st.input_folder = selectedPath.replace(/\\/g, "/")
    }
    FolderPicker {
        id: outFolderDlg; title: "Output folder"
        currentFolder: Shell.pathToUrl(page.st.output_folder)
        onAccepted: page.st.output_folder = Shell.urlToPath(selectedFolder).replace(/\\/g, "/")
    }

    ColumnLayout {
        id: content
        objectName: "pageContent"
        x: 18
        y: 6
        width: parent.width - 36
        spacing: Theme.gap

        // First on the page, because the costs are the surprise (v1).
        Section {
            title: "Experimental"
            // measured 2026-10-12 (user's 5060 Ti, MMGP int8): 97 frames in 16-17 min; one tile OOM at 81.
            // On hover, an (i) at the heading's right end (user, 2026-10-18), not a paragraph of its own.
            headerRight: [
                Item {
                    objectName: "v2Findings"
                    readonly property string text: "On a 16 GB RTX 5060 Ti (MMGP streaming, 5 steps, 2 tiles per axis) "
                        + "it takes about 10 seconds per frame at 1408x768 per eye; 20 steps take about 3.5 times as "
                        + "long. Keep Tiles / axis at 2 on a 16 GB card: 1 runs out of memory with 81-frame chunks."
                    implicitWidth: 22
                    implicitHeight: 22
                    Layout.alignment: Qt.AlignVCenter
                    HoverHandler { id: findingsHover }
                    Icon {
                        anchors.centerIn: parent
                        glyph: Theme.icon.info
                        size: 16
                        color: findingsHover.hovered ? Theme.fg : Theme.fgMuted
                    }
                    HoverTip { tip: parent.text }
                }
            ]
            Body {
                Layout.fillWidth: true
                // no timeframe until it has been measured (user, 2026-10-01)
                text: "Inpainting V2 (Wan VACE, 14B) is a much larger model and more demanding on hardware, "
                      + "so expect significantly longer wait times. It isn't practical for a full conversion: "
                      + "keep it for problem scenes where V1 isn't good enough at any other settings, and "
                      + "check the offload mode against your card before starting."
            }
            // Download V2 Weights (user, 2026-10-18: as SAM 3 and ROSE have): only while they aren't
            // all here, or a download runs. The sizes are weights.py's, per file, left to fetch.
            ColumnLayout {
                id: dlBlock
                objectName: "v2WeightsBlock"
                Layout.fillWidth: true
                spacing: 6
                readonly property var dl: page.wan.download
                readonly property bool running: !!dl.what
                // read again whenever a download starts, moves or ends
                readonly property var need: (dl, page.wan.weightsNeed())
                readonly property var fp8: (dl, page.wan.fp8Need())
                readonly property bool fp8Picked: String(page.st.offload_mode).toLowerCase().indexOf("fp8") === 0
                visible: running || !page.wan.weightsInstalled || (fp8Picked && !page.wan.fp8Installed)

                function ask(what, n) {
                    const lines = n.parts.map(p => "  " + p.name + ": " + p.left
                                              + (p.left !== p.size ? " left of " + p.size : ""))
                    page.prompt.ask({
                        title: what === "fp8" ? "Download StereoCrafter2 FP8?" : "Download V2's weights?",
                        message: "From Hugging Face into " + n.folder + ":\n\n" + lines.join("\n")
                                 + "\n\n" + n.size + " in all" + (n.free ? " (" + n.free + " free on that drive)" : "")
                                 + ". Windows shows it as about " + (n.bytes / 1073741824).toFixed(1) + " GB.\n\n"
                                 + "It runs in the background and can be stopped: the next Download carries on "
                                 + "where it stopped. The models are under their own licences (see the README).",
                        buttons: [{ text: "Download", code: 1, kind: "filled" }, { text: "Cancel", code: 0, kind: "ghost" }],
                        onDone: (code) => { if (code === 1) { if (what === "fp8") page.wan.downloadFp8(); else page.wan.downloadWeights() } }
                    })
                }

                RowLayout {
                    visible: !dlBlock.running && !page.wan.weightsInstalled
                    Layout.fillWidth: true
                    spacing: 10
                    Body {
                        objectName: "weightsStorageNote"
                        Layout.fillWidth: true
                        text: "Its weights aren't on this PC yet: " + dlBlock.need.size + " to download"
                              + (dlBlock.need.size !== dlBlock.need.total ? " (of " + dlBlock.need.total + ")" : "")
                              + " -- Wan 2.1 VACE's text encoder, VAE and tokenizer, and StereoCrafter2."
                    }
                    AccentButton {
                        objectName: "v2DownloadWeights"
                        text: "Download V2 Weights"
                        kind: "outline"
                        iconGlyph: Theme.icon.importIcon
                        enabled: !page.wan.busy
                        onClicked: dlBlock.ask("weights", page.wan.weightsNeed())
                        HoverTip { tip: "Downloads only the files V2 loads, " + dlBlock.need.total + ", into the weights folder." }
                    }
                }
                RowLayout {
                    visible: !dlBlock.running && page.wan.weightsInstalled && dlBlock.fp8Picked && !page.wan.fp8Installed
                    Layout.fillWidth: true
                    spacing: 10
                    Body {
                        objectName: "fp8StorageNote"
                        Layout.fillWidth: true
                        text: "FP8 resident runs a pre-quantized StereoCrafter2: " + dlBlock.fp8.size
                              + " more to download (it needs about 20 GB of VRAM)."
                    }
                    AccentButton {
                        objectName: "v2DownloadFp8"
                        text: "Download FP8 Model"
                        kind: "outline"
                        iconGlyph: Theme.icon.importIcon
                        enabled: !page.wan.busy
                        onClicked: dlBlock.ask("fp8", page.wan.fp8Need())
                    }
                }
                RowLayout {
                    visible: dlBlock.running
                    Layout.fillWidth: true
                    spacing: 8
                    ProgressBar {
                        objectName: "v2DownloadProgress"
                        Layout.fillWidth: true
                        from: 0; to: 1; value: dlBlock.dl.progress || 0
                        background: Rectangle { implicitHeight: 6; radius: 3; color: Theme.trough }
                        contentItem: Item { Rectangle { width: parent.parent.visualPosition * parent.width; height: 6; radius: 3; color: Theme.accent } }
                    }
                    AccentButton {
                        objectName: "v2DownloadStop"
                        text: "Stop"
                        kind: "ghost"
                        onClicked: page.wan.cancelDownload()
                        HoverTip { tip: "Stops the download. What came down is kept: the next Download carries on from there." }
                    }
                }
                Text {
                    objectName: "v2DownloadStatus"
                    visible: dlBlock.running
                    Layout.fillWidth: true
                    text: dlBlock.dl.text || ""
                    color: Theme.fgMuted
                    font.family: Theme.bodyFont; font.pixelSize: Theme.smallSize + 1
                    elide: Text.ElideRight
                }
            }
        }

        Section {
            id: pathsSection
            title: "Paths"
            FieldRow {
                label: "Input file/folder:"
                help: "A single video file (processed alone) or a folder of videos (every supported video is batch-processed)."
                labelWidth: 130
                RowLayout {
                    width: parent.width
                    spacing: 8
                    WBox { key: "input_folder"; numeric: false; Layout.fillWidth: true }
                    // Folder first, then file: the same order as every other stage (v1).
                    AccentButton { objectName: "browseInput"; text: "Browse"; kind: "outline"; onClicked: inFolderDlg.open()
                                   HoverTip { tip: "Pick a folder to process every clip in it, or a single clip to process just that one." } }
                }
            }
            FieldRow {
                label: "Output folder:"
                // how V2 writes (user, 2026-10-11): Inpainting's Encoding Settings, and what 10-bit gives V2
                help: "Where the inpainted videos are written. They're encoded as Settings > Inpainting > Encoding "
                      + "Settings say, as V1's are: H.264 / H.265 (.mp4), 10-bit (Main10), or Enable DNxHR (.mov). "
                      + "V2's picture is 8-bit (the model's output and the left eye are 8-bit), so a 10-bit file holds no more detail than that -- but it still means no second 8-bit compression on top: 10-bit (Main10) or Enable DNxHR keeps the fill exactly as V2 made it on its way to Merging."
                labelWidth: 130
                RowLayout {
                    width: parent.width
                    spacing: 8
                    WBox { key: "output_folder"; numeric: false; Layout.fillWidth: true }
                    AccentButton { text: "Browse Folder"; kind: "outline"; onClicked: outFolderDlg.open() }
                }
            }
        }

        // the settings on the left, Upcoming Jobs beside them (user, 2026-10-11: V1's clip list
        // for V2 too), so the page keeps its height
        RowLayout {
            id: v2SettingsRow
            Layout.fillWidth: true
            spacing: Theme.gap
            ColumnLayout {
                Layout.fillWidth: true
                Layout.preferredWidth: 3
                Layout.alignment: Qt.AlignTop
                spacing: Theme.gap
                Section {
                    title: "Parameters"
                    PairRow {
                        Half { label: "Frames / chunk:"; help: page.tips.PARAM_TIP_CHUNK || ""; WBox { key: "frames_chunk" } }
                        Half { label: "Frames overlap:"; help: page.tips.PARAM_TIP_OVERLAP || ""; WBox { key: "frames_overlap" } }
                    }
                    PairRow {
                        Half { label: "Tile overlap (px):"; help: "Pixel overlap between spatial tiles when tiling is enabled."; WBox { key: "tile_overlap" } }
                        Half { label: "Tiles / axis:"; help: "Number of spatial tiles per axis. 1 disables tiling."; WBox { key: "tile_num" } }
                    }
                    PairRow {
                        Half { label: "Inference steps:"; help: "Number of denoising steps. More = slower, potentially higher quality."; WBox { key: "inference_steps" } }
                        Half { label: "Seed:"; help: "Random seed for reproducible results."; WBox { key: "seed" } }
                    }
                    CheckField { store: page.st; key: "move_to_finished"; text: "Resume"; help: page.tips.RESUME_TIP || "" }
                }

                Section {
                    title: "VRAM / Performance"
                    FieldRow {
                        label: "Offload mode:"
                        help: page.tips.OFFLOAD_TIP || ""
                        labelWidth: 130
                        Dropdown { store: page.st; key: "offload_mode"; model: page.wan.offloadModes; boxWidth: 210 }
                    }
                    // Torch Compile where Use SageAttention was (user, 2026-10-11: that one is in Settings >
                    // Inpainting already, as Use Sage Attention (for V2))
                    CheckField {
                        objectName: "v2Compile"
                        shown: !Theme.sage1     // SageAttention 1 (RTX 20): its Triton 3.2 breaks Torch Compile
                        store: page.st; key: "use_compile"; text: "Torch Compile"
                        help: "Compiles the model for this GPU when a run starts: faster chunks, after a first chunk that takes "
                              + "several minutes longer while it compiles (the compiled kernels are kept, so the next run of the "
                              + "same size starts sooner). Worth it for long unattended runs, not for a clip or two. Works with "
                              + "Offload mode None, FP8 resident and MMGP streaming; the other offload modes run without it."
                    }
                    // Hidden under MMGP streaming, which holds its own references and
                    // ignores it (v1 _sync_free_te_visibility); the value is kept.
                    CheckField {
                        store: page.st; key: "free_text_encoder"
                        text: "Free text encoder after prompt (saves ~11 GB RAM)"
                        help: page.tips.FREE_TE_TIP || ""
                        shown: String(page.st.offload_mode).toLowerCase().indexOf("mmgp") !== 0
                    }
                }
            }
            JobQueue {
                objectName: "v2JobQueue"
                Layout.fillWidth: true
                Layout.preferredWidth: 2
                Layout.fillHeight: true
                Layout.minimumHeight: 220
                clip: true
                source: page.wan.jobClips
                ctl: page.wan
                busy: page.wan.busy
                trustRunning: true
                running: page.wan.clipName
                progress: page.wan.busy ? (page.wan.progress || 0) / 100 : 0
                rowActions: [{ id: "run", text: "Inpaint This Clip Now" }, { id: "test", text: "Test Render" },
                             { id: "finished", text: "Move to Finished" },
                             { id: "send", text: "Send to Merging", whenDone: true }]
                onRowAction: (id, path) => {
                    if (id === "run") page.wan.startClip(path)
                    else if (id === "test") page.wan.testClip(path)     // into Test Renders (user, 2026-10-15)
                    else if (id === "finished") page.wan.jobClips.moveToFinished(path)
                    else if (id === "send") sendOn(path, page.wan, "merging", Merging, 3)
                }
            }
        }

        // V1's foot (user, 2026-10-24): the chunk ring, sec/it with "Inference Step n / N", the chunk's
        // frames, the clip's bar with its ETA and the batch's, "n / N frames inpainted", the status --
        // this page's own job's numbers only (statsOwner), as JobStats has them from V2's engine
        ActionBar {
            id: actionBar
            objectName: "v2ActionBar"
            Layout.fillWidth: true
            Layout.topMargin: 10
            statsStage: "inpainting"
            statsOwner: "v2"
            framesLabel: "frames inpainted"
            tips: Tips.forStage("inpainting")
            consoleRef: page.consoleRef
            busy: page.wan.busy || page.wan.loading
            status: page.wan.status
            jobTime: false      // the finish line says it already, as V1's does
            title: "Progress"

            AccentButton {
                objectName: "v2Start"
                text: "Start"; iconGlyph: Theme.icon.play
                enabled: !page.wan.busy && !page.wan.loading
                onClicked: page.wan.start()
            }
            AccentButton {
                objectName: "v2Stop"
                text: "Stop"; kind: "outline"
                enabled: page.wan.busy
                // asks, then stops now (audit #13); Stop After Clip is the gentle way
                onClicked: (page.prompt ? page.prompt.askStop({ stage: "Inpainting", detail: "the clip being inpainted stops, but the chunks it has finished are kept: the next Start picks it up from the last one. Clips already finished are kept.", safeText: "Stop after this clip", safeDetail: "the clip being processed finishes, then the batch stops. The rest of the folder is left for the next Start.", onSafe: page.wan.multiClip ? () => { if (!page.wan.stopAfterClip) page.wan.toggleStopAfterClip() } : null, onYes: () => page.wan.stop() })
                         : page.wan.stop())
            }
            AccentButton {
                objectName: "v2StopAfter"
                text: page.wan.stopAfterClip ? "Stopping After Clip" : "Stop After Clip"
                kind: page.wan.stopAfterClip ? "tonal" : "outline"
                // only while a folder of clips is running (user, 2026-09-28)
                visible: page.wan.busy && page.wan.multiClip
                onClicked: page.wan.toggleStopAfterClip()
                HoverTip {
                    tip: "Let the clip being processed finish, then stop before the next one.\n"
                         + "The finished clips are complete; the rest of the folder is\n"
                         + "left for the next Start. Click again to carry on instead."
                }
            }

            trailing: [
                // not during a batch, as V1's (bug sweep 2026-10-09, A7): Restore moved
                // clips the run had just finished back to be run again, and Load
                // showed settings the run was not using
                AccentButton {
                    objectName: "v2RestoreFinished"
                    text: "Restore Finished"; kind: "outline"
                    enabled: !page.wan.busy && !page.wan.loading
                    onClicked: page.prompt.ask({
                        title: "Restore Finished Files",
                        message: "Are you sure you want to move all processed videos from the 'finished' folder back to the input folder?",
                        buttons: [{ text: "Yes", code: 1, kind: "filled" }, { text: "No", code: 0, kind: "ghost" }],
                        onDone: (code) => { if (code === 1) page.wan.restoreFinished() }
                    })
                }
            ]
        }
    }

    // drag and drop (user, 2026-10-25): Drop Zones over the paths
    DropZones {
        over: pathsSection
        store: page.st
        blocked: page.wan.busy
        targets: [{ key: "input_folder", label: "Input", folder: true, files: ["mp4", "mkv", "mov", "avi", "webm"] },
                  { key: "output_folder", label: "Output Folder", folder: true }]
    }
}
