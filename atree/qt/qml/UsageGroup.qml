import QtQuick

Column {
    id: group
    objectName: "usageGroup"
    property var data_: ({})
    // drag the name sideways to reorder (see AgentOrder)
    property bool dragging: false
    property real shift: 0
    signal dragStarted()
    signal dragMoved(real dx)
    signal dragEnded()
    spacing: 3
    z: dragging ? 10 : 0
    transform: Translate { x: group.shift }
    Behavior on shift {
        enabled: !group.dragging
        NumberAnimation { duration: 140; easing.type: Easing.OutCubic }
    }

    Text {
        text: String(group.data_.name || "").toUpperCase() + "  ✦"
        textFormat: Text.PlainText
        font.pixelSize: 11
        font.bold: true
        font.letterSpacing: 0.6
        font.underline: nameArea.containsMouse
        color: Theme.toolColor(group.data_.key)
        DragClickArea {
            id: nameArea
            objectName: "usageName"
            anchors.fill: parent
            onTapped: backend.openAssistant(group.data_.key)
            onDragStarted: { Ui.hideTip(parent); group.dragStarted() }
            onDragMoved: dx => group.dragMoved(dx)
            onDragEnded: group.dragEnded()
            onContainsMouseChanged: containsMouse && !moving
                ? Ui.showTip(parent, "Talk to the atree assistant (" + group.data_.name + ") about cleanup:\nopens a terminal, continuing your last chat", 500)
                : Ui.hideTip(parent)
        }
    }
    Text {
        visible: text !== ""
        width: 220
        text: group.data_.error || ""
        textFormat: Text.PlainText
        elide: Text.ElideRight
        font.pixelSize: 11
        font.italic: true
        color: Theme.dim
    }
    // bar rows share the group's 3 px line spacing; 8 px is the gap inside a row
    Repeater {
        model: group.data_.bars || []
        Row {
            id: bar
            objectName: "usageBar"
            required property var modelData
            spacing: 8
            Text {
                width: 74
                text: bar.modelData.label
                textFormat: Text.PlainText
                font.pixelSize: 12
                color: Theme.muted
                anchors.verticalCenter: parent.verticalCenter
            }
            Item {
                width: 110
                height: 12
                anchors.verticalCenter: parent.verticalCenter
                Rectangle {
                    id: track
                    anchors.verticalCenter: parent.verticalCenter
                    width: parent.width
                    height: 8
                    radius: 4
                    color: Theme.bg
                    border.color: Theme.line
                    Rectangle {
                        height: parent.height
                        radius: 4
                        width: parent.width * bar.modelData.left / 100
                        color: Theme.usageColor(bar.modelData.left)
                        Behavior on width { NumberAnimation { duration: 600; easing.type: Easing.OutCubic } }
                    }
                }
                Rectangle {
                    visible: bar.modelData.pace >= 0
                    x: bar.modelData.pace * track.width - 1
                    anchors.verticalCenter: parent.verticalCenter
                    width: 2
                    height: 12
                    color: Qt.rgba(0.902, 0.914, 0.941, 0.75)
                }
                MouseArea {
                    anchors.fill: parent
                    hoverEnabled: true
                    onContainsMouseChanged: containsMouse
                        ? Ui.showTip(parent, bar.modelData.used + "% used · white tick = even pace for this window"
                                     + (group.data_.plan ? "\nplan: " + group.data_.plan : ""), 500)
                        : Ui.hideTip(parent)
                }
            }
            Text {
                width: 58
                text: bar.modelData.left + "% left"
                font.pixelSize: 12
                color: Theme.text
                anchors.verticalCenter: parent.verticalCenter
            }
            Text {
                text: bar.modelData.resets
                textFormat: Text.PlainText
                font.pixelSize: 11
                color: Theme.dim
                anchors.verticalCenter: parent.verticalCenter
            }
        }
    }
}
