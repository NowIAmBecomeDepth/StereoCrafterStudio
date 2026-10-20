import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Effects
import QtQuick.Layouts
import Studio

// Studio's folder picker (user, 2026-10-05: the "Columns" design from
// prototypes/folder_picker, in place of Windows' folder dialog). Each level
// sits side by side; the last column lists the clips in the open folder.
//
//   one click on a folder   highlight it and open it in the next column
//   double-click a folder   pick it
//   one click on a clip     highlight it (allowFiles): Select Clip picks just it
//   double-click a clip     pick it
//   right-click a column    New Folder: made there, named in place (2026-10-05)
//   right-click a row       New Folder, or Move to Recycle Bin -- asked first (2026-10-10)
//   the four shortcuts      under the drives: an empty one keeps the highlighted
//                           folder; right-click to change or remove (2026-10-10)
//   Windows Explorer...     Windows' own dialog, as a backup (2026-10-10): what it
//                           picks is picked here
//
// A drop-in for FolderDialog: title, currentFolder (a url or a path), open(),
// accepted(), selectedFolder (the pick, as a url -- a clip's when a clip was
// picked). selectedPath is the same as a path. Folders: folder_browser.py.
Popup {
    id: picker
    objectName: "folderPicker"

    property string title: "Choose a folder"
    property var currentFolder: ""
    // a clip can be picked as well as a folder (fields that take either)
    property bool allowFiles: false
    // the clip types listed (no dot); empty: video
    property var fileTypes: []
    property url selectedFolder
    property string selectedPath: ""
    signal accepted()

    // the open folder at each level; the folder whose clips the last column
    // lists; what is highlighted (that folder, or one clip in it)
    property var cols: []
    property string leaf: ""
    property string sel: ""
    readonly property bool selIsFile: sel !== "" && Folders.isFile(sel)
    // bumped when a folder is made, so the listings are read again
    property int rev: 0
    // New Folder: the folder it goes in ("" when not naming one), and why the
    // name typed was refused
    property string creatingIn: ""
    property string createError: ""
    // the columns shown: the open folder's own column only when it has
    // folders in it (2026-10-05: an empty "No folders" column sat between it
    // and its clips) -- or a new one is being named in it
    property var colsShown: []
    function shownNow() {
        const n = cols.length
        if (n && cols[n - 1] === leaf && creatingIn !== leaf && !Folders.hasDirs(leaf))
            return cols.slice(0, n - 1)
        return cols
    }
    // The view's model: one row per column, the clips last. Only the columns
    // that changed are replaced -- a plain count as the model rebuilt every
    // column on each change, so the first click of a double-click destroyed
    // the row the second landed on (2026-10-05).
    ListModel { id: colModel }
    function syncCols() {
        colsShown = shownNow()
        const want = colsShown.map(p => ({ path: p, last: false })).concat([{ path: leaf, last: true }])
        let i = 0
        while (i < colModel.count && i < want.length && colModel.get(i).path === want[i].path
               && colModel.get(i).last === want[i].last)
            i++
        if (i < colModel.count) colModel.remove(i, colModel.count - i)
        for (let k = i; k < want.length; ++k) colModel.append(want[k])
    }
    function setRoot(path) { cols = [path]; leaf = path; sel = path; syncCols() }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(1180, (parent ? parent.width : 1200) - 60)
    height: Math.min(640, (parent ? parent.height : 700) - 60)
    padding: 18
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    onVisibleChanged: Theme.modals = Math.max(0, Theme.modals + (visible ? 1 : -1))
    Overlay.modal: Rectangle { color: Theme.alpha("#000000", Theme.isDark ? 0.55 : 0.3) }

    function start() {
        let p = Folders.toPath(currentFolder)
        if (!p || !(Folders.isDir(p) || Folders.isFile(p)))
            p = Folders.isDir(Shell.projectFolder) ? Shell.projectFolder : ((Folders.drives()[0] || {}).path || "")
        const file = Folders.isFile(p)
        const folder = file ? Folders.parentOf(p) : p
        goTo(folder)
        sel = file && allowFiles ? p : folder
    }
    // show `folder` open, with the two levels above it
    function goTo(folder) {
        const steps = Folders.crumbs(folder).map(s => s.path)
        cols = steps.slice(Math.max(0, steps.length - 3))
        leaf = folder
        sel = folder
        syncCols()
    }
    function openAt(level, path) { cols = cols.slice(0, level + 1).concat([path]); leaf = path; sel = path; syncCols() }
    // New Folder in `folder`: a name box at the top of its column
    function beginNew(folder) {
        createError = ""
        if (folder === leaf && cols[cols.length - 1] !== leaf) cols = cols.concat([leaf])
        creatingIn = folder
        syncCols()
    }
    function finishNew(name) {
        const res = Folders.makeDir(creatingIn, name)
        if (res.error) { createError = res.error; return false }
        const parentFolder = creatingIn
        creatingIn = ""; createError = ""
        rev++
        colModel.clear()                     // read every listing again
        const level = cols.indexOf(parentFolder)
        if (level >= 0) openAt(level, res.path)
        else goTo(res.path)
        return true
    }
    function cancelNew() { creatingIn = ""; createError = ""; syncCols() }
    function nameOf(path) { return path ? ((Folders.crumbs(path).slice(-1)[0] || {}).name || path) : "" }
    // the folder a shortcut keeps: the one highlighted (a highlighted clip's folder)
    readonly property string folderNow: selIsFile ? Folders.parentOf(sel) : sel
    // a row's right-click just now: its column's own handler, which hears the
    // same click, stands down
    property double rowMenuAt: 0
    function rowMenu(folder, path, isDir, dirs, clips) {
        rowMenuAt = Date.now()
        itemMenu.folder = folder; itemMenu.target = path; itemMenu.isDir = isDir
        itemMenu.dirs = dirs; itemMenu.clips = clips
        itemMenu.popup()
    }
    // Move to Recycle Bin (user, 2026-10-10): asked first, Cancel the safe choice
    function askRecycle(path, isDir, dirs, clips) {
        const parts = []
        if (dirs > 0) parts.push(dirs + (dirs === 1 ? " folder" : " folders"))
        if (clips > 0) parts.push(clips + (clips === 1 ? " clip" : " clips"))
        const what = isDir
            ? "the folder \u201c" + nameOf(path) + "\u201d and everything in it" + (parts.length ? " (" + parts.join(", ") + ")" : "")
            : "\u201c" + nameOf(path) + "\u201d"
        confirmDlg.ask({
            title: "Move to Recycle Bin",
            message: "Move " + what + " to the Recycle Bin?\n\n" + path + "\n\nYou can put it back from the Recycle Bin.",
            buttons: [{ text: "Move to Recycle Bin", code: 1, kind: "danger" }, { text: "Cancel", code: 0, kind: "ghost" }],
            focusIndex: 1,
            onDone: (code) => { if (code === 1) picker.recycle(path) }
        })
    }
    function recycle(path) {
        const res = Folders.recycle(path)
        if (res.error) {
            confirmDlg.ask({ title: "Not moved", message: res.error })
            return
        }
        if (res.kept) return                 // too big for the bin, and Keep It was picked
        const gone = path.replace(/\//g, "\\").toLowerCase()
        const under = p => { const q = String(p || "").replace(/\//g, "\\").toLowerCase(); return q === gone || q.startsWith(gone + "\\") }
        rev++
        colModel.clear()                     // read every listing again
        if (cols.some(under) || under(leaf) || under(sel)) goTo(Folders.parentOf(path))
        else syncCols()
    }
    function pick(path) {
        if (!path) return
        selectedPath = path
        selectedFolder = Folders.toUrl(path)
        close()
        accepted()
    }
    onAboutToShow: { cancelNew(); start() }

    enter: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.dur(160) }
            NumberAnimation { property: "scale"; from: 0.95; to: 1; duration: Theme.dur(260); easing.type: Easing.OutCubic }
        }
    }
    exit: Transition { NumberAnimation { property: "opacity"; to: 0; duration: Theme.dur(120) } }

    background: Rectangle {
        PopupGrain { radius: 14 }
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
    }

    // ---- pieces --------------------------------------------------------------
    component Badge: Rectangle {
        property string label: ""
        visible: label !== ""
        implicitWidth: badgeText.implicitWidth + 14
        implicitHeight: 18
        radius: 9
        color: Theme.alpha(Theme.accent, 0.18)
        Text {
            id: badgeText
            anchors.centerIn: parent
            text: parent.label
            color: Theme.accentInk
            font.family: Theme.bodyFont
            font.pixelSize: Theme.smallSize
            font.weight: Font.DemiBold
        }
    }
    component Row2: Rectangle {
        id: rw
        property bool on: false
        signal tapped()
        signal doubleTapped()
        radius: 8
        color: on ? Theme.alpha(Theme.accent, 0.16) : (rwHover.hovered ? Theme.hover : "transparent")
        border.width: on ? 1 : 0
        border.color: Theme.alpha(Theme.accent, 0.5)
        HoverHandler { id: rwHover; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: rw.tapped(); onDoubleTapped: rw.doubleTapped() }
    }
    component SmallHeading: Text {
        color: Theme.fgFaint
        font.family: Theme.buttonFont
        font.pixelSize: Theme.smallSize
        font.weight: Font.Bold
        font.capitalization: Font.AllUppercase
        font.letterSpacing: 0.8
        elide: Text.ElideRight
    }
    component Chip: Rectangle {
        id: chip
        property string label: ""
        property bool on: false
        signal tapped()
        implicitWidth: chipText.implicitWidth + 20
        implicitHeight: 26
        radius: 13
        color: on ? Theme.alpha(Theme.accent, 0.2) : (chipHover.hovered ? Theme.hover : Theme.alpha(Theme.fg, 0.05))
        HoverHandler { id: chipHover; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: chip.tapped() }
        Text {
            id: chipText
            anchors.centerIn: parent
            text: chip.label
            color: chip.on ? Theme.accentInk : Theme.fg
            font.family: Theme.bodyFont
            font.pixelSize: Theme.smallSize + 1
        }
    }

    contentItem: ColumnLayout {
        spacing: 10
        Keys.onReturnPressed: picker.pick(picker.sel)
        Keys.onEnterPressed: picker.pick(picker.sel)

        // title, and the places to start from
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Text {
                text: picker.title
                color: Theme.fg
                font.family: Theme.buttonFont
                font.pixelSize: Theme.headingSize
                font.weight: Font.Bold
                font.capitalization: Font.AllUppercase
                font.letterSpacing: 0.8
                elide: Text.ElideRight
                Layout.fillWidth: true
            }
            Chip {
                objectName: "pickerProjectChip"
                visible: Folders.isDir(Shell.projectFolder)
                label: "Project"
                onTapped: picker.goTo(Shell.projectFolder)
                HoverTip { tip: Shell.projectFolder }
            }
            Repeater {
                model: Folders.drives()
                Chip {
                    required property var modelData
                    label: modelData.name
                    on: (picker.cols[0] || "").toUpperCase().startsWith(modelData.path.toUpperCase())
                    onTapped: picker.setRoot(modelData.path)
                }
            }
        }
        // four folder shortcuts, under the drives (user, 2026-10-10): an empty one
        // keeps the highlighted folder; right-click to change or remove one
        RowLayout {
            objectName: "pickerShortcuts"
            Layout.fillWidth: true
            spacing: 8
            Item { Layout.fillWidth: true }
            SmallHeading { text: "Shortcuts" }
            Repeater {
                model: Shell.pickerShortcuts
                Chip {
                    id: sc
                    required property int index
                    required property var modelData
                    objectName: "pickerShortcut"
                    readonly property string path: String(modelData || "")
                    readonly property bool there: (picker.opened, picker.rev, path !== "" && Folders.isDir(path))
                    readonly property string name: picker.nameOf(path)
                    label: path === "" ? "+ Shortcut" : (name.length > 20 ? name.slice(0, 19) + "\u2026" : name)
                    on: there && picker.leaf === path
                    opacity: path !== "" && !there ? 0.5 : 1
                    onTapped: {
                        if (path === "") { if (picker.folderNow !== "") Shell.setPickerShortcut(index, picker.folderNow) }
                        else if (there) picker.goTo(path)
                    }
                    TapHandler {
                        acceptedButtons: Qt.RightButton
                        onTapped: { scMenu.index = sc.index; scMenu.popup() }
                    }
                    HoverTip {
                        tip: sc.path === ""
                             ? "An empty shortcut. Click it to keep the highlighted folder here"
                               + (picker.folderNow !== "" ? " (" + picker.folderNow + ")" : "") + "."
                             : sc.path + (sc.there ? "\nClick to go there. Right-click to change or remove it."
                                                   : "\nThis folder isn't there any more. Right-click to change or remove the shortcut.")
                    }
                }
            }
        }
        // the path, every step clickable
        Flow {
            objectName: "pickerCrumbs"
            Layout.fillWidth: true
            spacing: 2
            Repeater {
                model: Folders.crumbs(picker.sel)
                Row {
                    required property var modelData
                    required property int index
                    readonly property bool last: index === Folders.crumbs(picker.sel).length - 1
                    spacing: 2
                    Icon { visible: index > 0; glyph: Theme.icon.right; size: 9; color: Theme.fgFaint; anchors.verticalCenter: parent.verticalCenter }
                    Rectangle {
                        implicitWidth: crText.implicitWidth + 14
                        implicitHeight: 24
                        radius: 6
                        color: crHover.hovered ? Theme.hover : "transparent"
                        HoverHandler { id: crHover; cursorShape: Qt.PointingHandCursor }
                        TapHandler {
                            onTapped: Folders.isFile(modelData.path) ? (picker.sel = modelData.path) : picker.goTo(modelData.path)
                        }
                        Text {
                            id: crText
                            anchors.centerIn: parent
                            text: modelData.name
                            color: parent.parent.last ? Theme.fg : Theme.fgMuted
                            font.family: Theme.bodyFont
                            font.pixelSize: Theme.bodySize
                            font.weight: parent.parent.last ? Font.DemiBold : Font.Normal
                        }
                    }
                }
            }
        }
        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }

        // the columns
        ListView {
            id: colView
            objectName: "pickerColumns"
            Layout.fillWidth: true
            Layout.fillHeight: true
            // A horizontal list asks for its whole content width; in the layout that made every
            // row of the picker as wide as all its columns, past the popup's edge on a narrow
            // window (user, 2026-10-13: the drives, shortcuts and Select Clip stuck out)
            implicitWidth: 0
            // the columns share the view: as many whole ones as fit at MIN_COL or wider (at
            // most MAX_COL), so scrolled to the end it shows whole columns -- a fixed 260 left
            // part of one hanging at the left edge (user, 2026-10-13)
            readonly property real minCol: 220
            readonly property real maxCol: 340
            readonly property real colW: {
                const fit = Math.max(1, Math.floor(width / minCol))
                return Math.min(maxCol, width / Math.max(1, Math.min(count, fit)))
            }
            onWidthChanged: Qt.callLater(() => colView.positionViewAtEnd())
            orientation: ListView.Horizontal
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            model: colModel
            onCountChanged: Qt.callLater(() => colView.positionViewAtEnd())
            ScrollBar.horizontal: SlimScrollBar {}
            delegate: Item {
                id: col
                objectName: "pickerColumn"
                required property int index
                required property string path
                required property bool last
                readonly property var subdirs: (picker.rev, last ? [] : Folders.dirs(path))
                readonly property var clipNames: (picker.rev, last ? Folders.files(path, picker.fileTypes) : [])
                width: colView.colW
                height: colView.height
                Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: Theme.border }
                // a handler, not a MouseArea over the rows: that took their double-clicks
                TapHandler {
                    objectName: "pickerColumnMenuArea"
                    acceptedButtons: Qt.RightButton
                    onTapped: {
                        if (Date.now() - picker.rowMenuAt < 400) return     // a row's menu is open
                        newMenu.folder = col.path; newMenu.popup()
                    }
                }
                ColumnLayout {
                    anchors.fill: parent
                    anchors.rightMargin: 9
                    anchors.leftMargin: col.index === 0 ? 0 : 8
                    spacing: 4
                    SmallHeading {
                        text: col.last ? "Clips inside" : ((Folders.crumbs(col.path).slice(-1)[0] || {}).name || "")
                        Layout.fillWidth: true
                        Layout.topMargin: 4
                        Layout.bottomMargin: 4
                    }
                    // folders
                    ListView {
                        id: dirList
                        visible: !col.last
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        spacing: 2
                        model: col.subdirs
                        ScrollBar.vertical: SlimScrollBar {}
                        header: Column {
                            width: dirList.width - 8
                            visible: picker.creatingIn === col.path
                            height: visible ? implicitHeight + 6 : 0
                            spacing: 4
                            Rectangle {
                                width: parent.width
                                height: 36
                                radius: 8
                                color: Theme.alpha(Theme.accent, 0.12)
                                border.width: 1
                                border.color: picker.createError ? Theme.danger : Theme.accent
                                RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: 10
                                    anchors.rightMargin: 6
                                    spacing: 8
                                    Icon { glyph: Theme.icon.folder; size: 14; color: Theme.accentInk }
                                    TextField {
                                        id: nameBox
                                        objectName: "pickerNewName"
                                        Layout.fillWidth: true
                                        text: "New folder"
                                        color: Theme.fg
                                        selectionColor: Theme.alpha(Theme.accent, 0.45)
                                        selectedTextColor: Theme.fg
                                        font.family: Theme.bodyFont
                                        font.pixelSize: Theme.bodySize
                                        selectByMouse: true
                                        background: null
                                        padding: 2
                                        // ready to type, whether it was made visible or made already showing
                                        function prime() { text = "New folder"; selectAll(); forceActiveFocus() }
                                        onVisibleChanged: if (visible) Qt.callLater(prime)
                                        Component.onCompleted: if (visible) Qt.callLater(prime)
                                        onTextEdited: picker.createError = ""
                                        Keys.onReturnPressed: (e) => { picker.finishNew(text); e.accepted = true }
                                        Keys.onEnterPressed: (e) => { picker.finishNew(text); e.accepted = true }
                                        Keys.onEscapePressed: (e) => { picker.cancelNew(); e.accepted = true }
                                    }
                                    IconButton {
                                        size: 26
                                        iconGlyph: Theme.icon.check
                                        onClicked: picker.finishNew(nameBox.text)
                                        HoverTip { tip: "Make the folder (Enter)" }
                                    }
                                    IconButton {
                                        size: 26
                                        iconGlyph: Theme.icon.close
                                        onClicked: picker.cancelNew()
                                        HoverTip { tip: "Cancel (Esc)" }
                                    }
                                }
                            }
                            Text {
                                visible: picker.createError !== ""
                                width: parent.width
                                text: picker.createError
                                color: Theme.danger
                                font.family: Theme.bodyFont
                                font.pixelSize: Theme.smallSize
                                wrapMode: Text.WordWrap
                            }
                        }
                        delegate: Row2 {
                            id: dirRow
                            required property var modelData
                            objectName: "pickerFolder"
                            property string path: modelData.path
                            width: dirList.width - 8
                            height: 36
                            on: picker.cols[col.index + 1] === modelData.path || picker.leaf === modelData.path
                            onTapped: picker.openAt(col.index, modelData.path)
                            onDoubleTapped: picker.pick(modelData.path)
                            TapHandler {
                                acceptedButtons: Qt.RightButton
                                onTapped: picker.rowMenu(col.path, dirRow.modelData.path, true,
                                                         dirRow.modelData.dirs, dirRow.modelData.clips)
                            }
                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 10
                                anchors.rightMargin: 8
                                spacing: 8
                                Icon { glyph: Theme.icon.folder; size: 14; color: dirRow.modelData.clips > 0 ? Theme.accentInk : Theme.fgMuted }
                                Text {
                                    text: dirRow.modelData.name
                                    color: Theme.fg
                                    font.family: Theme.bodyFont
                                    font.pixelSize: Theme.bodySize
                                    font.weight: dirRow.on ? Font.DemiBold : Font.Normal
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }
                                Badge { label: dirRow.modelData.clips > 0 ? dirRow.modelData.clips + (dirRow.modelData.clips === 1 ? " clip" : " clips") : "" }
                                Text {
                                    visible: dirRow.modelData.clips === 0
                                    text: dirRow.modelData.dirs > 0 ? dirRow.modelData.dirs + (dirRow.modelData.dirs === 1 ? " folder" : " folders") : "empty"
                                    color: Theme.fgFaint
                                    font.family: Theme.bodyFont
                                    font.pixelSize: Theme.smallSize
                                }
                                Icon { glyph: Theme.icon.right; size: 9; color: Theme.fgFaint; visible: dirRow.modelData.dirs > 0 }
                            }
                        }
                        Text {
                            anchors.centerIn: parent
                            visible: dirList.count === 0 && picker.creatingIn !== col.path
                            text: "No folders"
                            color: Theme.fgFaint
                            font.family: Theme.bodyFont
                            font.pixelSize: Theme.bodySize
                        }
                    }
                    // the open folder's clips
                    ListView {
                        id: clipList
                        visible: col.last
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        clip: true
                        spacing: 2
                        model: col.clipNames
                        ScrollBar.vertical: SlimScrollBar {}
                        delegate: Row2 {
                            id: clipRow
                            required property string modelData
                            objectName: "pickerClip"
                            readonly property string path: Folders.join(col.path, modelData)
                            width: clipList.width - 8
                            height: 30
                            on: picker.sel === path
                            opacity: picker.allowFiles ? 1 : 0.6
                            onTapped: if (picker.allowFiles) picker.sel = clipRow.path
                            onDoubleTapped: if (picker.allowFiles) picker.pick(clipRow.path)
                            TapHandler {
                                acceptedButtons: Qt.RightButton
                                onTapped: picker.rowMenu(col.path, clipRow.path, false, 0, 0)
                            }
                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 8
                                anchors.rightMargin: 6
                                spacing: 8
                                Icon { glyph: Theme.icon.video; size: 12; color: clipRow.on ? Theme.accentInk : Theme.fgMuted }
                                Text {
                                    text: clipRow.modelData
                                    color: clipRow.on ? Theme.fg : Theme.fgMuted
                                    font.family: Theme.bodyFont
                                    font.pixelSize: Theme.smallSize + 1
                                    font.weight: clipRow.on ? Font.DemiBold : Font.Normal
                                    elide: Text.ElideMiddle
                                    Layout.fillWidth: true
                                }
                            }
                        }
                        Text {
                            anchors.centerIn: parent
                            visible: clipList.count === 0
                            text: "No clips in this folder"
                            color: Theme.fgFaint
                            font.family: Theme.bodyFont
                            font.pixelSize: Theme.bodySize
                        }
                    }
                }
            }
        }
        StudioMenu {
            id: newMenu
            objectName: "pickerNewMenu"
            property string folder: ""
            MenuItem {
                text: "New Folder"
                onTriggered: picker.beginNew(newMenu.folder)
            }
        }
        // a folder's or clip's own menu
        StudioMenu {
            id: itemMenu
            objectName: "pickerItemMenu"
            property string folder: ""          // the column it's in: New Folder goes there
            property string target: ""
            property bool isDir: false
            property int dirs: 0
            property int clips: 0
            MenuItem {
                text: "New Folder"
                onTriggered: picker.beginNew(itemMenu.folder)
            }
            MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: Theme.border } }
            MenuItem {
                objectName: "pickerRecycle"
                text: "Move to Recycle Bin\u2026"
                onTriggered: picker.askRecycle(itemMenu.target, itemMenu.isDir, itemMenu.dirs, itemMenu.clips)
            }
        }
        StudioMenu {
            id: scMenu
            objectName: "pickerShortcutMenu"
            property int index: 0
            MenuItem {
                objectName: "pickerShortcutSet"
                text: picker.folderNow !== "" ? "Keep \u201c" + picker.nameOf(picker.folderNow) + "\u201d here" : "Keep the highlighted folder here"
                enabled: picker.folderNow !== ""
                onTriggered: Shell.setPickerShortcut(scMenu.index, picker.folderNow)
            }
            MenuItem {
                objectName: "pickerShortcutClear"
                text: "Remove this shortcut"
                enabled: (Shell.pickerShortcuts[scMenu.index] || "") !== ""
                onTriggered: Shell.setPickerShortcut(scMenu.index, "")
            }
        }
        PromptDialog { id: confirmDlg; objectName: "pickerConfirm" }
        // Windows' own dialogs, as a backup (user, 2026-10-10): opened on the
        // highlighted folder; what they pick is picked here
        FolderDialog {
            id: winFolderDlg
            objectName: "pickerWindowsFolder"
            title: picker.title
            currentFolder: Folders.toUrl(picker.folderNow)
            onAccepted: picker.pick(Folders.toPath(selectedFolder))
        }
        FileDialog {
            id: winFileDlg
            objectName: "pickerWindowsFile"
            title: picker.title
            currentFolder: Folders.toUrl(picker.folderNow)
            nameFilters: ["Clips (" + (picker.fileTypes.length ? picker.fileTypes : Folders.videoTypes())
                                      .map(t => "*." + t).join(" ") + ")", "All files (*)"]
            onAccepted: picker.pick(Folders.toPath(selectedFile))
        }
        StudioMenu {
            id: winMenu
            objectName: "pickerWindowsMenu"
            MenuItem { text: "Pick a folder\u2026"; onTriggered: winFolderDlg.open() }
            MenuItem { text: "Pick a clip\u2026"; onTriggered: winFileDlg.open() }
        }
        Text {
            text: (picker.allowFiles
                  ? "Click a folder to open it, double-click to pick it. Click a clip to pick just that one; double-click picks it straight away."
                  : "Click a folder to open it, double-click (or Select Folder) to pick it.") + " Right-click a column for New Folder, a folder or clip to move it to the Recycle Bin."
            color: Theme.fgFaint
            font.family: Theme.bodyFont
            font.pixelSize: Theme.smallSize
            // wraps: on one line it held every row of the picker to its length, past the popup's
            // edge on a narrow window (user, 2026-10-13)
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
        // what will be picked, and the buttons
        RowLayout {
            Layout.fillWidth: true
            spacing: 10
            Icon { glyph: picker.selIsFile ? Theme.icon.video : Theme.icon.folder; size: 13; color: Theme.accentInk }
            Text {
                objectName: "pickerChosen"
                text: picker.sel || "Pick a folder"
                color: picker.sel ? Theme.fg : Theme.fgFaint
                font.family: Theme.bodyFont
                font.pixelSize: Theme.bodySize
                elide: Text.ElideMiddle
                Layout.fillWidth: true
                Layout.minimumWidth: 60          // a long path elides; it never pushes the buttons out
            }
            AccentButton {
                id: winBtn
                objectName: "pickerWindows"
                text: "Windows Explorer\u2026"
                kind: "outline"
                // a field that takes a clip too: which one Windows' dialog picks
                onClicked: picker.allowFiles ? winMenu.popup(winBtn, 0, -winMenu.implicitHeight - 4) : winFolderDlg.open()
                HoverTip { tip: "Pick with Windows' own dialog instead, if this picker won't do." }
            }
            AccentButton { objectName: "pickerCancel"; text: "Cancel"; kind: "ghost"; onClicked: picker.close() }
            AccentButton {
                objectName: "pickerSelect"
                text: picker.selIsFile ? "Select Clip" : "Select Folder"
                kind: "filled"
                enabled: picker.sel !== ""
                onClicked: picker.pick(picker.sel)
            }
        }
    }
}
