import QtQuick
import Studio

// iOS-style segmented control: one thumb springs between segments and
// stretches to each one's width.
Rectangle {
    id: root
    property var model: ["One", "Two", "Three"]
    property int currentIndex: 0
    property int fontSize: 13
    property bool compact: false
    // Controlled: a click only reports the choice (activated) and leaves
    // currentIndex to its binding, e.g. "the preset the settings match".
    property bool controlled: false
    // Segments listed but not to be picked: greyed, a click does nothing; `hoveredDisabled` while
    // the pointer is over one, for the page's tip to say why (user, 2026-10-11: Glasses-Free 3D)
    property var disabledItems: []
    readonly property bool hoveredDisabled: _hoverOff > 0
    property int _hoverOff: 0
    signal activated(int index)

    implicitWidth: row.implicitWidth + 8
    implicitHeight: compact ? 30 : 36
    radius: height / 2
    color: Theme.trough
    border.width: 1
    border.color: Theme.border

    Rectangle {
        id: thumb
        readonly property Item target: repeater.count > root.currentIndex ? repeater.itemAt(root.currentIndex) : null
        x: target ? row.x + target.x : 4
        width: target ? target.width : 0
        y: 3
        height: root.height - 6
        radius: height / 2
        color: Theme.accent
        Behavior on x { enabled: !Theme.calm; SpringAnimation { spring: 3.6; damping: 0.3; epsilon: 0.05 } }
        Behavior on width { enabled: !Theme.calm; SpringAnimation { spring: 3.6; damping: 0.3; epsilon: 0.05 } }
    }

    Row {
        id: row
        x: 4
        anchors.verticalCenter: parent.verticalCenter
        Repeater {
            id: repeater
            model: root.model
            Item {
                id: seg
                required property int index
                required property var modelData
                readonly property bool current: index === root.currentIndex
                readonly property bool off: (root.disabledItems || []).indexOf(modelData) >= 0
                opacity: off ? Theme.dim(0.45) : 1
                width: segText.implicitWidth + (root.compact ? 22 : 30)
                height: root.height - 6
                Text {
                    id: segText
                    anchors.centerIn: parent
                    text: seg.modelData
                    color: seg.current ? Theme.accentFg : (hover.hovered ? Theme.fg : Theme.fgMuted)
                    font.family: Theme.bodyFont
                    font.pixelSize: root.fontSize
                    font.weight: seg.current ? Font.DemiBold : Font.Normal
                    Behavior on color { ColorAnimation { duration: Theme.dur(200) } }
                }
                HoverHandler {
                    id: hover
                    cursorShape: seg.off ? Qt.ArrowCursor : Qt.PointingHandCursor
                    readonly property bool overOff: hovered && seg.off
                    onOverOffChanged: root._hoverOff += overOff ? 1 : -1
                }
                TapHandler {
                    enabled: !seg.off
                    onTapped: {
                        if (!root.controlled) root.currentIndex = seg.index
                        root.activated(seg.index)
                    }
                }
            }
        }
    }
}
