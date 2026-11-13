import QtQuick
import Studio

// A project folder field holding ANOTHER project's folder (2026-11-12: a tester's
// stages kept a different project's folders after a switch): a small amber tag
// at the field's right end, "In <Other>"; a click puts the stage back on the open
// project's folders (its controller's useProjectFolders). TextBox makes it, only
// for the fields the stage's chapter picker fills (Shell.projectFieldKeys).
//
// Looked up when the field's value changes, the project changes or is refreshed,
// or the field comes into view -- never on a timer. Hidden it takes no room.
Rectangle {
    id: tag
    objectName: "foreignFolderTag"
    property string path: ""
    property var owner: null
    // the field is on screen: looked up again when it comes into view
    property bool live: true
    property string other: ""
    // what a click does, when it isn't "use the project's folders" (the Depth Map Editor's: a loaded clip stays)
    property string clickTip: ""

    function look() {
        other = live && path.trim() !== "" && typeof Shell !== "undefined" ? Shell.foreignProject(path) : ""
    }
    onPathChanged: look()
    onLiveChanged: if (live) look()
    Component.onCompleted: look()
    Connections {
        target: typeof Shell !== "undefined" ? Shell : null
        function onProjectChanged() { tag.look() }
        function onProjectRefreshed() { tag.look() }
    }

    visible: other !== ""
    width: Math.min(label.implicitWidth + 16, 170)
    height: 20
    radius: 10
    color: hover.hovered ? Theme.alpha(Theme.warning, 0.26) : Theme.alpha(Theme.warning, 0.16)
    border.width: 1
    border.color: Theme.alpha(Theme.warning, 0.7)
    Behavior on color { ColorAnimation { duration: Theme.dur(120) } }

    Text {
        id: label
        anchors.centerIn: parent
        width: Math.min(implicitWidth, tag.width - 12)
        text: "In " + tag.other
        elide: Text.ElideRight
        color: Theme.warning
        font.family: Theme.bodyFont
        font.pixelSize: Theme.smallSize
        font.weight: Font.DemiBold
    }
    HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
    TapHandler {
        onTapped: if (tag.owner && tag.owner.useProjectFolders) tag.owner.useProjectFolders()
    }
    HoverTip {
        tip: tag.visible ? "This folder belongs to " + tag.other + ", not " + Shell.projectName + ".\n\n"
                           + (tag.clickTip !== "" ? tag.clickTip : "Click to use " + Shell.projectName + "'s folders.") : ""
    }
}
