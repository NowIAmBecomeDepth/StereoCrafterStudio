pragma Singleton
import QtQuick
import Studio

// A folder or a file dragged onto Studio from Explorer (user, 2026-10-25: drag and drop onto the input
// and output boxes, the Drop Lab's "Drop Zones"). Main's DropArea feeds it; each DropZones over a page's
// folders registers here, shows its targets while a drag is on, and takes the drop when it lands on it.
QtObject {
    id: fd
    property bool active: false          // a drag is over the window
    property string path: ""             // what is dragged: its path,
    property bool isDir: false           // ...whether it is a folder,
    property string ext: ""              // ...and a file's extension, lower case, no dot
    property real x: -1                  // the pointer, in the window
    property real y: -1
    property var groups: []              // the DropZones that can take it

    function register(g) { groups = groups.concat([g]) }
    function unregister(g) { groups = groups.filter(o => o !== g) }

    function begin(info, px, py) {
        path = String(info.path || "")
        isDir = !!info.dir
        ext = String(info.ext || "")
        x = px; y = py
        active = path !== ""
    }
    function move(px, py) { x = px; y = py }
    function end() { active = false }

    // the DropZones under the pointer that can take a drop now (a dialog's while one is open)
    function groupAt(px, py) {
        for (let i = groups.length - 1; i >= 0; --i) {
            const g = groups[i]
            if (g && g.live && g.contains(px, py))
                return g
        }
        return null
    }
    // let go at (px, py): true when a DropZones took it (set its box, or said why not)
    function drop(px, py) {
        const g = groupAt(px, py)
        active = false
        return g ? g.dropAt(px, py) : false
    }
}
