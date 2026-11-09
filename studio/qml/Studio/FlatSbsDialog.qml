import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Effects
import QtQuick.Layouts
import Studio

// "Render as 2D Full SBS" (user, 2026-11-08: end credits with no depth): asks where the flat
// Full SBS goes before it's written -- Merging's Output Folder to start with, any other picked
// or typed -- and names the file Merging would give it.
// open(dc, path): `dc` the DepthCrafter controller, `path` the clip.
Popup {
    id: dlg
    objectName: "flatSbsDialog"
    parent: Overlay.overlay
    modal: true
    focus: true
    closePolicy: Popup.CloseOnEscape
    anchors.centerIn: parent
    width: Math.min(600, (parent ? parent.width : 800) - 80)
    padding: 22

    property var dc: null
    property string path: ""
    property string fileName: ""
    readonly property string folder: folderBox.text.trim()

    // counted while shown: the page under it ignores clicks (Theme.modalOpen), as PromptDialog
    property bool counted: false
    onVisibleChanged: {
        if (visible && !counted) { counted = true; Theme.modals = Theme.modals + 1 }
        else if (!visible && counted) { counted = false; Theme.modals = Math.max(0, Theme.modals - 1) }
    }

    function show(ctl, clip) {
        dc = ctl
        path = clip
        folderBox.text = ctl ? ctl.flatSbsFolder() : ""
        fileName = ctl ? ctl.flatSbsName(clip) : ""
        open()
    }
    function go() {
        if (!dlg.folder) return
        const c = dlg.dc, p = dlg.path, f = dlg.folder
        close()
        if (c) c.renderFlatSbs(p, f)
    }

    FolderDialog {
        id: pick
        title: "Where to write the 2D Full SBS"
        currentFolder: Shell.pathToUrl(dlg.folder)
        onAccepted: folderBox.text = Shell.urlToPath(selectedFolder).replace(/\\/g, "/")
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
            shadowOpacity: Theme.isDark ? 0.6 : 0.25
            shadowVerticalOffset: 10
        }
    }

    contentItem: ColumnLayout {
        spacing: 14
        Text {
            text: Theme.headingText("Render as 2D Full SBS")
            color: Theme.fg
            font.family: Theme.headingFont
            font.pixelSize: Theme.headingSize
            font.weight: Font.DemiBold
            Layout.fillWidth: true
        }
        Text {
            text: "For a clip with no depth to give it, such as end credits: the same picture in both eyes, "
                  + "side by side, written with Merging's Encoding Settings under the name Merging gives a Full SBS. "
                  + "It's done when it's written: the clip moves to finished and skips Splatting, Inpainting and Merging."
            color: Theme.fgMuted
            wrapMode: Text.WordWrap
            font.family: Theme.bodyFont; font.pixelSize: Theme.bodySize
            Layout.fillWidth: true
        }
        GridLayout {
            columns: 2
            columnSpacing: 12; rowSpacing: 6
            Layout.fillWidth: true
            Text { text: "Clip"; color: Theme.fgMuted; font.family: Theme.bodyFont; font.pixelSize: Theme.bodySize }
            Text {
                objectName: "flatSbsClip"
                text: dlg.path.replace(/^.*[\\/]/, "")
                color: Theme.fg; elide: Text.ElideMiddle
                font.family: Theme.bodyFont; font.pixelSize: Theme.bodySize; font.weight: Font.DemiBold
                Layout.fillWidth: true
            }
            Text { text: "Written as"; color: Theme.fgMuted; visible: dlg.fileName.length > 0
                   font.family: Theme.bodyFont; font.pixelSize: Theme.bodySize }
            Text {
                objectName: "flatSbsFile"
                visible: dlg.fileName.length > 0
                text: dlg.fileName
                color: Theme.fg; elide: Text.ElideMiddle
                font.family: Theme.bodyFont; font.pixelSize: Theme.bodySize
                Layout.fillWidth: true
            }
        }
        Text { text: "Send it to"; color: Theme.fg; font.family: Theme.bodyFont; font.pixelSize: Theme.bodySize; font.weight: Font.DemiBold }
        RowLayout {
            spacing: 8
            Layout.fillWidth: true
            TextBox {
                id: folderBox
                objectName: "flatSbsFolder"
                numeric: false
                Layout.fillWidth: true
                onAccepted: dlg.go()
            }
            AccentButton { text: "Browse..."; kind: "outline"; onClicked: pick.open() }
        }
        Text {
            text: dlg.folder ? "" : "Pick a folder: Merging's Output Folder is empty."
            visible: text.length > 0
            color: Theme.danger
            font.family: Theme.bodyFont; font.pixelSize: Theme.smallSize
        }
        RowLayout {
            Layout.alignment: Qt.AlignRight
            spacing: 10
            AccentButton {
                objectName: "flatSbsGo"
                text: "Render"
                kind: "filled"
                enabled: dlg.folder.length > 0
                onClicked: dlg.go()
            }
            AccentButton { objectName: "flatSbsCancel"; text: "Cancel"; kind: "ghost"; onClicked: dlg.close() }
        }
    }
}
