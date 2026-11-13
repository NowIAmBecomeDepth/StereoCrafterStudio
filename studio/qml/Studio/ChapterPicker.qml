import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import Studio

// v1's chapter dropdown: point the stage at one chapter of the project and
// every path fills from the project's stage folders. "Manual paths" leaves
// the fields alone; editing a path by hand switches back to it.
ComboBox {
    id: box
    property var dc
    signal picked(int num)                 // a real pick, not a refresh
    // Scene Cut's extra entry (v1 _CHAPTER_WHOLE): the whole movie, its
    // scenes filed into chapter folders. The controller knows it as -2.
    property bool wholeMovie: false
    implicitWidth: 260
    // In a section header it gives way first when the window is narrow.
    Layout.fillWidth: true
    Layout.minimumWidth: 150
    Layout.maximumWidth: 260
    implicitHeight: Theme.rowHeight
    font.family: Theme.bodyFont
    font.pixelSize: Theme.bodySize
    hoverEnabled: true

    readonly property var entries: {
        const out = [{ num: -1, label: "Manual paths" }]
        if (wholeMovie) out.push({ num: -2, label: "Whole Movie (all chapters)" })
        for (const c of (dc ? dc.chapters : []))
            out.push({ num: c.num, label: c.name })
        return out
    }
    model: entries.map(e => e.label)
    currentIndex: {
        for (let i = 0; i < entries.length; ++i)
            if (entries[i].num === (dc ? dc.chapterNum : -1)) return i
        return 0
    }
    onActivated: (i) => { dc.pickChapter(entries[i].num); picked(entries[i].num) }
    // On the project with no chapter and no folders yet (project_memory, 2026-11-12):
    // it says so, in amber, until a chapter is picked or a folder set
    // (read on chapterChanged, not bound: reading it can settle the flag, which says
    // chapterChanged again -- a binding loop)
    property bool needs: false
    function lookNeeds() { needs = !!dc && !!dc.needsChapter }
    onDcChanged: lookNeeds()
    Component.onCompleted: lookNeeds()
    Connections {
        target: box.dc
        ignoreUnknownSignals: true
        function onChapterChanged() { Qt.callLater(box.lookNeeds) }
    }
    displayText: needs ? "Pick a chapter" : currentText

    // Studio's own tooltip (user, 2026-10-02: Qt's attached one was black, and could run off the window); not while the list is open
    HoverTip {
        tip: box.popup.visible ? "" : (box.needs ? "This stage has no folders in this project yet. Pick a chapter here, "
                                                   + "or set its folders by hand.\n\n" : "")
             + "Point this stage at one chapter of the movie. Studio finds the chapter's folder in each "
             + "stage folder of the project and fills Input and Output from them.\n\n"
             + "\"Whole project\" is a project whose stage folders hold the clips directly, with no chapter folders.\n\n"
             + "\"Manual paths\" leaves the fields as you set them. Editing a path by hand switches back to it."
    }

    contentItem: Row {
        leftPadding: 10
        spacing: 8
        Icon { glyph: box.needs ? Theme.icon.warning : Theme.icon.project; size: 13
               color: box.needs ? Theme.warning : Theme.accentInk; anchors.verticalCenter: parent.verticalCenter }
        Text {
            objectName: "chapterPickerText"
            anchors.verticalCenter: parent.verticalCenter
            width: box.width - 64
            text: box.displayText
            color: box.needs ? Theme.warning : Theme.fg
            font: box.font
            elide: Text.ElideRight
        }
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
        radius: 16
        readonly property color tone: box.needs ? Theme.warning : Theme.accent
        color: box.hovered ? Theme.alpha(tone, 0.16) : Theme.alpha(tone, 0.1)
        border.width: 1
        border.color: Theme.alpha(tone, box.popup.visible ? 0.8 : box.needs ? 0.6 : 0.4)
        Behavior on color { ColorAnimation { duration: Theme.dur(140) } }
    }
    delegate: ItemDelegate {
        id: item
        required property int index
        required property var modelData
        width: ListView.view ? ListView.view.width : box.width
        height: 30
        highlighted: box.highlightedIndex === index
        contentItem: Text {
            text: item.modelData
            color: item.index === box.currentIndex ? Theme.accentInk : Theme.fg
            font.family: Theme.bodyFont
            font.pixelSize: Theme.bodySize
            font.italic: item.index === 0
            elide: Text.ElideRight
            verticalAlignment: Text.AlignVCenter
        }
        background: null
    }
    popup: Popup {
        y: box.height + 4
        width: Math.max(box.width, 300)
        implicitHeight: Math.min(list.contentHeight + 8, 420)
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
            radius: 9
            color: Theme.field
            border.width: 1
            border.color: Theme.borderStrong
            PopupGrain { radius: 9 }   // the window's film grain
        }
    }
}
