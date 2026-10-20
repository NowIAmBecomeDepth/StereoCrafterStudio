import QtQuick
import QtQuick.Layouts
import Studio

// One setting: label and control, with the setting's help_content.json text
// as a tooltip over the label's text and the control (not the empty rest of
// the row). `shown` folds the row away, for v1's settings that only exist
// while their checkbox is ticked.
Item {
    id: row
    property string label
    property string help: ""
    property int labelWidth: 150
    property bool shown: true
    default property alias control: slot.data

    Layout.fillWidth: true
    property real reveal: shown ? 1 : 0
    Behavior on reveal { NumberAnimation { duration: Theme.dur(300); easing.type: Easing.InOutCubic } }
    implicitHeight: Math.max(Theme.rowHeight, slot.childrenRect.height) * reveal
    opacity: reveal
    visible: reveal > 0.001
    clip: reveal < 1

    Text {
        id: lbl
        width: row.labelWidth
        height: Theme.rowHeight
        verticalAlignment: Text.AlignVCenter
        text: row.label
        color: row.enabled ? Theme.fg : Theme.fgFaint
        font.family: Theme.bodyFont
        font.pixelSize: Theme.bodySize
        elide: Text.ElideRight
        Behavior on color { ColorAnimation { duration: Theme.dur(160) } }
    }
    Item {
        id: slot
        x: row.labelWidth + 8
        width: row.width - x
        height: childrenRect.height
    }
    // the label's painted text, for the tooltip's hover
    Item {
        id: lblHit
        width: Math.min(lbl.contentWidth, lbl.width)
        height: lbl.height
    }
    // the control's own extent (a row's control is laid out inside the slot)
    Item {
        id: ctlHit
        x: slot.x + slot.childrenRect.x
        y: slot.y + slot.childrenRect.y
        width: slot.childrenRect.width
        height: slot.childrenRect.height
    }
    HoverTip { tip: row.help; hotItems: [lblHit, ctlHit] }
}
