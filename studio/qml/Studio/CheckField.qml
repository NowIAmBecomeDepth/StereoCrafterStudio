import QtQuick
import QtQuick.Layouts
import Studio

// A checkbox bound to one settings key; its help shows on hover.
Item {
    id: root
    property var store: null
    property string key: ""
    property string text: ""
    property string help: ""
    property bool shown: true
    // function () -> bool, asked before a tick goes in: false leaves the box off (it said why)
    property var guard: null

    Layout.fillWidth: true
    implicitWidth: check.implicitWidth
    property real reveal: shown ? 1 : 0
    Behavior on reveal { NumberAnimation { duration: Theme.dur(300); easing.type: Easing.InOutCubic } }
    implicitHeight: Theme.rowHeight * reveal
    opacity: reveal
    visible: reveal > 0.001
    clip: reveal < 1

    AnimCheck {
        id: check
        anchors.verticalCenter: parent.verticalCenter
        text: root.text
        checked: root.store && root.key ? !!root.store[root.key] : false
        onToggled: {
            if (checked && root.guard && !root.guard()) {
                checked = Qt.binding(() => root.store && root.key ? !!root.store[root.key] : false)
                return
            }
            if (root.store && root.key) root.store[root.key] = checked
        }
        opacity: enabled ? 1 : Theme.dim(0.45)
    }
    // over the box and its text only, not the empty rest of the row
    HoverTip { tip: root.help; hotItems: [check] }
}
