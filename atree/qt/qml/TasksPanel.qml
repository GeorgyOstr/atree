import QtQuick
import QtQuick.Controls

SidePanel {
    id: panel
    objectName: "tasksPanel"
    readonly property var tasks: backend.tasks || []
    property real room: 600
    property real topInStack: 0
    readonly property real maxHeight: Math.max(Math.min(400, room), room - topInStack)
    readonly property int openCount: {
        var n = 0
        for (var i = 0; i < tasks.length; i++)
            if (!tasks[i].done)
                n++
        return n
    }

    // reorder drag state
    property int dragFrom: -1
    property int dragTo: -1
    property real dragDy: 0
    property real dragH: 0
    property bool settling: false

    title: "Tasks"
    note: tasks.length === 0 ? "" : (openCount > 0 ? openCount + " open" : "all done")
    action: openCount < tasks.length ? "clear done" : ""
    onActionClicked: backend.clearDoneTasks()
    bodyHeight: Math.min(maxHeight, 32 + 34 + listCol.height + 10) - 32

    Connections {
        target: backend
        function onTasksChanged() { panel.cancelDrag() }
    }

    function cancelDrag() {
        settle.stop()
        dragFrom = -1
        dragTo = -1
        dragDy = 0
        settling = false
    }

    function rowAt(i) { return rep.itemAt(i) }

    function dropIndex() {
        var me = rowAt(dragFrom)
        if (!me)
            return dragFrom
        var mid = me.y + dragDy + me.height / 2
        for (var j = 0; j < rep.count; j++) {
            if (j === dragFrom)
                continue
            var r = rowAt(j)
            if (r.y + r.height / 2 > mid || (j === 0 && r.y + r.height / 2 >= mid))
                return j
        }
        return rep.count
    }

    function shiftFor(i) {
        if (dragFrom < 0 || i === dragFrom)
            return 0
        if (dragTo > dragFrom && i > dragFrom && i < dragTo)
            return -dragH
        if (dragTo < dragFrom && i >= dragTo && i < dragFrom)
            return dragH
        return 0
    }

    function slotOffset() {
        // where the dragged row lands, relative to its original y
        var me = rowAt(dragFrom)
        if (!me)
            return 0
        if (dragTo > dragFrom) {
            var last = rowAt(dragTo - 1)
            return last.y + last.height - me.height - me.y
        }
        if (dragTo < dragFrom)
            return rowAt(dragTo).y - me.y
        return 0
    }

    function release() {
        if (dragFrom < 0)
            return
        settling = true
        settleAnim.to = slotOffset()
        settle.start()
    }

    function commit() {
        var from = dragFrom, to = dragTo
        var id = tasks[from] ? tasks[from].id : ""
        cancelDrag()
        if (id && to !== from && to !== from + 1)
            backend.moveTask(id, to)
    }

    SequentialAnimation {
        id: settle
        NumberAnimation { id: settleAnim; target: panel; property: "dragDy"; duration: 140; easing.type: Easing.OutCubic }
        ScriptAction { script: panel.commit() }
    }

    TextBox {
        id: entry
        objectName: "taskEntry"
        x: 10
        width: parent.width - 20
        enabled: !panel.folded
        placeholder: "Add a task…"
        onAccepted: {
            backend.addTask(text)
            text = ""
        }
        onEscaped: {
            text = ""
            Ui.focusCanvas()
        }
    }

    Flickable {
        id: flick
        objectName: "taskList"
        y: 34
        x: 6
        width: parent.width - 12
        height: Math.max(0, parent.height - 34 - 10)
        contentHeight: listCol.height
        interactive: contentHeight > height + 1 && panel.dragFrom < 0
        boundsBehavior: Flickable.StopAtBounds
        clip: true
        ScrollBar.vertical: ThinScrollBar {
            parent: flick.parent
            x: flick.x + flick.width - width + 4
            y: flick.y
            height: flick.height
        }

        Column {
            id: listCol
            width: flick.width
            Repeater {
                id: rep
                model: panel.tasks
                TaskRow {
                    required property var modelData
                    required property int index
                    task: modelData
                    width: listCol.width
                    dragging: panel.dragFrom === index
                    anyDrag: panel.dragFrom >= 0
                    shift: panel.dragFrom === index ? panel.dragDy : panel.shiftFor(index)
                    onGripPressed: {
                        panel.cancelDrag()
                        panel.dragFrom = index
                        panel.dragTo = index
                        panel.dragH = height
                    }
                    onGripMoved: dy => {
                        if (panel.settling)
                            return
                        var top = -y
                        var bottom = listCol.height - y - height
                        panel.dragDy = Math.max(top, Math.min(bottom, dy))
                        panel.dragTo = panel.dropIndex()
                    }
                    onGripReleased: panel.release()
                }
            }
        }
    }

    MouseArea {
        anchors.fill: flick
        acceptedButtons: Qt.NoButton
        onWheel: wheel => {
            var range = flick.contentHeight - flick.height
            var touch = wheel.phase !== Qt.NoScrollPhase && wheel.pixelDelta.y !== 0
            var dy = touch ? wheel.pixelDelta.y : wheel.angleDelta.y / 120 * 90
            var to = Math.max(0, Math.min(range, flick.contentY - dy))
            if (panel.dragFrom >= 0 || range <= 0 || Math.abs(to - flick.contentY) < 0.5) {
                wheel.accepted = false
                return
            }
            flick.contentY = to
        }
    }
}
