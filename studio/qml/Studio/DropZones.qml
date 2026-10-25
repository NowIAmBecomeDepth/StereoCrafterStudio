import QtQuick
import QtQuick.Layouts
import Studio

// Drop Zones (user, 2026-10-25, picked in the Drop Lab): while a folder or a file is dragged over the
// window, the section `over` turns into one dashed target per box, side by side -- its height never
// changes, so more boxes make narrower targets. Each says what it takes; the one under the pointer
// lights up, or turns red with the reason. Letting go sets that box, as its Browse button would.
//
//   DropZones {
//       over: foldersSection
//       store: page.st                    // the settings the boxes write, or a target's own set()
//       targets: [{ key: "input_folder", label: "Input Folder", folder: true, files: ["mp4", "mov"] },
//                 { key: "output_folder", label: "Output Folder", folder: true }]
//   }
//
// A target: key, label, folder (takes a folder), files (the extensions it takes, lower case),
// set(path) in place of store[key] = path, and slash: false to keep Windows' backslashes.
Item {
    id: dz
    property Item over: null
    property var store: null
    property var targets: []
    property bool modal: false           // on a dialog: live only while a dialog is open
    property bool blocked: false         // refuse drops (a job runs on a page whose section stays live)
    property string flashKey: ""         // the box just set, a moment

    parent: over
    anchors.fill: parent
    z: 100
    readonly property bool live: over !== null && over.visible && over.width > 0 && over.height > 0
                                 && (Theme.modalOpen ? modal : !modal)
    readonly property bool idle: over !== null && over.enabled && !blocked
    visible: (FileDrop.active && live) || flashKey !== ""

    Component.onCompleted: FileDrop.register(dz)
    Component.onDestruction: FileDrop.unregister(dz)

    function contains(px, py) {
        const p = mapFromItem(null, px, py)
        return p.x >= 0 && p.y >= 0 && p.x <= width && p.y <= height
    }
    function ok(t) {
        return FileDrop.isDir ? !!t.folder : (t.files || []).indexOf(FileDrop.ext) >= 0
    }
    function why(t) {
        if (!dz.idle) return "Not while a job runs"
        if (FileDrop.isDir) return "Takes a file, not a folder"
        if (!t.folder && !(t.files || []).length) return "Can't take this"
        if (!(t.files || []).length) return "Takes a folder, not a file"
        return "Takes " + (t.folder ? "a folder or " : "") + t.files.map(e => "." + e).join(" / ")
    }
    function hint(t) {
        const f = t.files || []
        if (!f.length) return "a folder"
        return (t.folder ? "a folder or " : "") + (f.length > 3 ? "a clip" : f.map(e => "." + e).join(" / "))
    }
    function indexAt(px, py) {
        for (let i = 0; i < zones.count; ++i) {
            const z = zones.itemAt(i)
            if (!z) continue
            const p = z.mapFromItem(null, px, py)
            if (p.x >= 0 && p.y >= 0 && p.x <= z.width && p.y <= z.height) return i
        }
        return -1
    }
    readonly property int hot: FileDrop.active ? (FileDrop.x, FileDrop.y, indexAt(FileDrop.x, FileDrop.y)) : -1

    function dropAt(px, py) {
        const i = indexAt(px, py)
        if (i < 0) return false
        const t = targets[i]
        if (!dz.idle || !ok(t)) {
            Theme.toast("info", t.label, why(t))
            return true
        }
        const p = t.slash === false ? FileDrop.path : FileDrop.path.replace(/\\/g, "/")
        if (typeof t.set === "function") t.set(p)
        else if (store) store[t.key] = p
        flashKey = t.key
        flashTimer.restart()
        return true
    }
    Timer { id: flashTimer; interval: 700; onTriggered: dz.flashKey = "" }

    Rectangle {
        anchors.fill: parent
        radius: 10
        color: Theme.bg
        RowLayout {
            anchors.fill: parent
            anchors.margins: 6
            spacing: 8
            Repeater {
                id: zones
                model: dz.targets
                Rectangle {
                    id: zone
                    required property var modelData
                    required property int index
                    readonly property bool hot: dz.hot === index
                    readonly property bool good: dz.idle && dz.ok(modelData)
                    readonly property bool flash: dz.flashKey === modelData.key
                    readonly property color ink: flash ? Theme.success : !good ? Theme.danger : hot ? Theme.accent
                                                 : Theme.borderStrong
                    objectName: "dropZone_" + modelData.key
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.preferredWidth: 1
                    radius: 10
                    color: flash ? Theme.alpha(Theme.success, 0.2)
                           : !good ? Theme.alpha(Theme.danger, hot ? 0.18 : 0.05)
                           : Theme.alpha(Theme.accent, hot ? 0.28 : 0.08)
                    opacity: dz.flashKey !== "" && !flash ? 0 : 1
                    Canvas {     // the dashed edge
                        anchors.fill: parent
                        property color ink: zone.ink
                        onInkChanged: requestPaint()
                        onWidthChanged: requestPaint()
                        onHeightChanged: requestPaint()
                        onPaint: {
                            const ctx = getContext("2d"); ctx.reset()
                            ctx.setLineDash([7, 5]); ctx.lineWidth = 2; ctx.strokeStyle = ink
                            ctx.beginPath(); ctx.roundedRect(1, 1, width - 2, height - 2, 10, 10); ctx.stroke()
                        }
                    }
                    ColumnLayout {
                        anchors.centerIn: parent
                        width: parent.width - 16
                        spacing: 2
                        Icon {
                            Layout.alignment: Qt.AlignHCenter
                            visible: parent.parent.height >= 64
                            glyph: zone.flash ? Theme.icon.check
                                   : (zone.modelData.files || []).length && !zone.modelData.folder ? Theme.icon.file
                                   : Theme.icon.folder
                            size: 22
                            color: zone.ink
                        }
                        Text {
                            Layout.fillWidth: true
                            horizontalAlignment: Text.AlignHCenter
                            text: zone.modelData.label
                            color: Theme.fg; font.family: Theme.buttonFont; font.pixelSize: Theme.bodySize + 1
                            font.weight: Font.DemiBold
                            elide: Text.ElideRight
                        }
                        Text {
                            Layout.fillWidth: true
                            horizontalAlignment: Text.AlignHCenter
                            visible: parent.parent.height >= 44
                            text: zone.flash ? "Set" : !zone.good ? dz.why(zone.modelData) : dz.hint(zone.modelData)
                            color: zone.flash ? Theme.success : !zone.good ? Theme.danger : Theme.fgMuted
                            font.family: Theme.bodyFont; font.pixelSize: Theme.smallSize + 1
                            elide: Text.ElideRight
                        }
                    }
                }
            }
        }
    }
}
