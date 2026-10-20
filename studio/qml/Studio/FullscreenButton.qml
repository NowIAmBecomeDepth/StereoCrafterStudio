import QtQuick
import Studio

// The full-screen icon in a previewer's top-right corner (user, 2026-10-15: in place of the Full
// Preview button). Over the picture whatever the theme: a dark round-cornered square, a white glyph.
Rectangle {
    id: root
    property string tip: ""
    signal clicked()
    width: 32
    height: 32
    radius: 8
    color: hit.containsMouse ? "#d0303030" : "#90181818"
    border.color: "#50ffffff"
    border.width: 1
    Behavior on color { ColorAnimation { duration: Theme.dur(120) } }
    Icon {
        anchors.centerIn: parent
        glyph: Theme.icon.fullscreen
        size: 15
        color: root.enabled ? "white" : "#80ffffff"
    }
    MouseArea {
        id: hit
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: root.clicked()
    }
    HoverTip { tip: root.tip }
}
