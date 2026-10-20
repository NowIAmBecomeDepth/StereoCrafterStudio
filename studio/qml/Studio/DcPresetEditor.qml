import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Effects
import QtQuick.Layouts
import Studio

// Custom resolution presets, in Settings > DepthCrafter (user, 2026-10-05):
// pick an aspect ratio, tick sizes from the second drop-down (every width in
// steps of 64 up to ~1080p, each with the multiple-of-64 height closest to the
// ratio and how far it is from it), Save -- they join the Resolution Preset
// menu, a column per ratio, with that difference beside each size. Saved in
// config/depthcrafter_presets.json (this machine's own; git ignores config/).
ColumnLayout {
    id: ed
    objectName: "dcPresetEditor"
    property var dc
    // Splatting's Low Res presets use this editor too (user, 2026-10-11): its own words
    property string stageName: "DepthCrafter"
    property string presetsFile: "config/depthcrafter_presets.json"
    property string intro: "Pick an aspect ratio, then tick the sizes you want. Every size is a multiple of 64, as "
                           + "DepthCrafter needs, so most can't match the ratio exactly: beside each is how much wider "
                           + "or taller it is than the true ratio. That's only the size DepthCrafter works at: "
                           + "Splatting resizes the depth map to the original video's own dimensions, so the finished "
                           + "clip keeps its true shape. Saved sizes appear in Resolution Preset, a column per ratio."
    spacing: 10

    property string ratio: "16:9"
    // what is ticked, per ratio: {label: ["WxH", ...]}; saved: what is on disk
    property var picked: ({})
    property var saved: ({})
    // each ratio's Auto Size pick: {label: "WxH"} (user, 2026-10-05)
    property var autoPicked: ({})
    property var autoSaved: ({})
    property int rev: 0                 // bumped on each tick, for the bindings
    // sizes typed in by hand this session, per ratio, until saved: {label: [{w, h, key, diff}]}
    property var typed: ({})
    readonly property int step: dc && dc.presetStep ? dc.presetStep : 64
    readonly property var sizes: { rev; const base = dc ? dc.customSizes(ratio) : []
                                   const keys = base.map(s => s.key)
                                   return base.concat((typed[ratio] || []).filter(s => keys.indexOf(s.key) < 0))
                                              .sort((a, b) => a.w - b.w || a.h - b.h) }
    readonly property bool dirty: { rev; return JSON.stringify(norm(picked)) !== JSON.stringify(norm(saved))
                                              || JSON.stringify(autoPicked) !== JSON.stringify(autoSaved) }
    readonly property int autoIndex: { rev; return sizes.findIndex(s => s.key === autoPicked[ratio]) }
    readonly property int pickedHere: { rev; return (picked[ratio] || []).length }
    readonly property int pickedAll: { rev; let n = 0; for (const k in picked) n += picked[k].length; return n }

    function norm(m) {
        const out = {}
        for (const k of Object.keys(m).sort()) if (m[k].length) out[k] = m[k].slice().sort()
        return out
    }
    // a size typed in by hand (user, 2026-10-11): ticked, and in the list at once
    function addTyped(wText, hText) {
        const w = parseInt(wText), h = parseInt(hText)
        if (!(w > 0 && h > 0) || String(w) !== String(wText).trim() || String(h) !== String(hText).trim()) {
            Theme.toast("warning", "Not a size", "Type a width and a height in whole pixels, e.g. 1408 and 768.")
            return false
        }
        if (w % step || h % step) {
            Theme.toast("warning", "Not a multiple of " + step,
                        stageName + " needs both sides in multiples of " + step + ": " + w + "×" + h + " isn't.")
            return false
        }
        if (w > 4096 || h > 4096) {
            Theme.toast("warning", "Too big", "Keep both sides at 4096 or less.")
            return false
        }
        const key = w + "x" + h
        const r = Number(ratio.split(":")[0]) / Number(ratio.split(":")[1])
        const d = (w / h) / r - 1
        const diff = Math.abs(d) < 0.0005 ? "exact" : (Math.abs(d) * 100).toFixed(1) + "% " + (d > 0 ? "wider" : "taller")
        const copy = Object.assign({}, typed)
        copy[ratio] = (copy[ratio] || []).filter(s => s.key !== key).concat([{ w: w, h: h, key: key, diff: diff }])
        typed = copy
        toggle(key, true)
        return true
    }
    function load() {
        typed = ({})
        saved = dc ? dc.customSaved() : ({})
        const copy = {}
        for (const k in saved) copy[k] = saved[k].slice()
        picked = copy
        autoSaved = dc ? dc.autoSizes() : ({})
        autoPicked = Object.assign({}, autoSaved)
        rev++
    }
    function setAuto(key) {
        const copy = Object.assign({}, autoPicked)
        copy[ratio] = key
        autoPicked = copy
        rev++
    }
    function isPicked(key) { rev; return (picked[ratio] || []).indexOf(key) >= 0 }
    function toggle(key, on) {
        const list = (picked[ratio] || []).filter(k => k !== key)
        if (on) list.push(key)
        picked[ratio] = list
        rev++
    }
    function summary() {
        rev
        const parts = []
        for (const r of (dc ? dc.customRatios() : []))
            if ((picked[r] || []).length)
                parts.push(r + ":  " + picked[r].slice().sort((a, b) => parseInt(a) - parseInt(b)).map(k => k.replace("x", "×")).join(", "))
        return parts.length ? parts.join("\n") : "None yet."
    }
    Component.onCompleted: load()

    Text {
        text: "Custom Resolution Presets"
        color: Theme.fg
        font.family: Theme.buttonFont
        font.pixelSize: Theme.headingSize + 2
        font.weight: Font.Bold
        font.capitalization: Font.AllUppercase
        font.letterSpacing: 0.8
        Layout.topMargin: 10
    }
    Text {
        text: ed.intro
        color: Theme.fgMuted
        font.family: Theme.bodyFont
        font.pixelSize: Theme.smallSize + 1
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    }
    RowLayout {
        Layout.fillWidth: true
        spacing: 10
        Text { text: "Aspect ratio"; color: Theme.fg; font.family: Theme.bodyFont; font.pixelSize: Theme.bodySize }
        Dropdown {
            objectName: "dcPresetRatio"
            boxWidth: 110
            model: ed.dc ? ed.dc.customRatios() : []
            currentIndex: model.indexOf(ed.ratio)
            onActivated: (i) => ed.ratio = model[i]
        }
        Text { text: "Sizes"; color: Theme.fg; font.family: Theme.bodyFont; font.pixelSize: Theme.bodySize; Layout.leftMargin: 8 }
        // the second drop-down: a box like the first, opening a list to tick
        Rectangle {
            id: sizeBox
            objectName: "dcPresetSizesBox"
            implicitWidth: 230
            implicitHeight: Theme.rowHeight
            radius: 6
            color: Theme.field
            border.width: 1
            border.color: sizeHover.hovered || sizePop.opened ? Theme.borderStrong : Theme.border
            HoverHandler { id: sizeHover; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: sizePop.opened ? sizePop.close() : sizePop.open() }
            Text {
                x: 10
                anchors.verticalCenter: parent.verticalCenter
                text: ed.pickedHere ? ed.pickedHere + (ed.pickedHere === 1 ? " size ticked" : " sizes ticked") : "Choose sizes…"
                color: Theme.fg
                font.family: Theme.bodyFont
                font.pixelSize: Theme.bodySize
            }
            Icon {
                x: parent.width - width - 10
                anchors.verticalCenter: parent.verticalCenter
                glyph: Theme.icon.down
                size: 10
                color: Theme.fgMuted
                rotation: sizePop.opened ? 180 : 0
            }
            Popup {
                id: sizePop
                objectName: "dcPresetSizesPopup"
                y: sizeBox.height + 4
                // kept inside the window (user, 2026-10-11: it ran off the bottom): Qt moves it
                // up and shortens it when there's no room below; the list scrolls
                margins: 8
                width: 300
                height: Math.min(sizeList.contentHeight + 2 * padding, 380)
                padding: 6
                enter: Transition {
                    ParallelAnimation {
                        NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.dur(140) }
                        NumberAnimation { property: "scale"; from: 0.9; to: 1; duration: Theme.dur(240); easing.type: Easing.OutCubic }
                    }
                }
                exit: Transition { NumberAnimation { property: "opacity"; to: 0; duration: Theme.dur(100) } }
                background: Rectangle {
                    PopupGrain { radius: 10 }
                    radius: 10
                    color: Theme.field
                    border.width: 1
                    border.color: Theme.borderStrong
                    layer.enabled: true
                    layer.effect: MultiEffect { shadowEnabled: true; shadowBlur: 0.9; shadowOpacity: Theme.isDark ? 0.55 : 0.2; shadowVerticalOffset: 6 }
                }
                contentItem: ListView {
                    id: sizeList
                    objectName: "dcPresetSizeList"
                    clip: true
                    spacing: 1
                    boundsBehavior: Flickable.StopAtBounds
                    model: ed.sizes
                    ScrollBar.vertical: SlimScrollBar {}
                    delegate: Rectangle {
                        id: row
                        required property var modelData
                        objectName: "dcPresetSizeRow"
                        readonly property bool on: ed.isPicked(modelData.key)
                        width: sizeList.width - 8
                        height: 30
                        radius: 6
                        color: rowHover.hovered ? Theme.hover : "transparent"
                        HoverHandler { id: rowHover; cursorShape: Qt.PointingHandCursor }
                        TapHandler { onTapped: ed.toggle(row.modelData.key, !row.on) }
                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 6
                            anchors.rightMargin: 8
                            spacing: 8
                            AnimCheck {
                                checked: row.on
                                onToggled: { ed.toggle(row.modelData.key, checked); checked = Qt.binding(() => row.on) }
                            }
                            Text {
                                text: row.modelData.w + "×" + row.modelData.h
                                color: Theme.fg
                                font.family: Theme.bodyFont
                                font.pixelSize: Theme.bodySize
                                font.weight: row.on ? Font.DemiBold : Font.Normal
                                font.features: { "tnum": 1 }
                                Layout.preferredWidth: 90
                            }
                            Text {
                                text: "(" + row.modelData.diff + ")"
                                color: row.modelData.diff === "exact" ? Theme.success : Theme.fgFaint
                                font.family: Theme.bodyFont
                                font.pixelSize: Theme.smallSize + 1
                                Layout.fillWidth: true
                            }
                        }
                    }
                }
            }
        }
        Item { Layout.fillWidth: true }
    }
    // any exact size, typed in (user, 2026-10-11)
    RowLayout {
        Layout.fillWidth: true
        spacing: 8
        Text { text: "Add a size"; color: Theme.fg; font.family: Theme.bodyFont; font.pixelSize: Theme.bodySize }
        TextBox { id: typedW; objectName: "dcPresetTypedW"; boxWidth: 72; placeholderText: "Width" }
        Text { text: "×"; color: Theme.fgMuted; font.family: Theme.bodyFont; font.pixelSize: Theme.bodySize }
        TextBox { id: typedH; objectName: "dcPresetTypedH"; boxWidth: 72; placeholderText: "Height"
                  onAccepted: addButton.clicked() }
        AccentButton {
            id: addButton
            objectName: "dcPresetAdd"
            text: "Add"
            kind: "outline"
            onClicked: if (ed.addTyped(typedW.text, typedH.text)) { typedW.text = ""; typedH.text = "" }
        }
        Text {
            text: "to " + ed.ratio + ", in multiples of " + ed.step
            color: Theme.fgMuted
            font.family: Theme.bodyFont
            font.pixelSize: Theme.smallSize + 1
            Layout.fillWidth: true
            elide: Text.ElideRight
        }
    }
    // the size Auto Size fills in for a clip of this ratio
    RowLayout {
        Layout.fillWidth: true
        spacing: 10
        Text { text: "Auto Size picks"; color: Theme.fg; font.family: Theme.bodyFont; font.pixelSize: Theme.bodySize }
        Dropdown {
            objectName: "dcPresetAuto"
            boxWidth: 230
            model: ed.sizes.map(s => s.w + "×" + s.h + "  (" + s.diff + ")")
            currentIndex: ed.autoIndex
            onActivated: (i) => ed.setAuto(ed.sizes[i].key)
        }
        Text {
            text: "for a " + ed.ratio + " clip. Default " + (ed.dc ? ed.dc.defaultAutoSize(ed.ratio).replace("x", "×") : "")
            color: Theme.fgMuted
            font.family: Theme.bodyFont
            font.pixelSize: Theme.smallSize + 1
            Layout.fillWidth: true
            elide: Text.ElideRight
        }
    }
    // everything ticked, across the ratios
    Text {
        objectName: "dcPresetSummary"
        text: "Ticked:\n" + ed.summary()
        color: Theme.fgMuted
        font.family: Theme.bodyFont
        font.pixelSize: Theme.smallSize + 1
        lineHeight: 1.15
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    }
    RowLayout {
        spacing: 8
        AccentButton {
            objectName: "dcPresetSave"
            text: "Save Presets"
            kind: "filled"
            enabled: ed.dirty
            onClicked: {
                if (ed.dc && ed.dc.saveCustomPresets(ed.picked) && ed.dc.saveAutoSizes(ed.autoPicked)) {
                    ed.load()
                    Theme.toast("success", "Presets saved", ed.pickedAll + (ed.pickedAll === 1 ? " size is" : " sizes are")
                                + " in " + ed.stageName + "'s Resolution Preset menu.")
                } else {
                    Theme.toast("error", "Presets not saved", "They couldn't be written to " + ed.presetsFile + " -- see the console.")
                }
            }
        }
        AccentButton { objectName: "dcPresetRevert"; text: "Revert"; kind: "ghost"; enabled: ed.dirty; onClicked: ed.load() }
        Item { Layout.fillWidth: true }
        // every ratio's presets gone at once (user, 2026-10-11); a second click within a
        // few seconds confirms, so one stray click can't empty the menu
        AccentButton {
            id: removeAll
            objectName: "dcPresetRemoveAll"
            property bool armed: false
            text: armed ? "Click Again to Remove All" : "Remove All Presets"
            kind: "ghost"
            enabled: Object.keys(ed.saved).some(k => (ed.saved[k] || []).length) || ed.pickedAll > 0
            onClicked: {
                if (!armed) { armed = true; disarm.restart(); return }
                armed = false
                if (ed.dc && ed.dc.saveCustomPresets({})) {
                    ed.load()
                    Theme.toast("success", "Presets removed", ed.stageName + "'s Resolution Preset menu is empty.")
                } else {
                    Theme.toast("error", "Presets not removed", "They couldn't be written to " + ed.presetsFile + " -- see the console.")
                }
            }
            Timer { id: disarm; interval: 4000; onTriggered: removeAll.armed = false }
        }
    }
}
