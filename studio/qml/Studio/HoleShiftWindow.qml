import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Window
import Studio

// v1's Hole Shift window (splatting_gui._dilreg_open): the splat of the frame
// on screen with its disocclusion holes tinted (green = selected, red = not),
// a click selects or drops a hole, and X / Y knobs shift every selected hole
// without changing its shape (user, 2026-09-27: knobs in place of v1's D-pad;
// the middle is no shift, X moves it left / right, Y down / up). The rules are in
// stages/splatting/editors.HoleShift and run on the preview's worker; this
// is only the window.
Window {
    id: win
    property var pv: null
    property var st: null              // the stage's settings (dilreg_enabled)
    property var prompt: null          // for the refusal, in the main window
    property var s: ({})               // editors.HoleShift.state(), latest
    property int serial: 0
    property bool wanted: false        // opened by the user and not closed since

    title: "Hole Shift — frame " + (s.frame !== undefined ? s.frame : 0)
    width: 1500
    height: 950
    minimumWidth: 900
    minimumHeight: 600
    color: Theme.bg
    visible: false

    function openFor() {
        if (visible) { raise(); requestActivate(); return }
        wanted = true
        pv.openEditor("holeshift")
    }
    onClosing: {
        wanted = false
        commitTimer.stop()
        pv.closeEditor("holeshift")
    }

    Connections {
        target: win.pv
        function onEditorChanged(name, state) {
            if (name !== "holeshift" || !win.wanted) return
            if (!state.open) return
            win.s = state
            if (state.serial) win.serial = state.serial
            // The knobs follow the selection -- but not while one is being
            // turned, nor just after: states still on their way describe older
            // values, and took the knob back with them (user, 2026-09-27).
            if (!xKnob.pressed && !yKnob.pressed && !knobHot.running) {
                knobs.offset_x = Number(state.xv || 0)
                knobs.offset_y = Number(state.yv || 0)
            }
            if (!win.visible) { win.show(); win.raise(); win.requestActivate() }
        }
        function onEditorRefused(name, message) {
            if (name !== "holeshift") return
            win.wanted = false
            if (win.prompt) win.prompt.ask({ title: "Hole Shift", message: message })
        }
    }

    // v1 _push_sliders re-rendered only when the pushing stopped. A knob is
    // turned for seconds: re-render every 200 ms while it turns, so the
    // preview follows live (the worker keeps only the newest commit), and
    // once more when it stops.
    Timer { id: commitTimer; interval: 180; onTriggered: win.pv.editorOp("holeshift", "commit", []) }
    Timer {
        id: liveCommit
        interval: 200
        repeat: true
        running: xKnob.pressed || yKnob.pressed
        onTriggered: if (win.dirty) { win.dirty = false; win.pv.editorOp("holeshift", "commit", []) }
    }
    property bool dirty: false
    // a knob was just turned: the selection's echo is older than it
    Timer { id: knobHot; interval: 1200 }
    function nudge(attr, direction) {
        pv.editorOp("holeshift", "nudge", [attr, direction])
        commitTimer.restart()
    }
    function setOffset(attr, value) {
        pv.editorOp("holeshift", "set_offset", [attr, value])
        dirty = true
        knobHot.restart()
        commitTimer.restart()
    }
    // What the knobs show and write (DialField keeps its value in a store).
    QtObject {
        id: knobs
        property real offset_x: 0
        property real offset_y: 0
    }
    readonly property bool hasSelection: s.x !== undefined && s.x !== "—"
    component ShiftKnob: DialField {
        store: knobs
        size: 84
        from: -20; to: 20; step: 0.1; decimals: 1
        defaultValue: 0; fillFrom: 0
        enabled: win.hasSelection
        onMoved: (v) => win.setOffset(key, v)
        onReleased: (v) => win.setOffset(key, v)     // also the right-click reset
    }

    PromptDialog { id: confirmDlg }

    component Note: Text {
        color: Theme.fg
        font.family: Theme.bodyFont
        font.pixelSize: Theme.bodySize
    }
    component Pad: AccentButton {
        property string tip: ""
        kind: "outline"
        implicitWidth: 36
        HoverTip { tip: parent.tip }
    }

    RowLayout {
        anchors.fill: parent
        anchors.margins: 8
        spacing: 10

        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: "#101010"
            Image {
                id: pic
                anchors.fill: parent
                fillMode: Image.PreserveAspectFit
                smooth: false                    // v1 resizes NEAREST
                cache: false
                source: win.serial > 0 ? "image://splatpreview/holeshift/" + win.serial : ""
                readonly property real ox: (width - paintedWidth) / 2
                readonly property real oy: (height - paintedHeight) / 2
            }
            TapHandler {
                onTapped: (point, button) => {
                    if (pic.paintedWidth <= 0 || pic.paintedHeight <= 0) return
                    const nx = (point.position.x - pic.ox) / pic.paintedWidth
                    const ny = (point.position.y - pic.oy) / pic.paintedHeight
                    if (nx < 0 || nx >= 1 || ny < 0 || ny >= 1) return
                    win.pv.editorOp("holeshift", "click", [nx, ny])
                }
            }
        }

        ColumnLayout {
            Layout.alignment: Qt.AlignTop
            Layout.preferredWidth: 220
            spacing: 4

            Note {
                text: "Click a hole to select it.\nThe knobs move it in pixels,\nkeeping its shape, and apply\nto every selected hole.\nThe middle is no shift;\nright-click one to go back."
                Layout.bottomMargin: 8
            }

            // X / Y knobs: the middle is no shift. The useful shift is a few
            // tenths: the wheel and the arrow keys move 0.1 px a notch.
            RowLayout {
                spacing: 8
                ShiftKnob {
                    id: xKnob
                    objectName: "hsXKnob"
                    key: "offset_x"; label: "X (left / right)"
                    help: "Moves the selected holes left (below the middle) or right (above it), in pixels. "
                          + "The wheel and the arrow keys move 0.1 px a notch; right-click goes back to no shift."
                }
                ShiftKnob {
                    id: yKnob
                    objectName: "hsYKnob"
                    key: "offset_y"; label: "Y (down / up)"
                    help: "Moves the selected holes down (below the middle) or up (above it), in pixels. "
                          + "The wheel and the arrow keys move 0.1 px a notch; right-click goes back to no shift."
                }
            }
            Note {
                objectName: "hsMixed"
                visible: win.s.x === "mixed" || win.s.y === "mixed"
                text: "The selected holes differ: a knob\nsets them all to one value."
                color: Theme.fgMuted
                font.pixelSize: Theme.smallSize
            }

            RowLayout {
                spacing: 4
                Note { text: "Feather"; Layout.preferredWidth: 64 }
                Pad { text: "−"; implicitWidth: 30; onClicked: win.nudge("feather", -1) }
                Note {
                    text: win.s.feather || "—"
                    Layout.preferredWidth: 48
                    horizontalAlignment: Text.AlignHCenter
                    font.features: { "tnum": 1 }
                }
                Pad { text: "+"; implicitWidth: 30; onClicked: win.nudge("feather", 1) }
            }

            Note { objectName: "hsInfo"; text: win.s.info || "0 selected"; Layout.topMargin: 8 }

            RowLayout {
                spacing: 4
                Layout.topMargin: 10
                AccentButton { text: "Add Key"; kind: "outline"; implicitWidth: 70; onClicked: win.pv.editorOp("holeshift", "add_key", []) }
                AccentButton { text: "Del Key"; kind: "outline"; implicitWidth: 70; onClicked: win.pv.editorOp("holeshift", "del_key", []) }
                AccentButton {
                    text: "Clear All"; kind: "outline"; implicitWidth: 76
                    onClicked: confirmDlg.ask({
                        title: "Hole Shift", message: "Clear ALL regions for this clip?",
                        buttons: [{ text: "Yes", code: 1, kind: "filled" }, { text: "No", code: 0, kind: "ghost" }],
                        onDone: (code) => { if (code === 1) win.pv.editorOp("holeshift", "clear_all", []) }
                    })
                }
            }

            AnimCheck {
                text: "Enable"
                Layout.topMargin: 10
                checked: win.st ? !!win.st.dilreg_enabled : false
                onToggled: {
                    win.st.dilreg_enabled = checked
                    checked = Qt.binding(() => win.st ? !!win.st.dilreg_enabled : false)
                    win.pv.editorOp("holeshift", "set_enabled", [win.st.dilreg_enabled])
                }
            }

            AccentButton { text: "Close"; kind: "outline"; Layout.topMargin: 10; onClicked: win.close() }
        }
    }
}
