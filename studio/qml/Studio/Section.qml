import QtQuick
import QtQuick.Layouts
import Studio

// A titled settings group: v1's _section, heading in Bahnschrift SemiBold
// caps. `shown` folds the whole group away (height and opacity together)
// instead of popping it out of the layout. `headerRight` takes items to sit
// on the heading line, the way v1 seats the chapter picker and import button.
Rectangle {
    id: sec
    property string title
    property bool shown: true
    property bool bodyShown: true
    default property alias content: body.data
    property alias headerRight: headerRow.data
    property alias spacing: body.spacing

    Layout.fillWidth: true
    property real reveal: shown ? 1 : 0
    Behavior on reveal { NumberAnimation { duration: Theme.dur(380); easing.type: Easing.InOutCubic } }
    implicitHeight: (column.implicitHeight + 2 * Theme.sectionPad) * reveal
    opacity: reveal
    visible: reveal > 0.001
    clip: reveal < 1

    radius: 12
    color: Theme.panel
    border.width: Theme.outlineWidth
    border.color: Theme.outline
    Behavior on border.color { ColorAnimation { duration: Theme.dur(200) } }

    ColumnLayout {
        id: column
        x: 14
        y: Theme.sectionPad
        width: parent.width - 28
        spacing: 6

        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Text {
                text: Theme.headingText(sec.title)
                color: Theme.fg
                font.family: Theme.headingFont
                font.pixelSize: Theme.headingSize
                font.weight: Font.DemiBold
                font.letterSpacing: 0.4
                Layout.alignment: Qt.AlignVCenter
            }
            Item { Layout.fillWidth: true }
            // May shrink (to its items' minimums) rather than run past the
            // section's edge in a narrow window: the chapter dropdown gives
            // way first (user, 2026-09-26).
            RowLayout {
                id: headerRow
                spacing: 8
                Layout.alignment: Qt.AlignVCenter
                Layout.fillWidth: true
                Layout.maximumWidth: implicitWidth
            }
        }
        // bodyShown false: just the heading line (a chapter's folders put
        // away on a page whose section holds nothing else).
        Rectangle {
            visible: sec.bodyShown
            Layout.fillWidth: true
            implicitHeight: 1
            color: Theme.border
        }
        ColumnLayout {
            id: body
            visible: sec.bodyShown
            Layout.fillWidth: true
            Layout.topMargin: 2
            spacing: Theme.rowGap
        }
    }
}
