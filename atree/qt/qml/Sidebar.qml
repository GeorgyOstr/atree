import QtQuick
import QtQuick.Controls

// Scrollable column of side panels; the wheel over it never reaches the tree.
Flickable {
    id: bar
    objectName: "sidebar"
    property real room: 600
    property var view
    property alias offset: bar.contentY
    readonly property real maxOffset: Math.max(0, stack.height - height)
    signal aiCleared()

    width: activity.width + 10
    height: Math.min(stack.height, room)
    contentWidth: width
    contentHeight: stack.height
    interactive: maxOffset > 0
    boundsBehavior: Flickable.StopAtBounds
    clip: true
    onMaxOffsetChanged: offset = Math.min(offset, maxOffset)
    ScrollBar.vertical: ThinScrollBar { objectName: "sidebarScrollBar" }

    function scrollBy(dy) {
        offset = Math.max(0, Math.min(maxOffset, offset - dy))
    }

    MouseArea {
        width: bar.width
        height: bar.contentHeight
        acceptedButtons: Qt.NoButton
        onWheel: wheel => {
            var touch = wheel.phase !== Qt.NoScrollPhase && wheel.pixelDelta.y !== 0
            bar.scrollBy(touch ? wheel.pixelDelta.y : wheel.angleDelta.y / 120 * 90)
            wheel.accepted = true
        }
    }

    Column {
        id: stack
        width: activity.width
        spacing: 10

        AiPanel {
            id: aiPanel
            width: activity.width
            onCleared: bar.aiCleared()
        }
        ActivityPanel {
            id: activity
            folded: bar.view ? bar.view.activityFolded : false
            onToggled: bar.view.activityFolded = !folded
        }
        TimersPanel {
            id: timersPanel
            width: activity.width
            folded: bar.view ? bar.view.timersFolded : false
            onToggled: bar.view.timersFolded = !folded
        }
        CalendarPanel {
            id: calendarPanel
            width: activity.width
            folded: bar.view ? bar.view.calendarFolded : false
            onToggled: bar.view.calendarFolded = !folded
        }
        TasksPanel {
            id: tasksPanel
            width: activity.width
            room: bar.room
            topInStack: y
            folded: bar.view ? bar.view.tasksFolded : false
            onToggled: bar.view.tasksFolded = !folded
        }
    }
}
