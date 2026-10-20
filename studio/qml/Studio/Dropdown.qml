import QtQuick
import QtQuick.Controls.Basic
import Studio

// A dropdown bound to one settings key. The list unfolds from the box with
// a scale and fade, the hovered row is tracked by a sliding highlight, and the
// chevron turns.
ComboBox {
    id: box
    property var store: null
    property string key: ""
    property int boxWidth: 170
    // measure: true -> fitWidth is the width the longest option needs (set
    // boxWidth: fitWidth); off by default, so the other boxes measure nothing
    // (user, 2026-10-11: the previews' Front / Back cut their options short)
    property bool measure: false
    readonly property int fitWidth: {
        if (!measure) return 0
        // read so the width follows the font (advanceWidth() is a call: it adds no dependency) --
        // measured in a fallback font before the theme's had loaded, the boxes came out too narrow
        void boldMetrics.font
        void boldMetrics.height
        const m = model || []
        let w = 0
        for (let i = 0; i < m.length; ++i) w = Math.max(w, boldMetrics.advanceWidth(String(m[i])))
        return Math.ceil(w) + 46     // the text's padding and the chevron (contentItem / indicator)
    }
    // the picked row is DemiBold in the list: measured that way, it fits there too
    FontMetrics { id: boldMetrics; font.family: Theme.bodyFont; font.pixelSize: Theme.bodySize; font.weight: Font.DemiBold }
    // The open list is as wide as its longest option, however narrow the box (user, 2026-10-11:
    // a small window cut Front / Back's options short in the list too). Measured while open only.
    readonly property int listFitWidth: {
        if (!popup.visible) return 0
        void boldMetrics.font
        void boldMetrics.height
        const m = model || []
        let w = 0
        for (let i = 0; i < m.length; ++i) w = Math.max(w, boldMetrics.advanceWidth(String(m[i])))
        return Math.ceil(w) + 48     // the row's padding, the list's, and room for its scroll bar
    }

    implicitWidth: boxWidth
    implicitHeight: Theme.rowHeight
    width: boxWidth
    font.family: Theme.bodyFont
    font.pixelSize: Theme.bodySize
    hoverEnabled: true

    // -1 (no row marked) for a stored value the list lacks, not row 0
    // (audit 2026-09-28, minor)
    currentIndex: store && key && model ? model.indexOf(store[key]) : -1
    // Options listed but not to be picked (greyed in the list, `disabledTip` over them):
    // Glasses-Free 3D without its display, or on another screen (user, 2026-10-11).
    property var disabledItems: []
    property string disabledTip: ""
    onActivated: (i) => {
        if ((disabledItems || []).indexOf(model[i]) >= 0) {
            // not taken: the row stays on the stored value
            currentIndex = Qt.binding(() => store && key && model ? model.indexOf(store[key]) : -1)
            return
        }
        if (store && key) store[key] = model[i]
    }

    contentItem: Text {
        leftPadding: 10
        rightPadding: 28
        // The stored value, even one the list lacks, never a stand-in.
        text: box.store && box.key ? String(box.store[box.key]) : box.displayText
        color: box.enabled ? Theme.fg : Theme.fgFaint
        font: box.font
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    indicator: Icon {
        x: box.width - width - 10
        y: (box.height - height) / 2
        glyph: Theme.icon.down
        size: 10
        color: Theme.fgMuted
        rotation: box.popup.visible ? 180 : 0
        Behavior on rotation { NumberAnimation { duration: Theme.dur(260); easing.type: Easing.OutBack } }
    }
    background: Rectangle {
        radius: 7
        color: box.hovered && box.enabled ? Theme.mix(Theme.field, Theme.fg, 0.05) : Theme.field
        border.width: box.visualFocus || box.popup.visible ? 1.5 : 1
        border.color: box.visualFocus || box.popup.visible ? Theme.accent : Theme.border
        opacity: box.enabled ? 1 : Theme.dim(0.55)
        Behavior on color { ColorAnimation { duration: Theme.dur(140) } }
    }

    delegate: ItemDelegate {
        id: item
        required property int index
        required property var modelData
        width: ListView.view ? ListView.view.width : box.width
        height: 30
        hoverEnabled: true
        highlighted: box.highlightedIndex === index
        readonly property bool off: (box.disabledItems || []).indexOf(item.modelData) >= 0
        HoverTip { tip: item.off ? box.disabledTip : "" }
        contentItem: Text {
            leftPadding: 4
            text: item.modelData
            opacity: item.off ? Theme.dim(0.45) : 1
            color: item.off ? Theme.fgFaint : item.index === box.currentIndex ? Theme.accentInk : Theme.fg
            font.family: Theme.bodyFont
            font.pixelSize: Theme.bodySize
            font.weight: item.index === box.currentIndex ? Font.DemiBold : Font.Normal
            verticalAlignment: Text.AlignVCenter
        }
        background: null
    }

    popup: Popup {
        y: box.height + 4
        margins: 8              // kept inside the window, not cut off at its edge (2026-10-11)
        width: Math.max(box.width, 140, box.listFitWidth)
        implicitHeight: Math.min(list.contentHeight + 8, 320)
        padding: 4
        transformOrigin: Popup.Top
        enter: Transition {
            ParallelAnimation {
                NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.dur(140) }
                NumberAnimation { property: "scale"; from: 0.9; to: 1; duration: Theme.dur(220); easing.type: Easing.OutCubic }
            }
        }
        exit: Transition { NumberAnimation { property: "opacity"; to: 0; duration: Theme.dur(100) } }
        contentItem: ListView {
            id: list
            clip: true
            implicitHeight: contentHeight
            model: box.popup.visible ? box.delegateModel : null
            currentIndex: box.highlightedIndex
            highlightMoveDuration: Theme.dur(140)
            highlight: Rectangle { radius: 6; color: Theme.alpha(Theme.accent, 0.16) }
            ScrollBar.vertical: SlimScrollBar {}
        }
        background: Rectangle {
            PopupGrain { radius: 9 }   // the window's film grain
            radius: 9
            color: Theme.field
            border.width: 1
            border.color: Theme.borderStrong
        }
    }
}
