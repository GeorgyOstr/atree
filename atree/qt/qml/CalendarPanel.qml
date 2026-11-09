import QtQuick

SidePanel {
    id: panel
    objectName: "calendarPanel"
    readonly property var events: agenda.events || []
    readonly property var calendars: agenda.calendars || []
    property bool editing: false
    property real maxHeight: 360
    property bool wasAdding: false

    readonly property string nextTime: {
        for (var i = 0; i < events.length; i++) {
            var e = events[i]
            if (!e.head && !e.now && e.time !== "all day")
                return e.time
        }
        return ""
    }

    title: "Calendar"
    note: nextTime ? "next " + nextTime : ""
    action: editing ? "done" : (calendars.length > 0 ? "edit" : "+ add")
    bodyHeight: Math.min(maxHeight, 32 + inner.implicitHeight + 10) - 32
    onActionClicked: {
        editing = !editing
        if (!editing)
            Ui.focusCanvas()
    }

    function submit() {
        if (urlBox.text.trim() === "" || agenda.adding)
            return
        agenda.addCalendar(urlBox.text, nameBox.text)
    }

    Connections {
        target: agenda
        function onChanged() {
            if (panel.wasAdding && !agenda.adding && agenda.error === "") {
                urlBox.text = ""
                nameBox.text = ""
            }
            panel.wasAdding = agenda.adding
        }
    }

    Flickable {
        id: flick
        objectName: "calendarList"
        x: 0
        width: parent.width
        height: parent.height - 4
        contentHeight: inner.implicitHeight
        interactive: contentHeight > height + 1
        boundsBehavior: Flickable.StopAtBounds
        clip: true

        Column {
            id: inner
            x: 6
            width: flick.width - 12
            spacing: 4

            // edit mode
            Repeater {
                model: panel.editing ? panel.calendars : []
                Item {
                    id: calRow
                    required property var modelData
                    width: inner.width
                    height: 22
                    HoverHandler { id: calHover }
                    Rectangle {
                        x: 8
                        width: 8
                        height: 8
                        radius: 4
                        color: calRow.modelData.color
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    Text {
                        x: 24
                        width: parent.width - 48
                        anchors.verticalCenter: parent.verticalCenter
                        text: calRow.modelData.name + (calRow.modelData.failing ? "  · can't fetch" : "")
                        textFormat: Text.PlainText
                        elide: Text.ElideRight
                        font.pixelSize: 12
                        color: calRow.modelData.failing ? Theme.warn : Theme.text
                    }
                    RemoveButton {
                        visible: calHover.hovered
                        anchors.right: parent.right
                        anchors.rightMargin: 9
                        anchors.verticalCenter: parent.verticalCenter
                        onClicked: agenda.removeCalendar(calRow.modelData.id)
                    }
                }
            }
            TextBox {
                id: urlBox
                objectName: "calendarUrl"
                visible: panel.editing
                width: inner.width
                height: 26
                placeholder: "Secret address in iCal format (https://…ics)"
                onAccepted: panel.submit()
                onEscaped: Ui.focusCanvas()
            }
            Row {
                visible: panel.editing
                spacing: 6
                TextBox {
                    id: nameBox
                    objectName: "calendarName"
                    width: inner.width - addBtn.width - 6
                    height: 26
                    placeholder: "Name (optional)"
                    onAccepted: panel.submit()
                    onEscaped: Ui.focusCanvas()
                }
                MiniButton {
                    id: addBtn
                    height: 26
                    text: agenda.adding ? "checking…" : "Add"
                    textColor: urlBox.text.trim() === "" ? Theme.dim : Theme.text
                    onClicked: panel.submit()
                }
            }
            Text {
                visible: panel.editing
                width: inner.width
                wrapMode: Text.Wrap
                font.pixelSize: 11
                color: Theme.dim
                text: "Google Calendar: Settings → your calendar → Integrate calendar → Secret address in iCal format."
            }
            Text {
                visible: (agenda.error || "") !== ""
                width: inner.width
                wrapMode: Text.Wrap
                textFormat: Text.PlainText
                font.pixelSize: 11
                color: Theme.bad
                text: agenda.error || ""
            }

            // view mode
            Text {
                objectName: "calendarEmpty"
                visible: !panel.editing && panel.events.length === 0
                x: 8
                height: 24
                verticalAlignment: Text.AlignVCenter
                font.pixelSize: 12
                color: Theme.dim
                text: panel.calendars.length > 0 ? "Nothing today or tomorrow" : "Link a calendar by its iCal address"
            }
            Repeater {
                model: panel.editing ? [] : panel.events
                Item {
                    id: ev
                    required property var modelData
                    width: inner.width
                    height: modelData.head ? 20 : 24
                    Text {
                        visible: ev.modelData.head
                        anchors.bottom: parent.bottom
                        anchors.bottomMargin: 2
                        text: ev.modelData.title
                        textFormat: Text.PlainText
                        font.pixelSize: 11
                        font.bold: true
                        color: Theme.dim
                    }
                    Rectangle {
                        visible: !ev.modelData.head && ev.modelData.now
                        anchors.fill: parent
                        radius: 6
                        color: Theme.panel2
                        border.color: Theme.line
                    }
                    Item {
                        visible: !ev.modelData.head
                        anchors.fill: parent
                        Rectangle {
                            x: 9
                            width: 7
                            height: 7
                            radius: 3.5
                            color: ev.modelData.color || Theme.muted
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        Text {
                            id: timeText
                            x: 24
                            width: 46
                            text: ev.modelData.time || ""
                            textFormat: Text.PlainText
                            font.pixelSize: ev.modelData.time === "all day" ? 10 : 12
                            font.features: ({ "tnum": 1 })
                            color: ev.modelData.now ? Theme.live : Theme.muted
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        Text {
                            x: timeText.x + timeText.width + 4
                            width: parent.width - x - 8 - (untilText.visible ? untilText.implicitWidth + 8 : 0)
                            text: ev.modelData.title
                            textFormat: Text.PlainText
                            elide: Text.ElideRight
                            font.pixelSize: 12
                            font.bold: ev.modelData.now === true
                            color: Theme.text
                            anchors.verticalCenter: parent.verticalCenter
                        }
                    }
                    Text {
                        id: untilText
                        visible: !ev.modelData.head && !!ev.modelData.until && inner.width > 260
                        anchors.right: parent.right
                        anchors.rightMargin: 8
                        anchors.verticalCenter: parent.verticalCenter
                        text: ev.modelData.until || ""
                        textFormat: Text.PlainText
                        font.pixelSize: 11
                        color: Theme.muted
                    }
                }
            }
        }
    }

    MouseArea {
        // the list scrolls first; at its ends the wheel goes on to the sidebar
        anchors.fill: flick
        acceptedButtons: Qt.NoButton
        onWheel: wheel => {
            var range = flick.contentHeight - flick.height
            var touch = wheel.phase !== Qt.NoScrollPhase && wheel.pixelDelta.y !== 0
            var dy = touch ? wheel.pixelDelta.y : wheel.angleDelta.y / 120 * 90
            var to = Math.max(0, Math.min(range, flick.contentY - dy))
            if (range <= 0 || Math.abs(to - flick.contentY) < 0.5) {
                wheel.accepted = false
                return
            }
            flick.contentY = to
        }
    }
}
