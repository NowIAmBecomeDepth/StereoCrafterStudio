import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Layouts
import Studio

// The Studio window, arranged as v1's unified shell (unified_gui/shell.py):
//
//   menu strip   Studio  View  GPU  | then the showing stage's own menus
//   tab strip    1. DepthCrafter  2. Splatting  3. Inpainting  4. Merging  Extra
//   the stage page
//   status bar
//
// Sized as v1 sizes itself (unified_gui/shell.py request_fit): each page
// reports the width and height it needs, and the window fits them -- at
// launch, on a stage switch, and when the page changes shape (Process as
// Segments adds DepthCrafter's third column). A size the user drags to is
// kept, and only ever grown to what the page needs; View > Fit Window to
// Content returns to the fit. Pages never scroll.
ApplicationWindow {
    id: win

    // ---- UI scale: v1's (core/ui/ctk_theme._UI_SCALE_STEPS) -----------------
    // Everything is laid out at 1.00, the size it has on a 1440p-logical
    // screen; a 1080p screen gets 0.85 and anything smaller 0.75, measured
    // on the screen the window is on NOW, so dragging it across monitors
    // rescales it as v1 does. View > UI Scale can fix a factor instead.
    // The whole UI -- zoomRoot below, and the overlay popups sit in -- is
    // scaled as one, so pages keep their unscaled sizes and requiredWidth.
    readonly property real screenHeight: Screen.height > 0 ? Screen.height : 1440
    readonly property real autoZoom: screenHeight >= 1300 ? 1.0 : screenHeight >= 1000 ? 0.85 : 0.75
    readonly property real zoom: Shell.uiScale === "auto" ? autoZoom : Number(Shell.uiScale) || 1.0
    // View > Glass Opacity, into the theme (-1 = the palette's default).
    Binding { target: Theme; property: "glassKeep"; value: Shell.glassOpacity === "auto" ? -1 : Number(Shell.glassOpacity) }
    // The refits nobody asked for (a scale, a page's needs changing) wait
    // while a job runs, and happen when it ends (audit 2026-09-28: a UI Scale
    // change resized the window mid-job). A stage switch or Fit Window to
    // Content still refits at once.
    property bool refitAfterJob: false
    function autoFit() {
        if (userSized) return
        if (Theme.calm) refitAfterJob = true
        else Qt.callLater(fitToContent)
    }
    onZoomChanged: { syncOverlay(); autoFit() }
    Connections {
        target: Theme
        function onCalmChanged() {
            if (!Theme.calm && win.refitAfterJob) {
                win.refitAfterJob = false
                if (!win.userSized) Qt.callLater(win.fitToContent)
            }
        }
    }
    function syncOverlay() {
        const o = win.overlay
        if (!o) return
        o.transformOrigin = Item.TopLeft
        o.scale = zoom
        o.width = win.width / zoom
        o.height = win.height / zoom
    }
    onHeightChanged: {
        // a height-only drag is the user's too (audit 2026-09-28, minor): only
        // a width change counted, so the next refit snapped the height back
        if (!fitting && !screenMoving && !restoreSettle.running && visibility === Window.Windowed
                && Math.abs(height - fitHeight) > 2) userSized = true
        Qt.callLater(syncOverlay)
        glassRefit.restart()
    }

    readonly property Item currentPage: stage.current >= 0 ? stage.pages[stage.current] : null
    // The stage strip, the Settings gear and the VRAM pill share one row: never
    // narrower than all three (DepthCrafter's page alone is narrower).
    readonly property real headerWidth: tabs.x + tabs.implicitWidth + 12
        + (beginnerSwitch.visible ? beginnerSwitch.width + 10 : 0) + settingsButton.width + 4 + bell.width + 8 + themePicker.width
        + (vramPill.visible ? 8 + vramPill.width : 0) + 16
    readonly property int fitWidth: Math.ceil(zoom * Math.max(640, headerWidth,
                                                         currentPage ? currentPage.requiredWidth || 790 : 790))
    width: 790
    height: 900
    minimumWidth: fitWidth
    // ...but never taller than the screen has room for: past that the page scrolls (stageScroll)
    readonly property int neededHeight: Math.ceil(zoom * Math.max(560, stage.y + statusBar.height
                                                                  + (currentPage ? currentPage.requiredHeight || 0 : 0)))
    minimumHeight: Shell.capToScreen ? Math.min(neededHeight, Math.max(Math.ceil(zoom * 560), win.screenRoomHeight - 40))
                                     : neededHeight
    // A page may want more than it needs (Merging: a bigger preview). The fit
    // gives it that much, as far as the screen's free height allows.
    readonly property int fitHeight: Math.max(minimumHeight, Math.min(
        Math.ceil(zoom * (stage.y + statusBar.height + (currentPage ? currentPage.preferredHeight || 0 : 0))),
        win.screenRoomHeight - 40))
    // The free height of THIS window's screen. Not Screen.desktopAvailableHeight:
    // that is the whole desktop, and with the 4K screen stacked above the 1080p
    // one it was both together -- Splatting fitted taller than its screen, the
    // window straddled both, and every refit after moved it again (user,
    // 2026-09-28).
    // (re-read when the window changes screen: win.screen is read here)
    readonly property real screenRoomHeight: (win.screen, Shell.screenRoom(win).height || 1040)

    property bool userSized: false
    property bool fitting: false
    // Ctrl+= / Ctrl+- (user, 2026-10-02): the theme popout's UI Scale knob, a
    // step at a time (75-100%, 5% steps; the screen's own size is Auto)
    function stepUiScale(delta) {
        const autoPct = Math.round(win.autoZoom * 100)
        const cur = Shell.uiScale === "auto" ? autoPct : Math.round(Number(Shell.uiScale) * 100)
        const next = Math.max(75, Math.min(100, Math.round((cur + delta) / 5) * 5))
        if (next === cur) return
        Shell.uiScale = next === autoPct ? "auto" : String(next / 100)
        Theme.toast("info", "UI Scale " + next + "%" + (next === autoPct ? " (Auto)" : ""), "")
    }

    function fitToContent() {
        userSized = false
        if (visibility === Window.Maximized || visibility === Window.FullScreen) return
        fitting = true
        width = fitWidth
        height = fitHeight
        fitting = false
    }
    // Only a size nobody asked for counts as the user's: the platform echoes
    // our own resizes back, and those match the fit.
    onWidthChanged: {
        if (!fitting && !screenMoving && !restoreSettle.running && visibility === Window.Windowed
                && Math.abs(width - fitWidth) > 2) userSized = true
        Qt.callLater(syncOverlay)
        glassRefit.restart()
    }
    onFitWidthChanged: autoFit()
    // Back from maximised / full screen: Windows restores the size from
    // before -- another stage's, if the stage changed meanwhile -- and that
    // echo counted as a size the user picked, so the window never refit again
    // (user, 2026-09-28). A restore refits to the stage showing instead.
    //
    // Maximised on one screen and dragged by the caption to another (user,
    // 2026-09-28, 1080p -> 4K): Windows can send the maximised size just
    // BEFORE the state, so maximising itself counted as the user's size; the
    // drag restores the window and crosses screens within a second, and the
    // screen move put that stale "user-sized" back -- no stage refit again.
    // So: going enlarged undoes a user-sized set in that last moment, and a
    // restore clears it (and its settled copy) and, for a second, takes no
    // resize or screen move as the user's, then refits.
    property bool wasEnlarged: false
    onVisibilityChanged: (v) => {
        const enlarged = v === Window.Maximized || v === Window.FullScreen
        if (enlarged && !wasEnlarged && userSizedCommit.running) {
            userSizedCommit.stop()
            userSized = userSizedStable
        }
        if (!enlarged && v === Window.Windowed && wasEnlarged) {
            userSizedCommit.stop()
            userSized = false
            userSizedStable = false
            userSizedBeforeMove = false
            restoreSettle.restart()
            Qt.callLater(fitToContent)
        }
        wasEnlarged = enlarged
    }
    Timer {
        id: restoreSettle
        interval: 1000
        onTriggered: if (!win.screenMoving) { win.userSized = false; win.fitToContent() }
    }
    // Moved to another screen (user, 2026-09-28): Windows resizes the window
    // for the new screen's scale, and that too counted as a size the user
    // picked -- the window never refit again. While a move settles, resizes
    // are not the user's; then it refits, unless it was user-sized before.
    property bool screenMoving: false
    property bool userSizedBeforeMove: false
    onScreenChanged: {
        if (!screenMoving) userSizedBeforeMove = restoreSettle.running ? false : userSizedStable
        screenMoving = true
        userSized = userSizedBeforeMove
        screenSettle.restart()
    }
    Timer {
        id: screenSettle
        interval: 700
        onTriggered: {
            win.screenMoving = false
            win.userSized = win.userSizedBeforeMove
            if (!win.userSized) win.fitToContent()
        }
    }
    // userSized as it stood before the latest resizes: a DPI resize can land
    // just before the screen change is reported
    property bool userSizedStable: false
    Timer { id: userSizedCommit; interval: 800; onTriggered: win.userSizedStable = win.userSized }
    onUserSizedChanged: if (!screenMoving) userSizedCommit.restart()
    // And when the page's height need changes (a chapter's folders folding
    // away), shrink as well as grow -- a user-sized window is left alone.
    onMinimumHeightChanged: autoFit()
    visible: true
    // no "v2" (user, 2026-09-28): the CTk Studio was never released, it was the prototype
    title: Beginner.on ? "StereoCrafter Studio (Beginner Mode)" : "StereoCrafter Studio"
    // The caption is TitleBar's; WinFrame gives Windows back everything else.
    readonly property bool customFrame: typeof WinFrame !== "undefined" && WinFrame.active && Shell.customTitleBarAtStart
    // Created as a plain window either way -- see attachFrame().
    color: Theme.ground

    // v1 calls the last tab "Extra" (unified_gui/shell.py BONUS_TITLE).
    readonly property var stageNames: ["DepthCrafter", "Splatting", "Inpainting", "Merging", "Extra"]
    // where each stage is in the manual (F1, and its section of Settings --
    // user, 2026-10-04); Extra: the tool showing
    function manualAt(i) {
        if (i === 4) {
            const extra = stage.pages && stage.pages[4]
            return extra && extra.tool === 1 ? "part3.html" : extra && extra.tool === 2 ? "part2.html#fill-edges"
                                                                           : "part2.html#scenecut"
        }
        return ["part2.html#depthcrafter", "part2.html#splatting", "part2.html#inpainting",
                "part2.html#merging"][i] || ""
    }
    function manualName(i) {
        if (i === 4) {
            const extra = stage.pages && stage.pages[4]
            return extra && extra.tool === 1 ? "Depth Map Editor" : extra && extra.tool === 2 ? "Edge Fill" : "Scene Cut"
        }
        return stageNames[i] || ""
    }

    // ---- dark caption bar and glass ---------------------------------------
    function syncDark() {
        Shell.setDark(Theme.isDark)
        if (typeof Backdrop !== "undefined") Backdrop.setDark(Theme.isDark)
    }
    Connections { target: Theme; function onIsDarkChanged() { win.syncDark() } }
    // A page asking for another stage (Splatting <-> the Depth Map Editor):
    // the stage, and on the Extra tab which tool
    // Too big for the Recycle Bin (user, 2026-10-14): as Windows asks, in Studio -- deleting it
    // removes it for good. Keep It is the safe choice (and what closing the box answers).
    Connections {
        target: Shell
        function onAskPermanentDelete(rid, path, sizeText) {
            const name = String(path).replace(/\\/g, "/").split("/").pop()
            prompt.ask({
                title: "Too big for the Recycle Bin",
                message: "\u201c" + name + "\u201d (" + sizeText + ") is too big for the Recycle Bin.\n\n"
                         + "Deleting it removes it permanently -- it can't be put back.\n\n" + path,
                buttons: [{ text: "Delete Permanently", code: 1, kind: "danger" }, { text: "Keep It", code: 0, kind: "ghost" }],
                focusIndex: 1,
                onDone: (code) => Shell.answerPermanentDelete(rid, code === 1)
            })
        }
    }
    Connections {
        target: Shell
        function onOpenStageRequested(i, tool) {
            stage.show(i, false)
            const p = stage.pages[i]
            if (tool >= 0 && p && p.tool !== undefined) p.tool = tool
            else if (tool >= 0 && p && p.engine !== undefined) p.engine = tool     // Inpainting: V1 / V2
        }
    }

    // The frame in the palette, re-sent through the palette fade. The caption
    // itself is TitleBar's now (so it can be frosted AND tinted); the DWM
    // caption colour still matters wherever Windows draws one -- and if the
    // custom frame is off -- so it is sent solid, as v1 does.
    function syncCaption() {
        if (typeof Backdrop === "undefined") return
        Backdrop.setCaptionColors(Theme.bg, Theme.fg, Qt.tint(Theme.bg, Theme.borderStrong), false)
    }
    Connections {
        target: Theme
        function onBgChanged() { win.syncCaption() }
        function onFgChanged() { win.syncCaption() }
    }

    // Glass on: backdrop first, then thin the tint over it. Glass off: thicken
    // the tint first and drop the backdrop once it is opaque, so nothing ever
    // composites over an empty frame.
    function setGlass(mode, remember) {
        if (typeof Backdrop === "undefined" || !Backdrop.supported) return
        if (remember) Shell.glass = mode
        if (mode === "none") {
            Theme.glass = 0
            dropBackdrop.interval = Math.max(1, Theme.dur(560))
            dropBackdrop.restart()
        } else {
            dropBackdrop.stop()
            if (Backdrop.apply(mode)) Theme.glass = 1
            else {
                // the switch shows what there is (bug sweep 2026-10-09, A1: it stayed ON
                // over no glass, and the refused mode was saved and retried every start)
                Theme.glass = 0
                Shell.glass = "none"
                Theme.toast("warning", "No glass", "Windows refused the " + mode + " material, so Frosted Glass is off. "
                            + "You can turn it back on in the theme menu.")
            }
        }
    }
    Timer { id: dropBackdrop; onTriggered: Backdrop.apply("none") }
    // The acrylic is sized when it is set: re-seat it once a resize settles
    // (the stage fit after launch, a drag, maximise), or what lies past the
    // old size shows stretched pixels (platform_win Backdrop.refresh).
    Timer {
        id: glassRefit
        objectName: "glassRefit"
        interval: 150
        onTriggered: if (typeof Backdrop !== "undefined" && Shell.glass !== "none") Backdrop.refresh()
    }

    // Once more after launch, when Windows has settled the frame: applying the
    // material again is harmless, and it covers a launch where the first
    // apply did not take (seen as a solid window or a solid caption).
    Timer {
        id: glassReassert
        interval: 1500
        onTriggered: if (Shell.glass !== "none") win.setGlass(Shell.glass, false)
    }

    // The custom caption goes on after the FIRST frame, not at load. Created
    // frameless, Qt builds a transparent window differently (layered, no
    // blur-behind) and its glass came out flat grey however it was patched
    // afterwards; created plain it is the window whose glass works, and
    // going frameless later keeps that (same HWND, Qt's frame margins 0).
    function attachFrame() {
        win.frameSwapped.disconnect(attachFrame)
        fitting = true
        try {
            WinFrame.attach(win, titleBar, titleBar.maxButton,
                            [titleBar.minButton, titleBar.closeButton, titleBar.centerButton])
        } catch (e) {
            console.warn("Custom title bar did not attach:", e)
        } finally {
            fitting = false
            // The glass goes on AFTER the frame (attaching re-sets the window's
            // styles) -- and whether or not the attach went through, so a
            // failed attach can never leave the window without glass.
            if (Shell.glass !== "none") setGlass(Shell.glass, false)
            glassReassert.start()
        }
        if (!userSized) Qt.callLater(fitToContent)
    }

    Component.onCompleted: {
        if (customFrame)
            win.frameSwapped.connect(attachFrame)
        syncOverlay()
        syncDark()
        syncCaption()
        if (!customFrame)          // with the custom frame, attachFrame applies it
            Qt.callLater(() => { if (Shell.glass !== "none") setGlass(Shell.glass, false) })
        stage.show(Math.max(0, Math.min(Shell.lastStage, stageNames.length - 1)), true)
        Qt.callLater(fitToContent)
        Qt.callLater(showUpdateNotice)
        startupUpdateCheck.start()
        envCheck.start()
    }
    // What this PC is missing for Studio -- no NVIDIA driver, one too old, no FFmpeg (release check
    // 2026-10-17): once, after the window has settled; each is a warning, kept in the bell
    Timer {
        id: envCheck
        interval: 1500
        onTriggered: Shell.checkEnvironment()
    }
    Connections {
        target: Shell
        function onEnvWarning(title, text) { Theme.toast("warning", title, text) }
    }
    // Check for Updates at Startup (off by default): once, after Studio has settled
    Timer {
        id: startupUpdateCheck
        interval: 5000
        onTriggered: if (typeof Updater !== "undefined") Updater.startupCheck()
    }

    // Install & Restart: the helper waits for Studio to close, then updates it
    Connections {
        target: typeof Updater !== "undefined" ? Updater : null
        function onQuitForUpdate() { win.close() }
        function onUpdateFound(n, branch) {
            Theme.toast("info", "Studio update available",
                        (n === 1 ? "1 new change" : n + " new changes") + " on " + branch
                        + ". Settings > Studio > Check for Updates installs it.")
        }
    }
    // the last update's outcome, once, at start
    function showUpdateNotice() {
        const n = typeof Updater !== "undefined" ? Updater.notice : null
        if (n && n.message)
            Theme.toast(n.ok ? "success" : "error", n.ok ? "Studio updated" : "Update didn't finish", n.message)
    }

    Connections {
        target: Beginner
        function onRefused(title, text) {
            prompt.ask({ title: title, message: text, buttons: [{ text: "OK", code: 1, kind: "filled" }] })
        }
    }
    // v1 request_job: a stage's Start while another stage renders.
    Connections {
        target: GpuJobs
        function onRefused(title, text) {
            prompt.ask({ title: title, message: text, buttons: [{ text: "OK", code: 1, kind: "filled" }] })
        }
        function onWarned(title, text) { Theme.toast("warning", title, text) }     // bug sweep A2
    }

    // Every stage that runs jobs, as the status bar and the close guard name it.
    readonly property var jobStages: [
        { name: "DepthCrafter", ctl: DepthCrafter }, { name: "Splatting", ctl: Splatting },
        { name: "Inpainting", ctl: Inpainting }, { name: "Inpainting V2", ctl: InpaintingV2 },
        { name: "Merging", ctl: Merging }, { name: "Scene Cut", ctl: SceneCut },
        { name: "Depth Map Editor", ctl: DepthEdit }, { name: "Edge Fill", ctl: EdgeFill }]
    Binding { target: Theme; property: "calm"; value: !!win.runningStage }
    Binding { target: Theme; property: "lightJob"
              value: !!win.runningStage && (win.runningStage.name === "Splatting" || win.runningStage.name === "Merging") }
    // the VRAM pill republishes in 0.5 GB steps while a job runs (audit O7)
    Binding { target: Vram; property: "coarse"; value: Theme.calm; when: typeof Vram !== "undefined" && !!Vram }
    // Section outlines one physical pixel wide, whatever the scaling (Theme.hairline)
    Binding { target: Theme; property: "hairline"; value: 1 / Math.max(0.5, (Screen.devicePixelRatio || 1) * win.zoom) }
    readonly property var runningStage: {
        for (const s of jobStages) if (s.ctl.busy) return s
        return null
    }

    // Closing mid-run asks first; each stage's shutdown() stops its worker.
    property bool closeConfirmed: false
    onClosing: (close) => {
        const running = win.runningStage ? win.runningStage.name : ""
        if (running && !closeConfirmed) {
            close.accepted = false
            prompt.ask({
                title: "A " + running + " job is running",
                // audit #13 (user, 2026-09-29): Quit stops the job NOW
                message: running === "Edge Fill"      // no Stop After Clip there (bug sweep 2026-10-14b)
                         ? "Quitting stops it now: the clip being filled is left as it was. Clips already "
                           + "filled stay filled."
                         : "Quitting stops it now: the clip being processed is cut off and its output is "
                           + "discarded. Clips already finished are kept.\n\nTo let this clip finish first, "
                           + "use Stop After Clip and quit once it has stopped.",
                buttons: [{ text: "Quit now", code: 1, kind: "danger" }, { text: "Keep running", code: 0, kind: "ghost" }],
                onDone: (code) => { if (code === 1) { win.closeConfirmed = true; win.close() } }
            })
        }
        // Studio is closing: the console goes with it (an open console would
        // otherwise keep the app running as its last window)
        if (close.accepted)
            consoleWindow.close()
    }

    // the caption's project button: the projects in Settings' Projects Folder
    ProjectPicker {
        id: projectPicker
        anchorItem: titleBar.centerButton
        onBrowse: {
            projectDlg.currentFolder = Shell.pathToUrl(Shell.projectsRoot || Shell.projectFolder)
            projectDlg.open()
        }
        onChooseRoot: projectsRootDlg.open()
    }
    FolderPicker {
        id: projectsRootDlg
        objectName: "projectsRootDlg"
        title: "Projects folder (holds one folder per movie or show)"
        currentFolder: Shell.pathToUrl(Shell.projectsRoot || Shell.projectFolder)
        onAccepted: {
            Shell.projectsRoot = Shell.urlToPath(selectedFolder)
            Theme.toast("success", "Projects folder set", Shell.projectsRoot)
        }
    }
    FolderPicker {
        id: projectDlg
        title: "Project folder (holds the Source, DepthMaps, Splats... folders -- or a show's seasons and episodes)"
        currentFolder: Shell.pathToUrl(Shell.projectFolder)
        onAccepted: {
            Shell.projectFolder = Shell.urlToPath(selectedFolder)
            Theme.toast("success", "Project folder set", Shell.projectFolder)
        }
    }

    // ---- v1's shell menus, now Settings sections (SettingsDialog draws them) --
    StudioMenu {
        id: studioMenu
        title: "Studio"
        MenuItem {
            text: "Save All Settings"
            property string tip: "Writes every stage's settings to disk now (they are also saved when Studio closes)."
            // every stage (audit 2026-09-28, minor: only DepthCrafter + Inpainting)
            onTriggered: {
                DepthCrafter.save(); Splatting.save(); Inpainting.save(); InpaintingV2.save()
                Merging.save(); SceneCut.save(); DepthEdit.save(); Shell.save()
                Theme.toast("success", "Settings saved", "Every stage's settings are on disk.")
            }
        }
        MenuItem { text: "Set Project Folder…"; onTriggered: projectDlg.open() }
        // the models folder, each model's download, the Hugging Face sign-in (user, 2026-10-20)
        MenuItem {
            objectName: "modelsItem"
            text: "Models"
            property string tip: "Where Studio keeps its models, which are here, and a Download for each one missing "
                                 + "(with the Hugging Face sign-in the gated ones need)."
            onTriggered: modelsDlg.open()
        }
        // for the experts who never use it (user, 2026-10-11)
        MenuItem {
            objectName: "hideBeginnerItem"
            text: "Hide Beginner Mode Switch"
            checkable: true
            checked: Shell.hideBeginnerSwitch
            onTriggered: Shell.hideBeginnerSwitch = checked
            property string tip: "Takes the Beginner Mode switch off the top bar. It comes back while Beginner Mode is on, "
                                 + "so you can always turn the mode off."
        }
        MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: Theme.border } }
        MenuItem {
            text: "Check for Updates"   // no "…": a ghost button, the name itself clickable (user)
            property string tip: "Looks for a newer Studio on your GitHub repository and can install it."
            onTriggered: updateDlg.open()
        }
        // off by default (user, 2026-10-05): once, a few seconds after Studio opens
        MenuItem {
            objectName: "checkOnStartItem"
            text: "Check for Updates at Startup"
            checkable: true
            checked: typeof Updater !== "undefined" && Updater.checkOnStart
            onTriggered: if (typeof Updater !== "undefined") Updater.setCheckOnStart(checked)
            property string tip: "Checks your GitHub repository for a newer Studio once, when Studio first opens. It doesn't "
                                 + "check again after that unless you click Check for Updates. It only looks: nothing is "
                                 + "installed until you choose to. Needs the repository set in Check for Updates first."
        }
        MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: Theme.border } }
        MenuItem { text: "Open Console"; property string tip: "The console window, on the tab of the stage showing (Ctrl+`)."; onTriggered: consoleWindow.open = true }
        MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: Theme.border } }
        MenuItem { text: "Exit"; onTriggered: win.close() }
    }

    StudioMenu {
        id: gpuMenu
        title: "GPU"
        MenuItem {
            text: "Release GPU Memory"
            property string tip: "Unloads the models every stage is holding on the GPU (Inpainting V2 keeps its 14B engine). Not while a job runs."
            // v1 release_all_gpu: refused while any stage runs; every stage's
            // held weights go (Inpainting V2 keeps its 14B engine loaded).
            enabled: win.runningStage === null
            onTriggered: {
                Splatting.releaseGpu(); InpaintingV2.releaseGpu(); Merging.releaseGpu(); SceneCut.releaseGpu()
                DepthEdit.releaseGpu(); Theme.toast("info", "GPU", Shell.releaseGpu())
            }
        }
    }

    // Everything on screen, laid out at 1.00 and scaled by `zoom`.
    Item {
        id: zoomRoot
        objectName: "zoomRoot"
        width: win.width / win.zoom
        height: win.height / win.zoom
        scale: win.zoom
        transformOrigin: Item.TopLeft

        // ---- caption, menu strip, tab strip -----------------------------------
        // one backing under the whole strip over thin glass (Theme.bed)
        Rectangle {
            objectName: "stripBed"
            width: parent.width
            height: stage.y          // ends where the page begins: no overlap with a page's own tab bar
            visible: Theme.bedAlpha > 0
            color: Theme.bed
        }
        TitleBar {
            id: titleBar
            visible: win.customFrame
            height: visible ? implicitHeight : 0
            width: parent.width
            window: win
            centerText: Shell.projectName
            centerClickable: true
            onCenterClicked: projectPicker.opened ? projectPicker.close() : projectPicker.open()
        }

        StageTabBar {
            id: tabs
            x: 10
            y: (titleBar.visible ? titleBar.height : 0) + 6
            implicitHeight: 44
            model: win.stageNames
            numberedCount: 4          // the four stages; Extra is not a fifth
            bed: false                // stripBed
            onActivated: (i) => stage.show(i, false)
            // Extra's tools drop down on hover; a click on Extra still opens the one last used (user, 2026-10-18)
            menus: ({ 4: ["Scene Cut", "Depth Map Editor", "Edge Fill"] })
            menuCurrent: ({ 4: p4.tool })
            onMenuPicked: (i, item) => {
                if (i !== 4) return
                p4.tool = item
                stage.show(4, false)
            }
        }
        // Live VRAM (job_stats.VramMonitor: NVML every 2 s, the whole GPU),
        // at the right end of the stage strip. Redrawn only when it moves.
        // Beginner Mode (user, 2026-09-27: out of Settings, a switch by the gear).
        // v1: hides the advanced settings, renames a few, pins some, and
        // switches the hover text to plain language (beginner.py).
        // The look, one click away (user, 2026-09-27): theme, glass, UI scale,
        // outlines -- between the bell and the VRAM pill (user, 2026-10-08).
        ThemePicker {
            id: themePicker
            window: win
            anchors.right: vramPill.visible ? vramPill.left : parent.right
            anchors.rightMargin: vramPill.visible ? 8 : 16
            y: tabs.y + (tabs.height - height) / 2
        }
        Row {
            id: beginnerSwitch
            objectName: "beginnerSwitch"
            // Settings > Studio can hide it; never while the mode is on, or there'd be no way out
            visible: !Shell.hideBeginnerSwitch || Beginner.on
            spacing: 8
            anchors.right: settingsButton.left
            anchors.rightMargin: 10
            y: tabs.y + (tabs.height - height) / 2
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: "Beginner Mode"
                color: Beginner.on ? Theme.fg : Theme.fgMuted
                font.family: Theme.tabFont
                font.pixelSize: Theme.smallSize + 1
                font.letterSpacing: 0.5
            }
            Toggle {
                objectName: "beginnerToggle"
                anchors.verticalCenter: parent.verticalCenter
                checked: Beginner.on
                onToggled: { Beginner.setEnabled(checked); checked = Qt.binding(() => Beginner.on) }
            }
            HoverTip { tip: "Beginner Mode: hides the advanced settings, gives some plainer names, and switches the hover help to plain language." }
        }
        // Settings (user, 2026-09-27: in place of the menu bar), left of the bell.
        IconButton {
            id: settingsButton
            objectName: "settingsButton"
            size: 34
            iconGlyph: Theme.icon.settings
            turnOnHover: true
            anchors.right: bell.left
            anchors.rightMargin: 4
            y: tabs.y + (tabs.height - height) / 2
            onClicked: settings.openAt(0)      // on the stage, not the last section shown (sweep 2026-10-14c)
            HoverTip { tip: "Settings (Ctrl+,): theme, glass, scale, and each stage's options." }
        }
        // The notification bell (user, 2026-10-05): between Settings and the
        // theme picker, every toast of the session kept in it.
        NotificationBell {
            id: bell
            anchors.right: themePicker.left
            anchors.rightMargin: 8
            y: tabs.y + (tabs.height - height) / 2
        }
        Rectangle {
            id: vramPill
            objectName: "vramPill"
            visible: typeof Vram !== "undefined" && Vram.available && Vram.total > 0
            anchors.right: parent.right
            anchors.rightMargin: 16
            y: tabs.y + (tabs.height - height) / 2
            height: 28
            width: vramRow.implicitWidth + 20
            radius: 8
            color: Theme.alpha(Theme.fg, 0.04)
            border.width: 1
            border.color: Theme.border
            // in the text's 0.1 GB steps: the bar moves only when the number does
            // (every redraw during a job costs it; see Spinner)
            readonly property real frac: visible ? Math.min(1, Math.round(Vram.used * 10) / 10 / Vram.total) : 0
            Row {
                id: vramRow
                anchors.centerIn: parent
                spacing: 8
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: "VRAM"
                    color: Theme.fgMuted
                    font.family: Theme.tabFont
                    font.pixelSize: Theme.smallSize
                    font.letterSpacing: 1
                }
                Text {
                    id: vramText
                    objectName: "vramText"
                    anchors.verticalCenter: parent.verticalCenter
                    text: vramPill.visible ? Vram.used.toFixed(1) + " / " + Vram.total.toFixed(1) + " GB" : ""
                    color: Theme.fg
                    font.family: Theme.bodyFont
                    font.pixelSize: Theme.smallSize + 1
                    font.features: { "tnum": 1 }
                    // as wide as it can get on this card ("total / total GB": used never
                    // passes the total, and the digits are tabular), so 9.9 -> 10.0 GB
                    // can't widen the pill and, through headerWidth, the window's
                    // minimum width mid-job (bug sweep 2026-10-09, A1)
                    width: Math.ceil(vramWidest.advanceWidth)
                    horizontalAlignment: Text.AlignRight
                    TextMetrics {
                        id: vramWidest
                        font: vramText.font
                        text: vramPill.visible ? Vram.total.toFixed(1) + " / " + Vram.total.toFixed(1) + " GB" : ""
                    }
                }
                Rectangle {
                    anchors.verticalCenter: parent.verticalCenter
                    width: 54; height: 5; radius: 2.5
                    color: Theme.trough
                    Rectangle {
                        width: parent.width * vramPill.frac
                        height: parent.height; radius: 2.5
                        color: vramPill.frac > 0.9 ? Theme.warning : Theme.accent
                    }
                }
            }
            // a click shows the VRAM Status window (user, 2026-09-27: in place of
            // Settings > GPU > Show VRAM Status)
            function showStatus() {
                prompt.ask({ title: "VRAM Status", message: Shell.vramStatus(),
                             buttons: [{ text: "OK", code: 1, kind: "filled" }] })
            }
            HoverHandler { id: vramHover; cursorShape: Qt.PointingHandCursor; enabled: !Theme.modalOpen }
            TapHandler { objectName: "vramTap"; enabled: !Theme.modalOpen; onTapped: vramPill.showStatus() }
            Rectangle {
                anchors.fill: parent; radius: parent.radius
                color: Theme.hover
                opacity: vramHover.hovered ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: Theme.dur(120) } }
            }
            HoverTip { tip: "GPU memory in use on the whole card (every program, not only Studio), updated every 2 seconds. Click for the details: free memory and what Studio itself has allocated." }
        }
        Rectangle {
            id: tabRule
            y: tabs.y + tabs.height
            width: parent.width
            height: 1
            color: Theme.border
        }

        // Only while THIS window has the keyboard (Shell.focusWindow; see
        // shell.py for why not Window.active): the editor and Full Preview
        // windows are transient children of this one, and Alt+F4 pressed in
        // one of them must close that window, not the whole of Studio.
        readonly property bool keysHere: Shell.focusWindow === win
        Shortcut { enabled: zoomRoot.keysHere; sequences: ["Ctrl+Tab"]; onActivated: stage.show((stage.current + 1) % win.stageNames.length, false) }
        Shortcut { enabled: zoomRoot.keysHere; sequences: ["Ctrl+Shift+Tab"]; onActivated: stage.show((stage.current + win.stageNames.length - 1) % win.stageNames.length, false) }
        Shortcut { enabled: zoomRoot.keysHere; sequence: "Ctrl+`"; onActivated: consoleWindow.open = !consoleWindow.open }
        Shortcut { enabled: zoomRoot.keysHere; sequence: "Ctrl+,"; onActivated: settings.opened ? settings.close() : settings.openAt(0) }
        // Settings' Fit Window to Content (user, 2026-10-01)
        Shortcut { objectName: "fitShortcut"; enabled: zoomRoot.keysHere; sequence: "Ctrl+0"; onActivated: win.fitToContent() }
        // (user, 2026-10-02) Ctrl+1-4 a stage, Ctrl+5 Extra; Ctrl+P the projects;
        // F1 the manual at this stage; Ctrl+= / Ctrl+- UI Scale. All listed in
        // Settings > Keyboard Shortcuts.
        Repeater {
            model: win.stageNames.length
            Item {
                required property int index
                Shortcut { objectName: "stageShortcut"; enabled: zoomRoot.keysHere; sequence: "Ctrl+" + (index + 1); onActivated: stage.show(index, false) }
            }
        }
        Shortcut { objectName: "projectsShortcut"; enabled: zoomRoot.keysHere; sequence: "Ctrl+P"; onActivated: projectPicker.opened ? projectPicker.close() : projectPicker.open() }
        Shortcut {
            objectName: "manualShortcut"
            enabled: zoomRoot.keysHere
            sequence: "F1"
            onActivated: {
                const at = win.manualAt(stage.current)
                if (!Shell.openManual(at))
                    Theme.toast("warning", "Manual not found", "The manual isn't in this Studio folder (docs/manual).")
            }
        }
        Shortcut { objectName: "scaleUpShortcut"; enabled: zoomRoot.keysHere; sequences: ["Ctrl+=", "Ctrl++"]; onActivated: win.stepUiScale(5) }
        Shortcut { objectName: "scaleDownShortcut"; enabled: zoomRoot.keysHere; sequences: ["Ctrl+-"]; onActivated: win.stepUiScale(-5) }
        // The custom frame has no WS_SYSMENU (see platform_win.WindowFrame), so
        // Windows may not turn Alt+F4 into a close by itself.
        Shortcut { enabled: zoomRoot.keysHere; sequence: "Alt+F4"; context: Qt.ApplicationShortcut; onActivated: win.close() }

        // ---- pages ------------------------------------------------------------
        Item {
            id: stage
            objectName: "stage"
            y: tabRule.y + 1
            width: parent.width
            height: parent.height - y - statusBar.height
            clip: true

            property int current: -1
            property Item inPage: null
            property Item outPage: null
            property real outX: 0
            readonly property var pages: [p0, p1, p2, p3, p4]

            // A page taller than the window scrolls (user, 2026-10-08: "make the controls scrollable
            // if they don't all fit on a screen ... scrollable and grabbable like a touchscreen"): the
            // window's minimum height stops at the screen (minimumHeight), each page keeps at least
            // its required height, and the wheel, the bar or a drag on an empty part scrolls it.
            // UI Scale still shrinks what has to fit.
            Flickable {
                id: stageScroll
                objectName: "stageScroll"
                anchors.fill: parent
                contentWidth: width
                contentHeight: win.currentPage ? win.currentPage.height : height
                readonly property bool scrollable: contentHeight > height + 1
                interactive: scrollable
                boundsBehavior: Flickable.StopAtBounds
                flickableDirection: Flickable.VerticalFlick
                ScrollBar.vertical: SlimScrollBar { policy: stageScroll.scrollable ? ScrollBar.AlwaysOn : ScrollBar.AlwaysOff }

                DepthCrafterPage { id: p0; width: stage.width; height: stage.pageHeight(p0); visible: false; active: stage.current === 0; prompt: prompt; consoleRef: consoleWindow }
                SplattingPage { id: p1; width: stage.width; height: stage.pageHeight(p1); visible: false; active: stage.current === 1; prompt: prompt; consoleRef: consoleWindow }
                InpaintingPage { id: p2; width: stage.width; height: stage.pageHeight(p2); visible: false; active: stage.current === 2; prompt: prompt; consoleRef: consoleWindow }
                MergingPage { id: p3; width: stage.width; height: stage.pageHeight(p3); visible: false; active: stage.current === 3; prompt: prompt; consoleRef: consoleWindow }
                ExtraPage { id: p4; width: stage.width; height: stage.pageHeight(p4); visible: false; active: stage.current === 4; prompt: prompt; consoleRef: consoleWindow }
            }

            function pageHeight(p) { return Math.max(stage.height, Math.ceil(p.requiredHeight || 0)) }

            function settle(p) { p.x = 0; p.opacity = 1 }

            function show(i, instant) {
                if (i === current || i < 0 || i >= pages.length) return
                switchAnim.complete()
                const from = current >= 0 ? pages[current] : null
                const to = pages[i]
                for (const p of pages) if (p !== from && p !== to) { p.visible = false; settle(p) }
                settle(to)
                tabs.currentIndex = i
                Shell.lastStage = i
                stageScroll.contentY = 0
                const dir = i > current ? 1 : -1
                current = i
                Beginner.reapply()         // v1 re-pins on every activation
                if (!win.userSized) Qt.callLater(win.fitToContent)
                to.visible = true
                to.z = 1
                if (!from || instant) {
                    if (from) { from.visible = false; settle(from) }
                    return
                }
                settle(from)
                from.z = 0
                to.x = dir * width * 0.3
                to.opacity = 0
                outX = -dir * width * 0.2
                outPage = from
                inPage = to
                switchAnim.start()
            }

            ParallelAnimation {
                id: switchAnim
                NumberAnimation { target: stage.inPage; property: "x"; to: 0; duration: Theme.dur(420); easing.type: Easing.OutCubic }
                NumberAnimation { target: stage.inPage; property: "opacity"; to: 1; duration: Theme.dur(320); easing.type: Easing.OutQuad }
                NumberAnimation { target: stage.outPage; property: "x"; to: stage.outX; duration: Theme.dur(420); easing.type: Easing.OutCubic }
                NumberAnimation { target: stage.outPage; property: "opacity"; to: 0; duration: Theme.dur(240); easing.type: Easing.OutQuad }
                onFinished: {
                    if (stage.outPage && stage.outPage !== stage.inPage) {
                        stage.outPage.visible = false
                        stage.settle(stage.outPage)
                    }
                }
            }
        }

        // ---- status bar (v1's shell status line) ------------------------------
        Rectangle {
            id: statusBar
            anchors.bottom: parent.bottom
            width: parent.width
            height: 24
            color: Theme.alpha(Theme.fg, 0.04)
            Rectangle { z: -1; anchors.fill: parent; visible: Theme.bedAlpha > 0; color: Theme.bed }
            Rectangle { width: parent.width; height: 1; color: Theme.border }
            MorphLabel {
                x: 10
                anchors.verticalCenter: parent.verticalCenter
                text: win.runningStage ? win.runningStage.name + ": " + win.runningStage.ctl.status : "Ready"
                color: Theme.fgMuted
                family: Theme.bodyFont
                pixelSize: 12
            }
        }

        // v1's tabbed Console window, a window of its own (user, 2026-09-30)
        ConsoleWindow {
            id: consoleWindow
            stageIndex: stage.current
            owner: zoomRoot
        }

        // notifications drop in at the top centre, under the stage tabs (user, 2026-10-05)
        ToastHost {
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.top: parent.top
            anchors.topMargin: tabs.y + tabs.height + 10
            height: 420
            z: 100
        }

        PromptDialog { id: prompt; objectName: "prompt" }

        // Settings > Studio > Check for Updates (updater.py; user, 2026-09-30)
        UpdateDialog {
            id: updateDlg
            jobRunning: win.runningStage !== null
            onInstallRefused: (why) => prompt.ask({ title: "Can't install the update", message: why,
                                                    buttons: [{ text: "OK", code: 1, kind: "filled" }] })
        }

        // Settings > Studio > Models (models_controller.py; user, 2026-10-20)
        ModelsDialog {
            id: modelsDlg
            jobRunning: win.runningStage !== null
            onAskSignOut: (opts) => prompt.ask(opts)
        }

        SettingsDialog {
            id: settings
            window: win
            // the stage showing first (Extra: its tool's own), then Studio and GPU
            // (user, 2026-10-10: the stage's section at the top, Studio under it)
            // manual: where its "Open the manual" link goes ("" the contents)
            sections: (stage.current >= 0 ? [{ name: win.stageNames[stage.current],
                                               menus: stage.pages[stage.current].stageMenus || [],
                                               extra: stage.pages[stage.current].settingsExtra || null,
                                               manual: win.manualAt(stage.current),
                                               manualName: win.manualName(stage.current) }] : []).concat(
                [{ name: "Studio", menus: [studioMenu], manual: "", manualName: "" },
                 { name: "GPU", menus: [gpuMenu] }])
        }
    }

    // ---- film grain (grain.py; user, 2026-09-28) ---------------------------------
    // One static tile over everything, one grain per SCREEN pixel (outside the
    // UI Scale zoom), never taking the mouse. The previews stay clean: the
    // window minus each GrainHole's rectangle is cut into bands of plain
    // rectangles, each showing the same window-aligned tile clipped to itself
    // (no layers, no effect: nothing extra in video memory). Popups and dialogs
    // sit above it, clean too. Nothing animates: while a job runs the holes are
    // looked at every 2 s, and pieces are only rebuilt when a hole has moved.
    Item {
        id: grainLayer
        objectName: "grainLayer"
        anchors.fill: parent
        z: 1000
        enabled: false
        visible: Theme.grainSource !== ""
        readonly property real dpr: win.screen ? win.screen.devicePixelRatio : 1
        property var holes: []          // [{x, y, w, h}] in this item
        // the window minus the holes, as rectangles
        readonly property var pieces: {
            const W = Math.ceil(width), H = Math.ceil(height)
            if (!holes.length) return [{ x: 0, y: 0, w: W, h: H }]
            const ys = [0, H]
            for (const h of holes) ys.push(Math.max(0, Math.min(H, h.y)), Math.max(0, Math.min(H, h.y + h.h)))
            const cuts = Array.from(new Set(ys)).sort((a, b) => a - b)
            const out = []
            for (let i = 0; i + 1 < cuts.length; ++i) {
                const y0 = cuts[i], y1 = cuts[i + 1]
                if (y1 <= y0) continue
                const spans = holes.filter(h => h.y < y1 && h.y + h.h > y0)
                                   .map(h => [Math.max(0, h.x), Math.min(W, h.x + h.w)])
                                   .sort((a, b) => a[0] - b[0])
                let x = 0
                for (const sp of spans) {
                    if (sp[0] > x) out.push({ x: x, y: y0, w: sp[0] - x, h: y1 - y0 })
                    x = Math.max(x, sp[1])
                }
                if (x < W) out.push({ x: x, y: y0, w: W - x, h: y1 - y0 })
            }
            return out
        }
        function refreshHoles() {
            const out = []
            for (const h of Theme.grainHoles) {
                if (!h || !h.visible || h.width <= 0 || h.height <= 0) continue
                const p = h.mapToItem(grainLayer, 0, 0)
                const q = h.mapToItem(grainLayer, h.width, h.height)
                let x0 = p.x, y0 = p.y, x1 = q.x, y1 = q.y
                if (h.clipTo) {
                    const cp = h.clipTo.mapToItem(grainLayer, 0, 0)
                    const cq = h.clipTo.mapToItem(grainLayer, h.clipTo.width, h.clipTo.height)
                    x0 = Math.max(x0, cp.x); y0 = Math.max(y0, cp.y); x1 = Math.min(x1, cq.x); y1 = Math.min(y1, cq.y)
                }
                if (x1 - x0 < 1 || y1 - y0 < 1) continue
                out.push({ x: Math.round(x0), y: Math.round(y0), w: Math.round(x1 - x0), h: Math.round(y1 - y0) })
            }
            if (JSON.stringify(out) !== JSON.stringify(holes)) holes = out
        }
        Timer {
            interval: Theme.calm ? 2000 : 200
            repeat: true
            running: grainLayer.visible && win.visible
            triggeredOnStart: true
            onTriggered: grainLayer.refreshHoles()
        }
        Repeater {
            model: grainLayer.pieces
            Item {
                required property var modelData
                objectName: "grainPiece"
                x: modelData.x; y: modelData.y; width: modelData.w; height: modelData.h
                clip: true
                Image {
                    objectName: "grainImage"
                    x: -parent.x; y: -parent.y           // the tile stays put across pieces
                    width: grainLayer.width * grainLayer.dpr
                    height: grainLayer.height * grainLayer.dpr
                    scale: 1 / grainLayer.dpr
                    transformOrigin: Item.TopLeft
                    fillMode: Image.Tile
                    smooth: false
                    source: Theme.grainSource
                }
            }
        }
    }
}
