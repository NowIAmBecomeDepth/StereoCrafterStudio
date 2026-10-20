import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import Studio

// v1's spin box (core/ui/ctk_theme.CtkSpin): a number field with − / + either
// side, stepping by one and held to [from, to]. Writes an int into the
// setting; typing is allowed and clamped when the field is left.
RowLayout {
    id: spin
    property var store: null
    property string key: ""
    property int from: 0
    property int to: 100
    property int boxWidth: 44
    property string help: ""
    spacing: 2

    readonly property int value: {
        const v = parseInt(store && key ? store[key] : from)
        return isNaN(v) ? from : Math.max(from, Math.min(to, v))
    }
    function write(v) { if (store && key) store[key] = Math.max(from, Math.min(to, Math.round(v))) }

    AccentButton {
        text: "−"; kind: "ghost"; implicitWidth: 24
        enabled: spin.value > spin.from
        onClicked: spin.write(spin.value - 1)
    }
    TextField {
        id: field
        objectName: "spinText"
        implicitWidth: spin.boxWidth
        implicitHeight: Theme.rowHeight
        text: String(spin.value)
        horizontalAlignment: Text.AlignHCenter
        color: Theme.fg
        font.family: Theme.bodyFont
        font.pixelSize: Theme.bodySize
        validator: IntValidator { bottom: spin.from; top: spin.to }
        background: Rectangle { radius: 6; color: Theme.field; border.width: 1; border.color: field.activeFocus ? Theme.accent : Theme.border }
        // Left or Enter: written (clamped), and the field shows the setting
        // again. Not only on editingFinished: the validator calls an empty
        // field or a number below `from` unfinished, so that never came and
        // the field kept the text over the old number (audit 2026-09-28, minor).
        function commit() { const v = parseInt(text); if (!isNaN(v)) spin.write(v); text = Qt.binding(() => String(spin.value)) }
        onEditingFinished: commit()
        onActiveFocusChanged: if (!activeFocus) commit()
        Keys.onReturnPressed: (event) => { commit(); event.accepted = false }
        Keys.onEnterPressed: (event) => { commit(); event.accepted = false }
        HoverTip { tip: spin.help }
    }
    AccentButton {
        text: "+"; kind: "ghost"; implicitWidth: 24
        enabled: spin.value < spin.to
        onClicked: spin.write(spin.value + 1)
    }
}
