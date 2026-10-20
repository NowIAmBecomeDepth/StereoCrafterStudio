import QtQuick
import QtQuick.Shapes
import Studio

// The running chunk's progress as a ring (user, 2026-09-27): its steps done,
// as a percentage in the middle, "5 / 12" under it and the unit ("chunk";
// DepthCrafter: "window") under that -- three short lines, so the text sits
// inside the ring rather than running into it. The whole clip is the bar beside it (ActionBar). It eases to each new
// value, and only when one lands -- nothing loops (Theme.calm).
Item {
    id: ring
    property string stage: ""
    property int size: 92
    readonly property bool mine: typeof JobStats !== "undefined" && JobStats.stage === stage
    readonly property real fraction: mine ? JobStats.part : 0
    readonly property int chunks: mine ? JobStats.chunks : 0
    readonly property int running: mine ? Math.max(1, JobStats.chunk) : 0
    readonly property string unit: stage === "depthcrafter" ? "window" : "chunk"

    implicitWidth: size
    implicitHeight: size

    property real shown: fraction
    Behavior on shown { NumberAnimation { duration: Theme.dur(420); easing.type: Easing.OutCubic } }

    readonly property real r: size / 2 - 5
    Shape {
        anchors.fill: parent
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
            strokeColor: Theme.trough
            strokeWidth: 7
            fillColor: "transparent"
            PathAngleArc { centerX: ring.size / 2; centerY: ring.size / 2; radiusX: ring.r; radiusY: ring.r; startAngle: 0; sweepAngle: 360 }
        }
        ShapePath {
            strokeColor: Theme.accent
            strokeWidth: 7
            fillColor: "transparent"
            capStyle: ShapePath.RoundCap
            PathAngleArc {
                centerX: ring.size / 2; centerY: ring.size / 2; radiusX: ring.r; radiusY: ring.r
                startAngle: -90
                sweepAngle: Math.max(ring.shown > 0 ? 1 : 0, 360 * ring.shown)
            }
        }
    }
    Column {
        anchors.centerIn: parent
        spacing: -2
        Text {
            objectName: "jobRingPercent"
            anchors.horizontalCenter: parent.horizontalCenter
            text: ring.chunks > 0 ? Math.round(ring.fraction * 100) + "%" : "—"
            color: ring.chunks > 0 ? Theme.fg : Theme.fgFaint
            font.family: Theme.bodyFont
            font.pixelSize: 18
            font.weight: Font.DemiBold
            font.features: { "tnum": 1 }
        }
        Text {
            objectName: "jobRingChunk"
            anchors.horizontalCenter: parent.horizontalCenter
            text: ring.chunks > 0 ? ring.running + " / " + ring.chunks : "—"
            color: ring.chunks > 0 ? Theme.fg : Theme.fgFaint
            font.family: Theme.bodyFont
            font.pixelSize: 11
            font.features: { "tnum": 1 }
        }
        Text {
            objectName: "jobRingUnit"
            anchors.horizontalCenter: parent.horizontalCenter
            text: ring.unit
            color: Theme.fgMuted
            font.family: Theme.bodyFont
            font.pixelSize: 10
        }
    }
}
