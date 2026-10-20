import QtQuick
import QtQuick.Window
import QtQuick.Controls.Basic
import Studio

// v1's Full Preview (core/ui/preview_canvas_window.py): the preview in its own
// window, mirroring the inline one live -- true full screen only (user, 2026-10-13: "instead
// of a windowed mostly full screen"), on the screen Studio is on (openFull). Escape or F11
// closes it, and so does an X that shows on opening, goes after a moment and comes back when
// the mouse moves. The picture is fitted to the screen; the wigglegram flips here too.
Window {
    id: win
    property var pv
    // The stage's image provider (image://<provider>/frame/<serial>).
    property string provider: "splatpreview"
    // Glasses-Free 3D (user, 2026-10-11): the stage ("splatting" / "merging") and whether its
    // facing view is Glasses-Free 3D -- then this window's picture is the one woven
    property string stage: ""
    property bool glasses: false
    // A few of the stage's own controls, floating under the frame slider (user, 2026-10-14:
    // Splatting's Convergence, Disparity and Gamma); null: none
    property Component dials: null
    title: "StereoCrafter Studio — Full Preview"
    width: 1280
    height: 720
    color: "black"
    visible: false
    onVisibleChanged: {
        if (visible)
            return
        // hidden under a held slider: its release never comes, and every render after stayed
        // the fast kind (bug sweep 2026-10-14c)
        if (win.pv)
            win.pv.setDragging(false)
        win.controlsShown = false
        if (win.screen && win.screen.name !== Shell.fullPreviewScreen)
            Shell.fullPreviewScreen = win.screen.name       // moved there (Win+Shift+arrow): next time too
    }
    // moved while open: remembered now, or opening it again (F11) put it back on the old screen
    // (bug sweep 2026-10-14c)
    onScreenChanged: if (visible && screen && screen.name !== Shell.fullPreviewScreen) Shell.fullPreviewScreen = screen.name

    // Open full screen on the screen it was last on -- a 3D TV, say -- or else `studioScreen`,
    // the screen of the window it was opened from.
    function openFull(studioScreen) {
        var screens = Qt.application.screens, last = null
        for (var i = 0; i < screens.length; i++)
            if (screens[i].name === Shell.fullPreviewScreen)
                last = screens[i]
        if (last || studioScreen)
            win.screen = last || studioScreen
        win.showFullScreen()
        win.requestActivate()
        win.revealControls()
    }

    // The X and the frame slider (user, 2026-10-13): shown on opening, gone after a moment,
    // back whenever the mouse moves; held while the mouse is on them or the slider is dragged.
    property bool controlsShown: false
    onControlsShownChanged: glassView.update()   // their holes open and close with them, not a poll later
    function revealControls() { controlsShown = true; hideTimer.restart() }
    Timer { id: hideTimer; objectName: "fullPreviewCloseTimer"; interval: 1600; onTriggered: win.controlsShown = false }

    Image {
        anchors.fill: parent
        fillMode: Image.PreserveAspectFit
        cache: false
        smooth: true
        mipmap: true
        visible: win.visible && win.pv && !win.pv.wiggling
        source: visible && win.pv.imageWidth > 0 ? "image://" + win.provider + "/frame/" + win.pv.imageSerial : ""
    }
    Image {
        id: wiggle
        anchors.fill: parent
        fillMode: Image.PreserveAspectFit
        cache: false
        visible: win.visible && win.pv && win.pv.wiggling
        property bool leftEye: true
        source: visible ? "image://" + win.provider + "/" + (leftEye ? "left" : "right") + "/" + win.pv.imageSerial : ""
        Timer {
            objectName: "wiggleTimer"
            // not while a job runs: no redraws then (Theme.calm, bug sweep A10)
            running: wiggle.visible && !Theme.calm
            interval: Math.round(1000 / Shell.wiggleSpeed)
            repeat: true
            onTriggered: wiggle.leftEye = !wiggle.leftEye
        }
    }
    // the picture's fitted rect (PreserveAspectFit), woven by the SR display in Glasses-Free 3D
    Item {
        id: fitted
        readonly property real aspect: win.pv && win.pv.imageHeight > 0 ? win.pv.imageWidth / win.pv.imageHeight : 16 / 9
        width: Math.min(win.width, win.height * aspect)
        height: width / aspect
        anchors.centerIn: parent
        visible: win.visible && !!win.pv && win.pv.imageWidth > 0
    }
    GlassesFreeView {
        id: glassView
        objectName: "glassesFreeView"
        extraHoles: [closeBtn, scrubBar, dialBar]   // so the X, the slider and the dials show over the 3D
        stage: win.stage
        pv: win.pv
        picture: fitted
        area: win.contentItem
        wanted: win.visible && win.glasses && win.stage !== ""
    }
    Text {
        anchors.centerIn: parent
        visible: !win.pv || !win.pv.loaded
        text: "No preview loaded."
        color: "#888"
        font.pixelSize: 16
    }

    // the mouse moving anywhere brings the X back
    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.NoButton
        onPositionChanged: win.revealControls()
    }
    // the X (user, 2026-10-13): a backup to Escape, shown on opening, gone after a moment
    Rectangle {
        id: closeBtn
        objectName: "fullPreviewClose"
        width: 44; height: 44; radius: 22
        anchors { top: parent.top; right: parent.right; margins: 20 }
        color: hit.containsMouse ? "#d0303030" : "#a0181818"
        border.color: "#60ffffff"
        border.width: 1
        opacity: win.controlsShown || hit.containsMouse ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.dur(220) } }
        Icon { anchors.centerIn: parent; glyph: Theme.icon.close; size: 16; color: "white" }
        MouseArea {
            id: hit
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: win.close()
        }
    }

    // the frame slider (user, 2026-10-13): floating in the middle, a little above the bottom
    Rectangle {
        id: scrubBar
        objectName: "fullPreviewScrub"
        readonly property bool usable: !!win.pv && win.pv.loaded && win.pv.frameCount > 1
        width: Math.min(900, win.width * 0.6)
        height: 52
        radius: 14
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: dialBar.shows ? dialBar.top : parent.bottom
        anchors.bottomMargin: dialBar.shows ? 10 : Math.max(40, win.height * 0.06)
        color: "#a0181818"
        border.color: "#40ffffff"
        border.width: 1
        opacity: usable && (win.controlsShown || barHover.hovered || scrub.pressed || dialHover.hovered) ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.dur(220) } }
        HoverHandler { id: barHover }
        Row {
            anchors.centerIn: parent
            spacing: 14
            Text {
                objectName: "fullPreviewFrameText"
                anchors.verticalCenter: parent.verticalCenter
                width: 120
                horizontalAlignment: Text.AlignRight
                color: "white"
                font.family: Theme.bodyFont
                font.pixelSize: 14
                font.features: { "tnum": 1 }
                text: scrubBar.usable ? "Frame " + ((scrub.pressed ? Math.round(scrub.value) : win.pv.frame) + 1)
                                        + " / " + win.pv.frameCount : ""
            }
            Slider {
                id: scrub
                objectName: "fullPreviewSlider"
                anchors.verticalCenter: parent.verticalCenter
                width: scrubBar.width - 120 - 14 - 40
                from: 0
                to: scrubBar.usable ? win.pv.frameCount - 1 : 0
                stepSize: 1
                snapMode: Slider.SnapAlways
                // the frame shown, but not while held: a late seek's result pulled the handle back
                // from under the hand (bug sweep 2026-10-14c)
                Binding on value { when: !scrub.pressed; value: scrubBar.usable ? win.pv.frame : 0; restoreMode: Binding.RestoreNone }
                focusPolicy: Qt.NoFocus         // the keys stay the window's (Space, arrows, Escape)
                // live while held, exactly where let go -- as the page's own slider
                property int lastSeek: -1
                onMoved: {
                    const f = Math.round(value)
                    if (pressed && f !== lastSeek) { lastSeek = f; win.pv.seek(f) }
                }
                onPressedChanged: {
                    if (!win.pv)
                        return
                    win.pv.setDragging(pressed)
                    if (!pressed) {
                        const f = lastSeek >= 0 ? lastSeek : Math.round(value)   // where the hand let go
                        lastSeek = -1
                        win.pv.seek(f)
                    }
                    win.revealControls()
                }
                background: Rectangle {
                    x: scrub.leftPadding
                    y: scrub.topPadding + scrub.availableHeight / 2 - 2
                    width: scrub.availableWidth
                    height: 4
                    radius: 2
                    color: "#55ffffff"
                    Rectangle { width: scrub.visualPosition * parent.width; height: 4; radius: 2; color: Theme.accent }
                }
                handle: Rectangle {
                    x: scrub.leftPadding + scrub.visualPosition * (scrub.availableWidth - width)
                    y: scrub.topPadding + scrub.availableHeight / 2 - height / 2
                    width: 10; height: 22; radius: 4
                    color: "white"
                    border.width: 2
                    border.color: Theme.accent
                }
            }
        }
    }

    // the stage's dials (`dials`), under the slider; shown and held as it is
    Rectangle {
        id: dialBar
        objectName: "fullPreviewDials"
        readonly property bool shows: win.dials !== null
        width: dialLoader.item ? dialLoader.item.implicitWidth + 40 : 0
        height: dialLoader.item ? dialLoader.item.implicitHeight + 20 : 0
        radius: 14
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: Math.max(40, win.height * 0.06)
        color: "#a0181818"
        border.color: "#40ffffff"
        border.width: 1
        opacity: shows && !!win.pv && win.pv.loaded && (win.controlsShown || dialHover.hovered || barHover.hovered) ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.dur(220) } }
        HoverHandler { id: dialHover; onHoveredChanged: if (!hovered) win.revealControls() }
        Loader {
            id: dialLoader
            anchors.centerIn: parent
            active: dialBar.shows
            sourceComponent: win.dials
        }
    }

    Shortcut {
        enabled: Shell.focusWindow === win
        sequence: "F11"
        onActivated: win.close()                // the key that opened it closes it too
    }
    Shortcut {
        enabled: Shell.focusWindow === win
        sequence: "Escape"
        onActivated: win.close()
    }
    // The playback keys work here too.
    readonly property bool keysLive: Shell.focusWindow === win && win.pv && win.pv.loaded
    Shortcut { enabled: win.keysLive; sequence: "Space"; onActivated: win.pv.togglePlay() }
    Shortcut { enabled: win.keysLive; sequence: "Left"; onActivated: win.pv.stepFrames(-1) }
    Shortcut { enabled: win.keysLive; sequence: "Right"; onActivated: win.pv.stepFrames(1) }
    Shortcut { enabled: win.keysLive; sequence: "Home"; onActivated: win.pv.seek(0) }   // first / last frame (2026-10-02)
    Shortcut { enabled: win.keysLive; sequence: "End"; onActivated: win.pv.seek(Math.max(0, win.pv.frameCount - 1)) }
}
