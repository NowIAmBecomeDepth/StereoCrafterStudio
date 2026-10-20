import QtQuick
import QtQuick.Controls.Basic
import Studio

// Thin until hovered, fades out when the page stops moving.
ScrollBar {
    id: bar
    policy: ScrollBar.AsNeeded
    minimumSize: 0.06
    padding: 3
    hoverEnabled: true
    // shown, faintly, whenever there is more to scroll -- not only while moving
    // (user, 2026-10-11: a long Settings page didn't look scrollable)
    property bool alwaysShow: false

    contentItem: Rectangle {
        implicitWidth: bar.hovered || bar.pressed ? 8 : 4
        radius: width / 2
        color: bar.pressed ? Theme.accent : (bar.hovered ? Theme.fgMuted : Theme.fgFaint)
        opacity: bar.active || bar.hovered ? 1 : (bar.alwaysShow && bar.size < 1 ? 0.55 : 0)
        Behavior on implicitWidth { NumberAnimation { duration: Theme.dur(160); easing.type: Easing.OutCubic } }
        Behavior on color { ColorAnimation { duration: Theme.dur(160) } }
        Behavior on opacity { NumberAnimation { duration: Theme.dur(bar.active ? 120 : 700) } }
    }
    background: null
}
