import QtQuick
import QtQuick.Effects
import Studio

// Stack of notifications at the top centre, under the stage tabs (user,
// 2026-10-05; it was the bottom-right corner). New ones drop in from above,
// the others slide down to make room, each dismisses itself after a
// countdown shown as a shrinking line, and hovering a toast pauses it.
Item {
    id: host
    width: 400

    ListModel { id: toasts }
    property int serial: 0
    // how far down the stack reaches (GlassesFreeView: a hole in the woven preview)
    readonly property real stackHeight: toasts.count > 0 ? Math.min(height, list.contentHeight) : 0
    Component.onCompleted: Theme.toastHost = host

    function push(kind, title, message) {
        toasts.insert(0, { kind: kind, title: title, message: message, uid: ++serial })
        if (toasts.count > 5) toasts.remove(toasts.count - 1)
    }
    function removeUid(uid) {
        for (let i = 0; i < toasts.count; ++i)
            if (toasts.get(i).uid === uid) { toasts.remove(i); return }
    }

    Connections {
        target: Theme
        function onToastRequested(kind, title, message) { host.push(kind, title, message) }
    }

    ListView {
        id: list
        anchors.fill: parent
        model: toasts
        spacing: 10
        interactive: false
        verticalLayoutDirection: ListView.TopToBottom

        add: Transition {
            ParallelAnimation {
                NumberAnimation { property: "y"; from: -90; duration: Theme.dur(520); easing.type: Easing.OutBack; easing.overshoot: 1.2 }
                NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.dur(240) }
            }
        }
        remove: Transition {
            ParallelAnimation {
                NumberAnimation { property: "opacity"; to: 0; duration: Theme.dur(220) }
                NumberAnimation { property: "scale"; to: 0.9; duration: Theme.dur(260) }
            }
        }
        displaced: Transition {
            NumberAnimation { property: "y"; duration: Theme.dur(380); easing.type: Easing.OutCubic }
            NumberAnimation { property: "scale"; to: 1; duration: Theme.dur(200) }
            NumberAnimation { property: "opacity"; to: 1; duration: Theme.dur(200) }
        }

        delegate: Item {
            id: toast
            required property string kind
            required property string title
            required property string message
            required property int uid
            width: list.width
            // grows with the message (user, 2026-09-27: long ones were cut off)
            height: Math.max(76, body.implicitHeight + 32)

            readonly property color tone: kind === "success" ? Theme.success
                : kind === "error" ? Theme.danger
                : kind === "warning" ? Theme.warning : Theme.accent
            property real remaining: 1
            // 3 s each (user, 2026-10-05; it was 4-9 s by length): hovering one
            // holds it, and every message is kept in the bell to read later
            readonly property int life: 3000

            // The countdown ticks every frame -- but twice a second while a job
            // runs (Theme.calm): a toast used to redraw the window for its whole
            // 4-9 s, each frame costing the job (audit 2026-09-28 O7).
            Timer {
                id: countdown
                interval: Theme.calm ? 500 : 16
                repeat: true
                running: !hover.hovered
                onTriggered: {
                    toast.remaining = Math.max(0, toast.remaining - interval / toast.life)
                    if (toast.remaining <= 0) {
                        stop()
                        host.removeUid(toast.uid)
                    }
                }
            }

            Rectangle {
                id: card
                anchors.fill: parent
                radius: 12
                color: Theme.field
                border.width: 1
                border.color: Theme.borderStrong
                layer.enabled: true
                layer.effect: MultiEffect {
                    shadowEnabled: true
                    shadowColor: "#000000"
                    shadowBlur: 0.9
                    shadowOpacity: Theme.isDark ? 0.55 : 0.18
                    shadowVerticalOffset: 6
                }

                Rectangle {
                    x: 0; y: 10
                    width: 4; height: parent.height - 20; radius: 2
                    color: toast.tone
                }
                Rectangle {
                    x: 18; y: 18
                    width: 30; height: 30; radius: 15
                    color: Theme.alpha(toast.tone, 0.18)
                    Icon {
                        anchors.centerIn: parent
                        size: 16          // even in a 30 circle: centred on whole pixels
                        color: toast.tone
                        glyph: toast.kind === "success" ? Theme.icon.check
                            : toast.kind === "error" ? Theme.icon.error
                            : toast.kind === "warning" ? Theme.icon.warning : Theme.icon.info
                    }
                }
                Column {
                    id: body
                    x: 60; y: 15
                    // from the host's width, not the card's: the list places a toast as
                    // it is added, so its wrapped height has to be right from the start
                    width: host.width - 100
                    spacing: 3
                    Text {
                        text: toast.title
                        color: Theme.fg
                        font.family: Theme.bodyFont; font.pixelSize: 14; font.weight: Font.DemiBold
                        elide: Text.ElideRight; width: parent.width
                    }
                    Text {
                        text: toast.message
                        color: Theme.fgMuted
                        font.family: Theme.bodyFont; font.pixelSize: 13
                        wrapMode: Text.Wrap; width: parent.width
                        maximumLineCount: 6; elide: Text.ElideRight
                    }
                }
                IconButton {
                    x: parent.width - 36; y: 8
                    size: 28
                    iconGlyph: Theme.icon.close
                    tint: Theme.fgMuted
                    onClicked: host.removeUid(toast.uid)
                }
            }
            // Outside the card: inside, each tick re-rendered its shadow layer (O7).
            Rectangle {
                objectName: "toastCountdown"
                x: 12
                y: card.height - 5
                height: 2; radius: 1
                width: (card.width - 24) * toast.remaining
                color: Theme.alpha(toast.tone, 0.7)
            }
            HoverHandler { id: hover }
        }
    }
}
