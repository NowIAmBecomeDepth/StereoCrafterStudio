import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Effects
import QtQuick.Layouts
import Studio

// Settings > Studio > Models (user, 2026-10-20; models_controller.py): the models folder, every
// model with its size and whether it's here, a Download for each, and the Hugging Face sign-in
// the gated ones need. A download waits while a job runs; the rest queue behind it.
Popup {
    id: dlg
    objectName: "modelsDialog"
    property var m: (typeof Models !== "undefined") ? Models : null
    property bool jobRunning: false          // Main binds it
    property string folderError: ""
    property string rowError: ""
    property string focusKey: ""             // the row a page sent us to, highlighted (showRow)
    // ...kept in view until the user scrolls: every refresh (the access check's too, a moment after
    // opening) brings new rows, which put the list back at the top
    property bool scrollPending: false
    // Sign Out asked first, in Main's prompt (user, 2026-10-20): it signs the whole PC out
    signal askSignOut(var opts)

    parent: Overlay.overlay
    modal: true
    onVisibleChanged: Theme.modals = Math.max(0, Theme.modals + (visible ? 1 : -1))
    focus: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    anchors.centerIn: parent
    width: Math.min(900, (parent ? parent.width : 960) - 60)
    height: Math.min(780, (parent ? parent.height : 820) - 60)
    padding: 22

    onOpened: {
        folderError = ""; rowError = ""
        if (!m) return
        m.refresh()
        if (m.account.state !== "checking") m.checkAccount()
        if (scrollPending) Qt.callLater(scrollToFocus)
    }

    Connections {
        target: dlg.m
        function onToast(kind, title, text) { Theme.toast(kind, title, text) }
    }
    Connections {
        target: Theme
        function onModelsRequested(key) { dlg.showRow(key) }
    }
    onClosed: { focusKey = ""; scrollPending = false }

    // open at one model's row, highlighted (Theme.needModel: a TensorRT box without TensorRT)
    function showRow(key) {
        focusKey = key
        scrollPending = true
        if (opened) Qt.callLater(scrollToFocus)
        else open()
    }
    function scrollToFocus() {
        const rows = dlg.m ? dlg.m.rows : []
        const i = rows.findIndex(r => r.key === dlg.focusKey)
        if (i >= 0 && list.height > 0 && list.count === rows.length)
            list.positionViewAtIndex(i, ListView.Center)
    }

    FolderPicker {
        id: folderPick
        objectName: "modelsFolderPicker"
        title: "Models folder (Studio looks for every model here)"
        currentFolder: dlg.m ? Shell.pathToUrl(dlg.m.folder) : ""
        onAccepted: dlg.folderError = dlg.m.setFolder(Shell.urlToPath(selectedFolder))
    }

    Overlay.modal: Rectangle { color: Theme.alpha("#000000", Theme.isDark ? 0.55 : 0.3) }
    enter: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.dur(160) }
            NumberAnimation { property: "scale"; from: 0.95; to: 1; duration: Theme.dur(260); easing.type: Easing.OutCubic }
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

    component Body: Text {
        Layout.fillWidth: true
        wrapMode: Text.Wrap
        color: Theme.fgMuted
        font.family: Theme.bodyFont
        font.pixelSize: Theme.bodySize
    }
    component Small: Text {
        color: Theme.fgFaint
        font.family: Theme.bodyFont
        font.pixelSize: Theme.smallSize + 1
    }
    component Card: Rectangle {
        Layout.fillWidth: true
        radius: 10
        color: Theme.bg
        border.width: Theme.outlineWidth
        border.color: Theme.outline
    }
    component Bar: ProgressBar {
        from: 0; to: 1
        background: Rectangle { implicitHeight: 6; radius: 3; color: Theme.trough }
        contentItem: Item { Rectangle { width: parent.parent.visualPosition * parent.width; height: 6; radius: 3; color: Theme.accent } }
    }

    contentItem: ColumnLayout {
        spacing: 12

        RowLayout {
            Layout.fillWidth: true
            Text {
                text: Theme.headingText("Models")
                color: Theme.fg
                font.family: Theme.headingFont
                font.pixelSize: Theme.headingSize
                font.weight: Font.DemiBold
            }
            Item { Layout.fillWidth: true }
            IconButton { iconGlyph: Theme.icon.close; size: 30; onClicked: dlg.close() }
        }

        Body {
            text: "Every model Studio uses, where it is and whether it's here. Download gets only the files Studio "
                  + "loads, from Hugging Face; the models keep their own licences (see the README)."
        }

        // ---- the folder ----
        Card {
            implicitHeight: folderCol.implicitHeight + 24
            ColumnLayout {
                id: folderCol
                x: 12; y: 12
                width: parent.width - 24
                spacing: 6
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    Text {
                        text: "Models folder"
                        color: Theme.fg
                        font.family: Theme.bodyFont
                        font.pixelSize: Theme.bodySize
                        font.weight: Font.DemiBold
                    }
                    Text {
                        objectName: "modelsFolderPath"
                        Layout.fillWidth: true
                        text: dlg.m ? dlg.m.folder : ""
                        elide: Text.ElideMiddle
                        color: Theme.fg
                        font.family: Theme.monoFont
                        font.pixelSize: 12
                    }
                    Small { text: dlg.m && dlg.m.free ? dlg.m.free + " free" : "" }
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    Small {
                        Layout.fillWidth: true
                        wrapMode: Text.Wrap
                        text: !dlg.m ? "" : dlg.m.isDefault
                              ? "Downloads go here. Point Studio at another folder or drive to keep the models there."
                              : "Downloads go here. A model not moved here yet is still found in " + dlg.m.defaultFolder + "."
                    }
                    AccentButton {
                        objectName: "modelsChangeFolder"
                        text: "Change…"
                        kind: "outline"
                        iconGlyph: Theme.icon.folder
                        enabled: !!dlg.m && !dlg.jobRunning && !dlg.m.download.key
                        onClicked: folderPick.open()
                        HoverTip { tip: dlg.jobRunning ? "Not while a job runs." : "Nothing is moved or copied: Studio looks there from now on. Move the model folders yourself (Open, then cut and paste), or download them there." }
                    }
                    AccentButton {
                        text: "Open"
                        kind: "ghost"
                        onClicked: { const why = dlg.m.openFolder(""); if (why) dlg.folderError = why }
                    }
                    AccentButton {
                        objectName: "modelsDefaultFolder"
                        visible: !!dlg.m && !dlg.m.isDefault
                        text: "Use Default"
                        kind: "ghost"
                        enabled: !dlg.jobRunning && !!dlg.m && !dlg.m.download.key
                        onClicked: dlg.folderError = dlg.m.useDefaultFolder()
                        HoverTip { tip: "Back to the weights folder inside Studio's own folder." }
                    }
                }
                Text {
                    objectName: "modelsFolderError"
                    visible: dlg.folderError !== ""
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: dlg.folderError
                    color: Theme.danger
                    font.family: Theme.bodyFont
                    font.pixelSize: Theme.bodySize
                }
            }
        }

        // ---- Hugging Face ----
        Card {
            implicitHeight: hfCol.implicitHeight + 24
            ColumnLayout {
                id: hfCol
                x: 12; y: 12
                width: parent.width - 24
                spacing: 6
                readonly property var acc: dlg.m ? dlg.m.account : ({})
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    Text {
                        text: "Hugging Face"
                        color: Theme.fg
                        font.family: Theme.bodyFont
                        font.pixelSize: Theme.bodySize
                        font.weight: Font.DemiBold
                    }
                    Spinner { visible: hfCol.acc.state === "checking"; size: 14; thickness: 2 }
                    Text {
                        objectName: "modelsAccount"
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                        text: hfCol.acc.state === "signedIn" ? "Signed in as " + hfCol.acc.name
                            : hfCol.acc.state === "signedOut" ? "Not signed in"
                            : hfCol.acc.state === "checking" ? "Checking…" : ""
                        color: Theme.fg
                        font.family: Theme.bodyFont
                        font.pixelSize: Theme.bodySize
                    }
                    AccentButton {
                        objectName: "modelsSignOut"
                        visible: hfCol.acc.state === "signedIn"
                        text: "Sign Out"
                        kind: "ghost"
                        onClicked: dlg.askSignOut({
                            title: "Sign this PC out of Hugging Face?",
                            message: "This removes every Hugging Face token saved on this PC, not just Studio's: the "
                                     + "installer, the hf command and any other tool using them are signed out too.\n\n"
                                     + "To sign in again, paste a read token here (or run hf auth login).",
                            buttons: [{ text: "Sign Out", code: 1, kind: "danger" },
                                      { text: "Cancel", code: 0, kind: "ghost" }],
                            focusIndex: 1,
                            onDone: (code) => { if (code === 1) dlg.m.signOut() }
                        })
                        HoverTip { tip: "Signs this whole PC out of Hugging Face: every token saved here is removed, the "
                                        + "one `hf auth login` and the installer use too. Asks first." }
                    }
                }
                Small {
                    visible: hfCol.acc.state !== "signedIn"
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: "Stable Video Diffusion, SAM 3 and DINOv3 are gated: open each one's page and ask for access "
                          + "(Stable Video Diffusion's is granted at once), then sign in here with a read token from "
                          + "your Hugging Face settings. It's stored where `hf auth login` keeps it, never in Studio's settings."
                }
                RowLayout {
                    visible: hfCol.acc.state === "signedOut" || hfCol.acc.state === "unknown"
                    Layout.fillWidth: true
                    spacing: 8
                    TextField {
                        id: tokenBox
                        objectName: "modelsToken"
                        Layout.fillWidth: true
                        implicitHeight: Theme.rowHeight
                        echoMode: TextInput.Password
                        placeholderText: "hf_…  (a read token)"
                        placeholderTextColor: Theme.fgFaint
                        color: Theme.fg
                        selectionColor: Theme.alpha(Theme.accent, 0.45)
                        font.family: Theme.monoFont
                        font.pixelSize: Theme.bodySize
                        leftPadding: 10
                        background: Rectangle {
                            radius: 6
                            color: Theme.field
                            border.width: 1
                            border.color: tokenBox.activeFocus ? Theme.accent : Theme.border
                        }
                        onAccepted: if (text.trim()) { dlg.m.signIn(text); text = "" }
                    }
                    AccentButton {
                        objectName: "modelsSignIn"
                        text: "Sign In"
                        kind: "filled"
                        enabled: tokenBox.text.trim() !== ""
                        onClicked: { dlg.m.signIn(tokenBox.text); tokenBox.text = "" }
                    }
                    AccentButton {
                        text: "Get a Token"
                        kind: "ghost"
                        onClicked: dlg.m.openTokensPage()
                        HoverTip { tip: "Opens huggingface.co/settings/tokens: make a token of type Read and paste it here." }
                    }
                }
                Text {
                    visible: !!hfCol.acc.message
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: hfCol.acc.message || ""
                    color: Theme.danger
                    font.family: Theme.bodyFont
                    font.pixelSize: Theme.bodySize
                }
            }
        }

        // ---- the download running ----
        ColumnLayout {
            readonly property var dl: dlg.m ? dlg.m.download : ({})
            id: dlStrip
            visible: !!dl.key || !!dl.error || dlg.rowError !== ""
            Layout.fillWidth: true
            spacing: 4
            RowLayout {
                visible: !!dlStrip.dl.key
                Layout.fillWidth: true
                spacing: 8
                Bar {
                    objectName: "modelsProgress"
                    visible: (dlStrip.dl.progress || 0) >= 0
                    Layout.fillWidth: true
                    value: dlStrip.dl.progress || 0
                }
                // an install (TensorRT, 2026-10-29): uv says no sizes, so no fraction
                IndeterminateBar {
                    visible: (dlStrip.dl.progress || 0) < 0
                    running: visible
                    Layout.fillWidth: true
                }
                AccentButton {
                    objectName: "modelsStop"
                    text: "Stop"
                    kind: "ghost"
                    onClicked: dlg.m.stop()
                    HoverTip { tip: "Stops the download or install (and the queue). What came down is kept: the next Download carries on from there." }
                }
            }
            Small {
                objectName: "modelsProgressText"
                visible: !!dlStrip.dl.key
                Layout.fillWidth: true
                elide: Text.ElideRight
                text: dlStrip.dl.text || ""
            }
            RowLayout {
                visible: !!dlStrip.dl.error || dlg.rowError !== ""
                Layout.fillWidth: true
                spacing: 8
                Text {
                    objectName: "modelsError"
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    text: dlg.rowError || (dlStrip.dl.error ? dlStrip.dl.error.text : "")
                    color: Theme.danger
                    font.family: Theme.bodyFont
                    font.pixelSize: Theme.bodySize
                }
                IconButton { iconGlyph: Theme.icon.close; size: 24; onClicked: { dlg.rowError = ""; dlg.m.clearError() } }
            }
        }

        // ---- the models ----
        ListView {
            id: list
            objectName: "modelsList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            spacing: 2
            model: dlg.m ? dlg.m.rows : []
            onCountChanged: if (dlg.scrollPending) Qt.callLater(dlg.scrollToFocus)
            onModelChanged: if (dlg.scrollPending) Qt.callLater(dlg.scrollToFocus)
            onHeightChanged: if (dlg.scrollPending) Qt.callLater(dlg.scrollToFocus)
            onMovementStarted: dlg.scrollPending = false
            ScrollBar.vertical: SlimScrollBar {}
            delegate: Rectangle {
                id: rowItem
                required property var modelData
                readonly property var r: modelData
                readonly property bool here: r.state === "installed"
                objectName: "modelsRow_" + r.key
                width: ListView.view.width - 10
                implicitHeight: rowLay.implicitHeight + 16
                radius: 8
                color: rowItem.r.key === dlg.focusKey ? Theme.alpha(Theme.accent, 0.14)
                     : rowHover.hovered ? Theme.hover : "transparent"
                border.width: rowItem.r.key === dlg.focusKey ? 1 : 0
                border.color: Theme.accent
                HoverHandler { id: rowHover }
                RowLayout {
                    id: rowLay
                    x: 10; y: 8
                    width: parent.width - 20
                    spacing: 12
                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 1
                        RowLayout {
                            spacing: 8
                            Text {
                                text: rowItem.r.name
                                color: Theme.fg
                                font.family: Theme.bodyFont
                                font.pixelSize: Theme.bodySize
                                font.weight: Font.Medium
                                HoverTip { tip: rowItem.r.tip || "" }
                            }
                            Rectangle {
                                visible: rowItem.r.gated
                                implicitWidth: gatedText.implicitWidth + 12
                                implicitHeight: gatedText.implicitHeight + 4
                                radius: height / 2
                                color: Theme.alpha(Theme.warning, 0.16)
                                Text {
                                    id: gatedText
                                    anchors.centerIn: parent
                                    text: rowItem.r.access === "ok" ? "Gated · access granted"
                                        : rowItem.r.access === "request" ? "Gated · ask for access"
                                        : "Gated"
                                    color: Theme.warning
                                    font.family: Theme.bodyFont
                                    font.pixelSize: Theme.smallSize
                                }
                            }
                        }
                        Small { text: rowItem.r.usedBy + (rowItem.r.note ? "  ·  " + rowItem.r.note : ""); Layout.fillWidth: true; wrapMode: Text.Wrap }
                        Small {
                            Layout.fillWidth: true
                            visible: rowItem.r.where !== ""
                            text: rowItem.r.where
                            elide: Text.ElideMiddle
                            font.family: Theme.monoFont
                            font.pixelSize: 11
                        }
                    }
                    Small {
                        Layout.preferredWidth: 70
                        horizontalAlignment: Text.AlignRight
                        text: rowItem.r.state === "partial" ? rowItem.r.left + " left" : rowItem.r.size
                    }
                    Text {
                        objectName: "modelsState_" + rowItem.r.key
                        Layout.preferredWidth: 90
                        text: rowItem.r.downloading ? (rowItem.r.kind === "package" ? "Installing" : "Downloading")
                            : rowItem.r.queued ? "Queued"
                            : rowItem.here ? "Installed" : rowItem.r.state === "partial" ? "Partly here" : "Missing"
                        color: rowItem.here ? Theme.success : rowItem.r.downloading || rowItem.r.queued ? Theme.accentInk
                             : Theme.warning
                        font.family: Theme.bodyFont
                        font.pixelSize: Theme.bodySize
                    }
                    // the action: one button, or a line saying where to get it
                    Item {
                        Layout.preferredWidth: 190
                        implicitHeight: actions.implicitHeight
                        RowLayout {
                            id: actions
                            anchors.right: parent.right
                            spacing: 6
                            AccentButton {
                                objectName: "modelsDownload_" + rowItem.r.key
                                visible: (rowItem.r.kind === "download" || rowItem.r.kind === "package") && !rowItem.here
                                         && !rowItem.r.downloading && !rowItem.r.queued && rowItem.r.access !== "request"
                                text: rowItem.r.kind === "package" ? "Install" : "Download"
                                kind: "outline"
                                iconGlyph: Theme.icon.importIcon
                                onClicked: dlg.rowError = dlg.m.start(rowItem.r.key)
                                HoverTip { tip: (rowItem.r.kind === "package" ? "About " + rowItem.r.size + " into Studio's own environment, the versions Studio was tested with"
                                                 : rowItem.r.left + " into " + (dlg.m ? dlg.m.folder : ""))
                                                + (dlg.jobRunning ? ". A job is running: it starts once that has finished." : ".") }
                            }
                            AccentButton {
                                visible: rowItem.r.queued
                                text: "Remove"
                                kind: "ghost"
                                onClicked: dlg.m.unqueue(rowItem.r.key)
                            }
                            AccentButton {
                                visible: !rowItem.here && (rowItem.r.access === "request" || rowItem.r.kind === "manual")
                                text: rowItem.r.kind === "manual" ? "Get It" : "Ask for Access"
                                kind: "outline"
                                onClicked: dlg.m.openPage(rowItem.r.key)
                                HoverTip { tip: rowItem.r.kind === "manual" ? "Opens the download page." : "Opens the model's page: ask for access there, then come back and Download." }
                            }
                            Small {
                                visible: rowItem.r.kind === "rose" && !rowItem.here
                                text: "Set Up ROSE in the editor"
                            }
                            IconButton {
                                visible: rowItem.here || rowItem.r.kind === "manual"
                                iconGlyph: Theme.icon.folder
                                size: 28
                                onClicked: { const why = dlg.m.openFolder(rowItem.r.folder); if (why) dlg.rowError = why }
                                HoverTip { tip: "Open its folder" }
                            }
                            IconButton {
                                visible: rowItem.r.page !== ""
                                iconGlyph: Theme.icon.info
                                size: 28
                                onClicked: dlg.m.openPage(rowItem.r.key)
                                HoverTip { tip: "Its page (licence, details)" }
                            }
                        }
                    }
                }
            }
        }
    }
}
