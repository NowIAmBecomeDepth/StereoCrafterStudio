import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Effects
import QtQuick.Layouts
import Studio

// Merge All SBS to 1 Video (user, 2026-10-11; studio/merge_all.py). begin() looks through
// every chapter; then either "merge everything?" -- every scene has its merged output --
// or the scenes that don't, each with Play (the default player) and Send to the stage it
// still needs, and Merge Anyway (black in their place, each as long as its scene).
Popup {
    id: dlg
    property var mg: null
    property var info: ({})
    property string phase: "scanning"      // scanning | error | ready | missing
    readonly property var missing: info.missing || []
    readonly property int total: info.total || 0

    parent: Overlay.overlay
    modal: true
    property bool counted: false
    onVisibleChanged: {
        if (visible && !counted) { counted = true; Theme.modals = Theme.modals + 1 }
        else if (!visible && counted) { counted = false; Theme.modals = Math.max(0, Theme.modals - 1) }
    }
    focus: true
    closePolicy: Popup.CloseOnEscape
    anchors.centerIn: parent
    width: Math.min(720, (parent ? parent.width : 900) - 80)
    padding: 22

    function begin() {
        info = ({}); phase = "scanning"
        open()
        mg.mergeAllScan()
    }
    Connections {
        target: dlg.mg
        function onMergeAllScanned(result) {
            dlg.info = result
            dlg.phase = result.error ? "error" : ((result.missing || []).length ? "missing" : "ready")
        }
    }
    function start(override) {
        close()
        mg.mergeAllStart(override)
    }
    function send(i) {
        const p = mg.mergeAllSendPlan(i)
        if (p.error) { Theme.toast("warning", "Couldn't send it on", p.error); return }
        const ctl = { depthcrafter: DepthCrafter, splatting: Splatting, inpainting: Inpainting, merging: Merging }[p.stage]
        if (ctl.busy) { Theme.toast("warning", "Busy", "Wait for that stage's job to finish, then try again."); return }
        close()
        if (p.stage === "depthcrafter") ctl.loadForRerun(p.source, p.output)
        else ctl.loadSent(p)
        Shell.openStage(p.index, p.index === 2 ? 0 : -1)       // Inpainting: V1, which lists clips
    }
    function chaptersText() {
        const n = (info.chapters || []).length
        return total + (total === 1 ? " scene" : " scenes") + " in " + n + (n === 1 ? " chapter" : " chapters")
    }
    function outputsText() {
        return (info.outputs || []).map(p => p.replace(/\//g, "\\")).join("\n")
    }

    Overlay.modal: Rectangle { color: Theme.alpha("#000000", Theme.isDark ? 0.55 : 0.3) }
    enter: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.dur(160) }
            NumberAnimation { property: "scale"; from: 0.94; to: 1; duration: Theme.dur(300); easing.type: Easing.OutBack }
        }
    }
    exit: Transition { NumberAnimation { property: "opacity"; to: 0; duration: Theme.dur(120) } }
    background: Rectangle {
        PopupGrain { radius: 14 }
        radius: 14
        color: Theme.field
        border.width: 1
        border.color: Theme.borderStrong
        layer.enabled: true
        layer.effect: MultiEffect { shadowEnabled: true; shadowBlur: 1.0; shadowOpacity: Theme.isDark ? 0.6 : 0.25; shadowVerticalOffset: 10 }
    }

    component Body: Text {
        color: Theme.fg
        font.family: Theme.bodyFont
        font.pixelSize: Theme.bodySize
        wrapMode: Text.Wrap
        Layout.fillWidth: true
    }

    contentItem: ColumnLayout {
        spacing: 14
        Text {
            text: Theme.headingText(dlg.phase === "missing" ? "Some scenes aren't merged" : "Merge all into one video")
            color: Theme.fg
            font.family: Theme.headingFont
            font.pixelSize: Theme.headingSize
            font.weight: Font.DemiBold
            Layout.fillWidth: true
        }

        // ---- looking
        RowLayout {
            visible: dlg.phase === "scanning"
            spacing: 12
            Spinner { running: dlg.visible && dlg.phase === "scanning"; Layout.preferredWidth: 22; Layout.preferredHeight: 22 }
            Body { text: "Looking through every chapter for its scenes and their merged outputs..." }
        }

        // ---- can't
        Body { visible: dlg.phase === "error"; text: dlg.info.error || "" }

        // ---- every scene there
        Body {
            objectName: "mergeAllReady"
            visible: dlg.phase === "ready"
            text: "Every scene has its merged output: " + dlg.chaptersText() + " of " + (dlg.info.name || "the project")
                  + ".\n\nMerge them all, in order, into " + (dlg.info.layout || "Full SBS") + "?\n"
                  + dlg.outputsText()
                  + (dlg.info.audio ? "\n\nSound: " + dlg.info.audio.replace(/\//g, "\\") : "\n\nNo soundtrack: pick the movie file in Settings > Encoding Options > Merge All for one.")
        }

        // ---- some missing
        Body {
            visible: dlg.phase === "missing"
            text: dlg.missing.length + " of " + dlg.chaptersText() + " have no merged output. Send each to the stage it "
                  + "still needs, play it to see which scene it is, or merge anyway: each missing scene is black for "
                  + "its own length, so the sound still lines up."
        }
        Flickable {
            id: missList
            objectName: "mergeAllMissing"
            visible: dlg.phase === "missing"
            Layout.fillWidth: true
            Layout.preferredHeight: Math.min(missCol.implicitHeight, 320)
            contentHeight: missCol.implicitHeight
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: SlimScrollBar {}
            Column {
              id: missCol
              width: missList.width - 10
              spacing: 6
              Repeater {
               model: dlg.phase === "missing" ? dlg.missing : []
               delegate: Rectangle {
                required property var modelData
                required property int index
                width: missCol.width
                height: row.implicitHeight + 12
                radius: 8
                color: Theme.alpha(Theme.fg, 0.05)
                RowLayout {
                    id: row
                    anchors.fill: parent
                    anchors.margins: 6
                    anchors.leftMargin: 10
                    spacing: 8
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 1
                        Text {
                            text: modelData.stem
                            color: Theme.fg
                            font.family: Theme.bodyFont; font.pixelSize: Theme.bodySize; font.weight: Font.DemiBold
                            elide: Text.ElideMiddle
                            Layout.fillWidth: true
                        }
                        Text {
                            text: modelData.chapterName + " · needs " + modelData.stageTitle + (modelData.note ? " (" + modelData.note + ")" : "")
                            color: Theme.fgMuted
                            font.family: Theme.bodyFont; font.pixelSize: Theme.smallSize
                            elide: Text.ElideRight
                            Layout.fillWidth: true
                        }
                    }
                    AccentButton {
                        text: "Play"; kind: "ghost"
                        onClicked: { const e = Shell.openFile(modelData.source); if (e) Theme.toast("warning", "Couldn't play it", e) }
                        HoverTip { tip: "Open the scene's source clip in your default media player." }
                    }
                    AccentButton {
                        objectName: "mergeAllSend"
                        text: "Send to " + modelData.stageTitle; kind: "outline"
                        onClicked: dlg.send(index)
                        HoverTip { tip: "Load this scene into " + modelData.stageTitle + ", the first stage it hasn't been through, "
                                        + "with the chapter's folders. Check the settings there, then press Start." }
                    }
                }
               }
              }
            }
        }

        RowLayout {
            Layout.alignment: Qt.AlignRight
            spacing: 10
            AccentButton {
                objectName: "mergeAllAnyway"
                visible: dlg.phase === "missing"
                text: "Merge Anyway"; kind: "tonal"
                onClicked: dlg.start(true)
                HoverTip { tip: "Merge everything that's there; each missing scene is black for as many frames as it has. "
                                + "For scenes you skipped on purpose." }
            }
            AccentButton {
                objectName: "mergeAllGo"
                visible: dlg.phase === "ready"
                text: "Merge"; kind: "filled"
                onClicked: dlg.start(false)
            }
            AccentButton {
                text: dlg.phase === "error" ? "Close" : "Cancel"; kind: "ghost"
                onClicked: dlg.close()
            }
        }
    }
}
