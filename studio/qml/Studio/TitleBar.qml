import QtQuick
import QtQuick.Controls.Basic
import Studio

// The window caption, drawn here so it takes the palette -- over glass too,
// where DWM can only draw a caption frosted OR coloured. Transparent: the
// window's tinted ground shows through, so the bar is the same frosted,
// palette-tinted sheet as the page.
//
// Dragging, double-click, snapping and the system menu stay Windows' own:
// WinFrame (platform_win.WindowFrame) reports this bar as HTCAPTION and the
// maximise button as HTMAXBUTTON, which is what brings up Windows 11's snap
// layouts. That button is non-client, so its hover/press come from WinFrame.
Item {
    id: bar
    objectName: "titleBar"
    property var window: null
    readonly property bool maximized: window && window.visibility === Window.Maximized
    readonly property bool windowActive: window ? window.active : true
    readonly property alias minButton: minBtn
    readonly property alias maxButton: maxBtn
    readonly property alias closeButton: closeBtn
    // The WindowFrame that owns this caption: Main's, or the console window's own.
    property var frame: typeof WinFrame !== "undefined" ? WinFrame : null
    readonly property bool nativeFrame: frame !== null && frame.active
    // centred in the bar (Main: the movie's name, Shell.projectName)
    property string centerText: ""
    // Main (user, 2026-10-01): the name is a button that picks the project
    // folder; with none set it says so. Client area, not caption (Main hands
    // centerButton to WinFrame with the other buttons).
    property bool centerClickable: false
    signal centerClicked()
    readonly property alias centerButton: centre
    // Main (user, 2026-10-25): the app's name in the top-left corner opens the manual at its contents.
    // Client area too, when clickable (Main hands nameButton to WinFrame).
    property bool nameClickable: false
    signal nameClicked()
    readonly property alias nameButton: nameBtn

    implicitHeight: 32

    function toggleMaximized() {
        if (!window) return
        if (maximized) window.showNormal()
        else window.showMaximized()
    }

    // the name as a button, behind it (as the project name's): its hover, press and tip
    Item {
        id: nameBtn
        objectName: "titleName"
        x: leftRow.x - 6
        width: leftRow.width + 10
        height: bar.height - 6
        anchors.verticalCenter: parent.verticalCenter
        Rectangle {
            anchors.fill: parent
            radius: 7
            visible: bar.nameClickable
            color: nameTap.pressed ? Theme.alpha(Theme.fg, 0.12) : nameHover.hovered ? Theme.hover : "transparent"
            Behavior on color { ColorAnimation { duration: Theme.dur(120) } }
        }
        HoverHandler { id: nameHover; enabled: bar.nameClickable; cursorShape: Qt.PointingHandCursor }
        TapHandler { id: nameTap; enabled: bar.nameClickable; onTapped: bar.nameClicked() }
        HoverTip { tip: bar.nameClickable ? "Open the manual" : "" }
    }
    Row {
        id: leftRow
        x: 12
        anchors.verticalCenter: parent.verticalCenter
        spacing: 8
        Image {
            anchors.verticalCenter: parent.verticalCenter
            source: typeof Shell !== "undefined" && Shell.appIconUrl ? Shell.appIconUrl
                                                                     : Qt.resolvedUrl("../../../assets/StereoCrafter.ico")
            sourceSize: Qt.size(16, 16)
            width: 16
            height: 16
            smooth: true
        }
        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: bar.window ? bar.window.title : ""
            color: bar.windowActive ? Theme.fg : Theme.fgFaint
            font.family: Theme.bodyFont
            font.pixelSize: 12
            Behavior on color { ColorAnimation { duration: Theme.dur(160) } }
        }
        // 3D glasses beside the name (user, 2026-09-28), in the caption's colour
        GlassesIcon {
            objectName: "titleGlasses"
            anchors.verticalCenter: parent.verticalCenter
            anchors.verticalCenterOffset: -1
            width: 26
            height: implicitHeight
            implicitWidth: 26
            color: bar.windowActive ? Theme.fg : Theme.fgFaint
            Behavior on color { ColorAnimation { duration: Theme.dur(160) } }
        }
    }

    component CaptionButton: Item {
        id: btn
        property string glyph: ""
        property bool danger: false
        readonly property bool hoverRaw: hover.hovered
        readonly property bool pressRaw: tap.pressed
        property bool hovered: hoverRaw
        property bool pressed: pressRaw
        signal clicked()
        width: 46
        height: bar.height

        Rectangle {
            anchors.fill: parent
            color: btn.danger && (btn.hovered || btn.pressed)
                   ? (btn.pressed ? "#b3251a" : "#c42b1c")
                   : (btn.pressed ? Theme.alpha(Theme.fg, 0.12) : (btn.hovered ? Theme.alpha(Theme.fg, 0.08) : "transparent"))
            Behavior on color { ColorAnimation { duration: Theme.dur(100) } }
        }
        Icon {
            anchors.centerIn: parent
            glyph: btn.glyph
            size: 10
            color: btn.danger && (btn.hovered || btn.pressed) ? "#ffffff"
                   : (bar.windowActive ? Theme.fg : Theme.fgFaint)
        }
        HoverHandler { id: hover }
        TapHandler { id: tap; onTapped: btn.clicked() }
    }

    // The movie, centred on the window; it gives way (elides) before it
    // would reach the name on the left or the caption buttons.
    Item {
        id: centre
        objectName: "titleCenter"
        readonly property bool empty: bar.centerText === ""
        readonly property string shown: empty ? "Select project folder…" : bar.centerText
        visible: !empty || bar.centerClickable
        readonly property real room: Math.max(0, Math.min(bar.width / 2 - leftRow.width - leftRow.x - 24,
                                                          bar.width / 2 - buttons.width - 24))
        readonly property real pad: bar.centerClickable ? 10 : 0
        readonly property real iconW: bar.centerClickable ? 13 + 7 : 0
        width: Math.min(centreText.implicitWidth + iconW + 2 * pad, 2 * room)
        height: bar.height - 6
        x: (bar.width - width) / 2
        anchors.verticalCenter: parent.verticalCenter

        Rectangle {
            anchors.fill: parent
            radius: 7
            visible: bar.centerClickable
            color: centreTap.pressed ? Theme.alpha(Theme.fg, 0.12) : centreHover.hovered ? Theme.hover : "transparent"
            Behavior on color { ColorAnimation { duration: Theme.dur(120) } }
        }
        Icon {
            id: centreIcon
            visible: bar.centerClickable
            x: centre.pad
            anchors.verticalCenter: parent.verticalCenter
            glyph: Theme.icon.project
            size: 13
            color: Theme.accentInk
        }
        Text {
            id: centreText
            x: centre.pad + centre.iconW
            width: centre.width - x - centre.pad
            anchors.verticalCenter: parent.verticalCenter
            text: centre.shown
            elide: Text.ElideRight
            color: !bar.windowActive ? Theme.fgFaint : centre.empty ? Theme.fgMuted : Theme.fg
            font.family: Theme.headingFont
            font.pixelSize: 13
            font.weight: Font.DemiBold
            font.letterSpacing: 0.4
            Behavior on color { ColorAnimation { duration: Theme.dur(160) } }
        }
        HoverHandler { id: centreHover; enabled: bar.centerClickable; cursorShape: Qt.PointingHandCursor }
        TapHandler { id: centreTap; enabled: bar.centerClickable; onTapped: bar.centerClicked() }
        HoverTip {
            tip: bar.centerClickable ? (centre.empty ? "Pick the project folder: a movie's, or a show's (its seasons and episodes are found inside)."
                                                     : "The project: click to pick another project folder.") : ""
        }
    }

    Row {
        id: buttons
        anchors.right: parent.right
        height: parent.height
        CaptionButton {
            id: minBtn
            objectName: "captionMin"
            glyph: ""                          // ChromeMinimize
            onClicked: if (bar.window) bar.window.showMinimized()
        }
        CaptionButton {
            id: maxBtn
            objectName: "captionMax"
            glyph: bar.maximized ? "" : "" // ChromeRestore / ChromeMaximize
            // Non-client under WinFrame (for snap layouts): Qt sees no mouse here.
            hovered: bar.nativeFrame ? bar.frame.maxHovered : hoverRaw
            pressed: bar.nativeFrame ? bar.frame.maxPressed : pressRaw
            onClicked: if (!bar.nativeFrame) bar.toggleMaximized()
        }
        CaptionButton {
            id: closeBtn
            objectName: "captionClose"
            glyph: ""                          // ChromeClose
            danger: true
            onClicked: if (bar.window) bar.window.close()
        }
    }

    Connections {
        target: bar.nativeFrame ? bar.frame : null
        function onMaximizeClicked() { bar.toggleMaximized() }
    }
}
