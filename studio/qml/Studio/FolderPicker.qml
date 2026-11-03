import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Effects
import QtQuick.Layouts
import Studio

// Studio's folder picker, laid out as Windows Explorer (user, 2026-10-31: "Explorer+Preview" from
// prototypes/explorer_lab, in place of the Columns picker of 2026-10-05).
//
//   back / forward / up       Alt+Left (Backspace) / Alt+Right / Alt+Up; F5 reads the folder again
//   the path                  every step clickable; click its empty part (or Ctrl+L) to type or paste one
//   search                    this folder and the ones under it (Ctrl+F / F3)
//   navigation pane           the project, the four shortcuts (an empty one keeps the highlighted
//                             folder; right-click to change or remove one), the drives as a tree
//                             opened out to the folder you're in
//   Details / Tiles / Large   the view, and the preview pane, kept as they were left (Shell)
//   one click                 highlights a folder, or a clip (allowFiles)
//   double-click              a folder: go in; a clip: pick it
//   Select                    the highlighted folder or clip, else the folder you're in
//   right-click               New Folder (named in place), a folder or clip: Move to Recycle Bin, asked first
//   a drop from Explorer      a folder: go there; a clip: its folder, the clip highlighted
//   Windows Explorer...       Windows' own dialog, as a backup: what it picks is picked here
//
// A drop-in for FolderDialog: title, currentFolder (a url or a path), open(), accepted(),
// selectedFolder (the pick, as a url -- a clip's when a clip was picked). selectedPath is the same
// as a path. Folders: folder_browser.py.
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

    // ---- where it is ---------------------------------------------------------------------------
    property string path: ""            // the folder shown
    property var back: []
    property var fwd: []
    property string sel: ""             // what is highlighted ("" for nothing)
    property string sortKey: "name"
    property bool asc: true
    property string search: ""
    // bumped when the disk changed under it (a folder made, something recycled, F5)
    property int rev: 0
    // New Folder: the folder it goes in ("" when not naming one), and why the name was refused
    property string creatingIn: ""
    property string createError: ""

    readonly property var items: (rev, opened && path !== "" ? Folders.entries(path, fileTypes, search, sortKey, asc) : [])
    readonly property var selItem: { for (const it of items) if (it.path === sel) return it; return null }
    readonly property bool selIsFile: sel !== "" && Folders.isFile(sel)
    // what Select picks: the highlighted folder or clip, else the folder shown
    readonly property string choice: sel !== "" && (allowFiles || !selIsFile) ? sel : path
    // the folder a shortcut keeps: the one highlighted, else the one shown
    readonly property string folderNow: sel !== "" && !selIsFile ? sel : path
    // compatibility with the Columns picker's name for the open folder
    readonly property string leaf: path

    function nameOf(p) { return p ? ((Folders.crumbs(p).slice(-1)[0] || {}).name || p) : "" }
    function go(p) {
        if (!p || p === path) return
        if (path !== "") { back = back.concat([path]); fwd = [] }
        path = p; sel = ""; search = ""; cancelNew()
    }
    function goTo(p) { go(p) }           // (the Columns picker's name)
    function goBack() { if (!back.length) return; fwd = [path].concat(fwd); path = back[back.length - 1]; back = back.slice(0, -1); sel = ""; search = ""; cancelNew() }
    function goFwd() { if (!fwd.length) return; back = back.concat([path]); path = fwd[0]; fwd = fwd.slice(1); sel = ""; search = ""; cancelNew() }
    function goUp() { const p = Folders.parentOf(path); if (p) { const was = path; go(p); sel = was } }
    function sortBy(k) { if (sortKey === k) asc = !asc; else { sortKey = k; asc = true } }
    // a row's click / double-click / Enter
    function highlight(it) { if (it.isDir || allowFiles) sel = it.path; if (body.item) body.item.keySink.forceActiveFocus() }
    function openItem(it) { if (it.isDir) go(it.path); else if (allowFiles) pick(it.path) }
    // a path arriving (the address bar, a drop): a folder opens, a clip opens its folder highlighted
    function arrive(p) {
        if (!p) return false
        if (Folders.isDir(p)) { go(p); return true }
        if (Folders.isFile(p)) { go(Folders.parentOf(p)); sel = p; return true }
        return false
    }

    function start() {
        let p = Folders.toPath(currentFolder)
        if (!p || !(Folders.isDir(p) || Folders.isFile(p)))
            p = Folders.isDir(Shell.projectFolder) ? Shell.projectFolder : ((Folders.drives()[0] || {}).path || "")
        back = []; fwd = []; path = ""; search = ""; cancelNew()
        if (Folders.isFile(p)) { path = Folders.parentOf(p); sel = allowFiles ? p : "" }
        else { path = p; sel = "" }
        rev++
    }
    function pick(p) {
        if (!p) return
        selectedPath = p
        selectedFolder = Folders.toUrl(p)
        close()
        accepted()
    }

    // New Folder: a name box over the list, in the folder shown
    function beginNew() { createError = ""; search = ""; creatingIn = path }
    function finishNew(name) {
        const res = Folders.makeDir(creatingIn, name)
        if (res.error) { createError = res.error; return false }
        creatingIn = ""; createError = ""
        rev++
        sel = res.path
        return true
    }
    function cancelNew() { creatingIn = ""; createError = "" }

    // Move to Recycle Bin (user, 2026-10-10): asked first, Cancel the safe choice
    function askRecycle(p, isDir, dirs, clips) {
        const parts = []
        if (dirs > 0) parts.push(dirs + (dirs === 1 ? " folder" : " folders"))
        if (clips > 0) parts.push(clips + (clips === 1 ? " clip" : " clips"))
        const what = isDir
            ? "the folder “" + nameOf(p) + "” and everything in it" + (parts.length ? " (" + parts.join(", ") + ")" : "")
            : "“" + nameOf(p) + "”"
        confirmDlg.ask({
            title: "Move to Recycle Bin",
            message: "Move " + what + " to the Recycle Bin?\n\n" + p + "\n\nYou can put it back from the Recycle Bin.",
            buttons: [{ text: "Move to Recycle Bin", code: 1, kind: "danger" }, { text: "Cancel", code: 0, kind: "ghost" }],
            focusIndex: 1,
            onDone: (code) => { if (code === 1) picker.recycle(p) }
        })
    }
    function recycle(p) {
        const res = Folders.recycle(p)
        if (res.error) {
            confirmDlg.ask({ title: "Not moved", message: res.error })
            return
        }
        if (res.kept) return                 // too big for the bin, and Keep It was picked
        const gone = p.replace(/\//g, "\\").toLowerCase()
        const under = q => { const s = String(q || "").replace(/\//g, "\\").toLowerCase(); return s === gone || s.startsWith(gone + "\\") }
        if (under(sel)) sel = ""
        back = back.filter(q => !under(q)); fwd = fwd.filter(q => !under(q))
        if (under(path)) { path = Folders.parentOf(p); search = "" }
        rev++
    }
    // a row's right-click: its own menu (the list's own handler, which hears the same click, stands down)
    property double rowMenuAt: 0
    function rowMenu(it) {
        rowMenuAt = Date.now()
        itemMenu.target = it.path; itemMenu.isDir = it.isDir
        itemMenu.dirs = it.dirs || 0; itemMenu.clips = it.clips || 0
        itemMenu.popup()
    }
    function areaMenu() { if (Date.now() - rowMenuAt > 400) newMenu.popup() }

    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(1240, (parent ? parent.width : 1300) - 60)
    height: Math.min(760, (parent ? parent.height : 820) - 60)
    padding: 16
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    onVisibleChanged: Theme.modals = Math.max(0, Theme.modals + (visible ? 1 : -1))
    Overlay.modal: Rectangle { color: Theme.alpha("#000000", Theme.isDark ? 0.55 : 0.3) }

    onAboutToShow: start()
    // Settings > Studio > Pick Folders with Windows Explorer (user, 2026-10-31; off by default):
    // straight to Windows' own dialog, on the folder this picker would open at -- it never shows.
    // A field that takes a clip too asks which, at the pointer.
    function open() {
        if (!Shell.explorerPickers) { visible = true; return }
        start()
        if (allowFiles) explorerMenu.popup()
        else winFolderDlg.open()
    }

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

    // Explorer's keys (only while it's open; a text box keeps the keys it types with)
    Shortcut { enabled: picker.opened; sequences: ["Alt+Left", "Backspace"]; onActivated: picker.goBack() }
    Shortcut { enabled: picker.opened; sequence: "Alt+Right"; onActivated: picker.goFwd() }
    Shortcut { enabled: picker.opened; sequence: "Alt+Up"; onActivated: picker.goUp() }
    Shortcut { enabled: picker.opened; sequence: "F5"; onActivated: picker.rev++ }
    Shortcut { enabled: picker.opened; sequences: ["Ctrl+L", "Alt+D", "F4"]; onActivated: if (body.item) body.item.address.edit() }
    Shortcut { enabled: picker.opened; sequences: ["Ctrl+F", "Ctrl+E", "F3"]; onActivated: if (body.item) body.item.searchBox.focusIt() }
    Shortcut { enabled: picker.opened && picker.creatingIn === ""; sequence: "Ctrl+Shift+N"; onActivated: picker.beginNew() }
    Shortcut {
        enabled: picker.opened && picker.selItem !== null && !(body.item && body.item.address.editing)
        sequence: "Delete"
        onActivated: picker.askRecycle(picker.selItem.path, picker.selItem.isDir, picker.selItem.dirs, picker.selItem.clips)
    }

    // ---- pieces --------------------------------------------------------------------------------
    component Glyph: Icon { color: Theme.fgMuted; size: 14 }
    component SmallHeading: Text {
        color: Theme.fgFaint
        font.family: Theme.buttonFont
        font.pixelSize: Theme.smallSize
        font.weight: Font.Bold
        font.capitalization: Font.AllUppercase
        font.letterSpacing: 0.8
        elide: Text.ElideRight
    }
    component NavRow: Rectangle {
        id: nr
        property string label: ""
        property string glyph: Theme.icon.folder
        property bool on: false
        property bool faded: false
        property int indent: 4
        property bool expandable: false
        property bool expanded: false
        signal tapped()
        signal rightTapped()
        signal toggled()
        height: 28
        radius: 6
        color: on ? Theme.alpha(Theme.accent, 0.16) : (nrHover.hovered ? Theme.hover : "transparent")
        opacity: faded ? 0.5 : 1
        HoverHandler { id: nrHover; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: nr.tapped(); onDoubleTapped: if (nr.expandable) nr.toggled() }
        TapHandler { acceptedButtons: Qt.RightButton; onTapped: nr.rightTapped() }
        Item {
            x: nr.indent; width: 16; height: parent.height
            visible: nr.expandable
            Glyph { anchors.centerIn: parent; glyph: nr.expanded ? Theme.icon.down : Theme.icon.right; size: 9; color: Theme.fgFaint }
            TapHandler { onTapped: nr.toggled() }
        }
        Glyph { id: ng; x: nr.indent + 18; anchors.verticalCenter: parent.verticalCenter; glyph: nr.glyph; size: 13; color: nr.on ? Theme.accentInk : Theme.fgMuted }
        Text {
            anchors.left: ng.right; anchors.leftMargin: 8; anchors.right: parent.right; anchors.rightMargin: 6
            anchors.verticalCenter: parent.verticalCenter
            text: nr.label
            elide: Text.ElideRight
            color: nr.on ? Theme.accentInk : Theme.fg
            font.family: Theme.bodyFont
            font.pixelSize: Theme.bodySize
        }
    }
    component Cell: Text {
        height: 30
        leftPadding: 8
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
        color: Theme.fgMuted
        font.family: Theme.bodyFont
        font.pixelSize: Theme.smallSize + 1
    }
    // what a row shows of a folder: its clips, else its folders, else empty
    function folderLine(it) {
        const parts = []
        if (it.clips) parts.push(it.clips + (it.clips === 1 ? " clip" : " clips"))
        if (it.dirs) parts.push(it.dirs + (it.dirs === 1 ? " folder" : " folders"))
        return parts.length ? parts.join(", ") : "Empty folder"
    }

    // The window is built only while the picker shows (user, 2026-11-01: the Explorer picker made a theme
    // change lag -- each of Studio's 28 pickers, hidden, kept its whole window, every part of which
    // took the new colours and followed the window's size). What must work with it hidden -- the
    // menus, the question, Windows' own dialogs (Pick Folders with Windows Explorer) -- is built beside it.
    contentItem: Loader {
        id: body
        objectName: "pickerBody"
        active: picker.visible
        focus: true
        sourceComponent: bodyComponent
    }
    Component {
        id: bodyComponent
        Item {
            // what the picker reaches in it from outside
            property alias keySink: keySink
            property alias address: address
            property alias searchBox: searchBox
            // the keys' home once the list is clicked (Delete, Enter): a text box keeps them otherwise
            Item {
                id: keySink
                objectName: "pickerKeys"
                focus: true
                Keys.onReturnPressed: if (picker.selItem) picker.openItem(picker.selItem)
                Keys.onEnterPressed: if (picker.selItem) picker.openItem(picker.selItem)
            }
            // a folder or clip dragged in from Windows Explorer (the window's own Drop Zones get no
            // drags while this is open): a folder opens, a clip opens its folder highlighted
            DropArea {
                objectName: "pickerDrop"
                anchors.fill: parent
                keys: ["text/uri-list"]
                onEntered: (d) => { d.accepted = d.hasUrls && d.urls.length > 0 && Shell.dropInfo(d.urls[0]).path !== "" }
                onDropped: (d) => { if (d.hasUrls && d.urls.length && picker.arrive(Shell.dropInfo(d.urls[0]).path)) d.acceptProposedAction() }
                Rectangle {
                    anchors.fill: parent
                    radius: 10
                    visible: parent.containsDrag
                    color: Theme.alpha(Theme.accent, 0.08)
                    border.width: 2
                    border.color: Theme.alpha(Theme.accent, 0.6)
                    z: 10
                }
            }

            ColumnLayout {
                anchors.fill: parent
                spacing: 10

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

                // back / forward / up / refresh, the path, search
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    Row {
                        spacing: 2
                        IconButton { objectName: "pickerBack"; size: 32; iconGlyph: Theme.icon.left; enabled: picker.back.length > 0; opacity: enabled ? 1 : 0.35; onClicked: picker.goBack(); HoverTip { tip: "Back (Alt+Left)" } }
                        IconButton { objectName: "pickerForward"; size: 32; iconGlyph: Theme.icon.right; enabled: picker.fwd.length > 0; opacity: enabled ? 1 : 0.35; onClicked: picker.goFwd(); HoverTip { tip: "Forward (Alt+Right)" } }
                        IconButton { objectName: "pickerUp"; size: 32; iconGlyph: Theme.icon.up; enabled: Folders.parentOf(picker.path) !== ""; opacity: enabled ? 1 : 0.35; onClicked: picker.goUp(); HoverTip { tip: "Up a folder (Alt+Up)" } }
                        IconButton { size: 32; iconGlyph: Theme.icon.refresh; onClicked: picker.rev++; HoverTip { tip: "Refresh (F5)" } }
                    }
                    // The path: each step a button, as Explorer's; a click on the empty part (or Ctrl+L)
                    // turns it into a box to type or paste a path into -- Enter goes there, Esc puts it back
                    Rectangle {
                        id: address
                        objectName: "pickerCrumbs"
                        property bool editing: false
                        function edit() { editing = true; typed.text = picker.path; typed.forceActiveFocus(); typed.selectAll() }
                        Layout.fillWidth: true
                        implicitHeight: 32
                        radius: 6
                        color: Theme.surface
                        border.width: 1
                        border.color: editing ? Theme.accent : Theme.border
                        TapHandler { enabled: !address.editing; onTapped: address.edit() }
                        Item {
                            visible: !address.editing
                            anchors.fill: parent
                            anchors.leftMargin: 6; anchors.rightMargin: 6
                            clip: true
                        Row {
                            id: crumbRow
                            // a path longer than the bar shows its end, as Explorer's does
                            x: Math.min(0, parent.width - implicitWidth)
                            anchors.verticalCenter: parent.verticalCenter
                            Repeater {
                                model: Folders.crumbs(picker.path)
                                Row {
                                    required property var modelData
                                    required property int index
                                    anchors.verticalCenter: parent.verticalCenter
                                    Rectangle {
                                        objectName: "pickerCrumb"
                                        width: crumbText.implicitWidth + 14; height: 24; radius: 4
                                        anchors.verticalCenter: parent.verticalCenter
                                        color: crumbHover.hovered ? Theme.hover : "transparent"
                                        HoverHandler { id: crumbHover; cursorShape: Qt.PointingHandCursor }
                                        TapHandler { onTapped: picker.go(modelData.path) }
                                        Text {
                                            id: crumbText
                                            anchors.centerIn: parent
                                            text: modelData.name
                                            color: Theme.fg
                                            font.family: Theme.bodyFont
                                            font.pixelSize: Theme.bodySize
                                        }
                                    }
                                    Glyph { glyph: Theme.icon.right; size: 9; color: Theme.fgFaint; anchors.verticalCenter: parent.verticalCenter; leftPadding: 2; rightPadding: 2 }
                                }
                            }
                        }
                        }
                        TextField {
                            id: typed
                            objectName: "pickerAddress"
                            visible: address.editing
                            anchors.fill: parent
                            anchors.margins: 1
                            color: Theme.fg
                            font.family: Theme.bodyFont
                            font.pixelSize: Theme.bodySize
                            selectByMouse: true
                            selectionColor: Theme.alpha(Theme.accent, 0.4)
                            selectedTextColor: Theme.fg
                            background: null
                            leftPadding: 10
                            Keys.onEscapePressed: (e) => { address.editing = false; e.accepted = true }
                            onAccepted: {
                                if (picker.arrive(Folders.resolve(text))) address.editing = false
                                else typedBad.restart()
                            }
                            onActiveFocusChanged: if (!activeFocus) address.editing = false
                            SequentialAnimation on anchors.leftMargin {
                                id: typedBad; running: false; loops: 2
                                NumberAnimation { to: 5; duration: Theme.dur(40) }
                                NumberAnimation { to: -3; duration: Theme.dur(40) }
                                NumberAnimation { to: 1; duration: Theme.dur(40) }
                            }
                        }
                    }
                    Rectangle {
                        id: searchBox
                        function focusIt() { searchField.forceActiveFocus(); searchField.selectAll() }
                        implicitHeight: 32
                        implicitWidth: 230
                        radius: 6
                        color: Theme.surface
                        border.width: 1
                        border.color: searchField.activeFocus ? Theme.accent : Theme.border
                        Glyph { glyph: Theme.icon.search; size: 12; anchors.left: parent.left; anchors.leftMargin: 10; anchors.verticalCenter: parent.verticalCenter }
                        TextField {
                            id: searchField
                            objectName: "pickerSearch"
                            anchors.fill: parent
                            anchors.leftMargin: 26
                            color: Theme.fg
                            placeholderText: "Search " + picker.nameOf(picker.path)
                            placeholderTextColor: Theme.fgFaint
                            font.family: Theme.bodyFont
                            font.pixelSize: Theme.bodySize
                            selectionColor: Theme.alpha(Theme.accent, 0.4)
                            selectedTextColor: Theme.fg
                            background: null
                            selectByMouse: true
                            onTextChanged: searchWait.restart()
                            Keys.onEscapePressed: (e) => { if (text !== "") { text = ""; e.accepted = true } else e.accepted = false }
                            Connections { target: picker; function onPathChanged() { searchField.text = "" } }
                        }
                        Timer { id: searchWait; interval: 250; onTriggered: picker.search = searchField.text }
                    }
                }

                // Explorer's command bar: New Folder, sort, view, the preview pane
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    AccentButton {
                        objectName: "pickerNewFolder"
                        text: "New Folder"
                        kind: "outline"
                        enabled: picker.creatingIn === "" && picker.path !== ""
                        onClicked: picker.beginNew()
                        HoverTip { tip: "Make a folder in the one shown (Ctrl+Shift+N)." }
                    }
                    Item { width: 6 }
                    Text { text: "Sort by"; color: Theme.fgMuted; font.family: Theme.bodyFont; font.pixelSize: Theme.bodySize }
                    Dropdown {
                        objectName: "pickerSort"
                        boxWidth: 150
                        model: ["Name", "Date modified", "Type", "Size"]
                        currentIndex: ["name", "modified", "type", "size"].indexOf(picker.sortKey)
                        onActivated: (i) => { picker.sortKey = ["name", "modified", "type", "size"][i] }
                    }
                    IconButton {
                        size: 30
                        iconGlyph: picker.asc ? Theme.icon.up : Theme.icon.down
                        onClicked: picker.asc = !picker.asc
                        HoverTip { tip: picker.asc ? "A to Z, oldest, smallest first. Click for the other way." : "Z to A, newest, largest first. Click for the other way." }
                    }
                    Item { Layout.fillWidth: true }
                    SegmentedControl {
                        objectName: "pickerView"
                        model: ["Details", "Tiles", "Large icons"]
                        compact: true
                        currentIndex: ["details", "tiles", "icons"].indexOf(Shell.pickerView)
                        onActivated: (i) => Shell.pickerView = ["details", "tiles", "icons"][i]
                    }
                    AnimCheck {
                        objectName: "pickerPreviewPane"
                        text: "Preview pane"
                        checked: Shell.pickerPreview
                        onToggled: Shell.pickerPreview = checked
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    spacing: 10

                    // ---- the navigation pane -------------------------------------------------------
                    Rectangle {
                        id: nav
                        objectName: "pickerNav"
                        Layout.preferredWidth: 230
                        Layout.fillHeight: true
                        radius: 8
                        color: Theme.alpha(Theme.fg, 0.03)
                        ListModel { id: tree }
                        function build() {
                            tree.clear()
                            for (const d of Folders.drives())
                                tree.append({ name: d.name, path: d.path, depth: 0, open: false, kids: true, glyph: "" })
                            reveal(picker.path)
                        }
                        function toggle(i) {
                            const n = tree.get(i)
                            if (n.open) {
                                let k = i + 1
                                while (k < tree.count && tree.get(k).depth > n.depth) k++
                                tree.remove(i + 1, k - i - 1)
                                tree.setProperty(i, "open", false)
                                return
                            }
                            const kids = Folders.dirs(n.path)
                            for (let j = 0; j < kids.length; ++j)
                                tree.insert(i + 1 + j, { name: kids[j].name, path: kids[j].path, depth: n.depth + 1, open: false,
                                                         kids: kids[j].dirs > 0, glyph: Theme.icon.folder })
                            tree.setProperty(i, "open", true)
                        }
                        // the folder shown, opened out in the tree (Explorer's "expand to current folder")
                        function reveal(p) {
                            const norm = s => String(s || "").replace(/[\\\/]+$/, "").toLowerCase()
                            const steps = Folders.crumbs(p).map(s => norm(s.path))
                            const raw = Folders.crumbs(p)
                            for (let si = 0; si < steps.length - 1; ++si) {
                                let at = -1
                                for (let i = 0; i < tree.count; ++i)
                                    if (norm(tree.get(i).path) === steps[si]) { at = i; break }
                                if (at < 0) return
                                if (!tree.get(at).open) toggle(at)
                                // the next step not in its listing (a huge folder's is capped): added at its end
                                let k = at + 1, found = false
                                while (k < tree.count && tree.get(k).depth > tree.get(at).depth) {
                                    if (norm(tree.get(k).path) === steps[si + 1]) found = true
                                    k++
                                }
                                if (!found)
                                    tree.insert(k, { name: raw[si + 1].name, path: raw[si + 1].path, depth: tree.get(at).depth + 1,
                                                     open: false, kids: true, glyph: Theme.icon.folder })
                            }
                            Qt.callLater(() => {
                                for (let i = 0; i < tree.count; ++i)
                                    if (norm(tree.get(i).path) === norm(p)) { navList.positionViewAtIndex(i, ListView.Contain); break }
                            })
                        }
                        Connections {
                            target: picker
                            function onAboutToShow() { nav.build() }
                            function onPathChanged() { if (picker.opened) nav.reveal(picker.path) }
                            function onRevChanged() { if (picker.opened && tree.count === 0) nav.build() }
                        }

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: 6
                            spacing: 0
                            // the project and the shortcuts stay in view, as Explorer's Quick access does;
                            // the drives' tree under them scrolls on its own
                            Column {
                                objectName: "pickerShortcuts"
                                Layout.fillWidth: true
                                spacing: 1
                                    SmallHeading { text: "Pinned"; leftPadding: 8; topPadding: 4; bottomPadding: 4 }
                                    NavRow {
                                        objectName: "pickerProjectChip"
                                        visible: Folders.isDir(Shell.projectFolder)
                                        width: parent.width
                                        label: "Project" + (Shell.projectName ? ": " + Shell.projectName : "")
                                        glyph: Theme.icon.project
                                        on: picker.path === Shell.projectFolder
                                        onTapped: picker.go(Shell.projectFolder)
                                        HoverTip { tip: Shell.projectFolder }
                                    }
                                    // four folder shortcuts (user, 2026-10-10): an empty one keeps the
                                    // highlighted folder; right-click to change or remove one
                                    Repeater {
                                        model: Shell.pickerShortcuts
                                        NavRow {
                                            id: sc
                                            required property int index
                                            required property var modelData
                                            objectName: "pickerShortcut"
                                            readonly property string path: String(modelData || "")
                                            readonly property bool there: (picker.opened, picker.rev, path !== "" && Folders.isDir(path))
                                            width: parent.width
                                            label: path === "" ? "+ Shortcut" : picker.nameOf(path)
                                            glyph: path === "" ? Theme.icon.add : ""
                                            on: there && picker.path === path
                                            faded: path !== "" && !there
                                            onTapped: {
                                                if (path === "") { if (picker.folderNow !== "") Shell.setPickerShortcut(index, picker.folderNow) }
                                                else if (there) picker.go(path)
                                            }
                                            onRightTapped: { scMenu.index = sc.index; scMenu.popup() }
                                            HoverTip {
                                                tip: sc.path === ""
                                                     ? "An empty shortcut. Click it to keep the highlighted folder here"
                                                       + (picker.folderNow !== "" ? " (" + picker.folderNow + ")" : "") + "."
                                                     : sc.path + (sc.there ? "\nClick to go there. Right-click to change or remove it."
                                                                           : "\nThis folder isn't there any more. Right-click to change or remove the shortcut.")
                                            }
                                        }
                                    }
                                    Rectangle { width: parent.width - 16; x: 8; height: 1; color: Theme.border }
                                    SmallHeading { text: "This PC"; leftPadding: 8; topPadding: 6; bottomPadding: 4 }
                            }
                        ListView {
                            id: navList
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            clip: true
                            boundsBehavior: Flickable.StopAtBounds
                            ScrollBar.vertical: SlimScrollBar {}
                            model: tree
                            delegate: NavRow {
                                required property int index
                                required property string name
                                required property string path
                                required property int depth
                                required property bool open
                                required property bool kids
                                required property string glyph
                                objectName: "pickerTreeRow"
                                width: navList.width
                                indent: 4 + depth * 14
                                label: name
                                expandable: kids
                                expanded: open
                                on: picker.path.toLowerCase() === path.toLowerCase()
                                onTapped: picker.go(path)
                                onToggled: nav.toggle(index)
                            }
                        }
                        }
                    }

                    // ---- the folder's contents ------------------------------------------------------
                    Item {
                        id: body
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        // right-click on the empty part: New Folder
                        TapHandler {
                            objectName: "pickerColumnMenuArea"
                            acceptedButtons: Qt.RightButton
                            onTapped: picker.areaMenu()
                        }
                        ColumnLayout {
                            anchors.fill: parent
                            spacing: 6
                            // New Folder's name box
                            ColumnLayout {
                                visible: picker.creatingIn !== ""
                                Layout.fillWidth: true
                                spacing: 4
                                Rectangle {
                                    Layout.fillWidth: true
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
                                            function prime() { text = "New folder"; selectAll(); forceActiveFocus() }
                                            onVisibleChanged: if (visible) Qt.callLater(prime)
                                            onTextEdited: picker.createError = ""
                                            Keys.onReturnPressed: (e) => { picker.finishNew(text); e.accepted = true }
                                            Keys.onEnterPressed: (e) => { picker.finishNew(text); e.accepted = true }
                                            Keys.onEscapePressed: (e) => { picker.cancelNew(); e.accepted = true }
                                        }
                                        IconButton { size: 26; iconGlyph: Theme.icon.check; onClicked: picker.finishNew(nameBox.text); HoverTip { tip: "Make the folder (Enter)" } }
                                        IconButton { size: 26; iconGlyph: Theme.icon.close; onClicked: picker.cancelNew(); HoverTip { tip: "Cancel (Esc)" } }
                                    }
                                }
                                Text {
                                    visible: picker.createError !== ""
                                    Layout.fillWidth: true
                                    text: picker.createError
                                    color: Theme.danger
                                    font.family: Theme.bodyFont
                                    font.pixelSize: Theme.smallSize
                                    wrapMode: Text.WordWrap
                                }
                            }

                            Item {
                                Layout.fillWidth: true
                                Layout.fillHeight: true

                                // Details: a click on a heading sorts by it (again: the other way)
                                Item {
                                    id: details
                                    anchors.fill: parent
                                    visible: Shell.pickerView === "details"
                                    readonly property var cols: [{ key: "name", title: "Name", w: 0.46 }, { key: "modified", title: "Date modified", w: 0.2 },
                                                                 { key: "type", title: "Type", w: 0.17 }, { key: "size", title: "Size", w: 0.17 }]
                                    Row {
                                        id: head
                                        width: parent.width
                                        height: 30
                                        Repeater {
                                            model: details.cols
                                            Rectangle {
                                                required property var modelData
                                                required property int index
                                                objectName: "pickerHeading"
                                                width: (head.width - 12) * modelData.w + (index === 0 ? 12 : 0)
                                                height: head.height
                                                color: hh.hovered ? Theme.hover : "transparent"
                                                HoverHandler { id: hh; cursorShape: Qt.PointingHandCursor }
                                                TapHandler { onTapped: picker.sortBy(modelData.key) }
                                                Text {
                                                    anchors.left: parent.left; anchors.leftMargin: index === 0 ? 34 : 8
                                                    anchors.verticalCenter: parent.verticalCenter
                                                    text: modelData.key === "modified" && picker.search ? "Folder" : modelData.title
                                                    color: picker.sortKey === modelData.key ? Theme.fg : Theme.fgMuted
                                                    font.family: Theme.bodyFont
                                                    font.pixelSize: Theme.smallSize + 1
                                                    font.weight: picker.sortKey === modelData.key ? Font.DemiBold : Font.Normal
                                                }
                                                Glyph {
                                                    visible: picker.sortKey === modelData.key
                                                    anchors.right: parent.right; anchors.rightMargin: 8; anchors.verticalCenter: parent.verticalCenter
                                                    glyph: picker.asc ? Theme.icon.up : Theme.icon.down; size: 9
                                                }
                                                Rectangle { anchors.right: parent.right; width: 1; height: 16; anchors.verticalCenter: parent.verticalCenter; color: Theme.border; visible: index < 3 }
                                            }
                                        }
                                    }
                                    Rectangle { anchors.top: head.bottom; width: parent.width; height: 1; color: Theme.border }
                                    ListView {
                                        id: list
                                        objectName: "pickerList"
                                        anchors.top: head.bottom; anchors.topMargin: 2
                                        anchors.bottom: parent.bottom
                                        width: parent.width
                                        clip: true
                                        model: picker.items
                                        boundsBehavior: Flickable.StopAtBounds
                                        ScrollBar.vertical: SlimScrollBar {}
                                        delegate: Rectangle {
                                            id: row
                                            required property var modelData
                                            required property int index
                                            objectName: modelData.isDir ? "pickerFolder" : "pickerClip"
                                            readonly property string path: modelData.path
                                            readonly property bool on: picker.sel === modelData.path
                                            readonly property bool pickable: modelData.isDir || picker.allowFiles
                                            width: list.width
                                            height: 30
                                            radius: 6
                                            color: on ? Theme.alpha(Theme.accent, 0.16) : (rowHover.hovered ? Theme.hover : "transparent")
                                            opacity: pickable ? 1 : 0.6
                                            HoverHandler { id: rowHover }
                                            TapHandler { onTapped: picker.highlight(row.modelData); onDoubleTapped: picker.openItem(row.modelData) }
                                            TapHandler { acceptedButtons: Qt.RightButton; onTapped: picker.rowMenu(row.modelData) }
                                            Row {
                                                anchors.fill: parent
                                                Item {
                                                    width: (head.width - 12) * 0.46 + 12; height: parent.height
                                                    Glyph {
                                                        id: ig; x: 10; anchors.verticalCenter: parent.verticalCenter; size: 14
                                                        glyph: row.modelData.isDir ? Theme.icon.folder : Theme.icon.video
                                                        color: row.modelData.isDir ? (row.modelData.clips > 0 ? Theme.accentInk : Theme.fgMuted) : Theme.fgMuted
                                                    }
                                                    Text {
                                                        anchors.left: ig.right; anchors.leftMargin: 10; anchors.right: parent.right; anchors.rightMargin: 8
                                                        anchors.verticalCenter: parent.verticalCenter
                                                        text: row.modelData.name
                                                        elide: Text.ElideMiddle
                                                        color: Theme.fg
                                                        font.family: Theme.bodyFont
                                                        font.pixelSize: Theme.bodySize
                                                        font.weight: row.on ? Font.DemiBold : Font.Normal
                                                    }
                                                }
                                                Cell { width: (head.width - 12) * 0.2; text: picker.search ? (row.modelData.where || "(here)") : row.modelData.modified }
                                                Cell {
                                                    width: (head.width - 12) * 0.17
                                                    text: row.modelData.isDir ? (row.modelData.clips ? picker.folderLine(row.modelData) : "File folder") : row.modelData.type
                                                    color: row.modelData.isDir && row.modelData.clips ? Theme.accentInk : Theme.fgMuted
                                                }
                                                Cell { width: (head.width - 12) * 0.17; text: row.modelData.sizeText; horizontalAlignment: Text.AlignRight; rightPadding: 14 }
                                            }
                                        }
                                    }
                                }

                                // Tiles and Large icons (a folder shows how many clips it holds)
                                GridView {
                                    id: grid
                                    objectName: "pickerGrid"
                                    anchors.fill: parent
                                    visible: Shell.pickerView !== "details"
                                    readonly property bool large: Shell.pickerView === "icons"
                                    clip: true
                                    model: visible ? picker.items : []
                                    cellWidth: large ? 132 : Math.max(240, Math.floor(width / Math.max(1, Math.floor(width / 260))))
                                    cellHeight: large ? 128 : 64
                                    boundsBehavior: Flickable.StopAtBounds
                                    ScrollBar.vertical: SlimScrollBar {}
                                    delegate: Item {
                                        id: cell
                                        required property var modelData
                                        objectName: modelData.isDir ? "pickerFolder" : "pickerClip"
                                        readonly property string path: modelData.path
                                        readonly property bool on: picker.sel === modelData.path
                                        width: grid.cellWidth; height: grid.cellHeight
                                        opacity: modelData.isDir || picker.allowFiles ? 1 : 0.6
                                        Rectangle {
                                            anchors.fill: parent; anchors.margins: 3; radius: 8
                                            color: cell.on ? Theme.alpha(Theme.accent, 0.16) : (ch.hovered ? Theme.hover : "transparent")
                                            border.width: cell.on ? 1 : 0
                                            border.color: Theme.alpha(Theme.accent, 0.5)
                                            HoverHandler { id: ch }
                                            TapHandler { onTapped: picker.highlight(cell.modelData); onDoubleTapped: picker.openItem(cell.modelData) }
                                            TapHandler { acceptedButtons: Qt.RightButton; onTapped: picker.rowMenu(cell.modelData) }
                                        }
                                        Column {
                                            visible: grid.large
                                            anchors.horizontalCenter: parent.horizontalCenter
                                            y: 12
                                            spacing: 6
                                            Item {
                                                width: 60; height: 56
                                                anchors.horizontalCenter: parent.horizontalCenter
                                                Glyph { anchors.centerIn: parent; size: 46; glyph: cell.modelData.isDir ? Theme.icon.folder : Theme.icon.video
                                                        color: cell.modelData.isDir ? Theme.accentInk : Theme.fgMuted }
                                                Rectangle {
                                                    visible: cell.modelData.isDir && cell.modelData.clips > 0
                                                    anchors.right: parent.right; anchors.bottom: parent.bottom
                                                    width: badge.implicitWidth + 10; height: 18; radius: 9
                                                    color: Theme.accent
                                                    Text { id: badge; anchors.centerIn: parent; text: cell.modelData.clips; color: Theme.bg; font.family: Theme.bodyFont; font.pixelSize: Theme.smallSize; font.weight: Font.Bold }
                                                }
                                            }
                                            Text {
                                                width: grid.cellWidth - 16
                                                horizontalAlignment: Text.AlignHCenter
                                                text: cell.modelData.name
                                                wrapMode: Text.WrapAnywhere
                                                maximumLineCount: 2
                                                elide: Text.ElideRight
                                                color: Theme.fg
                                                font.family: Theme.bodyFont
                                                font.pixelSize: Theme.smallSize + 1
                                            }
                                        }
                                        Row {
                                            visible: !grid.large
                                            anchors.verticalCenter: parent.verticalCenter
                                            x: 12
                                            spacing: 12
                                            Glyph { anchors.verticalCenter: parent.verticalCenter; size: 30; glyph: cell.modelData.isDir ? Theme.icon.folder : Theme.icon.video
                                                    color: cell.modelData.isDir ? Theme.accentInk : Theme.fgMuted }
                                            Column {
                                                anchors.verticalCenter: parent.verticalCenter
                                                Text { width: grid.cellWidth - 70; text: cell.modelData.name; elide: Text.ElideMiddle; color: Theme.fg; font.family: Theme.bodyFont; font.pixelSize: Theme.bodySize }
                                                Text {
                                                    width: grid.cellWidth - 70; elide: Text.ElideRight
                                                    text: cell.modelData.isDir ? picker.folderLine(cell.modelData) : cell.modelData.type + "  ·  " + cell.modelData.sizeText
                                                    color: Theme.fgMuted; font.family: Theme.bodyFont; font.pixelSize: Theme.smallSize + 1
                                                }
                                            }
                                        }
                                    }
                                }
                                Text {
                                    anchors.centerIn: parent
                                    visible: picker.items.length === 0
                                    text: picker.search ? "Nothing here matches “" + picker.search + "”."
                                                        : (picker.allowFiles ? "No folders or clips in this folder." : "No folders in this folder.")
                                    color: Theme.fgFaint
                                    font.family: Theme.bodyFont
                                    font.pixelSize: Theme.bodySize
                                }
                            }
                        }
                    }

                    // ---- the preview pane: what is highlighted, else the folder shown ----------------
                    Rectangle {
                        id: pane
                        objectName: "pickerPreview"
                        visible: Shell.pickerPreview
                        Layout.preferredWidth: 250
                        Layout.fillHeight: true
                        radius: 8
                        color: Theme.alpha(Theme.fg, 0.03)
                        readonly property string what: picker.sel || picker.path
                        readonly property var d: (picker.rev, picker.opened && visible ? Folders.details(what) : ({}))
                        Column {
                            anchors.fill: parent
                            anchors.margins: 14
                            spacing: 10
                            Rectangle {
                                width: parent.width; height: width * 9 / 16; radius: 8
                                color: Theme.alpha(Theme.fg, 0.05)
                                border.width: 1; border.color: Theme.border
                                clip: true
                                Glyph { anchors.centerIn: parent; size: 44; visible: thumb.status !== Image.Ready
                                        glyph: pane.d.dir === false ? Theme.icon.video : Theme.icon.folder
                                        color: pane.d.dir === false ? Theme.fgMuted : Theme.accentInk }
                                // a clip's first frame (Studio's own thumbnails, as the job lists show)
                                Image {
                                    id: thumb
                                    anchors.fill: parent
                                    anchors.margins: 1
                                    fillMode: Image.PreserveAspectFit
                                    asynchronous: true
                                    cache: true
                                    source: pane.d.dir === false ? "image://clipthumb/" + encodeURIComponent(pane.what) : ""
                                }
                            }
                            Text { width: parent.width; text: pane.d.name || ""; wrapMode: Text.WrapAnywhere; maximumLineCount: 3; elide: Text.ElideRight
                                   color: Theme.fg; font.family: Theme.buttonFont; font.pixelSize: Theme.headingSize; font.weight: Font.DemiBold }
                            Text { width: parent.width; text: pane.d.kind || ""; color: Theme.fgMuted; font.family: Theme.bodyFont; font.pixelSize: Theme.bodySize }
                            Rectangle { width: parent.width; height: 1; color: Theme.border; visible: !!pane.d.name }
                            Repeater {
                                model: {
                                    const d = pane.d
                                    if (!d.name) return []
                                    const rows = []
                                    if (d.dir) {
                                        rows.push(["Clips", String(d.clips)])
                                        rows.push(["Folders", String(d.dirs)])
                                        if (d.sizeText) rows.push(["Clips' size", d.sizeText])
                                    } else {
                                        rows.push(["Size", d.sizeText])
                                        rows.push(["In", d.where])
                                    }
                                    rows.push(["Modified", d.modified])
                                    return rows
                                }
                                Column {
                                    required property var modelData
                                    width: parent.width
                                    Text { text: modelData[0]; color: Theme.fgFaint; font.family: Theme.bodyFont; font.pixelSize: Theme.smallSize }
                                    Text { width: parent.width; text: modelData[1]; wrapMode: Text.WrapAnywhere; color: Theme.fg; font.family: Theme.bodyFont; font.pixelSize: Theme.bodySize }
                                }
                            }
                        }
                    }
                }

                Text {
                    text: (picker.allowFiles
                          ? "Double-click a folder to go in, a clip to pick it. Select picks what is highlighted, or the folder you're in."
                          : "Double-click a folder to go in. Select Folder picks the one highlighted, or the folder you're in.")
                          + " Right-click for New Folder, or to move a folder or clip to the Recycle Bin. Drop a folder or clip here to go to it."
                    color: Theme.fgFaint
                    font.family: Theme.bodyFont
                    font.pixelSize: Theme.smallSize
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }
                Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
                // what will be picked, and the buttons
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    Text {
                        objectName: "pickerStatus"
                        text: picker.items.length + (picker.items.length === 1 ? " item" : " items")
                              + (picker.search ? " found" : "")
                        color: Theme.fgFaint
                        font.family: Theme.bodyFont
                        font.pixelSize: Theme.smallSize + 1
                    }
                    Rectangle { width: 1; height: 16; color: Theme.border }
                    Icon { glyph: picker.choice !== "" && Folders.isFile(picker.choice) ? Theme.icon.video : Theme.icon.folder; size: 13; color: Theme.accentInk }
                    Text {
                        objectName: "pickerChosen"
                        text: picker.choice || "Pick a folder"
                        color: picker.choice ? Theme.fg : Theme.fgFaint
                        font.family: Theme.bodyFont
                        font.pixelSize: Theme.bodySize
                        elide: Text.ElideMiddle
                        Layout.fillWidth: true
                        Layout.minimumWidth: 60          // a long path elides; it never pushes the buttons out
                    }
                    AccentButton {
                        id: winBtn
                        objectName: "pickerWindows"
                        text: "Windows Explorer…"
                        kind: "outline"
                        // a field that takes a clip too: which one Windows' dialog picks
                        onClicked: picker.allowFiles ? winMenu.popup(winBtn, 0, -winMenu.implicitHeight - 4) : winFolderDlg.open()
                        HoverTip { tip: "Pick with Windows' own dialog instead, if this picker won't do." }
                    }
                    AccentButton { objectName: "pickerCancel"; text: "Cancel"; kind: "ghost"; onClicked: picker.close() }
                    AccentButton {
                        objectName: "pickerSelect"
                        text: picker.choice !== "" && Folders.isFile(picker.choice) ? "Select Clip" : "Select Folder"
                        kind: "filled"
                        enabled: picker.choice !== ""
                        onClicked: picker.pick(picker.choice)
                    }
                }
            }
        }
    }

    // ---- menus, the question, Windows' own dialogs -----------------------------------------
    StudioMenu {
        id: newMenu
        parent: picker.contentItem   // where it opens (it lives outside the window, see body)
        objectName: "pickerNewMenu"
        MenuItem { text: "New Folder"; onTriggered: picker.beginNew() }
    }
    // a folder's or clip's own menu
    StudioMenu {
        id: itemMenu
        parent: picker.contentItem   // where it opens (it lives outside the window, see body)
        objectName: "pickerItemMenu"
        property string target: ""
        property bool isDir: false
        property int dirs: 0
        property int clips: 0
        MenuItem { text: itemMenu.isDir ? "Open" : "Select"; onTriggered: itemMenu.isDir ? picker.go(itemMenu.target) : picker.pick(itemMenu.target)
                   enabled: itemMenu.isDir || picker.allowFiles }
        MenuItem { text: "New Folder"; onTriggered: picker.beginNew() }
        MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: Theme.border } }
        MenuItem {
            objectName: "pickerRecycle"
            text: "Move to Recycle Bin…"
            onTriggered: picker.askRecycle(itemMenu.target, itemMenu.isDir, itemMenu.dirs, itemMenu.clips)
        }
    }
    StudioMenu {
        id: scMenu
        parent: picker.contentItem   // where it opens (it lives outside the window, see body)
        objectName: "pickerShortcutMenu"
        property int index: 0
        MenuItem {
            objectName: "pickerShortcutSet"
            text: picker.folderNow !== "" ? "Keep “" + picker.nameOf(picker.folderNow) + "” here" : "Keep the highlighted folder here"
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
    // Windows' own dialogs, as a backup (user, 2026-10-10): opened on the folder shown; what
    // they pick is picked here
    FolderDialog {
        id: winFolderDlg
        objectName: "pickerWindowsFolder"
        // the app's window, said outright: with this picker unshown (Pick Folders with Windows
        // Explorer) the dialog found none of its own and never opened (2026-10-31)
        parentWindow: picker.parent ? picker.parent.Window.window : null
        title: picker.title
        currentFolder: Folders.toUrl(picker.folderNow)
        onAccepted: picker.pick(Folders.toPath(selectedFolder))
    }
    FileDialog {
        id: winFileDlg
        objectName: "pickerWindowsFile"
        parentWindow: picker.parent ? picker.parent.Window.window : null
        title: picker.title
        currentFolder: Folders.toUrl(picker.folderNow)
        nameFilters: ["Clips (" + (picker.fileTypes.length ? picker.fileTypes : Folders.videoTypes())
                                  .map(t => "*." + t).join(" ") + ")", "All files (*)"]
        onAccepted: picker.pick(Folders.toPath(selectedFile))
    }
    StudioMenu {
        id: winMenu
        parent: picker.contentItem   // where it opens (it lives outside the window, see body)
        objectName: "pickerWindowsMenu"
        MenuItem { text: "Pick a folder…"; onTriggered: winFolderDlg.open() }
        MenuItem { text: "Pick a clip…"; onTriggered: winFileDlg.open() }
    }
    // the same choice with this picker unshown (Pick Folders with Windows Explorer)
    StudioMenu {
        id: explorerMenu
        objectName: "pickerExplorerMenu"
        // the field's own item, on the page: Overlay.overlay read from in here (this picker never
        // shown) found no window, and the menu never opened -- Browse did nothing on a field that
        // takes a clip too (user, 2026-10-31)
        parent: picker.parent
        MenuItem { objectName: "pickerExplorerFolder"; text: "Pick a folder…"; onTriggered: winFolderDlg.open() }
        MenuItem { objectName: "pickerExplorerClip"; text: "Pick a clip…"; onTriggered: winFileDlg.open() }
    }
}
