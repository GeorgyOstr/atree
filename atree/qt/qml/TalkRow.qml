import QtQuick

Item {
    id: row
    objectName: "talkRow"
    required property string uid
    required property string key
    required property string title
    required property string meta
    required property string tool
    required property bool live
    required property bool busy
    required property bool archived
    required property bool selected
    required property bool match
    required property bool old
    required property int depth
    required property string tip

    readonly property bool placeholder: key === ""
    readonly property bool hovered: hover.hovered && !placeholder
    readonly property real dotSize: depth > 0 ? 6 : 9

    width: ListView.view ? ListView.view.width : 0
    height: Theme.rowH
    enabled: !placeholder

    HoverHandler {
        id: hover
        cursorShape: drag.active ? Qt.ClosedHandCursor : Qt.PointingHandCursor
    }

    Rectangle {
        x: 4
        width: parent.width - 8
        height: parent.height
        radius: 6
        color: row.selected ? Theme.selection : (row.hovered ? Theme.panel3 : "transparent")
        Behavior on color { ColorAnimation { duration: Theme.quick } }
        Rectangle {
            visible: row.selected
            width: 3
            height: parent.height - 8
            anchors.verticalCenter: parent.verticalCenter
            radius: 1.5
            color: Theme.accent
        }
    }

    Item {
        id: dot
        x: 14 + row.depth * 16
        width: row.dotSize
        height: row.dotSize
        anchors.verticalCenter: parent.verticalCenter
        visible: !row.placeholder
        Rectangle {
            anchors.fill: parent
            radius: width / 2
            color: row.archived ? "transparent" : Theme.toolColor(row.tool)
            border.width: row.archived ? 1.5 : 0
            border.color: Theme.toolColor(row.tool)
        }
        LiveDot {
            visible: row.live
            size: row.dotSize
            busy: row.busy
            opacity: 0.9
        }
    }

    Text {
        id: titleText
        x: dot.x + row.dotSize + 8
        width: (row.hovered ? actions.x : metaText.x) - x - 8
        anchors.verticalCenter: parent.verticalCenter
        text: row.title
        textFormat: Text.PlainText
        elide: Text.ElideRight
        font.pixelSize: 13
        font.italic: row.archived
        color: row.placeholder ? Theme.dim
             : row.match ? Theme.warn
             : row.archived ? Theme.muted
             : row.old ? Theme.stale : Theme.text
    }

    Text {
        id: metaText
        anchors.right: parent.right
        anchors.rightMargin: 12
        anchors.verticalCenter: parent.verticalCenter
        width: Math.min(implicitWidth, row.width * 0.45)
        horizontalAlignment: Text.AlignRight
        text: row.meta
        textFormat: Text.PlainText
        elide: Text.ElideRight
        font.pixelSize: 11
        color: row.live ? Theme.live : Theme.dim
        opacity: row.hovered ? 0 : 1
        Behavior on opacity { NumberAnimation { duration: Theme.quick } }
    }

    MouseArea {
        id: area
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        hoverEnabled: true
        onPressed: Ui.markTap()
        onClicked: mouse => {
            Ui.markTap()
            Ui.hideTip(row)
            var additive = (mouse.modifiers & (Qt.ControlModifier | Qt.ShiftModifier)) !== 0
            if (mouse.button === Qt.RightButton) {
                if (!row.selected)
                    backend.select(row.key, false)
                Ui.app.openTalkMenu(row.key, row, mouse.x, mouse.y)
            } else {
                backend.select(row.key, additive)
            }
        }
        onDoubleClicked: mouse => {
            Ui.markTap()
            if (mouse.button === Qt.LeftButton && !row.archived)
                backend.resume(row.key)
        }
        onContainsMouseChanged: {
            if (containsMouse && row.tip && !Ui.canvas.dragging)
                Ui.showTip(row, row.tip, 900)
            else
                Ui.hideTip(row)
        }
    }

    DragHandler {
        id: drag
        target: null
        enabled: !row.archived && !row.placeholder
        dragThreshold: 6
        onActiveChanged: {
            Ui.hideTip(row)
            if (active)
                Ui.canvas.startDrag(backend.actionKeys(row.key), row.title)
            else
                Ui.canvas.finishDrag()
        }
        onCentroidChanged: if (active) Ui.canvas.dragTo(centroid.scenePosition.x, centroid.scenePosition.y)
    }

    Row {
        id: actions
        anchors.right: parent.right
        anchors.rightMargin: 8
        anchors.verticalCenter: parent.verticalCenter
        spacing: 2
        opacity: row.hovered ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.quick } }

        MiniButton {
            visible: !row.archived
            height: 20; width: 22
            text: "▶"; tip: "Resume in terminal"
            onClicked: backend.resume(row.key)
        }
        MiniButton {
            visible: !row.archived
            height: 20; width: 22
            text: "⧉"; tip: "Copy resume command"
            onClicked: backend.copyResume(row.key)
        }
        MiniButton {
            visible: !row.archived
            height: 20; width: 22
            text: "↦"; tip: "Move to folder…"
            onClicked: Ui.app.openMoveDialog(backend.actionKeys(row.key), "")
        }
        MiniButton {
            visible: !row.archived && !row.live
            height: 20; width: 22
            text: "⤓"; tip: "Archive (compress)"
            onClicked: Ui.app.archiveKeys(backend.actionKeys(row.key))
        }
        MiniButton {
            visible: row.archived
            height: 20; width: 22
            text: "⤒"; tip: "Restore from archive"
            onClicked: backend.restore(backend.actionKeys(row.key))
        }
        MiniButton {
            visible: row.archived
            height: 20; width: 22
            text: "✕"; tip: "Delete permanently"
            onClicked: Ui.app.openDeleteDialog(backend.actionKeys(row.key))
        }
        MiniButton {
            id: moreBtn
            height: 20; width: 22
            text: "⋯"; tip: "More"
            onClicked: Ui.app.openTalkMenu(row.key, moreBtn, 0, moreBtn.height)
        }
    }
}
