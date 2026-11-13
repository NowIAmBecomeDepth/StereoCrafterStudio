import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Effects
import QtQuick.Layouts
import QtQuick.Shapes
import QtQuick.Window
import Studio

// The caption's project button opens this (user, 2026-10-01; styled as the
// theme picker's popup): the projects in Settings' Projects Folder as tiles --
// a poster lying in the project folder, or a folder icon -- on a Movies or TV
// Shows tab, the current one outlined; a click switches the project.
// The Add Project tile after the last poster adds any folder for good (Shell.addProject) and opens
// it; a poster's "−" takes it off the list (nothing on disk touched); hovering a poster shows that
// project's folders beside the list (Main: ProjectFoldersPanel's hover mode) (2026-11-12).
// With no Projects Folder set it offers to choose one.
// Main only -- the console's caption keeps the name as plain text.
Popup {
    id: popup
    objectName: "projectPicker"
    property Item anchorItem: null           // the caption's centre button
    signal addProject()                      // Main: the folder picker, then Shell.addProject + the switch
    signal chooseRoot()                      // Main: the Projects Folder dialog
    signal switchTo(string path)             // Main: the switch, and its one toast (2026-11-12)
    signal folders()                         // Main: the Project Folders panel
    signal hoverProject(string path, Item tile)   // Main: the hover panel for this project
    signal hoverEnded(Item tile)
    // Main: a popup of its own over the list (the Add folder picker, the hover panel): a
    // click there is not a click outside
    property bool holdOpen: false
    // Main: the project the hover panel shows ("" when none)
    property string panelFor: ""

    property var projects: []
    // Movies / TV Shows (user, 2026-10-02): a show has an "episode" folder inside
    property int kind: 0
    readonly property var shown: projects.filter(p => !!p.show === (kind === 1))
    // the tiles cascade in as the list opens, not each time it is read again
    property bool cascade: false
    function key(p) { return String(p || "").replace(/\\/g, "/").replace(/\/$/, "").toLowerCase() }
    function refresh() { projects = Shell.listProjects() }
    // a project just added: its tab, its tile (Main, after Shell.addProject)
    function showProject(path, show) { kind = show ? 1 : 0; refresh() }
    function removeTile(tile) {
        const p = tile.modelData
        popup.hoverEnded(tile)
        if (!Shell.removeProject(p.path)) return
        Theme.toast("success", "Removed from the list", p.name)
    }
    function tileHovered(tile, on) {
        if (on && !tile.modelData.missing) popup.hoverProject(tile.modelData.path, tile)
        else popup.hoverEnded(tile)
    }
    // placed when it opens: a binding on mapToItem would not follow the
    // caption's layout (it read the bar before it was laid out)
    onAboutToShow: {
        const list = Shell.listProjects()
        // open on the tab holding the current project
        const cur = key(Shell.projectFolder)
        const hit = list.find(p => p.path.toLowerCase() === cur)
        cascade = !Theme.calm
        kind = hit && hit.show ? 1 : 0
        projects = list
        cascade = false
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
        // a project added or taken off the list
        function onProjectListChanged() { if (popup.visible) popup.refresh() }
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
    closePolicy: holdOpen ? Popup.CloseOnEscape : Popup.CloseOnEscape | Popup.CloseOnPressOutside

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
                onActivated: (i) => { popup.cascade = !Theme.calm; popup.kind = i; popup.cascade = false }
            }
        }
        // the folder's path is not shown here (user, 2026-10-01): Change Projects
        // Folder… opens on it, and Settings shows it

        // no Projects Folder yet
        Text {
            Layout.fillWidth: true
            visible: Shell.projectsRoot === "" && Shell.showsRoot === ""
            text: "Choose the folder that holds your projects -- each folder in it a movie or a show -- "
                  + "and they'll be listed here. Or add a single project with the Add Project tile."
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
        // its poster, or a folder icon; the current one outlined. The Add Project
        // tile after them, always (an empty tab: the tile alone)
        Flickable {
            id: list
            objectName: "projectTiles"
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(tiles.implicitHeight, 3 * 146 + 8)
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
                        // an added folder not there now (a drive unplugged): dimmed, not opened
                        readonly property bool missing: !!modelData.missing
                        readonly property bool hovered: tileHover.hovered
                        // the hover panel shows this project's folders
                        readonly property bool panelShown: popup.panelFor !== "" && popup.key(popup.panelFor) === popup.key(modelData.path)
                        // the "−" shows on hover, and with the keyboard on the tile
                        readonly property bool showRemove: tileHover.hovered || activeFocus
                        width: (tiles.width - 3 * tiles.spacing) / 4
                        height: 142
                        radius: 10
                        color: tileHover.hovered ? Theme.hover : "transparent"
                        border.width: activeFocus ? 2 : current ? 1.5 : 0
                        border.color: Theme.accent
                        activeFocusOnTab: true
                        function activate() {
                            if (tile.missing) return
                            popup.close()
                            if (!tile.current)
                                popup.switchTo(tile.modelData.path)
                        }
                        Keys.onPressed: (e) => {
                            if (e.key === Qt.Key_Return || e.key === Qt.Key_Enter || e.key === Qt.Key_Space) {
                                tile.activate(); e.accepted = true
                            } else if (e.key === Qt.Key_Delete) {
                                popup.removeTile(tile); e.accepted = true
                            }
                        }
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
                            if (Theme.calm || !popup.cascade) return
                            opacity = 0; scale = 0.6; cascade.start()
                        }
                        // the poster, 2:3, or a folder in its place
                        Rectangle {
                            id: frame
                            anchors.horizontalCenter: parent.horizontalCenter
                            y: 9
                            width: 66; height: 99
                            radius: 6
                            opacity: tile.missing ? 0.4 : 1
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
                            id: caption
                            anchors.horizontalCenter: parent.horizontalCenter
                            y: frame.y + frame.height + 8
                            width: parent.width - 8
                            horizontalAlignment: Text.AlignHCenter
                            elide: Text.ElideRight
                            text: tile.modelData.name
                            color: tile.current ? Theme.fg : Theme.fgMuted
                            opacity: tile.missing ? 0.5 : 1
                            font.family: Theme.bodyFont
                            font.pixelSize: Theme.smallSize + 1
                        }
                        HoverHandler {
                            id: tileHover
                            cursorShape: tile.missing ? Qt.ArrowCursor : Qt.PointingHandCursor
                            onHoveredChanged: popup.tileHovered(tile, hovered)
                        }
                        TapHandler {
                            // the "−" is a click of its own, not "open this project"
                            onTapped: (point) => {
                                const q = removeBtn.mapFromItem(tile, point.position.x, point.position.y)
                                if (removeBtn.visible && removeBtn.contains(q)) return
                                tile.activate()
                            }
                        }
                        HoverTip {
                            hotItems: [frame, caption]
                            // none while its Project Folders show (they name the folders)
                            tip: tile.missing ? "Folder not found. Is its drive plugged in?\n\n" + tile.modelData.path
                                 : tile.panelShown ? "" : tile.modelData.path
                        }
                        // take it off the list: the folder stays as it is
                        Rectangle {
                            id: removeBtn
                            objectName: "projectRemove"
                            x: tile.width - width - 3
                            y: 3
                            width: 22; height: 22
                            radius: 11
                            visible: opacity > 0
                            opacity: tile.showRemove ? 1 : 0
                            Behavior on opacity { NumberAnimation { duration: Theme.dur(120) } }
                            color: removeHover.hovered ? Theme.danger : Theme.field
                            border.width: 1
                            border.color: removeHover.hovered ? Theme.danger : Theme.borderStrong
                            Icon {
                                anchors.centerIn: parent
                                glyph: Theme.icon.remove
                                size: 12
                                color: removeHover.hovered ? "#ffffff" : Theme.fg
                            }
                            HoverHandler { id: removeHover; cursorShape: Qt.PointingHandCursor }
                            TapHandler { onTapped: popup.removeTile(tile) }
                            HoverTip { tip: "Remove from this list. The folder and its files are not touched." }
                        }
                    }
                }
                // after the last poster: add a project folder from anywhere, and open it
                Rectangle {
                    id: addTile
                    objectName: "projectAddTile"
                    width: (tiles.width - 3 * tiles.spacing) / 4
                    height: 142
                    radius: 10
                    color: addHover.hovered ? Theme.hover : "transparent"
                    border.width: activeFocus ? 2 : 0
                    border.color: Theme.accent
                    activeFocusOnTab: true
                    Keys.onPressed: (e) => {
                        if (e.key === Qt.Key_Return || e.key === Qt.Key_Enter || e.key === Qt.Key_Space) {
                            popup.addProject(); e.accepted = true
                        }
                    }
                    Shape {
                        id: addFrame
                        anchors.horizontalCenter: parent.horizontalCenter
                        y: 9
                        width: 66; height: 99
                        scale: addHover.hovered ? 1.06 : 1
                        Behavior on scale { NumberAnimation { duration: Theme.dur(180); easing.type: Easing.OutBack } }
                        preferredRendererType: Shape.CurveRenderer
                        ShapePath {
                            strokeColor: addHover.hovered ? Theme.accent : Theme.borderStrong
                            strokeWidth: 1.5
                            strokeStyle: ShapePath.DashLine
                            dashPattern: [4, 3]
                            fillColor: Theme.alpha(Theme.fg, addHover.hovered ? 0.06 : 0.03)
                            PathRectangle { x: 0.75; y: 0.75; width: 64.5; height: 97.5; radius: 6 }
                        }
                        // a folder with a "+" on it
                        Icon {
                            id: addFolder
                            anchors.centerIn: parent
                            glyph: Theme.icon.folder
                            size: 28
                            color: addHover.hovered ? Theme.accent : Theme.fgMuted
                        }
                        Rectangle {
                            x: addFolder.x + addFolder.width / 2 + 4
                            y: addFolder.y + addFolder.height / 2 + 1
                            width: 16; height: 16; radius: 8
                            color: Theme.accent
                            border.width: 1.5
                            border.color: Theme.field
                            // the "+" drawn: crisp at this size on any palette
                            Rectangle { anchors.centerIn: parent; width: 8; height: 2; color: Theme.field }
                            Rectangle { anchors.centerIn: parent; width: 2; height: 8; color: Theme.field }
                        }
                    }
                    Text {
                        anchors.horizontalCenter: parent.horizontalCenter
                        y: addFrame.y + addFrame.height + 8
                        width: parent.width - 8
                        horizontalAlignment: Text.AlignHCenter
                        elide: Text.ElideRight
                        text: "Add Project"
                        color: addHover.hovered ? Theme.fg : Theme.fgMuted
                        font.family: Theme.bodyFont
                        font.pixelSize: Theme.smallSize + 1
                    }
                    HoverHandler { id: addHover; cursorShape: Qt.PointingHandCursor }
                    TapHandler { onTapped: popup.addProject() }
                    HoverTip { tip: "Add a project folder from anywhere and open it. It stays on this list." }
                }
            }
        }

        Rectangle { Layout.fillWidth: true; Layout.topMargin: 2; implicitHeight: 1; color: Theme.border }
        // the open project's own folders (2026-11-12), on a line of their own
        RowLayout {
            Layout.fillWidth: true
            visible: Shell.projectFolder !== ""
            spacing: 8
            Text {
                Layout.fillWidth: true
                leftPadding: 4
                text: Shell.projectName
                elide: Text.ElideRight
                color: Theme.fgMuted
                font.family: Theme.bodyFont
                font.pixelSize: Theme.bodySize
            }
            AccentButton {
                objectName: "projectFoldersButton"
                text: "Project Folders…"
                kind: "ghost"
                iconGlyph: Theme.icon.folder
                onClicked: { popup.close(); popup.folders() }
                HoverTip { tip: "The open project's stage folders: where each stage's clips are, and which are missing." }
            }
        }
        // (Open Other Project… was here: the Add Project tile does it now, 2026-11-12)
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
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
