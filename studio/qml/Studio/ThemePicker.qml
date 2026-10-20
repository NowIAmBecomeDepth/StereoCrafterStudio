import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Effects
import QtQuick.Layouts
import Studio

// The look, one click away (user, 2026-09-27; as the Qt showcase's palette
// picker): a header button showing the current palette -- its two tones and
// its name -- left of Beginner Mode. Its popup grows out of the button with
// the palettes (Dark, then Light, cascading in), Frosted Glass and its
// opacity, UI Scale, the section outlines, the film grain and the app icon. Most of these used to be in
// Settings > Appearance.
Item {
    id: root
    objectName: "themePicker"
    property var window: null            // Main: setGlass(), autoZoom
    implicitWidth: button.width
    implicitHeight: 30
    readonly property alias opened: popup.opened
    function open() { popup.open() }
    function close() { popup.close() }

    // the palette's two tones: its ground, its accent as the dot
    component Swatch: Rectangle {
        property var p
        property int size: 28
        width: size; height: size; radius: size / 2
        color: p ? p.bg : "black"
        border.width: 1
        border.color: Theme.borderStrong
        Rectangle {
            anchors.centerIn: parent
            width: parent.width * 0.5; height: width; radius: width / 2
            color: parent.p ? parent.p.accent : "white"
            border.width: 1
            border.color: Theme.alpha("#000000", 0.25)
        }
    }
    component Heading: Text {
        color: Theme.fgMuted
        font.family: Theme.tabFont
        font.pixelSize: Theme.smallSize
        font.letterSpacing: 1.2
        Layout.topMargin: 4
    }

    Rectangle {
        id: button
        objectName: "themeButton"
        width: row.implicitWidth + 22
        height: 30
        radius: 15
        color: hover.hovered || popup.opened ? Theme.hover : "transparent"
        border.width: 1
        border.color: Theme.border
        Behavior on color { ColorAnimation { duration: Theme.dur(160) } }
        Row {
            id: row
            anchors.centerIn: parent
            spacing: 8
            Swatch { anchors.verticalCenter: parent.verticalCenter; p: Theme.p; size: 16 }
            MorphLabel {
                anchors.verticalCenter: parent.verticalCenter
                text: Theme.p ? Theme.p.name : ""
                pixelSize: Theme.smallSize + 1
                family: Theme.bodyFont
            }
            Icon {
                anchors.verticalCenter: parent.verticalCenter
                glyph: Theme.icon.down
                size: 9
                color: Theme.fgMuted
                rotation: popup.opened ? 180 : 0
                Behavior on rotation { NumberAnimation { duration: Theme.dur(300); easing.type: Easing.OutBack } }
            }
        }
        HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor; enabled: !Theme.modalOpen || popup.opened }
        TapHandler { enabled: !Theme.modalOpen || popup.opened; onTapped: popup.opened ? popup.close() : popup.open() }
        HoverTip { tip: popup.opened ? "" : "Theme, glass, UI scale, section outlines and the app icon." }
    }

    Popup {
        id: popup
        objectName: "themePopup"
        y: button.height + 8
        x: button.width - width + 60
        width: 460
        // never past the window's bottom: the rest scrolls (the overlay is
        // laid out at 1.00, as zoomRoot is)
        readonly property Item ov: root.Overlay.overlay
        height: ov ? Math.min(implicitHeight, ov.height - button.mapToItem(ov, 0, button.height).y - 24) : implicitHeight
        padding: 14
        modal: true
        focus: true                      // so Esc closes it
        // counted while shown: the page under it ignores clicks (Theme.modalOpen)
        onVisibleChanged: Theme.modals = Math.max(0, Theme.modals + (visible ? 1 : -1))
        Overlay.modal: Item {}
        transformOrigin: Popup.Top
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

        enter: Transition {
            ParallelAnimation {
                NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.dur(160) }
                NumberAnimation { property: "scale"; from: 0.85; to: 1; duration: Theme.dur(320); easing.type: Easing.OutBack }
            }
        }
        exit: Transition {
            ParallelAnimation {
                NumberAnimation { property: "opacity"; to: 0; duration: Theme.dur(140) }
                NumberAnimation { property: "scale"; to: 0.92; duration: Theme.dur(140) }
            }
        }

        background: Rectangle {
            PopupGrain { radius: 14 }   // the window's film grain
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

        contentItem: Flickable {
            id: flick
            implicitHeight: body.implicitHeight
            contentHeight: body.implicitHeight
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: SlimScrollBar {}
            ColumnLayout {
                id: body
                width: flick.width
                spacing: 8

                Heading { text: "DARK THEMES"; Layout.topMargin: 0 }
                PaletteGrid { objectName: "darkPalettes"; dark: true }
                Heading { text: "LIGHT THEMES" }
                PaletteGrid { objectName: "lightPalettes"; dark: false }

                Rectangle { Layout.fillWidth: true; Layout.topMargin: 6; implicitHeight: 1; color: Theme.border }

                // Glass and the layout, side by side: two switches, two knobs
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 14
                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.minimumWidth: 200
                        Layout.alignment: Qt.AlignVCenter
                        spacing: 10
                        SwitchRow {
                            objectName: "glassSwitch"
                            text: "Frosted Glass"
                            tip: "The desktop shows through, blurred, behind the window. The Opacity knob sets how "
                                 + "much of the theme's colour stays over it: lower is more see-through; double-click "
                                 + "it for Auto."
                            enabled: typeof Backdrop !== "undefined" && Backdrop.supported
                            on: Shell.glass !== "none"
                            onFlipped: (v) => { if (root.window) root.window.setGlass(v ? "acrylic" : "none", true) }
                        }
                        SwitchRow {
                            objectName: "outlinesSwitch"
                            text: "Section Outlines"
                            tip: "A thin outline around each section, in the theme's outline colour."
                            on: Shell.sectionOutlines
                            onFlipped: (v) => Shell.sectionOutlines = v
                        }
                        SwitchRow {
                            objectName: "grainSwitch"
                            text: "Film Grain"
                            tip: "A fine film grain over the window, tuned for each theme. The previews are "
                                 + "always kept clean, so it is never mistaken for grain in the video."
                            enabled: Theme.grain.enabled && Theme.grain.amount > 0
                            on: Shell.filmGrain
                            onFlipped: (v) => Shell.filmGrain = v
                        }
                    }
                    // Shell.glassOpacity is "auto" or one of 0.9 ... 0.3; the knob shows
                    // percent, Auto as the palette's own default (Theme.defaultKeep).
                    QtObject {
                        id: glassKnobStore
                        readonly property real autoPct: Math.round(Theme.defaultKeep * 100)
                        property real pct: Shell.glassOpacity === "auto" ? autoPct : Math.round(Number(Shell.glassOpacity) * 100)
                        function apply(v) {
                            Shell.glassOpacity = Math.round(v) === autoPct ? "auto" : (Math.round(v) / 100).toFixed(1)
                            pct = Qt.binding(() => Shell.glassOpacity === "auto" ? autoPct
                                                                                : Math.round(Number(Shell.glassOpacity) * 100))
                        }
                    }
                    DialField {
                        objectName: "glassOpacityKnob"
                        store: glassKnobStore; key: "pct"
                        label: Shell.glassOpacity === "auto" ? "Opacity · Auto" : "Opacity"
                        size: 50; captionLines: 1
                        Layout.preferredWidth: 92          // room for "Opacity · Auto"
                        from: 30; to: 90; step: 10; decimals: 0
                        defaultValue: glassKnobStore.autoPct
                        enabled: Shell.glass !== "none"
                        opacity: enabled ? 1 : Theme.dim(0.5)
                        Layout.alignment: Qt.AlignTop
                        onMoved: (v) => glassKnobStore.apply(v)
                        onReleased: (v) => glassKnobStore.apply(v)
                        help: "Glass Opacity: how much of the theme's colour stays over the glass, 30-90%. "
                              + "Lower is more see-through. Double-click (or right-click) for Auto, this theme's own "
                              + "setting (" + glassKnobStore.autoPct + "%)."
                    }
                    // UI Scale: 75-100%, double-click for Auto (by screen). The whole
                    // window rescales at each value, so it applies once the turning stops.
                    QtObject {
                        id: scaleKnobStore
                        readonly property real autoPct: Math.round((root.window ? root.window.autoZoom : 1) * 100)
                        function current() { return Shell.uiScale === "auto" ? autoPct : Math.round(Number(Shell.uiScale) * 100) }
                        property real pct: current()
                        property real pending: -1
                        function apply() {
                            if (pending < 0) return
                            const v = Math.round(pending); pending = -1
                            Shell.uiScale = v === autoPct ? "auto" : String(v / 100)
                            pct = Qt.binding(() => current())
                        }
                    }
                    Timer { id: scaleApply; interval: 450; onTriggered: scaleKnobStore.apply() }
                    DialField {
                        objectName: "uiScaleKnob"
                        store: scaleKnobStore; key: "pct"
                        label: Shell.uiScale === "auto" && scaleKnobStore.pending < 0 ? "Scale · Auto" : "UI Scale"
                        size: 50; captionLines: 1
                        Layout.preferredWidth: 80
                        from: 75; to: 100; step: 5; decimals: 0
                        defaultValue: scaleKnobStore.autoPct
                        Layout.alignment: Qt.AlignTop
                        onMoved: (v) => { scaleKnobStore.pending = v; scaleApply.restart() }
                        onReleased: (v) => { scaleKnobStore.pending = v; scaleApply.restart() }
                        help: "UI Scale: how large the whole window is drawn, 75-100%. Auto picks by the screen's "
                              + "height (" + scaleKnobStore.autoPct + "% here). Double-click (or right-click) for Auto."
                    }
                }

                // The app icon (user, 2026-10-08): the window's, the taskbar's, the title
                // bar's and the notifications'. Fifteen colourways, matte (with grain) or
                // glossy, and the icon Studio had before as Classic.
                Rectangle { Layout.fillWidth: true; Layout.topMargin: 6; implicitHeight: 1; color: Theme.border }
                RowLayout {
                    Layout.fillWidth: true
                    Heading { text: "APP ICON"; Layout.fillWidth: true }
                    SegmentedControl {
                        id: iconFinish
                        objectName: "iconFinish"
                        compact: true
                        controlled: true
                        fontSize: 12
                        model: ["Matte", "Glossy"]
                        // the finish shown: the current icon's; while it is Classic, the set picked
                        // (Matte at first). Never assigned: assigning broke the binding, and after
                        // Classic the switch and grid no longer followed the icon (bug sweep A1)
                        property string classicFinish: "matte"
                        readonly property string finish: Shell.appIcon.endsWith("_glossy") ? "glossy"
                                                         : Shell.appIcon.endsWith("_matte") ? "matte" : classicFinish
                        currentIndex: finish === "glossy" ? 1 : 0
                        onActivated: (i) => {
                            const want = i === 1 ? "glossy" : "matte"
                            const cur = Shell.appIcon
                            if (cur.endsWith("_matte") || cur.endsWith("_glossy"))
                                Shell.appIcon = cur.replace(/_(matte|glossy)$/, "_" + want)  // the same colours, the other finish
                            else
                                classicFinish = want                                        // Classic: just show that set
                        }
                        HoverTip { tip: "Matte has a fine film grain and no sheen; Glossy is smooth with a soft highlight." }
                    }
                }
                Grid {
                    id: iconGrid
                    objectName: "appIconGrid"
                    columns: 8
                    spacing: 4
                    Repeater {
                        model: Shell.appIcons.filter(i => i.finish === iconFinish.finish || i.id === "classic")
                        Rectangle {
                            id: iconCell
                            required property var modelData
                            readonly property bool current: modelData.id === Shell.appIcon
                            objectName: "appIconCell"
                            width: (flick.width - 7 * iconGrid.spacing) / 8
                            height: width
                            radius: 10
                            color: iconHover.hovered ? Theme.hover : "transparent"
                            border.width: current ? 1.5 : 0
                            border.color: Theme.accent
                            Image {
                                anchors.centerIn: parent
                                width: parent.width - 10; height: width
                                source: iconCell.modelData.url
                                sourceSize: Qt.size(128, 128)
                                smooth: true
                                mipmap: true
                                scale: iconHover.hovered ? 1.08 : 1
                                Behavior on scale { NumberAnimation { duration: Theme.dur(180); easing.type: Easing.OutBack } }
                            }
                            HoverHandler { id: iconHover; cursorShape: Qt.PointingHandCursor }
                            TapHandler { onTapped: Shell.appIcon = iconCell.modelData.id }
                            HoverTip { tip: iconCell.modelData.name + (iconCell.modelData.finish ? " (" + iconCell.modelData.finish + ")" : "") }
                        }
                    }
                }
            }
        }
    }

    // a label with a switch at the right, its description a tooltip
    component SwitchRow: RowLayout {
        id: sr
        property string text: ""
        property string tip: ""
        property bool on: false
        signal flipped(bool value)
        Layout.fillWidth: true
        spacing: 10
        Text {
            text: sr.text
            color: sr.enabled ? Theme.fg : Theme.fgFaint
            font.family: Theme.bodyFont
            font.pixelSize: Theme.bodySize
            Layout.fillWidth: true
            elide: Text.ElideRight
            HoverTip { tip: sr.tip }
        }
        Toggle {
            objectName: "settingsSwitch"
            checked: sr.on
            onClicked: { const v = checked; checked = Qt.binding(() => sr.on); sr.flipped(v) }
        }
    }

    // a Grid, not a Flow: a Flow in a layout reports its height a pass late
    // (an empty row was left under the dark palettes)
    component PaletteGrid: Grid {
        id: grid
        property bool dark: true
        columns: 4
        spacing: 4
        Repeater {
            // keep each palette's own index: that is what Theme.setPalette takes
            model: Theme.palettes.map((p, i) => ({ p: p, i: i })).filter(x => !!x.p.isDark === grid.dark)
            Rectangle {
                id: cell
                required property var modelData
                required property int index
                readonly property int paletteIndex: modelData.i
                readonly property var pal: modelData.p
                readonly property bool current: paletteIndex === Theme.index
                function pick() { Theme.setPalette(paletteIndex) }
                objectName: "paletteSwatch"
                width: (flick.width - 3 * grid.spacing) / 4; height: 66
                radius: 10
                color: cellHover.hovered ? Theme.hover : "transparent"
                border.width: current ? 1.5 : 0
                border.color: Theme.accent
                // the cascade: each swatch a little after the last (not while a job runs)
                opacity: 1
                scale: 1
                SequentialAnimation {
                    id: cascade
                    PauseAnimation { duration: Theme.dur(16 * (cell.index + (grid.dark ? 0 : 8))) }
                    ParallelAnimation {
                        NumberAnimation { target: cell; property: "opacity"; to: 1; duration: Theme.dur(180) }
                        NumberAnimation { target: cell; property: "scale"; to: 1; duration: Theme.dur(340); easing.type: Easing.OutBack }
                    }
                }
                Connections {
                    target: popup
                    function onAboutToShow() {
                        if (Theme.calm) { cell.opacity = 1; cell.scale = 1; return }
                        cell.opacity = 0; cell.scale = 0.6; cascade.restart()
                    }
                }
                Swatch {
                    anchors.horizontalCenter: parent.horizontalCenter
                    y: 8
                    p: cell.pal
                    size: 28
                    scale: cellHover.hovered ? 1.15 : 1
                    Behavior on scale { NumberAnimation { duration: Theme.dur(180); easing.type: Easing.OutBack } }
                }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    y: 42
                    width: parent.width - 6
                    horizontalAlignment: Text.AlignHCenter
                    elide: Text.ElideRight
                    text: cell.pal.name
                    color: cell.current ? Theme.fg : Theme.fgMuted
                    font.family: Theme.bodyFont
                    font.pixelSize: Theme.smallSize
                }
                HoverHandler { id: cellHover; cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: cell.pick() }
            }
        }
    }
}
