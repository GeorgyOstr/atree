import QtQuick

// Scrollbar over the tree area mapping a pan range to a handle.
Item {
    id: bar
    property bool vertical: true
    property real pos: 0        // current world offset
    property real minPos: 0
    property real maxPos: 0
    property real viewLength: 1
    readonly property real range: maxPos - minPos
    readonly property bool pressed: handleArea.pressed || trackArea.pressed
    signal moved(real to)

    visible: range > 1
    readonly property real length: vertical ? height : width
    // purely proportional, no minimum
    readonly property real handleLength: length * viewLength / (viewLength + range)
    readonly property real handlePos: range > 0 ? (maxPos - pos) / range * (length - handleLength) : 0

    function posFor(offset) {
        var free = length - handleLength
        return free > 0 ? maxPos - Math.max(0, Math.min(free, offset)) / free * range : pos
    }

    MouseArea {
        id: trackArea
        anchors.fill: parent
        onPressed: mouse => {
            Ui.markTap()
            bar.moved(bar.posFor((bar.vertical ? mouse.y : mouse.x) - bar.handleLength / 2))
        }
    }
    Rectangle {
        id: handle
        x: bar.vertical ? (bar.width - 6) / 2 : bar.handlePos
        y: bar.vertical ? bar.handlePos : (bar.height - 6) / 2
        width: bar.vertical ? 6 : bar.handleLength
        height: bar.vertical ? bar.handleLength : 6
        radius: 3
        color: handleArea.pressed ? Theme.muted : Theme.dim
        opacity: handleArea.containsMouse || handleArea.pressed ? 1 : 0.55
        MouseArea {
            id: handleArea
            anchors.fill: parent
            anchors.margins: -3
            hoverEnabled: true
            property real grab: 0
            onPressed: mouse => { Ui.markTap(); grab = bar.vertical ? mouse.y : mouse.x }
            onPositionChanged: mouse => {
                if (!pressed)
                    return
                var p = mapToItem(bar, mouse.x, mouse.y)
                bar.moved(bar.posFor((bar.vertical ? p.y : p.x) - grab + 3))
            }
        }
    }
}
