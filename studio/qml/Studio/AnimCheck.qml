import QtQuick
import QtQuick.Controls.Basic
import Studio

// Checkbox: the box fills and bounces, and the tick is wiped in left-to-right
// the way a pen would draw it.
AbstractButton {
    id: control
    checkable: true
    hoverEnabled: true
    implicitWidth: 22 + (text.length ? label.implicitWidth + 10 : 0)
    implicitHeight: 24

    contentItem: Item {
        Rectangle {
            id: box
            width: 20; height: 20; radius: 6
            anchors.verticalCenter: parent.verticalCenter
            color: control.checked ? Theme.accent : (control.hovered ? Theme.hover : "transparent")
            border.width: control.checked ? 0 : 1.5
            border.color: control.hovered ? Theme.fgMuted : Theme.fgFaint
            Behavior on color { ColorAnimation { duration: Theme.dur(160) } }

            Item {
                id: wipe
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                height: parent.height
                width: control.checked ? parent.width : 0
                clip: true
                Behavior on width { NumberAnimation { duration: Theme.dur(control.checked ? 300 : 120); easing.type: Easing.OutCubic } }
                Icon {
                    x: 0
                    width: box.width; height: box.height
                    glyph: Theme.icon.check
                    size: 13
                    color: Theme.accentFg
                }
            }
        }
        Text {
            id: label
            x: 30
            anchors.verticalCenter: parent.verticalCenter
            text: control.text
            color: Theme.fg
            font.family: Theme.bodyFont
            font.pixelSize: 14
        }
    }
    background: null

    SequentialAnimation {
        id: bounce
        NumberAnimation { target: box; property: "scale"; to: 0.78; duration: Theme.dur(80) }
        NumberAnimation { target: box; property: "scale"; to: 1; duration: Theme.dur(420); easing.type: Easing.OutBack; easing.overshoot: 3 }
    }
    onToggled: bounce.restart()
}
