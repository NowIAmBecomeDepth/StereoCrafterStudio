import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Effects
import Studio

// Studio's stage tabs, Qt Quick edition. A tinted pill and a glowing accent
// bar spring to the selected tab; hover shows a faint pill of its own; the
// label brightens and each tab's step number fills in.
Item {
    id: root
    property var model: []
    property int currentIndex: 0
    property bool numbered: true
    // Only the first N tabs are numbered stages (Main: 4 -- Extra is not one).
    property int numberedCount: -1
    signal activated(int index)
    // A tab with choices under it (user, 2026-10-18: Extra's three tools): {tab index: [names]}. Hovering
    // the tab drops them down; picking one sends menuPicked. A click on the tab itself is as before.
    property var menus: ({})
    signal menuPicked(int index, int item)
    // how long the pointer rests on such a tab before its list drops (user, 2026-11-01: at 120 ms a pass
    // over Depth Map or Extra dropped a list over the options right below them)
    property int menuDelay: 1000
    readonly property alias hoverMenu: hoverMenu
    // a backing under the labels over thin glass (Theme.bed); Main's strip
    // has one of its own
    property bool bed: true
    // The row under the stage tabs (user, 2026-11-01: two accent lines one above the other felt
    // repetitive; picked in prototypes/subtab_lab): no pill and no line -- the label under the pointer
    // grows, the current one stays full size and bright with a glowing dot under it, the rest sit
    // smaller and dimmer.
    property bool subTabs: false
    readonly property real restScale: 0.70
    readonly property real hoverScale: 0.86
    readonly property real restInk: 0.46        // of the ink a resting label keeps (hovered: + 0.35)

    implicitHeight: 48
    implicitWidth: row.implicitWidth

    readonly property Item currentTab: repeater.count > currentIndex ? repeater.itemAt(currentIndex) : null

    Rectangle {
        anchors.fill: parent
        anchors.topMargin: 4          // clear of the strip above (user, 2026-10-01: its top looked cut off)
        radius: 10
        visible: root.bed && Theme.bedAlpha > 0
        color: Theme.bed
    }
    Rectangle {
        id: pill
        visible: !root.subTabs
        x: root.currentTab ? root.currentTab.x : 0
        width: root.currentTab ? root.currentTab.width : 0
        y: 4
        height: root.height - 8
        radius: 10
        color: Theme.alpha(Theme.accent, 0.16)
        Behavior on x { enabled: !Theme.calm; SpringAnimation { spring: 3.2; damping: 0.28; epsilon: 0.05 } }
        Behavior on width { enabled: !Theme.calm; SpringAnimation { spring: 3.2; damping: 0.28; epsilon: 0.05 } }
    }
    Rectangle {
        id: bar
        visible: !root.subTabs
        // The bar trails the pill a little (a softer spring), which reads as
        // one object with weight rather than two things moving in lockstep.
        x: root.currentTab ? root.currentTab.x + 14 : 0
        width: root.currentTab ? root.currentTab.width - 28 : 0
        y: root.height - 3
        height: 3
        radius: 1.5
        color: Theme.accent
        Behavior on x { enabled: !Theme.calm; SpringAnimation { spring: 2.2; damping: 0.24; epsilon: 0.05 } }
        Behavior on width { enabled: !Theme.calm; SpringAnimation { spring: 2.2; damping: 0.24; epsilon: 0.05 } }
        layer.enabled: true
        layer.effect: MultiEffect {
            shadowEnabled: true
            shadowColor: Theme.accent
            shadowBlur: 0.7
            shadowOpacity: 0.9
            shadowVerticalOffset: 0
        }
    }

    Rectangle {
        id: dot
        objectName: "subTabDot"
        visible: root.subTabs && !!root.currentTab
        width: 6
        height: 6
        radius: 3
        x: root.currentTab ? root.currentTab.x + root.currentTab.width / 2 - 3 : 0
        y: root.height - 6
        color: Theme.accent
        Behavior on x { enabled: !Theme.calm; SpringAnimation { spring: 2.6; damping: 0.26; epsilon: 0.05 } }
        layer.enabled: visible
        layer.effect: MultiEffect {
            shadowEnabled: true
            shadowColor: Theme.accent
            shadowBlur: 0.8
            shadowOpacity: 1.0
            shadowVerticalOffset: 0
        }
    }

    Row {
        id: row
        height: parent.height
        spacing: 2
        Repeater {
            id: repeater
            model: root.model
            Item {
                id: tab
                required property int index
                required property var modelData
                readonly property bool current: index === root.currentIndex
                // a sub-tab keeps room for its label bold and full size, so nothing beside it moves
                width: (root.subTabs ? fullWidth.width : content.implicitWidth) + 32
                TextMetrics {
                    id: fullWidth
                    text: Theme.tabText(tab.modelData)
                    font.family: Theme.tabFont
                    font.pixelSize: Theme.tabSize + 1
                    font.weight: Font.Bold
                }
                height: row.height

                Rectangle {
                    anchors.fill: parent
                    anchors.topMargin: 4
                    anchors.bottomMargin: 4
                    radius: 10
                    color: Theme.hover
                    opacity: hover.hovered && !tab.current && !root.subTabs ? 1 : 0
                    Behavior on opacity { NumberAnimation { duration: Theme.dur(160) } }
                }

                Row {
                    id: content
                    anchors.centerIn: parent
                    anchors.verticalCenterOffset: root.subTabs ? -2 : 0
                    scale: !root.subTabs || tab.current ? 1.0 : hover.hovered ? root.hoverScale : root.restScale
                    Behavior on scale { NumberAnimation { duration: Theme.dur(60); easing.type: Easing.OutCubic } }
                    spacing: 6
                    // "1." plain (user, 2026-09-26; it was a disc filled with the accent).
                    // Solid ink, every tab; the current one is told by weight alone
                    // (user, 2026-10-01): see-through grey text read as grey with a
                    // thin light rim over glass. Disabled: the faint ink, not opacity
                    // (that made it see-through again).
                    Text {
                        objectName: "tabNumber"
                        visible: root.numbered && (root.numberedCount < 0 || tab.index < root.numberedCount)
                        anchors.verticalCenter: parent.verticalCenter
                        text: (tab.index + 1) + "."
                        color: tab.enabled ? Theme.fg : Theme.fgFaint
                        font.family: Theme.tabFont
                        font.pixelSize: Theme.tabSize + 1
                        font.features: { "tnum": 1 }
                        font.weight: tab.current ? Font.Bold : Font.Normal
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        // v1 app_fonts: Bahnschrift caps; the current tab Bold
                        text: Theme.tabText(tab.modelData)
                        color: !tab.enabled ? Theme.fgFaint
                               : !root.subTabs || tab.current ? Theme.fg
                               : Theme.mix(Theme.surface, Theme.fg, hover.hovered ? root.restInk + 0.35 : root.restInk)
                        font.family: Theme.tabFont
                        font.pixelSize: Theme.tabSize + 1
                        font.weight: tab.current ? Font.Bold : Font.Normal
                        Behavior on color { ColorAnimation { duration: Theme.dur(root.subTabs ? 60 : 200) } }
                    }
                }

                HoverHandler {
                    id: hover
                    cursorShape: Qt.PointingHandCursor
                    onHoveredChanged: {
                        if (hovered && root.menus[tab.index]) hoverMenu.openFor(tab)
                        else hoverMenu.check()
                    }
                }
                TapHandler {
                    onTapped: {
                        hoverMenu.close()
                        root.currentIndex = tab.index
                        root.activated(tab.index)
                    }
                }
                // the handler's own state, not a copy: a copy could still be the old value inside the change
                readonly property alias hovered: hover.hovered
            }
        }
    }

    // The drop-down under a tab with a menu. Opens a moment after the pointer arrives (a pass across the
    // strip opens nothing) and closes a moment after it has left both the tab and the list, so the pointer
    // can travel from one to the other.
    Popup {
        id: hoverMenu
        objectName: "tabHoverMenu"
        property var tab: null            // a tab delegate: its own `hovered` is read (an Item-typed one hides it)
        property int tabIndex: -1
        readonly property var items: tabIndex >= 0 ? (root.menus[tabIndex] || []) : []
        parent: tab || root
        x: 0
        y: (tab ? tab.height : root.height) - 4
        padding: 5
        modal: false
        focus: false
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent
        transformOrigin: Popup.Top

        function openFor(t) {
            closeTimer.stop()
            if (opened && tab === t) return
            tab = t
            tabIndex = t.index
            openTimer.restart()
        }
        // the pointer moved: close once it is on neither the tab nor the list
        function check() {
            if ((tab && tab.hovered) || listHover.hovered) closeTimer.stop()
            else { openTimer.stop(); if (opened) closeTimer.restart() }
        }
        Timer { id: openTimer; interval: root.menuDelay; onTriggered: if (hoverMenu.tab && hoverMenu.tab.hovered) hoverMenu.open() }
        Timer { id: closeTimer; interval: 220; onTriggered: hoverMenu.close() }

        enter: Transition {
            ParallelAnimation {
                NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.dur(140) }
                NumberAnimation { property: "scale"; from: 0.94; to: 1; duration: Theme.dur(200); easing.type: Easing.OutCubic }
            }
        }
        exit: Transition { NumberAnimation { property: "opacity"; to: 0; duration: Theme.dur(100) } }

        background: Rectangle {
            PopupGrain { radius: 10 }   // the window's film grain
            radius: 10
            color: Theme.field
            border.width: 1
            border.color: Theme.borderStrong
            layer.enabled: true
            layer.effect: MultiEffect {
                shadowEnabled: true
                shadowColor: "#000000"
                shadowBlur: 0.9
                shadowOpacity: Theme.isDark ? 0.55 : 0.2
                shadowVerticalOffset: 6
            }
        }

        contentItem: Column {
            HoverHandler { id: listHover; onHoveredChanged: hoverMenu.check() }
            Repeater {
                model: hoverMenu.items
                Rectangle {
                    id: rowItem
                    objectName: "tabMenuItem"
                    required property int index
                    required property string modelData
                    width: Math.max(200, rowLabel.implicitWidth + 28)
                    height: 32
                    radius: 6
                    color: rowHover.hovered ? Theme.alpha(Theme.accent, 0.16) : "transparent"
                    Behavior on color { ColorAnimation { duration: Theme.dur(120) } }
                    // no tick for the one last used (user, 2026-10-25: "i don't think theres any use case for it")
                    Text {
                        id: rowLabel
                        x: 14
                        anchors.verticalCenter: parent.verticalCenter
                        text: rowItem.modelData
                        color: Theme.fg
                        font.family: Theme.bodyFont
                        font.pixelSize: Theme.bodySize
                    }
                    HoverHandler { id: rowHover; cursorShape: Qt.PointingHandCursor }
                    TapHandler {
                        onTapped: {
                            const i = hoverMenu.tabIndex
                            hoverMenu.close()
                            root.menuPicked(i, rowItem.index)
                        }
                    }
                }
            }
        }
    }
}
