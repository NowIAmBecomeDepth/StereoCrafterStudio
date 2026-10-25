import QtQuick
import Studio

// Takes a folder or a file dragged from Explorer and hands it to FileDrop, whose
// DropZones show where it can go (user, 2026-10-25). One fills the window; a modal
// dialog with Drop Zones of its own needs one too -- the window's gets no drags
// while a modal popup is open.
DropArea {
    id: area
    keys: ["text/uri-list"]
    onEntered: (d) => {
        const info = d.hasUrls && d.urls.length ? Shell.dropInfo(d.urls[0]) : ({ path: "" })
        const p = area.mapToItem(null, d.x, d.y)
        FileDrop.begin(info, p.x, p.y)
        d.accepted = FileDrop.active
    }
    onPositionChanged: (d) => { const p = area.mapToItem(null, d.x, d.y); FileDrop.move(p.x, p.y) }
    onExited: FileDrop.end()
    onDropped: (d) => {
        const p = area.mapToItem(null, d.x, d.y)
        if (FileDrop.drop(p.x, p.y)) d.acceptProposedAction()
    }
}
