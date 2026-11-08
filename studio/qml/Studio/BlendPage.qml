import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import Studio
import "tips.js" as Tips

// Depth Map's Blend tab (user, 2026-10-26): a clip's DepthCrafter and VDA maps blended into one, no model.
//
//   Depth Maps Folder (Splatting's)                         [Same as Splatting]
//   Blend         | Preview: clip, frame, DepthCrafter | VDA | Blend
//   [ action bar: progress, status, Blend All / Blend This Clip / Stop, View Folder ]
//
// The work is stages/blend over core/depthblend.
Item {
    id: page
    property var prompt: null
    property var consoleRef: null

    readonly property var bl: Blend
    readonly property var st: bl.settings
    readonly property bool idle: !bl.busy
    readonly property real colMin: 370
    readonly property int requiredWidth: 36 + colMin + Theme.gap + 520
    readonly property real requiredHeight: content.y + content.implicitHeight + Theme.gap + actionBar.implicitHeight + 12

    component BDrop: Dropdown { store: page.st }
    component BSlider: SliderRow { store: page.st; labelWidth: 110 }
    component Caption: Text {
        color: Theme.fgMuted
        font.family: Theme.bodyFont
        font.pixelSize: Theme.bodySize
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    }
    function help(k) { return page.bl.help[k] || "" }

    Connections {
        target: page.bl
        function onToast(kind, title, message) { Theme.toast(kind, title, message) }
    }

    FolderPicker {
        id: folderDlg
        title: "Splatting's depth-map folder"
        currentFolder: Shell.pathToUrl(page.st.depth_folder)
        onAccepted: page.st.depth_folder = Shell.urlToPath(selectedFolder)
    }

    ColumnLayout {
        id: content
        objectName: "blendContent"
        x: 18
        y: 6
        width: page.width - 36
        spacing: Theme.gap

        Section {
            id: folderSection
            title: "Depth Maps"
            enabled: page.idle
            headerRight: [
                AccentButton {
                    objectName: "blendSameAsSplatting"
                    text: "Same as Splatting"
                    kind: "tonal"
                    onClicked: page.bl.useSplattingFolder()
                    HoverTip { tip: "Use Splatting's Input Depth Maps folder." }
                }
            ]
            FieldRow {
                label: "Depth Maps Folder"
                help: page.help("depth_folder")
                labelWidth: 140
                RowLayout {
                    width: parent.width
                    spacing: 8
                    TextBox { objectName: "blendFolder"; store: page.st; owner: page.bl; key: "depth_folder"; numeric: false; Layout.fillWidth: true }
                    AccentButton { text: "Browse Folder"; kind: "outline"; onClicked: folderDlg.open() }
                }
            }
            Caption {
                text: page.bl.clips.length
                      ? page.bl.clips.length + (page.bl.clips.length === 1 ? " clip has" : " clips have") + " both a DepthCrafter and a " + page.st.other + " map here."
                      : "No clip here has both a DepthCrafter and a " + page.st.other + " map yet: make the " + page.st.other
                        + " map on its tab (or Splatting's right-click Rerun with " + (page.st.other === "DVD" ? "DVD" : "Video Depth Anything") + ")."
            }
        }

        Item {
            id: grid
            Layout.fillWidth: true
            implicitHeight: Math.max(col0.implicitHeight, previewSection.implicitHeight)

            ColumnLayout {
                id: col0
                width: page.colMin
                spacing: Theme.gap
                Section {
                    objectName: "blendSettings"
                    // a clip with its own settings (user, 2026-11-07): the panel is that clip's
                    title: page.bl.previewOwn ? "Blend: " + (page.bl.clips[page.bl.previewClip] || "") + " only" : "Blend"
                    enabled: page.idle
                    // the map blended with DepthCrafter's: VDA's or DVD's (user, 2026-11-07)
                    FieldRow { label: "Other Map"; help: page.help("other"); BDrop { objectName: "blendOther"; key: "other"; model: page.bl.choices.other } }
                    FieldRow { label: "Mode"; help: page.help("mode"); BDrop { objectName: "blendMode"; key: "mode"; model: page.bl.choices.mode } }
                    // By Depth (user, 2026-11-07): Near / Far Mix in place of one Mix
                    CheckField {
                        objectName: "blendByDepth"
                        visible: page.st.mode === "Mix"
                        store: page.st; key: "mix_by_depth"
                        text: "Mix by Depth"
                        help: page.help("mix_by_depth")
                    }
                    BSlider {
                        objectName: "blendMix"
                        visible: page.st.mode === "Mix" && !page.st.mix_by_depth
                        key: "mix"; label: "Mix"; help: page.help("mix")
                        from: 0; to: 1; step: 0.05; decimals: 2; defaultValue: 0.5
                    }
                    BSlider {
                        objectName: "blendMixNear"
                        visible: page.st.mode === "Mix" && !!page.st.mix_by_depth
                        key: "mix_near"; label: "Near Mix"; help: page.help("mix_near")
                        from: 0; to: 1; step: 0.05; decimals: 2; defaultValue: 0.7
                    }
                    BSlider {
                        objectName: "blendMixFar"
                        visible: page.st.mode === "Mix" && !!page.st.mix_by_depth
                        key: "mix_far"; label: "Far Mix"; help: page.help("mix_far")
                        from: 0; to: 1; step: 0.05; decimals: 2; defaultValue: 0.3
                    }
                    BSlider {
                        objectName: "blendMixSplit"
                        visible: page.st.mode === "Mix" && !!page.st.mix_by_depth
                        key: "mix_split"; label: "Split"; help: page.help("mix_split")
                        from: 0; to: 1; step: 0.05; decimals: 2; defaultValue: 0.5
                    }
                    BSlider {
                        objectName: "blendMixSoft"
                        visible: page.st.mode === "Mix" && !!page.st.mix_by_depth
                        key: "mix_soft"; label: "Softness"; help: page.help("mix_soft")
                        from: 0.02; to: 1; step: 0.02; decimals: 2; defaultValue: 0.2
                    }
                    Caption {
                        visible: page.st.mode === "Mix"
                        text: "0 = DepthCrafter, 1 = " + page.st.other
                        font.pixelSize: Theme.bodySize - 1
                    }
                    FieldRow { label: "Base"; help: page.help("base"); BDrop { objectName: "blendBase"; key: "base"; model: page.bl.choices.base } }
                    BSlider {
                        objectName: "blendDetailSize"
                        visible: page.st.mode === "Layout + Detail"
                        key: "detail_size"; label: "Detail Size"; help: page.help("detail_size")
                        from: 0.5; to: 64; step: 0.5; decimals: 1; defaultValue: 8
                    }
                    BSlider {
                        objectName: "blendDetailAmount"
                        visible: page.st.mode === "Layout + Detail"
                        key: "detail_amount"; label: "Detail"; help: page.help("detail_amount")
                        from: 0; to: 3; step: 0.05; decimals: 2; defaultValue: 1
                    }
                    BSlider {
                        objectName: "blendEdgeKeep"
                        visible: page.st.mode === "Layout + Detail"
                        key: "edge_keep"; label: "Edge Keep"; help: page.help("edge_keep")
                        from: 0; to: 1; step: 0.05; decimals: 2; defaultValue: 0
                    }
                    BSlider {
                        objectName: "blendDetailLimit"
                        visible: page.st.mode === "Layout + Detail"
                        key: "detail_limit"; label: "Detail Limit %"; help: page.help("detail_limit")
                        from: 0; to: 20; step: 0.5; decimals: 1; defaultValue: 0
                    }
                    BSlider {
                        objectName: "blendDetailFloor"
                        visible: page.st.mode === "Layout + Detail"
                        key: "detail_floor"; label: "Noise Floor %"; help: page.help("detail_floor")
                        from: 0; to: 5; step: 0.1; decimals: 1; defaultValue: 0
                    }
                    BSlider {
                        objectName: "blendDetailSmooth"
                        visible: page.st.mode === "Layout + Detail"
                        key: "detail_smooth"; label: "Smoothing"; help: page.help("detail_smooth")
                        from: 0; to: 6; step: 1; decimals: 0; defaultValue: 0
                    }
                    BSlider {
                        objectName: "blendEdgeBand"
                        key: "edge_band"; label: "Edge Band px"; help: page.help("edge_band")
                        from: 0; to: 128; step: 2; decimals: 0; defaultValue: 0
                    }
                    FieldRow {
                        visible: page.st.edge_band > 0
                        label: "Band Edges"; help: page.help("edge_band_sides")
                        BDrop { objectName: "blendEdgeBandSides"; key: "edge_band_sides"; model: page.bl.choices.edge_band_sides }
                    }
                    BSlider {
                        objectName: "blendLevelBlack"
                        key: "level_black"; label: "Black Point"; help: page.help("level_black")
                        from: 0; to: 0.5; step: 0.01; decimals: 2; defaultValue: 0
                    }
                    BSlider {
                        objectName: "blendLevelWhite"
                        key: "level_white"; label: "White Point"; help: page.help("level_white")
                        from: 0.5; to: 1; step: 0.01; decimals: 2; defaultValue: 1
                    }
                    BSlider {
                        objectName: "blendLevelGamma"
                        key: "level_gamma"; label: "Gamma"; help: page.help("level_gamma")
                        from: 0.2; to: 5; step: 0.05; decimals: 2; defaultValue: 1
                    }
                    FieldRow { label: "Align"; help: page.help("align"); BDrop { objectName: "blendAlign"; key: "align"; model: page.bl.choices.align } }
                    FieldRow { label: "Output Size"; help: page.help("size"); BDrop { key: "size"; model: page.bl.choices.size } }
                }
            }

            Section {
                id: previewSection
                objectName: "blendPreview"
                title: "Preview"
                x: page.colMin + Theme.gap
                width: grid.width - x
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    Dropdown {
                        id: clipBox
                        objectName: "blendClip"
                        boxWidth: 220
                        model: page.bl.clipLabels
                        currentIndex: page.bl.previewClip
                        enabled: page.bl.clips.length > 0
                    }
                    Connections { target: clipBox; function onActivated(i) { page.bl.setPreviewClip(i) } }
                    // this clip's own settings (user, 2026-11-07)
                    AnimCheck {
                        id: ownBox
                        objectName: "blendOwn"
                        text: "Own Settings"
                        enabled: page.idle && page.bl.clips.length > 0
                        checked: page.bl.previewOwn
                        onToggled: { page.bl.setOwn(checked); checked = Qt.binding(() => page.bl.previewOwn) }
                        HoverTip { tip: page.help("own") }
                    }
                    Slider {
                        id: frameSlider
                        objectName: "blendFrame"
                        Layout.fillWidth: true
                        from: 0; to: Math.max(0, page.bl.previewFrames - 1); stepSize: 1
                        snapMode: Slider.SnapAlways
                        enabled: page.bl.previewFrames > 1 && page.idle
                        value: page.bl.previewFrame
                        onMoved: page.bl.setPreviewFrame(Math.round(value))
                        background: Rectangle {
                            x: frameSlider.leftPadding
                            y: frameSlider.topPadding + frameSlider.availableHeight / 2 - 2
                            width: frameSlider.availableWidth; height: 4; radius: 2
                            color: Theme.trough
                            Rectangle { width: frameSlider.visualPosition * parent.width; height: parent.height; radius: 2; color: Theme.accent }
                        }
                        handle: Rectangle {
                            x: frameSlider.leftPadding + frameSlider.visualPosition * (frameSlider.availableWidth - width)
                            y: frameSlider.topPadding + frameSlider.availableHeight / 2 - height / 2
                            width: 8; height: 18; radius: 3
                            color: Theme.knob; border.width: 2; border.color: Theme.accent
                        }
                    }
                    Text {
                        text: page.bl.previewFrames ? "Frame " + (page.bl.previewFrame + 1) + " / " + page.bl.previewFrames : ""
                        color: Theme.fgMuted; font.family: Theme.bodyFont; font.pixelSize: Theme.bodySize
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 0
                    Repeater {
                        model: ["DepthCrafter", page.st.other, "Blend"]
                        Text {
                            required property string modelData
                            Layout.fillWidth: true
                            Layout.preferredWidth: 1
                            horizontalAlignment: Text.AlignHCenter
                            text: modelData + (modelData === page.st.base || modelData === "Blend" || page.st.align === "Off" ? "" : " (fitted)")
                            color: modelData === "Blend" ? Theme.accentInk : Theme.fgMuted
                            font.family: Theme.headingFont; font.pixelSize: Theme.bodySize; font.weight: Font.DemiBold
                        }
                    }
                }
                Item {
                    Layout.fillWidth: true
                    implicitHeight: Math.max(160, width / 3 * 0.56)
                    Image {
                        id: shot
                        objectName: "blendPreviewImage"
                        anchors.fill: parent
                        source: page.bl.previewImage
                        fillMode: Image.PreserveAspectFit
                        cache: false
                        asynchronous: true
                        opacity: page.bl.previewBusy ? 0.6 : 1
                    }
                    Text {
                        anchors.centerIn: parent
                        width: parent.width - 40
                        visible: page.bl.previewMessage !== ""
                        text: page.bl.previewMessage
                        horizontalAlignment: Text.AlignHCenter
                        wrapMode: Text.WordWrap
                        color: Theme.fg; font.family: Theme.bodyFont; font.pixelSize: Theme.bodySize
                    }
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
        tips: Tips.forStage("blend")
        busy: page.bl.busy
        done: page.bl.busy ? page.bl.framesDone : page.bl.progressDone
        total: page.bl.busy ? page.bl.framesTotal : page.bl.progressTotal
        status: page.bl.busy && page.bl.framesTotal > 0
                ? (page.bl.framesPhase === "measure" ? "Measuring " : "Blending ") + page.bl.clipName + ": "
                  + page.bl.framesDone + " / " + page.bl.framesTotal + " frames"
                  + (page.bl.progressTotal > 1 ? "  ·  clip " + (page.bl.progressDone + 1) + " / " + page.bl.progressTotal : "")
                : page.bl.status

        AccentButton {
            objectName: "blendStart"
            text: "Blend All"
            iconGlyph: Theme.icon.play
            enabled: page.idle && page.bl.clips.length > 0
            onClicked: page.bl.start()
            HoverTip { tip: "Blend every clip listed, each into the map Splatting reads." }
        }
        AccentButton {
            objectName: "blendThisClip"
            text: "Blend This Clip"
            kind: "outline"
            enabled: page.idle && page.bl.clips.length > 0
            onClicked: page.bl.startPreviewClip()
            HoverTip { tip: "Blend just the clip the preview shows." }
        }
        AccentButton {
            objectName: "blendStop"
            text: "Stop"
            kind: "outline"
            enabled: page.bl.busy
            onClicked: (page.prompt ? page.prompt.askStop({ stage: "Blend", safeText: "Stop after this clip", safeDetail: "the clip being blended finishes and is kept; the rest are left for the next Start.", onSafe: () => page.bl.stopAfterClip(), onYes: () => page.bl.stop() })
                         : page.bl.stop())
        }
        trailing: [
            AccentButton {
                text: "View Folder"
                kind: "outline"
                onClicked: page.bl.openOutputFolder()
            }
        ]
    }

    readonly property var stageMenus: [fileMenu]
    StudioMenu {
        id: fileMenu
        title: "File"
        MenuItem {
            text: "Reset to Default"
            property string tip: "Puts every Blend setting back to its default. Your folder is kept."
            enabled: page.idle
            onTriggered: page.bl.resetDefaults()
        }
    }

    DropZones {
        over: folderSection
        store: page.st
        targets: [{ key: "depth_folder", label: "Depth Maps Folder", folder: true, slash: false }]
    }
}
