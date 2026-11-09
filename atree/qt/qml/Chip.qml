import QtQuick

Rectangle {
    id: chip
    property string text: ""
    property bool on: false
    property color dotColor: "transparent"
    property bool hasDot: false
    // agent chips: drag sideways to reorder (see AgentOrder)
    property bool draggable: false
    property bool dragging: false
    property real shift: 0
    signal clicked()
    signal dragStarted()
    signal dragMoved(real dx)
    signal dragEnded()

    height: 24
    width: label.implicitWidth + (hasDot ? 30 : 20)
    radius: 12
    color: on ? Theme.panel3 : (area.containsMouse ? Theme.panel2 : "transparent")
    border.color: on ? Theme.dim : Theme.line
    Behavior on color { ColorAnimation { duration: Theme.quick } }
    z: dragging ? 10 : 0
    transform: Translate { x: chip.shift }
    Behavior on shift {
        enabled: !chip.dragging
        NumberAnimation { duration: 140; easing.type: Easing.OutCubic }
    }

    Row {
        anchors.centerIn: parent
        spacing: 6
        Rectangle {
            visible: chip.hasDot
            width: 8
            height: 8
            radius: 4
            anchors.verticalCenter: parent.verticalCenter
            color: chip.on ? chip.dotColor : Theme.dim
        }
        Text {
            id: label
            text: chip.text
            textFormat: Text.PlainText
            font.pixelSize: 12
            color: chip.on ? Theme.text : Theme.muted
        }
    }
    DragClickArea {
        id: area
        anchors.fill: parent
        draggable: chip.draggable
        onTapped: chip.clicked()
        onDragStarted: chip.dragStarted()
        onDragMoved: dx => chip.dragMoved(dx)
        onDragEnded: chip.dragEnded()
    }
}
