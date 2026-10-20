import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Effects
import QtQuick.Layouts
import Studio

// v1's Resolution Preset menu: category headings with sizes under each.
// Picking one fills Target Width/Height, which stay editable. The button
// names the current size when it matches a preset.
// DepthCrafter's by default; Splatting's Low Res uses it too (user,
// 2026-10-01) with its own presets, keys and apply.
Item {
    id: root
    property var dc
    property var st
    property var presets: dc ? dc.presets : []
    property string widthKey: "target_width"
    property string heightKey: "target_height"
    property var apply: (w, h) => root.dc.applyPreset(w, h)
    // a column with more sizes than this scrolls inside itself (user,
    // 2026-10-05: 4:3 gained two smaller sizes at the top)
    property int maxRows: 3
    // a line above the columns: DepthCrafter names the loaded clip's shape
    // there once the menu shows only its sizes (user, 2026-10-05)
    property string note: dc && dc.presetNote !== undefined ? dc.presetNote : ""
    // no columns at all: where to make some (user, 2026-10-11: nothing is built in)
    property string emptyText: "No sizes yet. Tick some in Settings > DepthCrafter > Custom Resolution Presets."
    // Splatting keeps its sizes as text: compare as numbers
    readonly property int curW: st ? Number(st[widthKey]) : 0
    readonly property int curH: st ? Number(st[heightKey]) : 0
    implicitWidth: button.implicitWidth
    implicitHeight: Theme.rowHeight

    readonly property string current: {
        if (!st) return ""
        const w = curW, h = curH
        for (const cat of presets)
            for (const it of cat.items)
                if (it.w === w && it.h === h) return w + "×" + h
        return ""
    }

    AccentButton {
        id: button
        kind: "outline"
        text: root.current.length ? root.current : "Select preset…"
        onClicked: pop.opened ? pop.close() : pop.open()
    }

    Popup {
        id: pop
        y: button.height + 4
        padding: 12
        margins: 8              // kept inside the window (2026-10-11)
        transformOrigin: Popup.Top
        enter: Transition {
            ParallelAnimation {
                NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.dur(140) }
                NumberAnimation { property: "scale"; from: 0.9; to: 1; duration: Theme.dur(240); easing.type: Easing.OutCubic }
            }
        }
        exit: Transition { NumberAnimation { property: "opacity"; to: 0; duration: Theme.dur(100) } }
        background: Rectangle {
            PopupGrain { radius: 10 }   // the window's film grain
            radius: 10
            color: Theme.field
            border.width: 1
            border.color: Theme.borderStrong
            layer.enabled: true
            layer.effect: MultiEffect { shadowEnabled: true; shadowBlur: 0.9; shadowOpacity: Theme.isDark ? 0.55 : 0.2; shadowVerticalOffset: 6 }
        }
        contentItem: Column {
          spacing: 8
          Text {
            objectName: "presetEmpty"
            visible: root.presets.length === 0
            text: root.emptyText
            color: Theme.fgMuted
            font.family: Theme.bodyFont
            font.pixelSize: Theme.smallSize + 1
          }
          Text {
            objectName: "presetNote"
            visible: root.note.length > 0
            text: root.note
            color: Theme.fgMuted
            font.family: Theme.bodyFont
            font.pixelSize: Theme.smallSize + 1
          }
          Row {
            spacing: 14
            Repeater {
                model: root.presets
                Column {
                    id: catCol
                    required property var modelData
                    readonly property var labels: modelData.items.map(it => it.w + "×" + it.h + (it.ratio ? "  (" + it.ratio + ")" : ""))
                    readonly property bool scrolls: modelData.items.length > root.maxRows
                    spacing: 2
                    TextMetrics {
                        id: widest
                        font.family: Theme.bodyFont
                        font.pixelSize: Theme.bodySize
                        text: catCol.labels.reduce((a, b) => b.length > a.length ? b : a, "")
                    }
                    Text {
                        text: Theme.headingText(modelData.category)
                        color: Theme.fgMuted
                        font.family: Theme.headingFont
                        font.pixelSize: 12
                        font.weight: Font.DemiBold
                        bottomPadding: 4
                    }
                    ListView {
                        id: sizes
                        objectName: "presetSizes"
                        model: catCol.modelData.items
                        readonly property real cellWidth: Math.max(118, widest.advanceWidth + 16)
                        width: cellWidth + (catCol.scrolls ? 10 : 0)
                        height: Math.min(count, root.maxRows) * 30 - 2
                        spacing: 2
                        clip: true
                        interactive: catCol.scrolls
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: SlimScrollBar { policy: catCol.scrolls ? ScrollBar.AlwaysOn : ScrollBar.AlwaysOff }
                        // opened: the current size in view, else the top
                        Connections {
                            target: pop
                            function onAboutToShow() {
                                const i = catCol.modelData.items.findIndex(it => it.w === root.curW && it.h === root.curH)
                                if (i >= 0) sizes.positionViewAtIndex(i, ListView.Contain)
                                else sizes.positionViewAtBeginning()
                            }
                        }
                        delegate: Rectangle {
                            id: cell
                            required property var modelData
                            readonly property bool isCurrent: !!root.st && root.curW === modelData.w && root.curH === modelData.h
                            width: sizes.cellWidth; height: 28; radius: 6
                            color: isCurrent ? Theme.alpha(Theme.accent, 0.2) : (h.hovered ? Theme.hover : "transparent")
                            Behavior on color { ColorAnimation { duration: Theme.dur(120) } }
                            Text {
                                id: cellText
                                x: 8
                                anchors.verticalCenter: parent.verticalCenter
                                text: cell.modelData.w + "×" + cell.modelData.h + (cell.modelData.ratio ? "  (" + cell.modelData.ratio + ")" : "")
                                color: cell.isCurrent ? Theme.accentInk : Theme.fg
                                font.family: Theme.bodyFont
                                font.pixelSize: Theme.bodySize
                            }
                            HoverHandler { id: h; cursorShape: Qt.PointingHandCursor }
                            TapHandler { onTapped: { root.apply(cell.modelData.w, cell.modelData.h); pop.close() } }
                        }
                    }
                }
            }
          }
        }
    }
}
