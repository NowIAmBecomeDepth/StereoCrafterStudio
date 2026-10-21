import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Effects
import QtQuick.Layouts
import Studio

// Settings > Studio > Check for Updates (user, 2026-09-30; updater.py). The
// source is Studio's own repository's main branch, built in -- no address or
// branch to type (user, 2026-10-18). Opening checks; Install & Restart hands
// over to the helper and Studio closes -- never with a job running, never over
// uncommitted changes.
Popup {
    id: dlg
    objectName: "updateDialog"
    property var up: (typeof Updater !== "undefined") ? Updater : null
    property bool jobRunning: false          // Main binds it
    signal installRefused(string why)

    parent: Overlay.overlay
    modal: true
    onVisibleChanged: Theme.modals = Math.max(0, Theme.modals + (visible ? 1 : -1))
    focus: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    anchors.centerIn: parent
    width: 640
    padding: 22

    onOpened: if (up && !up.busy) up.check()

    Overlay.modal: Rectangle { color: Theme.alpha("#000000", Theme.isDark ? 0.55 : 0.3) }
    enter: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.dur(160) }
            NumberAnimation { property: "scale"; from: 0.94; to: 1; duration: Theme.dur(300); easing.type: Easing.OutBack }
        }
    }
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
                text: Theme.headingText("Updates")
                color: Theme.fg
                font.family: Theme.headingFont
                font.pixelSize: Theme.headingSize
                font.weight: Font.DemiBold
            }
            Item { Layout.fillWidth: true }
            IconButton { iconGlyph: Theme.icon.close; size: 30; onClicked: dlg.close() }
        }

        Text {
            Layout.fillWidth: true
            wrapMode: Text.Wrap
            text: "Studio checks its GitHub page for a newer version and can install it for you."
            color: Theme.fgMuted
            font.family: Theme.bodyFont
            font.pixelSize: Theme.bodySize
        }


        Rectangle {
            Layout.fillWidth: true
            implicitHeight: statusCol.implicitHeight + 24
            radius: 10
            color: Theme.bg
            border.width: Theme.outlineWidth
            border.color: Theme.outline
            ColumnLayout {
                id: statusCol
                x: 12; y: 12
                width: parent.width - 24
                spacing: 8
                RowLayout {
                    spacing: 8
                    Spinner { visible: !!dlg.up && dlg.up.busy; size: 16; thickness: 2 }
                    Text {
                        objectName: "updateMessage"
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        text: !dlg.up ? "" : (dlg.up.message || "Press Check for Updates.")
                        color: dlg.up && (dlg.up.state === "error" || dlg.up.state === "diverged") ? Theme.danger : Theme.fg
                        font.family: Theme.bodyFont
                        font.pixelSize: Theme.bodySize
                    }
                }
                // the changelog, only while an update is waiting (user, 2026-10-18): each change's
                // title and what its commit message says
                Text {
                    objectName: "updateChangelogHeading"
                    visible: commitList.visible
                    text: "What's new"
                    color: Theme.fg
                    font.family: Theme.bodyFont
                    font.pixelSize: Theme.bodySize
                    font.weight: Font.DemiBold
                }
                ListView {
                    id: commitList
                    objectName: "updateCommits"
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.min(contentHeight, 300)
                    visible: count > 0 && !!dlg.up && (dlg.up.state === "available" || dlg.up.state === "diverged")
                    clip: true
                    spacing: 8
                    model: dlg.up ? dlg.up.commits : []
                    ScrollBar.vertical: SlimScrollBar {}
                    delegate: ColumnLayout {
                        required property var modelData
                        width: ListView.view.width - 10
                        spacing: 2
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 10
                            Text { text: modelData.date; color: Theme.fgFaint; font.family: Theme.monoFont; font.pixelSize: 12
                                   Layout.alignment: Qt.AlignTop; topPadding: 2 }
                            Text {
                                objectName: "updateCommitTitle"
                                Layout.fillWidth: true
                                text: modelData.subject
                                wrapMode: Text.Wrap
                                color: Theme.fg
                                font.family: Theme.bodyFont
                                font.pixelSize: Theme.bodySize
                                font.weight: Font.Medium
                            }
                        }
                        Text {
                            objectName: "updateCommitBody"
                            Layout.fillWidth: true
                            Layout.leftMargin: 84
                            visible: !!modelData.body
                            text: modelData.body || ""
                            wrapMode: Text.Wrap
                            color: Theme.fgMuted
                            font.family: Theme.bodyFont
                            font.pixelSize: Theme.smallSize + 1
                        }
                    }
                }
                // the files it changes (user, 2026-10-18): folded, the count on the heading
                Text {
                    id: filesHeading
                    objectName: "updateFilesHeading"
                    property bool open: false
                    // not only under commits: a ZIP's copy has none to list (user, 2026-10-21)
                    visible: !!dlg.up && (dlg.up.state === "available" || dlg.up.state === "diverged")
                             && dlg.up.files.length > 0
                    text: (open ? "\u25BE " : "\u25B8 ") + "Files it changes (" + (dlg.up ? dlg.up.files.length : 0) + ")"
                    color: filesHover.hovered ? Theme.fg : Theme.fgMuted
                    font.family: Theme.bodyFont
                    font.pixelSize: Theme.bodySize
                    HoverHandler { id: filesHover; cursorShape: Qt.PointingHandCursor }
                    TapHandler { onTapped: filesHeading.open = !filesHeading.open }
                }
                ListView {
                    objectName: "updateFiles"
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.min(contentHeight, 180)
                    visible: filesHeading.visible && filesHeading.open
                    clip: true
                    model: dlg.up ? dlg.up.files : []
                    ScrollBar.vertical: SlimScrollBar {}
                    delegate: RowLayout {
                        required property var modelData
                        width: ListView.view.width - 10
                        spacing: 10
                        Text {
                            Layout.preferredWidth: 64
                            text: modelData.kind
                            color: modelData.kind === "deleted" ? Theme.danger
                                 : modelData.kind === "added" ? Theme.success : Theme.fgFaint
                            font.family: Theme.monoFont; font.pixelSize: 12
                        }
                        Text {
                            objectName: "updateFilePath"
                            Layout.fillWidth: true
                            text: modelData.path + (modelData.from ? "  (was " + modelData.from + ")" : "")
                            elide: Text.ElideMiddle
                            color: Theme.fg
                            font.family: Theme.monoFont; font.pixelSize: 12
                        }
                    }
                }
                Text {
                    Layout.fillWidth: true
                    visible: !!dlg.up && dlg.up.needsDeps && dlg.up.state === "available"
                    wrapMode: Text.Wrap
                    text: "This update needs new Python packages. They're installed during the update (uv sync), "
                          + "which can take a few minutes."
                    color: Theme.fgMuted
                    font.family: Theme.bodyFont
                    font.pixelSize: Theme.smallSize + 1
                }
                Text {
                    objectName: "updateBlocked"
                    Layout.fillWidth: true
                    visible: text !== ""
                    wrapMode: Text.Wrap
                    text: !dlg.up ? "" : (dlg.up.blocked || (dlg.up.state === "available" && dlg.jobRunning
                                                              ? "A job is running. Install once it has finished." : ""))
                    color: Theme.warning
                    font.family: Theme.bodyFont
                    font.pixelSize: Theme.bodySize
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            spacing: 10
            Item { Layout.fillWidth: true }
            AccentButton {
                text: "Check for Updates"
                kind: "outline"
                enabled: !!dlg.up && !dlg.up.busy
                onClicked: dlg.up.check()
            }
            AccentButton {
                objectName: "updateInstall"
                text: "Install & Restart"
                iconGlyph: Theme.icon.download
                enabled: !!dlg.up && dlg.up.state === "available" && dlg.up.blocked === "" && !dlg.jobRunning
                onClicked: {
                    const why = dlg.up.install()
                    if (why) dlg.installRefused(why)
                    else dlg.close()
                }
                HoverTip { tip: "Closes Studio, updates it, and opens it again. Your settings and projects are kept." }
            }
        }
    }
}
