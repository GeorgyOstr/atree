import QtQuick

Item {
    id: row
    property var task: ({})
    property bool dragging: false
    property bool anyDrag: false
    property real shift: 0
    readonly property bool done: task.done === true
    signal gripPressed()
    signal gripMoved(real dy)
    signal gripReleased()

    height: Math.max(26, textItem.implicitHeight + 10)
    z: dragging ? 10 : 0
    transform: Translate { y: row.shift }
    Behavior on shift {
        enabled: !row.dragging
        NumberAnimation { duration: 140; easing.type: Easing.OutCubic }
    }

    HoverHandler { id: hover }
    readonly property bool hovered: hover.hovered && !anyDrag

    Rectangle {
        anchors.fill: parent
        radius: 6
        color: row.dragging ? Theme.panel3 : (row.hovered ? Theme.panel2 : "transparent")
        border.color: row.dragging ? Theme.line : "transparent"
    }

    Text {
        id: grip
        visible: row.hovered || row.dragging
        x: 2
        y: 4
        text: "⠿"
        font.pixelSize: 12
        color: Theme.muted
        MouseArea {
            anchors.fill: parent
            anchors.margins: -3
            preventStealing: true
            cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
            property real startY: 0
            property bool moving: false
            onPressed: mouse => {
                Ui.markTap()
                startY = mapToItem(null, mouse.x, mouse.y).y
                moving = false
            }
            onPositionChanged: mouse => {
                var dy = mapToItem(null, mouse.x, mouse.y).y - startY
                if (!moving && Math.abs(dy) > 2) {
                    moving = true
                    row.gripPressed()
                }
                if (moving)
                    row.gripMoved(dy)
            }
            onReleased: if (moving) row.gripReleased()
            onCanceled: if (moving) row.gripReleased()
        }
    }

    Rectangle {
        id: box
        objectName: "taskBox"
        x: 18
        y: 6
        width: 14
        height: 14
        radius: 4
        color: row.done ? Theme.live : "transparent"
        border.color: row.done ? Theme.live : (boxArea.containsMouse ? Theme.text : Theme.muted)
        Text {
            anchors.centerIn: parent
            visible: row.done
            text: "✓"
            font.pixelSize: 10
            font.bold: true
            color: Theme.bg
        }
        MouseArea {
            id: boxArea
            anchors.fill: parent
            anchors.margins: -5
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: { Ui.markTap(); backend.setTaskDone(row.task.id, !row.done) }
        }
    }

    Text {
        id: textItem
        objectName: "taskText"
        x: box.x + box.width + 8
        y: 5
        width: parent.width - x - 26
        visible: !editor.visible
        text: row.task.text || ""
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        font.pixelSize: 12
        font.strikeout: row.done
        color: row.done ? Theme.dim : Theme.text
        MouseArea {
            anchors.fill: parent
            onPressed: Ui.markTap()
            onDoubleClicked: {
                editor.text = row.task.text || ""
                editor.visible = true
                editor.focusAll()
            }
        }
    }

    TextBox {
        // the side panels' text box, single line, 4 px left of the text
        id: editor
        objectName: "taskEditor"
        visible: false
        x: textItem.x - 4
        y: 2
        width: textItem.width + 4
        height: 24
        function finish(save) {
            if (!visible)
                return
            visible = false
            if (save)
                backend.editTask(row.task.id, text)
        }
        onAccepted: { finish(true); Ui.focusCanvas() }
        onEscaped: { finish(false); Ui.focusCanvas() }
        onHasFocusChanged: if (!hasFocus) finish(true)
    }

    RemoveButton {
        visible: row.hovered
        anchors.right: parent.right
        anchors.rightMargin: 8
        y: 5
        onClicked: backend.removeTask(row.task.id)
    }
}
