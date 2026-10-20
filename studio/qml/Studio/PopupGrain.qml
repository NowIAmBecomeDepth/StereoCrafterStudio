import QtQuick
import QtQuick.Effects
import QtQuick.Window
import Studio

// The window's film grain on a popup's card (user, 2026-10-02): popups sit
// above Main's grain layer, so they were clean. Put one inside the card's
// background with the card's radius: the same tile as the window, one grain
// per screen pixel, cut to the rounded corners. Built only while the card is
// on screen (a closed dropdown list makes nothing) and with the theme
// popout's Film Grain switch on.
Item {
    id: g
    property real radius: 0
    anchors.fill: parent

    Loader {
        objectName: "popupGrain"
        anchors.fill: parent
        active: Theme.grainSource !== "" && !!g.parent && g.parent.visible && g.width > 0
        sourceComponent: Item {
            readonly property real dpr: g.Window.window && g.Window.window.screen ? g.Window.window.screen.devicePixelRatio : 1
            layer.enabled: g.radius > 0
            layer.effect: MultiEffect {
                maskEnabled: true
                maskSource: mask
                maskThresholdMin: 0.5
                maskSpreadAtMin: 1.0
            }
            clip: g.radius <= 0
            Image {
                objectName: "popupGrainTile"
                width: parent.width * parent.dpr
                height: parent.height * parent.dpr
                scale: 1 / parent.dpr
                transformOrigin: Item.TopLeft
                fillMode: Image.Tile
                smooth: false
                source: Theme.grainSource
            }
            Rectangle {
                id: mask
                anchors.fill: parent
                radius: g.radius
                visible: false
                layer.enabled: true
            }
        }
    }
}
