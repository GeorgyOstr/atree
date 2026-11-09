import QtCore
import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs

ApplicationWindow {
    id: win
    objectName: "mainWindow"
    width: 1500
    height: 950
    minimumWidth: 900
    minimumHeight: 500
    visible: true
    title: desktopWidget ? "atree Desktop" : "atree"
    color: desktopWidget ? "transparent" : Theme.bg
    flags: desktopWidget ? (Qt.Window | Qt.FramelessWindowHint) : Qt.Window

    property bool pickerOpen: false
    // widget mode: Desktop Icons NG's icon block is click-through (see widget.py); keep controls out of it
    property real holeLeft: 0
    property real holeRight: 0
    property real holeTop: 0
    property real holeRightTop: 0
    // icons low on the right: shorten the side panels to end above them instead of moving
    // them aside, so the layout keeps its full width
    readonly property bool iconsBelowPanels: holeRight > 0 && holeRightTop - 66 > 300
    readonly property real panelShift: iconsBelowPanels ? 0 : holeRight
    // widget mode: clicking the logo fades atree and hands every click but the logo's to the
    // desktop (drag-select, moving icons); widget.py sets maskHelper and shapes the input region
    property var maskHelper: null
    readonly property bool desktopPass: desktopWidget && viewSettings.desktopPass
    readonly property real passFade: desktopPass ? 0.15 : 1
    readonly property rect passRect: {
        header.width; header.height; header.leftInset   // re-map when the header moves
        var p = header.logoIcon.mapToItem(win.contentItem, 0, 0)
        return Qt.rect(p.x - 4, p.y - 4, header.logoIcon.width + 8, header.logoIcon.height + 8)
    }
    function toggleDesktopPass() { viewSettings.desktopPass = !viewSettings.desktopPass }
    onDesktopPassChanged: if (maskHelper) Qt.callLater(maskHelper.refresh)
    onPassRectChanged: if (maskHelper) Qt.callLater(maskHelper.refresh)

    Settings {
        id: viewSettings
        objectName: "viewSettings"
        category: "view"
        property real x: 30
        property real y: 24
        property real scale: 1
        property bool saved: false
        property bool activityFolded: false
        property bool tasksFolded: false
        property bool timersFolded: false
        property bool calendarFolded: false
        property bool desktopPass: false
    }

    Component.onCompleted: {
        Ui.app = win
        Ui.tip = sharedTip
        Ui.canvas = tree
        tree.forceActiveFocus()
    }

    onActiveChanged: if (!active) Ui.focusCanvas()

    Connections {
        target: backend
        function onToast(message, isError) { toast.show(message, isError, []) }
        function onUndoable(message, keys, isError) { toast.show(message, isError, keys) }
    }

    Shortcut {
        sequences: ["/", "Ctrl+F"]
        onActivated: header.searchField.focusField()
    }
    Shortcut {
        sequence: "Ctrl+0"
        onActivated: tree.resetZoom()
    }

    // window-wide, whatever non-text item has focus; never while typing or in a menu/dialog
    readonly property bool typing: activeFocusItem !== null && activeFocusItem.cursorPosition !== undefined
    readonly property bool keysFree: !typing && !tree.popupsOpen
    readonly property var current: backend.details
    readonly property bool hasCurrent: current.key !== undefined

    Shortcut {
        sequence: "F"
        enabled: win.keysFree
        onActivated: tree.fit(true)
    }
    Shortcut {
        sequence: "Esc"
        enabled: win.keysFree
        onActivated: {
            backend.clearSelection()
            Ui.focusCanvas()
        }
    }
    Shortcut {
        sequence: "A"
        enabled: win.keysFree && win.hasCurrent
        onActivated: win.archiveKeys(backend.actionKeys(win.current.key))
    }
    Shortcut {
        sequence: "M"
        enabled: win.keysFree && win.hasCurrent
        onActivated: win.openMoveDialog(backend.actionKeys(win.current.key), "")
    }
    Shortcut {
        sequence: "C"
        enabled: win.keysFree && win.hasCurrent
        onActivated: backend.copyResume(win.current.key)
    }
    Shortcut {
        sequences: ["Return", "Enter"]
        enabled: win.keysFree && win.hasCurrent && !win.current.archived
        onActivated: backend.resume(win.current.key)
    }

    // ---------------------------------------------------------------- actions

    function openTalkMenu(key, item, px, py) {
        var keys = backend.actionKeys(key)
        var info = backend.describe(keys)
        if (info.length === 0)
            return
        var anyArchived = false, anyLive = false
        for (var i = 0; i < info.length; i++) {
            anyArchived = anyArchived || info[i].archived
            anyLive = anyLive || info[i].live
        }
        var single = info.length === 1
        var t = info[0]
        talkMenu.popupAt(item, px, py, single ? t.title : info.length + " talks selected", [
            { text: "Resume in terminal", hint: "Enter", visible: single && !t.archived, run: function () { backend.resume(t.key) } },
            { text: "Copy resume command", hint: "C", visible: single && !t.archived, run: function () { backend.copyResume(t.key) } },
            { text: "Move to folder…", hint: "M", visible: !anyArchived, run: function () { win.openMoveDialog(keys, "") } },
            { text: "Archive (compress)", hint: "A", danger: true, visible: !anyArchived && !anyLive, run: function () { win.archiveKeys(keys) } },
            { text: "Restore from archive", visible: anyArchived, run: function () { backend.restore(keys) } },
            { text: "Delete permanently…", danger: true, visible: anyArchived, run: function () { win.openDeleteDialog(keys) } }
        ])
    }

    function openFolderMenu(path, item, px, py) {
        folderMenu.popupAt(item, px, py, backend.tildify(path), [
            { text: "Unfold everything inside", run: function () { backend.setAllUnder(path, false) } },
            { text: "Fold everything inside", run: function () { backend.setAllUnder(path, true) } },
            { text: "Select all talks here", run: function () { backend.selectFolder(path) } },
            { text: "Open in file manager", run: function () { backend.openFolder(path) } },
            { text: "Copy path", run: function () { backend.copyText(path) } }
        ])
    }

    function openMoveDialog(keys, target) {
        var info = backend.describe(keys).filter(function (t) { return !t.archived })
        if (info.length === 0)
            return
        moveDialog.talks = info
        moveDialog.keys = info.map(function (t) { return t.key })
        moveDialog.anyLive = info.some(function (t) { return t.live })
        moveDialog.title = "Move " + Theme.plural(info.length, "talk") + " to another folder"
        pathBox.text = target ? target : info[0].cwd
        moveDialog.open()
        pathBox.focusAll()
    }

    function openDeleteDialog(keys) {
        var info = backend.describe(keys).filter(function (t) { return t.archived })
        if (info.length === 0) {
            toast.show("archive a talk before deleting it", true, [])
            return
        }
        deleteDialog.talks = info
        deleteDialog.keys = info.map(function (t) { return t.key })
        deleteDialog.title = "Delete " + info.length + " archived " + (info.length === 1 ? "talk" : "talks")
        deleteDialog.open()
    }

    function archiveKeys(keys) {
        backend.archive(keys)
    }

    // ---------------------------------------------------------------- layout

    Header {
        id: header
        z: 10
        width: parent.width
        faded: win.desktopPass
        leftInset: win.holeTop < 66 ? win.holeLeft : 0
        rightInset: win.holeTop < 66 ? win.holeRight : 0
        onFitRequested: tree.fit(true)
    }

    Item {
        id: canvasArea
        y: header.height
        width: parent.width
        height: parent.height - header.height
        opacity: win.passFade
        Behavior on opacity { NumberAnimation { duration: Theme.slow } }

        Rectangle {
            anchors.fill: parent
            color: desktopWidget ? Qt.rgba(0.059, 0.071, 0.090, 0.55) : Theme.bg
        }

        TreeCanvas {
            id: tree
            anchors.fill: parent
            anchors.leftMargin: win.holeLeft
            treeW: sidebar.x - 12 - win.holeLeft
            view: viewSettings
            popupsOpen: talkMenu.opened || folderMenu.opened || moveDialog.opened
                        || deleteDialog.opened || win.pickerOpen
        }

        Sidebar {
            id: sidebar
            z: 5
            x: parent.width - width - 8 - win.panelShift
            y: 12
            room: win.iconsBelowPanels ? win.holeRightTop - canvasArea.y - 24 : parent.height - 24
            view: viewSettings
            onAiCleared: {
                header.searchField.clearAll()
            }
        }

        DetailsPanel {
            id: details
            z: 8
            y: 12
            height: win.iconsBelowPanels ? win.holeRightTop - canvasArea.y - 24 : parent.height - 24
            x: shown ? parent.width - width - 12 - win.panelShift : parent.width + 20
            Behavior on x { NumberAnimation { duration: Theme.slow; easing.type: Easing.OutCubic } }
        }

        Toast {
            id: toast
            z: 20
            maxWidth: win.width * 0.7
            x: (parent.width - width) / 2
            y: parent.height - 46 - height
        }
    }

    SharedTip { id: sharedTip }

    PopupMenu { id: talkMenu; objectName: "talkMenu" }
    PopupMenu { id: folderMenu; objectName: "folderMenu" }

    TalkDialog {
        id: moveDialog
        objectName: "moveDialog"
        property var keys: []
        property bool anyLive: false
        okText: "Move"
        okEnabled: backend.isDir(pathBox.text)
        onAcceptedOk: backend.move(keys, backend.expand(pathBox.text))

        Text {
            width: parent.width
            wrapMode: Text.Wrap
            font.pixelSize: 12
            color: Theme.muted
            text: "The working directory inside each transcript is rewritten, so the agent's resume command finds it from the new folder. Originals are backed up to ~/.local/share/atree/backups."
        }
        Text {
            visible: moveDialog.anyLive
            text: "Running talks will be skipped."
            font.pixelSize: 12
            color: Theme.bad
        }
        Row {
            spacing: 8
            width: parent.width
            TextBox {
                id: pathBox
                objectName: "movePath"
                width: parent.width - browse.width - 8
                height: 30
                fontSize: 13
                focusAccent: false
                invalid: !backend.isDir(text)
                onAccepted: moveDialog.accept()
                onEscaped: moveDialog.close()
            }
            HButton {
                id: browse
                height: 30
                text: "Browse…"
                onClicked: {
                    win.pickerOpen = true
                    picker.active = true
                }
            }
        }
    }

    TalkDialog {
        id: deleteDialog
        objectName: "deleteDialog"
        property var keys: []
        okText: "Delete"
        onAcceptedOk: backend.deleteArchived(keys)
        Text {
            text: "The archive files are deleted for good. This can't be undone."
            font.pixelSize: 12
            color: Theme.bad
        }
    }

    Loader {
        id: picker
        active: false
        sourceComponent: FolderDialog {
            title: "Move talks to…"
            currentFolder: backend.localFileUrl(pathBox.text)
            onAccepted: {
                pathBox.text = backend.localFilePath(selectedFolder)
                win.pickerOpen = false
                picker.active = false
            }
            onRejected: {
                win.pickerOpen = false
                picker.active = false
            }
        }
        onLoaded: item.open()
    }
}
