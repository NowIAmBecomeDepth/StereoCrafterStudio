import QtQuick
import QtQuick.Shapes
import Studio

// Material-style indeterminate spinner: the arc both rotates and breathes
// (its sweep grows and shrinks), which is what makes it look alive.
Item {
    id: root
    property int size: 32
    property real thickness: 3.5
    property color color: Theme.accent
    property bool running: true

    implicitWidth: size
    implicitHeight: size

    property real sweep: 30

    SequentialAnimation on sweep {
        running: root.running && root.visible && !Theme.calm
        loops: Animation.Infinite
        NumberAnimation { from: 30; to: 270; duration: Theme.dur(700); easing.type: Easing.InOutCubic }
        NumberAnimation { from: 270; to: 30; duration: Theme.dur(700); easing.type: Easing.InOutCubic }
    }

    // Calm (a job is running, Theme.calm): a still arc. Even a few steps a
    // second cost a job time: with the card full, every redraw makes Windows
    // swap the window's video memory against the job's (DepthCrafter 28%
    // slower visible than minimised, 2026-09-27).
    readonly property bool still: root.running && Theme.calm
    function settle() { if (still) { root.sweep = 270; shape.rotation = 0 } }
    onStillChanged: settle()
    Component.onCompleted: settle()

    Shape {
        id: shape
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer
        RotationAnimator on rotation {
            running: root.running && root.visible && !Theme.calm
            loops: Animation.Infinite
            from: 0; to: 360
            duration: Theme.dur(1100)
        }
        ShapePath {
            strokeColor: root.color
            strokeWidth: root.thickness
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            PathAngleArc {
                centerX: root.size / 2
                centerY: root.size / 2
                radiusX: (root.size - root.thickness) / 2
                radiusY: radiusX
                startAngle: -90
                sweepAngle: root.sweep
            }
        }
    }
}
