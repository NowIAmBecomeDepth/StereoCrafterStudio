import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Effects
import QtQuick.Layouts
import QtQuick.Window
import Studio

// The caption's project button opens this (user, 2026-10-01; styled as the
// theme picker's popup): the projects in Settings' Projects Folder as tiles --
// a poster lying in the project folder, or a folder icon -- on a Movies or TV
// Shows tab, the current one outlined; a click switches the project.
// Open Other Project… picks any folder; with no Projects Folder set it offers to choose one.
// Main only -- the console's caption keeps the name as plain text.
Popup {
    id: popup
    objectName: "projectPicker"
    property Item anchorItem: null           // the caption's centre button
    signal browse()                          // Main: its project folder dialog
    signal chooseRoot()                      // Main: the Projects Folder dialog

    property var projects: []
    // Movies / TV Shows (user, 2026-10-02): a show has an "episode" folder inside
    property int kind: 0
    readonly property var shown: projects.filter(p => !!p.show === (kind === 1))
    // placed when it opens: a binding on mapToItem would not follow the
    // caption's layout (it read the bar before it was laid out)
    onAboutToShow: {
        projects = Shell.listProjects()
        // open on the tab holding the current project
        const cur = Shell.projectFolder.replace(/\\/g, "/").replace(/\/$/, "").toLowerCase()
        const hit = projects.find(p => p.path.toLowerCase() === cur)
        kind = hit && hit.show ? 1 : 0
        if (anchorItem && ov) {
            const at = anchorItem.mapToItem(ov, anchorItem.width / 2, anchorItem.height)
            x = Math.max(8, Math.min(ov.width - width - 8, at.x - width / 2))
            y = at.y + 6
        }
    }

    // a video's cover art, found after the list (off the UI thread; bug sweep A2)
    Connections {
        target: Shell
        function onPosterFound(path, url) {
            if (projects.some(p => p.path === path))
                projects = projects.map(p => p.path === path ? Object.assign({}, p, { poster: url }) : p)
        }
    }

    parent: Overlay.overlay
    readonly property Item ov: Overlay.overlay
    width: 460
    height: ov ? Math.min(implicitHeight, ov.height - y - 24) : implicitHeight
    padding: 14
    modal: true
    focus: true
    onVisibleChanged: Theme.modals = Math.max(0, Theme.modals + (visible ? 1 : -1))
    Overlay.modal: Item {}
    transformOrigin: Popup.Top
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

    enter: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.dur(160) }
            NumberAnimation { property: "scale"; from: 0.85; to: 1; duration: Theme.dur(320); easing.type: Easing.OutBack }
        }
    }
    exit: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; to: 0; duration: Theme.dur(140) }
            NumberAnimation { property: "scale"; to: 0.92; duration: Theme.dur(140) }
        }
    }

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

    component Heading: Text {
        color: Theme.fgMuted
        font.family: Theme.tabFont
        font.pixelSize: Theme.smallSize
        font.letterSpacing: 1.2
    }

    contentItem: ColumnLayout {
        spacing: 8

        RowLayout {
            Layout.fillWidth: true
            Heading { text: "PROJECTS" }
            Item { Layout.fillWidth: true }
            StageTabBar {
                objectName: "projectKindTabs"
                implicitHeight: 30
                numbered: false
                bed: false
                model: ["Movies", "TV Shows"]
                currentIndex: popup.kind
                onActivated: (i) => popup.kind = i
            }
        }
        // the folder's path is not shown here (user, 2026-10-01): Change Projects
        // Folder… opens on it, and Settings shows it

        // no Projects Folder yet
        Text {
            Layout.fillWidth: true
            visible: Shell.projectsRoot === "" && Shell.showsRoot === ""
            text: "Choose the folder that holds your projects -- each folder in it a movie or a show -- "
                  + "and they'll be listed here."
            wrapMode: Text.WordWrap
            color: Theme.fg
            font.family: Theme.bodyFont
            font.pixelSize: Theme.bodySize
        }
        Text {
            Layout.fillWidth: true
            visible: (Shell.projectsRoot !== "" || Shell.showsRoot !== "") && popup.shown.length === 0
            text: popup.projects.length === 0 ? "No project folders in there yet."
                  : popup.kind === 1 ? "No TV shows found (a show has an Episode folder inside, or set a TV Shows Folder in Settings)."
                  : "No movies in there."
            color: Theme.fgMuted
            font.family: Theme.bodyFont
            font.pixelSize: Theme.bodySize
        }

        // a tile per project, as the theme picker's swatches (user, 2026-10-01):
        // its poster, or a folder icon; the current one outlined
        Flickable {
            id: list
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(tiles.implicitHeight, 3 * 146 + 8)
            visible: popup.shown.length > 0
            contentHeight: tiles.implicitHeight
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: SlimScrollBar {}
            Grid {
                id: tiles
                width: list.width
                columns: 4
                spacing: 4
                Repeater {
                    model: popup.shown
                    Rectangle {
                        id: tile
                        required property var modelData
                        required property int index
                        objectName: "projectRow"
                        readonly property bool current: modelData.path.toLowerCase() === Shell.projectFolder.replace(/\\/g, "/").replace(/\/$/, "").toLowerCase()
                        width: (tiles.width - 3 * tiles.spacing) / 4
                        height: 142
                        radius: 10
                        color: tileHover.hovered ? Theme.hover : "transparent"
                        border.width: current ? 1.5 : 0
                        border.color: Theme.accent
                        // the theme picker's cascade (instant while a job runs)
                        SequentialAnimation {
                            id: cascade
                            PauseAnimation { duration: Theme.dur(16 * tile.index) }
                            ParallelAnimation {
                                NumberAnimation { target: tile; property: "opacity"; to: 1; duration: Theme.dur(180) }
                                NumberAnimation { target: tile; property: "scale"; to: 1; duration: Theme.dur(340); easing.type: Easing.OutBack }
                            }
                        }
                        Component.onCompleted: {
                            if (Theme.calm) return
                            opacity = 0; scale = 0.6; cascade.start()
                        }
                        // the poster, 2:3, or a folder in its place
                        Rectangle {
                            id: frame
                            anchors.horizontalCenter: parent.horizontalCenter
                            y: 9
                            width: 66; height: 99
                            radius: 6
                            color: Theme.alpha(Theme.fg, 0.05)
                            border.width: 1
                            border.color: Theme.border
                            clip: true
                            scale: tileHover.hovered ? 1.06 : 1
                            Behavior on scale { NumberAnimation { duration: Theme.dur(180); easing.type: Easing.OutBack } }
                            Image {
                                id: poster
                                objectName: "projectPoster"
                                anchors.fill: parent
                                anchors.margins: 1
                                visible: tile.modelData.poster !== "" && status === Image.Ready
                                source: tile.modelData.poster
                                sourceSize: Qt.size(132, 198)      // 2x the tile, no more held in memory
                                fillMode: Image.PreserveAspectCrop
                                asynchronous: true
                                smooth: true
                            }
                            Icon {
                                anchors.centerIn: parent
                                visible: !poster.visible
                                glyph: Theme.icon.folder
                                size: 28
                                color: tile.current ? Theme.accentInk : Theme.fgMuted
                            }
                        }
                        Text {
                            anchors.horizontalCenter: parent.horizontalCenter
                            y: frame.y + frame.height + 8
                            width: parent.width - 8
                            horizontalAlignment: Text.AlignHCenter
                            elide: Text.ElideRight
                            text: tile.modelData.name
                            color: tile.current ? Theme.fg : Theme.fgMuted
                            font.family: Theme.bodyFont
                            font.pixelSize: Theme.smallSize + 1
                        }
                        HoverHandler { id: tileHover; cursorShape: Qt.PointingHandCursor }
                        TapHandler {
                            onTapped: {
                                if (!tile.current) {
                                    Shell.projectFolder = tile.modelData.path
                                    Theme.toast("success", "Project set", tile.modelData.name)
                                }
                                popup.close()
                            }
                        }
                        HoverTip { tip: tile.modelData.path }
                    }
                }
            }
        }

        Rectangle { Layout.fillWidth: true; Layout.topMargin: 2; implicitHeight: 1; color: Theme.border }
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            AccentButton {
                objectName: "projectBrowse"
                text: "Open Other Project…"
                kind: "ghost"
                onClicked: { popup.close(); popup.browse() }
            }
            Item { Layout.fillWidth: true }
            AccentButton {
                objectName: "projectChooseRoot"
                text: Shell.projectsRoot === "" ? "Choose Projects Folder…" : "Change Projects Folder…"
                kind: Shell.projectsRoot === "" ? "filled" : "ghost"
                onClicked: { popup.close(); popup.chooseRoot() }
            }
        }
    }
}
