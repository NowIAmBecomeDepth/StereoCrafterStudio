import QtQuick
import QtQuick.Controls.Basic
import Studio

// Radio: the ring thickens and the centre dot pops in with overshoot.
RadioButton {
    id: control
    hoverEnabled: true
    spacing: 10
    font.family: Theme.bodyFont
    font.pixelSize: 14

    indicator: Rectangle {
        x: control.leftPadding
        y: (control.height - height) / 2
        width: 20; height: 20; radius: 10
        color: "transparent"
        border.width: control.checked ? 2 : 1.5
        border.color: control.checked ? Theme.accent : (control.hovered ? Theme.fgMuted : Theme.fgFaint)
        Behavior on border.color { ColorAnimation { duration: Theme.dur(180) } }
        Rectangle {
            anchors.centerIn: parent
            width: 10; height: 10; radius: 5
            color: Theme.accent
            scale: control.checked ? 1 : 0
            Behavior on scale { NumberAnimation { duration: Theme.dur(360); easing.type: Easing.OutBack; easing.overshoot: 2.5 } }
        }
    }
    contentItem: Text {
        leftPadding: control.indicator.width + control.spacing
        text: control.text
        font: control.font
        color: Theme.fg
        verticalAlignment: Text.AlignVCenter
    }
}
