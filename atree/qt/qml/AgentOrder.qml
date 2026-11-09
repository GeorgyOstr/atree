import QtQuick

// Drag-to-reorder for a row of agents (filter chips, usage blocks): the dragged item follows
// the pointer, the others shift to make room, it settles into its slot, then the backend
// stores the new order, which both rows follow. Same feel as the task list.
Item {
    id: order
    property Repeater repeater
    property var names: []          // agent names, in the repeater's order
    property real spacing: 0
    property int dragFrom: -1
    property int dragTo: -1
    property real dragDx: 0
    property real dragW: 0
    property bool settling: false
    visible: false

    onNamesChanged: cancel()

    function cancel() {
        settle.stop()
        dragFrom = -1
        dragTo = -1
        dragDx = 0
        settling = false
    }

    function itemAt(i) { return repeater.itemAt(i) }

    function begin(i) {
        cancel()
        dragFrom = i
        dragTo = i
        dragW = itemAt(i).width + spacing
    }

    function move(dx) {
        var me = dragFrom >= 0 ? itemAt(dragFrom) : null
        if (settling || !me)
            return
        var first = itemAt(0), last = itemAt(repeater.count - 1)
        dragDx = Math.max(first.x - me.x, Math.min(last.x + last.width - me.x - me.width, dx))
        dragTo = dropIndex()
    }

    function dropIndex() {
        // a neighbour is passed once the dragged item's leading edge crosses its middle
        // (items differ in width, so middles alone could leave the last slot out of reach)
        var me = itemAt(dragFrom)
        var left = me.x + dragDx, right = left + me.width
        var to = dragFrom
        for (var j = 0; j < repeater.count; j++) {
            var r = itemAt(j), mid = r.x + r.width / 2
            if (j < dragFrom && left < mid)
                return j
            if (j > dragFrom && right > mid)
                to = j + 1
        }
        return to
    }

    function shiftFor(i) {
        if (dragFrom < 0)
            return 0
        if (i === dragFrom)
            return dragDx
        if (dragTo > dragFrom && i > dragFrom && i < dragTo)
            return -dragW
        if (dragTo < dragFrom && i >= dragTo && i < dragFrom)
            return dragW
        return 0
    }

    function slotOffset() {
        // where the dragged item lands, relative to its own x
        var me = itemAt(dragFrom)
        if (!me)
            return 0
        if (dragTo > dragFrom) {
            var last = itemAt(dragTo - 1)
            return last.x + last.width - me.width - me.x
        }
        if (dragTo < dragFrom)
            return itemAt(dragTo).x - me.x
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
        var list = names.slice()
        cancel()
        if (from < 0 || to === from || to === from + 1)
            return
        var name = list.splice(from, 1)[0]
        list.splice(to > from ? to - 1 : to, 0, name)
        backend.reorderAgents(list)
    }

    SequentialAnimation {
        id: settle
        NumberAnimation { id: settleAnim; target: order; property: "dragDx"; duration: 140; easing.type: Easing.OutCubic }
        ScriptAction { script: order.commit() }
    }
}
