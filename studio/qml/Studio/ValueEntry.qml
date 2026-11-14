import QtQuick
import QtQuick.Controls.Basic
import Studio

// Type a value into a dial or slider (user, 2026-11-13): a right-click opens this
// box over the control with the value selected. Enter sets it (clamped to the
// control's range and snapped to its step by the control's write), Esc or a
// click elsewhere leaves it as it was. Reset puts the default back -- what the
// right-click did before.
Popup {
    id: pop
    property real from: 0
    property real to: 1
    property int decimals: 2
    property real defaultValue: NaN
    property real current: 0
    signal accepted(real value)
    signal resetAsked()

    function openAt(item) {
        parent = item
        x = Math.round((item.width - implicitWidth) / 2)
        y = Math.round((item.height - implicitHeight) / 2)
        field.text = current.toFixed(decimals)
        field.bad = false
        open()
    }
    function accept() {
        const v = Number(field.text.trim().replace(",", "."))
        if (field.text.trim() === "" || isNaN(v)) { field.bad = true; return }
        close()
        accepted(Math.max(from, Math.min(to, v)))
    }

    modal: false
    focus: true
    padding: 8
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    onOpened: { field.forceActiveFocus(); field.selectAll() }

    enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.dur(120) } }
    exit: Transition { NumberAnimation { property: "opacity"; from: 1; to: 0; duration: Theme.dur(90) } }

    contentItem: Column {
        spacing: 6
        TextField {
            id: field
            objectName: "valueEntryField"
            property bool bad: false
            width: 96
            height: Theme.rowHeight
            leftPadding: 8
            rightPadding: 8
            horizontalAlignment: TextInput.AlignHCenter
            verticalAlignment: TextInput.AlignVCenter
            color: Theme.fg
            selectionColor: Theme.alpha(Theme.accent, 0.45)
            selectedTextColor: Theme.fg
            font.family: Theme.bodyFont
            font.pixelSize: Theme.bodySize
            font.features: { "tnum": 1 }
            selectByMouse: true
            inputMethodHints: Qt.ImhFormattedNumbersOnly
            onTextEdited: bad = false
            Keys.onReturnPressed: pop.accept()
            Keys.onEnterPressed: pop.accept()
            background: Rectangle {
                radius: 7
                color: Theme.field
                border.width: 1.5
                border.color: field.bad ? Theme.danger : Theme.accent
            }
        }
        Text {
            width: field.width
            horizontalAlignment: Text.AlignHCenter
            text: pop.from.toFixed(pop.decimals) + " – " + pop.to.toFixed(pop.decimals)
            color: Theme.fgMuted
            font.family: Theme.bodyFont
            font.pixelSize: Theme.smallSize
            font.features: { "tnum": 1 }
        }
        Text {
            id: resetLink
            objectName: "valueEntryReset"
            visible: !isNaN(pop.defaultValue)
            width: field.width
            horizontalAlignment: Text.AlignHCenter
            text: "Reset to " + pop.defaultValue.toFixed(pop.decimals)
            color: resetHover.hovered ? Theme.accent : Theme.fgMuted
            font.family: Theme.bodyFont
            font.pixelSize: Theme.smallSize
            font.underline: resetHover.hovered
            HoverHandler { id: resetHover; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: { pop.close(); pop.resetAsked() } }
        }
    }

    background: Rectangle {
        PopupGrain { radius: 9 }   // the window's film grain
        radius: 9
        color: Theme.field
        border.width: 1
        border.color: Theme.borderStrong
    }
}
