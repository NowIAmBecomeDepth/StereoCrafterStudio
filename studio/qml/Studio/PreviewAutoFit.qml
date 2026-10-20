import QtQuick
import Studio

// v1's preview auto-fit (core/ui/preview_autofit.py, Merging's and
// Splatting's): Preview Scale follows the room the picture has, so the live
// preview is as large as the window comfortably allows (user, 2026-09-26).
//
// v2 renders the frame at full size and scales it on the GPU, so this only
// picks the percentage: the largest that fits the whole frame inside `area`
// (less a small margin), rounded DOWN to a whole percent and held to 10..100%.
// v1 snapped down to 5% (so a drag would not flicker a step over); here the
// fit only runs once a resize has settled, so 1% steps are safe and the frame
// fills up to ~5% more of the room (user, 2026-09-27). Past 100% there is no
// more detail, only a softer picture. It runs once a resize
// settles, when a clip loads and when the frame's size changes; a Preview
// Scale picked by hand (or the wheel) stands until the next of those.
Item {
    id: fit
    property Item area: null
    property var pv: null
    property var store: null
    // Frame pixels -> item units (the page's pxToItem: 1:1 on the screen).
    property real pxToItem: 1
    property int margin: 8
    // the largest it fits to (Splatting's Fit, user 2026-10-03: a 2.39:1 clip
    // on a 4K screen fills the room at 125%, past the 100% cap)
    property int maxPercent: 100
    // false: a scale picked by hand stands through resizes and clip loads
    property bool active: true
    property int lastW: 0
    property int lastH: 0
    property bool wasReady: false
    width: 0
    height: 0

    function percent() {
        if (!pv || !pv.loaded || pv.imageWidth <= 0 || pv.imageHeight <= 0 || !area) return -1
        const aw = area.width - margin, ah = area.height - margin
        if (aw < 60 || ah < 60) return -1
        const raw = Math.min(aw / (pv.imageWidth * pxToItem), ah / (pv.imageHeight * pxToItem)) * 100
        return Math.max(10, Math.min(maxPercent, Math.floor(raw)))
    }
    function apply() {
        if (!active) return
        const pct = percent()
        if (pct < 0 || !store) return
        const cur = parseFloat(String(store.preview_size))
        if (cur !== pct) store.preview_size = pct + "%"
    }

    Timer { id: settle; interval: 180; onTriggered: fit.apply() }
    Connections {
        target: fit.area
        function onWidthChanged() { settle.restart() }
        function onHeightChanged() { settle.restart() }
    }
    Connections {
        target: fit.pv
        // A clip loaded: refit once its load has finished. The first frame
        // can land before the load does (a fresh engine), and a fit then
        // found "not loaded" and gave up -- the preview stayed at the saved
        // scale until a double-click (user, 2026-09-27).
        function onChanged() {
            const ready = fit.pv.loaded && !fit.pv.loading
            if (ready && !fit.wasReady) settle.restart()
            fit.wasReady = ready
        }
        // Every frame lands here (playback too): refit only when its size changes.
        function onFrameChanged() {
            const w = fit.pv.imageWidth, h = fit.pv.imageHeight
            if (w !== fit.lastW || h !== fit.lastH) {
                fit.lastW = w
                fit.lastH = h
                settle.restart()
            }
        }
    }
}
