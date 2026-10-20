import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import Studio

// v1's labelled slider (core/ui/widgets.create_single_slider_with_label_updater):
// "Label:" right-aligned, the track, the value as text. Snaps to `step`, one
// step per wheel notch, right-click resets to `defaultValue` (drawn as a tick
// on the track), and `fill` shades the track up to the knob.
//
// The value is written in the type the setting already holds: a v1 StringVar
// keeps a string ("23.0"), a DoubleVar/IntVar a number.
Item {
    id: row
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
    property bool doubleClickResets: false   // a left double-click resets as well as the right-click
    property bool fill: true
    property bool showValue: true     // off when an entry beside it shows the value
    property int labelWidth: 96
    property int valueWidth: 44
    readonly property alias pressed: slider.pressed
    signal moved(real value)          // every step of a drag
    signal released(real value)       // once, when let go

    readonly property var raw: store && key ? store[key] : undefined
    readonly property real value: {
        const v = Number(raw)
        return isNaN(v) ? from : Math.max(from, Math.min(to, v))
    }

    Layout.fillWidth: true
    implicitHeight: Theme.rowHeight
    implicitWidth: labelWidth + 120 + valueWidth

    function write(v) {
        if (!store || !key) return
        v = Math.max(from, Math.min(to, Math.round((v - from) / step) * step + from))
        const text = Number(v.toFixed(Math.max(decimals, 6))).toString()
        if (typeof raw === "string") {
            // v1's StringVar held str(float): "23.0", not "23".
            store[key] = text.indexOf(".") < 0 && text.indexOf("e") < 0 ? text + ".0" : text
        } else {
            store[key] = decimals === 0 && step >= 1 ? Math.round(v) : Number(text)
        }
    }

    Text {
        id: lbl
        width: row.labelWidth
        height: parent.height
        horizontalAlignment: Text.AlignRight
        verticalAlignment: Text.AlignVCenter
        text: row.label
        color: row.enabled ? Theme.fg : Theme.fgFaint
        font.family: Theme.bodyFont
        font.pixelSize: Theme.bodySize
        elide: Text.ElideLeft
    }

    Slider {
        id: slider
        x: row.labelWidth + 8
        width: row.width - x - row.valueWidth - 8
        height: parent.height
        from: row.from
        to: row.to
        stepSize: row.step
        snapMode: Slider.SnapAlways
        wheelEnabled: row.wheelOk || activeFocus || pressed
        value: row.value
        onMoved: { row.write(value); row.moved(value) }
        // doubleClickResets (user, 2026-10-11: Preview Brightness): a second press within the
        // double-click time resets on its release. Watched here, not by a TapHandler -- the
        // slider's own grab of the press cancelled the handler's double tap.
        objectName: "rowSlider"
        property double lastPress: 0
        property bool resetOnRelease: false
        onPressedChanged: {
            if (pressed) {
                const now = Date.now()
                resetOnRelease = row.doubleClickResets && !isNaN(row.defaultValue)
                                 && now - lastPress <= Qt.styleHints.mouseDoubleClickInterval
                lastPress = resetOnRelease ? 0 : now
            } else if (resetOnRelease) {
                resetOnRelease = false
                row.write(row.defaultValue)
                row.released(row.defaultValue)
            } else {
                row.released(value)
            }
        }

        background: Item {
            x: slider.leftPadding
            y: slider.topPadding + slider.availableHeight / 2 - 2
            width: slider.availableWidth
            height: 4
            Rectangle {
                anchors.fill: parent
                radius: 2
                color: Theme.trough
            }
            Rectangle {
                visible: row.fill
                width: slider.visualPosition * parent.width
                height: parent.height
                radius: 2
                color: row.enabled ? Theme.accent : Theme.alpha(Theme.accent, 0.4)
                Behavior on width { enabled: !slider.pressed; NumberAnimation { duration: Theme.dur(140) } }
            }
            // The default, as v1 marks it: a small tick through the track.
            Rectangle {
                visible: !isNaN(row.defaultValue) && row.to !== row.from
                x: (row.defaultValue - row.from) / (row.to - row.from) * parent.width - 1
                y: -3
                width: 2
                height: parent.height + 6
                radius: 1
                color: Theme.fgFaint
            }
        }
        // A tall, narrow knob (user's choice, like v1's) rather than a disc.
        handle: Rectangle {
            x: slider.leftPadding + slider.visualPosition * (slider.availableWidth - width)
            y: slider.topPadding + slider.availableHeight / 2 - height / 2
            width: 8
            height: 18
            radius: 3
            color: row.enabled ? Theme.knob : Theme.fgFaint
            border.width: 2
            border.color: row.enabled ? Theme.accent : Theme.border
            scale: slider.pressed ? 1.15 : (hh.hovered ? 1.08 : 1)
            Behavior on scale { NumberAnimation { duration: Theme.dur(120) } }
            Behavior on x { enabled: !slider.pressed; NumberAnimation { duration: Theme.dur(140) } }
            HoverHandler { id: hh }
        }
        TapHandler {
            acceptedButtons: Qt.RightButton
            enabled: !isNaN(row.defaultValue)
            onTapped: { row.write(row.defaultValue); row.released(row.defaultValue) }
        }
    }

    // the label's painted text (right-aligned in its column), for the tooltip
    Item {
        id: lblHit
        x: lbl.width - width
        width: Math.min(lbl.contentWidth, lbl.width)
        height: lbl.height
    }
    Text {
        id: valueText
        visible: row.showValue
        x: row.width - row.valueWidth
        width: row.valueWidth
        height: parent.height
        verticalAlignment: Text.AlignVCenter
        text: row.value.toFixed(row.decimals)
        color: row.enabled ? Theme.fg : Theme.fgFaint
        font.family: Theme.bodyFont
        font.pixelSize: Theme.bodySize
        font.features: { "tnum": 1 }
    }

    HoverTip { tip: row.help; hotItems: [lblHit, slider, valueText] }
}
