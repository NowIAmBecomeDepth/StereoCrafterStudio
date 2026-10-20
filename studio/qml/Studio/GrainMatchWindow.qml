import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Window
import Studio

// v1's Grain Match Preview (merging_gui.open_grain_preview): the clip's blended
// eye with grain on the fills, three magnified crops (master / blend / blend +
// grain) to judge by eye, the measured grain sigma of master vs fill, and the
// grain + fill-correction controls, so the main window isn't needed. The rules
// are in stages/merging/grain.GrainMatch on the preview's worker; this is only
// the window. The sliders are the stage's own settings (v1: the same tk
// variables as the main window), so moving one re-renders both.
Window {
    id: win
    property var pv: null
    property var st: null              // Merging's settings
    property var prompt: null          // for the refusal / chooser, in the main window
    property var s: ({})               // grain.GrainMatch.state(), latest
    property int serial: 0
    property bool wanted: false
    // v1 Dim UI: black chrome and muted text, so the pictures are the
    // brightest thing on screen. Remembered for the session, as v1 does.
    property bool dim: false

    readonly property int frameNo: s.frame !== undefined ? s.frame : 0
    readonly property int total: Math.max(1, s.frames || 1)
    readonly property var titles: s.titles || ["", "", ""]

    title: s.title || "Grain Match Preview"
    // v1: open wide (the crop row needs it), height fitted to the controls,
    // never past the screen.
    // this window's screen, not the whole desktop (two screens side by side or
    // stacked would add up; see Main.screenRoomHeight)
    readonly property var room: (win.screen, Shell.screenRoom(win))
    readonly property real roomHeight: (room.height || 1040) - 70
    width: Math.min(2100, (room.width || 1920) * 0.92)
    height: Math.min(900, roomHeight)
    minimumWidth: 1100
    minimumHeight: Math.min(720, roomHeight)
    function fitHeight() { height = Math.max(minimumHeight, Math.min(roomHeight, rightCol.implicitHeight + 20)) }
    color: dim ? "#000000" : Theme.bg
    visible: false

    function openFor() {
        if (visible) { raise(); requestActivate(); return }
        wanted = true
        pv.openGrain(-1)
    }
    onClosing: {
        wanted = false
        pv.closeGrain()
    }

    QtObject {
        id: local
        property real sigma: 0
    }

    Connections {
        target: win.pv
        function onGrainChanged(state) {
            if (!win.wanted) return
            if (!state.open) { if (win.visible) win.close(); return }
            const scanEnded = win.s.scanning === true && state.scanning === false
            win.s = state
            win.serial = state.serial
            // v1 _poll_suggestions: the buttons grow the column; fit again.
            if (scanEnded) Qt.callLater(win.fitHeight)
            // A render that left before the last Amount change reports the
            // old Amount: taking it would throw the slider back until the
            // newer render lands (user, 2026-09-26).
            if (!sigmaRow.pressed && !sigmaTimer.running && (state.seq || 0) >= win.sigmaSent) {
                win.syncing = true
                local.sigma = state.sigmaTarget
                win.syncing = false
            }
            if (!scrub.pressed) scrub.value = state.frame
            if (!win.visible) { win.show(); win.raise(); win.requestActivate(); Qt.callLater(win.fitHeight) }
        }
        function onGrainRefused(message) {
            if (!win.wanted) return
            win.wanted = false
            if (win.prompt) win.prompt.ask({ title: "Grain Preview", message: message })
        }
        // v1 _choose_from_list: several dual-eye clips qualify.
        function onGrainChoose(names) {
            if (!win.wanted || !win.prompt) return
            const buttons = []
            for (let i = 0; i < names.length; ++i) buttons.push({ text: names[i], code: i + 1, kind: i === 0 ? "filled" : "outline" })
            buttons.push({ text: "Cancel", code: 0, kind: "ghost" })
            win.prompt.ask({ title: "Grain Preview — pick a dual-eye clip", message: "Which clip should the grain be tuned on?",
                             buttons: buttons, onDone: (code) => { if (code > 0) win.pv.openGrain(code - 1); else win.wanted = false } })
        }
        function onGrainMessage(kind, text) {
            msgDlg.ask({ title: "Grain Preview", message: text, buttons: [{ text: "OK", code: 1, kind: "filled" }] })
        }
    }

    // The sigma slider converts to Amount on the worker (v1 _on_sigma_target).
    Timer {
        id: sigmaTimer
        interval: sigmaRow.pressed ? 0 : 60     // live while held
        onTriggered: win.sigmaSent = win.pv.grainOp("sigma", [local.sigma])
    }
    property int sigmaSent: 0
    // Every change that is not the render's own echo (drag, wheel, right-click
    // reset, the -/+ steppers) goes to the worker.
    property bool syncing: false
    Connections {
        target: local
        function onSigmaChanged() { if (!win.syncing) sigmaTimer.restart() }
    }
    function stepSigma(delta) {
        local.sigma = Math.max(0, Math.min(0.03, Math.round((local.sigma + delta) * 10000) / 10000))
    }
    function stepSetting(key, delta, lo, hi, places) {
        const v = Number(win.st[key]) + delta
        const f = Math.pow(10, places)
        win.st[key] = Math.round(Math.max(lo, Math.min(hi, v)) * f) / f
    }
    Timer {
        id: seekTimer
        interval: 120
        onTriggered: win.pv.grainOp("seek", [Math.round(scrub.value)])
    }

    PromptDialog { id: msgDlg }

    component Caption: Text {
        color: win.dim ? "#8a8a8a" : Theme.fg
        font.family: Theme.bodyFont
        font.pixelSize: Theme.bodySize
        Layout.alignment: Qt.AlignVCenter
    }
    component Muted: Caption { color: win.dim ? "#5f5f5f" : Theme.fgMuted; wrapMode: Text.Wrap }
    // Held: every change renders at once, newest wins (preview.py) -- live.
    component GSlider: SliderRow {
        store: win.st; labelWidth: 110; valueWidth: 60
        onPressedChanged: win.pv.setDragging(pressed)
    }
    component Stepper: AccentButton { kind: "outline"; implicitWidth: 28 }
    component CropPane: ColumnLayout {
        id: pane
        property string name
        property string heading
        spacing: 2
        Caption { text: pane.heading; elide: Text.ElideRight; Layout.maximumWidth: 290 }
        Rectangle {
            implicitWidth: 284; implicitHeight: 284
            color: win.dim ? "#000000" : Theme.alpha(Theme.fg, 0.04)
            Image {
                anchors.fill: parent
                fillMode: Image.PreserveAspectFit
                smooth: false          // v1: NEAREST, so magnified grain stays pixel-honest
                cache: false
                source: win.serial > 0 ? "image://mergepreview/grain_" + pane.name + "/" + win.serial : ""
            }
        }
    }

    RowLayout {
        anchors.fill: parent
        anchors.margins: 10
        spacing: 12

        // -- left: the view bar, the whole frame with the crop box, the buttons
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumWidth: 160
            Layout.preferredWidth: 400
            spacing: 6
            // Wraps rather than widening the column (v1's minimum is 1100 wide).
            Flow {
                Layout.fillWidth: true
                spacing: 8
                Caption { text: "Crop zoom:"; height: 30; verticalAlignment: Text.AlignVCenter }
                SegmentedControl {
                    objectName: "grainZoom"
                    compact: true
                    model: ["1x", "1.5x", "2x"]
                    currentIndex: Math.max(0, model.indexOf(win.s.cropZoom || "1.5x"))
                    onActivated: (i) => win.pv.grainOp("set_zoom", [model[i]])
                }
                Caption { text: "Eye:"; visible: !!win.s.dualEye; height: 30; verticalAlignment: Text.AlignVCenter }
                SegmentedControl {
                    visible: !!win.s.dualEye
                    compact: true
                    model: ["Left", "Right"]
                    currentIndex: win.s.eye === "L" ? 0 : 1
                    onActivated: (i) => win.pv.grainOp("set_eye", [i === 0 ? "L" : "R"])
                }
                AnimCheck {
                    text: "Dim UI"; checked: win.dim
                    onToggled: win.dim = checked
                }
                Muted {
                    text: "Resize the window to scale the preview • click image to place the crop • right-click for auto"
                    width: Math.min(implicitWidth, parent.width); height: 30; verticalAlignment: Text.AlignVCenter
                    wrapMode: Text.NoWrap; elide: Text.ElideRight
                }
            }
            Item {
                id: fullHolder
                Layout.fillWidth: true
                Layout.fillHeight: true
                readonly property real fit: (win.s.imageWidth || 0) > 0
                    ? Math.min(width / win.s.imageWidth, height / win.s.imageHeight, 1.0 / (Screen.devicePixelRatio || 1)) : 1
                Image {
                    id: fullImage
                    objectName: "grainFull"
                    x: 0; y: 0
                    width: (win.s.imageWidth || 0) * fullHolder.fit
                    height: (win.s.imageHeight || 0) * fullHolder.fit
                    cache: false
                    smooth: true
                    mipmap: true
                    source: win.serial > 0 ? "image://mergepreview/grain_full/" + win.serial : ""
                    // v1: the magnifier's footprint, in red.
                    Rectangle {
                        readonly property var r: win.s.cropRect || [0, 0, 0]
                        x: r[0] * fullHolder.fit; y: r[1] * fullHolder.fit
                        width: r[2] * fullHolder.fit; height: r[2] * fullHolder.fit
                        color: "transparent"
                        border.width: 2
                        border.color: "#ff4444"
                        visible: r[2] > 0
                    }
                    MouseArea {
                        anchors.fill: parent
                        acceptedButtons: Qt.LeftButton | Qt.RightButton
                        onClicked: (m) => {
                            if (m.button === Qt.RightButton) win.pv.grainOp("auto_crop", [])
                            else win.pv.grainOp("click", [m.x / fullHolder.fit, m.y / fullHolder.fit])
                        }
                    }
                }
            }
            Flow {
                Layout.fillWidth: true
                spacing: 8
                AccentButton { objectName: "grainSave"; text: "Save Grain to Sidecar"; onClicked: win.pv.grainOp("save_sidecar", []) }
                AccentButton { text: "Close"; kind: "outline"; onClicked: win.close() }
                Muted {
                    text: "Grain settings persist globally; Save writes a per-clip override."
                    width: Math.min(implicitWidth, parent.width); height: 34; verticalAlignment: Text.AlignVCenter
                    wrapMode: Text.NoWrap; elide: Text.ElideRight
                }
            }
        }

        // -- right: the crops, sigma, the controls, suggestions, the scrubber
        ColumnLayout {
            id: rightCol
            Layout.alignment: Qt.AlignTop
            // The three crop panes, 284 each.
            Layout.preferredWidth: 860
            Layout.minimumWidth: 860
            Layout.maximumWidth: 860
            spacing: 6
            RowLayout {
                spacing: 4
                CropPane { name: "master"; heading: win.titles[0] }
                CropPane { name: "before"; heading: win.titles[1] }
                CropPane { name: "after"; heading: win.titles[2] }
            }
            Caption { objectName: "grainSigma"; text: win.s.sigmaText || ""; font.bold: true; Layout.topMargin: 4; wrapMode: Text.Wrap; Layout.fillWidth: true; Layout.preferredWidth: 860 }
            Caption { text: "Match fill σ to master σ, then confirm by eye on the crop." }

            Section {
                title: "Grain Controls"
                Layout.fillWidth: true
                opacity: win.dim ? 0.75 : 1
                RowLayout {
                    Layout.fillWidth: true
                    CheckField { store: win.st; key: "grain_enable"; text: "Enable grain on render" }
                    Item { Layout.fillWidth: true }
                    Muted { text: win.st.grain_enable ? "grain WILL be applied on render" : "preview only — enable to apply on render"; wrapMode: Text.NoWrap }
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 4
                    SliderRow {
                        id: sigmaRow
                        objectName: "grainSigmaSlider"
                        store: local; key: "sigma"; label: "Amount (σ):"; labelWidth: 110; valueWidth: 60
                        onPressedChanged: win.pv.setDragging(pressed)
                        from: 0; to: 0.03; step: 0.0001; decimals: 4
                        defaultValue: win.s.sigmaDefault !== undefined ? win.s.sigmaDefault : NaN
                    }
                    Stepper { text: "−"; onClicked: win.stepSigma(-0.0001) }
                    Stepper { text: "+"; onClicked: win.stepSigma(0.0001) }
                }
                GSlider { key: "grain_size"; label: "Size (px):"; from: 0; to: 3; step: 0.01; decimals: 2; defaultValue: 1.2 }
                GSlider { key: "grain_luma_falloff"; label: "Luma Falloff:"; from: 0; to: 1; step: 0.01; decimals: 2; defaultValue: 0.6 }
                GSlider { key: "grain_chroma"; label: "Chroma:"; from: 0; to: 0.5; step: 0.01; decimals: 2; defaultValue: 0.12 }
                RowLayout {
                    Layout.fillWidth: true
                    Layout.topMargin: 6
                    Caption { text: "Fill Color Correction"; font.bold: true }
                    Item { Layout.fillWidth: true }
                    AccentButton {
                        text: "Reset to Default"; kind: "outline"
                        onClicked: win.pv.grainOp("reset_fill", [])
                        HoverTip { tip: "Sets Red Reduce and Brightness to 0 and Contrast to 1, then saves them now.\n\n"
                                        + "Grain settings above are left alone; right-click any single slider\nto reset just that one." }
                    }
                }
                GSlider { key: "fill_red_reduce"; label: "Red Reduce:"; from: 0; to: 1; step: 0.01; decimals: 2; defaultValue: 0 }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 4
                    GSlider { key: "fill_brightness"; label: "Brightness:"; from: -0.05; to: 0.05; step: 0.001; decimals: 3; defaultValue: 0 }
                    Stepper { text: "−"; onClicked: win.stepSetting("fill_brightness", -0.001, -0.05, 0.05, 3) }
                    Stepper { text: "+"; onClicked: win.stepSetting("fill_brightness", 0.001, -0.05, 0.05, 3) }
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 4
                    GSlider { key: "fill_contrast"; label: "Contrast:"; from: 0.5; to: 1.5; step: 0.001; decimals: 3; defaultValue: 1 }
                    Stepper { text: "−"; onClicked: win.stepSetting("fill_contrast", -0.001, 0.5, 1.5, 3) }
                    Stepper { text: "+"; onClicked: win.stepSetting("fill_contrast", 0.001, 0.5, 1.5, 3) }
                }
                RowLayout {
                    Layout.topMargin: 6
                    spacing: 6
                    Caption { text: "Seed (-1 = random):" }
                    TextBox {
                        store: win.st; key: "grain_seed"; boxWidth: 64
                        HoverTip { tip: "Fixed seed reproduces the exact grain pattern between runs.\n-1 draws fresh grain each run." }
                    }
                    AccentButton {
                        objectName: "grainAutoMatch"
                        text: "Auto-Match Amount"; Layout.leftMargin: 8
                        onClicked: win.pv.grainOp("auto_match", [])
                        HoverTip { tip: "Sets Amount so the fill's measured grain σ equals the master's,\nusing the current frame.\n\n"
                                        + "Grain adds in quadrature on top of the fill's own residual noise, so\n"
                                        + "the solver targets sqrt(master² - fill_ungrained²) and drops that\n"
                                        + "straight into the Amount (σ) slider — same scale as the readout.\n\n"
                                        + "Size, Luma Falloff and Chroma are NOT auto-set — judge those by eye\n"
                                        + "on the crop panes. Changing them re-measures the gain, so the σ\n"
                                        + "reading stays honest and Amount follows." }
                    }
                }
                Muted { objectName: "grainAutoStatus"; text: win.s.automatchStatus || ""; visible: text.length > 0; Layout.maximumWidth: 420 }
                Muted { text: win.s.fillStatus || ""; visible: text.length > 0; Layout.maximumWidth: 420 }
            }

            Section {
                title: "Suggested frames (most fill area)"
                Layout.fillWidth: true
                opacity: win.dim ? 0.75 : 1
                RowLayout {
                    spacing: 4
                    Muted {
                        visible: !!win.s.scanning || (win.s.suggestions || []).length === 0
                        text: win.s.scanning ? "Scanning the clip…" : "No frames with fill found."
                        wrapMode: Text.NoWrap
                    }
                    Repeater {
                        model: win.s.suggestions || []
                        AccentButton {
                            required property var modelData
                            kind: "outline"
                            text: "Frame " + modelData.frame + "  (" + (modelData.coverage * 100).toFixed(1) + "% fill)"
                            onClicked: win.pv.grainOp("seek", [modelData.frame])
                        }
                    }
                }
            }

            Slider {
                id: scrub
                objectName: "grainScrub"
                Layout.fillWidth: true
                Layout.topMargin: 6
                from: 0; to: Math.max(0, win.total - 1); stepSize: 1; snapMode: Slider.SnapAlways
                onMoved: seekTimer.restart()
                background: Rectangle {
                    x: scrub.leftPadding; y: scrub.topPadding + scrub.availableHeight / 2 - 2
                    width: scrub.availableWidth; height: 4; radius: 2; color: Theme.trough
                    Rectangle { width: scrub.visualPosition * parent.width; height: 4; radius: 2; color: Theme.accent }
                }
                handle: Rectangle {
                    x: scrub.leftPadding + scrub.visualPosition * (scrub.availableWidth - width)
                    y: scrub.topPadding + scrub.availableHeight / 2 - height / 2
                    width: 8; height: 18; radius: 3; color: Theme.knob; border.width: 2; border.color: Theme.accent
                }
            }
            Caption { text: "Frame " + (scrub.pressed ? Math.round(scrub.value) : win.frameNo) + " / " + (win.total - 1); font.features: { "tnum": 1 } }
        }
    }
}
