import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import Studio

// Upcoming Jobs (user, 2026-09-27; Merging, then Splatting): the clips the
// batch will work through, the Batch Progress counter above them. Idle, a
// click on a row loads that clip into the preview. While a job runs, each row
// carries its own bar: the clips done full, the one being worked on at
// `progress` (the page says how far, when it can), the rest empty.
//
// The page supplies the stage's side: `running` (the clip the job reports,
// with or without its extension), `progress`, the counter, and `trustRunning`
// -- whether `running` only ever names the job's clips (Splatting blanks it at
// the start) or can still name the preview's (Merging), in which case a clip
// counts as worked on only once `working` goes true for it.
//
// The rows arrive one after another when the list changes -- only then, and
// only while no job runs (Theme.calm): nothing here animates during a job.
//
// A right-click on a row (user, 2026-10-10) offers what the page puts in
// `rowActions` -- [{ id, text, whenDone }] -- and sends the pick back as rowAction(id, path);
// a whenDone action is offered on a row finished this run (green) only.
Section {
    id: queue
    objectName: "jobQueue"
    title: "Upcoming Jobs"

    property var pv: null                  // the stage's preview (jobClips, clipPaths, videoIndex, showClip)
    // the stage's controller: Process Selected runs its startSelected()
    property var ctl: null
    // A stage with no preview (DepthCrafter, Inpainting; user, 2026-10-10) hands its
    // ClipQueue here: its rows don't load anything on a click
    property var source: null
    // the list itself: from the input folders (clip_queue.py), there before
    // any clip is picked and kept through a job
    readonly property var src: source ? source : (pv ? pv.jobClips : null)
    // The lists read ONCE per change, here; the rows index these. Each row read
    // src.clipPaths / clipFrames / clipFx itself -- a fresh copy of the whole list
    // from Python per read, about 6N copies of N entries per change (bug sweep A2)
    readonly property var paths: src ? src.clipPaths : []
    readonly property var frameCounts: src ? src.clipFrames : []
    readonly property var ratios: src && src.clipRatios !== undefined ? src.clipRatios : []
    readonly property var fxs: src ? src.clipFx : []
    // the clip the preview shows
    readonly property string shownPath: pv && pv.loaded ? (pv.clipPaths[pv.videoIndex] || "") : ""
    property bool busy: false
    // a row's right-click menu: what the page offers ([] none), and the pick
    property var rowActions: []
    signal rowAction(string id, string path)

    // "Send to <next stage>" (user, 2026-10-11; studio.send_on): a finished clip's
    // output loaded as the next stage's input, its output the same chapter's folder
    // -- `ctl` this stage, `next` / `nextCtl` / `stageIndex` the one after it
    function sendOn(path, ctl, next, nextCtl, stageIndex) {
        if (nextCtl.busy) { Theme.toast("warning", "Busy", "Wait for that stage's job to finish, then try again."); return }
        let ch = ({})
        if (ctl.chapterNum >= 0) {
            const c = (ctl.chapters || []).find(c => c.num === ctl.chapterNum)
            ch = Shell.stagePaths(next, ctl.chapterNum, c ? c.name : "")
        }
        const p = queue.src.sendPlan(path, ch)
        if (p.error) { Theme.toast("warning", "Couldn't send it on", p.error); return }
        nextCtl.loadSent(p)
        Shell.openStage(stageIndex, stageIndex === 2 ? 0 : -1)        // Inpainting: V1, which lists clips
    }
    // the ticks (user, 2026-09-30): Process Selected, Move Selected to Finished
    readonly property var ticks: src ? src.checked : []
    readonly property int picked: src ? src.checkedCount : 0
    readonly property bool moving: !!src && src.moving
    Connections {
        target: queue.src
        function onMovedToFinished(moved, left) {
            if (left.length)
                Theme.toast("warning", "Some clips were not moved", left.join(", ")
                            + " -- still in use. The rest went to finished.")
            else
                Theme.toast("success", moved === 1 ? "1 clip moved to finished" : moved + " clips moved to finished",
                            "Restore Finished brings them back.")
        }
    }
    property string running: ""            // the clip the job is on ("" idle)
    property real progress: 0              // how far the job is into `running`, 0-1
    property bool working: false           // Merging: the job really is working on `running`
    property bool trustRunning: false
    property string countText: "—"
    property string countHelp: ""
    // Splatting: a bar per pass, H over L (user, 2026-09-28: the one bar sat
    // empty until the clip was done). The page gives the labels, which passes
    // the job runs, and for the running clip the frames each pass has made
    // and which have finished; the row's frame count makes that a fraction.
    property var passes: []                // e.g. ["H", "L"]; empty: one bar from `progress`
    property var passOn: []
    property var passFrames: []
    property var passDone: []
    // the running clip is finished: every pass it runs done
    readonly property bool runningFinished: passes.length
        ? passes.every((_p, i) => !passOn[i] || !!passDone[i]) : progress >= 1
    // the name part that the job's reports and the list share
    function keyOf(name) { return String(name).replace(/\.[^.\\/]+$/, "") }
    readonly property int rowHeight: 52       // room for the clip's still (user, 2026-09-27)
    // a row with badges besides the sidecar's: a line for them under the name (user, 2026-10-17)
    readonly property int fxLineHeight: 16
    function fxUnder(fx) { return !!fx && (!!fx[0] || !!fx[1] || !!fx[2] || !!fx[4] || !!fx[5] || !!fx[6] || !!fx[7]) }
    // the row numbers' column: as wide as the list's biggest number
    TextMetrics {
        id: numberMetrics
        font.family: Theme.bodyFont
        font.pixelSize: Theme.smallSize + 1
        text: String(Math.max(9, queue.src ? queue.src.clips.length : 0)).replace(/./g, "0")
    }
    // each row's bar: a set width, the name takes the rest
    readonly property real barWidth: Math.max(40, Math.min(110, (list.width - 8) * 0.22))
    // the bars' column: with passes, each bar has its letter before it
    readonly property real passColumnWidth: barWidth + (passes.length ? passMetrics.width + 4 : 0)
    TextMetrics {
        id: passMetrics
        font.family: Theme.bodyFont
        font.pixelSize: Theme.smallSize
        font.weight: Font.DemiBold
        text: "L:"
    }
    // the clip count on the heading line (user, 2026-09-27)
    headerRight: [
        Text {
            objectName: "queueClipCount"
            text: queue.src && queue.src.clips.length
                  ? queue.src.clips.length + " clips" + (queue.picked ? "  ·  " + queue.picked + " selected" : "") : ""
            color: Theme.fgFaint
            font.family: Theme.bodyFont
            font.pixelSize: Theme.smallSize
        }
    ]
    // Takes the room the rail has left rather than asking for its own: the
    // page must still fit a 1080p screen at 85% (check_merging_preview), and
    // the rail had 18 px to spare. So its implicit height is fixed -- the
    // heading, the counter and one row -- and the list fills whatever height
    // the layout gives the box (Layout.fillHeight).
    readonly property real fixedPart: 2 * Theme.sectionPad + 58
    // one whole row at least -- the tallest one: a row with its badge line under the name is
    // fxLineHeight taller (user, 2026-10-24: in a squeezed Splatting list it was cut off)
    readonly property bool anyFxUnder: { for (let i = 0; i < fxs.length; i++) if (fxUnder(fxs[i])) return true; return false }
    readonly property real minList: rowHeight + (anyFxUnder ? fxLineHeight : 0) + 4
    // the room the rows have: the box less its heading, counter and tick bar. Short of one whole
    // row the list and its legend step aside -- the heading and counter stay -- rather than show
    // a row cut off at the bottom; the legend shows only where it fits under a whole row.
    readonly property real listRoom: height - fixedPart - (tickBar.visible ? tickBar.height + spacing : 0)
    readonly property bool listFits: listRoom >= minList
    // ...plus the tick bar when it shows: without it a short page (Merging at its minimum height) squeezed
    // the list below one row and the row spilled out of the box (user, 2026-10-10: "the alignment is off").
    implicitHeight: fixedPart + minList + (tickBar.visible ? tickBar.height + spacing : 0)
    Layout.fillHeight: true

    // the clips it has finished this run, by keyOf
    property var done: ({})
    property string lastRunning: ""
    // the clips the job has actually worked on: at the start, clipName still
    // names whatever the preview held, and that clip is not done by being left
    property var worked: ({})
    onBusyChanged: {
        if (busy) { done = ({}); worked = ({}); lastRunning = "" }
        batchKey = pathsKey       // the clips the batch ran over (held while it runs)
        countStale = false
    }
    // The batch's count stays up after it ends -- until the list holds other
    // clips (a new batch loaded): then it's "—" until the next run (user,
    // 2026-10-05: the last batch's 29 / 29 sat over a new batch of 40).
    readonly property string pathsKey: src ? JSON.stringify(paths) : ""
    property string batchKey: ""
    property bool countStale: false
    onPathsKeyChanged: if (!busy && batchKey !== "" && pathsKey !== batchKey) countStale = true
    function markWorked() {
        const k = keyOf(running)
        if (running !== "" && (trustRunning || working) && !worked[k]) {
            const w = Object.assign({}, worked); w[k] = true; worked = w
        }
    }
    onWorkingChanged: markWorked()
    onRunningChanged: {
        const k = keyOf(running)
        // on to ANOTHER clip: the one before is done. The name clearing as the
        // job ends is not that -- the end is judged by its passes below (audit
        // 2026-09-28: a Stop between the passes marked the clip done here)
        if (running !== "" && lastRunning !== "" && lastRunning !== k && worked[lastRunning]) {
            const d = Object.assign({}, done); d[lastRunning] = true; done = d
        }
        if (running !== "") lastRunning = k
        markWorked()
    }
    // when the job ends, the last clip it was on is done too (unless stopped
    // part-way -- the counter says which; a stopped clip keeps its bar)
    Connections {
        target: queue
        function onBusyChanged() {
            if (!queue.busy && queue.lastRunning !== "" && queue.worked[queue.lastRunning] && queue.runningFinished) {
                const d = Object.assign({}, queue.done); d[queue.lastRunning] = true; queue.done = d
            }
        }
    }

    // the list's label: no _inpainted_ suffix and no extension (the tooltip has the file)
    function shortName(name) {
        return keyOf(String(name).replace(/_inpainted_(right_eye|dual_sbs|sbs)F?(?=\.[^.]+$)/, ""))
    }
    // The clip the job is on shows its live progress even if it was marked done
    // earlier in the run (a clip done twice, or a stale mark): done is for the
    // clips the job has left.
    function passProgressOf(name, i, frames) {
        if (!passOn[i]) return 0
        const k = keyOf(name)
        // the clip the job is on -- or, once a job was stopped part-way, the
        // one it stopped on: it keeps its bars (the passes stay until the next
        // run starts) rather than showing empty
        const live = (running !== "" && k === keyOf(running))
                     || (!busy && running === "" && k === lastRunning && !done[k] && !!worked[k])
        if (!live) return done[k] ? 1 : 0
        if (passDone[i]) return 1
        return frames > 0 ? Math.max(0, Math.min(1, (passFrames[i] || 0) / frames)) : 0
    }
    function progressOf(name) {
        const k = keyOf(name)
        if (running !== "" && k === keyOf(running)) return Math.max(0, Math.min(1, progress))
        return done[k] ? 1 : 0
    }

    // entrance: rows made within a moment of the list changing arrive staggered
    property double revealAt: 0
    Connections {
        target: queue.src
        function onChanged() {
            const n = queue.src ? queue.src.clips.length : 0
            if (n !== list.count || queue._firstNames !== (n ? queue.src.clips[0] : "")) {
                queue._firstNames = n ? queue.src.clips[0] : ""
                queue.revealAt = Date.now()
            }
        }
    }
    property string _firstNames: ""

    // ---- the Special FX badges (user, 2026-09-27 on) and their legend (2026-10-03) ----
    // [kind, the row flag it reads (clip_queue.splat_fx), objectName, name]; the
    // order they sit in on a row
    readonly property var fxOrder: [
        { kind: "sidecar", flag: 3, name: "fxSidecar", label: "Sidecar saved" },
        { kind: "edited", flag: 4, name: "fxDepthEdited", label: "Depth map edited in the Depth Map Editor" },
        { kind: "text", flag: 5, name: "fxTextMask", label: "Detect Text mask saved" },
        { kind: "keyframe", flag: 0, name: "fxKeyframe", label: "Keyframes" },
        { kind: "break", flag: 1, name: "fxFrameBreak", label: "Frame Break" },
        { kind: "pop", flag: 2, name: "fxPopBoost", label: "Pop Out Boost" },
        { kind: "particles", flag: 6, name: "fxParticles", label: "Rain" },      // was "Particles (rain)" (user, 2026-10-10)
        { kind: "erased", flag: 7, name: "fxErased", label: "Object removed (Erase from Clip)" }   // user, 2026-10-16
    ]
    // which the legend names (Merging only ever shows the sidecar); [] hides it
    property var legendKinds: []
    readonly property var legendItems: fxOrder.filter(f => legendKinds.indexOf(f.kind) >= 0)
    readonly property var legendText: ({ sidecar: "Sidecar", edited: "Depth edited", text: "Detect Text",
                                         keyframe: "Keyframes", "break": "Frame Break", pop: "Pop Out Boost",
                                         particles: "Rain", erased: "Object removed" })

    component FxIcon: Item {
        property string kind: ""
        width: kind === "text" || kind === "break" ? 14 : kind === "sidecar" ? 11 : 12
        height: 12
        // a sidecar: a page with its corner folded down
        Item {
            visible: parent.kind === "sidecar"
            anchors.fill: parent
            Rectangle {
                x: 1; y: 0
                width: 9; height: 12; radius: 1.5
                color: "transparent"
                border.width: 1.2
                border.color: Theme.fgMuted
                Rectangle { x: 2.5; y: 4.5; width: 4; height: 1.2; color: Theme.fgMuted }
                Rectangle { x: 2.5; y: 7.2; width: 4; height: 1.2; color: Theme.fgMuted }
            }
            Rectangle { x: 7; y: 0; width: 3; height: 3; color: Theme.fgMuted; radius: 0.5 }
        }
        // the depth map is an edit from the Depth Map Editor: a pencil, in the accent ink
        Icon {
            visible: parent.kind === "edited"
            anchors.centerIn: parent
            glyph: Theme.icon.edit
            size: 11
            color: Theme.accentInk
        }
        // Detect Text saved a mask: a caption box with a T in it
        Rectangle {
            visible: parent.kind === "text"
            anchors.centerIn: parent
            width: 14; height: 11; radius: 2
            color: "transparent"
            border.width: 1.2
            border.color: Theme.accentInk
            Text {
                anchors.centerIn: parent
                anchors.verticalCenterOffset: 0.5
                text: "T"
                color: Theme.accentInk
                font.family: Theme.buttonFont
                font.pixelSize: 8
                font.weight: Font.Bold
            }
        }
        // keyframes: a diamond, in the keyframe strip's blue
        Rectangle {
            visible: parent.kind === "keyframe"
            anchors.centerIn: parent
            width: 8; height: 8; rotation: 45; radius: 1
            color: "#4fa8ff"
        }
        // frame break: a frame with letterbox bars
        Rectangle {
            visible: parent.kind === "break"
            anchors.centerIn: parent
            width: 14; height: 10; radius: 1.5
            color: "transparent"
            border.width: 1.2
            border.color: Theme.fgMuted
            Rectangle { x: 0; y: 0; width: parent.width; height: 2.5; color: Theme.accentInk }
            Rectangle { x: 0; y: parent.height - 2.5; width: parent.width; height: 2.5; color: Theme.accentInk }
        }
        // particles (user, 2026-10-08): three short slanting streaks, like falling rain
        Item {
            visible: parent.kind === "particles"
            anchors.fill: parent
            Repeater {
                model: [[2, 1], [6.5, 3.5], [3.5, 7]]
                Rectangle {
                    required property var modelData
                    x: modelData[0]; y: modelData[1]
                    width: 1.6; height: 4.5; radius: 0.8
                    rotation: 20
                    color: "#7fc4ff"
                }
            }
        }
        // object removed (user, 2026-10-16): a circle struck through
        Item {
            visible: parent.kind === "erased"
            anchors.fill: parent
            Rectangle {
                anchors.centerIn: parent
                width: 11; height: 11; radius: 5.5
                color: "transparent"
                border.width: 1.4
                border.color: "#c38bff"
            }
            Rectangle {
                anchors.centerIn: parent
                width: 1.6; height: 13; radius: 0.8
                rotation: 45
                color: "#c38bff"
            }
        }
        // pop out boost: a burst -- a dot with a ring around it
        Rectangle {
            visible: parent.kind === "pop"
            anchors.centerIn: parent
            width: 11; height: 11; radius: 5.5
            color: "transparent"
            border.width: 1.5
            border.color: Theme.warning
            Rectangle { anchors.centerIn: parent; width: 4; height: 4; radius: 2; color: Theme.warning }
        }
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: 8
        Text {
            text: "Batch Progress:"
            color: Theme.fgMuted
            font.family: Theme.bodyFont
            font.pixelSize: Theme.bodySize
            HoverTip { tip: queue.countHelp }
        }
        Text {
            objectName: "queueBatchProgress"
            text: queue.countStale ? "—" : (queue.countText || "—")
            color: Theme.fg
            font.family: Theme.bodyFont
            font.pixelSize: Theme.bodySize
            font.features: { "tnum": 1 }
        }
        Item { Layout.fillWidth: true }
        // what the bars are, its P over their left ends (user, 2026-09-27):
        // a row's bar ends 10 px in from the row, the row 8 px in from the list
        Text {
            objectName: "queueProgressLabel"
            text: "Progress"
            color: Theme.fgMuted
            font.family: Theme.bodyFont
            font.pixelSize: Theme.smallSize
            Layout.preferredWidth: queue.passColumnWidth + 18
            horizontalAlignment: Text.AlignLeft
            HoverTip { tip: queue.passes.length ? "H is the full-resolution pass, L the low-resolution one: each fills as the job renders that pass, full once done."
                                                   : "Each clip's bar fills as the job works on it: full once done." }
        }
    }

    // what to do with ticked clips (user, 2026-09-30). Idle only; it wraps to
    // a second line in a narrow column (Splatting's), and takes its height
    // from the list, never from the page's budget (fixedPart).
    Flow {
        id: tickBar
        objectName: "queueTickBar"
        Layout.fillWidth: true
        // at most the list's inner width, so it wraps (2026-10-10: the layout gave it its
        // one-line width, and in a 330 px list the buttons ran out of the box)
        Layout.preferredWidth: 0
        Layout.minimumWidth: 0
        Layout.maximumWidth: queue.width - 28
        visible: !queue.busy && !!queue.src && queue.src.clips.length > 0
        spacing: 6
        AccentButton {
            objectName: "queueSelectAll"
            text: "Select All"
            kind: "ghost"
            implicitHeight: 24
            enabled: !queue.moving && queue.picked < (queue.src ? queue.src.clips.length : 0)
            onClicked: queue.src.checkAll()
            HoverTip { tip: "Ticks every clip in the list." }
        }
        AccentButton {
            objectName: "queueSelectNone"
            text: "Deselect All"
            kind: "ghost"
            implicitHeight: 24
            enabled: !queue.moving && queue.picked > 0
            onClicked: queue.src.checkNone()
        }
        AccentButton {
            objectName: "queueProcessSelected"
            text: "Process Selected"
            kind: "outline"
            implicitHeight: 24
            enabled: !!queue.ctl && !queue.moving && queue.picked > 0
            onClicked: queue.ctl.startSelected()
            HoverTip { tip: "Runs the batch on the ticked clips only, with the settings as they are. Start still runs every clip." }
        }
        AccentButton {
            objectName: "queueMoveFinished"
            text: queue.moving ? "Moving..." : "Move to Finished"
            kind: "ghost"
            implicitHeight: 24
            enabled: !queue.moving && queue.picked > 0
            onClicked: queue.src.moveCheckedToFinished()
            HoverTip { tip: "Moves the ticked clips, and the files that go with each, into the 'finished' folders now, "
                            + "without processing them. They leave the list; Restore Finished brings them back." }
        }
    }

    ListView {
        id: list
        objectName: "jobQueueList"
        Layout.fillWidth: true
        Layout.preferredHeight: Math.max(queue.minList, queue.listRoom
                                         - (legend.visible ? legend.height + queue.spacing : 0))
        visible: queue.listFits
        clip: true
        spacing: 6
        model: queue.src ? queue.src.clips : []
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: SlimScrollBar { onPressedChanged: if (pressed) wheelGlide.stop() }
        // The wheel scrolls two rows a notch (user, 2026-09-28: Qt's own step
        // crawled through a long list); a touchpad keeps its own pixel steps.
        // A notch glides there (user, 2026-10-05: smooth scrolling); notches in
        // quick succession add up and the glide carries on to the sum. It glides
        // during Splatting's and Merging's own jobs too (not heavy on the GPU --
        // user); while a heavier stage runs (Theme.durLight) it's an instant jump.
        property real wheelTarget: 0
        NumberAnimation {
            id: wheelGlide
            objectName: "jobQueueGlide"
            target: list
            property: "contentY"
            duration: Theme.durLight(220)
            easing.type: Easing.OutCubic
        }
        WheelHandler {
            objectName: "jobQueueWheel"
            parent: list
            acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
            onWheel: (e) => {
                const step = queue.rowHeight + list.spacing
                const top = Math.max(0, list.contentHeight - list.height)
                e.accepted = true
                if (e.pixelDelta.y !== 0) {            // a touchpad: already smooth, follow it
                    wheelGlide.stop()
                    list.contentY = Math.max(0, Math.min(top, list.contentY - e.pixelDelta.y))
                    return
                }
                // from where a glide still running is heading, so quick notches add up
                const from = wheelGlide.running ? list.wheelTarget : list.contentY
                list.wheelTarget = Math.max(0, Math.min(top, from - e.angleDelta.y / 120 * step * 2))
                if (wheelGlide.duration <= 0) {        // a heavy job runs: no animation
                    wheelGlide.stop()
                    list.contentY = list.wheelTarget
                    return
                }
                wheelGlide.stop()
                wheelGlide.from = list.contentY
                wheelGlide.to = list.wheelTarget
                wheelGlide.start()
            }
        }
        // a flick, a drag of the scroll bar or the list keeping the job's clip in
        // view takes over from a glide
        onMovementStarted: wheelGlide.stop()
        // the job's clip kept in view as it moves on
        Connections {
            target: queue
            function onRunningChanged() {
                if (!queue.src || queue.running === "") return
                const k = queue.keyOf(queue.running)
                const i = queue.src.clips.findIndex(n => queue.keyOf(n) === k)
                if (i >= 0) { wheelGlide.stop(); list.positionViewAtIndex(i, ListView.Contain) }
            }
        }

        delegate: Rectangle {
            id: row
            required property int index
            required property string modelData
            objectName: "jobQueueRow"
            width: ListView.view.width - 8
            readonly property var fx: queue.fxs.length > row.index ? queue.fxs[row.index] : null
            readonly property bool fxUnder: queue.fxUnder(fx)
            height: queue.rowHeight + (fxUnder ? queue.fxLineHeight : 0)
            radius: 8
            readonly property string path: queue.paths.length > index ? queue.paths[index] : ""
            // the clip the preview shows
            readonly property bool selected: !queue.busy && path !== "" && queue.shownPath === path
            readonly property bool current: queue.running !== "" && queue.keyOf(modelData) === queue.keyOf(queue.running)
            // finished this run: a light green of its own (user, 2026-10-05), until
            // the next run starts (queue.done is cleared then)
            readonly property bool finished: !current && !!queue.done[queue.keyOf(modelData)]
            color: current ? Theme.alpha(Theme.accent, 0.16)
                 : finished ? Theme.alpha(Theme.success, 0.16)
                 : selected ? Theme.alpha(Theme.accent, 0.12)
                 : rowHover.hovered && !queue.busy ? Theme.hover : Theme.alpha(Theme.fg, 0.04)
            border.width: selected || current ? 1 : 0
            border.color: Theme.alpha(Theme.accent, 0.6)

            // staggered entrance
            opacity: 1
            transform: Translate { id: slide; x: 0 }
            Component.onCompleted: {
                if (Theme.calm || Date.now() - queue.revealAt > 1200) return
                opacity = 0; slide.x = 18
                enter.start()
            }
            SequentialAnimation {
                id: enter
                PauseAnimation { duration: Math.min(10, row.index) * Theme.dur(45) }
                ParallelAnimation {
                    NumberAnimation { target: row; property: "opacity"; to: 1; duration: Theme.dur(260); easing.type: Easing.OutCubic }
                    NumberAnimation { target: slide; property: "x"; to: 0; duration: Theme.dur(320); easing.type: Easing.OutCubic }
                }
            }

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 10
                anchors.rightMargin: 10
                spacing: 8
                // ticked for Process Selected / Move to Finished (user, 2026-09-30);
                // its own click target: a click on the rest of the row still
                // loads the clip into the preview
                AnimCheck {
                    id: tick
                    objectName: "jobQueueCheck"
                    checkable: false
                    checked: !!queue.ticks[row.index]
                    enabled: !queue.busy && !queue.moving && row.path !== ""
                    Layout.alignment: Qt.AlignVCenter
                    onClicked: queue.src.setChecked(row.path, !checked)
                    HoverTip { tip: "Tick to include this clip in Process Selected or Move to Finished." }
                }
                // its place in the batch (user, 2026-09-28), before the still;
                // wide enough for the list's biggest number so the stills line up
                Text {
                    objectName: "jobQueueNumber"
                    text: String(row.index + 1)
                    color: row.current ? Theme.fg : Theme.fgMuted
                    font.family: Theme.bodyFont
                    font.pixelSize: Theme.smallSize + 1
                    font.features: { "tnum": 1 }
                    horizontalAlignment: Text.AlignRight
                    Layout.preferredWidth: numberMetrics.width
                    Layout.alignment: Qt.AlignVCenter
                }
                // a still from the clip, in place of the camera icon
                Rectangle {
                    objectName: "jobQueueThumbBox"
                    Layout.preferredWidth: 64
                    Layout.preferredHeight: 36
                    radius: 4
                    color: Theme.alpha(Theme.fg, 0.06)
                    border.width: row.current ? 1 : 0
                    border.color: Theme.accent
                    clip: true
                    Image {
                        objectName: "jobQueueThumb"
                        anchors.fill: parent
                        anchors.margins: row.current ? 1 : 0
                        asynchronous: true           // Qt's loader thread, never the UI thread
                        cache: true
                        fillMode: Image.PreserveAspectCrop
                        sourceSize.height: 72
                        source: row.path ? "image://clipthumb/" + encodeURIComponent(row.path) : ""
                        opacity: status === Image.Ready ? 1 : 0
                        Behavior on opacity { NumberAnimation { duration: Theme.dur(200) } }
                    }
                }
                // the name, its frame count under it (user, 2026-09-27)
                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignVCenter
                    spacing: 0
                    Text {
                        objectName: "jobQueueName"
                        text: queue.shortName(row.modelData)
                        color: Theme.fg
                        font.family: Theme.bodyFont
                        font.pixelSize: Theme.bodySize
                        elide: Text.ElideMiddle
                        Layout.fillWidth: true
                    }
                    Text {
                        id: frames
                        objectName: "jobQueueFrames"
                        readonly property int n: queue.frameCounts.length > row.index ? queue.frameCounts[row.index] : 0
                        // the clip's shape beside its length (user, 2026-10-11)
                        readonly property string shape: queue.ratios.length > row.index ? (queue.ratios[row.index] || "") : ""
                        visible: n > 0
                        text: n + " frames" + (shape ? "  ·  " + shape : "")
                        color: Theme.fgFaint
                        font.family: Theme.bodyFont
                        font.pixelSize: Theme.smallSize
                        font.features: { "tnum": 1 }
                        Layout.fillWidth: true
                    }
                    // the Special FX this clip's sidecar uses (Splatting: jobClips.clipFx), as little badges, on
                    // their own line under the name (user, 2026-10-17); the sidecar's stays by the bars
                    Row {
                        objectName: "jobQueueFxUnder"
                        HoverHandler { id: fxUnderHover }
                        spacing: 5
                        readonly property var fx: row.fx
                        visible: row.fxUnder
                        topPadding: 3
                        Repeater {
                            model: queue.fxOrder.filter(f => f.kind !== "sidecar")
                            FxIcon {
                                required property var modelData
                                kind: modelData.kind
                                objectName: modelData.name
                                visible: !!parent.fx && !!parent.fx[modelData.flag]
                                HoverTip { tip: modelData.label }
                            }
                        }
                    }
                }
                // the sidecar's badge, beside the bars
                Row {
                    objectName: "jobQueueFx"
                    // over a badge, only the badge's tip shows (not the clip name's too)
                    HoverHandler { id: fxHover }
                    spacing: 5
                    readonly property var fx: row.fx
                    visible: !!fx && !!fx[3]
                    Layout.alignment: Qt.AlignVCenter
                    Repeater {
                        model: queue.fxOrder.filter(f => f.kind === "sidecar")
                        FxIcon {
                            required property var modelData
                            kind: modelData.kind
                            objectName: modelData.name
                            visible: !!parent.fx && !!parent.fx[modelData.flag]
                            HoverTip { tip: modelData.label }
                        }
                    }
                }
                ShimmerBar {
                    objectName: "jobQueueBar"
                    visible: queue.passes.length === 0
                    // a set width: the name takes the rest of the row
                    Layout.preferredWidth: queue.barWidth
                    implicitHeight: 6
                    value: queue.progressOf(row.modelData)
                    shimmer: false
                }
                // one bar per pass, its letter before it; a pass the job
                // doesn't run is faded
                ColumnLayout {
                    objectName: "jobQueuePasses"
                    visible: queue.passes.length > 0
                    Layout.preferredWidth: queue.passColumnWidth
                    Layout.alignment: Qt.AlignVCenter
                    spacing: 3
                    Repeater {
                        model: queue.passes
                        RowLayout {
                            id: passRow
                            required property int index
                            required property string modelData
                            objectName: "jobQueuePass"
                            Layout.fillWidth: true
                            spacing: 4
                            opacity: queue.passOn[index] ? 1 : 0.35
                            Text {
                                text: passRow.modelData + ":"
                                color: Theme.fgMuted
                                font.family: Theme.bodyFont
                                font.pixelSize: Theme.smallSize
                                font.weight: Font.DemiBold
                                Layout.preferredWidth: passMetrics.width
                            }
                            ShimmerBar {
                                objectName: "jobQueuePassBar"
                                Layout.preferredWidth: queue.barWidth
                                implicitHeight: 5
                                value: queue.passProgressOf(row.modelData, passRow.index, frames.n)
                                shimmer: false
                            }
                        }
                    }
                }
            }
            HoverHandler { id: rowHover; enabled: !queue.busy && !Theme.modalOpen; cursorShape: queue.pv ? Qt.PointingHandCursor : Qt.ArrowCursor }
            TapHandler {
                enabled: !queue.busy && !!queue.pv && !Theme.modalOpen
                onTapped: queue.pv.showClip(row.path)
            }
            // open during a job too: Play is only a look (the actions wait for the job)
            TapHandler {
                objectName: "jobQueueRowMenuArea"
                acceptedButtons: Qt.RightButton
                enabled: !!queue.src && !queue.moving && row.path !== "" && !Theme.modalOpen
                onTapped: {
                    rowMenu.path = row.path
                    // an action marked whenDone shows on a row this run finished (green)
                    // only (user, 2026-10-11: "Send to <next stage>")
                    rowMenu.actions = queue.rowActions.filter(a => !a.whenDone || row.finished)
                    rowMenu.plays = queue.src.playables(row.path)
                    rowMenu.explores = queue.src.explorables ? queue.src.explorables(row.path) : []
                    if (rowMenu.actions.length || rowMenu.plays.length || rowMenu.explores.length) rowMenu.popup()
                }
            }
            HoverTip {
                tip: fxHover.hovered || fxUnderHover.hovered ? "" : queue.busy ? row.modelData
                     : row.modelData + (queue.pv ? "\nClick to load it into the preview." : "")
                       + (queue.rowActions.length ? (queue.pv ? " Right-click for more." : "\nRight-click for more.") : "")
            }
        }

        Text {
            anchors.centerIn: parent
            visible: list.count === 0
            text: "The clips appear here once the input folders are set."
            color: Theme.fgFaint
            font.family: Theme.bodyFont
            font.pixelSize: Theme.smallSize + 1
        }
    }

    // a row's right-click menu: the page's rowActions, then "Play ..." for each file
    // that goes with the clip (user, 2026-10-10: in the default player), then "Open ... in
    // Explorer" for each, selected there (user, 2026-10-18)
    StudioMenu {
        id: rowMenu
        objectName: "jobQueueRowMenu"
        property string path: ""
        property var actions: []
        property var plays: []
        property var explores: []
        Instantiator {
            model: rowMenu.actions
            delegate: MenuItem {
                required property var modelData
                objectName: "jobQueueAction_" + modelData.id
                text: modelData.text
                enabled: !queue.busy
                onTriggered: queue.rowAction(modelData.id, rowMenu.path)
            }
            onObjectAdded: (i, item) => rowMenu.insertItem(i, item)
            onObjectRemoved: (i, item) => rowMenu.removeItem(item)
        }
        MenuSeparator {
            contentItem: Rectangle { implicitHeight: rowMenu.plays.length && rowMenu.actions.length ? 1 : 0; color: Theme.border }
            padding: rowMenu.plays.length && rowMenu.actions.length ? 6 : 0
        }
        Instantiator {
            model: rowMenu.plays
            delegate: MenuItem {
                required property var modelData
                objectName: "jobQueuePlay"
                text: modelData.text
                onTriggered: {
                    const why = Shell.openFile(modelData.path)
                    if (why) Theme.toast("warning", "Couldn't open it", why)
                }
                HoverTip { tip: modelData.path }
            }
            onObjectAdded: (i, item) => rowMenu.insertItem(rowMenu.actions.length + 1 + i, item)
            onObjectRemoved: (i, item) => rowMenu.removeItem(item)
        }
        MenuSeparator {
            readonly property bool shown: rowMenu.explores.length > 0 && (rowMenu.plays.length + rowMenu.actions.length) > 0
            contentItem: Rectangle { implicitHeight: parent.shown ? 1 : 0; color: Theme.border }
            padding: shown ? 6 : 0
        }
        // last in the menu: appended after the separator above
        Instantiator {
            model: rowMenu.explores
            delegate: MenuItem {
                required property var modelData
                objectName: "jobQueueExplore"
                text: modelData.text
                onTriggered: {
                    const why = Shell.revealFile(modelData.path)
                    if (why) Theme.toast("warning", "Couldn't open Explorer", why)
                }
                HoverTip { tip: modelData.path.replace(/\//g, "\\") }
            }
            onObjectAdded: (i, item) => rowMenu.addItem(item)
            onObjectRemoved: (i, item) => rowMenu.removeItem(item)
        }
    }

    // what the badges on the rows mean (user, 2026-10-03); wraps in a narrow column
    Flow {
        id: legend
        objectName: "jobQueueLegend"
        Layout.fillWidth: true
        Layout.preferredWidth: 0            // wraps in the list's width, as the tick bar does
        Layout.minimumWidth: 0
        Layout.maximumWidth: queue.width - 28
        visible: queue.legendItems.length > 0 && list.count > 0 && queue.listFits
                 && queue.listRoom - implicitHeight - queue.spacing >= queue.minList
        spacing: 12
        Repeater {
            model: queue.legendItems
            Row {
                required property var modelData
                objectName: "jobQueueLegendItem"
                spacing: 5
                FxIcon { kind: parent.modelData.kind; anchors.verticalCenter: parent.verticalCenter }
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: queue.legendText[parent.modelData.kind] || ""
                    color: Theme.fgFaint
                    font.family: Theme.bodyFont
                    font.pixelSize: Theme.smallSize
                }
            }
        }
    }
}
