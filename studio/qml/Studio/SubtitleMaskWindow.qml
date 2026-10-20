import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Window
import Studio

// Detect Text: v1's Subtitle Mask editor (core/ui/subtitle_mask_editor.py),
// opened on the preview's clip at the preview's frame. Core / Edge / R / the
// colour key pick out burned-in subtitle text, a dragged rectangle limits it
// to the subtitle band, and Save renders <clip>_submask.mp4 beside the clip
// and records the submask_* settings in its sidecar. The rules are in
// stages/splatting/submask.py on the preview's worker; this is the window.
//
// Selections (user, 2026-10-02): several frame ranges, each with its own
// settings and rectangle. The strip under the scrubber picks the one the
// controls edit (its range is marked on the scrubber); From / To are its range;
// Save renders all of them into the one mask video.
Window {
    id: win
    property var pv: null
    property var prompt: null          // for the refusal, in the main window
    property var s: ({})               // SubtitleMask.state(), latest
    property int serial: 0
    property bool wanted: false
    property bool synced: false        // the controls hold the clip's settings
    property int syncedCurrent: -1     // ...for this selection
    property int syncedCount: 0
    property int syncedEpoch: 0        // Reset All: re-read the controls
    property bool syncing: false
    property bool dragging: false
    property real dx0: 0
    property real dy0: 0
    property real dx1: 0
    property real dy1: 0
    property int progressValue: 0
    property int progressMax: 1
    property string statusText: ""

    readonly property var view: s.view || [0, 0, 1, 1]
    readonly property int frames: s.frames || 1
    readonly property bool rendering: !!s.rendering
    readonly property var segments: s.segments || []
    readonly property int current: s.current !== undefined ? s.current : 0
    // none left (Remove takes the last one too): the controls rest, and Save
    // takes the clip's mask off
    readonly property bool hasSel: segments.length > 0
    // flattened selections (Flatten Layers): saved, shown, no longer edited
    readonly property var flat: s.flat || []

    title: s.title || "Subtitle Mask"
    width: 1180
    height: 820
    minimumWidth: 860
    minimumHeight: 560
    color: Theme.bg
    visible: false

    function openFor() {
        if (visible) { raise(); requestActivate(); return }
        wanted = true
        synced = false
        syncedCurrent = -1
        syncedEpoch = -1
        statusText = ""
        progressValue = 0
        pv.openEditor("submask")
    }
    onClosing: (close) => {
        // v1 _on_close: mid-render it only stops the render.
        if (win.rendering) { close.accepted = false; pv.editorOp("submask", "cancel", []); return }
        wanted = false
        updateTimer.stop()
        pv.closeEditor("submask")
    }

    // The window's own copy of the controls (v1's tk variables).
    QtObject {
        id: local
        property int core: 200
        property int edge: 165
        property bool refine: true
        property var r: 2
        property string edge_mode: "Hard"
        property string color: "Any"
        property bool invert: false
        property bool maskOnly: false
        onCoreChanged: win.schedule()
        onEdgeChanged: win.schedule()
        onRefineChanged: win.schedule()
        onRChanged: win.schedule()
        onEdge_modeChanged: win.schedule()
        onColorChanged: win.schedule()
        onInvertChanged: win.schedule()
        onMaskOnlyChanged: win.schedule()
    }
    function rValue() { return Math.max(0, Math.min(20, parseInt(local.r) || 0)) }
    function schedule() { if (!syncing && synced) updateTimer.restart() }
    // v1 schedule_update: DEBOUNCE_MS = 60.
    Timer {
        id: updateTimer
        interval: 60
        onTriggered: win.pv.editorOp("submask", "set", [{
            core: local.core, edge: local.edge, refine: local.refine, r: win.rValue(),
            edge_mode: local.edge_mode, color: local.color, invert: local.invert }, local.maskOnly])
    }

    Connections {
        target: win.pv
        function onEditorChanged(name, state) {
            if (name !== "submask" || !win.wanted) return
            // saved: v1 destroy_safe. Its last state goes too (user, 2026-10-02):
            // kept, it still said "rendering", and the hidden window refused
            // the close Qt asks of every window at quit -- Studio stayed running.
            if (!state.open) { win.wanted = false; win.s = ({}); win.visible = false; return }
            win.s = state
            if (state.serial) win.serial = state.serial
            // the controls take the selection's settings when the window
            // opens and whenever another selection becomes the current one
            const segs = state.segments || []
            if (!win.synced || state.current !== win.syncedCurrent || segs.length !== win.syncedCount
                    || (state.epoch || 0) !== win.syncedEpoch) {
                win.syncing = true
                local.core = state.core; local.edge = state.edge; local.refine = state.refine
                local.r = state.r; local.edge_mode = state.edge_mode; local.color = state.color
                local.invert = state.invert
                if (!win.synced) local.maskOnly = state.mask_only
                fromBox.text = String(state.from !== undefined ? state.from : 0)
                toBox.text = String(state.to !== undefined ? state.to : Math.max(0, state.frames - 1))
                win.syncing = false
                win.synced = true
                win.syncedCurrent = state.current
                win.syncedCount = segs.length
                win.syncedEpoch = state.epoch || 0
            } else {
                // the stored range (clamped to the clip), unless it is being typed
                if (!fromBox.activeFocus && state.from !== undefined) fromBox.text = String(state.from)
                if (!toBox.activeFocus && state.to !== undefined) toBox.text = String(state.to)
            }
            if (!scrub.pressed) scrub.value = state.frame
            if (!win.visible) { win.show(); win.raise(); win.requestActivate() }
        }
        function onEditorRefused(name, message) {
            if (name !== "submask") return
            win.wanted = false
            if (win.prompt) win.prompt.ask({ title: "Subtitle Mask", message: message })
        }
        function onEditorAlert(name, message) {
            if (name === "submask" && win.visible) alertDlg.ask({ title: "Subtitle Mask", message: message })
        }
        function onEditorProgress(name, value, maximum, text) {
            if (name !== "submask") return
            win.progressValue = value
            win.progressMax = Math.max(1, maximum)
            win.statusText = text
        }
    }

    PromptDialog { id: alertDlg }

    // v1's <Left>/<Right>/<Shift-...> bindings; not while typing, and only
    // while this window has keyboard focus (see SplattingPage.keysLive).
    readonly property bool typing: activeFocusItem && activeFocusItem.hasOwnProperty("cursorPosition")
    readonly property bool keysLive: Shell.focusWindow === win && !typing
    Shortcut { enabled: win.keysLive && !win.rendering; sequence: "Left"; onActivated: win.pv.editorOp("submask", "step", [-1]) }
    Shortcut { enabled: win.keysLive && !win.rendering; sequence: "Right"; onActivated: win.pv.editorOp("submask", "step", [1]) }
    Shortcut { enabled: win.keysLive && !win.rendering; sequence: "Shift+Left"; onActivated: win.pv.editorOp("submask", "step", [-10]) }
    Shortcut { enabled: win.keysLive && !win.rendering; sequence: "Shift+Right"; onActivated: win.pv.editorOp("submask", "step", [10]) }
    Shortcut { enabled: win.keysLive && !win.rendering; sequence: "Home"; onActivated: win.pv.editorOp("submask", "seek", [0]) }   // (2026-10-02)
    Shortcut { enabled: win.keysLive && !win.rendering; sequence: "End"; onActivated: win.pv.editorOp("submask", "seek", [Math.max(0, win.frames - 1)]) }
    // mark the current selection's From / To at the frame showing (user, 2026-10-02)
    Shortcut { enabled: win.keysLive && !win.rendering; sequence: "I"; onActivated: win.pv.editorOp("submask", "mark_in", []) }
    Shortcut { enabled: win.keysLive && !win.rendering; sequence: "O"; onActivated: win.pv.editorOp("submask", "mark_out", []) }

    component Note: Text {
        color: Theme.fg
        font.family: Theme.bodyFont
        font.pixelSize: Theme.bodySize
        Layout.alignment: Qt.AlignVCenter
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 6
        spacing: 6

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: "black"
            clip: true
            Image {
                id: pic
                anchors.fill: parent
                fillMode: Image.PreserveAspectFit
                smooth: true
                mipmap: true                     // v1 resizes INTER_AREA
                cache: false
                source: win.serial > 0 ? "image://splatpreview/submask/" + win.serial : ""
                readonly property real ox: (width - paintedWidth) / 2
                readonly property real oy: (height - paintedHeight) / 2
                readonly property real k: win.view[2] > 0 ? paintedWidth / win.view[2] : 1
                // v1 vid_to_canvas / canvas_to_vid (clamped to the frame).
                function toItemX(x) { return ox + (x - win.view[0]) * k }
                function toItemY(y) { return oy + (y - win.view[1]) * k }
                function toVidX(px) { return Math.max(0, Math.min(win.s.vw || 0, win.view[0] + (px - ox) / k)) }
                function toVidY(py) { return Math.max(0, Math.min(win.s.vh || 0, win.view[1] + (py - oy) / k)) }
            }
            // The detection rectangle: the one being dragged, else the saved one.
            Rectangle {
                id: rectBox
                readonly property var r: win.dragging ? [Math.min(win.dx0, win.dx1), Math.min(win.dy0, win.dy1),
                                                         Math.max(win.dx0, win.dx1), Math.max(win.dy0, win.dy1)]
                                                      : (win.s.rect || [])
                visible: r.length === 4 && pic.paintedWidth > 0
                x: visible ? pic.toItemX(r[0]) : 0
                y: visible ? pic.toItemY(r[1]) : 0
                width: visible ? (r[2] - r[0]) * pic.k : 0
                height: visible ? (r[3] - r[1]) * pic.k : 0
                color: "transparent"
                border.width: 2
                border.color: "#4cc36a"
            }
            MouseArea {
                anchors.fill: parent
                enabled: !win.rendering             // no new rectangle while Save renders
                cursorShape: Qt.CrossCursor
                onPressed: (m) => {
                    if (pic.paintedWidth <= 0) return
                    win.dx0 = win.dx1 = pic.toVidX(m.x); win.dy0 = win.dy1 = pic.toVidY(m.y)
                    win.dragging = true
                }
                onPositionChanged: (m) => { if (win.dragging) { win.dx1 = pic.toVidX(m.x); win.dy1 = pic.toVidY(m.y) } }
                onReleased: (m) => {
                    if (!win.dragging) return
                    win.dx1 = pic.toVidX(m.x); win.dy1 = pic.toVidY(m.y)
                    win.pv.editorOp("submask", "set_rect", [win.dx0, win.dy0, win.dx1, win.dy1])
                    win.dragging = false
                }
            }
        }

        // nav: v1's frame label and scrubber
        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            Note {
                text: "Frame: " + (win.s.frame !== undefined ? win.s.frame : "-") + " / " + Math.max(0, win.frames - 1)
                Layout.preferredWidth: 130
                font.features: { "tnum": 1 }
            }
            Slider {
                id: scrub
                objectName: "submaskScrub"
                Layout.fillWidth: true
                from: 0
                to: Math.max(0, win.frames - 1)
                stepSize: 1
                snapMode: Slider.SnapAlways
                enabled: !win.rendering
                onMoved: win.pv.editorOp("submask", "seek", [Math.round(value)])
                // Splatting's scrubber look (user, 2026-10-02): a thin track and
                // a rectangular handle
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
                    z: 1
                    // the frame it is on, while dragged (user, 2026-10-02: precise)
                    Rectangle {
                        visible: scrub.pressed
                        anchors.horizontalCenter: parent.horizontalCenter
                        anchors.bottom: parent.top
                        anchors.bottomMargin: 4
                        width: bubble.implicitWidth + 12; height: 20; radius: 5
                        color: Theme.accent
                        Text {
                            id: bubble
                            anchors.centerIn: parent
                            text: Math.round(scrub.value)
                            color: Theme.accentFg
                            font.family: Theme.bodyFont
                            font.pixelSize: Theme.smallSize + 1
                            font.features: { "tnum": 1 }
                        }
                    }
                }
                // a tick per frame when they are at least 4 px apart: the
                // handle visibly lands on each (it snaps to whole frames)
                Item {
                    objectName: "submaskFrameTicks"
                    x: scrub.leftPadding + 4
                    width: scrub.availableWidth - 8
                    y: scrub.topPadding + scrub.availableHeight / 2 - 6
                    height: 3
                    visible: win.frames > 1 && width / (win.frames - 1) >= 4
                    Repeater {
                        model: parent.visible ? win.frames : 0
                        Rectangle {
                            required property int index
                            x: parent.width * index / Math.max(1, win.frames - 1) - 0.5
                            width: 1; height: 3
                            color: Theme.alpha(Theme.fg, 0.3)
                        }
                    }
                }
                // each selection's frames just under the track (where Splatting
                // marks its keyframes); the current one in the accent
                Item {
                    objectName: "submaskRangeMarks"
                    x: scrub.leftPadding + 4
                    width: scrub.availableWidth - 8          // the handle's centre travels this
                    y: scrub.topPadding + scrub.availableHeight / 2 + 5
                    height: 4
                    // the flattened selections' frames, faint, under the current ones
                    Repeater {
                        model: win.flat
                        Rectangle {
                            objectName: "submaskFlatMark"
                            required property var modelData
                            readonly property real span: Math.max(1, win.frames - 1)
                            x: parent.width * modelData.from / span - 1
                            width: Math.max(3, parent.width * (modelData.to - modelData.from) / span + 2)
                            height: 4
                            radius: 1
                            color: Theme.alpha(Theme.fg, 0.18)
                        }
                    }
                    Repeater {
                        model: win.segments
                        Rectangle {
                            id: bar
                            required property var modelData
                            required property int index
                            readonly property real span: Math.max(1, win.frames - 1)
                            readonly property real len: Math.max(1, modelData.to - modelData.from)
                            readonly property color full: index === win.current ? Theme.accent : Theme.alpha(Theme.fg, 0.4)
                            x: parent.width * modelData.from / span - 1
                            width: Math.max(3, parent.width * (modelData.to - modelData.from) / span + 2)
                            height: 4
                            radius: 1
                            color: full
                        }
                    }
                    // the current selection's freeze frame
                    Rectangle {
                        objectName: "submaskHoldMark"
                        readonly property int hold: win.s.hold !== undefined ? win.s.hold : -1
                        visible: hold >= 0
                        width: 7; height: 7
                        rotation: 45
                        x: parent.width * Math.max(0, hold) / Math.max(1, win.frames - 1) - width / 2
                        y: 6
                        color: Theme.accent
                        border.width: 1
                        border.color: Theme.knob
                    }
                }
            }
        }

        // the selections: one chip each (its frames), + Add, Remove
        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            Note { text: "Selections:" }
            Flow {
                Layout.fillWidth: true
                spacing: 6
                Repeater {
                    model: win.segments
                    AccentButton {
                        required property var modelData
                        required property int index
                        objectName: "submaskSegment"
                        text: (index + 1) + "  ·  " + modelData.from + "–" + modelData.to
                        kind: index === win.current ? "filled" : "outline"
                        enabled: !win.rendering
                        onClicked: if (index !== win.current) win.pv.editorOp("submask", "select_segment", [index])
                    }
                }
                AccentButton {
                    objectName: "submaskAddSegment"
                    text: "+ Add"
                    kind: "ghost"
                    enabled: !win.rendering
                    onClicked: win.pv.editorOp("submask", "add_segment", [])
                    HoverTip { tip: "A new selection from the frame showing to the end, starting from this one's settings. Each selection has its own frames, settings and rectangle; Save puts them all in the one mask." }
                }
                AccentButton {
                    objectName: "submaskResetAll"
                    text: "Reset All"
                    kind: "ghost"
                    tint: Theme.danger
                    enabled: !win.rendering
                    onClicked: alertDlg.ask({
                        title: "Reset All",
                        message: "Reset Detect Text for this clip?\n\nEvery selection, rectangle and freeze is "
                                 + "erased, its saved settings are removed, and its mask video is deleted. You start "
                                 + "again from one selection over the whole clip.",
                        buttons: [{ text: "Reset All", code: 1, kind: "danger" }, { text: "Cancel", code: 0, kind: "tonal" }],
                        focusIndex: 1,
                        onDone: (code) => { if (code === 1) win.pv.editorOp("submask", "reset_all", []) }
                    })
                    HoverTip { tip: "Start over on this clip: erases every selection and freeze, the saved settings, and the mask video." }
                }
                AccentButton {
                    objectName: "submaskFlatten"
                    text: "Flatten Layers"
                    kind: "ghost"
                    enabled: !win.rendering && win.hasSel
                    onClicked: win.pv.editorOp("submask", "flatten", [fromBox.text, toBox.text])
                    HoverTip { tip: "Saves the mask with every selection so far, then sets them aside as flattened: they stay in the mask, and in the picture, but leave this list. The next selection you add starts fresh. For opening credits and other text that needs many masks." }
                }
                AccentButton {
                    objectName: "submaskRemoveSegment"
                    text: "Remove"
                    kind: "ghost"
                    enabled: !win.rendering && win.segments.length > 0
                    onClicked: win.pv.editorOp("submask", "remove_segment", [])
                    HoverTip { tip: "Removes this selection. With none left, Save takes the clip's text mask off." }
                }
            }
        }

        // the current selection's freeze frame (user, 2026-10-02): fading text
        // loses the detection, so hold the mask of a frame where it is caught
        // well, at full strength, for the whole selection (fades were removed:
        // a fading mask let the warped depth back onto the letters)
        RowLayout {
            Layout.fillWidth: true
            enabled: win.hasSel && !win.rendering   // not while Save renders (bug sweep 2026-10-09)
            spacing: 6
            readonly property int hold: win.s.hold !== undefined ? win.s.hold : -1
            Note { text: "Freeze:" }
            AccentButton {
                objectName: "submaskFreeze"
                text: parent.hold >= 0 ? "Frozen on " + parent.hold + "  ✕" : "Freeze Here"
                kind: parent.hold >= 0 ? "filled" : "outline"
                enabled: !win.rendering
                onClicked: win.pv.editorOp("submask", parent.hold >= 0 ? "unfreeze" : "freeze_here", [])
                HoverTip { tip: "Use this frame's mask, at full strength, for the whole selection: find a frame where the text is caught well (fading text loses the detection) and freeze there. Click again to unfreeze." }
            }
            Item { Layout.fillWidth: true }
        }

        // row 1: Core, Mask, Invert, Hard/Soft, colour
        RowLayout {
            Layout.fillWidth: true
            enabled: win.hasSel && !win.rendering   // not while Save renders (bug sweep 2026-10-09)
            spacing: 8
            SliderRow {
                store: local; key: "core"; label: "Core"; labelWidth: 40
                from: 0; to: 255; step: 1; decimals: 0; defaultValue: 200
            }
            AnimCheck { text: "Mask"; checked: local.maskOnly; onToggled: { local.maskOnly = checked; checked = Qt.binding(() => local.maskOnly) } }
            AnimCheck { text: "Invert"; checked: local.invert; onToggled: { local.invert = checked; checked = Qt.binding(() => local.invert) } }
            Dropdown { store: local; key: "edge_mode"; model: ["Hard", "Soft"]; boxWidth: 90 }
            Dropdown { store: local; key: "color"; model: ["Any", "White", "Yellow"]; boxWidth: 100 }
        }

        // row 2: Refine + Edge, R, Zoom Rect / Fit / Clear
        RowLayout {
            Layout.fillWidth: true
            enabled: win.hasSel && !win.rendering   // not while Save renders (bug sweep 2026-10-09)
            spacing: 8
            AnimCheck { text: "Refine"; checked: local.refine; onToggled: { local.refine = checked; checked = Qt.binding(() => local.refine) } }
            SliderRow {
                store: local; key: "edge"; label: "Edge"; labelWidth: 40
                from: 0; to: 255; step: 1; decimals: 0; defaultValue: 165
                enabled: local.refine
            }
            Note { text: "R" }
            AccentButton { text: "−"; kind: "outline"; implicitWidth: 28; onClicked: local.r = Math.max(0, win.rValue() - 1) }
            TextBox {
                id: rBox
                boxWidth: 44
                text: String(win.rValue())
                horizontalAlignment: TextInput.AlignHCenter
                onEditingFinished: {
                    const v = parseInt(text)
                    if (!isNaN(v)) local.r = Math.max(0, Math.min(20, v))
                    text = Qt.binding(() => String(win.rValue()))
                }
            }
            AccentButton { text: "+"; kind: "outline"; implicitWidth: 28; onClicked: local.r = Math.min(20, win.rValue() + 1) }
            AccentButton { text: "Zoom Rect"; kind: "outline"; onClicked: win.pv.editorOp("submask", "zoom_rect", []) }
            AccentButton { text: "Fit"; kind: "outline"; implicitWidth: 60; onClicked: win.pv.editorOp("submask", "fit_view", []) }
            AccentButton { text: "Clear"; kind: "outline"; implicitWidth: 70; onClicked: win.pv.editorOp("submask", "clear_rect", []) }
        }

        // row 3: From / To, the render's progress, Save / Cancel
        RowLayout {
            Layout.fillWidth: true
            Layout.bottomMargin: 4
            spacing: 6
            Note {
                text: win.hasSel ? "Selection " + (win.current + 1) + "   From:"
                      : win.flat.length ? "No selections: " + win.flat.length + " flattened, in the mask. + Add for more."
                      : "No selections: Save removes the mask."
            }
            TextBox {
                id: fromBox; objectName: "submaskFrom"; boxWidth: 60; text: "0"
                visible: win.hasSel
                enabled: !win.rendering
                onEditingFinished: win.pv.editorOp("submask", "set_range", [fromBox.text, toBox.text])
            }
            Note { text: "To:"; visible: win.hasSel }
            TextBox {
                id: toBox; objectName: "submaskTo"; boxWidth: 60; text: "0"
                visible: win.hasSel
                enabled: !win.rendering
                onEditingFinished: win.pv.editorOp("submask", "set_range", [fromBox.text, toBox.text])
            }
            // the frame showing as From / To, without typing (I / O)
            AccentButton {
                objectName: "submaskMarkIn"
                visible: win.hasSel
                text: "Set From"
                kind: "outline"
                enabled: !win.rendering
                onClicked: win.pv.editorOp("submask", "mark_in", [])
                HoverTip { tip: "This selection starts at the frame showing (I)." }
            }
            AccentButton {
                objectName: "submaskMarkOut"
                visible: win.hasSel
                text: "Set To"
                kind: "outline"
                enabled: !win.rendering
                onClicked: win.pv.editorOp("submask", "mark_out", [])
                HoverTip { tip: "This selection ends at the frame showing (O)." }
            }
            Item {
                Layout.fillWidth: true
                implicitHeight: 8
                Rectangle { anchors.fill: parent; radius: 4; color: Theme.trough }
                Rectangle {
                    width: parent.width * Math.min(1, win.progressValue / win.progressMax)
                    height: parent.height
                    radius: 4
                    color: Theme.accent
                }
            }
            Note { objectName: "submaskStatus"; text: win.statusText; Layout.preferredWidth: 230; elide: Text.ElideRight }
            AccentButton {
                objectName: "submaskSave"
                text: win.hasSel || win.flat.length ? "Save" : "Remove Mask"
                kind: win.hasSel || win.flat.length ? "filled" : "danger"
                enabled: !win.rendering
                onClicked: win.pv.editorOp("submask", "save", win.hasSel ? [fromBox.text, toBox.text] : [])
            }
            AccentButton { text: "Cancel"; kind: "outline"; onClicked: win.close() }
        }
    }
}
