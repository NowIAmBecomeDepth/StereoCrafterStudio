import QtQuick
import Studio

// "Working, no estimate yet" -- two segments chasing each other, the same
// motion Material uses, instead of a bar bouncing back and forth.
Item {
    id: root
    property color tint: Theme.accent
    property bool running: true

    implicitWidth: 300
    implicitHeight: 6
    clip: true

    Rectangle { anchors.fill: parent; radius: height / 2; color: Theme.trough }

    property real head: 0
    property real tail: 0
    property real head2: 0
    property real tail2: 0

    ParallelAnimation {
        running: root.running && root.visible && !Theme.calm
        loops: Animation.Infinite
        SequentialAnimation {
            NumberAnimation { target: root; property: "head"; from: 0; to: 1.1; duration: Theme.dur(1100); easing.type: Easing.InOutCubic }
            PauseAnimation { duration: Theme.dur(900) }
        }
        SequentialAnimation {
            PauseAnimation { duration: Theme.dur(250) }
            NumberAnimation { target: root; property: "tail"; from: 0; to: 1.1; duration: Theme.dur(1000); easing.type: Easing.InOutCubic }
            PauseAnimation { duration: Theme.dur(750) }
        }
        SequentialAnimation {
            PauseAnimation { duration: Theme.dur(1000) }
            NumberAnimation { target: root; property: "head2"; from: 0; to: 1.1; duration: Theme.dur(800); easing.type: Easing.OutCubic }
            PauseAnimation { duration: Theme.dur(200) }
        }
        SequentialAnimation {
            PauseAnimation { duration: Theme.dur(1200) }
            NumberAnimation { target: root; property: "tail2"; from: 0; to: 1.1; duration: Theme.dur(800); easing.type: Easing.InOutCubic }
        }
    }

    // Calm (a job is running, Theme.calm): one still segment -- no redraws
    // at all (with the card full each redraw costs the job; see Spinner).
    readonly property bool still: root.running && Theme.calm
    function settle() { if (still) { root.tail = 0.35; root.head = 0.65; root.head2 = root.tail2 = 0 } }
    onStillChanged: settle()
    Component.onCompleted: settle()

    Rectangle {
        x: root.tail * root.width
        width: Math.max(0, (root.head - root.tail) * root.width)
        height: root.height; radius: height / 2
        color: root.tint
    }
    Rectangle {
        x: root.tail2 * root.width
        width: Math.max(0, (root.head2 - root.tail2) * root.width)
        height: root.height; radius: height / 2
        color: root.tint
    }
}
