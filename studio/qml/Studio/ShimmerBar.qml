import QtQuick
import Studio

// Determinate progress bar. The fill eases to each new value, and a
// highlight sweeps along the filled part while work is in progress.
Item {
    id: root
    property real value: 0       // 0..1
    property color tint: Theme.accent
    property color track: Theme.trough   // the empty part
    property bool shimmer: value > 0 && value < 1

    implicitWidth: 300
    implicitHeight: 10

    property real shown: value
    Behavior on shown { NumberAnimation { duration: Theme.dur(650); easing.type: Easing.OutCubic } }

    Rectangle {
        anchors.fill: parent
        radius: height / 2
        color: root.track
    }
    Rectangle {
        id: fill
        width: Math.max(height, parent.width * root.shown)
        height: parent.height
        radius: height / 2
        color: root.tint
        opacity: root.shown > 0.001 ? 1 : 0
        clip: true

        Rectangle {
            id: sheen
            width: 90
            height: parent.height
            x: -width
            // No sheen while a job runs (Theme.calm): it redraws every refresh.
            opacity: root.shimmer && !Theme.calm ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: Theme.dur(300) } }
            gradient: Gradient {
                orientation: Gradient.Horizontal
                GradientStop { position: 0.0; color: "#00ffffff" }
                GradientStop { position: 0.5; color: "#88ffffff" }
                GradientStop { position: 1.0; color: "#00ffffff" }
            }
            NumberAnimation on x {
                running: root.shimmer && root.visible && !Theme.calm
                loops: Animation.Infinite
                from: -90
                to: Math.max(fill.width, 200) + 90
                duration: Theme.dur(1500)
                easing.type: Easing.InOutSine
            }
        }
    }
}
