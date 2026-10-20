import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Shapes
import Studio

// A compact rotary take on SliderRow, with the same interface (store / key,
// from / to / step / decimals, defaultValue, help, pressed, moved, released)
// so a page can swap one for the other. The look is the showcase's RingDial
// (prototypes/qt_showcase): a 270 degree arc filled up to the knob, the value
// in the middle, the label under it. Drag round it, one step per wheel notch
// or arrow key; right-click or double-click resets to the default (a tick on
// the arc).
//
// Dials sit side by side where sliders take a row each: that is the height
// they save (Merging's mask parameters, user 2026-09-26).
Item {
    id: root
    // The wheel turns this only while the page it sits on doesn't scroll, or
    // once it's been clicked (bug sweep 2026-10-09, A10): on a page taller
    // than the window, scrolling past it moved the value instead. Found once:
    // the main window's page scroller ("stageScroll") among its parents.
    property Item pageScroller: null
    readonly property bool wheelOk: !(pageScroller && pageScroller.scrollable)
    function findPageScroller() {
        for (var p = parent; p; p = p.parent)
            if (p.objectName === "stageScroll") return p
        return null
    }
    Component.onCompleted: pageScroller = findPageScroller()
    property var store: null
    property string key: ""
    property string label: ""
    property string help: ""
    property real from: 0
    property real to: 1
    property real step: 0.01
    property int decimals: 2
    property real defaultValue: NaN
    property int size: 64
    // Where the filled arc starts: NaN = the low end. A bipolar setting
    // (Shift -30..30, Bias -1..1) fills from its middle value outwards.
    property real fillFrom: NaN
    // Inline: the label to the left of a smaller dial, no caption under it
    // -- for a toolbar row (Splatting's Preview Brightness).
    property bool inline: false
    property int labelWidth: 0
    property int valueSize: Theme.bodySize
    // Caption lines kept free under the dial (a row of dials stays level).
    property int captionLines: 2
    // What the middle says, when the owner formats it itself ("+10%", "-2.5 px");
    // empty: the value to `decimals` places.
    property string valueText: ""
    // The arc and the knob scale with the dial: a small one is not all ring.
    readonly property real stroke: Math.max(3, Math.round(size / 13))
    readonly property real knobSize: Math.max(8, Math.round(size / 6))
    readonly property alias pressed: dial.pressed
    signal moved(real value)
    // false: a turn only reports moved() -- a keyframed Splatting dial, whose keyframe takes the
    // value (it wrote the clip's base value too; bug sweep 2026-10-14c)
    property bool writesStore: true
    // on a dark floating bar whatever the theme (Full Preview's dials, user 2026-10-14): light
    // text and track
    property bool onDark: false
    signal released(real value)

    readonly property var stored: store && key ? store[key] : undefined
    // What the knob shows in place of the stored value, when set: Splatting's keyframed
    // value at the frame on screen (user, 2026-10-11). Turning it still writes the setting.
    property var shown: undefined
    readonly property var raw: shown !== undefined ? shown : stored
    readonly property real value: {
        const v = Number(raw)
        return isNaN(v) ? from : Math.max(from, Math.min(to, v))
    }

    implicitWidth: inline ? (labelWidth > 0 ? labelWidth : inlineLabel.implicitWidth) + 8 + size : Math.max(size, 72)
    // Room for two caption lines, whatever this label needs: a row of dials
    // stays level.
    implicitHeight: inline ? size : size + 2 + Math.ceil(captionLines * Theme.smallSize * 1.3)

    Text {
        id: inlineLabel
        visible: root.inline
        anchors.verticalCenter: dial.verticalCenter
        width: root.labelWidth > 0 ? root.labelWidth : implicitWidth
        text: root.label
        color: root.enabled ? Theme.fg : Theme.fgFaint
        font.family: Theme.bodyFont
        font.pixelSize: Theme.bodySize
        elide: Text.ElideRight
    }

    // SliderRow.write: the setting keeps the type it already holds.
    function reset() {
        if (isNaN(defaultValue)) return
        write(defaultValue)
        released(defaultValue)
    }
    function write(v) {
        if (!store || !key) return
        v = Math.max(from, Math.min(to, Math.round((v - from) / step) * step + from))
        const text = Number(v.toFixed(Math.max(decimals, 6))).toString()
        if (typeof stored === "string")
            store[key] = text.indexOf(".") < 0 && text.indexOf("e") < 0 ? text + ".0" : text
        else
            store[key] = decimals === 0 && step >= 1 ? Math.round(v) : Number(text)
    }

    Dial {
        id: dial
        x: root.inline ? root.width - width : (root.width - width) / 2
        width: root.size
        height: root.size
        from: root.from
        to: root.to
        stepSize: root.step
        snapMode: Dial.SnapAlways
        value: root.value
        startAngle: -135
        endAngle: 135
        inputMode: Dial.Circular
        wheelEnabled: root.wheelOk || activeFocus || pressed
        hoverEnabled: true
        // Any change the user makes -- a drag, a wheel notch, an arrow key -- is
        // written. Not onMoved: Dial emits no moved() for the wheel, so a wheel
        // turn moved the knob and never reached the setting. A change that
        // comes from the store (value: root.value) matches it and is skipped;
        // `ready` keeps the from / to / value set-up at creation out of it.
        property bool ready: false
        Component.onCompleted: ready = true
        onValueChanged: {
            if (!ready || !(pressed || hovered || activeFocus)) return
            if (Math.abs(value - root.value) < 1e-9) return
            if (root.writesStore) root.write(value)
            root.moved(value)
        }
        // A double-click resets too (user, 2026-09-27). The Dial takes the
        // press itself, so a TapHandler never sees the second one: two
        // presses within the double-click interval, reset on the release.
        property double lastPress: 0
        property bool resetOnRelease: false
        // Dial counts an arrow key as a press too: two quick notches are not
        // a double-click. Keys sees the key before the Dial does.
        property bool keyDown: false
        Keys.onPressed: (e) => { keyDown = true; e.accepted = false }
        Keys.onReleased: (e) => { keyDown = false; e.accepted = false }
        onPressedChanged: {
            if (keyDown) {
                if (!pressed) root.released(value)
                return
            }
            if (pressed) {
                const now = Date.now()
                resetOnRelease = !isNaN(root.defaultValue) && now - lastPress <= Qt.styleHints.mouseDoubleClickInterval
                lastPress = resetOnRelease ? 0 : now
            } else if (resetOnRelease) {
                resetOnRelease = false
                root.reset()                 // (emits released with the default)
            } else {
                root.released(value)
            }
        }

        readonly property real c: width / 2
        readonly property real r: width / 2 - Math.max(root.stroke / 2, root.knobSize / 2) - 1
        readonly property color ink: root.enabled ? Theme.accent : Theme.alpha(Theme.accent, 0.4)
        property real shownAngle: angle
        // the fill's other end: the low end, or fillFrom's place on the arc
        readonly property real fillAngle: isNaN(root.fillFrom) || root.to === root.from ? startAngle
            : startAngle + (Math.max(root.from, Math.min(root.to, root.fillFrom)) - root.from)
                           / (root.to - root.from) * (endAngle - startAngle)
        Behavior on shownAngle { enabled: !dial.pressed; NumberAnimation { duration: Theme.dur(160); easing.type: Easing.OutCubic } }

        background: Item {
            implicitWidth: root.size
            implicitHeight: root.size
            Shape {
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    strokeColor: root.onDark ? "#40ffffff" : Theme.dialTrack
                    strokeWidth: root.stroke
                    fillColor: "transparent"
                    capStyle: ShapePath.RoundCap
                    PathAngleArc {
                        centerX: dial.c; centerY: dial.c
                        radiusX: dial.r; radiusY: dial.r
                        startAngle: dial.startAngle - 90
                        sweepAngle: dial.endAngle - dial.startAngle
                    }
                }
                ShapePath {
                    strokeColor: dial.ink
                    strokeWidth: root.stroke
                    fillColor: "transparent"
                    capStyle: ShapePath.RoundCap
                    PathAngleArc {
                        centerX: dial.c; centerY: dial.c
                        radiusX: dial.r; radiusY: dial.r
                        startAngle: Math.min(dial.fillAngle, dial.shownAngle) - 90
                        sweepAngle: Math.max(0.5, Math.abs(dial.shownAngle - dial.fillAngle))
                    }
                }
            }
            // The default, as SliderRow marks it: a short tick across the arc.
            Rectangle {
                visible: !isNaN(root.defaultValue) && root.to !== root.from
                readonly property real a: (dial.startAngle + (root.defaultValue - root.from) / (root.to - root.from)
                                           * (dial.endAngle - dial.startAngle) - 90) * Math.PI / 180
                x: dial.c + dial.r * Math.cos(a) - width / 2
                y: dial.c + dial.r * Math.sin(a) - height / 2
                width: 2
                height: root.stroke + 6
                radius: 1
                rotation: (a * 180 / Math.PI) + 90
                color: root.onDark ? "#80ffffff" : Theme.fgFaint
            }
            Text {
                anchors.centerIn: parent
                text: root.valueText !== "" ? root.valueText : root.value.toFixed(root.decimals)
                color: root.onDark ? (root.enabled ? "white" : "#80ffffff") : root.enabled ? Theme.fg : Theme.fgFaint
                font.family: Theme.bodyFont
                font.pixelSize: root.valueSize
                font.weight: Font.DemiBold
                font.features: { "tnum": 1 }
            }
        }

        handle: Rectangle {
            readonly property real a: (dial.shownAngle - 90) * Math.PI / 180
            x: dial.c + dial.r * Math.cos(a) - width / 2
            y: dial.c + dial.r * Math.sin(a) - height / 2
            width: root.knobSize + (dial.pressed ? 3 : (dial.hovered ? 2 : 0))
            height: width
            radius: width / 2
            color: root.enabled ? Theme.knob : Theme.fgFaint
            border.width: 2
            border.color: root.enabled ? Theme.accent : Theme.border
            Behavior on width { NumberAnimation { duration: Theme.dur(120) } }
        }

        TapHandler {
            acceptedButtons: Qt.RightButton
            enabled: !isNaN(root.defaultValue)
            onTapped: root.reset()
        }

    }

    Text {
        id: caption
        visible: !root.inline
        anchors.top: dial.bottom
        anchors.topMargin: 2
        width: root.width
        horizontalAlignment: Text.AlignHCenter
        text: root.label.replace(/:\s*$/, "")
        color: root.onDark ? (root.enabled ? "#d0ffffff" : "#70ffffff") : root.enabled ? Theme.fgMuted : Theme.fgFaint
        font.family: Theme.bodyFont
        font.pixelSize: Theme.smallSize
        wrapMode: Text.WordWrap
        maximumLineCount: root.captionLines
        elide: Text.ElideRight
        lineHeight: 0.95
    }

    // the caption's / inline label's painted text, for the tooltip
    Item {
        id: captionHit
        visible: caption.visible
        x: (root.width - width) / 2
        y: caption.y
        width: Math.min(caption.contentWidth, caption.width)
        height: caption.height
    }
    Item {
        id: labelHit
        visible: inlineLabel.visible
        x: inlineLabel.x
        y: inlineLabel.y
        width: Math.min(inlineLabel.contentWidth, inlineLabel.width)
        height: inlineLabel.height
    }
    HoverTip { tip: root.help; hotItems: [dial, captionHit, labelHit] }
}
