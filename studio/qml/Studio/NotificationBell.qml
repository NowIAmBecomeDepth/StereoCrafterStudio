import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Effects
import QtQuick.Layouts
import Studio

// The notification bell (user, 2026-10-05): between Beginner Mode's gear-side
// neighbours -- left of Settings -- it keeps every toast this session, newest
// first, so one that faded while you were away can still be read. A count of
// the unread ones sits on the bell; opening the list reads them all.
// Nothing here animates during a job (Theme.dur): a toast arriving then only
// changes the count.
Item {
    id: root
    objectName: "notificationBell"
    implicitWidth: button.width
    implicitHeight: button.height

    //: How many are kept (oldest dropped first).
    readonly property int keep: 200
    property int unread: 0
    readonly property alias count: history.count

    ListModel { id: history }

    function record(kind, title, message) {
        const now = new Date()
        history.insert(0, { kind: kind || "info", title: title || "", message: message || "",
                            time: Qt.formatTime(now, "hh:mm"), day: Qt.formatDate(now, "ddd d MMM") })
        while (history.count > keep) history.remove(history.count - 1)
        if (!popup.opened) unread++
    }
    function clear() { history.clear(); unread = 0 }

    Connections {
        target: Theme
        function onToastRequested(kind, title, message) { root.record(kind, title, message) }
    }

    IconButton {
        id: button
        objectName: "bellButton"
        size: 34
        iconGlyph: Theme.icon.bell
        onClicked: popup.opened ? popup.close() : popup.open()
        HoverTip { tip: root.unread > 0 ? root.unread + " new notification" + (root.unread === 1 ? "" : "s")
                                        : "Notifications: every message Studio has shown this session." }
    }
    // the unread count, on the bell's shoulder
    Rectangle {
        objectName: "bellBadge"
        visible: root.unread > 0
        x: button.width - width + 2
        y: 1
        height: 16
        width: Math.max(16, badgeText.implicitWidth + 8)
        radius: 8
        color: Theme.accent
        border.width: 1.5
        border.color: Theme.bg
        Text {
            id: badgeText
            anchors.centerIn: parent
            text: root.unread > 99 ? "99+" : root.unread
            color: Theme.accentFg
            font.family: Theme.bodyFont
            font.pixelSize: 9
            font.weight: Font.Bold
        }
    }

    Popup {
        id: popup
        objectName: "bellPopup"
        y: button.height + 8
        x: button.width - width
        width: 400
        readonly property Item ov: root.Overlay.overlay
        height: Math.min(implicitHeight, ov ? ov.height - button.mapToItem(ov, 0, button.height).y - 24 : 520, 560)
        padding: 14
        modal: true
        focus: true
        onVisibleChanged: Theme.modals = Math.max(0, Theme.modals + (visible ? 1 : -1))
        onOpened: root.unread = 0
        Overlay.modal: Item {}
        transformOrigin: Popup.TopRight
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        enter: Transition {
            ParallelAnimation {
                NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.dur(160) }
                NumberAnimation { property: "scale"; from: 0.88; to: 1; duration: Theme.dur(300); easing.type: Easing.OutBack }
            }
        }
        exit: Transition {
            ParallelAnimation {
                NumberAnimation { property: "opacity"; to: 0; duration: Theme.dur(140) }
                NumberAnimation { property: "scale"; to: 0.94; duration: Theme.dur(140) }
            }
        }
        background: Rectangle {
            PopupGrain { radius: 14 }
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

        // its own height from the list (not from the layout: a binding loop)
        implicitHeight: 2 * padding + 36 + (history.count ? Math.min(list.contentHeight, 440) : empty.implicitHeight + 24)
        contentItem: ColumnLayout {
            spacing: 10
            RowLayout {
                id: head
                Layout.fillWidth: true
                Text {
                    text: "NOTIFICATIONS"
                    color: Theme.fg
                    font.family: Theme.buttonFont
                    font.pixelSize: Theme.headingSize
                    font.weight: Font.Bold
                    font.letterSpacing: 0.8
                    Layout.fillWidth: true
                }
                AccentButton {
                    objectName: "bellClear"
                    text: "Clear All"
                    kind: "ghost"
                    implicitHeight: 26
                    enabled: history.count > 0
                    onClicked: root.clear()
                }
            }
            ListView {
                id: list
                objectName: "bellList"
                visible: history.count > 0
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                spacing: 6
                model: history
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: SlimScrollBar {}
                delegate: Rectangle {
                    id: note
                    required property string kind
                    required property string title
                    required property string message
                    required property string time
                    required property string day
                    readonly property color tone: kind === "success" ? Theme.success
                        : kind === "error" ? Theme.danger
                        : kind === "warning" ? Theme.warning : Theme.accent
                    width: ListView.view.width - 8
                    height: noteRow.implicitHeight + 16
                    radius: 10
                    color: Theme.alpha(Theme.fg, 0.035)
                    Rectangle {
                        x: 0; y: 8
                        width: 3; height: parent.height - 16; radius: 1.5
                        color: note.tone
                    }
                    RowLayout {
                        id: noteRow
                        anchors { left: parent.left; right: parent.right; top: parent.top; margins: 8; leftMargin: 12 }
                        spacing: 10
                        Rectangle {
                            Layout.alignment: Qt.AlignTop
                            implicitWidth: 26; implicitHeight: 26; radius: 13
                            color: Theme.alpha(note.tone, 0.18)
                            Icon {
                                anchors.centerIn: parent
                                size: 14          // even in a 26 circle: centred on whole pixels
                                color: note.tone
                                glyph: note.kind === "success" ? Theme.icon.check
                                    : note.kind === "error" ? Theme.icon.error
                                    : note.kind === "warning" ? Theme.icon.warning : Theme.icon.info
                            }
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2
                            RowLayout {
                                Layout.fillWidth: true
                                Text {
                                    text: note.title
                                    color: Theme.fg
                                    font.family: Theme.bodyFont
                                    font.pixelSize: Theme.bodySize
                                    font.weight: Font.DemiBold
                                    wrapMode: Text.WordWrap
                                    Layout.fillWidth: true
                                }
                                Text {
                                    text: note.time
                                    color: Theme.fgFaint
                                    font.family: Theme.bodyFont
                                    font.pixelSize: Theme.smallSize
                                    font.features: { "tnum": 1 }
                                    Layout.alignment: Qt.AlignTop
                                    HoverTip { tip: note.day + ", " + note.time }
                                }
                            }
                            Text {
                                visible: note.message !== ""
                                text: note.message
                                color: Theme.fgMuted
                                font.family: Theme.bodyFont
                                font.pixelSize: Theme.smallSize + 1
                                wrapMode: Text.WordWrap
                                textFormat: Text.PlainText
                                Layout.fillWidth: true
                            }
                        }
                    }
                }
            }
            Text {
                id: empty
                visible: history.count === 0
                text: "No notifications yet. Messages Studio shows -- a job finished, a folder set, a problem -- are kept here for the rest of the session."
                color: Theme.fgFaint
                font.family: Theme.bodyFont
                font.pixelSize: Theme.bodySize
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
                Layout.topMargin: 6
                Layout.bottomMargin: 6
            }
        }
    }
}
