import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Window
import Studio

// The console in a window of its own, tabbed as v1's was (unified_gui/
// console_window.py; user, 2026-09-30): a tab per stage, Extra (Scene Cut and
// the Depth Map Editor), and All. Open Console -- a stage's Current Clip Info,
// Settings > Studio, Ctrl+` -- shows it on the tab of the stage showing; every
// tab stays clickable. Closing only hides it: lines keep arriving (LogHub),
// and reopening shows them all. It remembers its place and size, and can sit
// on the other monitor while a job runs.
Window {
    id: con
    objectName: "consoleWindow"
    title: "StereoCrafter Console"
    // its own top-level window, never kept above Studio's
    transientParent: null
    // frosted as Studio's own window when the glass is on (user, 2026-10-01):
    // the palette's ground, thinned over the backdrop as Main's is
    color: Theme.ground
    width: 960
    height: 540
    // never narrower than its heading row: every tool stays on screen
    minimumWidth: Math.max(560, head.implicitWidth + 22)
    minimumHeight: 280

    property var hub: (typeof Logs !== "undefined") ? Logs : null
    readonly property var tabs: hub ? hub.tabs : []
    property int current: 0
    readonly property var model: hub && tabs.length ? hub.model(tabs[current].id) : null
    // Main binds the stage showing (0-3 the stages, 4 Extra); opening picks its tab.
    property int stageIndex: 0
    // Pages open it with consoleRef.open = true (ClipInfo), Main toggles it.
    property bool open: false
    property Item owner: null           // Main's window content, to centre over at first
    readonly property bool customFrame: typeof ConsoleFrame !== "undefined" && ConsoleFrame.active
                                        && Shell.customTitleBarAtStart
    readonly property int fontSize: Shell.consoleFontSize
    // v1: the bar's trough is the ground lifted a little (toward white on a
    // dark palette, toward black on a light one), so an empty bar still shows
    readonly property color trough: Theme.mix(Theme.bg, Theme.isDark ? Qt.rgba(1, 1, 1, 1) : Qt.rgba(0, 0, 0, 1),
                                              Theme.isDark ? 0.14 : 0.10)

    function tabIndex(id) {
        for (let i = 0; i < tabs.length; ++i) if (tabs[i].id === id) return i
        return 0
    }
    function stageTab(i) {
        return ["depthcrafter", "splatting", "inpainting", "merging", "extra"][i] || "all"
    }
    // Whether a screen shows enough of the saved rect's top to grab it: the
    // console is frameless, and opening where no screen is (the second monitor
    // gone, the layout changed) left nothing to drag (bug sweep A1)
    function onScreen(g) {
        const scr = Qt.application.screens
        for (let i = 0; i < scr.length; ++i) {
            const s = scr[i]
            const w = Math.min(g[0] + g[2], s.virtualX + s.width) - Math.max(g[0], s.virtualX)
            const h = Math.min(g[1] + 40, s.virtualY + s.height) - Math.max(g[1], s.virtualY)
            if (w >= 120 && h >= 20)
                return true
        }
        return false
    }
    function place() {
        const g = Shell.consoleGeometry
        if (g && g.length === 4 && g[2] >= minimumWidth && g[3] >= minimumHeight && onScreen(g)) {
            x = g[0]; y = g[1]; width = g[2]; height = g[3]
        } else if (owner && owner.Window.window) {
            const w = owner.Window.window
            x = w.x + Math.max(0, (w.width - width) / 2)
            y = w.y + Math.max(0, (w.height - height) / 2)
        }
    }
    function remember() {
        if (visibility === Window.Windowed)
            Shell.consoleGeometry = [x, y, width, height]
    }

    onOpenChanged: {
        if (open) {
            current = tabIndex(stageTab(stageIndex))
            if (!visible) { place(); show() }
            else if (visibility === Window.Minimized) showNormal()
            raise()
            requestActivate()
        } else if (visible) {
            close()
        }
    }
    onClosing: remember()
    // Hidden, it is attached again when next shown (user, 2026-10-02: the
    // console could not be moved or resized after being closed and opened
    // again): QWindow.close() destroys the native window and show() makes a new
    // one -- plain Qt frameless styles, nothing WindowFrame set, a hit-test
    // still pointed at the old HWND, so the whole window answered "client".
    onVisibleChanged: if (!visible) { open = false; frameAttached = false }

    // The custom caption goes on after the first frame (as Main's does), and
    // the glass after it (attaching re-sets the window's styles).
    property bool frameAttached: false
    onFrameSwapped: {
        if (frameAttached) return
        frameAttached = true
        if (customFrame) {
            try {
                ConsoleFrame.attach(con, titleBar, titleBar.maxButton, [titleBar.minButton, titleBar.closeButton])
            } catch (e) {
                console.warn("Console title bar did not attach:", e)
            }
        }
        if (glassable) {
            ConsoleBackdrop.bind(con)
            ConsoleBackdrop.setDark(Theme.isDark)
            applyGlass()
        }
    }

    // ---- glass: Settings' choice, as Studio's window takes it -------------
    readonly property bool glassable: typeof ConsoleBackdrop !== "undefined" && ConsoleBackdrop.supported
    function applyGlass() {
        if (!glassable || !frameAttached) return
        if (Shell.glass === "none") {
            dropGlass.interval = Math.max(1, Theme.dur(560))   // once the tint is opaque (Theme.glass)
            dropGlass.restart()
        } else {
            dropGlass.stop()
            ConsoleBackdrop.apply(Shell.glass)
        }
    }
    Timer { id: dropGlass; onTriggered: ConsoleBackdrop.apply("none") }
    Connections {
        target: Shell
        function onChanged() { if (con.appliedGlass !== Shell.glass) { con.appliedGlass = Shell.glass; con.applyGlass() } }
    }
    property string appliedGlass: Shell.glass
    Connections {
        target: Theme
        function onIsDarkChanged() { if (con.glassable && con.frameAttached) ConsoleBackdrop.setDark(Theme.isDark) }
    }
    // the acrylic is sized when set: re-seat it once a resize settles
    Timer {
        id: glassRefit
        interval: 150
        onTriggered: if (con.glassable && con.frameAttached && Shell.glass !== "none") ConsoleBackdrop.refresh()
    }
    onWidthChanged: if (visible) glassRefit.restart()
    onHeightChanged: if (visible) glassRefit.restart()

    TitleBar {
        id: titleBar
        visible: con.customFrame
        height: visible ? implicitHeight : 0
        width: parent.width
        window: con
        frame: typeof ConsoleFrame !== "undefined" ? ConsoleFrame : null
        centerText: Shell.projectName      // the movie, as on Studio's caption
    }

    RowLayout {
        id: head
        x: 10
        width: parent.width - 22
        y: titleBar.height + 6
        height: 40
        spacing: 10

        StageTabBar {
            id: tabBar
            objectName: "consoleTabs"
            Layout.preferredHeight: 40
            Layout.preferredWidth: implicitWidth
            numbered: false
            model: con.tabs.map(t => t.name)
            currentIndex: con.current
            onActivated: (i) => con.current = i
        }
        Text {
            text: con.model ? con.model.count + " lines" : ""
            color: Theme.fgFaint
            font.family: Theme.bodyFont
            font.pixelSize: Theme.smallSize
            Layout.alignment: Qt.AlignVCenter
        }
        Item { Layout.fillWidth: true }
        Toggle {
            id: follow
            objectName: "consoleFollow"
            text: "Follow"
            checked: true
            onCheckedChanged: if (checked) lines.positionViewAtEnd()
            HoverTip { tip: "Keep the newest line in view. Scrolling up to read turns it off." }
        }
        IconButton {
            iconGlyph: ""             // FontDecrease
            size: 30
            enabled: con.fontSize > 9
            onClicked: Shell.consoleFontSize = con.fontSize - 1
            HoverTip { tip: "Smaller text" }
        }
        IconButton {
            iconGlyph: ""             // FontIncrease
            size: 30
            enabled: con.fontSize < 20
            onClicked: Shell.consoleFontSize = con.fontSize + 1
            HoverTip { tip: "Larger text" }
        }
        // icons, so the six tabs and the tools fit one row (user, 2026-09-30:
        // Clear had gone off the right edge)
        IconButton {
            objectName: "consoleCopy"
            iconGlyph: copied.running ? Theme.icon.check : Theme.icon.copy
            size: 30
            onClicked: if (!con.copySelection()) copier.copyAll()
            HoverTip { tip: copied.running ? "Copied"
                            : (con.hasSelection ? "Copy the selected text (Ctrl+C)."
                                                : "Copy all: every line of this tab, to the clipboard. "
                                                  + "Drag across lines to pick part of them.") }
        }
        IconButton {
            objectName: "consoleClear"
            iconGlyph: Theme.icon.del
            size: 30
            onClicked: if (con.model) con.model.clear()
            HoverTip { tip: "Clear: empties this tab. The other tabs keep their lines." }
        }
    }

    Rectangle {
        id: body
        objectName: "consoleBody"
        x: 10
        y: head.y + head.height + 8
        width: parent.width - 20
        height: parent.height - y - 10
        // v1's console: the log straight on the palette's ground (with the
        // glass on, thinned a little less than the window around it)
        radius: 10
        color: Theme.alpha(Theme.bg, 1 - Theme.glass * 0.4)
        border.width: Theme.outlineWidth
        border.color: Theme.outline

        ListView {
            id: lines
            objectName: "consoleLines"
            anchors.fill: parent
            anchors.margins: 12
            clip: true
            model: con.model
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: SlimScrollBar {}
            onCountChanged: if (follow.checked) Qt.callLater(positionViewAtEnd)
            onModelChanged: if (follow.checked) Qt.callLater(positionViewAtEnd)
            onMovementStarted: follow.checked = false
            // v1's style (user, 2026-09-30): a record as "HH:MM:SS - LEVEL - message",
            // a printed line as printed, and a progress bar drawn as an accent
            // span over a faint trough, stretched so its numbers end at the
            // right edge (log_model.parse_bar hands over its parts)
            delegate: Item {
                id: entry
                required property int index
                required property string line
                required property int level
                required property string prefix
                required property var bar
                readonly property bool isBar: !!bar && bar.length === 3
                readonly property color ink: level >= 40 ? Theme.danger : (level >= 30 ? Theme.warning
                                             : (level <= 10 ? Theme.fgFaint : Theme.fg))
                width: ListView.view.width - 12
                height: isBar ? barRow.height : plain.implicitHeight
                readonly property alias textItem: plain
                property bool barPicked: false
                // this line's share of the window's selection
                function syncSelection() {
                    const s = con.selRange()
                    const inside = s !== null && entry.index >= s.r0 && entry.index <= s.r1
                    entry.barPicked = inside && entry.isBar
                    if (!inside || entry.isBar) { plain.deselect(); return }
                    plain.select(entry.index === s.r0 ? Math.min(s.p0, plain.length) : 0,
                                 entry.index === s.r1 ? Math.min(s.p1, plain.length) : plain.length)
                }
                Connections { target: con; function onSelRevChanged() { entry.syncSelection() } }
                Component.onCompleted: syncSelection()
                Rectangle {
                    visible: entry.barPicked
                    anchors.fill: parent
                    color: Theme.alpha(Theme.accent, 0.35)
                }
                TextEdit {
                    id: plain
                    objectName: "consoleText"
                    visible: !entry.isBar
                    width: parent.width
                    wrapMode: Text.Wrap
                    textFormat: Text.PlainText
                    readOnly: true
                    selectByMouse: false            // the list's drag picks across lines (below)
                    activeFocusOnPress: false
                    persistentSelection: true
                    selectionColor: Theme.alpha(Theme.accent, 0.45)
                    selectedTextColor: Theme.fg
                    text: entry.prefix + entry.line
                    color: entry.ink
                    font.family: Theme.monoFont
                    font.pixelSize: con.fontSize
                }
                RowLayout {
                    id: barRow
                    objectName: "consoleBar"
                    visible: entry.isBar
                    width: parent.width
                    height: barLeft.implicitHeight
                    spacing: con.fontSize * 0.6
                    Text {
                        id: barLeft
                        text: entry.isBar ? entry.bar[0] : ""
                        visible: text !== ""
                        color: Theme.fg
                        elide: Text.ElideRight
                        Layout.maximumWidth: barRow.width * 0.45
                        font.family: Theme.monoFont
                        font.pixelSize: con.fontSize
                    }
                    Rectangle {
                        objectName: "consoleBarTrough"
                        Layout.fillWidth: true
                        Layout.minimumWidth: con.fontSize * 6
                        Layout.preferredHeight: Math.round(con.fontSize * 0.9)
                        Layout.alignment: Qt.AlignVCenter
                        color: con.trough
                        Rectangle {
                            objectName: "consoleBarFill"
                            width: parent.width * (entry.isBar ? entry.bar[1] : 0)
                            height: parent.height
                            color: Theme.accent
                        }
                    }
                    Text {
                        text: entry.isBar ? entry.bar[2] : ""
                        visible: text !== ""
                        color: Theme.fg
                        font.family: Theme.monoFont
                        font.pixelSize: con.fontSize
                    }
                }
            }
        }
        // Drag picks text across lines; a click clears it. Over the list, so
        // a line's own text never takes the press; the wheel still scrolls.
        MouseArea {
            id: picker
            objectName: "consolePicker"
            anchors.fill: lines
            // not over the scroll bar (user, 2026-10-04: grabbing it picked text):
            // the bar (up to 14 px wide, hovered) sits in the gutter the lines leave
            anchors.rightMargin: 14
            acceptedButtons: Qt.LeftButton
            preventStealing: true
            cursorShape: Qt.IBeamCursor
            property bool moved: false
            property real lastX: 0
            property real lastY: 0
            // the model position under (mx, my), in the list's coordinates
            function hit(mx, my) {
                const n = lines.count
                if (n === 0) return null
                const y = Math.max(0, Math.min(lines.height - 1, my))
                let row = lines.indexAt(Math.max(0, Math.min(lines.width - 1, mx)), y + lines.contentY)
                if (row < 0) row = (y + lines.contentY < 0) ? 0 : n - 1
                const item = lines.itemAtIndex(row)
                if (!item) return { row: row, pos: 0 }
                if (my < 0) return { row: row, pos: 0 }
                if (my >= lines.height && row === n - 1) return { row: row, pos: 1e9 }
                if (item.isBar) return { row: row, pos: mx < lines.width / 2 ? 0 : 1e9 }
                const p = mapToItem(item.textItem, mx, my)
                return { row: row, pos: item.textItem.positionAt(p.x, p.y) }
            }
            onPressed: (mouse) => {
                follow.checked = false
                moved = false
                lastX = mouse.x; lastY = mouse.y
                const h = hit(mouse.x, mouse.y)
                con.selAnchor = h
                con.selHead = h
                con.selRev++
            }
            onPositionChanged: (mouse) => {
                if (!pressed || con.selAnchor === null) return
                moved = true
                lastX = mouse.x; lastY = mouse.y
                con.selHead = hit(mouse.x, mouse.y)
                con.selRev++
            }
            onReleased: if (!moved) con.clearSelection()
            onDoubleClicked: (mouse) => {             // the whole line
                const h = hit(mouse.x, mouse.y)
                if (!h) return
                con.selAnchor = { row: h.row, pos: 0 }
                con.selHead = { row: h.row, pos: 1e9 }
                con.selRev++
            }
            // dragged past the top or bottom: the list scrolls on
            Timer {
                interval: 40
                repeat: true
                running: picker.pressed && picker.moved && (picker.lastY < 0 || picker.lastY > lines.height)
                onTriggered: {
                    const step = picker.lastY < 0 ? Math.min(-picker.lastY, 60) * -0.5 : Math.min(picker.lastY - lines.height, 60) * 0.5
                    const maxY = Math.max(lines.originY, lines.originY + lines.contentHeight - lines.height)
                    lines.contentY = Math.max(lines.originY, Math.min(maxY, lines.contentY + step))
                    con.selHead = picker.hit(picker.lastX, picker.lastY)
                    con.selRev++
                }
            }
        }
        Text {
            anchors.centerIn: parent
            visible: lines.count === 0
            text: "Nothing logged here yet."
            color: Theme.fgFaint
            font.family: Theme.bodyFont
            font.pixelSize: Theme.bodySize
        }
    }

    // ---- selection (user, 2026-10-01): drag across lines, Ctrl+C copies --
    // the part picked; Ctrl+A picks the tab. Anchor and head are {row, pos}
    // in the model (pos: a character of prefix + line), so the copy comes
    // from the log itself, lines scrolled out of view included.
    property var selAnchor: null
    property var selHead: null
    property int selRev: 0              // bumped on every change: the lines re-read it
    readonly property bool hasSelection: selAnchor !== null && selHead !== null
                                         && (selAnchor.row !== selHead.row || selAnchor.pos !== selHead.pos)
    function selRange() {
        if (!hasSelection) return null
        const a = selAnchor, h = selHead
        const fwd = a.row < h.row || (a.row === h.row && a.pos <= h.pos)
        return fwd ? { r0: a.row, p0: a.pos, r1: h.row, p1: h.pos } : { r0: h.row, p0: h.pos, r1: a.row, p1: a.pos }
    }
    function clearSelection() {
        if (selAnchor === null && selHead === null) return
        selAnchor = null; selHead = null; selRev++
    }
    function selectAll() {
        if (!model || model.count === 0) return
        selAnchor = { row: 0, pos: 0 }
        selHead = { row: model.count - 1, pos: 1e9 }
        selRev++
    }
    function copySelection() {
        const s = selRange()
        if (!s || !model) return false
        copier.copyText(model.textRange(s.r0, s.p0, s.r1, s.p1))
        return true
    }
    // Rows first..last removed (a live bar moved to the bottom, or the oldest
    // lines trimmed): the selection moves with its lines instead of going --
    // it was cleared each time, so text couldn't be picked during a job that
    // logged between bar redraws (bug sweep A1). A point in the removed rows
    // goes to the start of the line after them.
    function shiftSelection(first, last) {
        if (selAnchor === null && selHead === null) return
        const n = last - first + 1
        function fix(p) {
            if (p === null) return null
            if (p.row > last) return { row: p.row - n, pos: p.pos }
            if (p.row >= first) return { row: first, pos: 0 }
            return p
        }
        selAnchor = fix(selAnchor); selHead = fix(selHead)
        if (!hasSelection) { selAnchor = null; selHead = null }
        selRev++
    }
    onModelChanged: clearSelection()
    Connections {
        target: con.model
        function onRowsRemoved(parent, first, last) { con.shiftSelection(first, last) }
        function onModelReset() { con.clearSelection() }       // the tab cleared
    }
    Shortcut { sequences: [StandardKey.Copy]; onActivated: con.copySelection() }
    Shortcut { sequences: [StandardKey.SelectAll]; onActivated: con.selectAll() }

    Shortcut { sequence: "Ctrl+`"; onActivated: con.close() }
    Shortcut { sequence: "Ctrl+Tab"; onActivated: con.current = (con.current + 1) % con.tabs.length }
    Shortcut { sequence: "Ctrl+Shift+Tab"; onActivated: con.current = (con.current + con.tabs.length - 1) % con.tabs.length }

    Timer { id: copied; interval: 1400 }
    // Clipboard without a C++ helper: a hidden TextEdit can copy.
    TextEdit {
        id: copier
        visible: false
        function copyAll() {
            copyText(con.model ? con.model.allText() : "")
        }
        function copyText(t) {
            text = t
            selectAll()
            copy()   // TextEdit's own copy(), not this function
            text = ""
            copied.restart()
        }
    }
}
