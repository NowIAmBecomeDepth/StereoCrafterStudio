import QtQuick
import QtQuick.Controls.Basic
import Studio

// Switch whose knob springs across and stretches while held, like iOS.
AbstractButton {
    id: control
    checkable: true
    hoverEnabled: true
    implicitWidth: track.width + (text.length ? label.implicitWidth + 12 : 0)
    implicitHeight: 28
    property string onGlyph: ""
    property string offGlyph: ""

    contentItem: Item {
        Rectangle {
            id: track
            width: 46; height: 26; radius: 13
            anchors.verticalCenter: parent.verticalCenter
            color: control.checked ? Theme.accent : Theme.trough
            border.width: 1
            border.color: control.checked ? Theme.accent : Theme.borderStrong
            Behavior on color { ColorAnimation { duration: Theme.dur(220) } }
            Behavior on border.color { ColorAnimation { duration: Theme.dur(220) } }

            Rectangle {
                id: knob
                readonly property real stretch: control.pressed ? 6 : 0
                width: 20 + stretch
                height: 20
                radius: 10
                y: 3
                x: control.checked ? track.width - width - 3 : 3
                // a light accent (white) under it: the dark ink, or the knob vanishes
                color: control.checked && Theme.accentLight ? Theme.accentFg : Theme.knob
                Behavior on x { enabled: !Theme.calm; SpringAnimation { spring: 4.5; damping: 0.32; epsilon: 0.05 } }
                Behavior on width { NumberAnimation { duration: Theme.dur(160); easing.type: Easing.OutCubic } }

                Icon {
                    anchors.centerIn: parent
                    glyph: control.checked ? control.onGlyph : control.offGlyph
                    visible: glyph.length > 0
                    size: 11
                    color: control.checked ? Theme.accent : Theme.trough
                    rotation: control.checked ? 0 : -180
                    Behavior on rotation { NumberAnimation { duration: Theme.dur(420); easing.type: Easing.OutBack } }
                }
            }
        }
        Text {
            id: label
            x: track.width + 12
            anchors.verticalCenter: parent.verticalCenter
            text: control.text
            color: Theme.fg
            font.family: Theme.bodyFont
            font.pixelSize: 14
        }
    }
    background: null
}
