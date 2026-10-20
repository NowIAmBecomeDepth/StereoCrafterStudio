import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import Studio

// The foot of a stage, arranged as v1 arranges it:
//
//   [ progress bar                                                        ]
//                          status line (centred)
//               [Start] [Cancel] [ ... ]                 [View Output Folder]
//                        [ secondary buttons ]
//
// DepthCrafter has the bar bare; Inpainting puts bar and status in a
// "Progress" section -- set `title` for that. The buttons are the default
// property and are centred; `trailing` sits at the far right, as v1 packs
// View Output Folder. `secondary` is a centred row beneath, folded away
// while `secondaryShown` is false -- DepthCrafter's segment tools, which do
// nothing without Process as Segments.
Item {
    id: bar
    objectName: "actionBar"
    property string title: ""
    // the console window (Main's); set, a titled bar's heading offers Open Console
    property var consoleRef: null
    property bool busy: false
    property int done: 0
    property int total: 0
    property string status: ""
    // How long the job took, after the status once it ends (user, 2026-10-04:
    // "time completed in" as Inpainting has, for every stage): "Completed in
    // 12m 05s", or "Stopped after ..." when the status says it was stopped.
    // Measured here, from busy going on to off -- two clock readings, nothing
    // redraws while the job runs. Shown while the finish status stands;
    // false for a stage whose own status already says it (Inpainting).
    property bool jobTime: true
    property double jobStartedAt: 0
    property double jobEndedAt: 0
    property string jobTook: ""
    property string jobStatus: ""
    onBusyChanged: {
        if (busy) {
            jobStartedAt = Date.now(); jobTook = ""
        } else if (jobStartedAt > 0) {
            jobEndedAt = Date.now()
            jobTook = jobTime ? fmtDuration((jobEndedAt - jobStartedAt) / 1000) : ""
            jobStatus = status
            jobStartedAt = 0
        }
    }
    // the finish message can land just after busy goes off: it is still the job's
    onStatusChanged: if (jobTook !== "" && Date.now() - jobEndedAt < 3000) jobStatus = status
    function fmtDuration(s) {
        s = Math.max(0, Math.round(s))
        const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), sec = s % 60
        const two = (n) => (n < 10 ? "0" : "") + n
        return h > 0 ? h + "h " + two(m) + "m " + two(sec) + "s" : m > 0 ? m + "m " + two(sec) + "s" : sec + "s"
    }
    readonly property string statusShown: {
        if (busy || jobTook === "" || status !== jobStatus) return status
        const stopped = /stop|cancel/i.test(status)
        const took = (stopped ? "Stopped after " : "Completed in ") + jobTook
        return status !== "" ? status + "  ·  " + took : took
    }
    default property alias buttons: buttonRow.data
    property alias trailing: trailRow.data
    property alias secondary: secondRow.data
    property bool secondaryShown: false
    // DepthCrafter / Inpainting: the chunk ring, the s/it and a clip bar in
    // place of the batch bar (JobRing, JobRate, job_stats.py). Empty: the bar.
    property string statsStage: ""
    // Tips above the bar (TipTicker); empty: none.
    property var tips: []
    // Set ("frames inpainted"): while the job runs, the line under the bar
    // is "50 / 400 frames inpainted" from the finished chunks, in place of
    // the batch's "Processing video n of m" (Current Clip Info has that).
    property string framesLabel: ""
    // Merging (user, 2026-09-27): while the job runs, the bar is the part of
    // the clip running -- the grain match first, then the render (then the
    // L/R split) -- each from 0, read off the engine's own console bars.
    property string phaseStage: ""
    readonly property string mergePhase: phaseStage !== "" && busy && typeof JobStats !== "undefined"
        && JobStats.stage === phaseStage && status.indexOf("Stopping") !== 0 ? JobStats.mergePhase : ""
    readonly property bool showFrames: framesLabel !== "" && busy && typeof JobStats !== "undefined"
        && JobStats.stage === statsStage && JobStats.frames > 0 && status.indexOf("Stopping") !== 0
    // Inpainting, once the chunks are done (JobStats.phase): the bar starts
    // again for the hi-res blend's render, then the encode.
    readonly property string phase: showFrames ? JobStats.phase : ""
    readonly property string framesText: !showFrames ? ""
        : phase === "render" ? "Hi-res blend: " + JobStats.renderDone + " / " + JobStats.renderTotal + " frames rendered"
        : phase === "encode" ? "Encoding the final video…"
        : JobStats.framesDone + " / " + JobStats.frames + " " + framesLabel

    implicitHeight: column.implicitHeight

    // a backing over thin glass (Theme.bed): the whole foot, or -- a titled
    // bar's progress being in a Section already -- the button rows under it
    Rectangle {
        x: -6
        y: bar.title === "" ? -6 : progressBlock.y + progressBlock.height + 2
        width: parent.width + 12
        height: bar.height + 6 - y
        radius: 12
        visible: Theme.bedAlpha > 0
        color: Theme.bed
    }

    function etaText(s, partial) {
        if (s < 0)
            return "ETA N/A"
        var m = Math.round(s / 60)
        var left = s < 60 ? "< 1 min" : m < 60 ? m + " min" : Math.floor(m / 60) + " h " + (m % 60) + " min"
        if (partial)    // what isn't timed yet: Inpainting's hi-res render, DepthCrafter's encode / decode
            return "ETA " + left + (bar.statsStage === "depthcrafter" ? " + encode/decode" : " + render")
        return "ETA " + left + " \u00b7 " + new Date(Date.now() + s * 1000).toLocaleTimeString(Qt.locale(), Locale.ShortFormat)
    }

    component Progress: RowLayout {
        spacing: 16
        // DepthCrafter / Inpainting (user, 2026-09-27): the running chunk as a
        // ring, the last step's s/it beside it, then the bar -- the whole CLIP
        // (finished chunks plus the running one's steps), not the batch.
        JobRing {
            visible: bar.statsStage !== ""
            stage: bar.statsStage
            Layout.alignment: Qt.AlignVCenter
        }
        JobRate {
            visible: bar.statsStage !== ""
            stage: bar.statsStage
            Layout.alignment: Qt.AlignVCenter
            Layout.preferredWidth: 140      // "Inference Step 15 / 15"
        }
        ColumnLayout {
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignVCenter
            spacing: 6
            TipTicker {
                objectName: "tipTicker"
                // Settings > Appearance > Show Tips turns them off everywhere
                visible: bar.tips.length > 0 && (typeof Shell === "undefined" || Shell.showTips)
                tips: bar.tips
                Layout.fillWidth: true
                Layout.bottomMargin: 6
            }
            // the clip, when this stage has the numbers
            RowLayout {
                visible: bar.statsStage !== ""
                Layout.fillWidth: true
                spacing: 10
                readonly property bool mine: typeof JobStats !== "undefined" && JobStats.stage === bar.statsStage
                readonly property real clip: !mine ? 0
                    : bar.phase === "render" ? (JobStats.renderTotal > 0 ? JobStats.renderDone / JobStats.renderTotal : 0)
                    : bar.phase === "encode" ? 1
                    : JobStats.fraction
                ShimmerBar {
                    objectName: "clipBar"
                    Layout.fillWidth: true
                    implicitHeight: 8
                    value: parent.clip
                    shimmer: false
                }
                Text {
                    id: clipPercent
                    objectName: "clipPercent"
                    // just the number (user, 2026-10-09); the blend and the encode keep their word
                    text: !(parent.mine && JobStats.chunks > 0) ? "—"
                        : bar.phase === "render" ? "render " + Math.round(parent.clip * 100) + "%"
                        : bar.phase === "encode" ? "encode"
                        : Math.round(parent.clip * 100) + "%"
                    color: parent.mine && JobStats.chunks > 0 ? Theme.fg : Theme.fgFaint
                    font.family: Theme.bodyFont
                    font.pixelSize: Theme.smallSize + 1
                    font.features: { "tnum": 1 }
                    Layout.preferredWidth: 64
                    horizontalAlignment: Text.AlignRight
                    // Inpainting: when the clip will be done (JobStats.eta, worked out
                    // per step and chunk), under the percentage. Anchored, not laid out,
                    // so it never narrows the bar; a finish time, so nothing ticks
                    // between updates (Theme.calm).
                    Text {
                        objectName: "jobEtaText"
                        visible: (bar.statsStage === "inpainting" || bar.statsStage === "depthcrafter")
                                 && clipPercent.parent.mine
                        anchors.right: parent.right
                        anchors.top: parent.bottom
                        anchors.topMargin: 4
                        text: visible ? bar.etaText(JobStats.eta, JobStats.etaPartial) : ""
                        color: JobStats.eta >= 0 ? Theme.fg : Theme.fgFaint
                        font.family: Theme.bodyFont
                        font.pixelSize: Theme.smallSize + 1
                        font.features: { "tnum": 1 }
                    }
                }
            }
            // the batch, elsewhere -- or Merging's running phase
            RowLayout {
                id: phaseRow
                visible: bar.statsStage === ""
                Layout.fillWidth: true
                spacing: 10
                readonly property bool phased: bar.mergePhase !== ""
                Item {
                    Layout.fillWidth: true
                    implicitHeight: 8
                    ShimmerBar {
                        objectName: "batchBar"
                        anchors.fill: parent
                        value: phaseRow.phased ? JobStats.mergePercent / 100
                             : (bar.total > 0 ? bar.done / bar.total : 0)
                        shimmer: false   // it shows only while busy, and never draws then (Theme.calm)
                    }
                    // No spinner or indeterminate bar (user, 2026-09-27): the bar
                    // moves only when the job reports progress, so a running job
                    // leaves the window still -- with the card full, every redraw
                    // costs the job (see Spinner).
                }
                Text {
                    objectName: "phasePercent"
                    visible: phaseRow.phased
                    text: !phaseRow.phased ? ""
                          : (bar.mergePhase === "grain" ? "grain match " : bar.mergePhase === "split" ? "split L/R " : "render ")
                            + Math.round(JobStats.mergePercent) + "%"
                    color: Theme.fg
                    font.family: Theme.bodyFont
                    font.pixelSize: Theme.smallSize + 1
                    font.features: { "tnum": 1 }
                }
            }
            Row {
                Layout.alignment: Qt.AlignHCenter
                spacing: 8
                MorphLabel {
                    objectName: "abStatus"
                    anchors.verticalCenter: parent.verticalCenter
                    text: bar.showFrames ? bar.framesText
                        : bar.mergePhase === "grain" ? "Matching grain" + (JobStats.mergeClip ? ": " + JobStats.mergeClip : "…")
                        : bar.mergePhase === "split" ? "Splitting into left / right videos…"
                        : bar.statusShown
                    color: Theme.fg
                    family: Theme.bodyFont
                    pixelSize: Theme.bodySize
                }
            }
        }
    }

    ColumnLayout {
        id: column
        width: parent.width
        spacing: 8

        // One progress block, bare or in its "Progress" section -- not both
        // built with one hidden (audit 2026-09-28 tidy-up).
        Loader {
            id: progressBlock
            Layout.fillWidth: true
            sourceComponent: bar.title.length > 0 ? titledProgress : bareProgress
        }
        Component { id: bareProgress; Progress {} }
        Component {
            id: titledProgress
            Section {
                title: bar.title
                // Open Console on the heading line, as Current Clip Info has it (Scene Cut,
                // user 2026-10-18); live during a job
                headerRight: [
                    AccentButton {
                        objectName: "openConsole"
                        visible: bar.consoleRef !== null
                        text: "Open Console"
                        kind: "ghost"
                        iconGlyph: Theme.icon.console
                        implicitHeight: 26
                        onClicked: bar.consoleRef.open = true
                    }
                ]
                Progress { Layout.fillWidth: true }
            }
        }

        Item {
            id: buttonLine
            Layout.fillWidth: true
            // The buttons centred, the trailing ones at the right; slid left clear of them when
            // there's room for both, else the trailing ones on a line of their own under the rest
            // (2026-10-11: Merging's Merge All beside View Output Folder covered Move to Finish)
            readonly property bool tight: trailRow.implicitWidth > 0
                && buttonRow.implicitWidth + 16 + trailRow.implicitWidth > width
            implicitHeight: tight ? buttonRow.implicitHeight + 8 + trailRow.implicitHeight
                                  : Math.max(buttonRow.implicitHeight, trailRow.implicitHeight)
            RowLayout {
                id: buttonRow; objectName: "abButtons"; spacing: 10
                y: (buttonLine.tight ? buttonRow.implicitHeight : buttonLine.height) / 2 - height / 2
                x: buttonLine.tight ? (buttonLine.width - width) / 2
                                    : Math.max(0, Math.min((buttonLine.width - width) / 2, buttonLine.width - trailRow.implicitWidth - 16 - width))
            }
            RowLayout {
                id: trailRow; objectName: "abTrailing"; spacing: 8
                anchors.right: parent.right
                y: buttonLine.tight ? buttonRow.implicitHeight + 8 : (buttonLine.height - height) / 2
            }
        }

        Item {
            id: second
            Layout.fillWidth: true
            // Folds like a FieldRow; the column's spacing folds with it, so a
            // hidden row costs no height at all.
            property real reveal: bar.secondaryShown && secondRow.children.length > 0 ? 1 : 0
            Behavior on reveal { NumberAnimation { duration: Theme.dur(300); easing.type: Easing.InOutCubic } }
            implicitHeight: secondRow.implicitHeight * reveal
            Layout.topMargin: (reveal - 1) * column.spacing
            opacity: reveal
            visible: reveal > 0.001
            clip: reveal < 1
            RowLayout { id: secondRow; objectName: "abSecondary"; anchors.horizontalCenter: parent.horizontalCenter; anchors.bottom: parent.bottom; spacing: 10 }
        }
    }
}
