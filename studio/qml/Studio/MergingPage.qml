import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Layouts
import Studio
import "glasses.js" as Glasses
import "tips.js" as Tips

// Merging, v2. v1's arrangement (merging_gui.create_widgets), section for
// section:
//
//   Folders   Inpainted / Original / Mask [Browse Folder][Select File], Output
//             [Encoding Options...]  Use Preset: [8-bit|10-bit]
//   the preview: scrubber, v1's two control lines (Preview Brightness on
//   the second), the picture
//   Mask Processing Parameters  | Film Grain (Inpainted Fills) | Options
//   (8 sliders, Highlight,      | Current Clip Info            |
//    Reset to Defaults)         |                              |
//   Progress
//   [Start Blending] [Stop] [Process Current Clip] [Move to Finish]   [View Output Folder]
//
// The batch is v1's own (stages/merging/engine.py, generated), and so is the
// preview (stages/merging/preview.py), with Save Preview, Move to Finish and
// the Grain Match Preview window (GrainMatchWindow.qml, grain.py) on it.
Item {
    id: page
    property bool active: false
    property real flipAngle: 0
    property var prompt: null
    property var consoleRef: null

    readonly property var mg: Merging
    readonly property var st: mg.settings
    // Studio > Use Beginner Mode: v1's rule key -> hidden (beginner.py).
    readonly property var bh: Beginner.hide
    readonly property var shadowRules: ({ shadow_shift: "merge.shadow_shift", shadow_decay_gamma: "merge.shadow_gamma",
                                          shadow_start_opacity: "merge.shadow_start", shadow_opacity_decay: "merge.shadow_decay",
                                          shadow_min_opacity: "merge.shadow_min" })
    readonly property bool idle: !mg.busy && !mg.loading
    property bool foldersOpen: false
    // Preview Scale's Fit, as Splatting's (user, 2026-10-18: the default): on until a percentage is picked
    property bool previewFitOn: true
    readonly property bool chapterMode: mg.hasProject && mg.chapterNum >= 0
    // Manual paths: put away by Hide Folders, remembered (user, 2026-09-30)
    readonly property bool manualHidden: !!Shell.foldersHidden["merging"]
    readonly property bool pathsFolded: chapterMode ? !foldersOpen : manualHidden
    function toggleFolders() {
        if (chapterMode) foldersOpen = !foldersOpen
        else Shell.setFoldersHidden("merging", !manualHidden)
    }
    readonly property var pv: mg.previewer
    property int ffStep: 5               // v1 fast_forward_step_var: session-only
    // The picture: never under 150 (the window's minimum), 360 when the
    // screen has the room (the fit's preferredHeight); a taller window gives
    // it the rest.
    readonly property int minPictureHeight: 150
    readonly property int preferredPictureHeight: 360

    // Two shapes, as Splatting (user, 2026-09-26):
    //   wide:     left rail (mask, shadow) | the preview, full height | right
    //             rail (film grain, clip info, options in two columns)
    //   compact:  the preview across the top, the columns under it
    // Wide whenever the page is wide enough for both rails and the preview's
    // control lines; compact is the narrowest nothing clips at (measured).
    readonly property int compactWidth: 1220
    readonly property int leftRail: 380
    readonly property int rightRail: 400
    readonly property real wideWidth: leftRail + rightRail + 2 * Theme.gap + 36 + previewPanel.minWidth
    // The screen's room, in page units (the window's zoom taken out).
    readonly property real roomWidth: (Screen.width || 1920) / (Window.window && Window.window.zoom ? Window.window.zoom : 1) - 24
    readonly property bool wide: width >= wideWidth - 1
    readonly property int requiredWidth: roomWidth >= wideWidth ? Math.ceil(wideWidth) : compactWidth
    readonly property real requiredHeight: content.y + content.implicitHeight + Theme.gap + actionBar.implicitHeight + 12
    // The picture at its preferred height; wide, the rails may already give it that.
    readonly property real preferredHeight: requiredHeight + (wide
            ? Math.max(0, previewPanel.implicitHeight + preferredPictureHeight - minPictureHeight - body.implicitHeight)
            : preferredPictureHeight - minPictureHeight)

    transform: Rotation {
        origin.x: page.width / 2; origin.y: page.height / 2
        axis { x: 0; y: 1; z: 0 }
        angle: page.flipAngle
    }

    component MBox: TextBox { store: page.st; owner: page.mg }
    component MCheck: CheckField { store: page.st }
    component MSlider: SliderRow {
        store: page.st; labelWidth: 170
        // Held: each change renders at once, newest wins (preview.py).
        onPressedChanged: page.pv.setDragging(pressed)
    }
    component MDial: DialField {
        store: page.st
        onPressedChanged: page.pv.setDragging(pressed)
    }
    // One of maskSliders' entries as a dial (the model row is modelData).
    component MaskDial: MDial {
        required property var modelData
        visible: !page.bh[page.shadowRules[modelData.key] || ""]
        key: modelData.key; label: modelData.label
        from: modelData.from; to: modelData.to; step: modelData.step
        decimals: modelData.decimals; defaultValue: modelData.defaultValue
        help: page.help({ mask_binarize_threshold: "binarize_thresh_(<0=off)",
                          mask_dilate_kernel_size: "dilate_kernel", mask_blur_kernel_size: "blur_kernel",
                          shadow_shift: "shadow_shift", shadow_decay_gamma: "shadow_gamma",
                          shadow_start_opacity: "shadow_opacity_start",
                          shadow_opacity_decay: "shadow_opacity_decay",
                          shadow_min_opacity: "shadow_opacity_min" }[modelData.key])
    }
    // Current Clip Info: in the left rail when wide, under Film Grain compact.
    component MergeClipInfo: ClipInfo {
        Layout.fillWidth: true
        labelWidth: 110
        consoleRef: page.consoleRef
        rows: [
            { label: "File:", value: page.mg.clipName },
            { label: "Res:", value: page.mg.clipResolution },
            { label: "Frames:", value: page.mg.clipFrames, help: page.help("current_total_frames") },
            { label: "Borders:", value: page.mg.clipBorders },
            { label: "Flip:", value: page.mg.clipFlip }
        ]
    }
    component MDrop: Dropdown { store: page.st }
    component Caption: Text {
        color: parent && parent.enabled === false ? Theme.fgFaint : Theme.fg
        font.family: Theme.bodyFont
        font.pixelSize: Theme.bodySize
        Layout.alignment: Qt.AlignVCenter
    }
    function help(k) { return page.mg.help[k] || "" }

    onActiveChanged: {
        if (active) {
            mg.refreshImport(); mg.refreshChapters()
            // the clip list picks up new files each time the stage is shown
            if (idle && pv && pv.jobClips) pv.jobClips.rescan()
        }
        else pv.pause()                  // v1: playback never runs off-screen
    }

    // ---- v1's preview keys (video_previewer: arrows, Ctrl+arrows, Up/Down,
    // Space, Shift+Space), only while this page shows, this window has the
    // focus (not Full Preview) and no text field is being typed in.
    readonly property bool keysLive: active && pv.loaded && Window.window === Shell.focusWindow && !(Window.activeFocusItem
                                     && Window.activeFocusItem.hasOwnProperty("cursorPosition"))
    // Full Preview, full screen on Studio's screen (user, 2026-10-13); Escape / F11 there close it
    function openFullPreview() { fullPreview.openFull(page.Window.window ? page.Window.window.screen : null) }
    Shortcut { objectName: "fullPreviewShortcut"; enabled: page.keysLive; sequence: "F11"; onActivated: page.openFullPreview() }
    Shortcut { enabled: page.keysLive; sequence: "Left"; onActivated: page.pv.stepFrames(-1) }
    Shortcut { enabled: page.keysLive; sequence: "Right"; onActivated: page.pv.stepFrames(1) }
    Shortcut { enabled: page.keysLive; sequence: "Shift+Left"; onActivated: page.pv.stepFrames(-10) }
    Shortcut { enabled: page.keysLive; sequence: "Shift+Right"; onActivated: page.pv.stepFrames(10) }
    Shortcut { enabled: page.keysLive; sequence: "Ctrl+Left"; onActivated: page.pv.step(-1) }
    Shortcut { enabled: page.keysLive; sequence: "Ctrl+Right"; onActivated: page.pv.step(1) }
    Shortcut { enabled: page.keysLive; sequence: "Up"; onActivated: page.pv.step(1) }
    Shortcut { enabled: page.keysLive; sequence: "Down"; onActivated: page.pv.step(-1) }
    Shortcut { enabled: page.keysLive; sequence: "Space"; onActivated: page.pv.togglePlay() }
    Shortcut { enabled: page.keysLive; sequence: "Shift+Space"; onActivated: page.pv.toggleFastForward(page.ffStep) }
    Shortcut { enabled: page.keysLive; sequence: "Home"; onActivated: page.pv.seek(0) }   // first / last frame (2026-10-02)
    Shortcut { enabled: page.keysLive; sequence: "End"; onActivated: page.pv.seek(Math.max(0, page.pv.frameCount - 1)) }
    FullPreviewWindow {
        id: fullPreview; pv: page.pv; provider: "mergepreview"
        stage: "merging"; glasses: String(page.st.preview_source) === "Glasses-Free 3D"
    }
    GrainMatchWindow { id: grainWin; objectName: "grainWin"; pv: page.pv; st: page.st; prompt: page.prompt }

    // File > Save Preview Frame / as SBS: v1 suggests the name, QML asks where.
    property bool saveSbs: false
    FileDialog {
        id: savePreviewDlg
        objectName: "savePreviewDlg"
        fileMode: FileDialog.SaveFile
        defaultSuffix: "png"
        nameFilters: ["PNG Image (*.png)", "JPEG Image (*.jpg)", "All Files (*)"]
        onAccepted: page.pv.saveTo(page.saveSbs, Shell.urlToPath(selectedFile))
    }
    Connections {
        target: page.pv
        function onSaveSuggested(sbs, name) {
            page.saveSbs = sbs
            savePreviewDlg.title = sbs ? "Save SBS Preview Frame As..." : "Save Preview Frame As..."
            const folder = String(page.st.output_folder || "")
            savePreviewDlg.currentFolder = Shell.pathToUrl(folder)
            savePreviewDlg.selectedFile = Shell.fileUrl(folder, name)
            savePreviewDlg.open()
        }
        // v1 _move_current_to_finish's question.
        function onMoveAsked(text) {
            page.confirm("Confirm Move", text, "Move", () => page.pv.moveToFinish(true))
        }
    }

    Connections {
        target: page.mg
        function onToast(kind, title, message) { Theme.toast(kind, title, message) }
        function onMessage(kind, title, text) {
            page.prompt.ask({ title: title, message: text, buttons: [{ text: "OK", code: 1, kind: "filled" }] })
        }
        // no original for some clips (user, 2026-10-09): ask before merging them right eye only
        function onMissingOriginals(title, text) {
            page.prompt.ask({
                title: title, message: text,
                buttons: [{ text: "Continue (right eye only)", code: 1, kind: "filled" },
                          { text: "Cancel", code: 0, kind: "ghost" }],
                onDone: (code) => { if (code === 1) page.mg.continueWithoutOriginals(); else page.mg.cancelWithoutOriginals() }
            })
        }
    }
    function confirm(title, message, yesText, action) {
        page.prompt.ask({
            title: title, message: message,
            buttons: [{ text: yesText, code: 1, kind: "filled" }, { text: "Cancel", code: 0, kind: "ghost" }],
            onDone: (code) => { if (code === 1) action() }
        })
    }

    // ---- dialogs ------------------------------------------------------------
    // One picker, pointed at whichever row asked (user, 2026-10-05: FolderPicker,
    // Columns): a row that takes a single clip too lets a clip be picked.
    property string browseKey: ""
    // Put Back's Original Eyes: the original clip's merge (user, 2026-10-16)
    FolderPicker {
        id: putBackEyesDlg
        title: "The original clip's merge: its folder, or one of its eye files"
        allowFiles: true
        fileTypes: ["mov", "mp4", "mkv", "avi"]
        currentFolder: (page.mg.putBack || {}).eyes || ""
        onAccepted: page.mg.setPutBack("eyes", selectedPath)
    }
    FolderPicker {
        id: folderDlg
        fileTypes: ["mp4", "mov", "mkv"]
        currentFolder: page.st[page.browseKey] || ""
        onAccepted: page.st[page.browseKey] = selectedPath.replace(/\\/g, "/")
    }
    function browse(key, title, files) {
        browseKey = key; folderDlg.title = title; folderDlg.allowFiles = !!files; folderDlg.open()
    }
    FileDialog {
        id: loadSettingsDlg; title: "Load Settings from File"
        nameFilters: ["Merge Config Files (*.mergecfg)", "All files (*)"]
        onAccepted: page.mg.loadSettingsFrom(Shell.urlToPath(selectedFile))
    }
    FileDialog {
        id: saveSettingsDlg; title: "Save Settings to File"
        fileMode: FileDialog.SaveFile; defaultSuffix: "mergecfg"
        nameFilters: ["Merge Config Files (*.mergecfg)", "All files (*)"]
        onAccepted: page.mg.saveSettingsTo(Shell.urlToPath(selectedFile))
    }
    EncodingDialog { id: encodingDlg; ctl: page.mg; mergeAll: true }
    MergeAllDialog { id: mergeAllDlg; mg: page.mg }

    component PathRow: FieldRow {
        id: pr
        property string key
        property string fileTitle: ""
        labelWidth: 170
        visible: !page.pathsFolded
        RowLayout {
            width: parent.width
            spacing: 8
            MBox { key: pr.key; numeric: false; Layout.fillWidth: true }
            AccentButton {
                objectName: "browsePath"
                text: "Browse"; kind: "outline"
                onClicked: page.browse(pr.key, pr.label + (pr.fileTitle.length > 0 ? ": a folder, or a single clip" : ""),
                                       pr.fileTitle.length > 0)
                HoverTip { tip: pr.fileTitle.length > 0 ? "Pick a folder, or a single clip for a single merge." : "Pick a folder." }
            }
        }
    }

    ColumnLayout {
        id: content
        objectName: "pageContent"
        x: 18
        y: 6
        width: parent.width - 36
        // Down to the action bar: the preview takes whatever height is left.
        height: Math.max(implicitHeight, actionBar.y - Theme.gap - y)
        spacing: Theme.gap

        Section {
            title: "Folders"
            enabled: page.idle
            headerRight: [
                AccentButton {
                    visible: page.mg.importDescription.length > 0
                    text: "Import from Inpainting"
                    kind: "tonal"
                    iconGlyph: Theme.icon.importIcon
                    onClicked: page.mg.importFromInpainting()
                    HoverTip {
                        tip: "Fill the Inpainted, Original and Mask folders from the most\n"
                             + "recent completed Inpainting job -- whichever ran last, on this\n"
                             + "machine. There is no picker: to use an older job, set the\n"
                             + "folders by hand.\n\n"
                             + "Clicking replaces whatever those three fields currently hold.\n\n"
                             + "Most recent job:\n" + page.mg.importDescription
                    }
                },
                AccentButton {
                    objectName: "mgImportV2"
                    visible: page.mg.importV2Description.length > 0
                    text: "Import from Inpainting V2"
                    kind: "tonal"
                    iconGlyph: Theme.icon.importIcon
                    onClicked: page.mg.importFromInpaintingV2()
                    HoverTip {
                        tip: "Fill the Inpainted, Original and Mask folders from the most\n"
                             + "recent completed Inpainting V2 (Wan VACE) job. Mask gets the\n"
                             + "splats V2 read; Original, Splatting's source clips.\n\n"
                             + "Clicking replaces whatever those three fields currently hold.\n\n"
                             + "Most recent job:\n" + page.mg.importV2Description
                    }
                },
                ChangeFoldersButton {
                    open: !page.pathsFolded
                    manual: !page.chapterMode
                    onClicked: page.toggleFolders()
                    paths: [{ label: "Inpainted Video Folder", value: page.st.inpainted_folder },
                            { label: "Original Video Folder", value: page.st.original_folder },
                            { label: "Mask Folder", value: page.st.mask_folder },
                            { label: "Output Folder", value: page.st.output_folder }]
                },
                ChapterPicker { visible: page.mg.hasProject; dc: page.mg; onPicked: page.foldersOpen = false }
            ]
            PathRow { label: "Inpainted Video Folder:"; key: "inpainted_folder"; help: page.help("inpainted_folder"); fileTitle: "Select Inpainted Video File" }
            PathRow { label: "Original Video Folder:"; key: "original_folder"; help: page.help("original_folder"); fileTitle: "Select a single original (left-eye) clip" }
            PathRow { label: "Mask Folder:"; key: "mask_folder"; help: page.help("mask_folder"); fileTitle: "Select a single splatted file" }
            PathRow { label: "Output Folder:"; key: "output_folder"; help: page.help("output_folder") }
            // v1 _build_encoding_preset_row: Encoding Options..., then the preset
            // toggle; "Custom" lights neither half and says so.
            RowLayout {
                spacing: 8
                // styled as Start Blending, with a film icon (user, 2026-09-27)
                AccentButton { objectName: "encodingButton"; text: "Encoding Options..."; iconGlyph: Theme.icon.video; onClicked: encodingDlg.open() }
                Caption { text: "Use Preset:" }
                SegmentedControl {
                    id: presetSeg
                    compact: true
                    controlled: true
                    model: page.mg.presetNames
                    currentIndex: page.mg.presetNames.indexOf(page.mg.encodingPreset)
                    onActivated: (i) => page.mg.applyEncodingPreset(page.mg.presetNames[i])
                    HoverTip { tip: page.mg.presetTooltip }
                }
                Caption { text: "Custom encoding settings used"; visible: presetSeg.currentIndex < 0 }
            }
        }

        // ---- the body: preview + the settings, in Splatting's two shapes -------
        Item {
            id: body
            objectName: "mergeBody"
            Layout.fillWidth: true
            Layout.fillHeight: true
            // Compact: the columns under the preview, as tall as the taller.
            readonly property real bandHeight: Math.max(leftCol.implicitHeight, rightGroup.implicitHeight)
            implicitHeight: page.wide
                            ? Math.max(leftCol.implicitHeight, rightGroup.implicitHeight, previewPanel.implicitHeight)
                            : previewPanel.implicitHeight + Theme.gap + bandHeight

            // ---- the preview: v1's VideoPreviewer (stack_controls) -- the frame
            //      scrubber, its two control lines, then the picture.
            Rectangle {
                id: previewPanel
                objectName: "previewPanel"
                x: page.wide ? page.leftRail + Theme.gap : 0
                y: 0
                width: page.wide ? body.width - page.leftRail - page.rightRail - 2 * Theme.gap : body.width
                height: page.wide ? body.height : body.height - Theme.gap - body.bandHeight
                implicitHeight: previewCol.implicitHeight + 20
                // Its control lines' natural width: what the wide shape must leave it.
                readonly property real minWidth: previewCol.implicitWidth + 20
                radius: 12
                color: Theme.panel
                border.width: Theme.outlineWidth
                border.color: Theme.outline
                clip: true

                readonly property var pv: page.pv
                // the Front / Back choices: Glasses-Free 3D always listed, greyed out (with why) unless
                // an SR display is connected and this window is on it (user, 2026-10-11)
                readonly property bool hasGlasses: typeof Glasses3D !== "undefined" && Glasses3D !== null
                readonly property var sources: hasGlasses ? page.mg.choices.preview_source.concat(["Glasses-Free 3D"])
                                                          : page.mg.choices.preview_source
                readonly property string glassesWhyNot: Glasses.whyNot(hasGlasses ? Glasses3D : null, Window.window)
                // Image pixels 1:1 on the screen, as v1's canvas shows them:
                // Preview Scale is a percentage of the CLIP.
                readonly property real pxToItem: 1 / ((Screen.devicePixelRatio || 1) * (Window.window && Window.window.zoom ? Window.window.zoom : 1))

                ColumnLayout {
                    id: previewCol
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 6

                    // Frame scrubber (v1: "Frame: n / N")
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 10
                        enabled: previewPanel.pv.loaded
                        Caption {
                            text: "Frame: " + (previewPanel.pv.loaded ? (scrub.pressed ? Math.round(scrub.value) + 1 : previewPanel.pv.frame + 1) : 0)
                                  + " / " + previewPanel.pv.frameCount
                            font.features: { "tnum": 1 }
                            Layout.preferredWidth: 110
                        }
                        Slider {
                            id: scrub
                            objectName: "frameScrubber"
                            Layout.fillWidth: true
                            from: 0
                            to: Math.max(0, previewPanel.pv.frameCount - 1)
                            stepSize: 1
                            snapMode: Slider.SnapAlways
                            // not while held: a late seek's result pulled the handle back (sweep 2026-10-14c)
                            Binding on value { when: !scrub.pressed; value: previewPanel.pv.frame; restoreMode: Binding.RestoreNone }
                            // Live while held (user, 2026-09-27; v1 rendered only on
                            // release): each new frame is a seek, the newest wins.
                            property int lastSeek: -1
                            onMoved: {
                                const f = Math.round(value)
                                if (pressed && f !== lastSeek) { lastSeek = f; previewPanel.pv.seek(f) }
                            }
                            onPressedChanged: if (!pressed) {
                                const f = lastSeek >= 0 ? lastSeek : Math.round(value)   // where the hand let go
                                if (f !== lastSeek) previewPanel.pv.seek(f)    // (Merging's seeks are exact already)
                                lastSeek = -1
                            }
                            background: Rectangle {
                                x: scrub.leftPadding
                                y: scrub.topPadding + scrub.availableHeight / 2 - 2
                                width: scrub.availableWidth
                                height: 4
                                radius: 2
                                color: Theme.trough
                                Rectangle { width: scrub.visualPosition * parent.width; height: 4; radius: 2; color: Theme.accent }
                            }
                            handle: Rectangle {
                                x: scrub.leftPadding + scrub.visualPosition * (scrub.availableWidth - width)
                                y: scrub.topPadding + scrub.availableHeight / 2 - height / 2
                                width: 8; height: 18; radius: 3
                                color: Theme.knob
                                border.width: 2
                                border.color: Theme.accent
                            }
                        }
                        // Which side is up -- here, above the picture, not on it (user, 2026-09-27:
                        // the frame fits larger with nothing covering it)
                        Text {
                            objectName: "flipBadge"
                            visible: previewPanel.pv.loaded && !previewPanel.pv.loading
                            text: ((flipCard.backUp ? "Back" : "Front") + "  ·  " + (flipCard.facing === "Blended Image" ? "Blended Right Eye" : flipCard.facing)).toUpperCase() + "  ·  click or drag an edge to flip"
                            color: Theme.fgMuted
                            font.family: Theme.tabFont
                            font.pixelSize: Theme.smallSize
                            font.letterSpacing: 1
                            opacity: flipCard.busy ? 0.4 : 1
                            Behavior on opacity { NumberAnimation { duration: Theme.dur(160) } }
                        }
                    }

                    // v1's first control line: source, load, clear, scale
                    RowLayout {
                        Layout.alignment: Qt.AlignHCenter
                        // never wider than the panel: the Front / Back boxes give way first (as Splatting's)
                        Layout.fillWidth: false
                        Layout.maximumWidth: parent.width
                        spacing: 8
                        // v1's Preview Source, as the flip card's two sides (user,
                        // 2026-09-26): the side facing you is the preview source; a
                        // click on the picture turns it over to the other.
                        Caption { text: "Front:" }
                        Dropdown {
                            objectName: "flipFront"
                            store: Shell; key: "mergeFlipFront"
                            model: previewPanel.sources; measure: true; boxWidth: fitWidth
                            disabledItems: previewPanel.glassesWhyNot ? ["Glasses-Free 3D"] : []
                            disabledTip: previewPanel.glassesWhyNot
                            Layout.fillWidth: true; Layout.minimumWidth: 112; Layout.maximumWidth: boxWidth
                            HoverTip { tip: "The preview source on the front of the card. Click the picture to turn it over." }
                        }
                        Caption { text: "Back:" }
                        Dropdown {
                            objectName: "flipBack"
                            store: Shell; key: "mergeFlipBack"
                            model: previewPanel.sources; measure: true; boxWidth: fitWidth
                            disabledItems: previewPanel.glassesWhyNot ? ["Glasses-Free 3D"] : []
                            disabledTip: previewPanel.glassesWhyNot
                            Layout.fillWidth: true; Layout.minimumWidth: 112; Layout.maximumWidth: boxWidth
                            HoverTip { tip: "The preview source on the back of the card. Click the picture to turn it over." }
                        }
                        CheckField {
                            objectName: "keepTracking"
                            // Keep Eye Tracking On (user, 2026-10-17; sr_display.py): only beside the choice
                            store: Shell; key: "glassesKeepTracking"
                            text: "Keep Tracking"
                            shown: previewPanel.hasGlasses && !previewPanel.glassesWhyNot
                                   && (Shell.mergeFlipFront === "Glasses-Free 3D" || Shell.mergeFlipBack === "Glasses-Free 3D")
                            Layout.fillWidth: false
                            help: "Keeps the 3D monitor's eye tracking (and its camera) on while a Glasses-Free 3D preview is open: "
                              + "between clips, in and out of Full Preview, and while a job runs -- so it does not have to find "
                              + "you again each time. Off: the camera is on only while the 3D picture is live. Either way it goes "
                              + "off when no preview shows Glasses-Free 3D, on another tab, and when Studio closes."
                        }
                        AccentButton {
                            text: "Clear Preview"
                            kind: "outline"
                            enabled: previewPanel.pv.loaded && !previewPanel.pv.loading
                            onClicked: previewPanel.pv.clear()
                            HoverTip { tip: "Releases the currently loaded preview and frees its GPU memory. Use this to "
                                            + "reclaim VRAM without closing the app -- e.g. before starting a render." }
                        }
                        Caption { text: "Preview Scale:"; Layout.leftMargin: 6 }
                        // Fit first, as Splatting's (user, 2026-10-18): the largest scale the room allows,
                        // kept as the window or the clip changes; a percentage picked here, the wheel or a
                        // double-click zoom stands instead
                        Dropdown {
                            objectName: "previewScaleBox"
                            boxWidth: 84
                            model: ["Fit"].concat(page.mg.choices.preview_size || [])
                            currentIndex: page.previewFitOn ? 0 : model.indexOf(String(page.st.preview_size))
                            displayText: page.previewFitOn ? "Fit" : String(page.st.preview_size)
                            onActivated: (i) => {
                                if (i === 0) { page.previewFitOn = true; previewFit.apply() }
                                else { page.previewFitOn = false; page.st.preview_size = model[i] }
                            }
                            HoverTip { tip: "Fit sizes the preview to fill the space it has, and keeps it filled as the "
                                            + "window or the clip changes. A percentage stays as picked; the mouse wheel "
                                            + "and a double-click zoom pick one too, and a double-click on a zoomed-in "
                                            + "picture goes back to Fit." }
                        }
                    }
                    // ...and the second: clips, playback, and Preview Brightness
                    // (v1 gives it a line of its own under the preview; here it
                    // shares this one, as in Splatting, so the page fits a 1080p
                    // screen). Never wider than the panel: the slider gives way.
                    RowLayout {
                        Layout.alignment: Qt.AlignHCenter
                        Layout.maximumWidth: parent.width
                        spacing: 8
                        AccentButton {
                            text: "‹ Prev"; kind: "outline"
                            enabled: page.idle && previewPanel.pv.loaded && previewPanel.pv.videoIndex > 0 && !previewPanel.pv.loading
                            onClicked: previewPanel.pv.step(-1)
                            HoverTip { tip: "Load the previous video in the list for preview." }
                        }
                        AccentButton {
                            text: "Next ›"; kind: "outline"
                            enabled: page.idle && previewPanel.pv.loaded && previewPanel.pv.videoIndex < previewPanel.pv.videoCount - 1 && !previewPanel.pv.loading
                            onClicked: previewPanel.pv.step(1)
                            HoverTip { tip: "Load the next video in the list for preview." }
                        }
                        Caption {
                            text: "Video: " + (previewPanel.pv.videoCount > 0 ? previewPanel.pv.videoIndex + 1 : 0) + " / " + previewPanel.pv.videoCount
                            font.features: { "tnum": 1 }
                            HoverTip { tip: page.help("current_video_count") }
                        }
                        AccentButton {
                            objectName: "playButton"
                            text: previewPanel.pv.playing && !previewPanel.pv.fastForward ? "❚❚" : "▶"
                            implicitWidth: 40; kind: "outline"
                            enabled: previewPanel.pv.loaded
                            onClicked: previewPanel.pv.togglePlay()
                            HoverTip { tip: "Play/Pause, at the clip's own frame rate or as fast as frames render, whichever "
                                            + "is slower -- every frame is shown. Shortcut: Spacebar" }
                        }
                        AccentButton {
                            text: previewPanel.pv.fastForward ? "❚❚" : "»"
                            implicitWidth: 40; kind: "outline"
                            enabled: previewPanel.pv.loaded
                            onClicked: previewPanel.pv.toggleFastForward(page.ffStep)
                            HoverTip { tip: "Fast Forward (step N frames). Shortcut: Shift+Spacebar (Spacebar pauses)" }
                        }
                        Dropdown {
                            boxWidth: 58
                            model: ["2", "3", "4", "5", "6", "7", "8", "9", "10"]
                            currentIndex: page.ffStep - 2
                            onActivated: (i) => page.ffStep = i + 2
                            HoverTip { tip: "Select the number of frames to skip during Fast Forward." }
                        }
                        AccentButton {
                            text: "⟲"; implicitWidth: 36
                            kind: previewPanel.pv.loop ? "tonal" : "ghost"
                            onClicked: previewPanel.pv.toggleLoop()
                            HoverTip { tip: "Loop playback. When reaching the end, wrap to the first frame and continue." }
                        }
                        MSlider {
                            key: "preview_brightness"; label: "Preview Brightness:"; labelWidth: 130
                            from: 0.25; to: 3.0; step: 0.05; decimals: 2; defaultValue: 1.0; doubleClickResets: true
                            help: page.help("preview_brightness")
                            Layout.fillWidth: true
                            Layout.minimumWidth: 170
                            Layout.maximumWidth: 360
                            Layout.leftMargin: 8
                        }
                        // Wigglegram only (user, 2026-10-09): how fast it switches eyes
                        SliderRow {
                            objectName: "wiggleSpeedSlider"
                            visible: String(page.st.preview_source) === "Wigglegram"
                            store: Shell; key: "wiggleSpeed"; label: "Wiggle Speed:"; labelWidth: 100
                            from: 2; to: 20; step: 0.5; decimals: 1; defaultValue: 7.5
                            help: "How fast the wigglegram switches between the left and right eye, in switches per second. Right-click resets it."
                            Layout.fillWidth: true
                            Layout.minimumWidth: 170
                            Layout.maximumWidth: 300
                            Layout.leftMargin: 8
                        }
                    }
                    // The picture
                    Rectangle {
                        id: stageArea
                        objectName: "previewPicture"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        implicitHeight: page.minPictureHeight
                        radius: 8
                        color: Theme.alpha(Theme.fg, 0.04)
                        clip: true

                        Flickable {
                            id: flick
                            anchors.fill: parent
                            contentWidth: Math.max(width, shown.width)
                            contentHeight: Math.max(height, shown.height)
                            boundsBehavior: Flickable.StopAtBounds
                            interactive: shown.width > width || shown.height > height

                            Item {
                                id: shown
                                // the video stays clean of the window's film grain
                                GrainHole { clipTo: stageArea }
                                // The flip card (user, 2026-09-26): a click turns the picture
                                // over between the anaglyph and the blended right eye.
                                transform: Rotation {
                                    origin.x: shown.width / 2; origin.y: shown.height / 2
                                    axis { x: 0; y: 1; z: 0 }
                                    angle: flipCard.angle
                                }
                                width: previewPanel.pv.imageWidth * previewPanel.pv.displayScale * previewPanel.pxToItem
                                // On the picture, inside the Flickable: on the area behind it
                                // the Flickable took the press first. A drag past the
                                // threshold still pans a picture bigger than the view.
                                TapHandler {
                                    objectName: "flipTap"
                                    enabled: previewPanel.pv.loaded && !previewPanel.pv.wiggling && !Theme.modalOpen
                                    acceptedButtons: Qt.LeftButton
                                    gesturePolicy: TapHandler.DragThreshold
                                    // One click flips, a double-click zooms (user, 2026-09-27): the flip
                                    // waits out the double-click time, so a double-click never flips first.
                                    onTapped: (ev) => {
                                        if (flipDelay.running) { flipDelay.stop(); zoomAnchor.toggleAt(ev.position.x, ev.position.y) }
                                        else flipDelay.restart()
                                    }
                                }
                                Timer {
                                    id: flipDelay
                                    objectName: "flipDelay"
                                    interval: Math.min(Qt.styleHints.mouseDoubleClickInterval, 350)
                                    onTriggered: flipCard.flip()
                                }
                                height: previewPanel.pv.imageHeight * previewPanel.pv.displayScale * previewPanel.pxToItem
                                x: Math.max(0, (flick.width - width) / 2)
                                y: Math.max(0, (flick.height - height) / 2)
                                visible: previewPanel.pv.imageWidth > 0

                                Image {
                                    objectName: "previewImage"
                                    anchors.fill: parent
                                    cache: false
                                    smooth: true
                                    mipmap: true
                                    visible: !previewPanel.pv.wiggling
                                    source: previewPanel.pv.imageWidth > 0 && !previewPanel.pv.wiggling
                                            ? "image://mergepreview/frame/" + previewPanel.pv.imageSerial : ""
                                }
                                // Wigglegram: v1 flips the eyes on a timer.
                                Image {
                                    anchors.fill: parent
                                    cache: false
                                    visible: previewPanel.pv.wiggling
                                    property bool leftEye: true
                                    source: previewPanel.pv.wiggling
                                            ? "image://mergepreview/" + (leftEye ? "left" : "right") + "/" + previewPanel.pv.imageSerial : ""
                                    Timer {
                                        objectName: "wiggleTimer"
                                        // not while a job runs: no redraws then (Theme.calm, bug sweep A10)
                                        running: previewPanel.pv.wiggling && page.active && !Theme.calm
                                        interval: Math.round(1000 / Shell.wiggleSpeed)
                                        repeat: true
                                        onTriggered: parent.leftEye = !parent.leftEye
                                    }
                                }
                            }
                        }
                        // the card turned over by hand: drag an edge across (user, 2026-10-10)
                        FlipDrag {
                            anchors.fill: flick
                            card: flipCard
                            pv: previewPanel.pv
                            picX: shown.x - flick.contentX
                            picY: shown.y - flick.contentY
                            picW: shown.width
                            picH: shown.height
                            // not while zoomed in (a drag pans then), nor on the wigglegram
                            allowed: previewPanel.pv.loaded && !previewPanel.pv.wiggling && !flick.interactive
                        }
                        // Glasses-Free 3D: the SR display weaves the visible picture (sr_display.py)
                        GlassesFreeView {
                            objectName: "glassesFreeView"
                            stage: "merging"
                            pv: previewPanel.pv
                            picture: shown
                            area: stageArea
                            extraHoles: [fullscreenBtn]      // the full-screen icon shows over the 3D
                            // (not while its Full Preview is open: that window's picture is woven then)
                            wanted: page.active && String(page.st.preview_source) === "Glasses-Free 3D"
                                    && flipCard.angle === 0 && !flipCard.busy && !fullPreview.visible
                        }
                        // Full screen: an icon in the picture's top-right corner (user, 2026-10-15: in place of
                        // the Full Preview button)
                        FullscreenButton {
                            id: fullscreenBtn
                            objectName: "fullscreenButton"
                            anchors { top: parent.top; right: parent.right; margins: 8 }
                            z: 50
                            visible: previewPanel.pv.loaded
                            onClicked: page.openFullPreview()
                            tip: "Full screen (F11): the preview on the screen Studio is on, mirroring this one live. Escape, F11 or the X in the corner (it shows when you move the mouse) brings you back."
                        }
                        // ---- the flip card: anaglyph <-> blended right eye -----------------
                        // Half a turn to edge-on, Preview Source swapped there, the new
                        // frame waited for (at most ~0.5 s), then the other half. One render.
                        QtObject {
                            id: flipCard
                            objectName: "flipCard"
                            property real angle: 0
                            property bool busy: false
                            property int serialAtSwap: -1
                            property int genAtSwap: 0
                            // a frame drawn after the swap: not a play frame already on the
                            // worker with the old side (bug sweep 2026-10-09, A8)
                            function fresh() {
                                return previewPanel.pv.imageSerial !== serialAtSwap
                                    && previewPanel.pv.frameGen >= genAtSwap
                            }
                            // The two sides (the Front / Back dropdowns, in Shell) and
                            // which one faces you; the facing side IS preview_source.
                            // Imperative, not bindings: each writes preview_source, whose
                            // change comes straight back (a binding loop otherwise).
                            property bool backUp: false
                            property string facing: ""
                            property bool applying: false
                            function sideSource(up) { return up ? Shell.mergeFlipBack : Shell.mergeFlipFront }
                            // A side's choice changed: show the facing one.
                            function show() {
                                facing = sideSource(backUp)
                                if (String(page.st.preview_source || "") !== facing) {
                                    applying = true
                                    page.st.preview_source = facing
                                    applying = false
                                }
                            }
                            // preview_source set elsewhere (Load Settings, a sidecar): the
                            // side holding it turns up, or the facing side takes it.
                            function adopt() {
                                const src = String(page.st.preview_source || "")
                                if (applying || src === "" || src === facing) return
                                if (src === Shell.mergeFlipFront) backUp = false
                                else if (src === Shell.mergeFlipBack) backUp = true
                                else if (backUp) Shell.mergeFlipBack = src
                                else Shell.mergeFlipFront = src
                                facing = src
                            }
                            Component.onCompleted: { adopt(); show(); Qt.callLater(() => { show(); live = true }) }
                            // A write from Python (Load Settings, a clip's sidecar) sends no
                            // valueChanged -- the property's own notify still arrives.
                            // Not during start-up: the card opens front up, as it always has.
                            readonly property string stSource: String(page.st.preview_source || "")
                            property bool live: false
                            onStSourceChanged: if (live) adopt()
                            function flip() {
                                if (busy || !previewPanel.pv.loaded || previewPanel.pv.wiggling) return
                                busy = true
                                flipOut.start()
                            }
                            function swapped() {
                                serialAtSwap = previewPanel.pv.imageSerial
                                backUp = !backUp
                                show()
                                genAtSwap = previewPanel.pv.gen     // after show(): the new side's
                                flipWait.restart()
                            }
                            function land() {
                                flipWait.stop()
                                angle = -90
                                flipIn.start()
                            }
                            // FlipDrag: the other side up, mid-drag; fresh() says when its frame is here
                            function turnOver() {
                                serialAtSwap = previewPanel.pv.imageSerial
                                backUp = !backUp
                                show()
                                genAtSwap = previewPanel.pv.gen
                            }
                        }
                        Connections {
                            target: Shell
                            function onChanged() { flipCard.show() }        // a side's choice
                        }
                        Connections {
                            target: page.st
                            function onValueChanged(key, value) { if (key === "preview_source") flipCard.adopt() }
                        }
                        NumberAnimation {
                            id: flipOut
                            target: flipCard; property: "angle"; from: 0; to: 90
                            duration: Theme.dur(170); easing.type: Easing.InCubic
                            onFinished: flipCard.swapped()
                        }
                        Timer { id: flipWait; interval: 500; onTriggered: flipCard.land() }
                        Connections {
                            target: previewPanel.pv
                            function onFrameChanged() {          // imageSerial's notify
                                if (flipWait.running && flipCard.fresh()) flipCard.land()
                            }
                            function onChanged() {               // frameGen's notify (after the frame)
                                if (flipWait.running && flipCard.fresh()) flipCard.land()
                            }
                        }
                        NumberAnimation {
                            id: flipIn
                            target: flipCard; property: "angle"; from: -90; to: 0
                            duration: Theme.dur(230); easing.type: Easing.OutCubic
                            onFinished: flipCard.busy = false
                        }
                        // v1 preview auto-fit: the largest Preview Scale the room allows.
                    PreviewAutoFit {
                        id: previewFit
                        objectName: "previewAutoFit"
                        area: stageArea; pv: previewPanel.pv; store: page.st
                        pxToItem: previewPanel.pxToItem
                        maxPercent: 250
                        active: page.previewFitOn
                    }
                        // The wheel ZOOMS the preview (user, 2026-09-27), never scrolls it: the
                        // handler sits on the Flickable itself, whose handlers see the wheel before
                        // the Flickable does -- so a zoomed-in picture no longer scrolls under the
                        // wheel. Each notch is ~12%, about the point under the pointer; a drag pans.
                        WheelHandler {
                            objectName: "previewZoomWheel"
                            parent: flick
                            enabled: previewPanel.pv.loaded && !Theme.modalOpen
                            acceptedModifiers: Qt.NoModifier
                            onWheel: (e) => {
                                const cur = parseFloat(String(page.st.preview_size)) || 100
                                const step = e.angleDelta.y > 0 ? 1.12 : 1 / 1.12
                                const next = Math.max(10, Math.min(250, Math.round(cur * step)))
                                if (next === cur) return
                                // keep the frame point under the pointer where it is (applied once
                                // the picture has its new size -- see zoomAnchor below)
                                zoomAnchor.fx = shown.width > 0 ? (flick.contentX + e.x - shown.x) / shown.width : 0.5
                                zoomAnchor.fy = shown.height > 0 ? (flick.contentY + e.y - shown.y) / shown.height : 0.5
                                zoomAnchor.vx = e.x; zoomAnchor.vy = e.y
                                zoomAnchor.at = Date.now()
                                page.previewFitOn = false
                                page.st.preview_size = next + "%"
                            }
                        }
                        QtObject {
                            id: zoomAnchor
                            property real fx: 0.5
                            property real fy: 0.5
                            property real vx: 0
                            property real vy: 0
                            property double at: 0
                            function apply() {
                                if (Date.now() - at > 2000) return
                                flick.contentX = Math.max(0, Math.min(flick.contentWidth - flick.width, shown.x + fx * shown.width - vx))
                                flick.contentY = Math.max(0, Math.min(flick.contentHeight - flick.height, shown.y + fy * shown.height - vy))
                            }
                            // Double-click (user, 2026-09-27, in place of a Fit button): zoomed in
                            // past the whole frame -> back to the whole frame; otherwise 50% closer
                            // about the point clicked. px/py are in the picture's own coordinates.
                            function toggleAt(px, py) {
                                const cur = parseFloat(String(page.st.preview_size)) || 100
                                const fit = previewFit.pv ? previewFit.percent() : -1
                                if (fit > 0 && Math.round(cur) > fit) {
                                    at = 0; page.previewFitOn = true; previewFit.apply(); flick.contentX = 0; flick.contentY = 0
                                    return
                                }
                                const next = Math.max(10, Math.min(250, Math.round(cur * 1.5)))
                                if (next === cur) return
                                fx = shown.width > 0 ? px / shown.width : 0.5
                                fy = shown.height > 0 ? py / shown.height : 0.5
                                vx = shown.x + px - flick.contentX; vy = shown.y + py - flick.contentY
                                at = Date.now()
                                page.previewFitOn = false
                                page.st.preview_size = next + "%"
                            }
                        }
                        Connections {
                            target: shown
                            function onWidthChanged() { Qt.callLater(zoomAnchor.apply) }
                            function onHeightChanged() { Qt.callLater(zoomAnchor.apply) }
                        }
                        Column {
                            anchors.centerIn: parent
                            spacing: 10
                            visible: !previewPanel.pv.loaded || previewPanel.pv.loading
                            Spinner {
                                anchors.horizontalCenter: parent.horizontalCenter
                                visible: previewPanel.pv.loading
                                running: visible
                            }
                            Text {
                                anchors.horizontalCenter: parent.horizontalCenter
                                objectName: "previewHint"
                                text: previewPanel.pv.loading ? "Loading preview…"
                                      : "Pick a clip in Upcoming Jobs to preview it -- or click here for the first one."
                                // no Load Preview button any more (user, 2026-09-27): the
                                // clip list loads what is picked; this covers a list with
                                // no room to show
                                TapHandler {
                                    enabled: !previewPanel.pv.loading && !previewPanel.pv.loaded && !Theme.modalOpen
                                    onTapped: previewPanel.pv.load()
                                }
                                HoverHandler { cursorShape: previewPanel.pv.loading ? Qt.ArrowCursor : Qt.PointingHandCursor }
                                color: Theme.fgMuted
                                font.family: Theme.bodyFont
                                font.pixelSize: Theme.bodySize
                            }
                        }
                    }
                }
            }

                // Mask Processing holds the three mask dials; the five shadow ones
                // fold away beneath it (user, 2026-09-26), closed by default and
                // remembered (Shell.mergeShadowOpen).
                ColumnLayout {
                    id: leftCol
                    objectName: "mergeLeftRail"
                    x: 0
                    y: page.wide ? 0 : previewPanel.height + Theme.gap
                    width: page.wide ? page.leftRail : body.width - rightGroup.width - Theme.gap
                    // compact: down to the band's bottom, Upcoming Jobs taking the rest
                    height: Math.max(implicitHeight, body.height - y)
                    spacing: Theme.gap

                    Section {
                        title: "Mask Processing Parameters"
                        enabled: page.idle
                        GridLayout {
                            Layout.fillWidth: true
                            columns: page.wide ? 1 : 2
                            columnSpacing: 0
                            rowSpacing: 8
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 0
                                Repeater {
                                    model: page.mg.maskSliders.filter(m => !page.shadowRules[m.key])
                                    MaskDial { Layout.fillWidth: !page.wide; Layout.preferredWidth: page.wide ? 110 : 96 }
                                }
                                Item { Layout.fillWidth: true; visible: !page.wide }
                            }
                            ColumnLayout {
                                Layout.alignment: page.wide ? Qt.AlignLeft : Qt.AlignVCenter
                                Layout.fillWidth: page.wide
                                spacing: 8
                                MCheck {
                                    shown: !page.bh["merge.highlight_mask"]
                                    key: "mask_highlight"; text: "Highlight Mask in Preview"
                                    help: "Overlays the processed mask on the Blended / Warped / Inpainted
    "
                                          + "preview: magenta tint whose strength follows the feathered mask
    "
                                          + "(so Blur, Shadow and Dilate changes show as visible gradients),
    "
                                          + "plus a green outline at the exact cutout boundary.
    "
                                          + "View-only — never affects the rendered output."
                                }
                                AccentButton {
                                    Layout.alignment: Qt.AlignRight
                                    text: "Reset to Defaults"; kind: "outline"
                                    onClicked: page.mg.resetMaskParameters()
                                    HoverTip { tip: page.help("reset_mask_parameters") }
                                }
                            }
                        }
                    }

                    // Put Back (user, 2026-10-15; core.removal.putback): a clip erased in the Depth Map
                    // Editor lays its object back over both eyes -- its controls, for the clip on screen
                    Section {
                        id: putBackSection
                        objectName: "putBackSection"
                        readonly property var pb: page.mg.putBack || ({})
                        readonly property bool over: pb.mode === "Over"
                        readonly property bool fromMap: over && (pb.depth === "Depth Map" || pb.depth === "Original Depth")
                        readonly property bool fromEyes: over && pb.depth === "Original Eyes"
                        // Add on the glass (2026-10-17): its own Convergence and Disparity, as the depth map's
                        readonly property bool fromGlass: pb.mode === "Add" && pb.depth === "Glass Plane"
                        title: "Put Back"
                        visible: !!pb.available
                        enabled: page.idle
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 6
                            CheckField {
                                objectName: "putBackOn"
                                store: page.mg.putBackStore; key: "enabled"
                                text: "Lay " + (putBackSection.pb.clip || "the object") + "'s object back"
                                help: "This clip was erased in the Depth Map Editor (Erase from Clip). On: the object comes "
                                      + "back over both eyes as Merging writes the clip, and in this preview."
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 8
                                Dropdown {
                                    objectName: "putBackMode"
                                    store: page.mg.putBackStore; key: "mode"; boxWidth: 90
                                    model: putBackSection.pb.modes || []
                                    HoverTip { tip: "Over: the original's pixels inside the object's mask, laid over (a solid "
                                                    + "thing: bars, a pole, a sign). Add: what erasing took away, added back "
                                                    + "(glass, rain, smoke -- seen through)." }
                                }
                                Dropdown {
                                    objectName: "putBackDepth"
                                    visible: putBackSection.over || (putBackSection.pb.depths || []).indexOf("Glass Plane") >= 0
                                    store: page.mg.putBackStore; key: "depth"; boxWidth: 140
                                    model: putBackSection.pb.depths || []
                                    disabledItems: putBackSection.pb.hasDepthMap ? [] : ["Depth Map", "Original Depth"]
                                    disabledTip: "No depth map was kept for this clip: it was erased before Put Back "
                                                 + "kept one. Erase it again in the Depth Map Editor to use it."
                                    HoverTip { tip: "Glass Plane (Add): what was taken off a window -- dust, a reflection -- laid "
                                                    + "on the glass itself: each window a plane fitted to the depth map around it "
                                                    + "(the depth map sees through glass). Original Eyes (Over): the object cut "
                                                    + "from the original clip's own merge. "
                                                    + "Original Depth: the object as the original clip's conversion had it -- its "
                                                    + "depth map at its own scale, every bit of the object's relief (a person "
                                                    + "nearer than anything else in the shot). "
                                                    + "Depth Map: the object at the depth the original clip's depth map gives "
                                                    + "it, pixel by pixel, warped as Splatting warped the scene -- a slanted or "
                                                    + "bowed thing keeps its shape. Parallax: one shift for the whole object." }
                                }
                            }
                            RowLayout {
                                objectName: "putBackEyesRow"
                                Layout.fillWidth: true
                                visible: putBackSection.fromEyes
                                spacing: 8
                                Text {
                                    Layout.fillWidth: true
                                    text: "Original merge: " + (putBackSection.pb.eyesFound
                                          || (putBackSection.pb.eyes ? "none found there" : "not chosen"))
                                    color: Theme.fg
                                    font.pixelSize: Theme.smallSize
                                    elide: Text.ElideMiddle
                                }
                                AccentButton {
                                    objectName: "putBackEyesBrowse"
                                    text: "Choose…"
                                    onClicked: putBackEyesDlg.open()
                                    HoverTip { tip: "The folder the ORIGINAL clip (the object still in it) was merged into "
                                                    + "-- its left and right eyes. The object is cut from them as that "
                                                    + "conversion made it." }
                                }
                            }
                            Text {
                                objectName: "putBackProblem"
                                Layout.fillWidth: true
                                visible: !!putBackSection.pb.depthMapProblem
                                text: putBackSection.pb.depthMapProblem || ""
                                color: Theme.warning
                                font.pixelSize: Theme.smallSize
                                wrapMode: Text.WordWrap
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 0
                                DialField {
                                    objectName: "putBackConv"
                                    visible: putBackSection.fromMap || putBackSection.fromGlass
                                    store: page.mg.putBackStore; key: "conv"; label: "Convergence:"
                                    from: -2; to: 2; step: 0.01; decimals: 2; defaultValue: 0; fillFrom: 0
                                    Layout.preferredWidth: page.wide ? 82 : 96
                                    help: "Moves the object off the depth its map gives it: + sets it back, - brings it "
                                          + "out. 0: where the original clip's depth map puts it."
                                    onPressedChanged: page.pv.setDragging(pressed)
                                }
                                DialField {
                                    objectName: "putBackDisp"
                                    visible: putBackSection.fromMap || putBackSection.fromGlass
                                    store: page.mg.putBackStore; key: "disp"; label: "Disparity:"
                                    from: 0; to: 300; step: 5; decimals: 0; defaultValue: 100; fillFrom: 100
                                    Layout.preferredWidth: page.wide ? 82 : 96
                                    help: "How much 3D the object gets, % of the scene's Disparity: 100 as Splatting "
                                          + "gave the scene, less flattens it toward the screen, more deepens it."
                                    onPressedChanged: page.pv.setDragging(pressed)
                                }
                                DialField {
                                    objectName: "putBackParallax"
                                    visible: !putBackSection.fromMap && !putBackSection.fromEyes && !putBackSection.fromGlass
                                    store: page.mg.putBackStore; key: "parallax"; label: "Parallax:"
                                    from: -10; to: 10; step: 0.05; decimals: 2; defaultValue: 0; fillFrom: 0
                                    Layout.preferredWidth: page.wide ? 82 : 96
                                    help: "One shift for the whole object, in percent of the width: + in front of the "
                                          + "screen, - behind it, 0 at the screen."
                                    onPressedChanged: page.pv.setDragging(pressed)
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 0
                                DialField {
                                    objectName: "putBackGrow"
                                    store: page.mg.putBackStore; key: "grow"; label: "Grow:"
                                    from: -10; to: 10; step: 1; decimals: 0; defaultValue: 0; fillFrom: 0
                                    Layout.preferredWidth: page.wide ? 82 : 96
                                    help: "The object's mask, px: + dilates it (takes in more of its edge), - erodes it "
                                          + "(leaves a halo of the old background out)."
                                    onPressedChanged: page.pv.setDragging(pressed)
                                }
                                DialField {
                                    objectName: "putBackBlur"
                                    store: page.mg.putBackStore; key: "blur"; label: "Blur:"
                                    from: 0; to: 10; step: 0.5; decimals: 1; defaultValue: 0
                                    Layout.preferredWidth: page.wide ? 82 : 96
                                    help: "Softens the object's edge over this many px, so it sits into the picture."
                                    onPressedChanged: page.pv.setDragging(pressed)
                                }
                                DialField {
                                    objectName: "putBackOpacity"
                                    store: page.mg.putBackStore; key: "opacity"; label: "Opacity:"
                                    from: 0; to: 100; step: 1; decimals: 0; defaultValue: 100
                                    Layout.preferredWidth: page.wide ? 82 : 96
                                    help: "How much of the object comes back, %."
                                    onPressedChanged: page.pv.setDragging(pressed)
                                }
                            }
                        }
                    }

                    FoldSection {
                        objectName: "shadowFold"
                        title: "Shadow"
                        chevron: true
                        enabled: page.idle
                        // Beginner Mode hides every shadow setting (v1): the fold goes with them.
                        visible: !page.bh["merge.shadow_shift"]
                        open: Shell.mergeShadowOpen
                        onToggled: (on) => Shell.mergeShadowOpen = on
                        help: "The shadow cast from the mask's edge into the fill: how far it shifts, "
                              + "how it fades, and how strong it starts and ends. Click to show or hide the dials."
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 0
                            Repeater {
                                model: page.mg.maskSliders.filter(m => !!page.shadowRules[m.key])
                                MaskDial { Layout.fillWidth: true }
                            }
                        }
                    }
                    MergeClipInfo { visible: page.wide }
                    // Compact, Upcoming Jobs lives here: under Options the right-hand
                    // column is already the tallest, and the page must still fit a
                    // 1080p screen at 85%. Wide, it sits under Options (user).
                    JobQueue {
                        objectName: "jobQueueCompact"
                        visible: !page.wide
                        Layout.fillWidth: true
                        pv: page.pv
                        ctl: page.mg
                        legendKinds: ["sidecar", "erased"]   // Merging's badges: the sidecar, an object removed (Put Back)
                        // right-click a clip (user, 2026-10-10)
                        rowActions: [{ id: "merge", text: "Merge This Clip Now" }, { id: "test", text: "Test Render" }, { id: "filledges", text: "Fill Edges" }, { id: "finished", text: "Move to Finished" }]
                        onRowAction: (id, path) => {
                            if (id === "merge") page.mg.startClip(path)
                            else if (id === "test") page.mg.testClip(path)   // into Test Renders (user, 2026-10-15)
                            else if (id === "filledges") EdgeFill.fillClip(path, String(page.mg.settings.output_folder || ""), String(page.mg.settings.mask_folder || ""))
                            else if (id === "finished") page.pv.jobClips.moveToFinished(path)
                        }
                        busy: page.mg.busy
                        // clipName still names the preview's clip as a job starts: a clip is
                        // worked on once a merge phase (grain / render / split) shows for it
                        running: page.mg.busy ? String(page.mg.clipName || "") : ""
                        working: page.mg.busy && typeof JobStats !== "undefined" && JobStats.stage === "merging"
                                 && JobStats.mergePhase !== ""
                        progress: typeof JobStats === "undefined" || JobStats.stage !== "merging" ? 0
                                  : JobStats.mergePhase === "render" ? JobStats.mergePercent / 100
                                  : JobStats.mergePhase === "split" ? 1 : 0
                        countText: String(page.mg.clipCount || "—")
                        countHelp: page.help("processing_clip_count")
                    }
            }

            // -- the right rail: Film Grain, Clip Info, then Options (under them
            //    wide, beside them compact)
            GridLayout {
                id: rightGroup
                objectName: "mergeRightRail"
                x: body.width - width
                y: page.wide ? 0 : previewPanel.height + Theme.gap
                width: page.wide ? page.rightRail : implicitWidth
                // down to the bottom of the band: Upcoming Jobs takes what is left
                height: Math.max(implicitHeight, body.height - y)
                columns: page.wide ? 1 : 2
                columnSpacing: Theme.gap
                rowSpacing: Theme.gap

                ColumnLayout {
                    Layout.alignment: Qt.AlignTop
                    Layout.fillWidth: page.wide
                    Layout.preferredWidth: 330
                    Layout.maximumWidth: page.wide ? page.rightRail : 330
                    spacing: Theme.gap

                    Section {
                        title: "Film Grain (Inpainted Fills)"
                        enabled: page.idle
                        MCheck {
                            shown: !page.bh["merge.grain_enable"]
                            key: "grain_enable"; text: "Add matching grain to inpainted fills"
                            help: "Adds 35mm-style grain ONLY where the blend mask is active (the\n"
                                  + "inpainted disocclusion fills of the right eye), weighted by the\n"
                                  + "feathered mask so it fades exactly where the blend fades.\n"
                                  + "The original left eye and un-inpainted pixels are never touched.\n"
                                  + "The main Blended Image preview does NOT show grain, colour correction\n"
                                  + "or red-flare reduction -- those are applied on export and in the\n"
                                  + "Grain Match Preview only, so judge them there.\n"
                                  + "Tune amount/size/falloff/chroma/seed in the Grain Match Preview window.\n"
                                  + "Sidecar keys grain_enable / grain_amount / grain_size /\n"
                                  + "grain_luma_falloff / grain_chroma / grain_seed override these values per clip."
                        }
                        // v1 _toggle_grain_widgets: these follow the grain switch.
                        MCheck {
                            key: "grain_automatch"; text: "Auto-match grain per clip on render"
                            // Beginner Mode: Auto-match IS the grain switch, so it stays (v1).
                            shown: !!page.st.grain_enable || !!page.bh["merge.grain_enable"]
                            help: "Before each clip renders, measures the master's grain σ against the\n"
                                  + "ungrained fill on the 8 frames with the most inpainted area, and uses\n"
                                  + "the MEDIAN solved Amount for the whole clip. Same solver as the Grain\n"
                                  + "Match Preview's Auto-Match button.\n\n"
                                  + "Clips whose sidecar already carries a grain_amount are SKIPPED —\n"
                                  + "hand-tuning always wins, so a re-run never clobbers your corrections.\n"
                                  + "The solved amount is written back to each clip's sidecar, which means\n"
                                  + "a second run over the same folder finds them hand-tuned and skips them.\n\n"
                                  + "Clips with no source video to measure against, or with almost no fill,\n"
                                  + "fall through to the Amount slider rather than a guessed number."
                        }
                        AccentButton {
                            objectName: "grainPreviewButton"
                            text: "Grain Match Preview…"; kind: "outline"
                            visible: !!page.st.grain_enable && !page.bh["merge.grain_preview"]
                            onClicked: grainWin.openFor()
                            HoverTip { tip: "Tune the fill grain against the master's own grain: the clip's blended eye with grain "
                                            + "on the fills, magnified crops, and the measured grain \u03c3 of master vs fill." }
                        }
                    }

                    // Here compact; wide it sits in the left rail (the rails even out).
                    MergeClipInfo { visible: !page.wide }
                }

                // Options, then Upcoming Jobs under it (user, 2026-09-27)
                ColumnLayout {
                    Layout.alignment: Qt.AlignTop
                    Layout.fillWidth: page.wide
                    Layout.fillHeight: true
                    spacing: Theme.gap
                Section {
                    objectName: "mergeOptions"
                    title: "Options"
                    enabled: page.idle
                    Layout.alignment: Qt.AlignTop
                    Layout.fillWidth: page.wide
                    Layout.preferredWidth: 260
                    Layout.maximumWidth: page.wide ? page.rightRail : 260
                    // Two columns in the rail (user, 2026-09-26), one when compact.
                    GridLayout {
                        Layout.fillWidth: true
                        columns: page.wide ? 2 : 1
                        columnSpacing: 12
                        rowSpacing: Theme.rowGap
                    MCheck { Layout.columnSpan: page.wide ? 2 : 1; key: "use_gpu"; text: "Use GPU for Mask Processing"; help: page.help("use_gpu") }
                    GridLayout {
                        Layout.columnSpan: page.wide ? 2 : 1
                        Layout.topMargin: 2
                        columns: page.wide ? 2 : 1          // compact: the label over the box, as v1
                        columnSpacing: 8
                        rowSpacing: 4
                        Caption { text: "Output Format:" }
                        Dropdown { store: page.st; key: "output_format"; model: page.mg.choices.output_format; boxWidth: page.wide ? 210 : 230; HoverTip { tip: page.help("output_format") } }
                    }
                    MCheck { key: "enable_color_transfer"; shown: !page.bh["merge.color_transfer"]; text: "Enable Color Transfer"; help: page.help("enable_color_transfer") }
                    MCheck { key: "pad_to_16_9"; text: "Pad to 16:9"; help: page.help("pad_to_16_9") }
                    MCheck { key: "add_borders"; text: "Add Borders"; help: page.help("add_borders") }
                    MCheck { key: "resume"; text: "Resume"; help: page.help("resume") }
                    // v1 _sync_split_options: Split L/R only for Full SBS, Delete SBS
                    // only while Split is on; hidden options keep their values.
                    MCheck {
                        key: "split_lr"; text: "Split L/R"
                        shown: String(page.st.output_format).indexOf("Full SBS") === 0
                        help: "After merging, also split each Full SBS output into separate\n"
                              + "Left (_L) and Right (_R) eye video files.\n"
                              + "Only applies to Full SBS output formats."
                    }
                    MCheck {
                        key: "delete_sbs_after_split"; text: "Delete SBS Videos"
                        Layout.leftMargin: page.wide ? 0 : 22
                        shown: String(page.st.output_format).indexOf("Full SBS") === 0 && !!page.st.split_lr
                        help: "After a successful L/R split, move the Full SBS video to the\n"
                              + "Recycle Bin, keeping only the _L and _R files.\n"
                              + "The SBS is kept if either eye fails to split."
                    }
                    RowLayout {
                        Layout.columnSpan: page.wide ? 2 : 1
                        Layout.topMargin: 6
                        spacing: 5
                        Caption { text: "Batch Chunk Size:" }
                        MBox { key: "batch_chunk_size"; boxWidth: 55; HoverTip { tip: page.help("batch_chunk_size") } }
                    }
                    }
                }
                JobQueue {
                    visible: page.wide
                    Layout.fillWidth: true
                    pv: page.pv
                    ctl: page.mg
                    legendKinds: ["sidecar", "erased"]   // Merging's badges: the sidecar, an object removed (Put Back)
                    // right-click a clip (user, 2026-10-10)
                    rowActions: [{ id: "merge", text: "Merge This Clip Now" }, { id: "test", text: "Test Render" }, { id: "filledges", text: "Fill Edges" }, { id: "finished", text: "Move to Finished" }]
                    onRowAction: (id, path) => {
                        if (id === "merge") page.mg.startClip(path)
                        else if (id === "test") page.mg.testClip(path)   // into Test Renders (user, 2026-10-15)
                        else if (id === "filledges") EdgeFill.fillClip(path, String(page.mg.settings.output_folder || ""), String(page.mg.settings.mask_folder || ""))
                        else if (id === "finished") page.pv.jobClips.moveToFinished(path)
                    }
                    busy: page.mg.busy
                    // clipName still names the preview's clip as a job starts: a clip is
                    // worked on once a merge phase (grain / render / split) shows for it
                    running: page.mg.busy ? String(page.mg.clipName || "") : ""
                    working: page.mg.busy && typeof JobStats !== "undefined" && JobStats.stage === "merging"
                             && JobStats.mergePhase !== ""
                    progress: typeof JobStats === "undefined" || JobStats.stage !== "merging" ? 0
                              : JobStats.mergePhase === "render" ? JobStats.mergePercent / 100
                              : JobStats.mergePhase === "split" ? 1 : 0
                    countText: String(page.mg.clipCount || "—")
                    countHelp: page.help("processing_clip_count")
                }
                }
            }
        }
    }

    ActionBar {
        id: actionBar
        anchors.left: parent.left
        anchors.right: parent.right
        // this stage's own tips, typed out above the bar (user, 2026-09-27)
        tips: Tips.forStage("merging")      // studio/qml/Studio/tips.js: Settings > Tips lists them all
        anchors.bottom: parent.bottom
        anchors.margins: 12
        busy: page.mg.busy
        phaseStage: "merging"
        done: Math.round(page.mg.progressValue)
        total: Math.round(page.mg.progressMaximum)
        status: page.mg.status
        title: "Progress"

        AccentButton {
            objectName: "mgStart"
            text: "Start Blending"; iconGlyph: Theme.icon.play
            enabled: page.idle
            onClicked: page.mg.start()
            HoverTip { tip: page.help("start_blending") }
        }
        AccentButton {
            objectName: "mgStop"
            text: "Stop"; kind: "outline"
            enabled: page.mg.busy
            // asks, then stops now (audit #13); Stop After Clip is the gentle way
            onClicked: (page.prompt ? page.prompt.askStop({ stage: "Merging", safeText: "Stop after this clip", safeDetail: "the clip being processed finishes, then the batch stops. The rest of the folder is left for the next Start.", onSafe: page.mg.multiClip ? () => { if (!page.mg.stopAfterClip) page.mg.toggleStopAfterClip() } : null, onYes: () => page.mg.stop() })
                         : page.mg.stop())
            HoverTip { tip: page.help("stop_blending") }
        }
        AccentButton {
            objectName: "mgStopAfter"
            text: page.mg.stopAfterClip ? "Stopping After Clip" : "Stop After Clip"
            kind: page.mg.stopAfterClip ? "tonal" : "outline"
            // only while a folder of clips is running (user, 2026-09-28)
            visible: page.mg.busy && page.mg.multiClip
            onClicked: page.mg.toggleStopAfterClip()
            HoverTip {
                tip: "Let the clip being processed finish, then stop before the next one.\n"
                     + "The finished clips are complete; the rest of the folder is\n"
                     + "left for the next Start. Click again to carry on instead."
            }
        }
        AccentButton {
            objectName: "mgProcessCurrent"
            text: "Process Current Clip"; kind: "outline"
            enabled: page.idle && page.pv.loaded
            onClicked: page.mg.processCurrentClip()
            HoverTip { tip: page.help("process_current_clip") || "Merge only the clip shown in the preview." }
        }
        AccentButton {
            objectName: "mgMoveToFinish"
            visible: !page.bh["merge.move_to_finish"]
            text: "Move to Finish"; kind: "outline"
            enabled: page.idle && page.pv.loaded
            onClicked: page.pv.moveToFinish(false)
            HoverTip { tip: page.help("move_to_finish") || "Move the clip shown in the preview (and its sidecars) to the finished folders without merging it." }
        }

        trailing: [
            // ROSE refills the strip at a frame's edge (user, 2026-10-13): Extra > Edge Fill on
            // this stage's Output and Mask folders (its own page since 2026-10-14)
            AccentButton {
                objectName: "mgFillEdges"
                text: "Fill Edges"
                kind: "outline"
                enabled: page.idle
                onClicked: {
                    EdgeFill.useFolders(String(page.mg.settings.output_folder || ""), String(page.mg.settings.mask_folder || ""))
                    Shell.openStage(4, 2)
                }
                HoverTip { tip: page.help("fill_edges") }
            },
            // the whole movie back together (user, 2026-10-11; studio/merge_all.py)
            AccentButton {
                objectName: "mgMergeAll"
                text: "Merge All SBS to 1 Video"
                kind: "outline"
                enabled: page.idle && page.mg.hasProject
                onClicked: mergeAllDlg.begin()
                HoverTip { tip: page.help("merge_all") }
            },
            // Never disabled: checking finished clips mid-batch is the point (v1).
            AccentButton {
                text: "View Output Folder"
                kind: "outline"
                onClicked: page.mg.openOutputFolder()
                HoverTip { tip: page.help("open_output_folder") }
            }
        ]
    }

    // v1's menus for this stage (shell mode: Options is empty there, so absent).
    readonly property var stageMenus: [fileMenu, helpMenu]

    StudioMenu {
        id: fileMenu
        title: "File"
        MenuItem { text: "Load Settings..."; enabled: page.idle; onTriggered: loadSettingsDlg.open() }
        MenuItem { text: "Save Settings..."; enabled: page.idle; onTriggered: saveSettingsDlg.open() }
        MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: Theme.border } }
        MenuItem { text: "Save Preview Frame..."; enabled: page.pv.loaded; onTriggered: page.pv.suggestSaveName(false) }
        MenuItem { text: "Save Preview as SBS..."; enabled: page.pv.loaded; onTriggered: page.pv.suggestSaveName(true) }
        MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: Theme.border } }
        MenuItem {
            text: "Reset to Default"
            property string tip: "Puts every setting on this stage back to its default. Asks first."
            enabled: page.idle
            onTriggered: page.confirm("Reset Settings", "Are you sure you want to reset all settings to their default values?",
                                      "Reset", () => page.mg.resetDefaults())
        }
        MenuItem {
            text: "Restore Finished"
            property string tip: "Moves the inputs already processed, from the 'finished' folders, back into their input folders so they can be run again."
            enabled: page.idle
            onTriggered: page.confirm("Restore Finished Files",
                                      "Are you sure you want to move all processed videos from the 'finished' folders back to their respective input directories?",
                                      "Restore", () => page.mg.restoreFinished())
        }
    }

    StudioMenu {
        id: helpMenu
        title: "Help"
        MenuItem {
            text: "Enable Debug Logging"
            checkable: true
            checked: !!page.st.debug_logging_enabled
            onTriggered: page.st.debug_logging_enabled = checked
        }
        MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: Theme.border } }
        MenuItem {
            text: "About"
            onTriggered: page.prompt.ask({ title: "About Merging", message: page.mg.aboutText,
                                           buttons: [{ text: "OK", code: 1, kind: "filled" }] })
        }
    }
}
