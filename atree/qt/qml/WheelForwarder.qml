import QtQuick

// Over an inner list: the wheel scrolls the list first; at its ends, or while blocked, the
// wheel goes on to whatever is behind (the sidebar)
MouseArea {
    property Flickable list
    property bool blocked: false
    acceptedButtons: Qt.NoButton
    onWheel: wheel => {
        var range = list.contentHeight - list.height
        var touch = wheel.phase !== Qt.NoScrollPhase && wheel.pixelDelta.y !== 0
        var dy = touch ? wheel.pixelDelta.y : wheel.angleDelta.y / 120 * 90
        var to = Math.max(0, Math.min(range, list.contentY - dy))
        if (blocked || range <= 0 || Math.abs(to - list.contentY) < 0.5) {
            wheel.accepted = false
            return
        }
        list.contentY = to
    }
}
