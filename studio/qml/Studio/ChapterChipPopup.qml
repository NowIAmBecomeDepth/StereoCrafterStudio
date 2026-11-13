import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Effects
import QtQuick.Layouts
import Studio

// The title bar's chapter chip opens this (2026-11-12): the open project's chapters
// (Shell.listChapters -- "Whole project" for a flat one), the one every stage is on
// marked; a click puts every stage on it (Scene Cut keeps its whole movie). Each
// stage's own picker still overrides. The project's folders are a click away.
Popup {
    id: popup
    objectName: "chapterChipPopup"
    property Item anchorItem: null           // the caption's chip
    signal folders()                         // Main: the Project Folders panel

    property var entries: []
    onAboutToShow: {
        entries = Shell.listChapters()
        if (anchorItem && ov) {
            const at = anchorItem.mapToItem(ov, anchorItem.width / 2, anchorItem.height)
            x = Math.max(8, Math.min(ov.width - width - 8, at.x - width / 2))
            y = at.y + 6
        }
    }

    parent: Overlay.overlay
    readonly property Item ov: Overlay.overlay
    width: 320
    height: ov ? Math.min(implicitHeight, ov.height - y - 24) : implicitHeight
    padding: 12
    modal: true
    focus: true
    onVisibleChanged: Theme.modals = Math.max(0, Theme.modals + (visible ? 1 : -1))
    Overlay.modal: Item {}
    transformOrigin: Popup.Top
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

    enter: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.dur(160) }
            NumberAnimation { property: "scale"; from: 0.9; to: 1; duration: Theme.dur(260); easing.type: Easing.OutCubic }
        }
    }
    exit: Transition { NumberAnimation { property: "opacity"; to: 0; duration: Theme.dur(120) } }

    background: Rectangle {
        id: card
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
        PopupGrain { radius: card.radius }   // the window's film grain
    }

    contentItem: ColumnLayout {
        spacing: 6

        Text {
            text: "CHAPTER FOR EVERY STAGE"
            color: Theme.fgMuted
            font.family: Theme.tabFont
            font.pixelSize: Theme.smallSize
            font.letterSpacing: 1.2
        }
        Text {
            visible: popup.entries.length === 0
            text: "No chapters in this project yet."
            color: Theme.fgMuted
            font.family: Theme.bodyFont
            font.pixelSize: Theme.bodySize
        }
        ListView {
            id: list
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(contentHeight, 10 * 30)
            visible: popup.entries.length > 0
            clip: true
            interactive: contentHeight > height
            boundsBehavior: Flickable.StopAtBounds
            model: popup.entries
            ScrollBar.vertical: SlimScrollBar {}
            delegate: Rectangle {
                id: row
                objectName: "chapterChipRow"
                required property var modelData
                readonly property bool current: modelData.name === Shell.commonChapter
                width: ListView.view.width
                height: 30
                radius: 6
                color: rowHover.hovered ? Theme.alpha(Theme.accent, 0.16) : "transparent"
                Behavior on color { ColorAnimation { duration: Theme.dur(100) } }
                Icon {
                    x: 8
                    anchors.verticalCenter: parent.verticalCenter
                    glyph: Theme.icon.check
                    size: 12
                    color: Theme.accentInk
                    visible: row.current
                }
                Text {
                    x: 28
                    width: parent.width - x - 8
                    anchors.verticalCenter: parent.verticalCenter
                    text: row.modelData.name
                    elide: Text.ElideRight
                    color: row.current ? Theme.accentInk : Theme.fg
                    font.family: Theme.bodyFont
                    font.pixelSize: Theme.bodySize
                    font.weight: row.current ? Font.DemiBold : Font.Normal
                }
                HoverHandler { id: rowHover; cursorShape: Qt.PointingHandCursor }
                TapHandler {
                    onTapped: {
                        Shell.pickChapterForAll(row.modelData.num)
                        Theme.toast("success", "All stages on " + row.modelData.name,
                                    "Each stage's own chapter picker can still change just that stage.")
                        popup.close()
                    }
                }
            }
        }

        Rectangle { Layout.fillWidth: true; Layout.topMargin: 2; implicitHeight: 1; color: Theme.border }
        AccentButton {
            objectName: "chipProjectFolders"
            text: "Project Folders…"
            kind: "ghost"
            iconGlyph: Theme.icon.folder
            onClicked: { popup.close(); popup.folders() }
            HoverTip { tip: "The project's stage folders: where each stage's clips are, and which are missing." }
        }
    }
}
