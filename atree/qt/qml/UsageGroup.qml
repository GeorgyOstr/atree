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

    // name row: the week's tokens and, with several accounts, a legend (dot + name) and one
    // 3 px credits graph with a series per account (the row's height doesn't change)
    Row {
    spacing: 10
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
        objectName: "usageTokens"
        visible: text !== ""
        text: group.data_.tokens || ""
        textFormat: Text.PlainText
        font.pixelSize: 11
        color: Theme.text
        anchors.verticalCenter: parent.verticalCenter
        HoverHandler {
            onHoveredChanged: hovered ? Ui.showTip(parent, "tokens spent over the last 7 days (cache reads included)", 500)
                                      : Ui.hideTip(parent)
        }
    }
    Repeater {
        model: group.data_.accounts || []
        Row {
            id: acct
            objectName: "usageAccount"
            required property var modelData
            spacing: 4
            anchors.verticalCenter: parent.verticalCenter
            Rectangle {
                width: 6; height: 6; radius: 3
                color: Theme.accountColor(acct.modelData.slot)
                anchors.verticalCenter: parent.verticalCenter
            }
            Text {
                text: acct.modelData.account
                textFormat: Text.PlainText
                font.pixelSize: 10
                color: Theme.muted
                anchors.verticalCenter: parent.verticalCenter
            }
        }
    }
    Row {
        id: credit
        objectName: "creditGraph"
        readonly property var data_: group.data_.credit || null
        visible: data_ !== null
        spacing: 5
        anchors.verticalCenter: parent.verticalCenter
        Rectangle {
            width: 44; height: 3; radius: 1.5
            color: Theme.bg
            border.color: Theme.line
            anchors.verticalCenter: parent.verticalCenter
            Repeater {
                model: credit.data_ ? credit.data_.series : []
                Rectangle {
                    required property var modelData
                    width: 44 * modelData.frac
                    height: 3
                    radius: 1.5
                    color: Theme.accountColor(modelData.slot)
                    opacity: credit.data_.series.length > 1 ? 0.55 : 1
                }
            }
        }
        Text {
            text: credit.data_ ? credit.data_.text : ""
            textFormat: Text.PlainText
            font.pixelSize: 10
            color: credit.data_ && credit.data_.full ? Theme.bad : Theme.text
            anchors.verticalCenter: parent.verticalCenter
        }
        HoverHandler {
            onHoveredChanged: hovered && credit.data_ ? Ui.showTip(credit, credit.data_.tip, 500) : Ui.hideTip(credit)
        }
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
            // one series per account; a single account keeps the green/amber/red fill
            readonly property var series: modelData.series || [modelData]
            readonly property bool multi: series.length > 1
            function colorOf(s) { return multi ? Theme.accountColor(s.slot) : Theme.usageColor(s.left) }
            function tipText() {
                return (group.data_.asof ? group.data_.asof + "\n" : "") + seriesTip()
            }
            function seriesTip() {
                if (!multi)
                    return modelData.used + "% used" + (modelData.resets ? " · " + modelData.resets : "")
                        + "\nwhite tick = even pace for this window"
                        + (group.data_.plan ? "\nplan: " + group.data_.plan : "")
                return series.map(s => s.account + ": " + s.used + "% used"
                                  + (s.resets ? " · " + s.resets : "")).join("\n")
                    + "\n" + modelData.left + "% left on average · ticks = even pace per account"
            }
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
                    // translucent fills overlap: where both accounts still have room the colours mix
                    Repeater {
                        model: bar.series
                        Rectangle {
                            required property var modelData
                            height: track.height
                            radius: 4
                            width: track.width * modelData.left / 100
                            color: bar.colorOf(modelData)
                            opacity: bar.multi ? 0.55 : 1
                            Behavior on width { NumberAnimation { duration: 600; easing.type: Easing.OutCubic } }
                        }
                    }
                }
                Repeater {
                    model: bar.series
                    Rectangle {
                        required property var modelData
                        visible: modelData.pace >= 0
                        x: modelData.pace * track.width - 1
                        anchors.verticalCenter: parent.verticalCenter
                        width: 2
                        height: 12
                        color: bar.multi ? Theme.accountColor(modelData.slot) : Qt.rgba(0.902, 0.914, 0.941, 0.75)
                    }
                }
                MouseArea {
                    anchors.fill: parent
                    hoverEnabled: true
                    onContainsMouseChanged: containsMouse ? Ui.showTip(parent, bar.tipText(), 500) : Ui.hideTip(parent)
                }
            }
            Text {
                width: 58
                text: bar.modelData.left + "% left"
                font.pixelSize: 12
                color: Theme.text
                anchors.verticalCenter: parent.verticalCenter
            }
        }
    }
}
