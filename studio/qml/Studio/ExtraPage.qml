import QtQuick
import QtQuick.Layouts
import Studio

// Extra, v2: v1's Bonus tab (unified_gui/tabs/bonus_tab.py, titled "Extra"),
// the tools that are not pipeline steps, on a sub-tab strip in pipeline
// order -- Scene Cut runs before DepthCrafter, the Depth Map Editor after it, Edge Fill
// on the merged clips (user, 2026-10-14).
Item {
    id: page
    property bool active: false
    property real flipAngle: 0
    property var prompt: null
    property var consoleRef: null
    property int tool: 0                 // 0 = Scene Cut, 1 = Depth Map Editor, 2 = Edge Fill
    // the tool last used, as the tab is (Shell.lastExtraTool, user 2026-10-11)
    Component.onCompleted: tool = Math.max(0, Math.min(2, Shell.lastExtraTool))
    onToolChanged: Shell.lastExtraTool = tool

    readonly property Item current: tool === 0 ? sceneCut : tool === 1 ? depthEdit : edgeFill
    readonly property int requiredWidth: current.requiredWidth || 860
    readonly property real requiredHeight: toolSwitch.height + (current.requiredHeight || 0)
    readonly property real preferredHeight: toolSwitch.height + (current.preferredHeight || current.requiredHeight || 0)
    readonly property var stageMenus: current.stageMenus || []

    transform: Rotation {
        origin.x: page.width / 2; origin.y: page.height / 2
        axis { x: 0; y: 1; z: 0 }
        angle: page.flipAngle
    }

    StageTabBar {
        id: toolSwitch
        objectName: "extraSwitch"
        x: 14
        y: 0
        implicitHeight: 34
        numbered: false
        model: ["Scene Cut", "Depth Map Editor", "Edge Fill"]
        currentIndex: page.tool
        onActivated: (i) => page.tool = i
    }

    SceneCutPage {
        id: sceneCut
        objectName: "sceneCutPage"
        y: toolSwitch.y + toolSwitch.height
        width: page.width
        height: page.height - y
        prompt: page.prompt
        consoleRef: page.consoleRef
        active: page.active && page.tool === 0
        opacity: page.tool === 0 ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.dur(260) } }
    }
    DepthEditPage {
        id: depthEdit
        objectName: "depthEditPage"
        y: toolSwitch.y + toolSwitch.height
        width: page.width
        height: page.height - y
        prompt: page.prompt
        active: page.active && page.tool === 1
        opacity: page.tool === 1 ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.dur(260) } }
    }
    EdgeFillPage {
        id: edgeFill
        objectName: "edgeFillPage"
        y: toolSwitch.y + toolSwitch.height
        width: page.width
        height: page.height - y
        prompt: page.prompt
        consoleRef: page.consoleRef
        active: page.active && page.tool === 2
        opacity: page.tool === 2 ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.dur(260) } }
    }
}
