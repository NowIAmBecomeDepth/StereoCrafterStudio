import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Effects
import QtQuick.Layouts
import Studio

// The one modal dialog. It serves three jobs:
//   * the runner's Yes / No / Cancel prompts (resume segments, checkpoint...)
//   * confirmations before a destructive File-menu action
//   * plain information (help pages)
//
// ask({title, message, buttons: [{text, code, kind}], onDone: fn(code)}).
// Esc gives the last button's code, which callers make the safe choice.
Popup {
    id: dlg
    parent: Overlay.overlay
    modal: true
    // counted while shown: the page under it ignores clicks (Theme.modalOpen)
    property bool counted: false
    onVisibleChanged: {
        if (visible && !counted) { counted = true; Theme.modals = Theme.modals + 1 }
        else if (!visible && counted) { counted = false; Theme.modals = Math.max(0, Theme.modals - 1) }
    }
    // Its window hidden while it asks (Hole Shift, Subtitle Mask, Grain Match closed
    // with their X): un-counted at once -- a hidden window may never finish the
    // closing animation, and the main window's controls stayed dead (bug sweep
    // A10) -- and answered as Esc answers.
    Connections {
        target: dlg.parent ? dlg.parent.Window.window : null
        ignoreUnknownSignals: true
        function onVisibleChanged() {
            if (target && !target.visible && (dlg.visible || dlg.counted)) {
                if (dlg.counted) { dlg.counted = false; Theme.modals = Math.max(0, Theme.modals - 1) }
                dlg.close()
            }
        }
    }
    focus: true
    closePolicy: Popup.CloseOnEscape
    anchors.centerIn: parent
    width: Math.min(560, (parent ? parent.width : 800) - 80)
    padding: 22

    property string title: ""
    property string message: ""
    property var buttons: []
    property var onDone: null
    property int focusIndex: 0      // the button Enter presses
    property bool answered: false
    // Questions asked while one is open wait their turn (audit 2026-09-28:
    // a second ask replaced the first's onDone, and its asker -- a worker
    // waiting on the answer -- was never answered).
    property var waiting: []
    property bool asking: false      // a question is up and not answered yet

    function ask(opts) {
        if (asking) {
            waiting = waiting.concat([opts])
            return
        }
        asking = true
        title = opts.title || ""
        message = opts.message || ""
        focusIndex = opts.focusIndex || 0   // before the buttons: their delegates read it
        buttons = opts.buttons || [{ text: "OK", code: 1, kind: "filled" }]
        onDone = opts.onDone || null
        answered = false
        open()
    }
    // A stage's Stop (audit #13, user 2026-09-29): asked first. "Stop now"
    // cuts the item in progress off and discards it; the safe choice, when
    // the stage has one, stops where the work can be picked up again (after
    // the segment / chunk / clip in progress). Enter picks the safe choice.
    // askStop({stage, detail, safeText, safeDetail, onYes, onSafe})
    function askStop(opts) {
        const safe = !!opts.onSafe
        const buttons = [{ text: "Stop now", code: 1, kind: "danger" }]
        if (safe) buttons.push({ text: opts.safeText, code: 2, kind: "tonal" })
        buttons.push({ text: "Keep running", code: 0, kind: "ghost" })
        ask({
            title: "Stop " + opts.stage + "?",
            message: "Stop now: " + (opts.detail || "the clip being processed is cut off and its output is discarded. "
                                                   + "Clips already finished are kept.")
                     + (safe ? "\n\n" + opts.safeText + ": " + opts.safeDetail : ""),
            buttons: buttons,
            focusIndex: safe ? 1 : buttons.length - 1,
            onDone: (code) => {
                if (code === 1 && opts.onYes) opts.onYes()
                else if (code === 2 && opts.onSafe) opts.onSafe()
            }
        })
    }
    function finish(code) {
        if (answered) return
        answered = true
        const cb = onDone
        close()
        if (cb) cb(code)
    }
    onOpened: { const b = btnRep.itemAt(focusIndex); if (b) b.forceActiveFocus() }
    onClosed: {
        if (!answered && buttons.length) finish(buttons[buttons.length - 1].code)
        // the next question once this one has fully closed -- opened during
        // the close, the popup's modal layer could stay over the page
        asking = false
        if (waiting.length) {
            const next = waiting[0]
            waiting = waiting.slice(1)
            Qt.callLater(() => ask(next))
        }
    }

    Overlay.modal: Rectangle {
        color: Theme.alpha("#000000", Theme.isDark ? 0.55 : 0.3)
        Behavior on opacity { NumberAnimation { duration: Theme.dur(160) } }
    }

    enter: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.dur(160) }
            NumberAnimation { property: "scale"; from: 0.92; to: 1; duration: Theme.dur(300); easing.type: Easing.OutBack }
        }
    }
    exit: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; to: 0; duration: Theme.dur(120) }
            NumberAnimation { property: "scale"; to: 0.96; duration: Theme.dur(120) }
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
            shadowOpacity: Theme.isDark ? 0.6 : 0.25
            shadowVerticalOffset: 10
        }
    }

    contentItem: ColumnLayout {
        spacing: 14
        Text {
            text: Theme.headingText(dlg.title)
            color: Theme.fg
            font.family: Theme.headingFont
            font.pixelSize: Theme.headingSize
            font.weight: Font.DemiBold
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        Flickable {
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(msg.implicitHeight, 360)
            contentHeight: msg.implicitHeight
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: SlimScrollBar {}
            TextEdit {
                id: msg
                width: parent.width
                text: dlg.message
                readOnly: true
                selectByMouse: true
                wrapMode: TextEdit.Wrap
                color: Theme.fg
                selectionColor: Theme.alpha(Theme.accent, 0.45)
                font.family: Theme.bodyFont
                font.pixelSize: Theme.bodySize
            }
        }
        RowLayout {
            Layout.alignment: Qt.AlignRight
            spacing: 10
            Repeater {
                id: btnRep
                model: dlg.buttons
                AccentButton {
                    required property var modelData
                    required property int index
                    text: modelData.text
                    kind: modelData.kind || "outline"
                    onClicked: dlg.finish(modelData.code)
                    Component.onCompleted: if (index === dlg.focusIndex) forceActiveFocus()
                }
            }
        }
    }
}
