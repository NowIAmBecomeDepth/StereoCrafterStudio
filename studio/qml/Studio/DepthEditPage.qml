import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Layouts
import Studio
import "glasses.js" as Glasses

// The Depth Map Editor, v2 (Extra tab). v1's arrangement
// (depth_editor_gui._create_widgets):
//
//   Clip       Source video [..] Depth map [..] [Load Clip] [Pick From Last DepthCrafter Job]
//   View [RGB|Depth|Edited] Masks  Colour depth        Tool [Add|Remove|Box|Gradient] | Layers
//   the picture                                                                        | [Start Tracking][Stop] ===
//   [<] frame slider [>] Frame n / N                                                   | [Segment|Layer|Gradient|Global|Output]
//   status                                                                             | the page
//
// Everything it does is v1's (stages/depthedit/engine.py, generated); the
// picture and its markers are drawn here from what the controller hands over.
Item {
    id: page
    property bool active: false
    property var prompt: null

    readonly property var de: DepthEdit
    readonly property var s: de.state
    readonly property var ctl: s.controls || ({})
    readonly property var ch: de.choices
    readonly property bool ready: !!s.ready
    readonly property bool working: !!s.busy
    // Start Here's direction (user, 2026-10-05): back over the previous frames
    readonly property bool trackBack: de.viewOptions.trackDir === "backward"

    readonly property int requiredWidth: 1220
    // The rail at its tallest page, as v1's is sized: switching pages must
    // not resize the window.
    readonly property real railHeight: layersSection.implicitHeight + pageStrip.implicitHeight + 2 * rail.spacing
        + Math.max(pSegment.implicitHeight, pLayer.implicitHeight, pGradient.implicitHeight, pGlobal.implicitHeight,
                   pOutput.implicitHeight)
    // The rail scrolls when it doesn't fit, so the page needs only the picture's room; it
    // still asks for the whole rail where the screen has it (user, 2026-10-08).
    readonly property real requiredHeight: clipSection.implicitHeight + 6 + Theme.gap + 420 + 16
    readonly property real preferredHeight: clipSection.implicitHeight + 6 + Theme.gap
                                            + Math.max(railHeight, 420) + 16
    // SAM 3 set up (packages there, the model's weights downloaded): its page
    // section goes, and its settings live in Settings (user, 2026-09-28)
    readonly property bool samInstalled: page.s.install_btn === false && page.s.weights_btn === false
    // SAM 3's download, run by Settings > Studio > Models (user, 2026-10-21): on = running or queued
    readonly property var samDl: {
        const d = typeof Models !== "undefined" && Models ? Models.download : ({})
        const running = d.key === "sam3"
        const queued = (d.queued || []).indexOf("sam3") >= 0
        const err = d.error && d.error.key === "sam3" ? d.error.text : ""
        return { on: running || queued, running: running, progress: d.progress || 0, text: d.text || "", error: err }
    }
    readonly property var stageMenus: [samMenu]
    StudioMenu {
        id: samMenu
        title: "SAM 3"
        MenuItem {
            text: "Keep Video Frames in System RAM"
            property string tip: page.help("offload_frames") || "Holds the clip's frames, and the memory SAM 3 builds up as it tracks, in system RAM instead of on the GPU: saves VRAM, a little slower."
            checkable: true
            checked: !!page.s.offload
            onTriggered: page.de.setVar("offload", checked)
        }
        MenuItem {
            text: "Unload SAM 3 Model"
            property string tip: "Releases the model's GPU memory now (about 4 GB); it loads again on next use."
            enabled: !!page.s.model_btn
            onTriggered: page.de.run("_toggle_model")
        }
        MenuItem {
            text: "Uninstall SAM 3..."
            property string tip: "Moves SAM 3's model weights (several GB, weights/SAM3) to the Recycle Bin. "
                                 + "Its small Python packages stay. The Segment page then offers the setup again."
            enabled: page.samInstalled && !page.working
            onTriggered: page.prompt.ask({
                title: "Uninstall SAM 3?",
                message: "SAM 3's model weights (several GB) go to the Recycle Bin, so you can restore them from there.\n\n"
                         + "Its Python packages stay installed. To use SAM 3 again, Download Weights on the Segment page.",
                buttons: [{ text: "Uninstall", code: 1, kind: "filled" }, { text: "Cancel", code: 0, kind: "ghost" }],
                onDone: (code) => { if (code === 1) page.de.uninstallSam() }
            })
        }
    }

    function help(k) { return page.de.help[k] || "" }
    onActiveChanged: { if (active) de.ensureLoaded(); else shapeDraft = [] }   // a shape half drawn goes

    // ---- v1's dialogs ---------------------------------------------------------
    FileDialog {
        id: rgbDlg; title: "Source video"
        currentFolder: Shell.pathToUrl(page.s.rgb_path || "")
        nameFilters: ["Video (*.mp4 *.mkv *.mov *.avi *.m4v *.webm)", "All files (*)"]
        onAccepted: page.de.setVar("rgb_path", Shell.urlToPath(selectedFile).replace(/\\/g, "/"))
    }
    FileDialog {
        id: depthDlg; title: "Depth map"
        currentFolder: Shell.pathToUrl(page.s.depth_path || "")
        nameFilters: ["Depth video (*_depth.mp4 *.mp4 *.mkv)", "All files (*)"]
        onAccepted: page.de.setVar("depth_path", Shell.urlToPath(selectedFile).replace(/\\/g, "/"))
    }
    FolderPicker {
        id: outDlg; title: "Output folder"
        currentFolder: Shell.pathToUrl(page.s.output_dir || "")
        onAccepted: page.de.setVar("output_dir", Shell.urlToPath(selectedFolder).replace(/\\/g, "/"))
    }
    FileDialog {
        id: pickDlg
        nameFilters: ["Video (*.mp4 *.mkv *.mov *.avi *.m4v *.webm)"]
        onAccepted: page.de.setVar("rgb_path", Shell.urlToPath(selectedFile).replace(/\\/g, "/"))
    }
    Connections {
        target: page.de
        function onMessage(kind, title, text) {
            page.prompt.ask({ title: title, message: text, buttons: [{ text: "OK", code: 1, kind: "filled" }] })
        }
        function onQuestion(title, text) {
            page.prompt.ask({ title: title, message: text,
                              buttons: [{ text: "Yes", code: 1, kind: "filled" }, { text: "No", code: 0, kind: "ghost" }],
                              onDone: (code) => page.de.answer(code === 1) })
        }
        function onPickFile(title, folder) {
            pickDlg.title = title
            pickDlg.currentFolder = Shell.pathToUrl(folder)
            pickDlg.open()
        }
        // A render of the clip Splatting sent here: offer the way back (user,
        // 2026-09-28). The edited map takes the original's place (what it
        // replaces is kept in edit_history), and Splatting reloads that clip.
        function onRendered(rgb, written) {
            const from = String(page.de.fromSplatting || "")
            const norm = (p) => String(p).replace(/\\/g, "/").toLowerCase()
            if (from === "" || norm(from) !== norm(rgb)) return
            page.prompt.ask({
                title: "Back to Splatting?",
                message: "The edited depth map is rendered.\n\nUse it in Splatting now? It takes the original map's "
                         + "place, so the preview and the batch both read it; the original and earlier edits "
                         + "are kept in the depth maps' edit_history folder.",
                buttons: [{ text: "Use in Splatting", code: 1, kind: "filled" }, { text: "Stay Here", code: 0, kind: "ghost" }],
                onDone: (code) => {
                    if (code !== 1) return
                    const live = page.de.useInSplatting(written)
                    if (live === "") return
                    Shell.openStage(1, -1)
                    // afresh, AT this clip: its frames of the old map are cached
                    Splatting.previewer.reloadClip(from)
                }
            })
        }
    }

    // ---- v1's keys: arrows step frames while the picture has the pointer --------
    readonly property bool keysLive: active && !!s.hasClip && Window.window === Shell.focusWindow
                                     && pictureHover.hovered
    Shortcut { enabled: page.keysLive; sequence: "Left"; onActivated: page.de.step(-1) }
    Shortcut { enabled: page.keysLive; sequence: "Right"; onActivated: page.de.step(1) }
    // keyframes for the selected layer (user, 2026-10-05: as Splatting's S / D);
    // anywhere on the page, but not while typing in a box
    readonly property bool keyKeysLive: active && !!s.hasLayer && Window.window === Shell.focusWindow
                                        && !(Window.activeFocusItem && Window.activeFocusItem.hasOwnProperty("cursorPosition"))
    Shortcut { objectName: "deKeyStatic"; enabled: page.keyKeysLive; sequence: "S"; onActivated: page.de.addKeyframe("static") }
    Shortcut { objectName: "deKeyDynamic"; enabled: page.keyKeysLive; sequence: "D"; onActivated: page.de.addKeyframe("dynamic") }
    // Undo / Redo (user, 2026-10-05): anywhere on the page but in a text box,
    // which keeps its own Ctrl+Z for what's typed in it
    readonly property bool undoKeysLive: active && !!s.hasClip && Window.window === Shell.focusWindow
                                         && !(Window.activeFocusItem && Window.activeFocusItem.hasOwnProperty("cursorPosition"))
    Shortcut { objectName: "deUndoKey"; enabled: page.undoKeysLive; sequence: "Ctrl+Z"; onActivated: page.de.undo() }
    Shortcut { objectName: "deRedoKey"; enabled: page.undoKeysLive; sequences: ["Ctrl+Y", "Ctrl+Shift+Z"]; onActivated: page.de.redo() }
    // Picked SAM 3 points (user, 2026-10-13): a click on a point picks it (Shift or Ctrl+click
    // adds to the pick), Delete takes them out and SAM 3 runs again without them. Their
    // places in the layer's clicks ("i"); any change to the frame's points drops the pick.
    property var selPoints: []
    readonly property string pointsKey: JSON.stringify(de.markers.points || [])
    onPointsKeyChanged: selPoints = []
    Shortcut {
        objectName: "deDeletePointsKey"
        enabled: page.undoKeysLive && page.selPoints.length > 0 && !page.working
        sequences: [StandardKey.Delete, "Backspace"]
        onActivated: { const pick = page.selPoints; page.selPoints = []; page.de.deletePoints(pick) }
    }
    Shortcut { enabled: page.undoKeysLive && page.selPoints.length > 0; sequence: "Escape"; onActivated: page.selPoints = [] }
    // Shapes (user, 2026-10-17): the polygon being drawn -- its points, frame-relative -- and whether it
    // cuts (Ctrl held on its first point). Enter closes it, Backspace takes its last point back, Esc drops it.
    property var shapeDraft: []
    property bool shapeCut: false
    readonly property bool shapeKeysLive: page.undoKeysLive && page.shapeDraft.length > 0 && !page.working
    function closeShape() {
        const pts = page.shapeDraft
        page.shapeDraft = []
        if (pts.length >= 3) page.de.addShape("Points", pts, page.shapeCut)
    }
    // another tool, kind or layer: the shape half drawn goes
    readonly property string shapeContext: (s.tool || "") + "|" + (s.shapeKind || "") + "|" + (s.selected !== undefined ? s.selected : "")
    onShapeContextChanged: shapeDraft = []
    Shortcut { objectName: "deShapeClose"; enabled: page.shapeKeysLive; sequences: ["Return", "Enter"]; onActivated: page.closeShape() }
    Shortcut { objectName: "deShapeBack"; enabled: page.shapeKeysLive; sequence: "Backspace"
               onActivated: page.shapeDraft = page.shapeDraft.slice(0, -1) }
    Shortcut { objectName: "deShapeCancel"; enabled: page.shapeKeysLive; sequence: "Escape"; onActivated: page.shapeDraft = [] }

    component Caption: Text {
        color: parent && parent.enabled === false ? Theme.fgFaint : Theme.fg
        font.family: Theme.bodyFont
        font.pixelSize: Theme.bodySize
        Layout.alignment: Qt.AlignVCenter
    }
    component Muted: Caption { color: Theme.fgMuted; wrapMode: Text.Wrap; Layout.fillWidth: true }
    component Field: TextField {
        id: fld
        property string varName
        implicitHeight: Theme.rowHeight
        text: page.s[varName] !== undefined ? String(page.s[varName]) : ""
        color: Theme.fg
        font.family: Theme.bodyFont
        font.pixelSize: Theme.bodySize
        background: Rectangle { radius: 6; color: Theme.field; border.width: 1; border.color: fld.activeFocus ? Theme.accent : Theme.border }
        onEditingFinished: if (text !== String(page.s[varName] || "")) page.de.setVar(varName, text)
    }
    component VarCheck: AnimCheck {
        property string varName
        property string tip: ""
        checked: !!page.s[varName]
        onToggled: page.de.setVar(varName, checked)
        HoverTip { tip: parent.tip }
    }
    component Seg: SegmentedControl {
        property string varName
        property var values: []
        compact: true
        controlled: true
        model: values
        currentIndex: values.indexOf(page.s[varName])
        onActivated: (i) => page.de.setVar(varName, values[i])
    }
    // An edit control, as a dial (user, 2026-10-01: as in Splatting and
    // Merging). The controller hands it its range in the units shown -- video
    // pixels, in half-pixel steps, for the edge and size settings -- and its
    // readout for the middle. Right-click or double-click resets it to the
    // value a new layer / frame starts with. `proxy` carries the dial's writes
    // to the controller and the controller's value back.
    component Ctl: DialField {
        id: row
        property string ctlKey
        readonly property var c: page.ctl[ctlKey] || ({})
        readonly property real want: c.value !== undefined ? Number(c.value) : 0
        property bool syncing: false
        property QtObject proxy: QtObject { property real v: 0 }
        objectName: "ctl_" + ctlKey
        enabled: page.s.hasLayer || ctlKey.indexOf("global.") === 0
        store: proxy
        key: "v"
        label: c.label || ""
        help: c.help || ""
        from: c.from !== undefined ? c.from : 0
        to: c.to !== undefined ? c.to : 1
        step: c.step || 0.01
        decimals: c.decimals !== undefined ? c.decimals : 2
        defaultValue: c["default"] !== undefined ? Number(c["default"]) : NaN
        valueText: c.readout || ""
        size: 50
        valueSize: 10
        captionLines: 2
        function pull() { syncing = true; proxy.v = want; syncing = false }
        onWantChanged: pull()
        Component.onCompleted: pull()
        Connections {
            target: row.proxy
            function onVChanged() {
                if (!row.syncing && Math.abs(row.proxy.v - row.want) > 1e-9)
                    page.de.setControl(row.ctlKey, row.decimals === 0 && row.step >= 1 ? Math.round(row.proxy.v) : row.proxy.v)
            }
        }
    }
    // A section's dials, wrapping to the width
    component CtlFlow: Flow {
        property var keys: []
        Layout.fillWidth: true
        spacing: 2
        // five across the rail's 392 px (user, 2026-10-05: Left / Right edge
        // beside Top / Bottom kept the page within a 1080p screen at 0.85)
        Repeater { model: parent.keys; Ctl { required property var modelData; ctlKey: modelData; width: 76 } }
    }
    component CtlCheck: AnimCheck {
        property string key
        property string tip: ""
        enabled: page.s.hasLayer || key.indexOf("global.") === 0
        checked: !!(page.ctl[key] || {}).value
        onToggled: page.de.setControl(key, checked)
        HoverTip { tip: parent.tip }
    }
    component Btn: AccentButton {
        property string op
        property string tip: ""
        kind: "outline"
        onClicked: page.de.run(op)
        HoverTip { tip: parent.tip }
    }

    // ---- Clip -------------------------------------------------------------------
    Section {
        id: clipSection
        x: 14; y: 6
        width: parent.width - 28
        title: "Clip"
        enabled: page.ready && !page.working
        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Caption { text: "Source video:" }
            Field { varName: "rgb_path"; Layout.fillWidth: true; HoverTip { tip: page.help("rgb_path") } }
            AccentButton { text: "Browse..."; kind: "outline"; onClicked: rgbDlg.open() }
            Caption { text: "Depth map:"; Layout.leftMargin: 8 }
            Field { varName: "depth_path"; Layout.fillWidth: true; HoverTip { tip: page.help("depth_path") } }
            AccentButton { text: "Browse..."; kind: "outline"; onClicked: depthDlg.open() }
            Btn { objectName: "deLoad"; op: "_load_clip"; text: "Load Clip"; kind: "filled"; tip: page.help("load_clip"); Layout.leftMargin: 6 }
            // the loaded map again at the source's size, layers kept (user, 2026-10-05)
            AccentButton {
                objectName: "deUpscale"
                text: page.s.upscaled ? "Map Upscaled" : "Upscale Map"
                kind: "outline"
                enabled: !!page.s.hasClip && !page.s.upscaled && !!page.s.rgb_path
                onClicked: page.de.upscaleMap()
                HoverTip { tip: page.help("upscale_map") }
            }
            Btn { op: "_pick_from_depthcrafter"; text: "Pick From Last DepthCrafter Job"; tip: page.help("import_depthcrafter") }
        }
    }

    // ---- the picture ---------------------------------------------------------------
    ColumnLayout {
        id: left
        x: 14
        y: clipSection.y + clipSection.height + Theme.gap
        width: railScroll.x - x - Theme.gap
        height: parent.height - y - 16
        spacing: 4
        enabled: page.ready

        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Caption { text: "View" }
            Seg { varName: "view"; values: page.ch.view || ["RGB", "Depth", "Edited"]; HoverTip { tip: page.help("view") } }
            VarCheck { varName: "overlay"; text: "Masks"; tip: page.help("overlay"); Layout.leftMargin: 8 }
            Item { Layout.fillWidth: true }
            Caption { text: "Tool" }
            Seg { objectName: "deTool"; varName: "tool"; values: page.ch.tool || ["Add", "Remove", "Box", "Gradient", "Shape"]
                  HoverTip { tip: page.help("tool") } }
        }
        // Shapes (user, 2026-10-17): the kind and Clear Shapes, a row of their own while the Shape tool is on
        // (beside the tools they ran past the picture's column on a 1220 px window, under the Layers panel)
        RowLayout {
            objectName: "deShapeRow"
            Layout.fillWidth: true
            visible: page.s.tool === "Shape"
            spacing: 8
            Caption { text: "Shape" }
            SegmentedControl {
                objectName: "deShapeKind"
                compact: true
                controlled: true
                model: page.ch.shapeKinds || ["Points", "Rectangle", "Ellipse"]
                currentIndex: model.indexOf(page.s.shapeKind || "Points")
                onActivated: (i) => { page.shapeDraft = []; page.de.setShapeKind(model[i]) }
                HoverTip { tip: page.help("shape_kind") }
            }
            AccentButton {
                objectName: "deClearShapes"
                visible: (page.s.shapeCount || 0) > 0
                kind: "outline"
                text: "Clear Shapes"
                enabled: !page.working
                onClicked: { page.shapeDraft = []; page.de.clearShapes() }
                HoverTip { tip: "Takes every shape off the selected layer (Undo brings them back)." }
            }
            Muted {
                Layout.fillWidth: true
                elide: Text.ElideRight
                maximumLineCount: 2
                text: (page.s.shapeKind || "Points") === "Points"
                      ? "Click each corner; click the first point (or Enter) to close. Ctrl on the first point cuts."
                      : "Drag it out. Hold Ctrl as you start to cut it out instead."
            }
        }
        // the picture's own options (user, 2026-09-28), a row of their own so
        // the view row keeps its width (the picture fills the height left)
        RowLayout {
            objectName: "deViewOptions"
            Layout.fillWidth: true
            spacing: 8
            // a picture option, with the others (2026-09-30: the view row had no
            // room left once View gained 3D)
            VarCheck { varName: "colormap"; text: "Colour depth"; tip: page.help("colormap") }
            // the depth views show the map's 10 bits (user, 2026-10-05: banding in
            // an 8-bit picture); not in the RGB view, and hidden in 3D like RGB/D
            AnimCheck {
                objectName: "deDither"
                visible: page.s.view !== "3D"
                text: "Dither"
                enabled: page.s.view !== "RGB"
                opacity: enabled ? 1 : Theme.dim(0.45)
                checked: page.de.viewOptions.dither !== false
                onToggled: { page.de.setViewOption("dither", checked); checked = Qt.binding(() => page.de.viewOptions.dither !== false) }
                HoverTip { tip: "The depth map has 10 bits (about 900 shades) but the screen shows 8 (256), so a gentle "
                                + "slope -- a wall, a floor -- shows as flat bands. Dither adds a faint fixed pattern so the "
                                + "in-between shades show and the bands go. Only the picture changes: the rendered map is "
                                + "always the full 10 bits. Off: the plain 8-bit picture." }
            }
            AnimCheck {
                objectName: "deOutlineOnly"
                text: "Outline only"
                enabled: !!page.s.overlay
                opacity: enabled ? 1 : Theme.dim(0.45)
                checked: page.de.viewOptions.maskStyle === "outline"
                onToggled: { page.de.setViewOption("maskStyle", checked ? "outline" : "tint"); checked = Qt.binding(() => page.de.viewOptions.maskStyle === "outline") }
                HoverTip { tip: "Draw each mask as a thin outline only, with nothing tinted, so you can see exactly where "
                                + "its edge sits on the picture underneath. The selected layer's outline is solid, the "
                                + "others lighter." }
            }
            // Highlight Specks (user, 2026-10-12): what a Despeck layer would catch, and does
            AnimCheck {
                objectName: "deSpecks"
                visible: page.s.view !== "3D"
                text: "Specks"
                checked: !!page.de.viewOptions.specks
                onToggled: { page.de.setViewOption("specks", checked); checked = Qt.binding(() => !!page.de.viewOptions.specks) }
                HoverTip { tip: page.help("specks") }
            }
            // RGB/D (RGB over depth; renamed by the user, 2026-10-05), its slider and
            // its %: hidden in the 3D view, where they
            // don't apply -- shown there (disabled) beside the 3D view's own options
            // they ran the row 10 px under the Layers panel (user, 2026-10-05)
            AnimCheck {
                objectName: "deRgbOverlay"
                visible: page.s.view !== "3D"
                text: "RGB/D"
                enabled: page.s.view !== "RGB" && page.s.view !== "3D"
                opacity: enabled ? 1 : Theme.dim(0.45)
                checked: !!page.de.viewOptions.rgbOverlay
                onToggled: { page.de.setViewOption("rgbOverlay", checked); checked = Qt.binding(() => !!page.de.viewOptions.rgbOverlay) }
                HoverTip { tip: "Blend the clip's own picture over the Depth and Edited views, to check the depth map's "
                                + "edges (and your edits) line up exactly with the objects in the video. The slider "
                                + "beside it sets how much of the picture shows." }
            }
            Slider {
                id: rgbOpacity
                objectName: "deRgbOpacity"
                visible: page.s.view !== "3D"
                Layout.preferredWidth: 90
                enabled: page.s.view !== "RGB" && page.s.view !== "3D" && !!page.de.viewOptions.rgbOverlay
                opacity: enabled ? 1 : Theme.dim(0.45)
                from: 0.05; to: 0.95; stepSize: 0.05
                snapMode: Slider.SnapAlways
                // as SliderRow: not while the page scrolls past it, unless clicked (bug sweep A10)
                property Item pageScroller: null
                Component.onCompleted: { for (var p = parent; p; p = p.parent) if (p.objectName === "stageScroll") { pageScroller = p; break } }
                wheelEnabled: !(pageScroller && pageScroller.scrollable) || activeFocus || pressed
                value: page.de.viewOptions.rgbOpacity !== undefined ? page.de.viewOptions.rgbOpacity : 0.5
                onMoved: rgbApply.restart()
                onPressedChanged: if (!pressed) { rgbApply.stop(); page.de.setViewOption("rgbOpacity", value) }
                // redraws while dragging, at most every 60 ms
                Timer { id: rgbApply; interval: 60; onTriggered: page.de.setViewOption("rgbOpacity", rgbOpacity.value) }
                background: Rectangle {
                    x: rgbOpacity.leftPadding
                    y: rgbOpacity.topPadding + rgbOpacity.availableHeight / 2 - 2
                    width: rgbOpacity.availableWidth; height: 4; radius: 2
                    color: Theme.trough
                    Rectangle { width: rgbOpacity.visualPosition * parent.width; height: parent.height; radius: 2; color: Theme.accent }
                }
                handle: Rectangle {
                    x: rgbOpacity.leftPadding + rgbOpacity.visualPosition * (rgbOpacity.availableWidth - width)
                    y: rgbOpacity.topPadding + rgbOpacity.availableHeight / 2 - height / 2
                    width: 8; height: 18; radius: 3
                    color: Theme.knob; border.width: 2; border.color: Theme.accent
                }
                HoverTip { tip: "How much of the clip's picture shows over the depth: " + Math.round(rgbOpacity.value * 100) + "%." }
            }
            Caption {
                visible: page.s.view !== "3D"
                text: Math.round(rgbOpacity.value * 100) + "%"
                opacity: rgbOpacity.enabled ? 1 : Theme.dim(0.45)
                Layout.preferredWidth: 32
            }
            // the 3D view's two looks (user, 2026-09-30): the red/cyan anaglyph,
            // or the warped right eye with its disocclusion holes highlighted
            SegmentedControl {
                id: warpModeBox
                objectName: "deWarpMode"
                visible: page.s.view === "3D"
                compact: true
                controlled: true
                // Glasses-Free 3D always listed, greyed out (with why) unless an SR display is
                // connected and this window is on it (user, 2026-10-11; sr_display.py)
                readonly property bool hasGlasses: typeof Glasses3D !== "undefined" && Glasses3D !== null
                readonly property bool glassesKnown: hasGlasses && Glasses3D.available
                readonly property string glassesWhyNot: Glasses.whyNot(hasGlasses ? Glasses3D : null, Window.window)
                model: hasGlasses ? ["Anaglyph", "Right Eye + Holes", "Glasses-Free 3D"] : ["Anaglyph", "Right Eye + Holes"]
                disabledItems: glassesWhyNot ? ["Glasses-Free 3D"] : []
                currentIndex: page.de.viewOptions.warpMode === "holes" ? 1
                              : page.de.viewOptions.warpMode === "glasses" && glassesKnown ? 2 : 0
                onActivated: (i) => page.de.setViewOption("warpMode", i === 1 ? "holes" : i === 2 ? "glasses" : "anaglyph")
                Layout.leftMargin: 8
                HoverTip { tip: warpModeBox.hoveredDisabled ? warpModeBox.glassesWhyNot
                                : "How the 3D view shows the edited depth, through Splatting's own preview with this "
                                + "clip's settings: Anaglyph for red/cyan glasses, the right eye with the gaps the "
                                + "depth opens highlighted, or Glasses-Free 3D on an SR 3D monitor (the picture only, "
                                + "each eye at full resolution). What you see is what Splatting will render once the "
                                + "edited map is rendered and used." }
            }
            CheckField {
                objectName: "keepTracking"
                // Keep Eye Tracking On (user, 2026-10-17; sr_display.py): only beside the choice
                store: Shell; key: "glassesKeepTracking"
                text: "Keep Tracking"
                shown: page.s.view === "3D" && warpModeBox.currentIndex === 2
                Layout.fillWidth: false
                help: "Keeps the 3D monitor's eye tracking (and its camera) on while a Glasses-Free 3D preview is open: "
                  + "between clips, in and out of Full Preview, and while a job runs -- so it does not have to find "
                  + "you again each time. Off: the camera is on only while the 3D picture is live. Either way it goes "
                  + "off when no preview shows Glasses-Free 3D, on another tab, and when Studio closes."
            }
            Item { Layout.fillWidth: true }
        }

        Rectangle {
            id: picture
            objectName: "dePicture"
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: 8
            color: Theme.isDark ? "#101010" : "#d8d8d8"
            clip: true
            HoverHandler { id: pictureHover }

            // v1 fits the picture at the SOURCE's aspect (the map may be
            // stretched to multiples of 64).
            readonly property real aspect: page.s.aspect || 16 / 9
            readonly property real dw: Math.min(width, height * aspect)
            readonly property real dh: dw / aspect
            // Zoom (user, 2026-10-13: "the same zooming function splatting and merging use"):
            // the wheel ~12% a notch about the point under the pointer, a double-click in the
            // 3D view's Right Eye + Holes 50% closer (zoomed in: back to Fit), a drag with the middle
            // button -- or the left one there, which takes no clicks -- pans. 1 = Fit; the
            // picture's own pixels, nothing re-rendered.
            property real zoom: 1
            readonly property real maxZoom: 8
            property real offX: 0           // the picture's corner while it is wider / taller than here
            property real offY: 0
            function placeX(w) { return w <= width ? (width - w) / 2 : Math.max(width - w, Math.min(0, offX)) }
            function placeY(h) { return h <= height ? (height - h) / 2 : Math.max(height - h, Math.min(0, offY)) }
            // z about (px, py), a point in this item: the frame point under it stays under it
            function zoomAt(z, px, py) {
                z = Math.max(1, Math.min(maxZoom, z))
                const fx = shown.width > 0 ? (px - shown.x) / shown.width : 0.5
                const fy = shown.height > 0 ? (py - shown.y) / shown.height : 0.5
                offX = px - fx * dw * z; offY = py - fy * dh * z
                zoom = z
                offX = shown.x; offY = shown.y      // kept in bounds from here on
            }
            function fit() { zoom = 1; offX = 0; offY = 0 }
            // another clip opens at Fit
            readonly property string clipKey: String(page.s.depth_path || "")
            onClipKeyChanged: fit()
            function toggleAt(px, py) { if (zoom > 1.001) fit(); else zoomAt(zoom * 1.5, px, py) }
            function panBy(dx, dy) { offX = shown.x + dx; offY = shown.y + dy; offX = shown.x; offY = shown.y }
            WheelHandler {
                objectName: "deZoomWheel"
                enabled: !!page.s.hasClip && !Theme.modalOpen
                acceptedModifiers: Qt.NoModifier
                onWheel: (e) => picture.zoomAt(picture.zoom * (e.angleDelta.y > 0 ? 1.12 : 1 / 1.12), e.x, e.y)
            }
            Item {
                id: shown
                // the video stays clean of the window's film grain
                GrainHole { clipTo: picture }
                width: picture.dw * picture.zoom; height: picture.dh * picture.zoom
                x: picture.placeX(width); y: picture.placeY(height)
                visible: !!page.s.hasClip
                Image {
                    objectName: "deImage"
                    anchors.fill: parent
                    cache: false
                    smooth: true
                    source: page.de.imageSerial > 0 && page.s.hasClip ? "image://depthedit/frame/" + page.de.imageSerial : ""
                }
                // Glasses-Free 3D: the SR display weaves the 3D view's picture (sr_display.py)
                GlassesFreeView {
                    objectName: "glassesFreeView"
                    stage: "depthedit"
                    pv: page.de
                    picture: shown
                    area: shown.parent      // the clip around it ("picture", named apart from this property)
                    wanted: page.active && page.s.view === "3D" && page.de.viewOptions.warpMode === "glasses"
                            && page.de.warpNote === ""
                }
                // the 3D view's word when Splatting can't draw it (user, 2026-09-30)
                Rectangle {
                    objectName: "deWarpNote"
                    visible: page.s.view === "3D" && page.de.warpNote !== ""
                    // centred in the view, wherever a zoom has put the picture
                    x: (picture.width - width) / 2 - shown.x; y: (picture.height - height) / 2 - shown.y
                    width: Math.min(parent.width - 40, 520)
                    height: warpNoteText.implicitHeight + 24
                    radius: 8
                    color: Theme.alpha(Theme.bg, 0.88)
                    border.width: 1
                    border.color: Theme.borderStrong
                    Text {
                        id: warpNoteText
                        anchors.fill: parent
                        anchors.margins: 12
                        wrapMode: Text.Wrap
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        text: page.de.warpNote
                        color: Theme.fg
                        font.family: Theme.bodyFont
                        font.pixelSize: Theme.bodySize
                    }
                }
                // the Global Despeck's zone (user, 2026-10-12): where it works, while its page is open
                Rectangle {
                    objectName: "deDespeckZone"
                    readonly property var zn: page.de.markers.zone || null
                    visible: !!zn
                    x: (zn ? zn[0] : 0) * shown.width
                    y: (zn ? zn[1] : 0) * shown.height
                    width: (zn ? zn[2] - zn[0] : 0) * shown.width
                    height: (zn ? zn[3] - zn[1] : 0) * shown.height
                    color: "transparent"; border.width: 2; border.color: "#ffb020"
                }
                // v1 _draw_markers: boxes, clicks, the gradient line.
                Repeater {
                    model: (page.de.markers.boxes || [])
                    Rectangle {
                        required property var modelData
                        x: Math.min(modelData[0], modelData[2]) * shown.width
                        y: Math.min(modelData[1], modelData[3]) * shown.height
                        width: Math.abs(modelData[2] - modelData[0]) * shown.width
                        height: Math.abs(modelData[3] - modelData[1]) * shown.height
                        color: "transparent"; border.width: 2; border.color: page.de.markers.colour || "white"
                    }
                }
                Repeater {
                    model: (page.de.markers.points || [])
                    Rectangle {
                        required property var modelData
                        x: modelData.x * shown.width - 5; y: modelData.y * shown.height - 5
                        width: 10; height: 10; radius: 5
                        color: modelData.add ? "#3ddc84" : "#ff4d4d"; border.width: 1.5; border.color: "white"
                        Text { anchors.centerIn: parent; text: modelData.add ? "+" : "-"; color: "white"; font.pixelSize: 8; font.bold: true }
                        // picked for Delete
                        Rectangle {
                            objectName: "dePointPicked"
                            visible: page.selPoints.indexOf(modelData.i) >= 0
                            anchors.centerIn: parent
                            width: 20; height: 20; radius: 10
                            color: "transparent"; border.width: 2; border.color: "#ffd84a"
                        }
                    }
                }
                // The drag in progress (v1 draws it on the canvas): a box.
                Rectangle {
                    id: dragBox
                    visible: false
                    color: "transparent"; border.width: 2; border.color: "white"
                }
            }
            // The gradient line and its drag: canvases the size of the view, not of the
            // picture -- zoomed in 8x a picture-sized canvas would be hundreds of MB
            Canvas {
                id: gradCanvas
                anchors.fill: parent
                visible: shown.visible
                readonly property var g: page.de.markers.gradient || null
                onGChanged: requestPaint()
                onWidthChanged: requestPaint()
                onHeightChanged: requestPaint()
                Connections {
                    target: shown
                    enabled: !!gradCanvas.g
                    function onXChanged() { gradCanvas.requestPaint() }
                    function onYChanged() { gradCanvas.requestPaint() }
                    function onWidthChanged() { gradCanvas.requestPaint() }
                }
                onPaint: {
                    const ctx = getContext("2d")
                    ctx.reset()
                    if (!g) return
                    const ax = shown.x + g[0] * shown.width, ay = shown.y + g[1] * shown.height
                    const bx = shown.x + g[2] * shown.width, by = shown.y + g[3] * shown.height
                    ctx.strokeStyle = "#ffffff"; ctx.lineWidth = 2
                    ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke()
                    const ang = Math.atan2(by - ay, bx - ax)
                    ctx.beginPath(); ctx.moveTo(bx, by)
                    ctx.lineTo(bx - 12 * Math.cos(ang - 0.4), by - 12 * Math.sin(ang - 0.4))
                    ctx.lineTo(bx - 12 * Math.cos(ang + 0.4), by - 12 * Math.sin(ang + 0.4))
                    ctx.closePath(); ctx.fillStyle = "#ffffff"; ctx.fill()
                    for (const [x, y, t] of [[ax, ay, "N"], [bx, by, "F"]]) {
                        ctx.beginPath(); ctx.arc(x, y, 8, 0, 2 * Math.PI)
                        ctx.fillStyle = "#222222"; ctx.fill(); ctx.strokeStyle = "white"; ctx.lineWidth = 1; ctx.stroke()
                        ctx.fillStyle = "white"; ctx.font = "bold 9px sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle"
                        ctx.fillText(t, x, y)
                    }
                }
            }
            Canvas {
                id: dragLine
                anchors.fill: parent
                visible: false
                property real ax; property real ay; property real bx; property real by
                onPaint: {
                    const ctx = getContext("2d"); ctx.reset()
                    ctx.strokeStyle = "white"; ctx.lineWidth = 2
                    ctx.beginPath(); ctx.moveTo(ax, ay); ctx.lineTo(bx, by); ctx.stroke()
                }
            }
            // the shape being drawn (2026-10-17): its points joined, a line to the pointer, and the
            // rectangle or ellipse a drag is making
            Canvas {
                id: shapeCanvas
                objectName: "deShapeCanvas"
                anchors.fill: parent
                visible: shown.visible && page.s.tool === "Shape"
                property point hover: Qt.point(-1, -1)
                property var dragRect: null          // [ax, ay, bx, by] frame-relative, or null
                property var edgePreview: null       // a shape with one edge being dragged: its corners, or null
                readonly property string key: JSON.stringify(page.shapeDraft) + JSON.stringify(dragRect)
                                              + JSON.stringify(edgePreview)
                onKeyChanged: requestPaint()
                onHoverChanged: if (page.shapeDraft.length) requestPaint()
                onWidthChanged: requestPaint()
                onHeightChanged: requestPaint()
                Connections {
                    target: shown
                    function onXChanged() { shapeCanvas.requestPaint() }
                    function onYChanged() { shapeCanvas.requestPaint() }
                    function onWidthChanged() { shapeCanvas.requestPaint() }
                }
                onPaint: {
                    const ctx = getContext("2d")
                    ctx.reset()
                    const px = (p) => [shown.x + p[0] * shown.width, shown.y + p[1] * shown.height]
                    const col = page.shapeCut ? "#ff6b6b" : "#ffd84a"
                    ctx.lineWidth = 2
                    ctx.strokeStyle = col
                    if (dragRect) {
                        const [ax, ay] = px([dragRect[0], dragRect[1]]), [bx, by] = px([dragRect[2], dragRect[3]])
                        ctx.beginPath()
                        if (page.s.shapeKind === "Ellipse") {
                            ctx.ellipse(Math.min(ax, bx), Math.min(ay, by), Math.abs(bx - ax), Math.abs(by - ay))
                        } else {
                            ctx.rect(Math.min(ax, bx), Math.min(ay, by), Math.abs(bx - ax), Math.abs(by - ay))
                        }
                        ctx.stroke()
                    }
                    if (edgePreview && edgePreview.length >= 3) {          // the shape as the edge drag leaves it
                        ctx.beginPath()
                        const [ex, ey] = px(edgePreview[0]); ctx.moveTo(ex, ey)
                        for (let k = 1; k < edgePreview.length; ++k) { const [x, y] = px(edgePreview[k]); ctx.lineTo(x, y) }
                        ctx.closePath(); ctx.strokeStyle = "#ffd84a"; ctx.stroke()
                    }
                    const pts = page.shapeDraft
                    if (!pts.length) return
                    ctx.beginPath()
                    let [x0, y0] = px(pts[0])
                    ctx.moveTo(x0, y0)
                    for (let k = 1; k < pts.length; ++k) { const [x, y] = px(pts[k]); ctx.lineTo(x, y) }
                    if (hover.x >= 0) ctx.lineTo(hover.x, hover.y)
                    ctx.stroke()
                    for (let k = 0; k < pts.length; ++k) {
                        const [x, y] = px(pts[k])
                        ctx.beginPath(); ctx.arc(x, y, k === 0 ? 6 : 4, 0, 2 * Math.PI)
                        ctx.fillStyle = k === 0 ? col : "#222222"; ctx.fill()
                        ctx.strokeStyle = col; ctx.lineWidth = 1.5; ctx.stroke()
                    }
                }
            }
            // v1 _on_press / _on_drag / _on_release / _on_right_click -- and the zoom's pan
            MouseArea {
                objectName: "dePictureMouse"
                anchors.fill: parent
                enabled: !!page.s.hasClip
                readonly property bool view3d: page.s.view === "3D"
                // the 3D view edits like the others (user, 2026-10-25): its Anaglyph and Glasses-Free
                // pictures hold the left eye where the frame is, the masks drawn on it alone -- only
                // Right Eye + Holes, a shifted picture, is to look at: there the left button pans
                readonly property bool look3d: view3d && page.de.viewOptions.warpMode === "holes"
                acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
                preventStealing: true           // a Box drag is never taken for a page scroll
                hoverEnabled: page.s.tool === "Shape"     // the polygon's line follows the pointer
                cursorShape: panning ? Qt.ClosedHandCursor : look3d ? (picture.zoom > 1.001 ? Qt.OpenHandCursor : Qt.ArrowCursor)
                             : (edgeDrag || hoverEdge) ? ((edgeDrag || hoverEdge).axis === "x" ? Qt.SizeHorCursor : Qt.SizeVerCursor)
                             : Qt.CrossCursor
                // dragging one edge of a shape (user, 2026-10-25): an upright edge sideways, a flat one up or
                // down -- that side stretches, nothing else moves. Points and Rectangle shapes of the selected
                // layer; a press on an edge (no shape half drawn, no Ctrl) takes it before a new shape.
                property var edgeDrag: null          // {shape, edge, axis} while dragging
                property var hoverEdge: null         // the edge under the pointer, for the cursor
                function edgeAt(mx, my) {
                    if (page.s.tool !== "Shape" || page.shapeDraft.length) return null
                    const list = page.s.shapes || []
                    let best = null, bestD = 7 * 7
                    for (let i = list.length - 1; i >= 0; --i) {           // the shape drawn last is on top
                        const es = list[i].edges || []
                        for (let k = 0; k < es.length; ++k) {
                            const ax = shown.x + es[k][0][0] * shown.width, ay = shown.y + es[k][0][1] * shown.height
                            const bx = shown.x + es[k][1][0] * shown.width, by = shown.y + es[k][1][1] * shown.height
                            const dx = bx - ax, dy = by - ay, len2 = dx * dx + dy * dy
                            const t = len2 > 0 ? Math.max(0, Math.min(1, ((mx - ax) * dx + (my - ay) * dy) / len2)) : 0
                            const qx = ax + t * dx - mx, qy = ay + t * dy - my, d = qx * qx + qy * qy
                            if (d < bestD) { bestD = d; best = { shape: i, edge: k, axis: Math.abs(dx) < Math.abs(dy) ? "x" : "y" } }
                        }
                    }
                    return best
                }
                // the dragged shape's corners as the edge moved `delta` (frame-relative) leaves them
                function edgeMoved(e, delta) {
                    const es = (page.s.shapes || [])[e.shape].edges
                    const pts = es.map((s) => [s[0][0], s[0][1]])
                    const i = e.axis === "x" ? 0 : 1
                    for (const k of [e.edge, (e.edge + 1) % pts.length]) pts[k][i] = Math.min(Math.max(pts[k][i] + delta, 0), 1)
                    return pts
                }
                function edgeDelta(m) {
                    return edgeDrag.axis === "x" ? (m.x - start.x) / shown.width : (m.y - start.y) / shown.height
                }
                property point start
                property bool dragging: false
                property bool panning: false
                property point panFrom
                function rel(mx, my) { const p = mapToItem(shown, mx, my); return Qt.point(p.x / shown.width, p.y / shown.height) }
                // the point drawn under (mx, my), within its own size: its "i", or -1
                function pointAt(mx, my) {
                    let best = -1, bestD = 9 * 9
                    for (const pt of (page.de.markers.points || [])) {
                        const dx = shown.x + pt.x * shown.width - mx, dy = shown.y + pt.y * shown.height - my
                        if (dx * dx + dy * dy <= bestD) { bestD = dx * dx + dy * dy; best = pt.i }
                    }
                    return best
                }
                function pick(i, more) {
                    const sel = page.selPoints.slice()
                    const at = sel.indexOf(i)
                    if (!more) page.selPoints = [i]
                    else { if (at >= 0) sel.splice(at, 1); else sel.push(i); page.selPoints = sel }
                }
                property int pressedPoint: -1
                // the shape tool (2026-10-17): Points -- a click adds a point, a click on the first point (or
                // a double-click, or Enter) closes it, a right-click takes the last back; Rectangle / Ellipse
                // -- a drag. Ctrl as it starts: the shape cuts its area out of the layer instead.
                property bool shapeDragging: false
                function shapePress(m, r) {
                    const inside = Qt.point(Math.min(Math.max(r.x, 0), 1), Math.min(Math.max(r.y, 0), 1))
                    if (m.button === Qt.LeftButton && !(m.modifiers & Qt.ControlModifier)) {
                        const e = edgeAt(m.x, m.y)
                        if (e) { edgeDrag = e; start = Qt.point(m.x, m.y); shapeCanvas.edgePreview = edgeMoved(e, 0); return }
                    }
                    if (page.s.shapeKind === "Points" || !page.s.shapeKind) {
                        if (m.button === Qt.RightButton) { page.shapeDraft = page.shapeDraft.slice(0, -1); return }
                        if (m.button !== Qt.LeftButton) return
                        const pts = page.shapeDraft
                        if (pts.length >= 3) {
                            const fx = shown.x + pts[0][0] * shown.width, fy = shown.y + pts[0][1] * shown.height
                            if ((fx - m.x) * (fx - m.x) + (fy - m.y) * (fy - m.y) <= 10 * 10) { page.closeShape(); return }
                        }
                        if (!pts.length) page.shapeCut = (m.modifiers & Qt.ControlModifier) !== 0
                        page.shapeDraft = pts.concat([[inside.x, inside.y]])
                        return
                    }
                    if (m.button !== Qt.LeftButton) return
                    page.shapeCut = (m.modifiers & Qt.ControlModifier) !== 0
                    start = Qt.point(m.x, m.y); shapeDragging = true
                    shapeCanvas.dragRect = [inside.x, inside.y, inside.x, inside.y]
                }
                onPressed: (m) => {
                    if (m.button === Qt.MiddleButton || (look3d && m.button === Qt.LeftButton)) {
                        panning = true; panFrom = Qt.point(m.x, m.y); return
                    }
                    if (look3d) return
                    const r = rel(m.x, m.y)
                    if (page.s.tool === "Shape") { shapePress(m, r); return }
                    const more = (m.modifiers & (Qt.ShiftModifier | Qt.ControlModifier)) !== 0
                    const hit = m.button === Qt.LeftButton ? pointAt(m.x, m.y) : -1
                    pressedPoint = hit
                    const tool = page.s.tool
                    // on a point: pick it, no new click (with Box / Gradient: once it is no drag)
                    if (hit >= 0 && tool !== "Box" && tool !== "Gradient") { pick(hit, more); return }
                    if (m.button === Qt.RightButton) { page.de.rightClick(r.x, r.y); return }
                    if (!more && hit < 0) page.selPoints = []
                    if (tool === "Box" || tool === "Gradient") {
                        start = Qt.point(m.x, m.y); dragging = true
                        const p = mapToItem(shown, m.x, m.y)
                        if (tool === "Box") { dragBox.x = p.x; dragBox.y = p.y; dragBox.width = 0; dragBox.height = 0; dragBox.visible = true }
                        else { dragLine.ax = dragLine.bx = m.x; dragLine.ay = dragLine.by = m.y; dragLine.visible = true; dragLine.requestPaint() }
                        return
                    }
                    page.de.click(r.x, r.y, (m.modifiers & Qt.ControlModifier) !== 0)
                }
                onDoubleClicked: (m) => {
                    if (look3d && m.button === Qt.LeftButton) { picture.toggleAt(m.x, m.y); return }
                    // a double-click closes the polygon: its second press added the same point again
                    if (page.s.tool === "Shape" && page.shapeDraft.length >= 4) {
                        page.shapeDraft = page.shapeDraft.slice(0, -1); page.closeShape()
                    }
                }
                onPositionChanged: (m) => {
                    if (panning) { picture.panBy(m.x - panFrom.x, m.y - panFrom.y); panFrom = Qt.point(m.x, m.y); return }
                    if (page.s.tool === "Shape") {
                        if (edgeDrag) { shapeCanvas.edgePreview = edgeMoved(edgeDrag, edgeDelta(m)); return }
                        if (!pressed) hoverEdge = edgeAt(m.x, m.y)
                        shapeCanvas.hover = Qt.point(m.x, m.y)
                        if (shapeDragging) {
                            const a = rel(start.x, start.y), b = rel(m.x, m.y)
                            shapeCanvas.dragRect = [a.x, a.y, Math.min(Math.max(b.x, 0), 1), Math.min(Math.max(b.y, 0), 1)]
                        }
                        return
                    }
                    if (!dragging) return
                    const a = mapToItem(shown, start.x, start.y), b = mapToItem(shown, m.x, m.y)
                    if (dragBox.visible) {
                        dragBox.x = Math.min(a.x, b.x); dragBox.y = Math.min(a.y, b.y)
                        dragBox.width = Math.abs(b.x - a.x); dragBox.height = Math.abs(b.y - a.y)
                    } else { dragLine.bx = m.x; dragLine.by = m.y; dragLine.requestPaint() }
                }
                onReleased: (m) => {
                    if (panning) { panning = false; return }
                    if (edgeDrag) {
                        const e = edgeDrag, d = edgeDelta(m)
                        edgeDrag = null; shapeCanvas.edgePreview = null
                        if (Math.abs(e.axis === "x" ? m.x - start.x : m.y - start.y) >= 3)
                            page.de.moveShapeEdge(e.shape, e.edge, e.axis, d)
                        return
                    }
                    if (shapeDragging) {
                        shapeDragging = false
                        const d = shapeCanvas.dragRect
                        shapeCanvas.dragRect = null
                        if (d && Math.abs(m.x - start.x) + Math.abs(m.y - start.y) >= 6)
                            page.de.addShape(page.s.shapeKind, [[d[0], d[1]], [d[2], d[3]]], page.shapeCut)
                        return
                    }
                    if (!dragging) return
                    dragging = false; dragBox.visible = false; dragLine.visible = false
                    if (Math.abs(m.x - start.x) + Math.abs(m.y - start.y) < 6) {        // v1: under 6 px is no drag
                        if (pressedPoint >= 0) pick(pressedPoint, (m.modifiers & (Qt.ShiftModifier | Qt.ControlModifier)) !== 0)
                        return
                    }
                    const a = rel(start.x, start.y), b = rel(m.x, m.y)
                    page.de.drag(a.x, a.y, b.x, b.y)
                }
                onCanceled: { panning = false; dragging = false; dragBox.visible = false; dragLine.visible = false
                              shapeDragging = false; shapeCanvas.dragRect = null
                              edgeDrag = null; shapeCanvas.edgePreview = null }
                onExited: { shapeCanvas.hover = Qt.point(-1, -1); hoverEdge = null }
            }
            Column {
                anchors.centerIn: parent
                spacing: 10
                visible: !page.s.hasClip
                Spinner { anchors.horizontalCenter: parent.horizontalCenter; visible: !!page.s.loading || page.s.busyJob === "Loading clip"; running: visible }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    text: page.s.loadError ? "The Depth Map Editor could not be loaded: " + page.s.loadError
                          : page.s.loading ? "Loading the editor…" : "No clip loaded"
                    color: "#888888"
                    font.family: Theme.bodyFont
                    font.pixelSize: 14
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            enabled: !!page.s.hasClip
            AccentButton { text: "<"; kind: "outline"; implicitWidth: 32; onClicked: page.de.step(-1) }
            Slider {
                id: frameSlider
                objectName: "deFrame"
                Layout.fillWidth: true
                from: 0; to: Math.max(0, (page.s.frames || 1) - 1); stepSize: 1; snapMode: Slider.SnapAlways
                value: page.s.frame || 0
                onMoved: page.de.setFrame(Math.round(value))
                Repeater {
                    objectName: "deKeyMarks"
                    model: page.s.keyframes || []
                    Rectangle {
                        required property var modelData
                        x: frameSlider.leftPadding + (frameSlider.to > 0 ? modelData.frame / frameSlider.to : 0)
                           * (frameSlider.availableWidth - 8) + 4 - width / 2
                        y: frameSlider.topPadding + frameSlider.availableHeight / 2 + 5
                        width: 4; height: 8; radius: 1
                        color: modelData.kind === "dynamic" ? "#ffb347" : "#4fa8ff"
                    }
                }
                background: Rectangle {
                    x: frameSlider.leftPadding; y: frameSlider.topPadding + frameSlider.availableHeight / 2 - 2
                    width: frameSlider.availableWidth; height: 4; radius: 2; color: Theme.trough
                    Rectangle { width: frameSlider.visualPosition * parent.width; height: 4; radius: 2; color: Theme.accent }
                }
                handle: Rectangle {
                    x: frameSlider.leftPadding + frameSlider.visualPosition * (frameSlider.availableWidth - width)
                    y: frameSlider.topPadding + frameSlider.availableHeight / 2 - height / 2
                    width: 8; height: 18; radius: 3; color: Theme.knob; border.width: 2; border.color: Theme.accent
                }
            }
            AccentButton { text: ">"; kind: "outline"; implicitWidth: 32; onClicked: page.de.step(1) }
            Caption { text: page.s.frameText || "Frame - / -"; Layout.preferredWidth: 120; font.features: { "tnum": 1 } }
            // the zoom, as Splatting's Preview Scale: Fit, or how far past it
            Dropdown {
                objectName: "deZoomBox"
                boxWidth: 76
                readonly property var levels: [1, 1.5, 2, 3, 4, 8]
                model: ["Fit", "150%", "200%", "300%", "400%", "800%"]
                currentIndex: levels.indexOf(Math.round(picture.zoom * 100) / 100)
                displayText: picture.zoom <= 1.001 ? "Fit" : Math.round(picture.zoom * 100) + "%"
                onActivated: (i) => { if (i === 0) picture.fit(); else picture.zoomAt(levels[i], picture.width / 2, picture.height / 2) }
                HoverTip { tip: "Zoom, as a share of Fit (the whole frame). The mouse wheel zooms about the point under "
                                + "the pointer; drag with the middle button to move around a zoomed-in picture (in the 3D "
                                + "view the left button drags too, and a double-click zooms in, or back to Fit)." }
            }
            // Undo / Redo (user, 2026-10-05): every edit to the layers and the whole
            // frame. Icons only, after the frame counter: the rows above the picture are full
            // (the 3D view's options already fill theirs)
            AccentButton {
                objectName: "deUndo"
                kind: "outline"
                iconGlyph: Theme.icon.undo
                implicitWidth: 34
                enabled: !!page.s.canUndo && !page.working
                onClicked: page.de.undo()
                HoverTip { tip: page.s.canUndo ? "Undo " + page.s.undoLabel + " (Ctrl+Z)" : "Nothing to undo (Ctrl+Z)" }
            }
            AccentButton {
                objectName: "deRedo"
                kind: "outline"
                iconGlyph: Theme.icon.redo
                implicitWidth: 34
                enabled: !!page.s.canRedo && !page.working
                onClicked: page.de.redo()
                HoverTip { tip: page.s.canRedo ? "Redo " + page.s.redoLabel + " (Ctrl+Y)" : "Nothing to redo (Ctrl+Y)" }
            }
        }
    }

    // ---- the rail -----------------------------------------------------------------
    // Scrolls when it doesn't fit (user, 2026-10-08: "on the 1080p screen some controls on the bottom
    // get cut off ... make the controls scrollable ... grabbable like a touchscreen"): the wheel, its
    // bar, or a drag on any empty part of it. The picture stays put.
    Flickable {
        id: railScroll
        objectName: "deRailScroll"
        x: parent.width - width - 14
        y: left.y
        width: 420 + (scrollable ? 10 : 0)
        height: Math.max(120, page.height - y - 8)
        contentWidth: width
        contentHeight: rail.implicitHeight
        readonly property bool scrollable: contentHeight > height + 1
        interactive: scrollable
        clip: scrollable
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        ScrollBar.vertical: SlimScrollBar { policy: railScroll.scrollable ? ScrollBar.AlwaysOn : ScrollBar.AlwaysOff }
        ColumnLayout {
            id: rail
            objectName: "deRail"
            width: 420
            spacing: 6
            enabled: page.ready

            Section {
                id: layersSection
                title: "Layers"
                Layout.fillWidth: true
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 5 * 24 + 8
                        radius: 6; color: Theme.field; border.width: 1; border.color: Theme.border
                        ListView {
                            id: layerList
                            objectName: "deLayers"
                            anchors.fill: parent; anchors.margins: 4
                            clip: true
                            model: page.s.layers || []
                            // a changed list (a layer added, renamed, moved) is a new
                            // model and the view went back to the top: the selected
                            // layer is kept in view (audit 2026-09-28, minor)
                            onModelChanged: Qt.callLater(() => {
                                if (page.s.selected !== undefined && page.s.selected !== null && page.s.selected >= 0)
                                    layerList.positionViewAtIndex(page.s.selected, ListView.Contain)
                            })
                            HoverTip { tip: page.help("layers") }
                            delegate: Rectangle {
                                required property var modelData
                                required property int index
                                width: layerList.width; height: 24; radius: 4
                                color: index === page.s.selected ? Theme.alpha(Theme.accent, 0.3) : "transparent"
                                Text {
                                    anchors.verticalCenter: parent.verticalCenter; x: 6
                                    text: modelData.name; color: modelData.colour
                                    font.family: Theme.bodyFont; font.pixelSize: Theme.bodySize
                                }
                                MouseArea { anchors.fill: parent; onClicked: page.de.selectLayer(index) }
                            }
                        }
                    }
                    ColumnLayout {
                        Layout.preferredWidth: 84
                        Layout.maximumWidth: 84
                        spacing: 2
                        Btn { op: "_new_layer"; text: "New"; tip: page.help("layer_new"); Layout.fillWidth: true }
                        Btn { op: "_delete_layer"; text: "Delete"; tip: page.help("layer_delete"); Layout.fillWidth: true }
                        AccentButton { text: "Up"; kind: "outline"; onClicked: page.de.moveLayer(-1); Layout.fillWidth: true; HoverTip { tip: page.help("layer_move") } }
                        AccentButton { text: "Down"; kind: "outline"; onClicked: page.de.moveLayer(1); Layout.fillWidth: true; HoverTip { tip: page.help("layer_move") } }
                    }
                }
                Muted { text: page.s.layer_info || "" }
                // Tracking here, not on a page: the Layers panel is always on screen.
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 6
                    Btn { objectName: "deTrack"; op: "_track"; text: "Start Tracking"; kind: "filled"; enabled: page.s.track_btn !== false; tip: page.help("track") }
                    // Start Here (user, 2026-10-05): from the frame showing, one way only --
                    // the arrow beside it picks back over the previous frames or on over the next
                    // (page.trackBack)
                    AccentButton {
                        objectName: "deTrackHere"
                        text: "Start Here"
                        kind: "outline"
                        enabled: page.s.track_btn !== false
                        onClicked: page.de.trackHere()
                        HoverTip { tip: (page.trackBack
                                         ? "Track from this frame BACK to the start only: the frames after it keep their masks."
                                         : "Track from this frame ON to the end only: the frames before it keep their masks.")
                                        + " The arrow beside it switches the direction." }
                    }
                    AccentButton {
                        objectName: "deTrackDir"
                        kind: "outline"
                        implicitWidth: 32
                        iconGlyph: page.trackBack ? Theme.icon.toStart : Theme.icon.toEnd
                        onClicked: page.de.setViewOption("trackDir", page.trackBack ? "forward" : "backward")
                        HoverTip { tip: page.trackBack ? "Start Here tracks back over the previous frames. Click to track on over the next instead."
                                                         : "Start Here tracks on over the next frames. Click to track back over the previous instead." }
                    }
                    Btn { op: "_request_stop"; text: "Stop"; enabled: !!page.s.stop_btn }
                    Item { Layout.fillWidth: true }
                }
                // the status -- tracking's and rendering's frame counts among it -- right
                // under the bar (user, 2026-10-05; was along the page's foot). One line,
                // held whatever it says, so the panel never changes height (two put the
                // page past a 1080p screen); the whole message on hover when it's longer.
                Text {
                    id: statusLine
                    objectName: "deStatus"
                    Layout.fillWidth: true
                    Layout.topMargin: 4             // room for the tracking bar, a gap each side of it
                    Layout.preferredHeight: fontMetrics.height
                    text: page.s.status || ""
                    elide: Text.ElideRight
                    color: Theme.fgMuted
                    font.family: Theme.bodyFont
                    font.pixelSize: Theme.bodySize
                    FontMetrics { id: fontMetrics; font: statusLine.font }
                    HoverTip { tip: statusLine.truncated ? statusLine.text : "" }
                    // tracking's progress, a line the panel's width in the gap above
                    // the status (user, 2026-10-05: Start Here left the row's bar no room)
                    // 4 px (user, 2026-10-05: 2 was too thin to read), 2 px clear of the
                    // buttons above and the status below (user, 2026-10-06)
                    Rectangle {
                        objectName: "deTrackBar"
                        y: -6
                        width: parent.width
                        height: 4
                        radius: 2
                        color: Theme.trough
                        Rectangle {
                            width: parent.width * Math.max(0, Math.min(1, (page.s.trackProgress || 0) / 100))
                            height: parent.height
                            radius: 2
                            color: Theme.accent
                        }
                    }
                }
            }

            Seg { id: pageStrip; objectName: "dePages"; varName: "page"; values: page.ch.page || ["Segment", "Layer", "Gradient", "Global", "Output"]; Layout.fillWidth: true }

            // -- Segment
            ColumnLayout {
                id: pSegment
                visible: page.s.page === "Segment" || page.s.page === undefined
                Layout.fillWidth: true
                spacing: 6
                Section {
                    title: "SAM 3"
                    objectName: "deSamSection"
                    // set up: nothing to do here any more -- its settings are in Settings
                    visible: !page.samInstalled
                    Layout.fillWidth: true
                    Muted { objectName: "deSamStatus"; text: page.s.sam_status || "" }
                    // a Flow and a short label: three buttons in a row and the long label pushed
                    // the section past the rail (manual shot, 2026-10-10)
                    Flow {
                        Layout.fillWidth: true
                        spacing: 6
                        Btn { op: "_install_packages"; text: "Install Packages"; enabled: !!page.s.install_btn; tip: page.help("install_packages") }
                        Btn { op: "_download_weights"; text: "Download Weights"; enabled: !!page.s.weights_btn && !page.samDl.on; tip: page.help("download_weights") }
                        Btn { op: "_toggle_model"; text: "Unload Model"; enabled: !!page.s.model_btn; tip: page.help("unload_model") }
                    }
                    // the download (Settings > Studio > Models' own): its bar and Stop (user, 2026-10-21)
                    RowLayout {
                        objectName: "deSamDownload"
                        visible: page.samDl.on
                        Layout.fillWidth: true
                        spacing: 8
                        ProgressBar {
                            objectName: "deSamDownloadProgress"
                            Layout.fillWidth: true
                            from: 0; to: 1; value: page.samDl.running ? page.samDl.progress : 0
                            background: Rectangle { implicitHeight: 6; radius: 3; color: Theme.trough }
                            contentItem: Item { Rectangle { width: parent.parent.visualPosition * parent.width; height: 6; radius: 3; color: Theme.accent } }
                        }
                        AccentButton {
                            objectName: "deSamDownloadStop"
                            text: "Stop"; kind: "ghost"
                            onClicked: page.samDl.running ? Models.stop() : Models.unqueue("sam3")
                            HoverTip { tip: "Stops the download. What came down is kept: the next Download Weights carries on from there." }
                        }
                    }
                    Muted {
                        visible: page.samDl.on || page.samDl.error !== ""
                        text: page.samDl.error !== "" ? page.samDl.error
                              : page.samDl.running ? page.samDl.text
                              : "Waiting for the download running in Settings > Studio > Models."
                    }
                    VarCheck { varName: "offload"; text: "Keep frames in system RAM (saves VRAM)"; tip: page.help("offload_frames") }
                }
                Section {
                    title: "Select Objects"
                    Layout.fillWidth: true
                    RowLayout {
                        spacing: 8
                        Caption { text: "Segment on:" }
                        Seg { varName: "layer_source"; values: page.ch.source || ["RGB", "Depth"]; HoverTip { tip: page.help("segment_on") } }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Caption { text: "Find:" }
                        Field {
                            id: findField
                            objectName: "deFindText"
                            varName: "text"; Layout.fillWidth: true
                            onAccepted: { page.de.setVar("text", text); page.de.run("_find_text") }
                            HoverTip { tip: page.help("text_prompt") }
                        }
                        AccentButton { text: "Find"; kind: "outline"; onClicked: { page.de.setVar("text", findField.text); page.de.run("_find_text") } }
                    }
                    Muted {
                        text: "Add: click an object. Ctrl+click starts another object.\n"
                              + "Remove: click (or right-click with any tool) to cut an area out.\n"
                              + "Box: drag around an object.  Arrow keys step frames.\n"
                              + "Drifting? Go to a frame where the mask is wrong, Add/Remove "
                              + "there, and Start Tracking again -- every corrected frame guides it."
                    }
                    Btn { op: "_clear_prompts"; text: "Clear Layer Prompts"; tip: page.help("clear_prompts") }
                }
            }

            // -- Layer
            ColumnLayout {
                id: pLayer
                visible: page.s.page === "Layer"
                Layout.fillWidth: true
                spacing: 6
                Section {
                    title: "Layer"
                    Layout.fillWidth: true
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        enabled: !!page.s.hasLayer
                        Caption { text: "Name:" }
                        Field { varName: "layer_name"; Layout.fillWidth: true }
                        VarCheck { varName: "layer_enabled"; text: "On"; tip: page.help("layer_enabled") }
                    }
                }
                // the mask first, then what it does to the depth (user, 2026-10-05)
                Section {
                    title: "Mask Edge"
                    Layout.fillWidth: true
                    CtlFlow { keys: page.de.tables.edge }
                    CtlCheck { key: "fill_holes"; text: "Fill holes in the mask"; tip: page.help("fill_holes") }
                }
                Section {
                    id: depthInside
                    title: "Depth Inside the Mask"
                    Layout.fillWidth: true
                    // Alter: the mask selects, the depth there is changed. Add: the
                    // mask is a new shape at its own depth (user, 2026-10-03)
                    readonly property bool adding: (page.ctl["mode"] || {}).value === "Add"
                    // Erase (user, 2026-10-05): the depth filled from around; Erase from Clip runs ROSE
                    readonly property bool erasing: (page.ctl["mode"] || {}).value === "Erase"
                    readonly property var er: page.s.erase || ({})
                    // ROSE (an object out) or Static (still reflections off glass, no model)
                    readonly property bool rose: (er.opts || {}).method !== "Static" && (er.opts || {}).method !== "Dust"
                    readonly property bool dust: (er.opts || {}).method === "Dust"
                    readonly property bool canRun: !rose || !!er.ready
                    RowLayout {
                        spacing: 8
                        enabled: !!page.s.hasLayer
                        Caption { text: "Mask:" }
                        SegmentedControl {
                            objectName: "deLayerMode"
                            compact: true
                            controlled: true
                            model: page.ch.mode || ["Alter", "Add"]
                            currentIndex: Math.max(0, (page.ch.mode || []).indexOf((page.ctl["mode"] || {}).value))
                            onActivated: (i) => page.de.setControl("mode", page.ch.mode[i])
                            HoverTip { tip: page.help("layer_mode") }
                        }
                        Btn {
                            visible: depthInside.adding
                            text: "Sample"
                            onClicked: page.de.sampleAddDepth()
                            tip: page.help("add_depth_sample")
                        }
                    }
                    // Erase has no dials: its panel takes their room, so the page still fits
                    // a 1080p screen at 0.85 (user, 2026-10-06)
                    CtlFlow {
                        visible: !depthInside.erasing
                        // Add takes all of Alter's controls (user, 2026-10-11), its Depth first
                        keys: depthInside.adding ? ["add_depth"].concat(page.de.tables.layer) : page.de.tables.layer
                    }
                    // Alter's Despeck (user, 2026-10-12): DepthCrafter's left-edge specks in the mask
                    // lowered to the object's own depth; Strength beside its switch
                    RowLayout {
                        objectName: "deDespeckRow"
                        visible: !depthInside.adding && !depthInside.erasing
                        spacing: 8
                        CtlCheck {
                            objectName: "deDespeckOn"
                            key: "despeck_on"
                            text: "Despeck"
                            tip: page.help("despeck_on")
                        }
                        // Level to edge (user, 2026-10-12): a particle's white dot down to the grey round its edge
                        CtlCheck {
                            objectName: "deLevelEdge"
                            key: "level_edge"
                            text: "Level to edge"
                            tip: page.help("level_edge")
                        }
                        Ctl {
                            ctlKey: "despeck_strength"
                            width: 76
                            enabled: !!(page.ctl["despeck_on"] || {}).value || !!(page.ctl["level_edge"] || {}).value
                            opacity: enabled ? 1 : Theme.dim(0.45)
                        }
                    }
                    ColumnLayout {
                        objectName: "deErasePanel"
                        visible: depthInside.erasing
                        Layout.fillWidth: true
                        spacing: 6
                        Muted {
                            visible: !depthInside.er.erased
                            text: "The depth inside the mask is filled from around it. Erase from Clip takes the object out of the "
                                  + "clip itself with ROSE -- every Erase layer at once."
                            font.pixelSize: Theme.smallSize + 1
                        }
                        // once erased: the object put back in 3D by Merging, at its own depth
                        // (user, 2026-10-06; core.removal.putback) -- in the explanation's place
                        RowLayout {
                            objectName: "dePutBack"
                            readonly property var pb: depthInside.er.putback || ({})
                            visible: !!depthInside.er.erased
                            enabled: !depthInside.er.task
                            spacing: 6
                            AnimCheck {
                                objectName: "dePutBackOn"
                                text: "Put back"
                                checked: !!parent.pb.enabled
                                onToggled: page.de.setPutBack("enabled", checked)
                                HoverTip { tip: page.help("putback") }
                            }
                            SegmentedControl {
                                objectName: "dePutBackMode"
                                compact: true
                                controlled: true
                                enabled: !!parent.pb.enabled
                                model: parent.pb.modes || ["Add", "Over"]
                                currentIndex: Math.max(0, model.indexOf(parent.pb.mode))
                                onActivated: (i) => page.de.setPutBack("mode", model[i])
                                HoverTip { tip: page.help("putback_mode") }
                            }
                            TextField {
                                id: pbPar
                                objectName: "dePutBackParallax"
                                readonly property string parShown: (Number(parent.pb.parallax || 0) >= 0 ? "+" : "") + Number(parent.pb.parallax || 0).toFixed(2)
                                enabled: !!parent.pb.enabled
                                implicitWidth: 58
                                implicitHeight: Theme.rowHeight
                                text: parShown
                                color: Theme.fg
                                font.family: Theme.bodyFont
                                font.pixelSize: Theme.bodySize
                                horizontalAlignment: TextInput.AlignHCenter
                                background: Rectangle { radius: 6; color: Theme.field; border.width: 1; border.color: pbPar.activeFocus ? Theme.accent : Theme.border }
                                onEditingFinished: if (text !== parShown) page.de.setPutBack("parallax", text)
                                onParShownChanged: text = parShown
                                HoverTip { tip: page.help("putback_parallax") }
                            }
                            Caption { text: "%"; font.pixelSize: Theme.smallSize + 1 }
                            // Sample's depth factor: x1 on the surface, x0.5 inside it (a mirror image)
                            TextField {
                                id: pbFac
                                objectName: "dePutBackFactor"
                                readonly property string facShown: "×" + Number(parent.pb.factor === undefined ? 1 : parent.pb.factor).toFixed(1)
                                enabled: !!parent.pb.enabled
                                implicitWidth: 44
                                implicitHeight: Theme.rowHeight
                                text: facShown
                                color: Theme.fg
                                font.family: Theme.bodyFont
                                font.pixelSize: Theme.bodySize
                                horizontalAlignment: TextInput.AlignHCenter
                                background: Rectangle { radius: 6; color: Theme.field; border.width: 1; border.color: pbFac.activeFocus ? Theme.accent : Theme.border }
                                onEditingFinished: if (text !== facShown) page.de.setPutBack("factor", text)
                                onFacShownChanged: text = facShown
                                HoverTip { tip: page.help("putback_factor") }
                            }
                            AccentButton {
                                objectName: "dePutBackSample"
                                text: "Sample"
                                kind: "outline"
                                enabled: !!parent.pb.enabled
                                onClicked: page.de.samplePutBack()
                                HoverTip { tip: page.help("putback_sample") }
                            }
                        }
                        RowLayout {
                            spacing: 6
                            visible: depthInside.rose && !depthInside.er.ready
                            Muted { text: "ROSE isn't set up on this PC."; Layout.fillWidth: false }
                            AccentButton {
                                objectName: "deRoseSetup"
                                text: "Set Up ROSE"
                                kind: "outline"
                                enabled: !depthInside.er.task
                                onClicked: page.prompt.ask({
                                    title: "Set up ROSE?",
                                    message: "This clones ROSE into weights/ROSE, builds its own Python environment there and downloads about "
                                             + (depthInside.er.downloadGb || 3.7) + " GB of weights (plus ~5 GB of packages). It needs git and uv.",
                                    buttons: [{ text: "Set Up", code: 1, kind: "filled" }, { text: "Cancel", code: 0, kind: "ghost" }],
                                    onDone: (code) => { if (code === 1) page.de.setupRose() }
                                })
                                HoverTip { tip: page.help("rose_setup") }
                            }
                        }
                        RowLayout {
                            spacing: 6
                            AccentButton {
                                objectName: "deEraseFromClip"
                                visible: depthInside.canRun     // the Method beside it stays: Static needs no ROSE
                                text: "Erase from Clip"
                                kind: "filled"
                                enabled: !depthInside.er.task && !page.s.busy && (depthInside.er.layers || 0) > 0 && !!depthInside.er.clip
                                onClicked: page.prompt.ask({
                                    title: "Erase from the clip?",
                                    message: (depthInside.rose
                                              ? "ROSE takes the object" + ((depthInside.er.layers || 0) > 1 ? "s of " + depthInside.er.layers + " Erase layers" : "")
                                                + " out of the clip, on the frames where the mask is."
                                              : depthInside.dust
                                                ? "The dust on the glass inside the mask comes off the clip (the camera riding with the glass: a car's interior). Merging's Put Back lays it back on the glass."
                                                : "The reflections that hold still on the glass inside the mask come off the clip (for a locked-off shot).")
                                             + " The clip is replaced in place -- Splatting and "
                                             + "DepthCrafter will read the erased one -- and the original is kept in edit_history beside it.\n\n"
                                             + (depthInside.rose ? "It takes minutes per 81 frames, and the GPU is ROSE's meanwhile."
                                                : depthInside.dust ? "It takes about as long as reading the clip twice." : "It takes seconds; no GPU."),
                                    buttons: [{ text: "Erase", code: 1, kind: "filled" }, { text: "Cancel", code: 0, kind: "ghost" }],
                                    onDone: (code) => { if (code === 1) page.de.eraseFromClip() }
                                })
                                HoverTip { tip: page.help("erase_from_clip") }
                            }
                            // the method beside the button (user, 2026-10-06), so the page keeps its height
                            Caption { text: "Method"; font.pixelSize: Theme.smallSize + 1 }
                            Dropdown {
                                objectName: "deEraseMethod"
                                boxWidth: 84
                                enabled: !depthInside.er.task
                                model: depthInside.er.methods || ["ROSE", "Static", "Dust"]
                                currentIndex: Math.max(0, model.indexOf((depthInside.er.opts || {}).method))
                                onActivated: (i) => page.de.setEraseOption("method", model[i])
                                HoverTip { tip: page.help("erase_method") }
                            }
                        }
                        Muted {
                            visible: !!depthInside.er.erased && !depthInside.er.task
                            text: "Erased; the original is in edit_history."
                            font.pixelSize: Theme.smallSize + 1
                        }
                        // ROSE's settings (user, 2026-10-06), kept between sessions
                        Flow {
                            objectName: "deEraseOptions"
                            // always: the Method choice is how a PC without ROSE gets to Static
                            enabled: !depthInside.er.task
                            Layout.fillWidth: true
                            spacing: 6
                            Repeater {
                                model: depthInside.rose
                                       ? [{ key: "steps", label: "Steps", tip: "erase_steps" },
                                          { key: "seed", label: "Seed", tip: "erase_seed" },
                                          { key: "grow", label: "Grow px", tip: "erase_grow" },
                                          { key: "feather", label: "Feather px", tip: "erase_feather" }]
                                       : depthInside.dust ? [{ key: "dust_size", label: "Size px", tip: "erase_dust_size" }]
                                       : [{ key: "static_size", label: "Size px", tip: "erase_static_size" }]
                                RowLayout {
                                    required property var modelData
                                    spacing: 4
                                    Caption { text: modelData.label; font.pixelSize: Theme.smallSize + 1 }
                                    TextField {
                                        id: ef
                                        objectName: "deErase_" + parent.modelData.key
                                        readonly property string shown: String(((depthInside.er.opts || {})[parent.modelData.key]) ?? "")
                                        implicitWidth: parent.modelData.key === "seed" ? 74 : 48
                                        implicitHeight: Theme.rowHeight
                                        text: shown
                                        color: Theme.fg
                                        font.family: Theme.bodyFont
                                        font.pixelSize: Theme.bodySize
                                        horizontalAlignment: TextInput.AlignHCenter
                                        validator: IntValidator { bottom: 0 }
                                        background: Rectangle { radius: 6; color: Theme.field; border.width: 1; border.color: ef.activeFocus ? Theme.accent : Theme.border }
                                        onEditingFinished: if (text !== shown) page.de.setEraseOption(parent.modelData.key, text)
                                        onShownChanged: text = shown
                                        HoverTip { tip: page.help(parent.parent.modelData.tip) }
                                    }
                                }
                            }
                            RowLayout {
                                visible: depthInside.rose
                                spacing: 4
                                Caption { text: "Size"; font.pixelSize: Theme.smallSize + 1 }
                                Dropdown {
                                    objectName: "deEraseSize"
                                    boxWidth: 74
                                    model: depthInside.er.sizes || []
                                    currentIndex: model.indexOf((depthInside.er.opts || {}).size)
                                    onActivated: (i) => page.de.setEraseOption("size", model[i])
                                    HoverTip { tip: page.help("erase_size") }
                                }
                            }
                            RowLayout {
                                visible: depthInside.rose
                                spacing: 4
                                Caption { text: "Window"; font.pixelSize: Theme.smallSize + 1 }
                                Dropdown {
                                    objectName: "deEraseWindow"
                                    boxWidth: 60
                                    model: depthInside.er.windows || []
                                    currentIndex: model.indexOf(String((depthInside.er.opts || {}).window))
                                    onActivated: (i) => page.de.setEraseOption("window", model[i])
                                    HoverTip { tip: page.help("erase_window") }
                                }
                            }
                            // user, 2026-10-15: a big object pre-filled first; a sharper full-size pass
                            RowLayout {
                                visible: depthInside.rose
                                spacing: 4
                                Caption { text: "Pre-fill"; font.pixelSize: Theme.smallSize + 1 }
                                Dropdown {
                                    objectName: "deErasePrefill"
                                    boxWidth: 70
                                    model: depthInside.er.prefills || []
                                    currentIndex: model.indexOf((depthInside.er.opts || {}).prefill)
                                    onActivated: (i) => page.de.setEraseOption("prefill", model[i])
                                    HoverTip { tip: page.help("erase_prefill") }
                                }
                            }
                            AnimCheck {
                                objectName: "deEraseRefine"
                                visible: depthInside.rose
                                text: "Refine at full size"
                                checked: !!(depthInside.er.opts || {}).refine
                                onToggled: page.de.setEraseOption("refine", checked)
                                HoverTip { tip: page.help("erase_refine") }
                            }
                        }
                        RowLayout {
                            visible: !!depthInside.er.task
                            spacing: 6
                            Layout.fillWidth: true
                            ProgressBar {
                                objectName: "deEraseProgress"
                                Layout.fillWidth: true
                                from: 0; to: 1; value: depthInside.er.progress || 0
                                background: Rectangle { implicitHeight: 6; radius: 3; color: Theme.trough }
                                contentItem: Item { Rectangle { width: parent.parent.visualPosition * parent.width; height: 6; radius: 3; color: Theme.accent } }
                            }
                            AccentButton { text: "Stop"; kind: "ghost"; onClicked: page.de.cancelErase() }
                        }
                        Muted {
                            objectName: "deEraseStatus"
                            visible: !!depthInside.er.task
                            text: depthInside.er.text || ""
                            elide: Text.ElideRight
                            wrapMode: Text.NoWrap
                            font.pixelSize: Theme.smallSize + 1
                        }
                    }
                }
                Section {
                    title: "Frames"
                    Layout.fillWidth: true
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 6
                        enabled: !!page.s.hasLayer
                        Caption { text: page.s.range || "Frames: all"; Layout.fillWidth: true }
                        AccentButton { text: "In"; kind: "outline"; onClicked: page.de.setRange("start"); HoverTip { tip: page.help("frame_range") } }
                        AccentButton { text: "Out"; kind: "outline"; onClicked: page.de.setRange("end"); HoverTip { tip: page.help("frame_range") } }
                        AccentButton { text: "All"; kind: "outline"; onClicked: page.de.setRange("clear"); HoverTip { tip: page.help("frame_range") } }
                    }
                    // keyframes for this layer (user, 2026-10-05): S static, D dynamic -- one row
                    // with the range, so the page still fits the 1080p screen at 85%
                    RowLayout {
                        objectName: "deKeyframes"
                        readonly property int nKeys: (page.s.keyframes || []).length
                        Layout.fillWidth: true
                        spacing: 4
                        enabled: !!page.s.hasLayer
                        Caption {
                            objectName: "deKeyStatus"
                            text: parent.nKeys === 0 ? "Keys: none"
                                  : page.s.onKey ? "On a " + page.s.onKey + " key"
                                  : parent.nKeys + (parent.nKeys === 1 ? " key" : " keys")
                            color: page.s.onKey === "dynamic" ? "#ffb347" : page.s.onKey === "static" ? "#4fa8ff" : Theme.fgMuted
                            font.pixelSize: Theme.smallSize + 1
                            elide: Text.ElideRight
                            Layout.fillWidth: true
                            HoverTip {
                                tip: !page.s.hasLayer ? "Keys belong to a layer: pick or make one first."
                                     : parent.parent.nKeys === 0 ? "No keys: this layer's settings hold on every frame. S (static) or D (dynamic) keys them on this frame."
                                     : page.s.onKey ? "On a " + page.s.onKey + " key: a change to a setting changes this key."
                                     : "Between keys the settings follow them. A change here isn't kept until you add a key (S or D)."
                            }
                        }
                        AccentButton {
                            objectName: "deAddStatic"; text: "S"; kind: "outline"; implicitWidth: 34
                            onClicked: page.de.addKeyframe("static")
                            HoverTip { tip: page.help("key_static") }
                        }
                        AccentButton {
                            objectName: "deAddDynamic"; text: "D"; kind: "outline"; implicitWidth: 34
                            onClicked: page.de.addKeyframe("dynamic")
                            HoverTip { tip: page.help("key_dynamic") }
                        }
                        AccentButton {
                            text: "‹"; kind: "ghost"; implicitWidth: 28
                            enabled: (page.s.keyframes || []).some(k => k.frame < (page.s.frame || 0))
                            onClicked: page.de.jumpKeyframe(-1)
                            HoverTip { tip: "Previous key" }
                        }
                        AccentButton {
                            text: "›"; kind: "ghost"; implicitWidth: 28
                            enabled: (page.s.keyframes || []).some(k => k.frame > (page.s.frame || 0))
                            onClicked: page.de.jumpKeyframe(1)
                            HoverTip { tip: "Next key" }
                        }
                        // the shaper (user, 2026-10-06: as Splatting's) -- in a popup: the
                        // Layer page has no height to spare at 85% on the 1080p screen
                        AccentButton {
                            id: shapeBtn
                            objectName: "deKeyShapeBtn"; text: "∿"; kind: "ghost"; implicitWidth: 30
                            enabled: deShaper.seg.ok
                            onClicked: shapePop.opened ? shapePop.close() : shapePop.open()
                            HoverTip { tip: "Shape: how the values travel into the dynamic key the playhead is heading for -- a ramp, an ease, an LFO, or your own points and bends." }
                            Popup {
                                id: shapePop
                                objectName: "deShaperPopup"
                                y: -height - 4                       // upward: the row sits at the window's foot
                                x: Math.min(0, shapeBtn.width - width)
                                width: 380
                                padding: 12
                                transformOrigin: Popup.Bottom
                                enter: Transition {
                                    ParallelAnimation {
                                        NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.dur(140) }
                                        NumberAnimation { property: "scale"; from: 0.9; to: 1; duration: Theme.dur(240); easing.type: Easing.OutCubic }
                                    }
                                }
                                exit: Transition { NumberAnimation { property: "opacity"; to: 0; duration: Theme.dur(100) } }
                                background: Rectangle {
                                    PopupGrain { radius: 10 }
                                    radius: 10
                                    color: Theme.field
                                    border.width: 1
                                    border.color: Theme.borderStrong
                                }
                                contentItem: KeyShaper {
                                    id: deShaper
                                    pv: page.de
                                    keyframes: page.s.keyframes || []
                                    frame: page.s.frame || 0
                                }
                            }
                        }
                        AccentButton {
                            objectName: "deDeleteKey"; text: "Delete"; kind: "ghost"
                            enabled: !!page.s.onKey
                            onClicked: page.de.removeKeyframe()
                            HoverTip { tip: "Removes the key on this frame." }
                        }
                        AccentButton {
                            objectName: "deClearKeys"; text: "Clear"; kind: "ghost"
                            enabled: parent.nKeys > 0
                            onClicked: page.prompt.ask({
                                title: "Clear all keys?",
                                message: "Every key on this layer goes; its settings hold on every frame again, as they are now on the Layer page.",
                                buttons: [{ text: "Clear Keys", code: 1, kind: "filled" }, { text: "Cancel", code: 0, kind: "ghost" }],
                                onDone: (code) => { if (code === 1) page.de.clearKeyframes() }
                            })
                            HoverTip { tip: "Removes every key on this layer." }
                        }
                    }
                }
            }

            // -- Gradient
            ColumnLayout {
                id: pGradient
                visible: page.s.page === "Gradient"
                Layout.fillWidth: true
                spacing: 6
                Section {
                    title: "Gradient"
                    Layout.fillWidth: true
                    CtlCheck { key: "gradient.enabled"; text: "Paint a depth gradient inside the mask"; tip: page.help("gradient_enabled") }
                    RowLayout {
                        spacing: 8
                        enabled: !!page.s.hasLayer
                        Caption { text: "Shape:"; Layout.preferredWidth: 60 }
                        SegmentedControl {
                            compact: true; controlled: true
                            model: page.ch.shape || []
                            currentIndex: (page.ch.shape || []).indexOf((page.ctl["gradient.shape"] || {}).value)
                            onActivated: (i) => page.de.setControl("gradient.shape", page.ch.shape[i])
                            HoverTip { tip: page.help("gradient_shape") }
                        }
                    }
                    RowLayout {
                        spacing: 8
                        enabled: !!page.s.hasLayer
                        Caption { text: "Blend:"; Layout.preferredWidth: 60 }
                        Dropdown {
                            model: page.ch.blend || []
                            boxWidth: 150
                            currentIndex: (page.ch.blend || []).indexOf((page.ctl["gradient.blend"] || {}).value)
                            onActivated: (i) => page.de.setControl("gradient.blend", page.ch.blend[i])
                            HoverTip { tip: page.help("gradient_blend") }
                        }
                    }
                    CtlFlow { keys: page.de.tables.gradient }
                    CtlCheck { key: "gradient.follow_object"; text: "Follow the object as it moves"; tip: page.help("gradient_follow") }
                    Flow {      // a Flow: the three ran past the rail (manual shot, 2026-10-10)
                        Layout.fillWidth: true
                        spacing: 6
                        enabled: !!page.s.hasLayer
                        Btn { op: "_sample_gradient"; text: "Sample From Object"; tip: page.help("gradient_sample") }
                        Btn { op: "_swap_gradient"; text: "Swap Ends" }
                        Btn { op: "_reset_gradient"; text: "Reset Direction" }
                    }
                    Muted { text: "Pick the Gradient tool and drag on the picture from the NEAR end of the object to its FAR "
                                  + "end -- e.g. from the bottom of a road to where it meets the horizon." }
                }
            }

            // -- Global
            ColumnLayout {
                id: pGlobal
                visible: page.s.page === "Global"
                Layout.fillWidth: true
                spacing: 6
                Section {
                    title: "Whole Frame"
                    Layout.fillWidth: true
                    CtlCheck { key: "global.invert"; text: "Invert (near <-> far)"; tip: page.help("global_invert") }
                    CtlFlow { keys: page.de.tables.global }
                    // the Global Despeck (user, 2026-10-12): Alter's Despeck on the whole frame
                    RowLayout {
                        objectName: "deGlobalDespeckRow"
                        spacing: 8
                        CtlCheck {
                            objectName: "deGlobalDespeckOn"
                            key: "global.despeck_on"
                            text: "Despeck the whole frame"
                            tip: page.help("global_despeck")
                        }
                    }
                    // its dials, under it (user, 2026-10-12): how far, what it takes, how far in, which edges
                    CtlFlow {
                        objectName: "deGlobalDespeckDials"
                        keys: ["global.despeck_strength", "global.despeck_threshold", "global.despeck_reach", "global.despeck_step"]
                        enabled: !!(page.ctl["global.despeck_on"] || {}).value
                        opacity: enabled ? 1 : Theme.dim(0.45)
                    }
                    // its zone, a row of its own (user, 2026-10-12: the borders together)
                    CtlFlow {
                        objectName: "deGlobalDespeckZone"
                        keys: ["global.despeck_left", "global.despeck_right", "global.despeck_top", "global.despeck_bottom"]
                        enabled: !!(page.ctl["global.despeck_on"] || {}).value
                        opacity: enabled ? 1 : Theme.dim(0.45)
                    }
                    // Local Match (user, 2026-10-13): a locked-off shot's coarse depth matched to one frame
                    RowLayout {
                        objectName: "deMatchRow"
                        spacing: 8
                        CtlCheck {
                            objectName: "deMatchOn"
                            key: "global.match_on"
                            text: "Local Match (locked-off shots)"
                            tip: page.help("global_match")
                        }
                        AccentButton {
                            objectName: "deMatchUse"
                            text: "Use This Frame"
                            kind: "outline"
                            // not mid-job, as Reset (bug sweep 2026-10-14c)
                            enabled: !!(page.ctl["global.match_on"] || {}).value && !page.working
                            onClicked: page.de.matchUseThisFrame()
                            HoverTip { tip: page.help("global_match_frame") }
                        }
                        Caption {
                            objectName: "deMatchFrame"
                            text: "Reference: frame " + ((((page.ctl["global.match_frame"] || {}).value) || 0) + 1)
                            opacity: (page.ctl["global.match_on"] || {}).value ? 1 : Theme.dim(0.45)
                        }
                    }
                    CtlFlow {
                        objectName: "deMatchDials"
                        keys: ["global.match_strength"]
                        enabled: !!(page.ctl["global.match_on"] || {}).value
                        opacity: enabled ? 1 : Theme.dim(0.45)
                    }
                    Btn { op: "_reset_global"; text: "Reset Whole Frame" }
                }
            }

            // -- Output
            ColumnLayout {
                id: pOutput
                visible: page.s.page === "Output"
                Layout.fillWidth: true
                spacing: 6
                Section {
                    id: outputSection
                    title: "Output"
                    Layout.fillWidth: true
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Caption { text: "Folder:" }
                        Field { varName: "output_dir"; Layout.fillWidth: true; HoverTip { tip: page.help("output_dir") } }
                        AccentButton { text: "Browse..."; kind: "outline"; onClicked: outDlg.open() }
                    }
                    VarCheck { varName: "handoff"; text: "Offer the result to Splatting's import button"; tip: page.help("send_to_splatting") }
                    VarCheck { objectName: "deRenderHires"; varName: "render_hires"; text: "Render at source resolution"; tip: page.help("render_hires") }
                    RowLayout {
                        spacing: 6
                        Btn { op: "_save_project"; text: "Save Project"; tip: page.help("save_project") }
                        Btn { objectName: "deRender"; op: "_render"; text: "Render Edited Depth"; kind: "filled"; enabled: page.s.render_btn !== false; tip: page.help("render") }
                        Btn { op: "_request_stop"; text: "Stop"; enabled: !!page.s.render_stop_btn }
                    }
                    ProgressBar {
                        Layout.fillWidth: true
                        from: 0; to: 100; value: page.s.renderProgress || 0
                        background: Rectangle { implicitHeight: 6; radius: 3; color: Theme.trough }
                        contentItem: Item { Rectangle { width: parent.parent.visualPosition * parent.width; height: 6; radius: 3; color: Theme.accent } }
                    }
                }
            }
        }
    }

    // drag and drop (user, 2026-10-25): Drop Zones over the clip and the output folder
    DropZones {
        over: clipSection
        targets: [{ key: "rgb_path", label: "Source Video", files: ["mp4", "mkv", "mov", "avi", "m4v", "webm"],
                    set: p => page.de.setVar("rgb_path", p) },
                  { key: "depth_path", label: "Depth Map", files: ["mp4", "mkv"],
                    set: p => page.de.setVar("depth_path", p) }]
    }
    DropZones {
        over: outputSection
        targets: [{ key: "output_dir", label: "Output Folder", folder: true,
                    set: p => page.de.setVar("output_dir", p) }]
    }
}
