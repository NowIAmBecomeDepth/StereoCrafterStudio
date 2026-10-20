import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Effects
import Studio

// A themed menu: grows out of whatever opened it, with checkable rows that
// show an animated tick and a sliding hover highlight.
Menu {
    id: menu
    padding: 5
    transformOrigin: Popup.TopRight

    enter: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.dur(140) }
            NumberAnimation { property: "scale"; from: 0.9; to: 1; duration: Theme.dur(240); easing.type: Easing.OutCubic }
        }
    }
    exit: Transition { NumberAnimation { property: "opacity"; to: 0; duration: Theme.dur(100) } }

    background: Rectangle {
        PopupGrain { radius: 10 }   // the window's film grain
        implicitWidth: 260
        radius: 10
        color: Theme.field
        border.width: 1
        border.color: Theme.borderStrong
        layer.enabled: true
        layer.effect: MultiEffect {
            shadowEnabled: true
            shadowColor: "#000000"
            shadowBlur: 0.9
            shadowOpacity: Theme.isDark ? 0.55 : 0.2
            shadowVerticalOffset: 6
        }
    }

    delegate: MenuItem {
        id: mi
        implicitHeight: 32
        hoverEnabled: true
        contentItem: Item {
            implicitWidth: label.implicitWidth + 40
            Icon {
                id: tick
                x: 2
                anchors.verticalCenter: parent.verticalCenter
                glyph: Theme.icon.check
                size: 12
                color: Theme.accentInk
                visible: mi.checkable
                scale: mi.checked ? 1 : 0
                Behavior on scale { NumberAnimation { duration: Theme.dur(260); easing.type: Easing.OutBack } }
            }
            Text {
                id: label
                x: 24
                anchors.verticalCenter: parent.verticalCenter
                text: mi.text
                color: mi.enabled ? Theme.fg : Theme.fgFaint
                font.family: Theme.bodyFont
                font.pixelSize: Theme.bodySize
            }
        }
        indicator: null
        background: Rectangle {
            radius: 6
            color: mi.highlighted && mi.enabled ? Theme.alpha(Theme.accent, 0.16) : "transparent"
            Behavior on color { ColorAnimation { duration: Theme.dur(120) } }
        }
    }
}
