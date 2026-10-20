import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import Studio

// v1's "Current Clip Info" section: label / value rows, the value easing to
// each new clip. Open Console sits on the heading line (v1 put it on a row of
// its own under the values, which cost a row the page no longer has).
//
//   rows: [{ label, value, help }]  -- or a pair on one line, as v1 Splatting
//         lays out its last rows: { label, value, label2, value2 }
Section {
    id: info
    title: "Current Clip Info"
    property var rows: []
    property var consoleRef: null
    property int labelWidth: 110
    property int labelWidth2: 80

    headerRight: [
        AccentButton {
            objectName: "openConsole"
            visible: info.consoleRef !== null
            text: "Open Console"
            kind: "ghost"
            iconGlyph: Theme.icon.console
            implicitHeight: 26
            onClicked: info.consoleRef.open = true
        }
    ]

    Repeater {
        model: info.rows
        RowLayout {
            id: row
            required property var modelData
            Layout.fillWidth: true
            Layout.preferredHeight: 20
            spacing: 10
            Text {
                text: row.modelData.label
                color: Theme.fgMuted
                font.family: Theme.bodyFont
                font.pixelSize: Theme.bodySize
                Layout.preferredWidth: info.labelWidth
                Layout.alignment: Qt.AlignVCenter
                // On the label: the value has a tooltip of its own when truncated.
                HoverTip { tip: row.modelData.help || "" }
            }
            Text {
                id: value
                text: row.modelData.value || "N/A"
                color: Theme.fg
                font.family: Theme.bodyFont
                font.pixelSize: Theme.bodySize
                elide: Text.ElideMiddle
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignVCenter
                // A new value brightens for a moment, so a new clip or a
                // finished job is noticed without watching for it.
                onTextChanged: flash.restart()
                SequentialAnimation on opacity {
                    id: flash
                    running: false
                    NumberAnimation { to: 0.25; duration: Theme.dur(90) }
                    NumberAnimation { to: 1; duration: Theme.dur(380); easing.type: Easing.OutCubic }
                }
                // the whole value, when it is cut short
                HoverTip { tip: value.truncated ? value.text : "" }
            }
            Text {
                visible: !!row.modelData.label2
                text: row.modelData.label2 || ""
                color: Theme.fgMuted
                font.family: Theme.bodyFont
                font.pixelSize: Theme.bodySize
                horizontalAlignment: Text.AlignRight
                Layout.preferredWidth: info.labelWidth2
                Layout.alignment: Qt.AlignVCenter
            }
            Text {
                visible: !!row.modelData.label2
                text: row.modelData.value2 || "N/A"
                color: Theme.fg
                font.family: Theme.bodyFont
                font.pixelSize: Theme.bodySize
                elide: Text.ElideMiddle
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignVCenter
            }
        }
    }
}
