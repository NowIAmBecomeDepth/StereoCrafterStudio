import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Effects
import Studio

// The workhorse button. Four things move:
//   * scale      -- springs up on hover, squashes on press, overshoots back
//   * glow       -- a coloured drop shadow that blooms on hover (filled kinds)
//   * ripple     -- expands from the exact press point, masked to the corners
//   * colour     -- fills and outlines cross-fade instead of snapping
AbstractButton {
    id: control
    property string kind: "filled"      // filled | tonal | outline | ghost | danger
    property string iconGlyph: ""
    property color tint: kind === "danger" ? Theme.danger : Theme.accent
    readonly property bool solid: kind === "filled" || kind === "danger"

    implicitHeight: 30
    implicitWidth: Math.max(80, content.implicitWidth + 30)
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    // v1 app_fonts: buttons are Cal Sans, caps, 13, never bold (one heavy weight).
    font.family: Theme.buttonFont
    font.pixelSize: Theme.buttonSize
    opacity: enabled ? 1 : Theme.dim(0.4)

    readonly property color labelColor: {
        if (kind === "filled") return Theme.accentFg
        if (kind === "danger") return "#ffffff"
        if (kind === "ghost") return Theme.fg
        return Theme.accentInk
    }

    scale: pressed ? 0.94 : (hovered ? 1.03 : 1.0)
    Behavior on scale { enabled: !Theme.calm; SpringAnimation { spring: 5; damping: 0.26; epsilon: 0.0005 } }

    background: Item {
        implicitHeight: 30

        Rectangle {
            id: bg
            anchors.fill: parent
            radius: 10
            color: {
                if (control.solid) return control.tint
                if (control.kind === "tonal") return Theme.alpha(control.tint, control.hovered ? 0.26 : 0.16)
                return control.hovered ? Theme.alpha(control.tint, 0.10) : "transparent"
            }
            border.width: control.kind === "outline" ? 1.5 : (control.visualFocus ? 2 : 0)
            border.color: control.visualFocus ? Theme.fg : (control.hovered ? control.tint : Theme.alpha(control.tint, 0.55))
            Behavior on color { ColorAnimation { duration: Theme.dur(180) } }
            Behavior on border.color { ColorAnimation { duration: Theme.dur(180) } }

            // Brighten solid buttons on hover, without recomputing the tint.
            Rectangle {
                anchors.fill: parent
                radius: parent.radius
                color: "#ffffff"
                opacity: control.solid && control.hovered && !control.pressed ? 0.13 : 0
                Behavior on opacity { NumberAnimation { duration: Theme.dur(160) } }
            }

            layer.enabled: control.solid
            layer.effect: MultiEffect {
                shadowEnabled: true
                shadowColor: control.tint
                shadowBlur: control.hovered ? 1.0 : 0.45
                shadowOpacity: control.hovered ? 0.85 : 0.35
                shadowVerticalOffset: control.hovered ? 5 : 2
                shadowScale: control.hovered ? 1.02 : 0.98
                Behavior on shadowBlur { NumberAnimation { duration: Theme.dur(260); easing.type: Easing.OutCubic } }
                Behavior on shadowOpacity { NumberAnimation { duration: Theme.dur(260) } }
                Behavior on shadowVerticalOffset { NumberAnimation { duration: Theme.dur(260); easing.type: Easing.OutCubic } }
                Behavior on shadowScale { NumberAnimation { duration: Theme.dur(260) } }
            }
        }

        // Ripple, clipped to the rounded corners by an alpha mask. Its two
        // offscreen layers and the mask effect exist only while it plays
        // (Studio audit 2026-09-28, PERF: ~140 buttons each kept them for good,
        // built at startup, for half a second of ripple after a press).
        Item {
            id: rippleLayer
            anchors.fill: parent
            visible: false
            layer.enabled: rippleAnim.running
            Rectangle {
                id: ripple
                property real cx: 0
                property real cy: 0
                x: cx - width / 2
                y: cy - height / 2
                width: 0
                height: width
                radius: width / 2
                color: control.solid ? "#ffffff" : control.tint
                opacity: 0
            }
        }
        Rectangle {
            id: rippleMask
            anchors.fill: parent
            radius: bg.radius
            visible: false
            layer.enabled: rippleAnim.running
        }
        Loader {
            anchors.fill: parent
            active: rippleAnim.running
            sourceComponent: MultiEffect {
                source: rippleLayer
                maskEnabled: true
                maskSource: rippleMask
                maskThresholdMin: 0.5
                maskSpreadAtMin: 1.0
            }
        }
    }

    ParallelAnimation {
        id: rippleAnim
        NumberAnimation {
            target: ripple; property: "width"; from: 0
            to: Math.hypot(control.width, control.height) * 2.1
            duration: Theme.dur(560); easing.type: Easing.OutCubic
        }
        NumberAnimation {
            target: ripple; property: "opacity"; from: 0.38; to: 0
            duration: Theme.dur(640); easing.type: Easing.InQuad
        }
    }
    onPressed: {
        ripple.cx = pressX
        ripple.cy = pressY
        rippleAnim.restart()
    }

    contentItem: Item {
        implicitWidth: content.implicitWidth
        implicitHeight: content.implicitHeight
        Row {
            id: content
            anchors.centerIn: parent
            spacing: 8
            Icon {
                glyph: control.iconGlyph
                visible: glyph.length > 0
                size: 15
                color: control.labelColor
                anchors.verticalCenter: parent.verticalCenter
            }
            Text {
                text: Theme.buttonText(control.text)
                font: control.font
                color: control.labelColor
                anchors.verticalCenter: parent.verticalCenter
                Behavior on color { ColorAnimation { duration: Theme.dur(180) } }
            }
        }
    }
}
