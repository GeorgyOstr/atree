import QtQuick

Rectangle {
    id: panel
    objectName: "detailsPanel"
    readonly property var d: backend.details
    readonly property bool shown: d.key !== undefined
    readonly property bool single: (d.selected || 0) <= 1
    readonly property bool hasPrompt: (d.prompt || "") !== ""

    component PanelButton: HButton {
        height: 28
        radius: 7
        fontSize: 13
        hpad: 22
    }

    width: 380
    radius: 12
    color: Theme.panel
    border.color: Theme.line
    clip: true

    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        onPressed: Ui.markTap()
        onWheel: wheel => wheel.accepted = true
    }

    Column {
        id: body
        x: 16
        y: 16
        width: parent.width - 32
        spacing: 10

        Item {
            width: parent.width
            height: titleText.height
            Text {
                id: titleText
                width: parent.width - 24
                text: panel.d.title || ""
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
                maximumLineCount: 3
                elide: Text.ElideRight
                font.pixelSize: 15
                font.bold: true
                color: Theme.text
            }
            Text {
                anchors.right: parent.right
                text: "✕"
                font.pixelSize: 15
                color: closeArea.containsMouse ? Theme.text : Theme.muted
                MouseArea {
                    id: closeArea
                    anchors.fill: parent
                    anchors.margins: -4
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: { Ui.markTap(); backend.clearSelection() }
                }
            }
        }

        Flow {
            width: parent.width
            spacing: 6
            component Tag: Rectangle {
                property string text: ""
                property color textColor: Theme.text
                height: 20
                width: tagText.implicitWidth + 14
                radius: 10
                color: Theme.panel2
                Text {
                    id: tagText
                    anchors.centerIn: parent
                    text: parent.text
                    textFormat: Text.PlainText
                    font.pixelSize: 11
                    color: parent.textColor
                }
            }
            Tag {
                text: panel.d.tool ? (backend.agentInfo[panel.d.tool] ? backend.agentInfo[panel.d.tool].label : panel.d.tool) : ""
                textColor: Theme.toolColor(panel.d.tool)
            }
            Tag {
                visible: panel.d.live === true
                text: "● " + (panel.d.status || "running")
                textColor: Theme.live
            }
            Tag {
                visible: panel.d.archived === true
                text: "archived"
                textColor: Theme.muted
            }
            Tag {
                visible: !panel.single
                text: panel.d.selected + " selected"
                textColor: Theme.accent
            }
        }

        Grid {
            columns: 2
            columnSpacing: 10
            rowSpacing: 5
            Repeater {
                model: panel.d.rows || []
                Text {
                    required property string modelData
                    required property int index
                    width: index % 2 === 0 ? 76 : body.width - 86
                    text: modelData
                    textFormat: Text.PlainText
                    wrapMode: Text.WrapAnywhere
                    font.pixelSize: 12
                    color: index % 2 === 0 ? Theme.dim : Theme.text
                }
            }
        }

        Text {
            // with no first prompt the caption and the well give way to empty space
            visible: panel.hasPrompt
            text: "First prompt"
            font.pixelSize: 12
            color: Theme.dim
        }
    }

    Rectangle {
        id: promptWell
        objectName: "promptWell"
        visible: panel.hasPrompt
        x: 16
        y: body.y + body.height + 6
        width: parent.width - 32
        height: Math.max(0, buttons.y - y - 12)
        radius: 8
        color: Theme.bg
        Flickable {
            id: promptFlick
            anchors.fill: parent
            anchors.margins: 10
            contentHeight: promptText.implicitHeight
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            TextEdit {
                id: promptText
                objectName: "detailsPrompt"
                width: promptFlick.width
                text: panel.d.prompt || ""
                textFormat: TextEdit.PlainText
                readOnly: true
                selectByMouse: true
                wrapMode: TextEdit.Wrap
                font.pixelSize: 13
                color: Theme.promptText
                selectionColor: Theme.selection
                Keys.onEscapePressed: event => { event.accepted = true; Ui.focusCanvas() }
            }
        }
    }

    Flow {
        id: buttons
        x: 16
        width: parent.width - 32
        y: parent.height - height - 16
        spacing: 6
        PanelButton {
            visible: !panel.d.archived
            primary: true
            text: "Resume in terminal"
            onClicked: backend.resume(panel.d.key)
        }
        PanelButton {
            visible: !panel.d.archived
            text: "Copy command"
            onClicked: backend.copyResume(panel.d.key)
        }
        PanelButton {
            visible: !panel.d.archived
            text: "Move…"
            onClicked: Ui.app.openMoveDialog(backend.actionKeys(panel.d.key), "")
        }
        PanelButton {
            visible: !panel.d.archived && !panel.d.live
            text: "Archive"
            onClicked: Ui.app.archiveKeys(backend.actionKeys(panel.d.key))
        }
        PanelButton {
            visible: panel.d.archived === true
            primary: true
            text: "Restore"
            onClicked: backend.restore(backend.actionKeys(panel.d.key))
        }
        PanelButton {
            visible: panel.d.archived === true
            text: "Delete"
            onClicked: Ui.app.openDeleteDialog(backend.actionKeys(panel.d.key))
        }
    }
}
