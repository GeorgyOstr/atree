import QtQuick

// A click area that becomes a sideways drag handle once the press moves past a few pixels;
// a drag never counts as a click.
MouseArea {
    id: area
    property bool draggable: true
    property bool moving: false
    property real startX: 0
    signal tapped()
    signal dragStarted()
    signal dragMoved(real dx)
    signal dragEnded()

    hoverEnabled: true
    preventStealing: draggable
    cursorShape: moving ? Qt.ClosedHandCursor : Qt.PointingHandCursor
    onPressed: mouse => {
        startX = mapToItem(null, mouse.x, mouse.y).x
        moving = false
    }
    onPositionChanged: mouse => {
        if (!pressed || !draggable)
            return
        var dx = mapToItem(null, mouse.x, mouse.y).x - startX
        if (!moving && Math.abs(dx) > 6) {
            moving = true
            dragStarted()
        }
        if (moving)
            dragMoved(dx)
    }
    onReleased: if (moving) dragEnded()
    onCanceled: if (moving) dragEnded()
    onClicked: {
        Ui.markTap()
        if (!moving)
            tapped()
    }
}
