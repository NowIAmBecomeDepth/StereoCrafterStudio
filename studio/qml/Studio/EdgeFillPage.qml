import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import Studio

// Edge Fill (Extra tab; user, 2026-10-14 -- it began as Merging's Fill Edges dialog,
// 2026-10-13; core/removal/edge_fill.py, stages/edgefill/controller.py). ROSE refills the
// strip a deep 3D shift leaves at a frame's edge.
//
//   left    Folders (merged clips, their splat masks) and the fill's settings: Steps,
//           Resolution, Match Grain
//   right   the merged folder's clips as a grid of stills: each the eye with the strip,
//           at the frame it's widest once its mask is measured (remembered beside the
//           clips), a red line where it ends and its width under it. A click ticks;
//           a right-click fills just that clip or plays its files. Select All, and a
//           slider for the stills' size.
//   bottom  Fill / Stop and the job's progress
//
// The grid looks when the page shows, when a folder changes, when a fill ends and on Refresh
// -- only while the page shows, and never during another stage's job (a look reads every mask
// and still; bug sweep 2026-10-14): then it waits, and looks when that job ends. Its measuring
// stops when the page goes away or another job starts.
Item {
    id: page
    property bool active: false
    property var prompt: null
    property var consoleRef: null

    readonly property var ef: EdgeFill
    readonly property var st: ef.settings
    readonly property bool idle: !ef.busy

    property var info: ({})
    property string phase: "none"          // none | scanning | waiting | error | list
    property string waitingFor: ""          // the stage whose job a look waits for
    // another stage's job running (Main's Theme.calm is up for any, Edge Fill's own included)
    readonly property bool otherJob: Theme.calm && !ef.busy
    property var picked: []                 // names of the clips to fill
    property var measured: ({})             // name -> EdgeFill.measured's map
    readonly property var clips: info.clips || []

    readonly property int requiredWidth: 1000
    readonly property real requiredHeight: 620
    readonly property real preferredHeight: requiredHeight
    readonly property int leftWidth: 330

    function help(k) { return page.ef.help[k] || "" }
    function look() {
        if (!page.active || !page.idle) return
        if (page.otherJob) { page.waitingFor = ef.otherStage(); page.phase = "waiting"; return }
        page.phase = "scanning"
        page.ef.scan()
    }
    function toggle(name, on) {
        const p = picked.filter(n => n !== name)
        if (on) p.push(name)
        picked = p
    }
    function fill(only) {
        const names = only ? [only] : clips.map(c => c.name).filter(n => picked.indexOf(n) >= 0)   // in the grid's order
        if (names.length) page.ef.fill(names)
    }

    // the grid looks when the page shows (the project's chapters read again, as the stages do);
    // its measuring stops when it goes
    onActiveChanged: if (active) { ef.refreshChapters(); look() } else ef.stopLooking()
    // another stage's job: the measuring stops, and the grid looks again once it ends
    onOtherJobChanged: {
        if (otherJob) { ef.stopLooking(); waitingFor = ef.otherStage(); if (phase === "scanning") phase = "waiting" }
        else if (active) look()
    }
    // a fill ended (here or from Merging's Upcoming Jobs): the grid shows the filled clips as filled
    readonly property bool fillBusy: ef.busy
    // a tick later: look() starts a scan, whose stateChanged re-read ef.busy inside this very
    // change ("Binding loop detected for property fillBusy")
    onFillBusyChanged: if (!fillBusy && active) Qt.callLater(look)
    // a folder typed or picked: look again, once the typing stops
    Timer { id: relook; interval: 700; onTriggered: page.look() }
    Connections {
        target: page.st
        function onValueChanged(key, value) {
            if (page.active && (key === "merged_folder" || key === "mask_folder")) relook.restart()
        }
    }
    // ...and when they change from Python -- a chapter picked, Merging's button, a project switch
    // (the map's valueChanged only comes for QML's own writes; user, 2026-10-14)
    Connections {
        target: page.ef
        function onFoldersChanged() { if (page.active) relook.restart() }
    }
    Connections {
        target: page.ef
        function onScanned(result) {
            if (result.waiting) { page.waitingFor = result.waiting; page.phase = "waiting"; return }
            const before = page.info.folder
            page.info = result
            page.phase = result.error ? "error" : "list"
            // a new folder: its ready clips ticked; the same one again: the ticks kept (filled ones dropped)
            const ready = (result.clips || []).filter(c => c.state === "ready").map(c => c.name)
            page.picked = before === result.folder ? page.picked.filter(n => ready.indexOf(n) >= 0) : ready
            if (before !== result.folder) page.measured = ({})
        }
        function onMeasured(m) {
            const all = Object.assign({}, page.measured)
            all[m.name] = m
            page.measured = all
            if (m.px === 0) page.toggle(m.name, false)       // no strip: nothing to fill
        }
        function onMessage(kind, title, text) {
            page.prompt.ask({ title: title, message: text, buttons: [{ text: "OK", code: 1, kind: "filled" }] })
        }
        function onToast(kind, title, message) { Theme.toast(kind, title, message) }
    }

    FolderPicker {
        id: mergedDlg
        title: "Select the folder of merged clips"
        currentFolder: Shell.pathToUrl(page.st.merged_folder || "")
        onAccepted: page.st.merged_folder = Shell.urlToPath(selectedFolder).replace(/\\/g, "/")
    }
    FolderPicker {
        id: maskDlg
        title: "Select the folder of splat masks"
        currentFolder: Shell.pathToUrl(page.st.mask_folder || "")
        onAccepted: page.st.mask_folder = Shell.urlToPath(selectedFolder).replace(/\\/g, "/")
    }

    // ---- left: folders and settings ----------------------------------------------------------
    ColumnLayout {
        id: left
        objectName: "edgeFillLeft"
        x: 18
        y: 6
        width: page.leftWidth
        spacing: Theme.gap
        // idle-only per section, not on the whole column: Open Console stays live during a fill

        Section {
            id: foldersSection
            title: "Folders"
            enabled: page.idle
            // a chapter of the project: its merged clips and their splats (user, 2026-10-14)
            headerRight: [
                ChapterPicker {
                    objectName: "edgeFillChapter"
                    visible: page.ef.hasProject
                    dc: page.ef
                    Layout.maximumWidth: 190
                }
            ]
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2
                Text { text: "Merged Folder"; color: Theme.fg; font.family: Theme.bodyFont; font.pixelSize: Theme.bodySize
                       HoverTip { tip: page.help("merged_folder") } }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    TextBox { objectName: "edgeFillMerged"; store: page.st; owner: page.ef; key: "merged_folder"; numeric: false; Layout.fillWidth: true }
                    AccentButton { text: "Browse..."; kind: "outline"; onClicked: mergedDlg.open() }
                }
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2
                Text { text: "Mask Folder"; color: Theme.fg; font.family: Theme.bodyFont; font.pixelSize: Theme.bodySize
                       HoverTip { tip: page.help("mask_folder") } }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    TextBox { objectName: "edgeFillMasks"; store: page.st; owner: page.ef; key: "mask_folder"; numeric: false; Layout.fillWidth: true }
                    AccentButton { text: "Browse..."; kind: "outline"; onClicked: maskDlg.open() }
                }
            }
        }

        Section {
            title: "Fill Settings"
            enabled: page.idle
            CheckField {
                objectName: "fillEdgesBothEdges"
                store: page.st
                key: "both_edges"
                text: "Right Edge Too"
                help: page.help("both_edges")
            }
            FieldRow {
                label: "Steps"; labelWidth: 110
                help: page.help("steps")
                TextBox { objectName: "fillEdgesSteps"; store: page.st; owner: page.ef; key: "steps"; boxWidth: 80 }
            }
            FieldRow {
                label: "Resolution"; labelWidth: 110
                help: page.help("size")
                Dropdown {
                    objectName: "fillEdgesSize"
                    store: page.st; key: "size"
                    model: page.ef.sizes
                    boxWidth: 190
                }
            }
            CheckField {
                objectName: "fillEdgesGrain"
                store: page.st
                key: "grain"
                text: "Match Grain"
                help: page.help("grain")
            }
            CheckField {
                objectName: "fillEdgesDetail"
                store: page.st
                key: "detail"
                text: "Match Detail"
                help: page.help("detail")
            }
        }

        // under Fill Settings (user, 2026-10-18): the console's Extra tab, its bars live
        AccentButton {
            objectName: "openConsole"
            visible: page.consoleRef !== null
            Layout.alignment: Qt.AlignRight
            text: "Open Console"
            kind: "ghost"
            iconGlyph: Theme.icon.console
            implicitHeight: 26
            onClicked: page.consoleRef.open = true
            HoverTip { tip: "The console window, on its Extra tab: each clip's fill as it goes, with its progress bars." }
        }

        Text {
            objectName: "fillEdgesNoRose"
            visible: page.phase === "list" && !page.info.rose
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            color: Theme.danger
            font.family: Theme.bodyFont; font.pixelSize: Theme.bodySize
            text: "ROSE isn't set up yet: Depth Map Editor > Erase > Set Up ROSE first."
        }
    }

    // ---- right: the clips --------------------------------------------------------------------
    Section {
        id: clipsSection
        objectName: "edgeFillClips"
        title: "Clips"
        x: left.x + left.width + Theme.gap
        y: left.y
        width: page.width - x - 18
        height: actionBar.y - Theme.gap - y
        Layout.fillWidth: false

        headerRight: [
            AccentButton {
                objectName: "edgeFillRefresh"
                text: "Refresh"; kind: "ghost"
                enabled: page.idle && page.phase !== "scanning"
                onClicked: page.look()
                HoverTip { tip: "Look through the folders again." }
            }
        ]

        // Select All / Deselect All, then the stills' size (kept between looks)
        RowLayout {
            id: toolbar
            visible: page.phase === "list"
            Layout.fillWidth: true
            // never wider than the section: its texts elide and the slider narrows first (user,
            // 2026-10-14: at 150% the row's full width pushed the grid past the section's edge)
            Layout.maximumWidth: clipsSection.width - 28
            Layout.minimumWidth: 0
            spacing: 8
            AccentButton {
                objectName: "fillEdgesSelectAll"
                // every clip that can be filled and has a strip (one measured at 0 px has nothing to fill)
                readonly property var pickable: page.clips.filter(c => c.state !== "no"
                    && !(page.measured[c.name] && page.measured[c.name].px === 0)).map(c => c.name)
                readonly property bool all: pickable.length > 0 && pickable.every(n => page.picked.indexOf(n) >= 0)
                text: all ? "Deselect All" : "Select All"
                kind: "outline"
                enabled: pickable.length > 0 && page.idle
                onClicked: page.picked = all ? [] : pickable.slice()
                HoverTip { tip: "Tick every clip with a strip to fill (clips already filled too), or untick them all." }
            }
            Text {
                objectName: "edgeFillPickedCount"
                text: page.picked.length + " of " + page.clips.length + " ticked"
                color: Theme.fgMuted
                font.family: Theme.bodyFont; font.pixelSize: Theme.smallSize
                elide: Text.ElideRight
                Layout.minimumWidth: 0
                Layout.preferredWidth: implicitWidth
                Layout.fillWidth: true
                Layout.maximumWidth: implicitWidth
            }
            Item { Layout.fillWidth: true; Layout.minimumWidth: 0 }
            Text {
                text: "Thumbnails"
                color: Theme.fgMuted
                font.family: Theme.bodyFont; font.pixelSize: Theme.smallSize
                elide: Text.ElideRight
                Layout.minimumWidth: 0
                Layout.preferredWidth: implicitWidth
                Layout.fillWidth: true
                Layout.maximumWidth: implicitWidth
            }
            AccentButton {
                objectName: "fillEdgesSmaller"
                text: "−"; kind: "ghost"
                enabled: tileSize.value > tileSize.from
                onClicked: tileSize.set(tileSize.value - tileSize.stepSize)
                HoverTip { tip: "Smaller thumbnails: more to a row." }
            }
            Slider {
                id: tileSize
                objectName: "fillEdgesTileSize"
                Layout.preferredWidth: 160
                Layout.minimumWidth: 60
                Layout.fillWidth: true
                Layout.maximumWidth: 160
                from: 120; to: 420; stepSize: 20
                snapMode: Slider.SnapAlways
                focusPolicy: Qt.NoFocus
                value: page.st ? (page.st["tile"] || 200) : 200
                function set(v) {
                    v = Math.max(from, Math.min(to, v))   // the setting moves the slider (its binding)
                    if (page.st) page.st["tile"] = v
                }
                onMoved: set(value)
                background: Rectangle {
                    x: tileSize.leftPadding
                    y: tileSize.topPadding + tileSize.availableHeight / 2 - 2
                    width: tileSize.availableWidth
                    height: 4
                    radius: 2
                    color: Theme.alpha(Theme.fg, 0.15)
                    Rectangle { width: tileSize.visualPosition * parent.width; height: 4; radius: 2; color: Theme.accent }
                }
                handle: Rectangle {
                    x: tileSize.leftPadding + tileSize.visualPosition * (tileSize.availableWidth - width)
                    y: tileSize.topPadding + tileSize.availableHeight / 2 - height / 2
                    width: 10; height: 20; radius: 4
                    color: Theme.field
                    border.width: 2
                    border.color: Theme.accent
                }
                HoverTip { tip: "How big the thumbnails are: drag right for bigger, fewer to a row." }
            }
            AccentButton {
                objectName: "fillEdgesBigger"
                text: "+"; kind: "ghost"
                enabled: tileSize.value < tileSize.to
                onClicked: tileSize.set(tileSize.value + tileSize.stepSize)
                HoverTip { tip: "Bigger thumbnails: fewer to a row." }
            }
            Text {
                objectName: "fillEdgesPerRow"
                text: grid.cols + " to a row"
                color: Theme.fgMuted
                font.family: Theme.bodyFont; font.pixelSize: Theme.smallSize
                elide: Text.ElideRight
                Layout.minimumWidth: 0
                Layout.preferredWidth: implicitWidth
                Layout.fillWidth: true
                Layout.maximumWidth: implicitWidth
            }
        }

        // what it can and can't rebuild (user, 2026-10-18): over the grid, where there's width for it
        Text {
            id: suits
            objectName: "fillEdgesSuits"
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            color: Theme.fgMuted
            font.family: Theme.bodyFont; font.pixelSize: Theme.smallSize
            text: "Best on simple backgrounds with little movement: walls, sky and plain objects (a house, a bench, "
                  + "a cabinet) carry on well. It can't rebuild fine detail -- faces or text in the strip stay rough."
        }

        // looking / can't
        RowLayout {
            visible: page.phase === "scanning"
            spacing: 12
            Spinner { running: page.active && page.phase === "scanning"; Layout.preferredWidth: 22; Layout.preferredHeight: 22 }
            Text { text: "Looking through the merged folder for clips and their splat masks..."
                   color: Theme.fg; font.family: Theme.bodyFont; font.pixelSize: Theme.bodySize }
        }
        Text {
            objectName: "edgeFillWaiting"
            visible: page.phase === "waiting" || (page.phase === "list" && page.otherJob)
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            color: Theme.fgMuted
            font.family: Theme.bodyFont; font.pixelSize: Theme.bodySize
            text: (page.waitingFor ? page.waitingFor + "'s" : "Another stage's") + " job is running: the clips are "
                  + "looked at (their masks read, their stills made) once it ends, so it isn't slowed."
        }
        Text {
            objectName: "edgeFillError"
            visible: page.phase === "error" || page.phase === "none"
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            color: Theme.fgMuted
            font.family: Theme.bodyFont; font.pixelSize: Theme.bodySize
            text: page.phase === "error" ? (page.info.error || "")
                  : "Pick the folder of merged clips and the folder of their splat masks, on the left."
        }

        Flickable {
            id: list
            objectName: "fillEdgesList"
            visible: page.phase === "list"
            Layout.fillWidth: true
            Layout.preferredHeight: Math.max(160, clipsSection.height - 2 * Theme.sectionPad - 46 - toolbar.height - 8
                                                - suits.height - 8)
            contentHeight: grid.implicitHeight
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: SlimScrollBar {}
            Flow {
                id: grid
                objectName: "fillEdgesGrid"
                width: Math.min(list.width, clipsSection.width - 28) - 10
                spacing: 10
                // as many tiles of the slider's size as fit, stretched to fill the row
                readonly property int cols: Math.max(1, Math.floor((width + spacing) / (tileSize.value + spacing)))
                readonly property real tileW: (width - spacing * (cols - 1)) / cols
                Repeater {
                    model: page.phase === "list" ? page.clips : []
                    delegate: Rectangle {
                        id: tile
                        objectName: "fillEdgesTile"
                        required property var modelData
                        readonly property bool can: modelData.state !== "no"
                        readonly property bool ticked: page.picked.indexOf(modelData.name) >= 0
                        // filled (its record says so), or by this run before the grid looks again: a light
                        // green, as the clip lists show a finished row (user, 2026-10-18)
                        readonly property bool finished: modelData.state === "done"
                                                         || page.ef.filledNow.indexOf(modelData.name) >= 0
                        readonly property var m: page.measured[modelData.name] || null
                        // the eye with the strip, at its widest frame once measured
                        readonly property string thumb: m && m.thumb ? m.thumb : (modelData.thumb || "")
                        readonly property string part: m && m.thumb ? m.part : (modelData.part || "whole")
                        readonly property int frame: m && m.thumb ? m.frame : -1
                        // the file's version: a filled clip's still is asked for again (Qt caches by URL)
                        readonly property string ver: String(m && m.thumb ? (m.ver || "0") : (modelData.ver || "0"))
                        width: grid.tileW
                        height: col.implicitHeight + 12
                        radius: 8
                        color: finished ? Theme.alpha(Theme.success, tileHover.hovered ? 0.22 : 0.16)
                             : Theme.alpha(Theme.fg, tileHover.hovered && can ? 0.09 : 0.05)
                        border.width: ticked ? 2 : 1
                        border.color: ticked ? Theme.accent : Theme.alpha(Theme.fg, 0.08)
                        opacity: can ? 1 : Theme.dim(0.55)

                        ColumnLayout {
                            id: col
                            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                            anchors.margins: 6
                            spacing: 4
                            Rectangle {
                                Layout.fillWidth: true
                                Layout.preferredHeight: width * 0.54
                                radius: 4
                                color: "#000000"
                                clip: true
                                Image {
                                    id: still
                                    objectName: "fillEdgesThumb"
                                    anchors.fill: parent
                                    asynchronous: true       // Qt's loader thread, never the UI thread
                                    fillMode: Image.PreserveAspectFit
                                    sourceSize.height: 324
                                    source: tile.thumb ? "image://edgethumb/" + tile.frame + "/" + tile.part + "/"
                                                         + tile.ver + "/" + encodeURIComponent(tile.thumb) : ""
                                    opacity: status === Image.Ready ? 1 : 0
                                }
                                // where the strip ends: a thin line over the still
                                Rectangle {
                                    objectName: "fillEdgesStripLine"
                                    visible: !!tile.m && tile.m.px > 0 && still.status === Image.Ready
                                    readonly property real px: visible ? Math.max(1, tile.m.px / Math.max(1, tile.m.eyeWidth) * still.paintedWidth) : 0
                                    readonly property real x0: (still.width - still.paintedWidth) / 2
                                    x: tile.m && tile.m.edge === "right" ? x0 + still.paintedWidth - px : x0 + px
                                    y: (still.height - still.paintedHeight) / 2
                                    width: 1
                                    height: still.paintedHeight
                                    color: Theme.danger
                                    opacity: 0.85
                                }
                                AnimCheck {
                                    objectName: "fillEdgesPick"
                                    anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 4
                                    enabled: tile.can && page.idle
                                    visible: tile.can
                                    checked: tile.ticked
                                    onToggled: page.toggle(tile.modelData.name, checked)
                                }
                            }
                            Text {
                                text: tile.modelData.name
                                color: tile.can ? Theme.fg : Theme.fgFaint
                                font.family: Theme.bodyFont; font.pixelSize: Theme.smallSize + 1; font.weight: Font.DemiBold
                                elide: Text.ElideMiddle
                                Layout.fillWidth: true
                            }
                            Text {
                                objectName: "fillEdgesStrip"
                                text: {
                                    const md = tile.modelData, m = tile.m
                                    if (md.state === "no") return md.note
                                    const pre = md.state === "done" ? "Filled  ·  " : ""
                                    if (!m) return pre + (page.otherJob ? "Waiting to measure the strip" : "Measuring the strip...")
                                    if (m.px < 0) return pre + "Couldn't measure the strip"
                                    if (m.px === 0) return pre + "No strip"
                                    return pre + m.px + " px strip  ·  " + (m.eye || (tile.part === "left" ? "L" : "R"))
                                           + " eye, " + m.edge + " edge"
                                }
                                color: tile.m && tile.m.px > 0 && tile.modelData.state === "ready" ? Theme.danger : Theme.fgMuted
                                font.family: Theme.bodyFont; font.pixelSize: Theme.smallSize
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                        }
                        HoverHandler { id: tileHover; cursorShape: tile.can ? Qt.PointingHandCursor : Qt.ArrowCursor }
                        TapHandler {
                            enabled: tile.can && page.idle
                            onTapped: page.toggle(tile.modelData.name, !tile.ticked)
                        }
                        TapHandler {
                            objectName: "fillEdgesTileMenuArea"
                            acceptedButtons: Qt.RightButton
                            onTapped: {
                                tileMenu.name = tile.modelData.name
                                tileMenu.can = tile.can
                                // the clip's files to play (user, 2026-10-14: see the whole clip, bar and
                                // all): the one the still shows -- the eye with the bar -- first
                                const folder = page.info.folder || ""
                                const shown = tile.thumb
                                const rest = tile.modelData.files.map(f => folder + "/" + f).filter(p => p !== shown)
                                tileMenu.plays = (shown ? [{ path: shown, text: "Play " + shown.split("/").pop() + "  (the eye with the bar)" }] : [])
                                        .concat(rest.map(p => ({ path: p, text: "Play " + p.split("/").pop() })))
                                // the merged clip and its hi-res splat, selected in Explorer (user, 2026-10-18)
                                const merged = shown || (tile.modelData.files.length ? folder + "/" + tile.modelData.files[0] : "")
                                tileMenu.explores = (merged ? [{ path: merged, text: "Open Merged Clip in Explorer" }] : [])
                                        .concat(tile.modelData.mask ? [{ path: tile.modelData.mask, text: "Open Hi-Res Splat in Explorer" }] : [])
                                tileMenu.popup()
                            }
                        }
                        HoverTip {
                            tip: tile.modelData.name + "\n" + tile.modelData.files.join("\n")
                                 + (tile.can ? "\nClick to tick or untick. Right-click to fill just this clip, play it or open it in Explorer."
                                             : "\nRight-click to play it or open it in Explorer.")
                        }
                    }
                }
            }
        }
    }
    StudioMenu {
        id: tileMenu
        objectName: "fillEdgesTileMenu"
        property string name: ""
        property bool can: false
        property var plays: []
        property var explores: []
        MenuItem {
            objectName: "fillEdgesJustThis"
            text: "Fill Just This Clip"
            enabled: tileMenu.can && !!page.info.rose && page.idle
            onTriggered: page.fill(tileMenu.name)
        }
        MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: Theme.border } }
        // in the default player, as Upcoming Jobs' "Play ..."
        Instantiator {
            model: tileMenu.plays
            delegate: MenuItem {
                required property var modelData
                objectName: "fillEdgesPlay"
                text: modelData.text
                onTriggered: {
                    const why = Shell.openFile(modelData.path)
                    if (why) Theme.toast("warning", "Couldn't open it", why)
                }
                HoverTip { tip: modelData.path.replace(/\//g, "\\") }
            }
            onObjectAdded: (i, item) => tileMenu.insertItem(2 + i, item)
            onObjectRemoved: (i, item) => tileMenu.removeItem(item)
        }
        MenuSeparator {
            contentItem: Rectangle { implicitHeight: tileMenu.explores.length ? 1 : 0; color: Theme.border }
            padding: tileMenu.explores.length ? 6 : 0
        }
        // last in the menu: appended after the separator above
        Instantiator {
            model: tileMenu.explores
            delegate: MenuItem {
                required property var modelData
                objectName: "fillEdgesExplore"
                text: modelData.text
                onTriggered: {
                    const why = Shell.revealFile(modelData.path)
                    if (why) Theme.toast("warning", "Couldn't open Explorer", why)
                }
                HoverTip { tip: modelData.path.replace(/\//g, "\\") }
            }
            onObjectAdded: (i, item) => tileMenu.addItem(item)
            onObjectRemoved: (i, item) => tileMenu.removeItem(item)
        }
    }

    // ---- bottom: Fill / Stop and the job ------------------------------------------------------
    ActionBar {
        id: actionBar
        objectName: "actionBar"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: 12
        title: "Fill"
        busy: page.ef.busy
        done: Math.round(page.ef.progress * 100)
        total: 100
        status: page.ef.busy && page.ef.clipCount ? page.ef.clipCount + "  ·  " + page.ef.status : page.ef.status

        AccentButton {
            objectName: "fillEdgesGo"
            text: page.picked.length === 1 ? "Fill 1 Clip" : "Fill " + page.picked.length + " Clips"
            iconGlyph: Theme.icon.play
            enabled: page.idle && !page.otherJob && page.phase === "list" && page.picked.length > 0 && !!page.info.rose
            onClicked: page.fill("")
            HoverTip { tip: page.help("fill") }
        }
        AccentButton {
            objectName: "fillEdgesStop"
            text: "Stop"; kind: "outline"
            enabled: page.ef.busy
            onClicked: page.ef.stop()
            HoverTip { tip: "Stops now: the clip being filled is left as it was; clips already filled stay filled." }
        }
    }

    readonly property var stageMenus: []

    // drag and drop (user, 2026-10-25): Drop Zones over the folders
    DropZones {
        over: foldersSection
        store: page.st
        targets: [{ key: "merged_folder", label: "Merged Clips", folder: true },
                  { key: "mask_folder", label: "Mask Folder", folder: true }]
    }
}
