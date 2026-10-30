import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Effects
import QtQuick.Dialogs
import QtQuick.Layouts
import Studio
import "tips.js" as Tips

// Settings, in place of the menu bar (user, 2026-09-27): the gear left of the
// VRAM pill opens it (Ctrl+,). Appearance is laid out by hand (the theme,
// glass, scale and outlines are the header's theme button, ThemePicker). The other sections are the old menus
// themselves (StudioMenu objects, still declared by Main and each page),
// drawn as rows: a checkable item is a switch, any other a button. A button
// closes Settings first, since most open a dialog or a prompt of their own.
Popup {
    id: dlg
    objectName: "settingsDialog"
    parent: Overlay.overlay
    modal: true
    // counted while shown: the page under it ignores clicks (Theme.modalOpen)
    onVisibleChanged: Theme.modals = Math.max(0, Theme.modals + (visible ? 1 : -1))
    focus: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    anchors.centerIn: parent
    width: Math.min(860, (parent ? parent.width : 900) - 60)
    height: Math.min(640, (parent ? parent.height : 700) - 60)
    padding: 0

    // [{ name, menus: [StudioMenu] }] after Appearance, in nav order
    property var sections: []
    property int current: 0
    // Appearance's place in the nav: right after Studio (user, 2026-10-10: the stage,
    // Studio, Appearance, Keyboard Shortcuts, GPU)
    readonly property int appearanceAt: {
        const i = sections.findIndex(s => s.name === "Studio")
        return i >= 0 ? i + 1 : Math.min(1, sections.length)
    }
    // ...and Keyboard Shortcuts right after it (user, 2026-10-02), then Tips (user, 2026-10-11)
    readonly property int shortcutsAt: appearanceAt + 1
    readonly property int tipsAt: appearanceAt + 2
    function navIndex(sectionIndex) { return sectionIndex < appearanceAt ? sectionIndex : sectionIndex + 3 }
    // Main's helpers
    property var window: null

    function openAt(i) { current = i; open() }

    Overlay.modal: Rectangle {
        color: Theme.alpha("#000000", Theme.isDark ? 0.55 : 0.3)
    }
    enter: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.dur(160) }
            NumberAnimation { property: "scale"; from: 0.95; to: 1; duration: Theme.dur(260); easing.type: Easing.OutCubic }
        }
    }
    exit: Transition {
        NumberAnimation { property: "opacity"; to: 0; duration: Theme.dur(120) }
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
            shadowOpacity: Theme.isDark ? 0.6 : 0.25
            shadowVerticalOffset: 10
        }
    }

    // ---- pieces ------------------------------------------------------------------
    // the buttons' face (user, 2026-10-02): Cal Sans, all caps -- bolder than
    // the buttons (Qt thickens the font's one weight)
    component Heading: Text {
        color: Theme.fg
        font.family: Theme.buttonFont
        font.pixelSize: Theme.headingSize + 2
        font.weight: Font.Bold
        font.capitalization: Font.AllUppercase
        font.letterSpacing: 0.8
        Layout.topMargin: 10
    }
    component Note: Text {
        color: Theme.fgMuted
        font.family: Theme.bodyFont
        font.pixelSize: Theme.smallSize + 1
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    }
    // a label with a switch at the right
    component SwitchRow: RowLayout {
        id: sr
        property string text: ""
        property string tip: ""
        property string note: ""
        property bool on: false
        signal flipped(bool value)
        Layout.fillWidth: true
        spacing: 12
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 0
            Text {
                text: sr.text
                color: sr.enabled ? Theme.fg : Theme.fgFaint
                font.family: Theme.bodyFont
                font.pixelSize: Theme.bodySize
                Layout.fillWidth: true
                elide: Text.ElideRight
            }
            // the description is a tooltip, not a line under the title (user, 2026-09-27)
            HoverTip { tip: [sr.note, sr.tip].filter(t => t !== "").join("

") }
        }
        Toggle {
            objectName: "settingsSwitch"
            checked: sr.on
            onClicked: { const v = checked; checked = Qt.binding(() => sr.on); sr.flipped(v) }
        }
    }
    // a label with a button at the right
    // `ghost`: no label, the setting's own name on a ghost button (user: in
    // place of "Run"); otherwise the name, then an outline button
    component ActionRow: RowLayout {
        id: ar
        property string text: ""
        property string button: "Open"
        property string tip: ""
        // a keyboard shortcut, shown left of the button (user, 2026-10-01)
        property string shortcut: ""
        // a value shown left of the button, elided in the middle (a folder)
        property string detail: ""
        property bool ghost: false
        signal run()
        Layout.fillWidth: true
        spacing: 12
        AccentButton {
            objectName: "settingsGhost"
            // its text lines up with the labels above and below: sized to its
            // text (no 80 px minimum, so the shift is one padding for every
            // name), and the pane leaves room on the left for the part of the
            // button -- and its hover grow -- left of the text (user: they were
            // cut off at the pane's clip)
            implicitWidth: contentItem.implicitWidth + 30
            Layout.leftMargin: -15
            visible: ar.ghost
            text: ar.text
            kind: "ghost"
            enabled: ar.enabled
            onClicked: ar.run()
            HoverTip { tip: ar.tip }
        }
        Item { visible: ar.ghost; Layout.fillWidth: true }
        Text {
            visible: !ar.ghost
            text: ar.text
            color: ar.enabled ? Theme.fg : Theme.fgFaint
            font.family: Theme.bodyFont
            font.pixelSize: Theme.bodySize
            Layout.fillWidth: true
            elide: Text.ElideRight
            HoverTip { tip: ar.tip }
        }
        Text {
            objectName: "settingsDetail"
            visible: !ar.ghost && ar.detail !== ""
            text: ar.detail
            color: Theme.fgMuted
            font.family: Theme.bodyFont
            font.pixelSize: Theme.smallSize + 1
            elide: Text.ElideMiddle
            Layout.maximumWidth: 280
        }
        Text {
            objectName: "settingsShortcut"
            visible: !ar.ghost && ar.shortcut !== ""
            text: ar.shortcut
            color: Theme.fgMuted
            font.family: Theme.bodyFont
            font.pixelSize: Theme.bodySize
        }
        AccentButton { visible: !ar.ghost; text: ar.button; kind: "outline"; enabled: ar.enabled; onClicked: ar.run() }
    }
    // one of the old menus, as rows
    component MenuRows: ColumnLayout {
        id: mr
        property var menu: null
        signal closeRequested()
        Layout.fillWidth: true
        spacing: 10
        Heading { visible: !!mr.menu && mr.menu.title !== ""; text: mr.menu ? mr.menu.title : "" }
        Repeater {
            model: mr.menu ? mr.menu.count : 0
            Loader {
                id: row
                required property int index
                readonly property var item: mr.menu.itemAt(index)
                // MenuItem has triggered(); a MenuSeparator has not
                readonly property bool action: !!item && typeof item.triggered === "function"
                Layout.fillWidth: true
                sourceComponent: !action ? gap : (item.checkable ? switchRow : actionRow)
                Component {
                    id: gap
                    Rectangle { implicitHeight: 1; color: Theme.border; opacity: 0.6 }
                }
                Component {
                    id: switchRow
                    SwitchRow {
                        objectName: "settingsItem"
                        text: row.item.text
                        on: row.item.checked
                        tip: row.item.tip || ""
                        enabled: row.item.enabled
                        // as a checkable MenuItem does: flip, then triggered
                        onFlipped: (v) => { row.item.checked = v; row.item.triggered() }
                    }
                }
                Component {
                    id: actionRow
                    ActionRow {
                        objectName: "settingsItem"
                        text: row.item.text.replace(/(\.\.\.|…)$/, "")
                        button: "Open"
                        ghost: !/(\.\.\.|…)$/.test(row.item.text)
                        tip: row.item.tip || ""
                        enabled: row.item.enabled
                        onRun: { const it = row.item; mr.closeRequested(); it.triggered() }
                    }
                }
            }
        }
    }

    // ---- the page ---------------------------------------------------------------
    contentItem: RowLayout {
        spacing: 0
        // the sections
        Rectangle {
            Layout.fillHeight: true
            Layout.preferredWidth: 190
            color: Theme.alpha(Theme.fg, 0.03)
            radius: 14
            Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: Theme.border }
            Column {
                anchors.fill: parent
                anchors.margins: 14
                spacing: 4
                Text {
                    text: "Settings"
                    color: Theme.fg
                    font.family: Theme.headingFont
                    font.pixelSize: Theme.headingSize + 3
                    font.weight: Font.DemiBold
                    bottomPadding: 12
                    leftPadding: 8
                }
                Repeater {
                    model: { const n = dlg.sections.map(s => s.name); n.splice(dlg.appearanceAt, 0, "Appearance", "Keyboard Shortcuts", "Tips"); return n }
                    Rectangle {
                        required property int index
                        required property string modelData
                        objectName: "settingsNav"
                        width: parent.width
                        height: 34
                        radius: 8
                        color: dlg.current === index ? Theme.alpha(Theme.accent, 0.18)
                                                     : (navHover.hovered ? Theme.hover : "transparent")
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            x: 10
                            text: parent.modelData
                            color: dlg.current === parent.index ? Theme.fg : Theme.fgMuted
                            font.family: Theme.bodyFont
                            font.pixelSize: Theme.bodySize
                            font.weight: dlg.current === parent.index ? Font.DemiBold : Font.Normal
                        }
                        HoverHandler { id: navHover; cursorShape: Qt.PointingHandCursor }
                        TapHandler { onTapped: dlg.current = parent.index }
                    }
                }
            }
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true
            IconButton {
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: 10
                z: 2
                size: 32
                iconGlyph: Theme.icon.close
                onClicked: dlg.close()
            }
            Flickable {
                id: flick
                anchors.fill: parent
                anchors.margins: 24
                anchors.rightMargin: 16
                // 20 px of the left margin inside the clip: room for the ghost
                // buttons, which reach 15 px left of the labels (+ hover grow)
                anchors.leftMargin: 24 - ghostRoom
                readonly property int ghostRoom: 20
                contentHeight: body.implicitHeight
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: SlimScrollBar {
                    objectName: "settingsScrollBar"; alwaysShow: true
                    // its track starts clear of the close button above it (user, 2026-10-11)
                    topPadding: 30
                }
                // more above / below (user, 2026-10-11: a long page didn't look scrollable)
                readonly property bool moreBelow: contentHeight - contentY - height > 4
                readonly property bool moreAbove: contentY > 4
                // one section showing at a time (a hidden one takes no room)
                ColumnLayout {
                    id: body
                    x: flick.ghostRoom
                    width: flick.width - 12 - flick.ghostRoom
                    spacing: 0
                    Connections { target: dlg; function onCurrentChanged() { flick.contentY = 0 } }

                    // ---- Appearance
                    ColumnLayout {
                        objectName: "settingsAppearance"
                        visible: dlg.current === dlg.appearanceAt
                        Layout.fillWidth: true
                        spacing: 12
                        // Theme, glass, UI scale and outlines moved to the header's
                        // theme button (ThemePicker, user 2026-09-27)
                        Heading { text: "Window"; Layout.topMargin: 0 }
                        SwitchRow {
                            objectName: "tipsSwitch"
                            text: "Show Tips"
                            note: "The tips above each stage's progress bar."
                            on: Shell.showTips
                            onFlipped: (v) => Shell.showTips = v
                        }
                        ActionRow {
                            text: "Fit Window to Content"
                            button: "Fit"
                            shortcut: "Ctrl+0"
                            onRun: if (dlg.window) dlg.window.fitToContent()
                        }
                        // the caption's project button lists this folder's projects (user, 2026-10-01)
                        Heading { text: "Projects" }
                        ActionRow {
                            objectName: "projectsRootRow"
                            text: "Projects Folder"
                            tip: "The folder that holds your projects, one folder per movie or show -- or Movies and "
                                 + "TV Shows folders holding them. The project button in the middle of the title bar "
                                 + "lists them. A show is a project with an Episode folder inside."
                            detail: Shell.projectsRoot || "Not set"
                            button: "Change…"
                            onRun: projectsRootSettingsDlg.open()
                        }
                        // shows kept somewhere else (user, 2026-10-02)
                        ActionRow {
                            objectName: "showsRootRow"
                            text: "TV Shows Folder"
                            tip: "Optional: if your TV shows are kept apart from the movies, the folder holding them. "
                                 + "Every folder in it is listed under TV Shows. Not set, shows are found in the Projects Folder."
                            detail: Shell.showsRoot || "Not set (optional)"
                            button: "Change…"
                            onRun: showsRootSettingsDlg.open()
                        }
                        ActionRow {
                            objectName: "showsRootClear"
                            visible: Shell.showsRoot !== ""
                            text: "Use the Projects Folder for shows"
                            button: "Clear"
                            onRun: Shell.showsRoot = ""
                        }
                    }

                    // ---- Keyboard Shortcuts (user, 2026-10-02): every shortcut, by where it works
                    ColumnLayout {
                        objectName: "settingsShortcuts"
                        visible: dlg.current === dlg.shortcutsAt
                        Layout.fillWidth: true
                        spacing: 6
                        Note { text: "Shortcuts with a single letter or arrow work when you're not typing in a box." }
                        Repeater {
                            model: [
                                { title: "Everywhere", rows: [
                                    ["Ctrl+1 … Ctrl+4", "Go to DepthCrafter, Splatting, Inpainting, Merging"],
                                    ["Ctrl+5", "Go to Extra"],
                                    ["Ctrl+Tab · Ctrl+Shift+Tab", "Next stage · the one before"],
                                    ["Ctrl+P", "Open the project list"],
                                    ["Ctrl+,", "Open or close Settings"],
                                    ["Ctrl+`", "Show or hide the console"],
                                    ["Ctrl+0", "Fit the window to the page"],
                                    ["Ctrl+= · Ctrl+-", "UI Scale up · down"],
                                    ["F1", "Open the manual at this stage"]] },
                                { title: "Previews (Splatting, Merging)", rows: [
                                    ["Left · Right", "One frame back · forward"],
                                    ["Shift+Left · Shift+Right", "Ten frames back · forward"],
                                    ["Home · End", "First · last frame"],
                                    ["Up · Down", "Next · previous clip (also Ctrl+Right · Ctrl+Left)"],
                                    ["Space", "Play or pause"],
                                    ["Shift+Space", "Fast forward"],
                                    ["F11", "Full Preview, full screen (Splatting, Merging); Escape or F11 closes it"]] },
                                { title: "Splatting", rows: [
                                    ["Ctrl+S", "Create or update the clip's sidecar"],
                                    ["Ctrl+T", "Detect Text"],
                                    ["Ctrl+M", "Edit Depth Map: this clip in the Depth Map Editor"],
                                    ["S", "Add a static keyframe"],
                                    ["D", "Add a dynamic keyframe"],
                                    ["C", "Pick Convergence: then click the picture (C again cancels)"],
                                    ["Mouse side buttons", "The actions picked in Settings > Studio"]] },
                                { title: "Detect Text", rows: [
                                    ["Left · Right", "One frame (Shift: ten)"],
                                    ["Home · End", "First · last frame"],
                                    ["I · O", "Set From · Set To at this frame"]] },
                                { title: "Depth Map Editor", rows: [
                                    ["Left · Right", "One frame back · forward"],
                                    ["S · D", "Key the selected layer: static · dynamic"],
                                    ["Ctrl+Z", "Undo the last edit"],
                                    ["Ctrl+Y · Ctrl+Shift+Z", "Redo it"]] },
                                { title: "Console", rows: [
                                    ["Ctrl+C · Ctrl+A", "Copy the selected lines · select the whole tab"],
                                    ["Ctrl+Tab · Ctrl+Shift+Tab", "Next tab · the one before"]] }
                            ]
                            ColumnLayout {
                                id: group
                                required property var modelData
                                Layout.fillWidth: true
                                spacing: 4
                                Heading { text: group.modelData.title }
                                Repeater {
                                    model: group.modelData.rows
                                    RowLayout {
                                        id: keyRow
                                        required property var modelData
                                        objectName: "shortcutRow"
                                        Layout.fillWidth: true
                                        spacing: 14
                                        Rectangle {
                                            Layout.preferredWidth: 210
                                            Layout.alignment: Qt.AlignTop
                                            implicitHeight: keyText.implicitHeight + 8
                                            color: "transparent"
                                            Rectangle {
                                                width: keyText.implicitWidth + 14
                                                height: parent.height
                                                radius: 6
                                                color: Theme.alpha(Theme.fg, 0.06)
                                                border.width: 1
                                                border.color: Theme.border
                                                Text {
                                                    id: keyText
                                                    anchors.centerIn: parent
                                                    text: keyRow.modelData[0]
                                                    color: Theme.fg
                                                    font.family: Theme.tabFont
                                                    font.pixelSize: Theme.smallSize + 1
                                                }
                                            }
                                        }
                                        Text {
                                            Layout.fillWidth: true
                                            Layout.alignment: Qt.AlignVCenter
                                            text: keyRow.modelData[1]
                                            color: Theme.fgMuted
                                            font.family: Theme.bodyFont
                                            font.pixelSize: Theme.bodySize
                                            wrapMode: Text.WordWrap
                                        }
                                    }
                                }
                            }
                        }
                    }

                    // ---- Tips (user, 2026-10-11): every tip in Studio, by what it's for -- the same
                    // lists the tip faders above the progress bars show (tips.js)
                    ColumnLayout {
                        objectName: "settingsTips"
                        visible: dlg.current === dlg.tipsAt
                        Layout.fillWidth: true
                        spacing: 6
                        Note { text: "Every tip in Studio, by what it's for. Each stage shows its own, one at a time, above its progress bar." }
                        Repeater {
                            model: Tips.GROUPS
                            ColumnLayout {
                                id: tipGroup
                                required property var modelData
                                Layout.fillWidth: true
                                spacing: 5
                                Heading { text: tipGroup.modelData.title }
                                Repeater {
                                    model: tipGroup.modelData.tips
                                    RowLayout {
                                        id: tipRow
                                        required property var modelData
                                        objectName: "tipRow"
                                        Layout.fillWidth: true
                                        spacing: 8
                                        Rectangle {
                                            Layout.alignment: Qt.AlignTop
                                            Layout.topMargin: Math.round(Theme.bodySize * 0.55)
                                            implicitWidth: 5; implicitHeight: 5; radius: 2.5
                                            color: Theme.accent
                                        }
                                        Text {
                                            Layout.fillWidth: true
                                            text: tipRow.modelData
                                            color: Theme.fg
                                            font.family: Theme.bodyFont
                                            font.pixelSize: Theme.bodySize
                                            wrapMode: Text.WordWrap
                                        }
                                    }
                                }
                            }
                        }
                    }

                    // ---- the old menus: the stage's own, Studio, GPU
                    Repeater {
                        model: dlg.sections
                        ColumnLayout {
                            id: sec
                            required property var modelData
                            required property int index
                            objectName: "settingsSection"
                            visible: dlg.current === dlg.navIndex(index)
                            Layout.fillWidth: true
                            spacing: 18
                            // the manual (user, 2026-10-04): its contents from Studio, a
                            // stage's own section from the stage's
                            RowLayout {
                                objectName: "settingsManualRow"
                                visible: sec.modelData.manual !== undefined
                                Layout.fillWidth: true
                                // first in the section: clear of the close button in the corner
                                Layout.rightMargin: 40
                                spacing: 10
                                Text {
                                    text: sec.modelData.manualName
                                          ? "How " + sec.modelData.manualName + " works, step by step, with every setting explained."
                                          : "The StereoCrafter Studio manual: setup, every stage, and troubleshooting."
                                    color: Theme.fgMuted
                                    font.family: Theme.bodyFont
                                    font.pixelSize: Theme.bodySize
                                    wrapMode: Text.WordWrap
                                    Layout.fillWidth: true
                                }
                                AccentButton {
                                    objectName: "settingsManualButton"
                                    text: sec.modelData.manualName ? "Open " + sec.modelData.manualName + " in the Manual"
                                                                   : "Open the Manual"
                                    kind: "outline"
                                    onClicked: {
                                        if (Shell.openManual(sec.modelData.manual || "")) dlg.close()
                                        else Theme.toast("warning", "Manual not found", "The manual isn't in this Studio folder (docs/manual).")
                                    }
                                    HoverTip { tip: "Opens the manual in your web browser" + (sec.modelData.manualName
                                                    ? ", at its " + sec.modelData.manualName + " section." : ".") + " F1 does the same from any page." }
                                }
                            }
                            Repeater {
                                model: sec.modelData.menus
                                MenuRows {
                                    required property var modelData
                                    menu: modelData
                                    onCloseRequested: dlg.close()
                                }
                            }
                            // a stage's own panel after its menus (DepthCrafter's custom
                            // resolution presets, user 2026-10-05): its page's settingsExtra
                            Loader {
                                objectName: "settingsExtra"
                                active: !!sec.modelData.extra
                                visible: active
                                sourceComponent: sec.modelData.extra || null
                                Layout.fillWidth: true
                            }
                            Note {
                                visible: sec.modelData.menus.length === 0 && !sec.modelData.extra
                                text: "This stage has no settings of its own here."
                            }
                        }
                    }
                }
            }
            // the page goes on: its edge fades, and a button says so (user, 2026-10-11).
            // They change only as the page scrolls -- nothing animates while it sits still.
            Rectangle {
                objectName: "settingsFadeTop"
                x: flick.x; y: flick.y
                width: flick.width - 12; height: 28
                opacity: flick.moreAbove ? 1 : 0
                visible: opacity > 0
                Behavior on opacity { NumberAnimation { duration: Theme.dur(160) } }
                gradient: Gradient {
                    GradientStop { position: 0; color: Theme.field }
                    GradientStop { position: 1; color: Theme.alpha(Theme.field, 0) }
                }
            }
            Rectangle {
                objectName: "settingsFadeBottom"
                x: flick.x; y: flick.y + flick.height - height
                width: flick.width - 12; height: 56
                opacity: flick.moreBelow ? 1 : 0
                visible: opacity > 0
                Behavior on opacity { NumberAnimation { duration: Theme.dur(160) } }
                gradient: Gradient {
                    GradientStop { position: 0; color: Theme.alpha(Theme.field, 0) }
                    GradientStop { position: 1; color: Theme.field }
                }
            }
            Rectangle {
                id: moreButton
                objectName: "settingsMoreBelow"
                anchors.horizontalCenter: flick.horizontalCenter
                y: flick.y + flick.height - height - 6
                width: moreRow.implicitWidth + 24; height: 28; radius: 14
                color: moreHover.hovered ? Theme.hover : Theme.alpha(Theme.fg, 0.07)
                border.width: 1
                border.color: Theme.border
                opacity: flick.moreBelow ? 1 : 0
                visible: opacity > 0
                Behavior on opacity { NumberAnimation { duration: Theme.dur(160) } }
                Row {
                    id: moreRow
                    anchors.centerIn: parent
                    spacing: 6
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: "More below"
                        color: Theme.fgMuted
                        font.family: Theme.bodyFont
                        font.pixelSize: Theme.smallSize
                    }
                    Icon { anchors.verticalCenter: parent.verticalCenter; glyph: Theme.icon.down; size: 9; color: Theme.fgMuted }
                }
                HoverHandler { id: moreHover; cursorShape: Qt.PointingHandCursor }
                // a page's worth down (not to the end: the next setting stays in sight)
                TapHandler {
                    onTapped: flick.contentY = Math.min(flick.contentHeight - flick.height, flick.contentY + flick.height * 0.8)
                }
            }
        }
    }

    FolderPicker {
        id: projectsRootSettingsDlg
        title: "Projects folder (holds one folder per movie or show)"
        currentFolder: Shell.pathToUrl(Shell.projectsRoot || Shell.projectFolder)
        onAccepted: {
            Shell.projectsRoot = Shell.urlToPath(selectedFolder)
            Theme.toast("success", "Projects folder set", Shell.projectsRoot)
        }
    }
    FolderPicker {
        id: showsRootSettingsDlg
        title: "TV shows folder (holds one folder per show)"
        currentFolder: Shell.pathToUrl(Shell.showsRoot || Shell.projectsRoot || Shell.projectFolder)
        onAccepted: {
            Shell.showsRoot = Shell.urlToPath(selectedFolder)
            Theme.toast("success", "TV shows folder set", Shell.showsRoot)
        }
    }
}
