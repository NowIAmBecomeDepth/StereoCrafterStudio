import QtQuick
import QtQuick.Controls.Basic
import Studio

// Round icon button. The glyph can react on its own: `spinOnClick` turns it a
// full revolution, `turnOnHover` twists it a quarter, and `popOnToggle` bursts
// a ring of sparks when a checkable button turns on (the heart).
AbstractButton {
    id: control
    property string iconGlyph: Theme.icon.settings
    property string checkedGlyph: iconGlyph
    property color tint: Theme.fg
    property color checkedTint: Theme.accent
    property int size: 40
    property bool spinOnClick: false
    property bool turnOnHover: false
    property bool popOnToggle: false

    implicitWidth: size
    implicitHeight: size
    hoverEnabled: true

    background: Rectangle {
        radius: width / 2
        color: control.pressed ? Theme.alpha(Theme.fg, 0.16) : Theme.hover
        scale: control.hovered || control.pressed ? 1 : 0.5
        opacity: control.hovered || control.pressed ? 1 : 0
        Behavior on scale { NumberAnimation { duration: Theme.dur(220); easing.type: Easing.OutBack } }
        Behavior on opacity { NumberAnimation { duration: Theme.dur(160) } }
    }

    contentItem: Item {
        Icon {
            id: glyph
            anchors.centerIn: parent
            glyph: control.checked ? control.checkedGlyph : control.iconGlyph
            size: Math.round(control.size * 0.42)
            color: control.checked ? control.checkedTint : control.tint
            Behavior on color { ColorAnimation { duration: Theme.dur(200) } }
            rotation: control.turnOnHover && control.hovered ? 90 : 0
            Behavior on rotation {
                enabled: !spin.running
                NumberAnimation { duration: Theme.dur(420); easing.type: Easing.OutBack }
            }
        }

        // Spark ring for popOnToggle -- built only for a button that bursts
        // (Studio audit 2026-09-28, PERF: every icon button carried eight)
        Repeater {
            model: control.popOnToggle ? 8 : 0
            Rectangle {
                id: spark
                required property int index
                readonly property real angle: index * Math.PI / 4
                property real dist: 0
                width: 4; height: 4; radius: 2
                color: control.checkedTint
                opacity: 0
                x: parent.width / 2 - 2 + Math.cos(angle) * dist
                y: parent.height / 2 - 2 + Math.sin(angle) * dist
                function burst() { sparkAnim.restart() }
                ParallelAnimation {
                    id: sparkAnim
                    NumberAnimation { target: spark; property: "dist"; from: control.size * 0.2; to: control.size * 0.62; duration: Theme.dur(520); easing.type: Easing.OutCubic }
                    NumberAnimation { target: spark; property: "opacity"; from: 1; to: 0; duration: Theme.dur(520); easing.type: Easing.InQuad }
                }
                Connections {
                    target: control
                    function onToggled() { if (control.popOnToggle && control.checked) spark.burst() }
                }
            }
        }
    }

    SequentialAnimation {
        id: pop
        NumberAnimation { target: glyph; property: "scale"; to: 0.6; duration: Theme.dur(90); easing.type: Easing.OutQuad }
        NumberAnimation { target: glyph; property: "scale"; to: 1.0; duration: Theme.dur(480); easing.type: Easing.OutElastic; easing.amplitude: 1.4; easing.period: 0.35 }
    }
    RotationAnimation {
        id: spin
        target: glyph; property: "rotation"
        from: 0; to: 360; duration: Theme.dur(700)
        easing.type: Easing.InOutCubic
    }

    onClicked: {
        if (spinOnClick) spin.restart()
        else pop.restart()
    }
}
