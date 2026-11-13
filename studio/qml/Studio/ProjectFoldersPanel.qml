import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Effects
import QtQuick.Layouts
import Studio

// Project Folders (2026-11-12): the open project's five stage folders as Studio
// found them by name (Shell.projectFolders) -- for a show, one episode's, picked at
// the top. Change… points a stage folder anywhere (kept in the project, every stage
// follows), Reset finds it by name again, Create Missing Folders makes the ones not
// there yet. Opened from the project list's footer and the chapter chip (pinned: the
// open project's, until closed); by itself once after a switch to a project with no
// stage folders yet (`fresh`).
// Hover mode (2026-11-12): hovering a poster in the project list shows THAT project's
// folders beside the list -- after 350 ms, so a sweep across the posters flashes
// nothing; another poster retargets it at once; it closes 1 s after the pointer has
// left both the poster and the panel (never while its folder picker or dropdown is
// open), and with the list.
Popup {
    id: popup
    objectName: "projectFoldersPanel"
    property Item anchorItem: null           // the caption's centre button
    // opened by a switch to a project with no stage folders yet: says so
    property bool fresh: false

    // the project shown: "" = the open one (pinned mode); a path in hover mode
    property string target: ""
    readonly property string project: target !== "" ? target : Shell.projectFolder
    readonly property string projectName: project.replace(/\\/g, "/").replace(/\/+$/, "").split("/").pop()
    readonly property string title: "Project Folders · " + projectName
    // hover mode: opened by a poster in the project list (`beside`), closes by itself
    property bool hoverMode: false
    property var beside: null                // the project list (a Popup in the overlay)
    property Item hoverTile: null            // the poster it is for (not covered)

    property var units: []
    property string unit: ""
    property var rows: []
    readonly property bool missing: rows.some(r => !r.exists)
    // the row whose Change… is picking
    property string pendingRole: ""
    // one of its own popups open: it stays
    readonly property bool busy: picker.opened || (unitBox.popup && unitBox.popup.opened)

    function key(p) { return String(p || "").replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase() }
    function refresh() {
        units = Shell.projectUnitsFor(target)
        if (!units.some(u => u.unit === unit))
            unit = units.length ? units[0].unit : ""
        rows = Shell.projectFoldersFor(target, unit)
    }
    // pinned: the open project's, until closed (the footer, the chip, an empty project)
    function openFresh(on) {
        openTimer.stop(); closeTimer.stop()
        fresh = on; target = ""; hoverMode = false; hoverTile = null
        if (opened) { refresh(); place(); forceActiveFocus() }   // was hovering: pinned now
        else open()
    }
    // the project list: a poster hovered / left
    property string pendingPath: ""
    property Item pendingTile: null
    function hoverEnter(path, tile, list) {
        closeTimer.stop()
        if (opened && !hoverMode) return     // pinned: stays on what it shows
        beside = list
        if (opened) {                        // on another poster: that one, at once
            hoverTile = tile
            if (key(target) !== key(path)) { target = path; unit = ""; refresh() }
            place()
            return
        }
        pendingPath = path; pendingTile = tile
        openTimer.restart()
    }
    function hoverLeave(tile) {
        if (tile && pendingTile === tile) openTimer.stop()
        if (opened && hoverMode && (!tile || tile === hoverTile || !hoverTile)) closeTimer.restart()
    }
    function count(r) {
        if (!r.exists) return "not made yet"
        if (r.clips <= 0) return "empty"
        return (r.clips >= 999 ? "999+" : r.clips) + (r.clips === 1 ? " clip" : " clips")
    }
    // hover mode: beside the list (right, else left, else below it, else below / above the
    // poster) and never over the poster; else under the caption
    readonly property int fullWidth: 600
    readonly property int narrowest: 500     // a path still reads beside the buttons
    function place() {
        if (!ov) return
        if (hoverMode && beside) {
            const lx = beside.x, ly = beside.y, lw = beside.width, lh = beside.height
            const right = ov.width - (lx + lw) - 16, left = lx - 16
            if (right >= narrowest || left >= narrowest) {
                // the right if it has the whole width, else the side with more room
                const onRight = right >= fullWidth || right >= left
                width = Math.min(fullWidth, onRight ? right : left)
                x = onRight ? lx + lw + 8 : lx - 8 - width
                y = Math.max(8, Math.min(ly, ov.height - implicitHeight - 24))
                return
            }
            width = Math.min(fullWidth, ov.width - 16)
            x = Math.max(8, Math.min(ov.width - width - 8, lx + lw / 2 - width / 2))
            if (ly + lh + 8 + implicitHeight <= ov.height - 24) { y = ly + lh + 8; return }
            if (hoverTile) {
                const t = hoverTile.mapToItem(ov, 0, 0)
                const below = t.y + hoverTile.height + 6
                if (below + Math.min(implicitHeight, 240) <= ov.height - 24) { y = below; return }
                y = Math.max(8, t.y - 6 - implicitHeight)
                return
            }
            y = ly + lh + 8
            return
        }
        width = fullWidth
        if (anchorItem) {
            const at = anchorItem.mapToItem(ov, anchorItem.width / 2, anchorItem.height)
            x = Math.max(8, Math.min(ov.width - width - 8, at.x - width / 2))
            y = at.y + 6
        }
    }

    Timer {
        id: openTimer
        objectName: "projectFoldersOpenDelay"
        interval: 350                        // a sweep across the posters opens nothing
        onTriggered: {
            if (!popup.pendingTile || !popup.pendingTile.hovered) return
            popup.hoverMode = true; popup.fresh = false
            popup.target = popup.pendingPath; popup.unit = ""
            popup.hoverTile = popup.pendingTile
            popup.open()
        }
    }
    Timer {
        id: closeTimer
        objectName: "projectFoldersCloseDelay"
        interval: 1000                       // the user's second, a real-time wait (not an animation)
        onTriggered: {
            if (!popup.hoverMode || popup.busy) return
            if (panelHover.hovered || (popup.hoverTile && popup.hoverTile.hovered)) return
            popup.close()
        }
    }
    // its own popup closed with the pointer elsewhere: the second starts then
    onBusyChanged: if (!busy && hoverMode && opened && !panelHover.hovered) closeTimer.restart()
    onHoverTileChanged: if (!hoverTile && hoverMode && opened && !panelHover.hovered) closeTimer.restart()

    onAboutToShow: { refresh(); place() }
    onImplicitHeightChanged: if (visible && hoverMode) place()
    onClosed: { fresh = false; hoverMode = false; target = ""; hoverTile = null; closeTimer.stop() }
    onUnitChanged: if (visible) rows = Shell.projectFoldersFor(target, unit)
    Connections {
        target: Shell
        function onProjectFoldersChanged() { if (popup.visible) popup.refresh() }
        function onProjectChanged() { if (popup.visible) popup.refresh() }
        function onProjectFoldersEdited(path) {
            if (popup.visible && popup.key(path) === popup.key(popup.project)) popup.refresh()
        }
    }

    parent: Overlay.overlay
    readonly property Item ov: Overlay.overlay
    width: 600
    height: ov ? Math.min(implicitHeight, ov.height - y - 24) : implicitHeight
    padding: 14
    // hover mode: beside the project list, which keeps the keyboard and its clicks
    modal: !hoverMode
    focus: !hoverMode
    onVisibleChanged: Theme.modals = Math.max(0, Theme.modals + (visible ? 1 : -1))
    Overlay.modal: Item {}
    transformOrigin: hoverMode ? Popup.Center : Popup.Top
    // a Change… picker open over it: a click there is not a click outside; hover mode
    // closes by itself
    closePolicy: picker.opened || hoverMode ? Popup.NoAutoClose : Popup.CloseOnEscape | Popup.CloseOnPressOutside

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
        // the pointer over the panel (padding too: on the popup's own item) keeps it open
        HoverHandler {
            id: panelHover
            parent: card.parent
            onHoveredChanged: {
                if (hovered) closeTimer.stop()
                else if (popup.hoverMode && popup.opened) closeTimer.restart()
            }
        }
    }

    FolderPicker {
        id: picker
        objectName: "projectFolderPicker"
        title: "Stage folder"
        onAccepted: {
            if (popup.pendingRole !== "")
                Shell.setUnitFolderFor(popup.target, popup.unit, popup.pendingRole, selectedPath)
            popup.pendingRole = ""
        }
    }

    component Small: Text {
        color: Theme.fgMuted
        font.family: Theme.bodyFont
        font.pixelSize: Theme.bodySize
    }

    contentItem: ColumnLayout {
        spacing: 8

        RowLayout {
            Layout.fillWidth: true
            spacing: 10
            Text {
                text: "PROJECT FOLDERS"
                color: Theme.fgMuted
                font.family: Theme.tabFont
                font.pixelSize: Theme.smallSize
                font.letterSpacing: 1.2
            }
            // which project: the open one, or the poster hovered
            Text {
                objectName: "projectFoldersName"
                Layout.fillWidth: true
                text: "·  " + popup.projectName
                elide: Text.ElideRight
                color: Theme.fg
                font.family: Theme.bodyFont
                font.pixelSize: Theme.bodySize
                font.weight: Font.DemiBold
            }
            // a show: one episode's folders at a time
            Dropdown {
                id: unitBox
                objectName: "projectFoldersUnit"
                visible: popup.units.length > 1
                boxWidth: 200
                model: popup.units.map(u => u.label)
                currentIndex: popup.units.findIndex(u => u.unit === popup.unit)
                onActivated: (i) => popup.unit = popup.units[i].unit
                HoverTip { tip: "This show's episodes each have their own stage folders: pick the episode to look at." }
            }
        }

        // a project just switched to, with nothing in it yet
        Rectangle {
            Layout.fillWidth: true
            visible: popup.fresh && Shell.projectEmpty
            implicitHeight: freshNote.implicitHeight + 16
            radius: 8
            color: Theme.alpha(Theme.warning, 0.12)
            border.width: 1
            border.color: Theme.alpha(Theme.warning, 0.5)
            Text {
                id: freshNote
                objectName: "projectFoldersFresh"
                x: 10; y: 8
                width: parent.width - 20
                text: "This folder has no stage folders yet. Create Missing Folders makes them, or point "
                      + "each one at a folder you already have."
                wrapMode: Text.WordWrap
                color: Theme.fg
                font.family: Theme.bodyFont
                font.pixelSize: Theme.bodySize
            }
        }
        Small {
            Layout.fillWidth: true
            text: "Studio found these folders by name. Change any that is wrong and every stage follows."
            wrapMode: Text.WordWrap
        }

        Repeater {
            model: popup.rows
            RowLayout {
                id: row
                objectName: "projectFolderRow"
                required property var modelData
                Layout.fillWidth: true
                spacing: 8
                Text {
                    Layout.preferredWidth: 92
                    text: row.modelData.label
                    color: Theme.fg
                    font.family: Theme.bodyFont
                    font.pixelSize: Theme.bodySize
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                }
                Text {
                    id: pathText
                    objectName: "projectFolderPath"
                    Layout.fillWidth: true
                    text: row.modelData.path
                    elide: Text.ElideMiddle
                    color: row.modelData.exists ? Theme.fg : Theme.fgMuted
                    font.family: Theme.bodyFont
                    font.pixelSize: Theme.bodySize
                    font.italic: row.modelData.overridden
                    HoverTip {
                        tip: row.modelData.path + (row.modelData.overridden ? "\n\nChosen for this project, not found by name." : "")
                    }
                }
                Text {
                    objectName: "projectFolderCount"
                    Layout.preferredWidth: 86
                    horizontalAlignment: Text.AlignRight
                    text: popup.count(row.modelData)
                    color: row.modelData.exists ? Theme.fgMuted : Theme.warning
                    font.family: Theme.bodyFont
                    font.pixelSize: Theme.smallSize + 1
                }
                AccentButton {
                    objectName: "projectFolderChange"
                    text: "Change…"
                    kind: "outline"
                    implicitWidth: 92
                    onClicked: {
                        popup.pendingRole = row.modelData.role
                        picker.currentFolder = row.modelData.exists ? row.modelData.path : popup.project
                        picker.open()
                    }
                    HoverTip { tip: "Pick the folder this project's " + row.modelData.label + " are in. Every stage follows." }
                }
                // the folder found by name again; holds its place when hidden, so the rows line up
                Item {
                    implicitWidth: 76
                    implicitHeight: 30
                    AccentButton {
                        objectName: "projectFolderReset"
                        anchors.fill: parent
                        visible: row.modelData.overridden
                        text: "Reset"
                        kind: "ghost"
                        onClicked: Shell.clearUnitFolderFor(popup.target, popup.unit, row.modelData.role)
                        HoverTip { tip: "Go back to the folder Studio finds by its name." }
                    }
                }
            }
        }

        Rectangle { Layout.fillWidth: true; Layout.topMargin: 2; implicitHeight: 1; color: Theme.border }
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            AccentButton {
                objectName: "createProjectFolders"
                visible: popup.missing
                text: "Create Missing Folders"
                kind: popup.fresh ? "filled" : "tonal"
                iconGlyph: Theme.icon.add
                onClicked: {
                    if (Shell.createProjectFoldersFor(popup.target, popup.unit))
                        Theme.toast("success", "Folders made", "Every stage folder of " + popup.projectName + " is there now.")
                    else
                        Theme.toast("error", "Folders not made", "Studio couldn't make the stage folders here.")
                }
                HoverTip { tip: "Make the stage folders that aren't there yet, with the names shown." }
            }
            Item { Layout.fillWidth: true }
            AccentButton {
                objectName: "projectFoldersClose"
                text: "Close"
                kind: "ghost"
                onClicked: popup.close()
                HoverTip { tip: "Close this panel (Esc)." }
            }
        }
    }
}
