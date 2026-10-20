import QtQuick
import Studio

// One tip at a time, held, then the next (user, 2026-09-27: the Inpainting
// Progress section's spare room). Click it for the next tip. A tip leaves by
// fading out as it slides up, and the next fades in rising from below
// (user, 2026-09-28: in place of the typewriter).
//
// While a job runs (Theme.calm) the swap is instant (Theme.dur is 0) and the
// next tip comes a minute later: one redraw a minute (with the card full every
// redraw costs the job; see Spinner).
Item {
    id: tick
    property var tips: []
    property int holdMs: 12000
    property int index: 0
    readonly property string full: tips.length > 0 ? tips[index % tips.length] : ""
    // how far a tip travels on its way out and in
    readonly property int rise: 8

    implicitHeight: line.implicitHeight
    Component.onCompleted: index = Math.floor(Math.random() * Math.max(1, tips.length))

    function next() { if (tips.length > 1 && !swap.running) swap.start() }

    SequentialAnimation {
        id: swap
        ParallelAnimation {
            NumberAnimation { target: body; property: "opacity"; to: 0; duration: Theme.dur(200); easing.type: Easing.InQuad }
            NumberAnimation { target: shift; property: "y"; to: -tick.rise; duration: Theme.dur(200); easing.type: Easing.InQuad }
        }
        ScriptAction { script: { tick.index = (tick.index + 1) % Math.max(1, tick.tips.length); shift.y = tick.rise } }
        ParallelAnimation {
            NumberAnimation { target: body; property: "opacity"; to: 1; duration: Theme.dur(320); easing.type: Easing.OutCubic }
            NumberAnimation { target: shift; property: "y"; to: 0; duration: Theme.dur(320); easing.type: Easing.OutCubic }
        }
    }
    Timer {
        interval: Theme.calm ? 60000 : tick.holdMs
        running: !swap.running && tick.visible && tick.tips.length > 1
        onTriggered: tick.next()
    }

    Row {
        id: line
        width: parent.width
        spacing: 8
        Text {
            id: badge
            objectName: "tipBadge"
            text: "TIP"
            color: Theme.accentInk
            font.family: Theme.bodyFont
            font.pixelSize: Theme.smallSize
            font.weight: Font.DemiBold
            font.letterSpacing: 1
            anchors.baseline: body.baseline
        }
        Text {
            id: body
            objectName: "tipText"
            width: line.width - badge.width - line.spacing
            text: tick.full
            color: Theme.fgMuted
            font.family: Theme.bodyFont
            font.pixelSize: Theme.smallSize + 1
            elide: Text.ElideRight
            transform: Translate { id: shift }
        }
    }
    TapHandler { onTapped: tick.next() }
    HoverHandler { cursorShape: Qt.PointingHandCursor }
}
