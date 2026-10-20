import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Effects
import QtQuick.Layouts
import Studio

// v1's Options > Encoding Settings (core/ui/encoding_settings.py), as a
// modal sheet. Edits apply as they are made -- there is no OK/Cancel pair to
// forget -- and the preset picker lights whichever preset the fields match.
Popup {
    id: dlg
    property var ctl: null
    readonly property var st: ctl ? ctl.settings : null
    // A stage whose settings are named otherwise maps them here (Splatting:
    // encoding_encoder is "encoder", ...); the choices keep the shared names.
    property var keyMap: ({})
    function k(name) { return keyMap[name] || name }
    // Splatting's two CRFs (v1 show_dual_crf): Full Res and Low Res.
    property bool dualCrf: false
    // Merging's Merge All SBS to 1 Video (user, 2026-10-11): its own tab, the whole movie's encode
    property bool mergeAll: false
    property int tab: 0                  // 0 the clips' encoding, 1 Merge All

    parent: Overlay.overlay
    modal: true
    // counted while shown: the page under it ignores clicks (Theme.modalOpen)
    onVisibleChanged: Theme.modals = Math.max(0, Theme.modals + (visible ? 1 : -1))
    focus: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    anchors.centerIn: parent
    width: 720
    padding: 22

    component EBox: TextBox { store: dlg.st; owner: dlg.ctl; boxWidth: 90 }
    component EDrop: Dropdown { store: dlg.st; boxWidth: 190 }
    component ECheck: CheckField { store: dlg.st }
    component PathPick: FieldRow {
        id: pp
        property string key
        property bool file: false
        labelWidth: 120
        Layout.fillWidth: true
        RowLayout {
            width: parent.width
            spacing: 8
            TextBox { store: dlg.st; owner: dlg.ctl; key: pp.key; numeric: false; Layout.fillWidth: true }
            AccentButton {
                text: "Browse"; kind: "outline"
                onClicked: dlg.browse(pp.key, pp.file)
            }
        }
    }
    function help(k) { return dlg.ctl ? (dlg.ctl.help[k] || "") : "" }

    // Merge All's movie file and output folder
    property string browseKey: ""
    FolderPicker {
        id: picker
        fileTypes: ["mkv", "mp4", "m2ts", "ts", "mov", "avi", "mka", "ac3", "eac3", "dts", "thd", "flac", "wav"]
        currentFolder: {
            const v = dlg.st && dlg.browseKey ? String(dlg.st[dlg.browseKey] || "") : ""
            return picker.allowFiles && v ? v.replace(/[\\/][^\\/]*$/, "") : v
        }
        onAccepted: if (dlg.st) dlg.st[dlg.browseKey] = selectedPath.replace(/\\/g, "/")
    }
    function browse(key, file) {
        browseKey = key
        picker.title = file ? "The movie file whose soundtrack goes in" : "Where Merge All writes the movie"
        picker.allowFiles = !!file
        picker.open()
    }

    Overlay.modal: Rectangle { color: Theme.alpha("#000000", Theme.isDark ? 0.55 : 0.3) }
    enter: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.dur(160) }
            NumberAnimation { property: "scale"; from: 0.94; to: 1; duration: Theme.dur(300); easing.type: Easing.OutBack }
        }
    }
    exit: Transition { NumberAnimation { property: "opacity"; to: 0; duration: Theme.dur(120) } }

    background: Rectangle {
        PopupGrain { radius: 14 }   // the window's film grain
        radius: 14
        color: Theme.field
        border.width: 1
        border.color: Theme.borderStrong
        layer.enabled: true
        layer.effect: MultiEffect { shadowEnabled: true; shadowBlur: 1.0; shadowOpacity: Theme.isDark ? 0.6 : 0.25; shadowVerticalOffset: 10 }
    }

    contentItem: ColumnLayout {
        spacing: 12

        RowLayout {
            Layout.fillWidth: true
            Text {
                text: Theme.headingText("Encoding Settings")
                color: Theme.fg
                font.family: Theme.headingFont
                font.pixelSize: Theme.headingSize
                font.weight: Font.DemiBold
                Layout.fillWidth: true
            }
            Text {
                visible: dlg.tab === 0
                text: "Preset"
                color: Theme.fgMuted
                font.family: Theme.bodyFont
                font.pixelSize: Theme.bodySize
            }
            SegmentedControl {
                visible: dlg.tab === 0
                compact: true
                controlled: true
                fontSize: 12
                model: dlg.ctl ? dlg.ctl.presetNames : []
                currentIndex: dlg.ctl ? dlg.ctl.presetNames.indexOf(dlg.ctl.encodingPreset) : -1
                onActivated: (i) => dlg.ctl.applyEncodingPreset(dlg.ctl.presetNames[i])
            }
            Text {
                visible: dlg.tab === 0
                text: dlg.ctl && dlg.ctl.encodingPreset === "Custom" ? "Custom" : ""
                color: Theme.accentInk
                font.family: Theme.bodyFont
                font.pixelSize: 12
            }
        }

        SegmentedControl {
            objectName: "encTabs"
            visible: dlg.mergeAll
            compact: true
            controlled: true
            model: ["Clips", "Merge All"]
            currentIndex: dlg.tab
            onActivated: (i) => dlg.tab = i
        }

        GridLayout {
            visible: dlg.tab === 0
            Layout.fillWidth: true
            columns: 2
            columnSpacing: 24
            rowSpacing: Theme.rowGap
            FieldRow { label: "Codec"; labelWidth: 120; help: dlg.help("encoding_codec") || dlg.help("encoding_settings"); EDrop { key: dlg.k("codec"); model: dlg.ctl ? dlg.ctl.choices.codec : [] } }
            FieldRow { label: "Bit Depth"; labelWidth: 120; help: dlg.help("encoding_bit_depth"); EDrop { key: dlg.k("bit_depth"); model: dlg.ctl ? dlg.ctl.choices.bit_depth : [] } }
            FieldRow { label: "Encoder"; labelWidth: 120; help: dlg.help("encoding_encoder"); EDrop { key: dlg.k("encoding_encoder"); model: dlg.ctl ? dlg.ctl.choices.encoding_encoder : [] } }
            FieldRow { label: "Pixel Format"; labelWidth: 120; EDrop { key: dlg.k("pix_fmt"); model: dlg.ctl ? dlg.ctl.choices.pix_fmt : [] } }
            FieldRow { label: "Quality Preset"; labelWidth: 120; help: dlg.help("encoding_quality"); EDrop { key: dlg.k("encoding_quality"); model: dlg.ctl ? dlg.ctl.choices.encoding_quality : [] } }
            FieldRow { label: "Color Tags"; labelWidth: 120; EDrop { key: dlg.k("color_tags"); model: dlg.ctl ? dlg.ctl.choices.color_tags : [] } }
            FieldRow { label: "CPU Tune"; labelWidth: 120; help: dlg.help("encoding_tune"); EDrop { key: dlg.k("encoding_tune"); model: dlg.ctl ? dlg.ctl.choices.encoding_tune : [] } }
            FieldRow { visible: !dlg.dualCrf; label: "Output CRF"; labelWidth: 120; help: dlg.help("output_crf"); EBox { key: dlg.k("output_crf") } }
            FieldRow { visible: dlg.dualCrf; label: "Full Res CRF"; labelWidth: 120; help: dlg.help("output_crf_full") || dlg.help("output_crf"); EBox { key: "output_crf_full" } }
            // Low Res CRF under Full Res CRF, in the right column (user, 2026-10-02)
            Item { visible: dlg.dualCrf; Layout.fillWidth: true }
            FieldRow { visible: dlg.dualCrf; label: "Low Res CRF"; labelWidth: 120; help: dlg.help("output_crf_low") || dlg.help("output_crf"); EBox { key: "output_crf_low" } }
        }

        RowLayout {
            visible: dlg.tab === 0
            Layout.fillWidth: true
            spacing: 24
            ColumnLayout {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop
                spacing: Theme.rowGap
                Text {
                    text: Theme.headingText("NVENC")
                    color: Theme.fgMuted
                    font.family: Theme.headingFont; font.pixelSize: 12; font.weight: Font.DemiBold
                }
                ECheck { key: dlg.k("nvenc_lookahead_enabled"); text: "Enable Lookahead"; help: dlg.help("encoding_nvenc_lookahead_enabled") }
                FieldRow {
                    objectName: "encLookaheadRow"
                    label: "Lookahead Frames"; labelWidth: 140
                    help: dlg.help("encoding_nvenc_lookahead")
                    enabled: dlg.st ? !!dlg.st[dlg.k("nvenc_lookahead_enabled")] : false   // mapped: Splatting's is encoding_* (bug sweep A10)
                    EBox { key: dlg.k("nvenc_lookahead") }
                }
                ECheck { key: dlg.k("nvenc_spatial_aq"); text: "Spatial AQ"; help: dlg.help("encoding_nvenc_spatial_aq") }
                ECheck { key: dlg.k("nvenc_temporal_aq"); text: "Temporal AQ"; help: dlg.help("encoding_nvenc_temporal_aq") }
                FieldRow { label: "AQ Strength"; labelWidth: 140; help: dlg.help("encoding_nvenc_aq_strength"); EBox { key: dlg.k("nvenc_aq_strength") } }
            }
            ColumnLayout {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop
                spacing: Theme.rowGap
                Text {
                    text: Theme.headingText("DNxHR")
                    color: Theme.fgMuted
                    font.family: Theme.headingFont; font.pixelSize: 12; font.weight: Font.DemiBold
                }
                ECheck { key: dlg.k("dnxhr_fullres_split"); text: "Enable DNxHR"; help: dlg.help("dnxhr_fullres_split_tooltip") }
                FieldRow {
                    objectName: "encDnxhrRow"
                    label: "DNxHR Profile"; labelWidth: 110
                    help: dlg.help("dnxhr_profile")
                    enabled: dlg.st ? !!dlg.st[dlg.k("dnxhr_fullres_split")] : false
                    EDrop { key: dlg.k("dnxhr_profile"); model: dlg.ctl ? dlg.ctl.choices.dnxhr_profile : [] }
                }
            }
        }

        // ---- Merge All: the whole movie (studio/merge_all.py)
        ColumnLayout {
            id: mAll
            objectName: "encMergeAll"
            visible: dlg.mergeAll && dlg.tab === 1
            Layout.fillWidth: true
            spacing: Theme.rowGap
            Text {
                text: "How Merge All SBS to 1 Video writes the whole movie. The clips' own encoding is on the Clips tab."
                color: Theme.fgMuted
                font.family: Theme.bodyFont
                font.pixelSize: Theme.smallSize + 1
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }
            readonly property bool dnx: dlg.st ? String(dlg.st.mergeall_codec) === "DNxHR" : false
            GridLayout {
                Layout.fillWidth: true
                columns: 2
                columnSpacing: 24
                rowSpacing: Theme.rowGap
                FieldRow { label: "Layout"; labelWidth: 120; help: dlg.help("mergeall_layout"); EDrop { key: "mergeall_layout"; model: dlg.ctl ? dlg.ctl.choices.mergeall_layout || [] : [] } }
                FieldRow { label: "Container"; labelWidth: 120; help: dlg.help("mergeall_container"); EDrop { key: "mergeall_container"; model: dlg.ctl ? dlg.ctl.choices.mergeall_container || [] : [] } }
                FieldRow { label: "Codec"; labelWidth: 120; help: dlg.help("mergeall_codec"); EDrop { key: "mergeall_codec"; model: dlg.ctl ? dlg.ctl.choices.mergeall_codec || [] : [] } }
                FieldRow { label: "Encoder"; labelWidth: 120; enabled: !mAll.dnx; help: dlg.help("encoding_encoder"); EDrop { key: "mergeall_encoder"; model: dlg.ctl ? dlg.ctl.choices.encoding_encoder : [] } }
                FieldRow { label: "Quality Preset"; labelWidth: 120; enabled: !mAll.dnx; help: dlg.help("encoding_quality"); EDrop { key: "mergeall_quality"; model: dlg.ctl ? dlg.ctl.choices.encoding_quality : [] } }
                FieldRow { label: "CRF"; labelWidth: 120; enabled: !mAll.dnx; help: dlg.help("mergeall_crf"); EBox { key: "mergeall_crf" } }
                FieldRow { label: "Bit Depth"; labelWidth: 120; enabled: !mAll.dnx; EDrop { key: "mergeall_bit_depth"; model: dlg.ctl ? dlg.ctl.choices.bit_depth : [] } }
                FieldRow { label: "DNxHR Profile"; labelWidth: 120; enabled: mAll.dnx; help: dlg.help("dnxhr_profile"); EDrop { key: "mergeall_dnxhr_profile"; model: dlg.ctl ? dlg.ctl.choices.dnxhr_profile : [] } }
                FieldRow { label: "Audio"; labelWidth: 120; help: dlg.help("mergeall_audio_codec"); EDrop { key: "mergeall_audio_codec"; model: dlg.ctl ? dlg.ctl.choices.mergeall_audio_codec || [] : [] } }
            }
            PathPick { objectName: "encMergeAudio"; label: "Movie (sound)"; key: "mergeall_audio_file"; file: true; help: dlg.help("mergeall_audio_file") }
            PathPick { objectName: "encMergeOut"; label: "Output Folder"; key: "mergeall_output_folder"; help: dlg.help("mergeall_output_folder") }
        }

        AccentButton {
            Layout.alignment: Qt.AlignRight
            text: "Done"
            onClicked: dlg.close()
        }
    }
}
