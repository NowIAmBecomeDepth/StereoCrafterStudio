import QtQuick
import QtQuick.Layouts
import Studio

// Two settings side by side, v1's "Label | Entry | Label | Entry" grid rows.
// Children share the width equally (give each Layout.fillWidth; an empty
// Item holds the second half open when a row has only one setting).
// `shown` folds the whole row away, as FieldRow does for a single setting.
Item {
    id: pair
    property bool shown: true
    default property alias content: row.data
    property alias spacing: row.spacing

    Layout.fillWidth: true
    property real reveal: shown ? 1 : 0
    Behavior on reveal { NumberAnimation { duration: Theme.dur(300); easing.type: Easing.InOutCubic } }
    implicitHeight: row.implicitHeight * reveal
    opacity: reveal
    visible: reveal > 0.001
    clip: reveal < 1

    RowLayout {
        id: row
        width: parent.width
        spacing: 26
    }
}
