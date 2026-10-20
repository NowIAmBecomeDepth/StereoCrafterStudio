import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Effects
import Studio

// Header button showing the current palette. The popup grows out of the
// button, and its swatches cascade in one after another.
Item {
    id: root
    property bool compact: false
    implicitWidth: button.width
    implicitHeight: 36

    Rectangle {
        id: button
        width: row.implicitWidth + 24
        height: 36
        radius: 18
        color: hover.hovered || popup.opened ? Theme.hover : "transparent"
        border.width: 1
        border.color: Theme.border
        Behavior on color { ColorAnimation { duration: Theme.dur(160) } }
        Row {
            id: row
            anchors.centerIn: parent
            spacing: 9
            Swatch { anchors.verticalCenter: parent.verticalCenter; p: Theme.p; size: 18 }
            MorphLabel {
                visible: !root.compact
                anchors.verticalCenter: parent.verticalCenter
                text: Theme.name
                pixelSize: 13
                family: Theme.bodyFont
            }
            Icon {
                anchors.verticalCenter: parent.verticalCenter
                glyph: Theme.icon.down
                size: 10
                color: Theme.fgMuted
                rotation: popup.opened ? 180 : 0
                Behavior on rotation { NumberAnimation { duration: Theme.dur(300); easing.type: Easing.OutBack } }
            }
        }
        HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: popup.opened ? popup.close() : popup.open() }
    }

    component Swatch: Rectangle {
        property var p
        property int size: 28
        width: size; height: size; radius: size / 2
        color: p ? p.bg : "black"
        border.width: 1
        border.color: Theme.borderStrong
        Rectangle {
            anchors.centerIn: parent
            width: parent.width * 0.5; height: width; radius: width / 2
            color: parent.p ? parent.p.accent : "white"
        }
    }

    Popup {
        id: popup
        y: button.height + 8
        x: button.width - width
        width: 344
        padding: 14
        transformOrigin: Popup.TopRight
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent

        enter: Transition {
            ParallelAnimation {
                NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.dur(160) }
                NumberAnimation { property: "scale"; from: 0.85; to: 1; duration: Theme.dur(320); easing.type: Easing.OutBack }
            }
        }
        exit: Transition {
            ParallelAnimation {
                NumberAnimation { property: "opacity"; to: 0; duration: Theme.dur(140) }
                NumberAnimation { property: "scale"; to: 0.92; duration: Theme.dur(140) }
            }
        }

        background: Rectangle {
            PopupGrain { radius: 14 }   // the window's film grain
            radius: 14
            color: Theme.field
            border.width: 1
            border.color: Theme.borderStrong
            layer.enabled: true
            layer.effect: MultiEffect {
                shadowEnabled: true
                shadowColor: "#000000"
                shadowBlur: 1.0
                shadowOpacity: Theme.isDark ? 0.6 : 0.2
                shadowVerticalOffset: 8
            }
        }

        contentItem: Column {
            spacing: 10
            Text {
                text: "Studio palettes"
                color: Theme.fgMuted
                font.family: Theme.bodyFont
                font.pixelSize: 12
            }
            Grid {
                columns: 4
                spacing: 6
                Repeater {
                    model: Theme.palettes
                    Item {
                        id: cell
                        required property int index
                        required property var modelData
                        readonly property bool current: index === Theme.index
                        width: 74; height: 66
                        opacity: 0
                        scale: 0.6

                        // Cascade: each swatch waits a little longer.
                        SequentialAnimation {
                            id: cascade
                            PauseAnimation { duration: Theme.dur(18 * cell.index) }
                            ParallelAnimation {
                                NumberAnimation { target: cell; property: "opacity"; to: 1; duration: Theme.dur(180) }
                                NumberAnimation { target: cell; property: "scale"; to: 1; duration: Theme.dur(360); easing.type: Easing.OutBack }
                            }
                        }
                        Connections {
                            target: popup
                            function onAboutToShow() { cell.opacity = 0; cell.scale = 0.6; cascade.restart() }
                        }

                        Rectangle {
                            anchors.fill: parent
                            radius: 10
                            color: cellHover.hovered ? Theme.hover : "transparent"
                            border.width: cell.current ? 1.5 : 0
                            border.color: Theme.accent
                        }
                        Swatch {
                            id: sw
                            anchors.horizontalCenter: parent.horizontalCenter
                            y: 8
                            p: cell.modelData
                            scale: cellHover.hovered ? 1.15 : 1
                            Behavior on scale { enabled: !Theme.calm; SpringAnimation { spring: 5; damping: 0.3 } }
                        }
                        Text {
                            anchors.horizontalCenter: parent.horizontalCenter
                            y: 42
                            width: parent.width - 4
                            horizontalAlignment: Text.AlignHCenter
                            elide: Text.ElideRight
                            text: cell.modelData.name
                            color: cell.current ? Theme.fg : Theme.fgMuted
                            font.family: Theme.bodyFont
                            font.pixelSize: 11
                        }
                        HoverHandler { id: cellHover; cursorShape: Qt.PointingHandCursor }
                        TapHandler { onTapped: Theme.setPalette(cell.index) }
                    }
                }
            }
        }
    }
}
