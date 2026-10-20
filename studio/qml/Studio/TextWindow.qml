import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Effects
import QtQuick.Layouts
import Studio

// v1's _ctk_text_window: a read-only document in its own sheet (the User
// Guide). Markdown is rendered; Escape or a click outside closes it.
Popup {
    id: win
    property string title: ""
    property string text: ""
    property bool markdown: true

    function show(t, body) { title = t; text = body; open() }

    parent: Overlay.overlay
    modal: true
    // counted while shown: the page under it ignores clicks (Theme.modalOpen)
    onVisibleChanged: Theme.modals = Math.max(0, Theme.modals + (visible ? 1 : -1))
    focus: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    anchors.centerIn: parent
    width: Math.min(900, parent ? parent.width - 80 : 900)
    height: Math.min(760, parent ? parent.height - 80 : 760)
    padding: 22

    Overlay.modal: Rectangle { color: Theme.alpha("#000000", Theme.isDark ? 0.55 : 0.3) }
    enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.dur(160) } }
    exit: Transition { NumberAnimation { property: "opacity"; to: 0; duration: Theme.dur(120) } }
    background: Rectangle {
        PopupGrain { radius: 14 }   // the window's film grain
        radius: 14
        color: Theme.field
        border.width: 1
        border.color: Theme.borderStrong
        layer.enabled: true
        layer.effect: MultiEffect { shadowEnabled: true; shadowBlur: 1.0; shadowOpacity: Theme.isDark ? 0.6 : 0.25; shadowVerticalOffset: 10 }
    }

    contentItem: ColumnLayout {
        spacing: 12
        RowLayout {
            Layout.fillWidth: true
            Text {
                text: Theme.headingText(win.title)
                color: Theme.fg
                font.family: Theme.headingFont
                font.pixelSize: Theme.headingSize
                font.weight: Font.DemiBold
                Layout.fillWidth: true
            }
            AccentButton { text: "Close"; kind: "ghost"; onClicked: win.close() }
        }
        ScrollView {
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            TextArea {
                readOnly: true
                wrapMode: TextEdit.Wrap
                textFormat: win.markdown ? TextEdit.MarkdownText : TextEdit.PlainText
                text: win.text
                color: Theme.fg
                font.family: Theme.bodyFont
                font.pixelSize: Theme.bodySize
                selectByMouse: true
                background: null
            }
        }
    }
}
