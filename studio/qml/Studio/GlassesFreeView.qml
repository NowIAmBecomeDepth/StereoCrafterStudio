import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Window
import Studio

// Glasses-Free 3D (user, 2026-10-11; studio/sr_display.py): tells the SR
// display's woven window where the preview's picture is. Nothing here draws.
// The page's own picture (the left eye) stays underneath, so whatever hides the
// woven window -- the card turning over, a popup over the picture, a job
// running -- shows the 2D picture in its place. Put it in the picture's clip area.
Item {
    id: root
    property string stage: ""
    property var pv: null
    property Item picture: null      // the picture item: its visible part is woven
    property Item area: null         // the clip around it
    property bool wanted: false      // the page is showing the source, face up
    // the window's own controls over the picture (Full Preview's X and frame slider; user,
    // 2026-10-13: the woven window hid the X) -- holes while they show
    property var extraHoles: []

    readonly property bool hasHub: typeof Glasses3D !== "undefined" && Glasses3D !== null
    readonly property bool on: hasHub && wanted && Glasses3D.available && !!pv && pv.stereo3d
                               && !!picture && picture.visible && picture.width > 0 && !!area
                               && !!Window.window && Window.window.visibility !== Window.Minimized
                               && Window.window.visibility !== Window.Hidden

    onOnChanged: update()
    // Keep Eye Tracking On: tracking is held while any view wants the source (sr_display.py)
    property string wantKey: ""
    onWantedChanged: if (hasHub && wantKey) Glasses3D.want(wantKey, wanted)
    Component.onCompleted: {
        if (!hasHub) return
        Glasses3D.calm = Theme.calm
        wantKey = stage + "#" + Math.random().toString(36).slice(2)
        Glasses3D.want(wantKey, wanted)
    }
    Component.onDestruction: if (hasHub) { Glasses3D.want(wantKey, false); Glasses3D.releaseFrom(stage, Window.window) }
    // a job running: frozen, no tracking (sr_display.py)
    Connections {
        target: Theme
        function onCalmChanged() { if (root.hasHub) Glasses3D.calm = Theme.calm }
    }

    // Polled: an ancestor moving sends this item no signal. Ten times a second
    // live, twice while a job runs; nothing redraws -- a placement that has not
    // changed is a no-op in Python.
    Timer {
        interval: Theme.calm ? 500 : 100
        running: root.on
        repeat: true
        onTriggered: root.update()
    }

    // The window's rects over the woven one: popups (dropdown lists, tips,
    // dialogs and their dimmer) and the toasts -- each gets a hole, so it shows.
    function holes(x0, y0, x1, y1) {
        const out = []
        const add = (item, h) => {
            if (!item || !item.visible || item.opacity < 0.01 || item.width <= 0) return
            const r = item.mapToItem(null, 0, 0, item.width, h === undefined ? item.height : h)
            if (r.x < x1 && r.y < y1 && r.x + r.width > x0 && r.y + r.height > y0) out.push(r)
        }
        const ov = root.Overlay.overlay
        if (ov) for (let i = 0; i < ov.children.length; ++i) add(ov.children[i])
        for (let i = 0; i < root.extraHoles.length; ++i) add(root.extraHoles[i])
        const th = Theme.toastHost      // (the main window's: not over a Full Preview)
        if (th && th.stackHeight > 0 && th.Window.window === root.Window.window) add(th, th.stackHeight)
        return out
    }

    function update() {
        if (!hasHub) return
        if (!on) { Glasses3D.releaseFrom(stage, Window.window); return }
        const a = area.mapToItem(null, 0, 0, area.width, area.height)
        const p = picture.mapToItem(null, 0, 0, picture.width, picture.height)
        const x0 = Math.max(a.x, p.x), y0 = Math.max(a.y, p.y)
        const x1 = Math.min(a.x + a.width, p.x + p.width), y1 = Math.min(a.y + a.height, p.y + p.height)
        if (x1 - x0 < 8 || y1 - y0 < 8) { Glasses3D.releaseFrom(stage, Window.window); return }
        Glasses3D.place(stage, Window.window, Qt.rect(x0, y0, x1 - x0, y1 - y0),
                        [(x0 - p.x) / p.width, (y0 - p.y) / p.height, (x1 - p.x) / p.width, (y1 - p.y) / p.height],
                        holes(x0, y0, x1, y1))
    }
}
