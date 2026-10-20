import QtQuick
import Studio

// A label that never just swaps its text: the old line slides up and fades
// while the new one rises into place. Good for status lines that change
// several times a second.
Item {
    id: root
    property string text: ""
    property color color: Theme.fg
    property int pixelSize: 14
    property string family: Theme.bodyFont
    property int weight: Font.Normal

    implicitWidth: Math.max(a.implicitWidth, b.implicitWidth)
    implicitHeight: Math.max(a.implicitHeight, b.implicitHeight)
    clip: false

    property bool showingA: true
    Component.onCompleted: a.text = text

    onTextChanged: {
        const incoming = showingA ? b : a
        const outgoing = showingA ? a : b
        incoming.text = text
        showingA = !showingA
        swap.stop()
        outAnimY.target = outgoing; outAnimO.target = outgoing
        inAnimY.target = incoming; inAnimO.target = incoming
        swap.start()
    }

    ParallelAnimation {
        id: swap
        NumberAnimation { id: outAnimY; property: "y"; from: 0; to: -root.pixelSize * 0.9; duration: Theme.dur(260); easing.type: Easing.InCubic }
        NumberAnimation { id: outAnimO; property: "opacity"; to: 0; duration: Theme.dur(220) }
        NumberAnimation { id: inAnimY; property: "y"; from: root.pixelSize * 0.9; to: 0; duration: Theme.dur(380); easing.type: Easing.OutBack }
        NumberAnimation { id: inAnimO; property: "opacity"; from: 0; to: 1; duration: Theme.dur(300) }
    }

    Text {
        id: a
        color: root.color
        font.family: root.family; font.pixelSize: root.pixelSize; font.weight: root.weight
    }
    Text {
        id: b
        opacity: 0
        color: root.color
        font.family: root.family; font.pixelSize: root.pixelSize; font.weight: root.weight
    }
}
