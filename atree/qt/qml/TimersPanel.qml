import QtQuick

SidePanel {
    id: panel
    objectName: "timersPanel"
    readonly property var items: timers.items || []
    property string error: ""
    readonly property int unfired: {
        var n = 0
        for (var i = 0; i < items.length; i++)
            if (!items[i].fired)
                n++
        return n
    }

    title: "Timers"
    note: unfired > 0 ? unfired + " running" : (items.length > 0 ? "done" : "")
    bodyHeight: 28 + (error ? 18 : 0) + 6 + rowsCol.implicitHeight + 8

    TextBox {
        id: entry
        x: 10
        width: parent.width - 20
        enabled: !panel.folded
        placeholder: "25m tea · 1h30 · 14:30 alarm…"
        invalid: panel.error !== ""
        onEdited: panel.error = ""
        onAccepted: {
            var err = timers.add(text)
            panel.error = err || ""
            if (!err)
                text = ""
        }
        onEscaped: {
            text = ""
            panel.error = ""
            Ui.focusCanvas()
        }
    }
    Text {
        visible: panel.error !== ""
        x: 10
        y: 30
        width: parent.width - 20
        text: panel.error
        textFormat: Text.PlainText
        elide: Text.ElideRight
        font.pixelSize: 11
        color: Theme.bad
    }
    Column {
        id: rowsCol
        x: 6
        y: 28 + (panel.error ? 18 : 0) + 6
        width: parent.width - 12
        Repeater {
            model: panel.items
            Rectangle {
                id: trow
                required property var modelData
                width: rowsCol.width
                height: 24
                radius: 6
                color: modelData.fired ? Qt.rgba(0.949, 0.722, 0.294, 0.12) : (thover.hovered ? Theme.panel2 : "transparent")
                HoverHandler { id: thover }
                Text {
                    id: icon
                    x: 8
                    anchors.verticalCenter: parent.verticalCenter
                    text: trow.modelData.alarm ? "⏰" : "⏱"
                    font.pixelSize: 11
                    color: Theme.muted
                }
                Text {
                    objectName: "timerLabel"
                    anchors.left: parent.left
                    anchors.leftMargin: 28
                    anchors.right: whenText.left
                    anchors.rightMargin: 8
                    anchors.verticalCenter: parent.verticalCenter
                    text: trow.modelData.label ? trow.modelData.label : (trow.modelData.alarm ? "Alarm" : "Timer")
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    font.pixelSize: 12
                    color: trow.modelData.label ? Theme.text : Theme.muted
                }
                Text {
                    id: whenText
                    anchors.right: parent.right
                    anchors.rightMargin: 28
                    anchors.verticalCenter: parent.verticalCenter
                    text: trow.modelData.when
                    textFormat: Text.PlainText
                    font.pixelSize: 12
                    font.features: ({ "tnum": 1 })
                    font.bold: trow.modelData.fired || trow.modelData.soon
                    color: trow.modelData.fired ? Theme.warn : (trow.modelData.soon ? Theme.text : Theme.muted)
                }
                RemoveButton {
                    visible: thover.hovered || trow.modelData.fired
                    anchors.right: parent.right
                    anchors.rightMargin: 9
                    anchors.verticalCenter: parent.verticalCenter
                    onClicked: timers.remove(trow.modelData.id)
                }
            }
        }
    }
}
