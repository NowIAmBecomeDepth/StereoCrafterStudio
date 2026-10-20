import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Window
import Studio

// v1's hover tooltip (core/ui/widgets.py Tooltip): hovering anywhere over the
// parent -- label and control alike -- shows the help after 600 ms, 20px in
// and just below (above, when it would not fit below: pushed back into the
// window it covered the pointer, and a click landed on the tip instead of the
// setting -- user, 2026-09-26); a click hides it until the pointer leaves,
// a click on the tip itself too. Long help wraps
// into a block at most maxWidth wide rather than one line across the window.
// Declare it inside the item it explains. The handlers are re-parented onto
// that item, because hover reaches an item's ancestors, not the siblings
// under it.
Item {
    id: root
    property string tip: ""
    // The popup is made on the first hover, not with the page (Studio audit
    // 2026-09-28, PERF: ~370 of them, each a popup, its text, background and
    // transitions, all built at startup and almost none ever shown).
    property var popup: null
    readonly property bool showing: !!popup && popup.visible
    readonly property size popupSize: popup ? Qt.size(popup.width, popup.height) : Qt.size(0, 0)
    // The drawn tip, where Qt actually put it (checks: never over the pointer;
    // popup.y keeps the asked-for place even when Qt pushes it back in).
    readonly property Item bubble: popup ? popup.background : null
    property bool dismissed: false
    readonly property int maxWidth: 340
    // Only over these (user, 2026-09-30: not from anywhere on the row's line):
    // the label's own text and the control itself. Empty: the whole parent.
    property var hotItems: []
    readonly property bool overHot: {
        if (!hotItems || hotItems.length === 0) return true
        const p = hover.point.position
        const host = root.parent
        if (!host) return false
        for (let i = 0; i < hotItems.length; ++i) {
            const it = hotItems[i]
            if (!it || !it.visible || it.width <= 0) continue
            const q = it.mapFromItem(host, p.x, p.y)
            if (q.x >= 0 && q.y >= 0 && q.x <= it.width && q.y <= it.height) return true
        }
        return false
    }
    width: 0
    height: 0

    HoverHandler {
        id: hover
        parent: root.parent
        onHoveredChanged: {
            if (!hovered) root.dismissed = false
            else if (!root.popup && root.tip.length > 0) root.popup = tipComponent.createObject(root)
        }
    }
    PointHandler {
        parent: root.parent
        acceptedButtons: Qt.AllButtons
        onActiveChanged: if (active) root.dismissed = true
    }
    // A text field takes the press before this row's handlers see it, but a
    // click there moves the focus -- so a focus change while hovered is a click.
    Connections {
        target: root.Window.window
        function onActiveFocusItemChanged() { if (hover.hovered) root.dismissed = true }
    }

    Component {
        id: tipComponent
    ToolTip {
        id: popup
        parent: root.parent
        visible: hover.hovered && root.overHot && !root.dismissed && root.tip.length > 0
        delay: 600
        // Beginner Mode: v1's plain-language text for the same help (beginner.py).
        text: typeof Beginner !== "undefined" && Beginner.on ? Beginner.tip(root.tip) : root.tip
        x: 20
        // Below the row if it fits in the window, else above it. The row's
        // place in the window is not bindable, so this re-evaluates on what
        // is: opening, and the height settling once the text has wrapped.
        y: place(implicitHeight, visible)
        function place(h, open) {
            const row = parent
            const below = (row ? row.height : 0) + 4
            const win = root.Window.window
            if (!row || !win || !open) return below
            // The page is zoomed (Main.qml) but the tip is drawn full size in
            // the window's overlay: `h` is window pixels, a row's y is not.
            const rowTop = row.mapToItem(null, 0, 0).y
            const zoom = row.mapToItem(null, 0, 1).y - rowTop || 1
            const rowBottom = rowTop + row.height * zoom
            if (rowBottom + 4 + h <= win.height - 8 || rowTop - 4 - h < 8)
                return below
            return -(h + 4) / zoom
        }
        padding: 10
        // The popup sizes itself to its content's implicit width, which for a
        // Text is the unwrapped line: cap the POPUP, and the text wraps inside.
        width: Math.min(implicitWidth, root.maxWidth)
        transformOrigin: Popup.TopLeft
        contentItem: Text {
            text: popup.text
            color: Theme.fg
            font.family: Theme.bodyFont
            font.pixelSize: 13
            wrapMode: Text.WordWrap
        }
        background: Rectangle {
            radius: 8
            color: Theme.field
            border.width: 1
            border.color: Theme.borderStrong
            // Too tall to fit either side, Qt keeps it inside the window, over
            // the pointer: a click on it hides it like a click on the row.
            TapHandler { onPressedChanged: if (pressed) root.dismissed = true }
        }
        enter: Transition {
            ParallelAnimation {
                NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.dur(160) }
                NumberAnimation { property: "scale"; from: 0.92; to: 1; duration: Theme.dur(220); easing.type: Easing.OutCubic }
            }
        }
        exit: Transition {
            NumberAnimation { property: "opacity"; to: 0; duration: Theme.dur(100) }
        }
    }
    }
}
