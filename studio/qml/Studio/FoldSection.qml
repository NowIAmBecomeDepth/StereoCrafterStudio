import QtQuick
import QtQuick.Layouts
import QtQuick.Shapes
import Studio

// v1's collapsible section (splatting_gui._add_section_toggle): the heading
// carries a checkbox in its corner, and unticked the body folds away,
// leaving just the heading bar. `open` is the checkbox; bind it to the
// setting that remembers it, and use `toggled` for what the tick does
// (v1's Special FX cards reset their effect when unticked).
Rectangle {
    id: sec
    property string title
    property bool open: true
    property bool centred: false           // v1 centres the Special FX card titles
    // A wide "V" in the corner instead of the checkbox, pointing down while
    // folded and up while open; the whole heading toggles it (Merging's
    // Shadow fold, user 2026-09-26).
    property bool chevron: false
    property string help: ""
    default property alias content: body.data
    signal toggled(bool on)

    Layout.fillWidth: true
    property real reveal: open ? 1 : 0
    Behavior on reveal { NumberAnimation { duration: Theme.dur(320); easing.type: Easing.InOutCubic } }
    implicitHeight: header.height + 2 * Theme.sectionPad - 4
                    + (divider.height + 6 + body.implicitHeight + 2) * reveal

    radius: 12
    color: Theme.panel
    border.width: Theme.outlineWidth
    border.color: Theme.outline
    Behavior on border.color { ColorAnimation { duration: Theme.dur(200) } }
    clip: true

    Item {
        id: header
        x: 14
        y: Theme.sectionPad - 2
        width: parent.width - 28
        height: 26
        Text {
            anchors.verticalCenter: parent.verticalCenter
            x: sec.centred ? (parent.width - width) / 2 - 12 : 0
            text: Theme.headingText(sec.title)
            color: Theme.fg
            font.family: Theme.headingFont
            font.pixelSize: Theme.headingSize
            font.weight: Font.DemiBold
            font.letterSpacing: 0.4
        }
        AnimCheck {
            id: box
            visible: !sec.chevron
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            text: ""
            checked: sec.open
            // A click breaks the binding; put it back so the box keeps
            // following `open` when something else changes it.
            onToggled: { sec.toggled(checked); checked = Qt.binding(() => sec.open) }
        }
        Shape {
            id: vee
            objectName: "foldChevron"
            visible: sec.chevron
            anchors.right: parent.right
            anchors.rightMargin: 4
            anchors.verticalCenter: parent.verticalCenter
            width: 18
            height: 9
            preferredRendererType: Shape.CurveRenderer
            rotation: sec.open ? 180 : 0
            Behavior on rotation { NumberAnimation { duration: Theme.dur(260); easing.type: Easing.InOutCubic } }
            ShapePath {
                strokeColor: headHover.hovered ? Theme.fg : Theme.fgMuted
                strokeWidth: 2.2
                fillColor: "transparent"
                capStyle: ShapePath.RoundCap
                joinStyle: ShapePath.RoundJoin
                startX: 1; startY: 1
                PathLine { x: 9; y: 8 }
                PathLine { x: 17; y: 1 }
            }
        }
        HoverHandler {
            id: headHover
            enabled: sec.chevron
            cursorShape: Qt.PointingHandCursor
        }
        TapHandler {
            enabled: sec.chevron
            onTapped: sec.toggled(!sec.open)
        }
        HoverTip { tip: sec.help }
    }
    Rectangle {
        id: divider
        x: 14
        y: header.y + header.height + 4
        width: parent.width - 28
        height: 1
        color: Theme.border
        opacity: sec.reveal
    }
    ColumnLayout {
        id: body
        x: 14
        y: divider.y + divider.height + 6
        width: parent.width - 28
        spacing: Theme.rowGap
        opacity: sec.reveal
        visible: sec.reveal > 0.001
    }
}
