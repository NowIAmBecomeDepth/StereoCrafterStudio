import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import Studio

// The shaper (user, 2026-10-06): how the values travel across the span the
// playhead is in -- from the keyframe it leaves to the DYNAMIC keyframe it
// arrives at (core.common.keyframes). The two keyframes are the two ends of
// the swing: a straight ramp, an ease, an LFO (Cycles: full swings back and
// forth before it lands on the second), or Custom -- in-between points placed
// here, each like a dynamic keyframe inside the span.
//
//   click the line or the graph   a new point (a preset becomes Custom from its own shape)
//   drag a point                  moves it (between its neighbours)
//   right-click / double-click    removes a point
//   hover a stretch of the line   its BEND handle (orange): drag up / down for how
//                                 strong the bend is, left / right for where it sits;
//                                 double-click it to straighten the stretch
//
// The curve is drawn from the controller's samples of the same function the
// preview and the render use; it redraws only when something changes.
ColumnLayout {
    id: sh
    objectName: "keyShaper"
    // what owns the keys: curveShapes, setKeyCurve, curveSamples, bakeCurve --
    // Splatting's preview, or the Depth Map Editor (its selected layer's keys)
    property var pv
    property var keyframes: pv ? pv.keyframes : []
    property int frame: pv ? pv.frame : 0
    spacing: 6

    // the span: the dynamic keyframe the playhead's frames arrive at, and the one before it
    readonly property var seg: {
        const k = keyframes || []
        const f = frame
        let arr = -1
        for (let i = 0; i < k.length; ++i)
            if (k[i].frame > f || (k[i].frame === f && k[i].kind === "dynamic")) { arr = i; break }
        if (arr <= 0 || k[arr].kind !== "dynamic")
            return ({ ok: false })
        return ({ ok: true, from: k[arr - 1].frame, to: k[arr].frame, curve: k[arr].curve || null })
    }
    readonly property var curve: seg.ok ? (seg.curve || ({ shape: "linear", cycles: 1 })) : ({ shape: "linear" })
    readonly property var shapes: pv ? pv.curveShapes : []
    readonly property bool lfo: ["sine", "triangle", "square", "saw", "random"].indexOf(curve.shape) >= 0
    readonly property bool custom: curve.shape === "custom"

    // the curve as points + bends: its own when Custom, else its shape baked (Linear: none)
    readonly property var basePts: !seg.ok ? [] : custom ? (curve.points || []).map(p => [p[0], p[1]])
                                   : curve.shape === "linear" ? [] : pv.bakeCurve(curve, seg.to)
    readonly property var baseBends: {
        const n = basePts.length + 1
        const b = custom ? (curve.bends || []) : []
        const out = []
        for (let i = 0; i < n; ++i) out.push(b[i] ? [b[i][0], b[i][1]] : null)
        return out
    }
    // while a drag is under way (committed on release)
    property var dragPts: null
    property var dragBends: null
    readonly property var pts: dragPts !== null ? dragPts : basePts
    readonly property var bends: dragBends !== null ? dragBends : baseBends
    readonly property var shownPts: (custom || dragPts !== null) ? pts : []      // presets show their line only
    readonly property var knots: [[0, 0]].concat(pts).concat([[1, 1]])
    property int hoverSeg: -1                                                    // the stretch whose handle shows
    readonly property real playT: seg.ok && seg.to > seg.from ? Math.max(0, Math.min(1, (frame - seg.from) / (seg.to - seg.from))) : -1

    function commit(c) { if (seg.ok) pv.setKeyCurve(seg.to, c) }
    function commitCustom(p, b) {
        const anyBend = b.some(x => x !== null)
        commit(anyBend ? { shape: "custom", points: p, bends: b } : { shape: "custom", points: p })
    }
    function copyPts() { return pts.map(p => [p[0], p[1]]) }
    function copyBends() { return bends.map(b => b ? [b[0], b[1]] : null) }
    // the bend handle of stretch i: where on (or off) the line it sits
    function handle(i) {
        const k0 = knots[i], k1 = knots[i + 1]
        const b = bends[i] || [0.5, 0]
        return [k0[0] + b[0] * (k1[0] - k0[0]), k0[1] + b[0] * (k1[1] - k0[1]) + b[1]]
    }
    function segAt(t) {
        for (let i = 0; i < knots.length - 1; ++i) if (t <= knots[i + 1][0]) return i
        return knots.length - 2
    }

    Text {
        visible: !sh.seg.ok
        text: "Shaper: put the playhead in a span that ends on a dynamic keyframe."
        color: Theme.fgMuted
        font.family: Theme.bodyFont
        font.pixelSize: Theme.smallSize + 1
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    }
    RowLayout {
        visible: sh.seg.ok
        spacing: 6
        Text { text: "Shape"; color: Theme.fg; font.family: Theme.bodyFont; font.pixelSize: Theme.smallSize + 1 }
        Dropdown {
            objectName: "keyShape"
            boxWidth: 108
            model: sh.shapes.map(s => s.label)
            currentIndex: Math.max(0, sh.shapes.findIndex(s => s.key === sh.curve.shape))
            onActivated: (i) => {
                const key = sh.shapes[i].key
                if (key === "custom")
                    sh.commitCustom(sh.copyPts(), sh.copyBends())
                else
                    sh.commit({ shape: key, cycles: sh.curve.cycles !== undefined ? sh.curve.cycles : 1 })
            }
            HoverTip { tip: "How the values travel from the keyframe before to this dynamic one: a straight ramp, an ease, an LFO swinging between the two looks, or Custom points. Every shape lands on this keyframe." }
        }
        Text { visible: sh.lfo; text: "Cycles"; color: Theme.fg; font.family: Theme.bodyFont; font.pixelSize: Theme.smallSize + 1 }
        TextField {
            id: cyc
            objectName: "keyCycles"
            visible: sh.lfo
            readonly property string shownCycles: String(sh.curve.cycles !== undefined ? sh.curve.cycles : 1)
            implicitWidth: 44
            implicitHeight: Theme.rowHeight
            text: shownCycles
            color: Theme.fg
            font.family: Theme.bodyFont
            font.pixelSize: Theme.bodySize
            horizontalAlignment: TextInput.AlignHCenter
            validator: IntValidator { bottom: 0; top: 64 }
            background: Rectangle { radius: 6; color: Theme.field; border.width: 1; border.color: cyc.activeFocus ? Theme.accent : Theme.border }
            onEditingFinished: if (text !== shownCycles) sh.commit({ shape: sh.curve.shape, cycles: Number(text) })
            onShownCyclesChanged: text = shownCycles
            HoverTip { tip: "Full swings back and forth between the two keyframes before it lands on this one (0: a single sweep)." }
        }
        Item { Layout.fillWidth: true }
    }
    Canvas {
        id: graph
        objectName: "keyShaperGraph"
        visible: sh.seg.ok
        Layout.fillWidth: true
        Layout.preferredHeight: 96
        readonly property real pad: 6
        readonly property color handleColour: "#ffb347"
        readonly property var samples: !sh.seg.ok ? []
            : sh.pv.curveSamples((sh.dragPts !== null) ? { shape: "custom", points: sh.dragPts, bends: sh.dragBends }
                                                       : sh.curve, sh.seg.to, 120)
        function px(t) { return pad + t * (width - 2 * pad) }
        function py(v) { return height - pad - v * (height - 2 * pad) }
        onSamplesChanged: requestPaint()
        onWidthChanged: requestPaint()
        Connections { target: sh; function onPlayTChanged() { graph.requestPaint() } function onHoverSegChanged() { graph.requestPaint() } }
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            ctx.fillStyle = Theme.field
            ctx.fillRect(0, 0, width, height)
            ctx.strokeStyle = Theme.border
            ctx.lineWidth = 1
            for (const v of [0, 0.5, 1]) { ctx.beginPath(); ctx.moveTo(pad, py(v)); ctx.lineTo(width - pad, py(v)); ctx.stroke() }
            if (sh.playT >= 0) {
                ctx.strokeStyle = Theme.fgMuted
                ctx.beginPath(); ctx.moveTo(px(sh.playT), pad); ctx.lineTo(px(sh.playT), height - pad); ctx.stroke()
            }
            const s = samples
            if (s.length > 1) {
                ctx.strokeStyle = Theme.accent
                ctx.lineWidth = 2
                ctx.beginPath()
                for (let i = 0; i < s.length; ++i) {
                    const x = px(i / (s.length - 1)), y = py(s[i])
                    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y)
                }
                ctx.stroke()
            }
            ctx.fillStyle = Theme.accent
            for (const p of sh.shownPts) { ctx.beginPath(); ctx.arc(px(p[0]), py(p[1]), 4, 0, 2 * Math.PI); ctx.fill() }
            // bend handles: the bent stretches' faintly, the hovered (or dragged) one solid
            ctx.lineWidth = 1.5
            for (let i = 0; i < sh.knots.length - 1; ++i) {
                const bent = !!sh.bends[i]
                if (i !== sh.hoverSeg && !bent) continue
                const h = sh.handle(i)
                ctx.beginPath(); ctx.arc(px(h[0]), py(Math.max(0, Math.min(1, h[1]))), i === sh.hoverSeg ? 5 : 3.5, 0, 2 * Math.PI)
                if (i === sh.hoverSeg) { ctx.fillStyle = handleColour; ctx.fill() }
                else { ctx.strokeStyle = handleColour; ctx.stroke() }
            }
            ctx.fillStyle = Theme.fgMuted
            ctx.font = "10px sans-serif"
            ctx.fillText(String(sh.seg.from + 1), pad + 2, height - pad - 3)
            ctx.fillText(String(sh.seg.to + 1), width - pad - 26, pad + 10)
        }
        MouseArea {
            id: ma
            anchors.fill: parent
            hoverEnabled: true
            acceptedButtons: Qt.LeftButton | Qt.RightButton
            preventStealing: true           // dragging a point is never taken for a scroll
            property int dragIdx: -1        // a point being dragged
            property int bendIdx: -1        // a stretch whose bend is being dragged
            function tAt(x) { return Math.max(0.001, Math.min(0.999, (x - graph.pad) / (graph.width - 2 * graph.pad))) }
            function vAt(y) { return (graph.height - graph.pad - y) / (graph.height - 2 * graph.pad) }
            function pointAt(x, y) {
                const p = sh.shownPts
                for (let i = 0; i < p.length; ++i)
                    if (Math.hypot(graph.px(p[i][0]) - x, graph.py(p[i][1]) - y) <= 8) return i
                return -1
            }
            function handleAt(x, y) {
                for (let i = 0; i < sh.knots.length - 1; ++i) {
                    if (i !== sh.hoverSeg && !sh.bends[i]) continue
                    const h = sh.handle(i)
                    if (Math.hypot(graph.px(h[0]) - x, graph.py(Math.max(0, Math.min(1, h[1]))) - y) <= 9) return i
                }
                return -1
            }
            // the stretch under the mouse when it is on (near) the line, else -1
            function lineAt(x, y) {
                const s = graph.samples
                if (s.length < 2) return -1
                const t = tAt(x)
                const v = s[Math.round(t * (s.length - 1))]
                return Math.abs(graph.py(v) - y) <= 10 ? sh.segAt(t) : -1
            }
            function removePoint(i) {
                const p = sh.copyPts(), b = sh.copyBends()
                p.splice(i, 1)
                b.splice(i, 2, null)                       // its two stretches become one, straight
                sh.commitCustom(p, b)
            }
            onPositionChanged: (m) => {
                if (bendIdx >= 0) {
                    const k0 = sh.knots[bendIdx], k1 = sh.knots[bendIdx + 1]
                    const a = Math.max(0.25, Math.min(0.75, (tAt(m.x) - k0[0]) / Math.max(1e-6, k1[0] - k0[0])))
                    // as far as keeps the curve inside the two keyframes' looks (keyframes.bend_limits)
                    const base = 2 * (k0[1] + a * (k1[1] - k0[1])) - (k0[1] + k1[1]) / 2
                    const bv = Math.max(-base / 2, Math.min((1 - base) / 2, vAt(m.y) - (k0[1] + a * (k1[1] - k0[1]))))
                    const b = sh.dragBends.map(x => x ? [x[0], x[1]] : null)
                    b[bendIdx] = Math.abs(bv) < 0.005 ? null : [a, bv]
                    sh.dragBends = b
                    return
                }
                if (dragIdx >= 0 && sh.dragPts !== null) {
                    const p = sh.dragPts.map(q => [q[0], q[1]])
                    const lo = (dragIdx > 0 ? p[dragIdx - 1][0] : 0) + 0.002
                    const hi = (dragIdx < p.length - 1 ? p[dragIdx + 1][0] : 1) - 0.002
                    p[dragIdx] = [Math.max(lo, Math.min(hi, tAt(m.x))), Math.max(0, Math.min(1, vAt(m.y)))]
                    sh.dragPts = p
                    return
                }
                if (!pressed) {
                    const h = handleAt(m.x, m.y)
                    sh.hoverSeg = h >= 0 ? h : (pointAt(m.x, m.y) >= 0 ? -1 : lineAt(m.x, m.y))
                }
            }
            onExited: if (bendIdx < 0 && dragIdx < 0) sh.hoverSeg = -1
            onPressed: (m) => {
                const pi = pointAt(m.x, m.y)
                if (m.button === Qt.RightButton) { if (pi >= 0) removePoint(pi); return }
                const hi = handleAt(m.x, m.y)
                if (hi >= 0) {                              // a bend: from the handle
                    bendIdx = hi
                    sh.hoverSeg = hi
                    sh.dragPts = sh.copyPts()
                    sh.dragBends = sh.copyBends()
                    return
                }
                if (pi >= 0) {
                    dragIdx = pi
                    sh.dragPts = sh.copyPts()
                    sh.dragBends = sh.copyBends()
                    return
                }
                // a new point: its stretch splits in two, both straight
                const t = tAt(m.x)
                const p = sh.copyPts(), b = sh.copyBends()
                const i = sh.segAt(t)
                p.splice(i, 0, [t, Math.max(0, Math.min(1, vAt(m.y)))])
                b.splice(i, 1, null, null)
                dragIdx = i
                sh.dragPts = p
                sh.dragBends = b
            }
            onReleased: {
                if (sh.dragPts !== null) {
                    const p = sh.dragPts, b = sh.dragBends
                    sh.dragPts = null
                    sh.dragBends = null
                    dragIdx = -1
                    bendIdx = -1
                    sh.commitCustom(p, b)
                }
            }
            onDoubleClicked: (m) => {
                // the double-click's own press began a drag; its release (after this) must not save it
                sh.dragPts = null
                sh.dragBends = null
                dragIdx = -1
                bendIdx = -1
                const hi = handleAt(m.x, m.y)
                if (hi >= 0 && sh.bends[hi]) {               // straighten the stretch
                    const b = sh.copyBends(); b[hi] = null
                    sh.commitCustom(sh.copyPts(), b)
                    return
                }
                const pi = pointAt(m.x, m.y)
                if (pi >= 0 && sh.custom) removePoint(pi)
            }
        }
        HoverTip { tip: "Bottom: the keyframe before. Top: this dynamic keyframe. The line is the playhead. Click to add a point, drag to move it, right-click or double-click to remove it. Hover a stretch of the line for its orange bend handle: drag it up or down for how strong the bend is, left or right for where it sits; double-click it to straighten." }
    }
    Text {
        visible: sh.seg.ok
        text: "Frames " + (sh.seg.from + 1) + " – " + (sh.seg.to + 1)
              + (sh.custom ? "  ·  " + sh.pts.length + (sh.pts.length === 1 ? " point" : " points")
                 + (sh.bends.some(b => b !== null) ? ", " + sh.bends.filter(b => b !== null).length + " bent" : "") : "")
        color: Theme.fgMuted
        font.family: Theme.bodyFont
        font.pixelSize: Theme.smallSize + 1
    }
}
