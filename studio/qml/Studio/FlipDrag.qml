import QtQuick
import Studio

// The preview's flip card, turned over by hand (user, 2026-10-10): press near
// the picture's left or right edge and drag across -- the card turns with the
// pointer, the edge you hold staying under it. Past halfway it is the other
// side (that side's frame is asked for there, and the card waits edge-on until
// it arrives, at most half a second, as a click's flip does); let go and it
// finishes the turn, or falls back if you didn't drag it past halfway. Dragged
// back over halfway, it turns back. A click on the picture still flips.
//
// Over the preview's Flickable, not inside the turning picture: the pointer is
// read in plain, unturned coordinates. The page gives the card (its angle,
// busy, turnOver() and fresh()), the picture's unturned rectangle in this
// item's coordinates, the preview (its frame notifies) and when it may turn.
Item {
    id: fd
    objectName: "flipDrag"

    property var card: null
    property var pv: null
    property real picX: 0
    property real picY: 0
    property real picW: 0
    property real picH: 0
    property bool allowed: true

    // the grip at each edge: a band of the picture, not the whole of it (a click
    // in the middle stays a click, a drag there a pan)
    readonly property real grip: Math.max(24, Math.min(90, picW * 0.14))
    readonly property bool dragging: leftGrip.active || rightGrip.active

    property int side: 0                // +1 the right edge held, -1 the left
    property bool holding: false        // a drag of ours is turning the card
    property bool turned: false         // past halfway: the other side is up
    property bool waiting: false        // turned, the new side's frame not here yet: edge-on
    property bool settleAfterWait: false
    property real lastX: 0

    function follow(mx) {
        lastX = mx
        if (picW <= 0) return
        const u = Math.max(-1, Math.min(1, side * (mx - (picX + picW / 2)) / (picW / 2)))
        const a = Math.acos(u) * 180 / Math.PI          // 0 flat, 90 edge-on, 180 turned right over
        if (a >= 90 !== turned) {
            turned = a >= 90
            waiting = true
            waitTimer.restart()
            card.turnOver()
        }
        card.angle = waiting ? (turned ? -side * 90 : side * 90)
                             : (turned ? side * (a - 180) : side * a)
    }
    function begin(s, mx) {
        if (!card || card.busy) return false
        side = s
        turned = false
        waiting = false
        settleAfterWait = false
        settle.stop()
        card.busy = true
        holding = true
        follow(mx)
        return true
    }
    function release() {
        holding = false
        if (waiting) { settleAfterWait = true; return }   // the new side lands once it's here
        settle.from = card.angle
        settle.start()
    }
    function arrived() {
        waitTimer.stop()
        waiting = false
        if (settleAfterWait) { settleAfterWait = false; card.angle = turned ? -side * 90 : side * 90; settle.from = card.angle; settle.start() }
        else if (holding) follow(lastX)
    }

    Timer { id: waitTimer; interval: 500; onTriggered: fd.arrived() }
    Connections {
        target: fd.pv
        ignoreUnknownSignals: true
        function onFrameChanged() { if (fd.waiting && fd.card.fresh()) fd.arrived() }
        function onChanged() { if (fd.waiting && fd.card.fresh()) fd.arrived() }
    }
    NumberAnimation {
        id: settle
        target: fd.card
        property: "angle"
        to: 0
        duration: Theme.dur(220)
        easing.type: Easing.OutCubic
        onFinished: if (fd.card) fd.card.busy = false
    }

    component Grip: Item {
        id: g
        property int edge: 1
        readonly property bool active: dh.active
        y: fd.picY
        height: fd.picH
        width: fd.grip
        HoverHandler {
            enabled: fd.allowed && !Theme.modalOpen
            cursorShape: dh.active ? Qt.ClosedHandCursor : Qt.OpenHandCursor
        }
        DragHandler {
            id: dh
            objectName: g.edge > 0 ? "flipDragRight" : "flipDragLeft"
            enabled: fd.allowed && !Theme.modalOpen
            target: null
            yAxis.enabled: false
            grabPermissions: PointerHandler.CanTakeOverFromAnything
            onActiveChanged: {
                if (active) fd.begin(g.edge, g.mapToItem(fd, centroid.position.x, 0).x)
                else if (fd.holding && fd.side === g.edge) fd.release()
            }
            onCentroidChanged: if (active && fd.holding && fd.side === g.edge) fd.follow(g.mapToItem(fd, centroid.position.x, 0).x)
        }
    }
    Grip { id: leftGrip; edge: -1; x: fd.picX }
    Grip { id: rightGrip; edge: 1; x: fd.picX + fd.picW - width }
}
