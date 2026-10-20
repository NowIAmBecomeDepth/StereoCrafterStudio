import QtQuick
import QtQuick.Controls.Basic
import Studio

// A compact text/number box bound to one settings key.
//
// It writes back on Enter or when focus leaves, never per keystroke, so a
// half-typed "-" never reaches the settings. The controller coerces the value
// and refuses one that will not parse (fieldRejected); the box then shakes and
// shows the last good value again.
TextField {
    id: box
    property var store: null
    property string key: ""
    property var owner: null          // the stage controller, for fieldRejected
    property bool numeric: true
    property int boxWidth: 110

    implicitWidth: boxWidth
    implicitHeight: Theme.rowHeight
    width: boxWidth
    leftPadding: 10
    rightPadding: 10
    verticalAlignment: TextInput.AlignVCenter
    color: enabled ? Theme.fg : Theme.fgFaint
    selectionColor: Theme.alpha(Theme.accent, 0.45)
    selectedTextColor: Theme.fg
    font.family: Theme.bodyFont
    font.pixelSize: Theme.bodySize
    selectByMouse: true
    inputMethodHints: numeric ? Qt.ImhFormattedNumbersOnly : Qt.ImhNone
    text: store && key ? String(store[key]) : ""

    function commit() {
        if (!store || !key) return
        const current = String(store[key])
        if (text !== current) store[key] = numeric ? text.trim() : text
    }
    onEditingFinished: commit()

    Connections {
        target: box.owner
        ignoreUnknownSignals: true
        function onFieldRejected(k) { if (k === box.key) shake.restart() }
    }

    background: Rectangle {
        radius: 7
        color: Theme.field
        border.width: box.activeFocus ? 1.5 : 1
        border.color: box.activeFocus ? Theme.accent : (hover.hovered ? Theme.borderStrong : Theme.border)
        opacity: box.enabled ? 1 : Theme.dim(0.55)
        Behavior on border.color { ColorAnimation { duration: Theme.dur(160) } }
        HoverHandler { id: hover; cursorShape: Qt.IBeamCursor }
    }
    transform: Translate { id: shift }
    SequentialAnimation {
        id: shake
        NumberAnimation { target: shift; property: "x"; to: -7; duration: Theme.dur(45) }
        NumberAnimation { target: shift; property: "x"; to: 6; duration: Theme.dur(70) }
        NumberAnimation { target: shift; property: "x"; to: -4; duration: Theme.dur(70) }
        NumberAnimation { target: shift; property: "x"; to: 0; duration: Theme.dur(60) }
    }
}
